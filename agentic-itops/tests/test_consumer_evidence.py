from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.contracts import canonical
from agentic_itops.consumer.evidence import IngestionService, LocalEvidenceStore, evidence_ref
from agentic_itops.consumer.logs import IncompleteQuery, WindowTooLarge, decode_response
from agentic_itops.consumer.storage_contract import EvidenceRef, IngestCommit, IntegrityConflict
from tests.test_consumer_observations import ROWS, SCOPE, response, window


def test_immutable_content_survives_restart_and_concurrent_put(tmp_path):
    content = canonical({"synthetic": True, "records": [1, 2, 3]})
    store = LocalEvidenceStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        references = list(executor.map(store.put, [content] * 12))
    assert len(set(references)) == 1
    assert LocalEvidenceStore(tmp_path).read(references[0]) == content
    assert len(list((tmp_path / "evidence").iterdir())) == 1


def test_corruption_never_becomes_a_successful_duplicate(tmp_path):
    store = LocalEvidenceStore(tmp_path)
    reference = store.put(b'{"evidence":"original"}')
    (tmp_path / "evidence" / f"{reference.sha256}.json").write_bytes(b"corrupted")
    with pytest.raises(IntegrityConflict):
        store.read(reference)
    with pytest.raises(IntegrityConflict):
        store.put(b'{"evidence":"original"}')


def test_reference_cannot_escape_store(tmp_path):
    store = LocalEvidenceStore(tmp_path)
    with pytest.raises(IntegrityConflict):
        store.read(EvidenceRef("..\\outside.json", "0" * 64, 1))


class RecordingWorkflow:
    def __init__(self, evidence, *, fail=False):
        self.evidence, self.fail = evidence, fail
        self.committed = []

    def commit_ingestion(self, batch, reference):
        assert self.evidence.read(reference) == canonical(batch.to_dict())
        if self.fail:
            raise RuntimeError("transaction failed")
        self.committed.append((batch, reference))
        return IngestCommit(batch.key, len(batch.records), batch.duplicate_count, ("detect-job",))


def reader():
    return SimpleNamespace(read=lambda selected: decode_response(response([ROWS[0]]), SCOPE, selected))


def test_blob_is_verified_before_transaction(tmp_path):
    evidence = LocalEvidenceStore(tmp_path)
    workflow = RecordingWorkflow(evidence)
    commits = IngestionService(reader(), evidence, workflow).ingest(window(ROWS[0]))
    assert len(commits) == 1 and len(workflow.committed) == 1


def test_sql_failure_leaves_only_reconcilable_orphan(tmp_path):
    evidence = LocalEvidenceStore(tmp_path)
    workflow = RecordingWorkflow(evidence, fail=True)
    with pytest.raises(RuntimeError, match="transaction failed"):
        IngestionService(reader(), evidence, workflow).ingest(window(ROWS[0]))
    assert not workflow.committed
    assert len(list((tmp_path / "evidence").iterdir())) == 1


def test_partial_query_writes_neither_blob_nor_checkpoint(tmp_path):
    evidence = LocalEvidenceStore(tmp_path)
    workflow = RecordingWorkflow(evidence)
    def incomplete(_):
        raise IncompleteQuery("partial")
    with pytest.raises(IncompleteQuery):
        IngestionService(SimpleNamespace(read=incomplete), evidence, workflow).ingest(window(ROWS[0]))
    assert not workflow.committed
    assert not list((tmp_path / "evidence").iterdir())


def test_bad_blob_store_cannot_commit(tmp_path):
    evidence = SimpleNamespace(put=lambda content: evidence_ref(content), read=lambda ref: b"wrong")
    workflow = RecordingWorkflow(evidence)
    with pytest.raises(IntegrityConflict):
        IngestionService(reader(), evidence, workflow).ingest(window(ROWS[0]))
    assert not workflow.committed


def test_response_limit_subdivides_into_nonoverlapping_receipts(tmp_path):
    original = window(ROWS[0])
    visited = []
    def bounded(selected):
        visited.append(selected)
        if selected.ingestion_end - selected.ingestion_start > timedelta(seconds=30):
            raise WindowTooLarge("partition")
        return decode_response(response([]), SCOPE, selected)
    evidence = LocalEvidenceStore(tmp_path)
    workflow = RecordingWorkflow(evidence)
    results = IngestionService(SimpleNamespace(read=bounded), evidence, workflow).ingest(original)
    assert len(results) == 2
    left, right = (item[0].window for item in workflow.committed)
    assert left.ingestion_end == right.ingestion_start
    assert left.ingestion_start == original.ingestion_start
    assert right.ingestion_end == original.ingestion_end
    assert all(not item[0].records for item in workflow.committed)


def test_unpartitionable_boundary_remains_incomplete(tmp_path):
    original = window(ROWS[0])
    selected = replace(original, ingestion_end=original.ingestion_start + timedelta(seconds=1))
    def oversized(_):
        raise WindowTooLarge("same timestamp")
    evidence = LocalEvidenceStore(tmp_path)
    workflow = RecordingWorkflow(evidence)
    with pytest.raises(WindowTooLarge, match="catch-up"):
        IngestionService(SimpleNamespace(read=oversized), evidence, workflow).ingest(selected)
    assert not workflow.committed
