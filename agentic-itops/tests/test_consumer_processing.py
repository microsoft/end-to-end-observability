from dataclasses import replace
from datetime import timedelta
import sqlite3

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.contracts import ContractError, canonical, strict_json
from agentic_itops.consumer.evidence import LocalEvidenceStore
from agentic_itops.consumer.logs import CompleteBatch, QueryRecord, QueryWindow
from agentic_itops.consumer.processing import AnalysisScope, persist_snapshot, process_snapshot
from agentic_itops.consumer.storage import LocalSQLiteWorkflowStore
from tests.test_consumer_detectors import series
from tests.test_consumer_observations import SCOPE


def setup_batches(tmp_path):
    observations, policy, start, end = series("upstream", "step-up")
    evidence = LocalEvidenceStore(tmp_path / "evidence")
    references = []
    batches = []
    for index in (0, 1):
        lower, upper = start + timedelta(minutes=index * 8), start + timedelta(minutes=(index + 1) * 8)
        window = QueryWindow(observations[0].row["RunId"], ("upstream",), start, end,
                             lower, upper, start, end)
        records = tuple(QueryRecord(item, start + timedelta(minutes=i))
                        for i, item in enumerate(observations) if index * 8 <= i < (index + 1) * 8)
        batch = CompleteBatch(SCOPE, window, records, 0, len(records))
        batches.append(batch)
        references.append(evidence.put(canonical(batch.to_dict())))
    scope = AnalysisScope(SCOPE, observations[0].row["RunId"], ("upstream",), start, end, start, end)
    return evidence, tuple(references), batches, scope, policy


def test_only_a_complete_multi_batch_snapshot_can_produce_candidates(tmp_path):
    evidence, references, _, scope, policy = setup_batches(tmp_path)
    incomplete = process_snapshot(scope, references[:1], evidence, [policy])
    assert incomplete["coverage"] == "incomplete" and incomplete["candidates"] == []
    assert incomplete["condition"] == "insufficient_evidence"
    complete = process_snapshot(scope, references, evidence, [policy])
    assert complete["coverage"] == "complete_as_queried" and len(complete["candidates"]) == 1
    assert complete["execution_allowed"] is False
    reference = persist_snapshot(complete, evidence)
    assert strict_json(evidence.read(reference)) == complete


def test_stored_receipt_roundtrips_and_rejects_forged_completeness(tmp_path):
    evidence, references, batches, _, _ = setup_batches(tmp_path)
    assert CompleteBatch.from_document(evidence.read(references[0])).to_dict() == batches[0].to_dict()
    changed = batches[0].to_dict()
    changed["query_complete"] = False
    with pytest.raises(ContractError, match="canonical"):
        CompleteBatch.from_document(canonical(changed))
    changed = batches[0].to_dict()
    changed["records"][0]["source_sha256"] = "f" * 64
    with pytest.raises(ContractError, match="digest"):
        CompleteBatch.from_document(canonical(changed))


def test_disabled_policy_cannot_create_a_reasoning_candidate(tmp_path):
    evidence, references, _, scope, policy = setup_batches(tmp_path)
    result = process_snapshot(scope, references, evidence, [replace(policy, enabled=False)])
    assert result["analyses"][0]["condition"] == "unsupported"
    assert result["candidates"] == []


def test_run_boundaries_cannot_be_substituted(tmp_path):
    evidence, references, _, scope, policy = setup_batches(tmp_path)
    with pytest.raises(ContractError, match="boundary"):
        process_snapshot(replace(scope, run_id="another-run"), references, evidence, [policy])


def test_sql_committed_evidence_survives_restart_and_feeds_detector(tmp_path):
    evidence, references, batches, scope, policy = setup_batches(tmp_path)
    database = tmp_path / "workflow.db"
    factory = lambda: sqlite3.connect(database)
    store = LocalSQLiteWorkflowStore(factory)
    store.bootstrap_schema()
    for batch, reference in zip(batches, references):
        store.commit_ingestion(batch, reference)
    restarted = LocalSQLiteWorkflowStore(factory)
    receipts = restarted.lookup_evidence(SCOPE, scope.run_id, scope.baseline_start, scope.reference_time)
    recovered_refs = tuple(item["evidence"] for item in receipts)
    report = process_snapshot(scope, recovered_refs, evidence, [policy])
    assert len(report["candidates"]) == 1
    assert restarted.claim("detector", "worker-1") is not None
