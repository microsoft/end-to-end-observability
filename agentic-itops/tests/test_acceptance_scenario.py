from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
import socket
from uuid import uuid4

import pytest

from agentic_itops.acceptance_scenario import evaluate_series, scenario_policy, write_package
from agentic_itops.consumer.contracts import ContractError, digest, iso
from tests.test_consumer_detectors import series


def sample(changed=True):
    records, _, start, _ = series("downstream", "recovery")
    run = str(uuid4())
    rows = []
    for index, record in enumerate(records):
        row = record.row
        row["RunId"] = run
        row.update(EntityId="asset-0ccfac117cb4", LocationId="A06")
        row["Payload"].update(
            measurement="availability_index", unit="index", tag_id="tag-2ee196932b31",
            value=0.952142 if changed and 12 <= index < 16 else 0.992142)
        row["Payload"].pop("phase", None)
        measurement = row["Context"].get("measurement")
        if measurement:
            measurement.update(measurement="availability_index", unit="index")
        rows.append(row)
    return rows, records[0].producer, run, start


def test_real_detector_proves_candidate_recovery_and_normal_control():
    for changed in (False, True):
        rows, producer, run, start = sample(changed)
        selected, proof = evaluate_series(rows, producer, run, start, changed=changed)
        assert selected == rows
        assert proof["candidate_count"] == int(changed)
        assert proof["analysis"]["baseline"]["sample_count"] == 12
        assert proof["analysis"]["baseline"]["change_threshold"] == 0.02
        assert proof["disabled_policy_candidate_count"] == 0
        assert proof["model_calls"] == proof["embedding_calls"] == 0
    assert not scenario_policy().enabled
    assert not scenario_policy().approval_reference


@pytest.mark.parametrize("mutation", [
    "wrong-target", "wrong-run", "missing", "stale", "no-crossing",
    "labels", "duplicate", "conflict", "wrong-phase", "shifted-time",
])
def test_invalid_evidence_cannot_claim_acceptance(mutation):
    rows, producer, run, start = sample()
    if mutation == "wrong-target":
        rows[0]["EntityId"] = "another-target"
    elif mutation == "wrong-run":
        rows[0]["RunId"] = str(uuid4())
    elif mutation == "missing":
        rows.pop()
    elif mutation == "stale":
        rows[12]["Payload"]["source_time_utc"] = iso(start)
    elif mutation == "no-crossing":
        for row in rows:
            row["Payload"]["value"] = 0.992142
    elif mutation == "labels":
        rows[0]["Context"]["scenario"] = "do-not-leak"
    elif mutation in {"duplicate", "conflict"}:
        extra = deepcopy(rows[0])
        if mutation == "conflict":
            extra["Payload"]["value"] = 0
        rows.append(extra)
    elif mutation == "wrong-phase":
        rows[0]["Payload"]["phase"] = "different"
    else:
        rows[0]["ObservedAt"] = iso(start + timedelta(seconds=1))
    with pytest.raises(ContractError):
        evaluate_series(rows, producer, run, start, changed=True)


def test_other_same_measurement_targets_do_not_create_fanout():
    rows, producer, run, start = sample()
    other = deepcopy(rows)
    for row in other:
        row["EntityId"] = "unapproved-target"
        row["EventId"] = digest(["other", row["EventId"]])
    selected, proof = evaluate_series(rows + other, producer, run, start, changed=True)
    assert selected == rows
    assert proof["source_row_count"] == 40
    assert proof["matching_row_count"] == 20
    assert proof["candidate_count"] == 1


def test_package_refuses_overwrite_and_separates_evaluator_labels(tmp_path):
    rows, producer, run, start = sample()
    selected, proof = evaluate_series(rows, producer, run, start, changed=True)
    normal_rows, producer, normal_run, start = sample(False)
    normal, control = evaluate_series(normal_rows, producer, normal_run, start, changed=False)
    output = tmp_path / "new-package"
    evaluator = {"scenario": "analyzer_delay", "changed": proof, "normal": control}
    write_package(output, selected, normal, evaluator)
    original = (output / "observations.jsonl").read_bytes()
    manifest = json.loads((output / "package-integrity.json").read_bytes())
    for name, expected in manifest["files"].items():
        content = (output / name).read_bytes()
        assert sha256(content).hexdigest() == expected["sha256"]
        assert len(content) == expected["bytes"]
    assert b"scenario" not in original
    assert b"analyzer_delay" not in original
    with pytest.raises(FileExistsError):
        write_package(output, selected, normal, evaluator)
    assert (output / "observations.jsonl").read_bytes() == original


def test_actual_producer_package_is_offline_deterministic_and_measurable(tmp_path, monkeypatch):
    from scripts import prepare_acceptance_scenario as script

    def deny_network(*args, **kwargs):
        raise AssertionError("offline preparation must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(script, "BUILD_ROOT", tmp_path / ".build")
    run = "b8867fbd-c160-47cd-bf90-4d8056c547cb"
    start = "2026-09-17T22:00:00Z"
    first, second = tmp_path / ".build" / "first", tmp_path / ".build" / "second"
    proof = script.build(run, start, first)
    assert script.build(run, start, second) == proof
    assert proof["changed"]["source_row_count"] == 200
    assert proof["changed"]["matching_row_count"] == 20
    assert proof["changed"]["candidate_count"] == 1
    assert proof["normal_control"]["candidate_count"] == 0
    assert proof["run_id"] != proof["normal_control_run_id"]
    assert all(path.read_bytes() == (second / path.name).read_bytes() for path in first.iterdir())
    rows = [json.loads(line) for line in (first / "observations.jsonl").read_bytes().splitlines()]
    assert [row["Payload"]["value"] for row in rows] == [0.992142] * 12 + [0.952142] * 4 + [0.992142] * 4
    assert all(row["Payload"]["source_time_utc"] == row["SimulatedAt"] for row in rows)
    policy = json.loads((first / "detector-policy.disabled.json").read_bytes())
    assert policy["enabled"] is False and policy["approval_reference"] == ""
    with pytest.raises(FileExistsError, match="overwrite"):
        script.build(run, start, first)
    with pytest.raises(ContractError, match="subdirectory"):
        script.build(run, start, tmp_path / ".build")
    with pytest.raises(ContractError, match="subdirectory"):
        script.build(run, start, tmp_path / "outside")
