from types import SimpleNamespace
import sqlite3

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.contracts import ContractError
from agentic_itops.consumer.storage import LocalSQLiteWorkflowStore
from agentic_itops.consumer.storage_contract import WorkRequest
from agentic_itops.consumer.worker import ConsumerWorker
from tests.test_consumer_processing import setup_batches
from tests.test_consumer_observations import SCOPE


def setup(tmp_path):
    evidence, references, batches, scope, policy = setup_batches(tmp_path)
    path = tmp_path / "worker.db"
    store = LocalSQLiteWorkflowStore(lambda: sqlite3.connect(path))
    store.bootstrap_schema()
    worker = ConsumerWorker(store, evidence, SimpleNamespace(producer=SCOPE),
                            {scope.run_id: scope}, [policy], "worker-1")
    return worker, store, evidence, references, batches, scope


def test_incomplete_then_complete_ingestion_durably_enqueues_one_reasoning_job(tmp_path):
    worker, store, _, references, batches, _ = setup(tmp_path)
    first = store.commit_ingestion(batches[0], references[0])
    assert worker.process_once("detector")
    detector_id = next(job for job in first.job_ids if store.get_job(job)["kind"] == "detector")
    assert store.get_job(detector_id)["result"]["coverage"] == "incomplete"
    assert store.get_job(detector_id)["next_job_ids"] == ()
    second = store.commit_ingestion(batches[1], references[1])
    assert worker.process_once("detector")
    detector_id = next(job for job in second.job_ids if store.get_job(job)["kind"] == "detector")
    assert len(store.get_job(detector_id)["next_job_ids"]) == 1
    while worker.process_once("coverage"):
        pass
    first_lease = store.claim("reasoning", "reasoner")
    assert first_lease is not None and first_lease.payload["execution_allowed"] is False
    assert store.claim("reasoning", "another") is None


def test_other_scope_work_is_not_claimed_or_terminally_failed(tmp_path):
    worker, store, _, _, _, _ = setup(tmp_path)
    job_id = store.enqueue(WorkRequest("detector", "bad-scope", {
        "producer_key": SCOPE.key, "run_id": "unauthorized", "IsSynthetic": True,
        "OperationalUse": "prohibited", "execution_allowed": False,
    }))
    assert worker.process_once("detector") is False
    job = store.get_job(job_id)
    assert job["status"] == "queued" and job["error_code"] is None
    assert job["result"] is None and job["next_job_ids"] == ()


def test_unbound_reasoning_handler_cannot_claim_or_complete_a_job(tmp_path):
    worker, store, _, _, _, _ = setup(tmp_path)
    job_id = store.enqueue(WorkRequest("reasoning", "unbound", {}))
    with pytest.raises(ContractError, match="no bound"):
        worker.process_once("reasoning")
    assert store.get_job(job_id)["status"] == "queued"


def test_multiline_model_validation_failure_is_terminal_and_preserves_original_error(tmp_path):
    from jsonschema import Draft202012Validator, ValidationError

    worker, store, _, _, _, scope = setup(tmp_path)
    def fail(lease, guard):
        Draft202012Validator({"type": "object", "required": ["summary"]}).validate({})
    worker.handlers["reasoning"] = fail
    job_id = store.enqueue(WorkRequest("reasoning", "invalid-model", {
        "producer_key": SCOPE.key, "run_id": scope.run_id,
        "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }))
    with pytest.raises(ValidationError):
        worker.process_once("reasoning")
    job = store.get_job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "ValidationError"
    assert "\\n" in job["error_message"] and "\n" not in job["error_message"]
    assert store.claim("reasoning", "another-worker") is None


def test_lease_loss_during_blob_write_cannot_commit_ingestion_or_detector_work(tmp_path):
    from datetime import datetime, timedelta, timezone
    from agentic_itops.consumer.logs import CompleteBatch
    from agentic_itops.consumer.storage_contract import LostLease

    evidence, _, batches, scope, policy = setup_batches(tmp_path)
    clock = [datetime.now(timezone.utc)]
    path = tmp_path / "fenced.db"
    store = LocalSQLiteWorkflowStore(lambda: sqlite3.connect(path), clock=lambda: clock[0])
    store.bootstrap_schema()
    payload = {"producer_key": SCOPE.key, "run_id": scope.run_id, "IsSynthetic": True,
               "OperationalUse": "prohibited", "execution_allowed": False}
    job_id = store.enqueue(WorkRequest("ingest", "scan", payload))

    class Reader:
        producer = SCOPE
        def read(self, window):
            records = tuple(record for batch in batches for record in batch.records
                            if window.ingestion_start <= record.ingested_at < window.ingestion_end)
            return CompleteBatch(SCOPE, window, records, 0, len(records))

    class SlowEvidence:
        def put(self, content):
            reference = evidence.put(content)
            clock[0] += timedelta(seconds=181)
            replacement = store.claim("ingest", "replacement", scopes=((SCOPE.key, scope.run_id),))
            assert replacement.fencing_token == 2
            return reference
        def read(self, reference):
            return evidence.read(reference)

    worker = ConsumerWorker(store, SlowEvidence(), Reader(), {scope.run_id: scope}, [policy], "old-owner")
    with pytest.raises(LostLease):
        worker.process_once("ingest")
    assert store.lookup_evidence(SCOPE, scope.run_id, scope.baseline_start, scope.reference_time) == ()
    assert store.claim("detector", "observer") is None
    assert store.claim("coverage", "observer") is None
    assert store.get_job(job_id)["owner"] == "replacement"
