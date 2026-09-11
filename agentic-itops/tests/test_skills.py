"""Unit tests for the code-hosted skills.

These complement `evaluations/` rather than duplicating it. The
evaluation suite asserts behaviour against curated datasets; these tests
cover edge cases, malformed input and internal invariants.

    python -m pytest agentic-itops/tests -q
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agentic_itops.classify import classify_symptom, infer_environment, map_severity
from agentic_itops.envelope import (
    Severity,
    SymptomClass,
    compute_coarse_key,
    compute_fingerprint,
)
from agentic_itops.hygiene import analyze
from agentic_itops.normalizer import (
    NormalizationError,
    correlate,
    normalize_many,
    normalize_one,
)
from agentic_itops.safety_gate import ChangeRequest, ChangeSafetyGate, Effect, load_policy

SUB = "55555555-5555-5555-5555-555555555555"
PUMP = (
    f"/subscriptions/{SUB}/resourceGroups/rg-refinery-midstream"
    f"/providers/Microsoft.Compute/virtualMachines/vm-pump-ctrl-07"
)


def azure_alert(rule: str, severity: str = "Sev2", resource: str = PUMP, **essentials):
    return {
        "data": {
            "essentials": {
                "alertRule": rule,
                "severity": severity,
                "monitorCondition": "Fired",
                "targetResource": resource,
                **essentials,
            }
        }
    }


# --- fingerprinting ---------------------------------------------------


class TestFingerprint:
    def test_is_stable_across_calls(self):
        a = compute_fingerprint("svc", PUMP, "saturation", "Percentage CPU")
        b = compute_fingerprint("svc", PUMP, "saturation", "Percentage CPU")
        assert a == b

    def test_is_case_insensitive_on_resource_id(self):
        a = compute_fingerprint("svc", PUMP, "saturation", "cpu")
        b = compute_fingerprint("svc", PUMP.upper(), "saturation", "cpu")
        assert a == b

    @pytest.mark.parametrize(
        "vendor_metric",
        ["Percentage CPU", "system.cpu.user", "node_cpu_seconds_total", "Processor Time"],
    )
    def test_vendor_metric_spellings_collapse(self, vendor_metric):
        """The whole point of cross-source dedup."""
        baseline = compute_fingerprint("svc", PUMP, "saturation", "Percentage CPU")
        assert compute_fingerprint("svc", PUMP, "saturation", vendor_metric) == baseline

    def test_different_symptoms_do_not_collapse(self):
        cpu = compute_fingerprint("svc", PUMP, "saturation", "cpu")
        disk = compute_fingerprint("svc", PUMP, "capacity", "disk")
        assert cpu != disk

    def test_different_resources_do_not_collapse(self):
        a = compute_fingerprint("svc", PUMP, "saturation", "cpu")
        b = compute_fingerprint("svc", PUMP.replace("07", "08"), "saturation", "cpu")
        assert a != b

    def test_coarse_key_ignores_metric(self):
        a = compute_coarse_key("svc", PUMP, "saturation")
        b = compute_coarse_key("svc", PUMP, "saturation")
        assert a == b
        assert a != compute_coarse_key("svc", PUMP, "capacity")


# --- classification ---------------------------------------------------


class TestClassification:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Percentage CPU - AMBA", SymptomClass.SATURATION),
            ("Heartbeat missing", SymptomClass.AVAILABILITY),
            ("HTTP 5xx rate above baseline", SymptomClass.ERROR_RATE),
            ("p95 latency regression", SymptomClass.LATENCY),
            ("Disk free space below 10%", SymptomClass.CAPACITY),
            ("Unauthorized access attempt", SymptomClass.SECURITY),
            ("Network In - AMBA", SymptomClass.CONNECTIVITY),
            ("Telemetry gap - stale sensor data", SymptomClass.DATA_QUALITY),
            ("Policy state non-compliant", SymptomClass.CONFIGURATION),
        ],
    )
    def test_known_symptoms(self, text, expected):
        assert classify_symptom(text) is expected

    def test_unknown_is_honest(self):
        assert classify_symptom("Widget frobnicator threshold") is SymptomClass.UNKNOWN

    def test_security_outranks_availability(self):
        """Ordering matters: a security signal must not be filed as availability."""
        assert classify_symptom("Unauthorized access - host down") is SymptomClass.SECURITY

    def test_empty_input_is_unknown(self):
        assert classify_symptom("", "", "") is SymptomClass.UNKNOWN


class TestSeverityMapping:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Sev0", Severity.CRITICAL),
            ("Sev4", Severity.INFO),
            ("critical", Severity.CRITICAL),
            ("error", Severity.HIGH),
            ("warning", Severity.MEDIUM),
            ("ok", Severity.INFO),
            (1, Severity.CRITICAL),
            (3, Severity.MEDIUM),
            ("2", Severity.HIGH),
            ("P1", Severity.CRITICAL),
        ],
    )
    def test_vendor_scales(self, value, expected):
        assert map_severity(value) is expected

    def test_unknown_falls_back_to_default(self):
        assert map_severity("banana") is Severity.MEDIUM
        assert map_severity(None) is Severity.MEDIUM

    def test_bool_does_not_become_severity_one(self):
        """bool is a subclass of int; True must not map to CRITICAL."""
        assert map_severity(True) is Severity.MEDIUM


class TestEnvironmentInference:
    def test_explicit_wins(self):
        assert infer_environment("rg-prod-01", explicit="dev") == "dev"

    def test_inferred_from_resource_group(self):
        assert infer_environment("rg-prod-portal") == "prod"

    def test_unknown_when_untagged(self):
        assert infer_environment("rg-refinery-midstream") == "unknown"


# --- normalization ----------------------------------------------------


class TestNormalization:
    def test_unmatched_payload_raises(self):
        with pytest.raises(NormalizationError):
            normalize_one({"totally": "unrecognized"})

    def test_batch_collects_failures_without_stopping(self):
        payloads = [azure_alert("Percentage CPU - AMBA"), {"garbage": True}]
        envelopes, failures = normalize_many(payloads)
        assert len(envelopes) == 1
        assert len(failures) == 1

    def test_strict_mode_raises(self):
        with pytest.raises(NormalizationError):
            normalize_many([{"garbage": True}], strict=True)

    def test_malformed_arm_id_degrades_not_raises(self):
        envelope = normalize_one(azure_alert("Percentage CPU - AMBA", resource="not-an-arm-id"))
        assert envelope.resource.name == "not-an-arm-id"
        assert envelope.resource.subscription_id == ""

    def test_missing_resource_is_tolerated(self):
        envelope = normalize_one(azure_alert("Percentage CPU - AMBA", resource=""))
        assert envelope.fingerprint  # still produces a usable envelope

    def test_warnings_surface_untagged_environment(self):
        envelope = normalize_one(azure_alert("Percentage CPU - AMBA"))
        assert any("environment" in w for w in envelope.normalization.warnings)

    def test_raw_payload_is_retained(self):
        payload = azure_alert("Percentage CPU - AMBA")
        assert normalize_one(payload).raw == payload


class TestCorrelation:
    def test_identical_symptoms_collapse(self):
        envelopes, _ = normalize_many([
            azure_alert("Percentage CPU - AMBA"),
            azure_alert("Percentage CPU - AMBA"),
        ])
        assert len(correlate(envelopes)) == 1

    def test_distinct_symptoms_stay_separate(self):
        envelopes, _ = normalize_many([
            azure_alert("Percentage CPU - AMBA"),
            azure_alert("Disk free space - AMBA"),
        ])
        assert len(correlate(envelopes)) == 2

    def test_primary_is_highest_severity(self):
        envelopes, _ = normalize_many([
            azure_alert("Percentage CPU - AMBA", severity="Sev3"),
            azure_alert("Percentage CPU - AMBA", severity="Sev0"),
        ])
        assert correlate(envelopes)[0].primary.severity is Severity.CRITICAL

    def test_metric_free_signal_does_not_merge_unrelated_symptoms(self):
        """A ticket must not glue together two different metric problems."""
        envelopes, _ = normalize_many([
            azure_alert("Percentage CPU - AMBA"),
            azure_alert("Available Memory Bytes - AMBA"),
        ])
        incidents = correlate(envelopes)
        assert len(incidents) == 2


# --- change-safety gate -----------------------------------------------


AT = datetime(2026, 9, 14, 13, 42, tzinfo=timezone.utc)  # Monday, outside any freeze


class TestChangeSafetyGate:
    @pytest.fixture
    def gate(self):
        return ChangeSafetyGate()

    def request(self, **kwargs):
        defaults = {
            "action": "scale_out",
            "resource_id": f"/subscriptions/{SUB}/resourceGroups/rg-app-portal/providers/x/y/z",
            "environment": "prod",
            "service": "rg-app-portal",
            "estimated_affected": 1,
            "at": AT,
        }
        return ChangeRequest(**{**defaults, "at": kwargs.pop("at", AT), **kwargs})

    def test_unknown_verb_is_denied(self, gate):
        assert gate.evaluate(self.request(action="rm_rf")).effect is Effect.DENY

    def test_ot_resource_is_denied_even_with_allowed_verb(self, gate):
        decision = gate.evaluate(self.request(action="restart", resource_id=PUMP.replace("pump-ctrl-07", "scada-01")))
        assert decision.effect is Effect.DENY
        assert "ot-boundary" in decision.matched_rules

    def test_every_decision_carries_reasons_and_audit_id(self, gate):
        for action in ("scale_out", "rm_rf", "restart"):
            decision = gate.evaluate(self.request(action=action))
            assert decision.reasons, f"{action} produced a silent verdict"
            assert decision.audit_id

    def test_audit_id_is_deterministic(self, gate):
        a = gate.evaluate(self.request()).audit_id
        b = gate.evaluate(self.request()).audit_id
        assert a == b

    def test_read_only_mode_blocks_mutation(self):
        policy = {**load_policy(), "read_only": True}
        decision = ChangeSafetyGate(policy=policy).evaluate(self.request())
        assert decision.effect is Effect.DENY
        assert "read_only" in decision.matched_rules

    def test_read_only_mode_allows_dry_run(self):
        policy = {**load_policy(), "read_only": True}
        decision = ChangeSafetyGate(policy=policy).evaluate(self.request(dry_run=True))
        assert decision.effect is not Effect.DENY or "read_only" not in decision.matched_rules

    def test_hard_ceiling_outranks_freeze(self, gate):
        """Regression: a freeze must not downgrade an absolute denial."""
        during_freeze = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)  # Saturday
        decision = gate.evaluate(self.request(estimated_affected=40, at=during_freeze))
        assert decision.effect is Effect.DENY

    def test_empty_policy_denies_everything(self):
        """Fail closed when policy is missing or empty."""
        decision = ChangeSafetyGate(policy={}).evaluate(self.request())
        assert decision.effect is Effect.DENY

    def test_deny_rule_with_no_predicates_matches_nothing(self):
        """A malformed rule must not become a catch-all denial."""
        policy = {**load_policy(), "deny": [{"id": "malformed", "reason": "oops"}]}
        decision = ChangeSafetyGate(policy=policy).evaluate(self.request())
        assert "malformed" not in decision.matched_rules


# --- hygiene ----------------------------------------------------------


class TestHygiene:
    def test_self_resolving_rule_scores_noisy(self):
        payloads = [
            azure_alert("Network In - AMBA", severity="Sev3", monitorCondition="Resolved")
            for _ in range(60)
        ]
        envelopes, _ = normalize_many(payloads)
        report = analyze(envelopes)
        finding = report["findings"][0]
        assert finding["auto_resolve_rate"] == 1.0
        assert finding["noise_score"] >= 70
        assert finding["recommendation"] == "suppress_or_retune"

    def test_single_firing_is_not_judged(self):
        envelopes, _ = normalize_many([azure_alert("Rare thing - AMBA")])
        assert analyze(envelopes)["findings"][0]["recommendation"] == "keep_insufficient_data"

    def test_rationale_is_never_empty(self):
        envelopes, _ = normalize_many([azure_alert("Percentage CPU - AMBA") for _ in range(3)])
        for finding in analyze(envelopes)["findings"]:
            assert finding["rationale"]

    def test_empty_corpus_does_not_divide_by_zero(self):
        report = analyze([])
        assert report["distinct_rules"] == 0
