"""Offline prompt-contract regressions, not evidence of model performance."""

from pathlib import Path

import pytest

from agentic_itops.portfolio_schemas import output_schema


ROOT = Path(__file__).resolve().parents[1]
FOLDERS = (
    "00-ops-orchestrator",
    "02-triage-dedup",
    "03-rca-investigator",
    "04-runbook-memory",
    "05-remediation-planner",
    "07-incident-comms",
    "08-postmortem-drafter",
    "09-alert-hygiene",
    "10-industry-signal-pack",
)


def instructions(folder):
    return " ".join(
        (ROOT / "agents" / folder / "instructions.md").read_text(encoding="utf-8").split()
    )


@pytest.mark.parametrize("folder", FOLDERS)
def test_completion_requires_successful_bounded_work_not_certainty(folder):
    prompt = instructions(folder)
    assert "## Task completion" in prompt
    assert "bounded analytical task was accomplished using verified supplied inputs" in prompt
    assert "required tool steps succeeded" in prompt
    assert "essential input is missing, a required tool fails" in prompt
    assert "source authority cannot be established" in prompt
    assert "requested conclusion cannot responsibly be made" in prompt
    assert "Keep unknown facts in `evidence_gaps`" in prompt
    assert "never invent cause, impact, timeline, or citations" in prompt
    assert "permission to execute or advance human review" in prompt
    assert output_schema(folder[3:])["properties"]["status"]["enum"] == [
        "completed", "insufficient_evidence",
    ]


def test_zero_eligible_matches_are_distinct_from_failed_retrieval():
    prompt = instructions("04-runbook-memory")
    assert "Successful scoped retrieval with zero eligible matches may return `completed`" in prompt
    assert "empty `runbooks` and `postmortems`, and an explicit `coverage_gap`" in prompt
    assert "no human-approved corpus" in prompt
    assert "Unavailable, failed or unauthorized search is not zero hits" in prompt
    assert "missing isolation approval remains blocking" in prompt
    assert "never invent a document or receipt ID" in prompt
    assert "source authority or eligibility is a gap, not an established negative finding" in prompt
    assert "there is no FileSearch fallback" in prompt


def test_no_action_can_complete_but_cannot_hide_failure():
    prompt = instructions("05-remediation-planner")
    assert "A completed typed no-action plan is legitimate" in prompt
    assert "available verified evidence supports no justified operational proposal" in prompt
    assert "Do not use no action as a failure fallback" in prompt
    assert "missing essential planning inputs or failed required checks still require" in prompt
    assert "Missing ARM mapping alone does not block a supported synthetic no-action" in prompt
    assert '`proposed_action: "none"`, `target_resource_id: null`' in prompt
    assert "Human acceptance of a draft does not authorize these actions" in prompt
    assert "A deferred gate response is not approval" in prompt


def test_coordinator_preserves_specialist_status_and_gate_order():
    prompt = instructions("00-ops-orchestrator")
    assert "Preserve specialist statuses faithfully; never upgrade `insufficient_evidence`" in prompt
    assert "missing or insufficient required specialist stage blocks coordinator completion" in prompt
    assert "Do not relabel prior blocked investigations or bypass review" in prompt
    assert "04 -> 03 -> finalized 05 -> deterministic gate -> 07" in prompt
    assert 'gate_decision: "not_evaluated"' in prompt
    assert "not as permission to skip the trusted gate stage or fabricate an ALLOW" in prompt


def test_triage_requires_detector_identity_without_fabricated_impact():
    prompt = instructions("02-triage-dedup")
    assert "Follow the supplied detector identity, fingerprint and lifecycle" in prompt
    assert "do not invent an alert event for an operator investigation" in prompt
    assert "turn observed recovery into human closure" in prompt
    assert "evidence-bounded signal classification, not fabricated customer severity" in prompt
    assert "Unknown business criticality remains unknown" in prompt
    assert "failed context lookup requires `insufficient_evidence`" in prompt


def test_rca_unknown_cause_does_not_require_invented_hypotheses():
    prompt = instructions("03-rca-investigator")
    assert "measured deviations separately from causal hypotheses" in prompt
    assert "complete with unknown cause and empty hypotheses" in prompt
    assert "required change, telemetry and context checks succeed" in prompt
    assert "synthetic recovery proves neither an outage nor an intervention" in prompt
    assert "missing observations essential to the requested analysis" in prompt


def test_comms_withholds_unverified_impact_but_requires_gate_receipt():
    prompt = instructions("07-incident-comms")
    assert "synthetic-only summary can complete" in prompt
    assert "leave `customer_update` empty" in prompt
    assert "impact is unverified" in prompt
    assert "missing or deferred receipt requires `insufficient_evidence`" in prompt
    assert "You never send them" in prompt


def test_postmortem_requires_real_human_closure_and_separate_publication():
    prompt = instructions("08-postmortem-drafter")
    assert "A real human closure payload from the trusted workflow is essential" in prompt
    assert "without it return `insufficient_evidence`" in prompt
    assert "cannot substitute for human closure authorization" in prompt
    assert "Draft completion is not approval for knowledge publication" in prompt
    assert 'retain `executed_action: "none"`' in prompt
    assert "never claim executed mitigation" in prompt
    assert "the separate publisher alone owns writes" in prompt
    assert "An empty list is correct when none is established" in prompt


def test_hygiene_distinguishes_coverage_assessment_from_required_estimate():
    prompt = instructions("09-alert-hygiene")
    assert "successful `analyzeAlertHygiene` result establishing insufficient longitudinal coverage" in prompt
    assert "`estimated_volume_reduction_pct: null`, empty recommendations" in prompt
    assert "quantitative estimate or rule evaluation is the required task" in prompt
    assert "essential history is missing, use `insufficient_evidence`" in prompt
    assert "unauthorized analysis is never a completed no-change assessment" in prompt
    assert "Invocation alone does not prove that the backend evaluated history" in prompt


def test_industry_context_does_not_invent_trusted_industry_or_criticality():
    prompt = instructions("10-industry-signal-pack")
    assert "Given a trusted supported industry" in prompt
    assert "classifications explicitly unverified" in prompt
    assert "Missing trusted industry requires `insufficient_evidence`" in prompt
    assert "Never select an industry merely to satisfy the output enum" in prompt
    assert "producer label as authority" in prompt
