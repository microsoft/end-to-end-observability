from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import random

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.contracts import ContractError, Observation, SURFACES, iso, utc
from agentic_itops.consumer.detectors import DetectorPolicy, analyze_series, analyze_window
from tests.test_consumer_observations import ROWS, SCOPE

CASES = ("normal", "normal-low", "step-up", "step-down", "recovery", "stale", "missing", "late")


def series(surface, case="normal"):
    original = next(row for row in ROWS if row["Surface"] == surface)
    start = utc(original["ObservedAt"])
    inventory = original["RecordType"] == "inventory_position"
    count = 20 if case == "recovery" else 13 if case == "missing" else 16
    records = []
    for index in range(count):
        row = deepcopy(original)
        at = start + timedelta(minutes=index)
        for key in ("TimeGenerated", "ObservedAt", "SimulatedAt"):
            row[key] = iso(at)
        row["EventId"] = hashlib.sha256(f"{surface}:{index}".encode()).hexdigest()
        value = 3 if case == "normal-low" else 100
        if index >= 10:
            if case in ("step-up", "late", "missing") or (case == "recovery" and index < 14):
                value = 130
            elif case == "step-down":
                value = 70
        payload = row["Payload"]
        payload["quantity" if inventory else "value"] = value
        payload["quality"] = "good"
        if not inventory:
            payload["source_time_utc"] = iso(at - timedelta(seconds=300)
                                            if case == "stale" and index >= 10 else at)
        elif case == "stale" and index >= 10:
            payload["quality"] = "stale"
        records.append(Observation.parse(SCOPE, row))
    policy = DetectorPolicy(
        policy_id=f"fixture-{surface}", version="1", surface=surface,
        record_type=original["RecordType"], measurement=original["Payload"].get("measurement"),
        unit=original["Payload"]["uom" if inventory else "unit"],
        minimum_absolute_change=5, relative_change=0.1,
        approval_reference="offline-test-only-not-a-production-approval", enabled=True,
    )
    reference = start + timedelta(minutes=20 if case == "recovery" else 16)
    if case == "late":
        random.Random(92).shuffle(records)
        records.extend(records[:2])
    return records, policy, start, reference


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("case", CASES)
def test_seventy_two_software_detector_cases(surface, case):
    records, policy, start, reference = series(surface, case)
    result = analyze_series(records, policy, baseline_start=start, reference_time=reference)
    assert result["baseline"]["sample_count"] == 10
    assert result["execution_allowed"] is False
    assert result["OperationalUse"] == "prohibited"
    fired = [event for event in result["transitions"] if event["kind"] == "fired"]
    recovered = [event for event in result["transitions"] if event["kind"] == "recovery_observed"]
    if case in ("normal", "normal-low"):
        assert result["condition"] == "normal"
        assert not fired and not recovered and not result["episodes"]
    elif case == "stale":
        assert result["condition"] == "data_quality"
        assert not fired and not recovered
    else:
        assert len(fired) == 1 and len(result["episodes"]) == 1
        assert result["episodes"][0]["onset_at"] == iso(start + timedelta(minutes=10))
        assert fired[0]["at"] == iso(start + timedelta(minutes=12))
        assert fired[0]["evidence_event_ids"] == [
            hashlib.sha256(f"{surface}:{index}".encode()).hexdigest() for index in (10, 11, 12)
        ]
        if case == "recovery":
            assert len(recovered) == 1
            assert recovered[0]["at"] == iso(start + timedelta(minutes=16))
            assert result["condition"] == "normal"
        elif case == "missing":
            assert result["condition"] == "insufficient_evidence"
            assert result["episodes"][0]["observed_state"] == "unknown"
            assert result["episodes"][0]["recovered_at"] is None
            assert not recovered
        else:
            assert result["condition"] == "candidate_anomaly"
            assert not recovered


def test_replay_order_and_duplicate_delivery_are_deterministic():
    records, policy, start, reference = series("midstream", "step-up")
    first = analyze_series(records, policy, baseline_start=start, reference_time=reference)
    second = analyze_series(list(reversed(records)) + records[:4], policy,
                            baseline_start=start, reference_time=reference)
    assert first == second


def test_missing_warmup_is_insufficient_not_a_new_baseline():
    records, policy, start, reference = series("upstream", "step-up")
    records.pop(5)
    result = analyze_series(records, policy, baseline_start=start, reference_time=reference)
    assert result["reason"] == "baseline_not_ready" and not result["episodes"]


def test_legacy_profile_and_enriched_profile_do_not_share_a_baseline():
    records, policy, start, reference = series("downstream", "step-up")
    legacy = records[0].row
    legacy["Context"].pop("details_profile")
    records[0] = Observation.parse(SCOPE, legacy)
    with pytest.raises(ContractError, match="profiles"):
        analyze_series(records, policy, baseline_start=start, reference_time=reference)
    results = analyze_window(records, [policy], baseline_start=start, reference_time=reference)
    assert len(results) == 2
    assert all(result["condition"] == "insufficient_evidence" for result in results)


def test_policy_disabled_or_absent_does_not_report_normal():
    records, policy, start, reference = series("power-grid")
    disabled = analyze_series(records, replace(policy, enabled=False, approval_reference=""),
                              baseline_start=start, reference_time=reference)
    assert disabled["condition"] == "unsupported"
    unmatched = analyze_window(records, [], baseline_start=start, reference_time=reference)
    assert unmatched[0]["condition"] == "unsupported"
    empty = analyze_window([], [policy], baseline_start=start, reference_time=reference)
    assert empty[0]["condition"] == "insufficient_evidence"


def test_multiple_matching_policies_are_not_arbitrarily_selected():
    records, policy, start, reference = series("power-grid")
    with pytest.raises(ContractError, match="multiple"):
        analyze_window(records, [policy, replace(policy, version="2")],
                       baseline_start=start, reference_time=reference)


def test_unknown_source_time_and_null_quantity_do_not_prove_recovery():
    records, policy, start, reference = series("upstream", "step-up")
    for index in (13, 14, 15):
        row = records[index].row
        row["Payload"]["value"] = None
        row["Payload"]["source_time_utc"] = None
        records[index] = Observation.parse(SCOPE, row)
    result = analyze_series(records, policy, baseline_start=start, reference_time=reference)
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["recovered_at"] is None
    assert not any(item["kind"] == "recovery_observed" for item in result["transitions"])


def test_same_timestamp_cannot_inflate_sample_count_or_hide_contradiction():
    records, policy, start, reference = series("midstream")
    duplicate = records[0].row
    duplicate["EventId"] = "f" * 64
    result = analyze_series(records + [Observation.parse(SCOPE, duplicate)], policy,
                            baseline_start=start, reference_time=reference)
    assert result["baseline"]["sample_count"] == 10
    duplicate["Payload"]["value"] = 500
    with pytest.raises(ContractError, match="conflicting observations"):
        analyze_series(records + [Observation.parse(SCOPE, duplicate)], policy,
                       baseline_start=start, reference_time=reference)


@pytest.mark.parametrize("changes", [
    {"enabled": True, "approval_reference": ""}, {"minimum_absolute_change": 0},
    {"minimum_absolute_change": float("nan")}, {"minimum_baseline_samples": True},
    {"firing_samples": 0}, {"relative_change": -1}, {"allowed_quality": ()},
])
def test_invalid_detector_configuration_rejected(changes):
    _, policy, _, _ = series("upstream")
    with pytest.raises(ContractError):
        replace(policy, **changes)
