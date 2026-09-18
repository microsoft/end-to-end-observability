"""Offline admission proofs; fixtures are never live review or model evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from agentic_itops.consumer.contracts import ContractError, canonical, iso, strict_json
from agentic_itops.consumer.processing import process_snapshot
from agentic_itops.consumer.storage_contract import EvidenceRef, IntegrityConflict, WorkRequest
from agentic_itops.investigations import approval, validate_detector_configuration
from agentic_itops.service_backends import OVERLAY
from tests.test_application_integration import OID, integrated
from tests.test_consumer_processing import setup_batches


def fixture(tmp_path, *, committed=True):
    f = integrated(tmp_path)
    _, refs, batches, scope, policy = setup_batches(tmp_path)
    row = batches[0].records[0].observation.row
    f.policy = replace(
        policy, entity_id=row["EntityId"], location_id=row["LocationId"],
        tag_id=row["Payload"].get("tag_id"), phase=row["Payload"].get("phase"))
    report = process_snapshot(scope, refs, f.runtime.evidence, [f.policy])
    f.reference = EvidenceRef(**report["candidates"][0]["evidence"])
    f.candidate = strict_json(f.runtime.evidence.read(f.reference))
    if committed:
        for batch, reference in zip(batches, refs):
            f.store.commit_ingestion(batch, reference)
    f.jobs.detector_policies = (f.policy,)
    f.jobs.investigation_approval = approval({
        "approval_id": "offline-detector-batch", "actor_oid": OID,
        "expires_at_utc": iso(datetime.now(timezone.utc) + timedelta(minutes=10)),
        "detector_authorization": {
            "producer_key": scope.producer.key, "run_id": scope.run_id,
            "policy_sha256": f.policy.sha256, "candidate_sha256": f.reference.sha256}})
    f.payload = {
        "producer_key": scope.producer.key, "run_id": scope.run_id,
        "candidate_evidence": asdict(f.reference), "incident_id": f.candidate["incident_id"], **OVERLAY}
    f.admitted_id = f.store.enqueue(WorkRequest("reasoning", f.reference.sha256, f.payload))
    original = f.transport.respond

    def respond(*args, **kwargs):
        response = original(*args, **kwargs)
        for item in response["output"]:
            if item["type"] == "message":
                value = json.loads(item["content"][0]["text"])
                if "incident_fingerprint" in value:
                    value["incident_fingerprint"] = f.candidate["incident_fingerprint"]
                item["content"][0]["text"] = json.dumps(value)
        return response

    f.transport.respond = respond
    return f


def test_exact_detector_requires_sql_committed_derivation_before_models(tmp_path):
    f = fixture(tmp_path)
    assert f.jobs._candidate(f.payload) == (f.candidate, f.reference)
    assert f.transport.calls == []
    assert f.worker.process_once("reasoning", job_id=f.admitted_id)
    assert f.worker.process_once("projection")
    assert f.repository.get_revision(f.candidate["incident_id"], 1)["review_state"] == "pending"


def test_approved_hash_without_sql_provenance_cannot_admit_models(tmp_path):
    f = fixture(tmp_path, committed=False)
    with pytest.raises(ContractError, match="SQL-committed"):
        f.worker.process_once("reasoning", job_id=f.admitted_id)
    assert f.transport.calls == []


@pytest.mark.parametrize("field", ["producer_key", "run_id", "policy_sha256", "candidate_sha256"])
def test_wrong_scope_policy_or_candidate_remains_closed(tmp_path, field):
    f = fixture(tmp_path)
    f.jobs.investigation_approval["detector_authorization"][field] = "f" * 64
    with pytest.raises(ContractError, match="approval|authorization"):
        f.worker.process_once("reasoning", job_id=f.admitted_id)
    assert f.transport.calls == []


def test_mutated_candidate_and_approved_hash_cannot_replace_actual_derivation(tmp_path):
    f = fixture(tmp_path)
    changed = deepcopy(f.candidate)
    changed["severity_basis"] = "invented source conclusion"
    reference = f.runtime.evidence.put(canonical(changed))
    f.jobs.investigation_approval["detector_authorization"]["candidate_sha256"] = reference.sha256
    with pytest.raises(ContractError, match="SQL-committed"):
        f.jobs._candidate({**f.payload, "candidate_evidence": asdict(reference)})
    assert f.transport.calls == []


def test_detector_batch_cannot_admit_wildcard_or_multiple_policies(tmp_path):
    f = fixture(tmp_path)
    approved = f.jobs.investigation_approval
    wildcard = replace(f.policy, entity_id=None, location_id=None, tag_id=None, phase=None)
    approved["detector_authorization"]["policy_sha256"] = wildcard.sha256
    with pytest.raises(ContractError, match="exact-signal"):
        validate_detector_configuration(approved, f.scope, [wildcard])
    approved["detector_authorization"]["policy_sha256"] = f.policy.sha256
    with pytest.raises(ContractError, match="single enabled"):
        validate_detector_configuration(approved, f.scope, [f.policy, f.policy])


@pytest.mark.parametrize("field,value", [
    ("candidate_sha256", ""), ("candidate_sha256", ["a" * 64]),
    ("producer_key", "A" * 64), ("run_id", ""), ("extra", True),
])
def test_malformed_detector_authorization_is_rejected(tmp_path, field, value):
    f = fixture(tmp_path)
    document = deepcopy(f.jobs.investigation_approval)
    document["detector_authorization"][field] = value
    with pytest.raises(ContractError, match="exact source"):
        approval(document)


def test_expiry_before_or_during_reasoning_blocks_calls(tmp_path):
    f = fixture(tmp_path)
    f.jobs.investigation_approval["expires_at_utc"] = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(ContractError, match="approval"):
        f.jobs._candidate(f.payload)
    with pytest.raises(ContractError, match="approval"):
        f.jobs._model_approval(f.candidate)
    assert f.transport.calls == []


def test_expiry_during_inference_prevents_the_next_model_request(tmp_path):
    f = fixture(tmp_path)
    respond = f.transport.respond

    def expire_after_response(*args, **kwargs):
        response = respond(*args, **kwargs)
        f.jobs.investigation_approval["expires_at_utc"] = iso(
            datetime.now(timezone.utc) - timedelta(seconds=1))
        return response

    f.transport.respond = expire_after_response
    with pytest.raises(ContractError, match="approval"):
        f.worker.process_once("reasoning", job_id=f.admitted_id)
    assert len(f.transport.calls) == 1
    assert f.store.get_job(f.admitted_id)["status"] == "failed"


def test_detector_analysis_still_requires_genuine_review_before_lifecycle(tmp_path):
    f = fixture(tmp_path)
    assert f.worker.process_once("reasoning", job_id=f.admitted_id)
    assert f.worker.process_once("projection")
    with pytest.raises(ContractError, match="closure"):
        f.jobs.enqueue_postmortem(f.candidate["incident_id"])
    assert len({name for name, _ in f.transport.calls}) == 7


def test_detector_batch_is_one_attempt_per_kind_even_with_alternate_job_key(tmp_path):
    f = fixture(tmp_path)
    assert f.worker.process_once("reasoning", job_id=f.admitted_id)
    assert f.worker.process_once("projection")
    calls = len(f.transport.calls)
    alternate = f.store.enqueue(WorkRequest("reasoning", "another-key", f.payload))
    with pytest.raises(IntegrityConflict, match="already reserved"):
        f.worker.process_once("reasoning", job_id=alternate)
    assert len(f.transport.calls) == calls


def test_completed_result_replay_after_expiry_is_model_free(tmp_path):
    f = fixture(tmp_path)
    lease = f.store.claim("reasoning", "offline-worker", job_id=f.admitted_id)
    result = f.jobs.reasoning(lease, lambda: None)
    calls = len(f.transport.calls)
    f.jobs.investigation_approval["expires_at_utc"] = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    assert f.jobs.reasoning(lease, lambda: None) == result
    assert len(f.transport.calls) == calls
