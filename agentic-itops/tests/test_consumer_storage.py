"""Offline contract regressions, NOT evidence of SQL Server execution or locking."""

from concurrent.futures import ThreadPoolExecutor
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest

from agentic_itops.consumer.contracts import ContractError, ProducerScope, canonical, digest, utc
from agentic_itops.consumer.logs import decode_response
from agentic_itops.consumer.storage import LocalSQLiteWorkflowStore, SqlServerWorkflowStore
from agentic_itops.consumer.storage_contract import (
    EvidenceRef, IntegrityConflict, LostLease, WorkRequest,
)
from tests.test_consumer_observations import ROWS, SCOPE, response, window


class Clock:
    def __init__(self):
        self.now = utc("2026-09-15T12:00:00Z")

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def database():
    # A shared in-memory database survives store/connection restarts without
    # putting tenant-like database files in the repository or touching temp dirs.
    uri = f"file:consumer-test-{uuid4().hex}?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True)

    def factory():
        return sqlite3.connect(uri, uri=True, timeout=5)

    clock = Clock()
    store = LocalSQLiteWorkflowStore(factory, clock=clock)
    store.bootstrap_schema()
    yield store, factory, clock, keeper
    keeper.close()


def batch_for(rows=None, *, producer=SCOPE, selected_window=None):
    rows = [deepcopy(ROWS[0])] if rows is None else rows
    return decode_response(response(rows), producer, selected_window or window(ROWS[0]))


def reference(batch, key=None):
    content = canonical(batch.to_dict())
    sha = hashlib.sha256(content).hexdigest()
    return EvidenceRef(key or "raw/" + sha + ".json", sha, len(content))


def work(key="work-1", payload=None, kind="analyze"):
    return WorkRequest(kind, key, {"value": 1} if payload is None else payload)


def counts(connection):
    return tuple(connection.execute(f"SELECT COUNT(*) FROM consumer_{table}").fetchone()[0]
                 for table in ("events", "batches", "batch_events", "jobs", "results"))


def test_constructor_has_no_connection_or_implicit_bootstrap():
    def forbidden():
        pytest.fail("construction must not connect")
    SqlServerWorkflowStore(forbidden)
    LocalSQLiteWorkflowStore(forbidden)


def test_explicit_bootstrap_is_repeatable_without_deleting_data(database):
    store, _, _, keeper = database
    job = store.enqueue(work())
    store.bootstrap_schema()
    assert store.get_job(job)["status"] == "queued"
    assert counts(keeper) == (0, 0, 0, 1, 0)


def test_duplicate_replay_across_store_restarts_returns_original_receipt(database):
    store, factory, clock, keeper = database
    batch = batch_for()
    first = store.commit_ingestion(batch, reference(batch))
    restarted = LocalSQLiteWorkflowStore(factory, clock=clock)
    # Re-uploading identical bytes at another location does not replace the ledger.
    second = restarted.commit_ingestion(batch, reference(batch, "raw/another-location"))
    assert second == first
    assert (first.inserted_events, first.duplicate_events) == (1, 0)
    assert counts(keeper) == (1, 1, 1, 2, 0)
    entries = restarted.lookup_evidence(SCOPE, batch.window.run_id,
                                       batch.window.observed_start, batch.window.observed_end)
    assert entries[0]["evidence"] == reference(batch)
    assert entries[0]["receipt"] == first


def test_replay_survives_database_image_in_a_new_process(database):
    store, _, _, keeper = database
    batch = batch_for()
    receipt = store.commit_ingestion(batch, reference(batch))
    # Transfer a committed SQLite image instead of creating database/temp files.
    # This tests process-independent persisted semantics, not disk crash recovery.
    script = """
import base64, json, sqlite3, sys
from agentic_itops.consumer.storage import LocalSQLiteWorkflowStore
from tests.test_consumer_storage import batch_for, reference
connection = sqlite3.connect(":memory:")
connection.deserialize(base64.b64decode(sys.stdin.read()))
class Borrowed:
    def __getattr__(self, key):
        return getattr(connection, key)
    def close(self):
        pass
store = LocalSQLiteWorkflowStore(Borrowed)
batch = batch_for()
receipt = store.commit_ingestion(batch, reference(batch))
assert store.get_job(receipt.job_ids[0])["status"] == "queued"
print(json.dumps([receipt.batch_id, receipt.inserted_events, receipt.duplicate_events,
                  list(receipt.job_ids)]))
connection.close()
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        input=base64.b64encode(keeper.serialize()).decode("ascii"),
        text=True, capture_output=True, timeout=30, cwd=Path(__file__).resolve().parents[1],
    )
    assert process.returncode == 0, process.stderr
    from agentic_itops.consumer.contracts import strict_json
    assert strict_json(process.stdout) == [
        receipt.batch_id, receipt.inserted_events, receipt.duplicate_events, list(receipt.job_ids),
    ]


def test_same_event_changed_source_content_rolls_back_without_receipt_or_jobs(database):
    store, _, _, keeper = database
    original = batch_for()
    store.commit_ingestion(original, reference(original))
    changed = deepcopy(ROWS[0])
    changed["Context"]["storage_test"] = "changed"
    conflicting = batch_for([changed])
    with pytest.raises(IntegrityConflict, match="source content"):
        store.commit_ingestion(conflicting, reference(conflicting))
    assert counts(keeper) == (1, 1, 1, 2, 0)


def test_timegenerated_only_variant_is_duplicate_event_but_new_evidence_revision(database):
    store, _, _, keeper = database
    first = batch_for()
    original = store.commit_ingestion(first, reference(first))
    row = deepcopy(ROWS[0])
    row["TimeGenerated"] = utc(row["ObservedAt"]) + timedelta(seconds=1)
    variant = batch_for([row])
    replay = store.commit_ingestion(variant, reference(variant))
    assert replay.batch_id != original.batch_id
    assert (replay.inserted_events, replay.duplicate_events) == (0, 1)
    assert counts(keeper) == (1, 2, 2, 4, 0)


def test_same_window_late_indexed_evidence_is_a_new_revision(database):
    store, _, _, keeper = database
    first = batch_for()
    original = store.commit_ingestion(first, reference(first))
    late = deepcopy(ROWS[0])
    late["EventId"] = digest("late indexed observation")
    second = batch_for([ROWS[0], late])
    revised = store.commit_ingestion(second, reference(second))
    assert original.batch_id != revised.batch_id
    assert (revised.inserted_events, revised.duplicate_events) == (1, 1)
    assert counts(keeper) == (2, 2, 3, 4, 0)


def test_empty_window_can_later_gain_evidence_without_rewriting_coverage(database):
    store, _, _, keeper = database
    empty, present = batch_for([]), batch_for()
    first = store.commit_ingestion(empty, reference(empty))
    second = store.commit_ingestion(present, reference(present))
    assert first.batch_id != second.batch_id and len(first.job_ids) == 1
    assert second.inserted_events == 1 and len(second.job_ids) == 2
    entries = store.lookup_evidence(SCOPE, empty.window.run_id,
                                    empty.window.observed_start, empty.window.observed_end)
    assert {entry["coverage"] for entry in entries} == {"no_observations", "observations_present"}
    assert counts(keeper) == (1, 2, 1, 3, 0)


def test_source_duplicate_count_is_preserved(database):
    store, _, _, _ = database
    batch = batch_for([ROWS[0], ROWS[0]])
    receipt = store.commit_ingestion(batch, reference(batch))
    assert (receipt.inserted_events, receipt.duplicate_events) == (1, 1)


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("byte_length", 0),
                                        ("byte_length", True)])
def test_evidence_hash_and_length_are_validated_before_sql(database, field, value):
    store, _, _, keeper = database
    batch = batch_for()
    with pytest.raises(IntegrityConflict, match="SHA/length"):
        store.commit_ingestion(batch, replace(reference(batch), **{field: value}))
    assert counts(keeper) == (0, 0, 0, 0, 0)


def test_producer_case_normalized_and_lookup_run_case_and_spaces_remain_exact(database):
    store, _, _, keeper = database
    batch = batch_for()
    receipt = store.commit_ingestion(batch, reference(batch))
    scope = ProducerScope(SCOPE.workspace_resource_id.upper())
    assert scope.key == SCOPE.key
    variant = batch_for(producer=scope)
    other_receipt = store.commit_ingestion(variant, reference(variant))
    assert other_receipt.inserted_events == 0
    lookup = lambda producer, run: store.lookup_evidence(
        producer, run, batch.window.observed_start, batch.window.observed_end)
    assert receipt in [entry["receipt"] for entry in lookup(scope, batch.window.run_id)]
    assert lookup(scope, batch.window.run_id.swapcase()) == ()
    assert lookup(scope, batch.window.run_id + " ") == ()
    assert lookup(ProducerScope(SCOPE.workspace_resource_id + "-other"), batch.window.run_id) == ()
    assert counts(keeper)[:2] == (1, 2)


def test_forged_complete_batch_scope_is_not_trusted(database):
    store, _, _, keeper = database
    batch = batch_for()
    forged = replace(batch, producer=ProducerScope(SCOPE.workspace_resource_id + "-other"))
    with pytest.raises(ContractError, match="scope|producer"):
        store.commit_ingestion(forged, reference(forged))
    assert counts(keeper) == (0, 0, 0, 0, 0)


def test_empty_complete_subwindows_never_cover_gaps_or_mean_healthy(database):
    store, _, _, _ = database
    first_window = window(ROWS[0])
    last_window = replace(first_window,
                          ingestion_start=first_window.ingestion_start + timedelta(minutes=2),
                          ingestion_end=first_window.ingestion_end + timedelta(minutes=2))
    for selected in (first_window, last_window):
        batch = batch_for([], selected_window=selected)
        receipt = store.commit_ingestion(batch, reference(batch))
        assert receipt.inserted_events == 0
        assert len(receipt.job_ids) == 1
        job = store.get_job(receipt.job_ids[0])
        assert job["kind"] == "coverage"
        assert job["payload"]["coverage"] == "no_observations"
        assert job["payload"]["execution_allowed"] is False
    entries = store.lookup_evidence(SCOPE, first_window.run_id,
                                    first_window.observed_start, first_window.observed_end)
    assert len(entries) == 2
    assert entries[0]["window"]["ingestion_end"] != entries[1]["window"]["ingestion_start"]
    assert {entry["coverage"] for entry in entries} == {"no_observations"}


def test_ingestion_failure_after_jobs_insert_rolls_back_everything(database):
    store, _, _, keeper = database
    keeper.execute("CREATE TRIGGER reject_batch BEFORE INSERT ON consumer_batches "
                   "BEGIN SELECT RAISE(ABORT, 'simulated SQL failure'); END")
    batch = batch_for()
    with pytest.raises(sqlite3.IntegrityError, match="simulated SQL failure"):
        store.commit_ingestion(batch, reference(batch))
    assert counts(keeper) == (0, 0, 0, 0, 0)
    keeper.execute("DROP TRIGGER reject_batch")
    assert store.commit_ingestion(batch, reference(batch)).inserted_events == 1


def test_work_idempotency_uses_canonical_payload_and_conflicts_explicitly(database):
    store, factory, clock, keeper = database
    first = store.enqueue(work(payload={"a": 1, "b": 2}))
    restarted = LocalSQLiteWorkflowStore(factory, clock=clock)
    assert restarted.enqueue(work(payload={"b": 2, "a": 1})) == first
    with pytest.raises(IntegrityConflict, match="different content"):
        restarted.enqueue(work(payload={"a": 2, "b": 2}))
    assert counts(keeper)[3] == 1


def test_opaque_work_kind_key_and_owner_are_not_casefolded_or_trimmed(database):
    store, _, _, _ = database
    ids = [store.enqueue(work(key=key, kind=kind)) for kind, key in
           (("analyze", "Key"), ("analyze", "key"), ("Analyze", "Key"), ("analyze ", "Key"))]
    assert len(set(ids)) == 4
    lease = store.claim("Analyze", "Owner")
    assert lease.job_id == ids[2]
    with pytest.raises(LostLease):
        store.complete(replace(lease, owner="owner"), {})
    with pytest.raises(LostLease):
        store.fail(replace(lease, owner="Owner "), "error", "not owner")


def test_exclusive_claim_and_expiry_reclaim_increase_fencing(database):
    store, factory, clock, _ = database
    other = LocalSQLiteWorkflowStore(factory, clock=clock)
    job = store.enqueue(work())
    first = store.claim("analyze", "worker-1", 10)
    assert first.job_id == job and first.fencing_token == 1
    assert other.claim("analyze", "worker-2", 10) is None
    clock.advance(10)
    second = other.claim("analyze", "worker-2", 10)
    assert second.job_id == job and second.fencing_token == 2
    for action in (lambda: store.complete(first, {}),
                   lambda: store.fail(first, "late", "stale failure"),
                   lambda: store.renew(first)):
        with pytest.raises(LostLease):
            action()
    other.complete(second, {"analysis": "done"})
    assert store.get_job(job)["status"] == "completed"
    assert store.claim("analyze", "worker-3") is None


def test_simultaneous_local_claims_never_grant_two_leases(database):
    store, factory, clock, _ = database
    store.enqueue(work())

    def claim(index):
        try:
            return LocalSQLiteWorkflowStore(factory, clock=clock).claim("analyze", f"worker-{index}")
        except sqlite3.OperationalError as exc:
            # Shared-memory SQLite uses immediate SQLITE_LOCKED, not server READPAST.
            assert "locked" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        leases = list(executor.map(claim, range(4)))
    assert len([lease for lease in leases if lease is not None]) == 1


@pytest.mark.parametrize("operation", ["complete", "fail", "renew"])
def test_expired_lease_rejected_even_without_reclaim(database, operation):
    store, _, clock, _ = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker", 1)
    clock.advance(1)
    with pytest.raises(LostLease):
        if operation == "complete":
            store.complete(lease, {})
        elif operation == "fail":
            store.fail(lease, "timeout", "late")
        else:
            store.renew(lease)
    assert store.get_job(job)["status"] == "leased"
    assert store.get_job(job)["result"] is None


@pytest.mark.parametrize("operation", ["complete", "fail"])
def test_expired_lease_takes_precedence_over_invalid_result_or_error(database, operation):
    store, _, clock, _ = database
    store.enqueue(work())
    lease = store.claim("analyze", "worker", 1)
    clock.advance(1)
    with pytest.raises(LostLease):
        if operation == "complete":
            store.complete(lease, {"execution_allowed": True})
        else:
            store.fail(lease, "", "")


def test_renew_uses_stored_lease_and_keeps_fence(database):
    store, _, clock, _ = database
    store.enqueue(work())
    first = store.claim("analyze", "worker", 1)
    clock.advance(0.5)
    renewed = store.renew(first, 10)
    assert renewed.fencing_token == first.fencing_token
    assert renewed.expires_at == clock.now + timedelta(seconds=10)
    clock.advance(1)
    store.complete(renewed, {})


@pytest.mark.parametrize("change", [
    {"owner": "another-worker"}, {"fencing_token": 2}, {"kind": "another-kind"},
])
def test_renew_rejects_mismatched_lease_without_changing_stored_expiry(database, change):
    store, _, clock, _ = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker", 10)
    clock.advance(1)
    with pytest.raises(LostLease):
        store.renew(replace(lease, **change))
    assert store.get_job(job)["expires_at"] == lease.expires_at
    assert store.get_job(job)["fencing_token"] == lease.fencing_token


@pytest.mark.parametrize("terminal", ["complete", "fail"])
def test_renew_cannot_reopen_terminal_work(database, terminal):
    store, _, _, _ = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker")
    if terminal == "complete":
        store.complete(lease, {})
    else:
        store.fail(lease, "STOPPED", "visible failure")
    with pytest.raises(LostLease):
        store.renew(lease)
    assert store.get_job(job)["expires_at"] is None


def test_result_and_next_work_outbox_commit_atomically(database):
    store, _, _, keeper = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker")
    follow_up = work("next", {"candidate": "synthetic"}, "review")
    store.complete(lease, {"finding": "synthetic anomaly"}, (follow_up,))
    saved = store.get_job(job)
    assert saved["status"] == "completed"
    assert saved["result"]["execution_allowed"] is False
    assert saved["result"]["IsSynthetic"] is True
    assert saved["result"]["OperationalUse"] == "prohibited"
    assert saved["result_sha256"] == digest(saved["result"])
    assert saved["next_job_ids"] == (store.enqueue(follow_up),)
    assert store.get_job(saved["next_job_ids"][0])["status"] == "queued"
    assert counts(keeper)[3:] == (2, 1)
    with pytest.raises(LostLease):
        store.complete(lease, {"overwritten": True})


def test_follow_up_conflict_rolls_back_result_and_other_follow_ups(database):
    store, _, _, keeper = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker")
    store.enqueue(work("existing", {"v": 1}, "review"))
    with pytest.raises(IntegrityConflict):
        store.complete(lease, {}, (work("new", kind="review"),
                                   work("existing", {"v": 2}, "review")))
    assert store.get_job(job)["status"] == "leased"
    assert store.get_job(job)["result"] is None
    assert counts(keeper)[3:] == (2, 0)


def test_result_insert_failure_rolls_back_queued_follow_up(database):
    store, _, _, keeper = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker")
    keeper.execute("CREATE TRIGGER reject_result BEFORE INSERT ON consumer_results "
                   "BEGIN SELECT RAISE(ABORT, 'result persistence failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="result persistence failed"):
        store.complete(lease, {}, (work("next", kind="review"),))
    assert store.get_job(job)["status"] == "leased"
    assert counts(keeper)[3:] == (1, 0)


def test_lease_expiring_during_completion_rolls_back_result_and_outbox(database, monkeypatch):
    store, _, clock, keeper = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker", 5)
    enqueue = store._enqueue

    def slow_enqueue(cursor, request):
        result = enqueue(cursor, request)
        clock.advance(5)
        return result

    monkeypatch.setattr(store, "_enqueue", slow_enqueue)
    with pytest.raises(LostLease):
        store.complete(lease, {}, (work("next", kind="review"),))
    assert store.get_job(job)["status"] == "leased"
    assert counts(keeper)[3:] == (1, 0)


def test_failure_is_durable_visible_and_never_success_shaped(database):
    store, factory, clock, _ = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker")
    store.fail(lease, "MODEL_UNAVAILABLE", "Approval-gated dependency was not called")
    restarted = LocalSQLiteWorkflowStore(factory, clock=clock)
    failed = restarted.get_job(job)
    assert failed["status"] == "failed" and failed["result"] is None
    assert failed["error_code"] == "MODEL_UNAVAILABLE"
    assert failed["error_message"] == "Approval-gated dependency was not called"
    assert failed["finished_at"] == clock.now
    assert restarted.claim("analyze", "other") is None
    with pytest.raises(LostLease):
        restarted.fail(lease, "OTHER", "cannot overwrite")


def test_forged_lease_payload_and_expiry_never_override_stored_authority(database):
    store, _, clock, _ = database
    payload = {"producer_key": "trusted-config-derived-by-caller", "run_id": "demo-run"}
    job = store.enqueue(work(payload=payload))
    lease = store.claim("analyze", "worker", 1)
    forged = replace(lease, payload={"producer_key": "different"},
                     expires_at=clock.now + timedelta(days=1))
    clock.advance(1)
    with pytest.raises(LostLease):
        store.complete(forged, {})
    assert store.get_job(job)["payload"] == payload


def ingest_work(batch, *, producer_key=None, run_id=None, kind="ingest"):
    return WorkRequest(kind, "ingest-test", {
        "producer_key": producer_key or batch.producer.key,
        "run_id": run_id or batch.window.run_id,
    })


@pytest.mark.parametrize("reclaim", [False, True])
def test_stale_ingestion_after_blob_write_cannot_persist_receipt_or_outbox(database, reclaim):
    store, _, clock, keeper = database
    batch = batch_for()
    job = store.enqueue(ingest_work(batch))
    lease = store.claim("ingest", "old-worker", 1)
    blob_ref = reference(batch)
    clock.advance(1)
    if reclaim:
        replacement = store.claim("ingest", "new-worker")
        assert replacement.fencing_token == lease.fencing_token + 1
    with pytest.raises(LostLease):
        store.commit_ingestion(batch, blob_ref, lease=lease)
    assert counts(keeper) == (0, 0, 0, 1, 0)
    assert store.get_job(job)["result"] is None


def test_stale_ingest_lease_cannot_take_idempotent_receipt_shortcut(database):
    store, _, clock, keeper = database
    batch = batch_for()
    store.enqueue(ingest_work(batch))
    lease = store.claim("ingest", "worker", 1)
    first = store.commit_ingestion(batch, reference(batch), lease=lease)
    assert store.commit_ingestion(batch, reference(batch), lease=lease) == first
    before = counts(keeper)
    clock.advance(1)
    store.claim("ingest", "replacement")
    with pytest.raises(LostLease):
        store.commit_ingestion(batch, reference(batch), lease=lease)
    assert counts(keeper) == before


@pytest.mark.parametrize("scope_change", [
    {"producer_key": "other-producer"}, {"run_id": "another-run"},
])
def test_ingestion_binds_to_database_scope_not_forged_lease_payload(database, scope_change):
    store, _, _, keeper = database
    batch = batch_for()
    store.enqueue(ingest_work(batch, **scope_change))
    lease = store.claim("ingest", "worker")
    forged = replace(lease, payload={"producer_key": batch.producer.key, "run_id": batch.window.run_id})
    with pytest.raises(IntegrityConflict, match="stored ingest job scope"):
        store.commit_ingestion(batch, reference(batch), lease=forged)
    assert counts(keeper) == (0, 0, 0, 1, 0)


def test_correct_ingest_scope_ignores_mutated_caller_payload_and_keeps_lease(database):
    store, _, _, _ = database
    batch = batch_for()
    job = store.enqueue(ingest_work(batch))
    lease = store.claim("ingest", "worker")
    forged = replace(lease, payload={"producer_key": "mutated", "run_id": "mutated"})
    receipt = store.commit_ingestion(batch, reference(batch), lease=forged)
    assert receipt.inserted_events == 1
    stored = store.get_job(job)
    assert stored["status"] == "leased" and stored["fencing_token"] == lease.fencing_token


def test_ingestion_rejects_non_ingest_kind_even_with_matching_scope(database):
    store, _, _, keeper = database
    batch = batch_for()
    store.enqueue(ingest_work(batch, kind="detector"))
    lease = store.claim("detector", "worker")
    with pytest.raises(LostLease, match="ingest-kind"):
        store.commit_ingestion(batch, reference(batch), lease=lease)
    assert counts(keeper) == (0, 0, 0, 1, 0)


def test_ingestion_expiring_during_writes_rolls_back_all_new_data(database, monkeypatch):
    store, _, clock, keeper = database
    batch = batch_for()
    store.enqueue(ingest_work(batch))
    lease = store.claim("ingest", "worker", 1)
    enqueue = store._enqueue

    def slow_enqueue(cursor, request):
        value = enqueue(cursor, request)
        clock.advance(1)
        return value

    monkeypatch.setattr(store, "_enqueue", slow_enqueue)
    with pytest.raises(LostLease):
        store.commit_ingestion(batch, reference(batch), lease=lease)
    assert counts(keeper) == (0, 0, 0, 1, 0)


@pytest.mark.parametrize("nested", [False, True])
def test_scoped_claims_leave_other_run_queued_for_direct_and_nested_payload(database, nested):
    store, _, _, _ = database
    ids = {}
    for run in ("Run-A", "run-a", "Run-A ", "Run-B"):
        payload = {"producer_key": SCOPE.key, "run_id": run}
        if nested:
            payload = {"source": payload, "review": "synthetic"}
        ids[run] = store.enqueue(work(run, payload, "review"))
    chosen = store.claim("review", "worker-A", scopes=((SCOPE.key, "Run-A"),))
    assert chosen.job_id == ids["Run-A"]
    assert store.claim("review", "worker-A", scopes=((SCOPE.key, "Run-A"),)) is None
    for run, job in ids.items():
        assert store.get_job(job)["status"] == ("leased" if run == "Run-A" else "queued")
    other = store.claim("review", "worker-B", scopes=((SCOPE.key, "Run-B"),))
    assert other.job_id == ids["Run-B"]


def test_scoped_claim_matches_producer_and_run_as_one_pair(database):
    store, _, _, _ = database
    pairs = (("producer-A", "Run-A"), ("producer-B", "Run-B"))
    forbidden = store.enqueue(work("cross-pair", {"producer_key": "producer-A", "run_id": "Run-B"}))
    allowed = store.enqueue(work("allowed", {"producer_key": "producer-B", "run_id": "Run-B"}))
    assert store.claim("analyze", "worker", scopes=pairs).job_id == allowed
    assert store.get_job(forbidden)["status"] == "queued"


def test_empty_scopes_and_unscoped_jobs_fail_closed(database):
    store, _, _, _ = database
    job = store.enqueue(work())
    assert store.claim("analyze", "worker", scopes=()) is None
    assert store.claim("analyze", "worker", scopes=((SCOPE.key, "demo"),)) is None
    assert store.get_job(job)["status"] == "queued"
    assert store.claim("analyze", "standalone", scopes=None).job_id == job


def test_direct_and_nested_scope_disagreement_is_explicit_conflict(database):
    store, _, _, keeper = database
    with pytest.raises(IntegrityConflict, match="scopes disagree"):
        store.enqueue(work(payload={
            "producer_key": SCOPE.key, "run_id": "A",
            "source": {"producer_key": SCOPE.key, "run_id": "B"},
        }))
    assert counts(keeper)[3] == 0


def test_existing_job_scopes_require_explicit_backfill(database):
    store, _, _, keeper = database
    pair = (SCOPE.key, "legacy")
    direct = store.enqueue(work("direct", {"producer_key": pair[0], "run_id": pair[1]}))
    nested = store.enqueue(work("nested", {"source": {"producer_key": pair[0], "run_id": pair[1]}}))
    unscoped = store.enqueue(work("unscoped"))
    keeper.execute("UPDATE consumer_jobs SET scope_key=NULL")
    keeper.commit()
    store.bootstrap_schema()
    assert store.claim("analyze", "worker", scopes=(pair,)) is None
    assert store.backfill_job_scopes() == 2
    assert store.backfill_job_scopes() == 0
    assert store.claim("analyze", "worker", scopes=(pair,)).job_id in (direct, nested)
    assert store.get_job(unscoped)["scope_key"] is None


@pytest.mark.parametrize("result", [
    {"execution_allowed": True}, {"IsSynthetic": False}, {"OperationalUse": "allowed"},
])
def test_synthetic_results_cannot_authorize_execution(database, result):
    store, _, _, keeper = database
    job = store.enqueue(work())
    lease = store.claim("analyze", "worker")
    with pytest.raises(ContractError, match="physical execution"):
        store.complete(lease, result)
    assert store.get_job(job)["status"] == "leased"
    assert counts(keeper)[-1] == 0


@pytest.mark.parametrize("table", ["events", "batches", "batch_events", "results"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_ledgers_reject_mutation(database, table, operation):
    store, _, _, keeper = database
    batch = batch_for()
    store.commit_ingestion(batch, reference(batch))
    lease = store.claim("coverage", "worker")
    store.complete(lease, {})
    assignments = {"events": "source_sha256=source_sha256", "batches": "coverage=coverage",
                   "batch_events": "event_key=event_key", "results": "result_json=result_json"}
    sql = (f"UPDATE consumer_{table} SET {assignments[table]}" if operation == "UPDATE"
           else f"DELETE FROM consumer_{table}")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        keeper.execute(sql)
    keeper.rollback()


def test_unknown_job_is_explicit_not_success_fallback(database):
    with pytest.raises(KeyError):
        database[0].get_job("0" * 64)


@pytest.mark.parametrize("seconds", [0, -1, True, 1.5, 86401])
def test_invalid_lease_duration_is_rejected(database, seconds):
    with pytest.raises(ContractError):
        database[0].claim("analyze", "worker", seconds)


class RecordingConnection:
    """Records generated SQL only; deliberately does NOT emulate SQL execution."""
    autocommit = False
    description = ()

    def __init__(self):
        self.calls = []
        self.committed = self.rolled_back = self.closed = False

    def cursor(self):
        return self

    def execute(self, sql, parameters=()):
        self.calls.append((sql, parameters))
        return self

    def fetchone(self):
        return None

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_sql_server_claim_uses_server_clock_atomic_update_and_bound_parameters():
    connection = RecordingConnection()
    store = SqlServerWorkflowStore(lambda: connection)
    owner = "worker'; DROP TABLE dbo.consumer_jobs;--"
    assert store.claim("detector", owner, 30) is None
    sql, parameters = connection.calls[-1]
    assert "UPDLOCK, READPAST, READCOMMITTEDLOCK" in sql
    assert "SYSUTCDATETIME()" in sql and "OUTPUT inserted.*" in sql
    assert "fencing_token=fencing_token+1" in sql
    assert owner not in sql and parameters == (digest("detector"), owner, 30)
    assert "BEGIN TRANSACTION" in connection.calls[0][0]
    assert connection.committed and connection.closed


def test_sql_server_scoped_claim_filters_inside_locked_atomic_candidate():
    connection = RecordingConnection()
    store = SqlServerWorkflowStore(lambda: connection)
    scopes = ((SCOPE.key, "Run-A"), (SCOPE.key, "run-a "))
    assert store.claim("review", "worker", scopes=scopes) is None
    sql, parameters = connection.calls[-1]
    assert "WHERE kind_key=? AND scope_key IN (?,?) AND" in sql
    assert sql.index("scope_key IN") < sql.index("UPDATE candidate")
    assert "UPDLOCK, READPAST, READCOMMITTEDLOCK" in sql
    assert parameters == (digest("review"),
                          *sorted(digest(list(pair)) for pair in scopes), "worker", 120)
    assert all(run not in sql for _, run in scopes)


def test_sql_server_guard_rechecks_expiry_in_mutating_statement():
    connection = RecordingConnection()
    store = SqlServerWorkflowStore(lambda: connection)
    from agentic_itops.consumer.storage_contract import WorkLease
    lease = WorkLease("a" * 64, "detector", "worker", 9, utc("2026-09-15T12:01:00Z"), {})
    with pytest.raises(LostLease):
        store._guarded_update(connection, lease, "status='failed'", ())
    sql, parameters = connection.calls[-1]
    assert "expires_at>SYSUTCDATETIME()" in sql
    assert "owner=? AND fencing_token=?" in sql
    assert "OUTPUT inserted.job_id" in sql
    assert parameters == (lease.job_id, lease.owner, 9)


def test_sql_server_renew_statement_uses_server_utc_and_preserves_owner_fence(monkeypatch):
    from agentic_itops.consumer.storage_contract import WorkLease
    connection = RecordingConnection()
    store = SqlServerWorkflowStore(lambda: connection)
    lease = WorkLease("a" * 64, "detector", "worker", 9, utc("2026-09-15T12:01:00Z"), {})
    returned_row = {
        "job_id": lease.job_id, "kind": lease.kind, "owner": lease.owner,
        "fencing_token": lease.fencing_token, "expires_at": utc("2026-09-15T12:02:00Z"),
        "payload_json": "{}",
    }
    # Only SQL statement generation is exercised here, not SQL Server execution.
    monkeypatch.setattr(store, "_verify_lease", lambda cursor, actual: returned_row)
    monkeypatch.setattr(store, "_one", lambda cursor, sql, parameters: returned_row)
    monkeypatch.setattr(connection, "fetchone", lambda: (lease.job_id,))
    renewed = store.renew(lease)
    sql, parameters = next(call for call in connection.calls if call[0].startswith("UPDATE"))
    assert "SET expires_at=DATEADD(second, ?, SYSUTCDATETIME()) OUTPUT inserted.job_id" in sql
    assert "status='leased' AND owner=? AND fencing_token=? AND expires_at>SYSUTCDATETIME()" in sql
    assert parameters == (120, lease.job_id, lease.owner, lease.fencing_token)
    assert renewed.owner == lease.owner and renewed.fencing_token == lease.fencing_token
    assert renewed.expires_at == returned_row["expires_at"]
    assert connection.committed and connection.closed


def test_sql_server_explicit_migration_is_nondestructive_and_has_immutable_ledgers():
    connection = RecordingConnection()
    SqlServerWorkflowStore(lambda: connection).bootstrap_schema()
    statements = "\n".join(sql for sql, _ in connection.calls)
    assert "DROP TABLE" not in statements and "TRUNCATE" not in statements
    assert "CREATE TABLE dbo.consumer_batches" in statements
    assert "UQ_consumer_event UNIQUE (producer_key, event_id)" in statements
    assert "UNIQUE (producer_key, window_key, artifact_sha256)" in statements
    assert statements.count("AFTER UPDATE, DELETE") == 4
    assert "Latin1_General_100_BIN2" in statements
    assert connection.committed and connection.closed


def test_sql_server_autocommit_connection_is_rejected():
    connection = RecordingConnection()
    connection.autocommit = True
    with pytest.raises(ContractError, match="autocommit"):
        SqlServerWorkflowStore(lambda: connection).claim("detector", "worker")
    assert connection.rolled_back and connection.closed and not connection.committed
