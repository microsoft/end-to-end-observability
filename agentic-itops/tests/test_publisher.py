"""SQLite/local doubles only: NOT SQL Server, Azure Search, or identity proof."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from threading import Event
from uuid import uuid4

import pytest

from agentic_itops.review.contracts import OVERLAY, ReviewError, canonical, digest
from agentic_itops.review.publisher import (
    EXCLUDE, PUBLISH, IndexingResult, LostPublisherLease, PublisherDispatcher,
    PublisherLease, SearchDeliveryReceipt, document_for,
)
from agentic_itops.review.repository import SQLiteReviewRepository, SqlServerReviewRepository, _SQLITE_SCHEMA
from tests.test_review import AUTHOR, OID, approval_body, result


OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def repo():
    repository = SQLiteReviewRepository()
    yield repository
    repository.close()


def approve(repository, incident_id="incident-a", revision=1, value=None):
    value = value or result(incident_id, revision)
    saved = repository.register_result(value)
    reviewed = repository.review(
        OID, incident_id, saved["etag"], f"review-{incident_id}-{revision}",
        {"action": "accept", "reason": "Independent synthetic review"},
    )
    body = {**approval_body(), "incident_id": incident_id, "analysis_revision": revision,
            "draft_sha256": value["postmortem_draft"]["sha256"]}
    return repository.approve_publication(
        OID, reviewed["etag"], f"approve-{incident_id}-{revision}", body,
    )


def revoke(repository, publication):
    current = repository.get_publication(publication["publication_id"])
    return repository.revoke_publication(
        OID, publication["publication_id"], current["etag"], str(uuid4()), {"reason": "Evidence corrected"},
    )


def expire_lease(repository, lease):
    with repository.transaction() as cursor:
        cursor.execute("UPDATE review_publisher_jobs SET expires_at=? WHERE outbox_id=?",
                       ("2000-01-01T00:00:00+00:00", lease.outbox_id))


def status(repository, outbox_id):
    with repository.transaction() as cursor:
        return cursor.execute("SELECT status FROM review_outbox WHERE outbox_id=?", (outbox_id,)).fetchone()[0]


def success(request):
    return SearchDeliveryReceipt(
        request.operation_id, request.operation, "local-test-request",
        (IndexingResult(request.document_id, True, 201 if request.operation == "upload" else 200),),
    )


class LocalSearch:
    """In-memory test double, deliberately not an Azure SDK receipt."""
    def __init__(self):
        self.documents = {}
        self.operations = []

    def upload(self, document, *, operation_id):
        self.operations.append((operation_id, "upload", document.document_id))
        self.documents[document.document_id] = document
        return SearchDeliveryReceipt(
            operation_id, "upload", "local-upload", (IndexingResult(document.document_id, True, 201),),
        )

    def delete(self, document_id, *, operation_id):
        self.operations.append((operation_id, "delete", document_id))
        self.documents.pop(document_id, None)
        return SearchDeliveryReceipt(
            operation_id, "delete", "local-delete", (IndexingResult(document_id, True, 200),),
        )


def dispatch(repository, search=None, owner=OWNER):
    return PublisherDispatcher(repository, search or LocalSearch(), owner=owner)


def assert_denied(repository, publication):
    document = document_for(publication)
    with pytest.raises(ReviewError):
        repository.publication_for_retrieval(
            publication["publication_id"], document.document_id, document.content_hash,
        )


def test_publish_accepts_only_exact_ledger_content_and_preserves_markers(repo):
    publication = approve(repo)
    original_receipt = canonical(publication)
    assert_denied(repo, publication)
    search = LocalSearch()
    receipt = dispatch(repo, search).run_once()
    assert receipt["status"] == "accepted"
    assert status(repo, receipt["outbox_id"]) == "processed"
    document = document_for(publication)
    assert search.documents == {document.document_id: document}
    actual = repo.publication_for_retrieval(
        publication["publication_id"], document.document_id, document.content_hash,
    )
    assert actual["draft"] == publication["draft"]
    assert all(actual[key] == value for key, value in OVERLAY.items())
    assert repo.get_publication(publication["publication_id"])["searchable"] is True
    assert canonical(publication) == original_receipt
    assert dispatch(repo, search).run_once() is None
    for key, content_hash in [("wrong", document.content_hash), (document.document_id, "a" * 64)]:
        with pytest.raises(ReviewError):
            repo.publication_for_retrieval(publication["publication_id"], key, content_hash)


def test_expired_lease_cannot_ack_renew_or_fail_and_reclaim_fences(repo):
    approve(repo)
    first = repo.claim_publisher(OWNER, 1)
    request = repo.prepare_publisher(first)
    expire_lease(repo, first)
    for operation in [
        lambda: repo.ack_publisher(first, success(request)),
        lambda: repo.renew_publisher(first),
        lambda: repo.fail_publisher(first, "timeout", "No response"),
    ]:
        with pytest.raises(LostPublisherLease):
            operation()
    second = repo.claim_publisher(OTHER)
    assert second.outbox_id == first.outbox_id
    assert second.fencing_token == first.fencing_token + 1
    assert repo.publisher_attempts(first.outbox_id)[0]["status"] == "uncertain"
    with pytest.raises(LostPublisherLease):
        repo.ack_publisher(first, success(request))
    retried = repo.prepare_publisher(second)
    assert retried == request
    assert repo.ack_publisher(second, success(retried))["status"] == "accepted"


def test_renew_uses_live_owner_and_exact_token(repo):
    approve(repo)
    lease = repo.claim_publisher(OWNER, 1)
    assert repo.renew_publisher(lease, 120) == lease
    with repo.transaction() as cursor:
        until = cursor.execute("SELECT expires_at FROM review_publisher_jobs WHERE outbox_id=?",
                               (lease.outbox_id,)).fetchone()[0]
    assert datetime.fromisoformat(until) > datetime.now(timezone.utc) + timedelta(seconds=100)
    for wrong in [replace(lease, owner=OTHER), replace(lease, fencing_token=lease.fencing_token + 1)]:
        with pytest.raises(LostPublisherLease):
            repo.prepare_publisher(wrong)


@pytest.mark.parametrize("owner", ["", "worker", OWNER.upper(), "{" + OWNER + "}", " " + OWNER,
                                  "00000000-0000-0000-0000-000000000000", None])
def test_canonical_owner_validation(repo, owner):
    with pytest.raises(ReviewError):
        repo.claim_publisher(owner)
    with pytest.raises(ReviewError):
        PublisherDispatcher(repo, LocalSearch(), owner=owner)


@pytest.mark.parametrize("seconds", [0, -1, 3601, True, 1.5, "120", None])
def test_bounded_lease_seconds(repo, seconds):
    with pytest.raises(ReviewError):
        repo.claim_publisher(OWNER, seconds)


@pytest.mark.parametrize("token", [True, 0, -1, 1.5, "1", 2**63])
def test_strict_fencing_token(repo, token):
    approve(repo)
    lease = repo.claim_publisher(OWNER)
    with pytest.raises(ReviewError):
        repo.renew_publisher(replace(lease, fencing_token=token))


def test_durable_restart_keeps_claim_attempt_and_immutable_retry(tmp_path):
    path = tmp_path / "publisher.sqlite"
    first = SQLiteReviewRepository(path)
    publication = approve(first)
    lease = first.claim_publisher(OWNER)
    request = first.prepare_publisher(lease)
    first.close()
    restarted = SQLiteReviewRepository(path)
    try:
        assert restarted.claim_publisher(OTHER) is None
        assert restarted.publisher_attempts(lease.outbox_id)[0]["status"] == "inflight"
        expire_lease(restarted, lease)
        reclaimed = restarted.claim_publisher(OTHER)
        assert restarted.prepare_publisher(reclaimed) == request
        assert restarted.ack_publisher(reclaimed, success(request))["status"] == "accepted"
        assert restarted.get_publication(publication["publication_id"])["searchable"]
    finally:
        restarted.close()


def test_ambiguous_external_completion_is_failed_visible_and_retry_is_idempotent(repo):
    publication = approve(repo)

    class AmbiguousSearch(LocalSearch):
        def upload(self, document, *, operation_id):
            receipt = super().upload(document, operation_id=operation_id)
            if len(self.operations) == 1:
                raise TimeoutError("local response lost after upload")
            return receipt

    search = AmbiguousSearch()
    worker = dispatch(repo, search)
    with pytest.raises(TimeoutError):
        worker.run_once()
    with repo.transaction() as cursor:
        outbox_id = cursor.execute("SELECT outbox_id FROM review_outbox WHERE kind=?", (PUBLISH,)).fetchone()[0]
    assert status(repo, outbox_id) == "failed"
    assert repo.publisher_attempts(outbox_id)[0]["status"] == "uncertain"
    assert_denied(repo, publication)
    assert worker.run_once()["status"] == "accepted"
    assert search.operations[0] == search.operations[1]
    assert len(search.documents) == 1
    # A retry is not proof the timed-out request can no longer finish later.
    revoke(repo, publication)
    assert worker.run_once()["status"] == "removal-pending"
    assert repo.get_publication(publication["publication_id"])["removal_state"] == "pending"
    assert any(row["kind"] == EXCLUDE for row in repo.pending_outbox())


@pytest.mark.parametrize("mode", ["revoked", "expired", "corrected", "evidence", "review"])
def test_preflight_rejects_ineligible_publication_without_upload(repo, mode):
    publication = approve(repo)
    lease = repo.claim_publisher(OWNER)
    if mode == "revoked":
        revoke(repo, publication)
    elif mode == "expired":
        with repo.transaction() as cursor:
            cursor.execute("UPDATE review_publications SET eligible_until='2000-01-01T00:00:00Z'")
    elif mode == "corrected":
        repo.register_result(result(revision=2))
    elif mode == "evidence":
        repo.invalidate_evidence("event-a", "Supporting evidence corrected")
    else:
        revision = repo.get_revision("incident-a", 1)
        repo.review(OID, "incident-a", revision["etag"], "withdraw",
                    {"action": "request_evidence", "reason": "Not supported"})
    assert repo.prepare_publisher(lease) is None
    assert status(repo, lease.outbox_id) == "cancelled"
    assert repo.publisher_attempts(lease.outbox_id)[0]["status"] == "rejected"
    assert_denied(repo, publication)
    assert any(row["kind"] == EXCLUDE for row in repo.pending_outbox())


@pytest.mark.parametrize("mode", ["revoked", "expired", "corrected", "evidence"])
def test_accepted_completion_rechecks_eligibility(repo, mode):
    publication = approve(repo)
    lease = repo.claim_publisher(OWNER)
    request = repo.prepare_publisher(lease)
    if mode == "revoked":
        revoke(repo, publication)
    elif mode == "expired":
        with repo.transaction() as cursor:
            cursor.execute("UPDATE review_publications SET eligible_until='2000-01-01T00:00:00Z'")
    elif mode == "corrected":
        repo.register_result(result(revision=2))
    else:
        repo.invalidate_evidence("event-a", "Erasure")
    assert repo.ack_publisher(lease, success(request))["status"] == "rejected"
    assert_denied(repo, publication)
    assert any(row["kind"] == EXCLUDE for row in repo.pending_outbox())


@pytest.mark.parametrize("malformation", [
    "dict", "none", "wrong-operation", "wrong-id", "empty", "mixed", "duplicate",
    "wrong-key", "false", "string-bool", "bool-status", "accepted-202", "missing-404",
    "error-on-success", "oversized-error", "missing-request-id", "list-results",
])
def test_malformed_or_partial_search_receipts_never_ack(repo, malformation):
    publication = approve(repo)

    class MalformedSearch(LocalSearch):
        def upload(self, document, *, operation_id):
            receipt = super().upload(document, operation_id=operation_id)
            item = receipt.results[0]
            mutations = {
                "dict": {"succeeded": True}, "none": None,
                "wrong-operation": replace(receipt, operation="delete"),
                "wrong-id": replace(receipt, operation_id="other"),
                "empty": replace(receipt, results=()),
                "mixed": replace(receipt, results=(item, IndexingResult("other", False, 500))),
                "duplicate": replace(receipt, results=(item, item)),
                "wrong-key": replace(receipt, results=(replace(item, document_id="other"),)),
                "false": replace(receipt, results=(replace(item, succeeded=False, status_code=503),)),
                "string-bool": replace(receipt, results=(replace(item, succeeded="true"),)),
                "bool-status": replace(receipt, results=(replace(item, status_code=True),)),
                "accepted-202": replace(receipt, results=(replace(item, status_code=202),)),
                "missing-404": replace(receipt, results=(replace(item, status_code=404),)),
                "error-on-success": replace(receipt, results=(replace(item, error_message="failed"),)),
                "oversized-error": replace(receipt, results=(replace(item, error_message="x" * 2001),)),
                "missing-request-id": replace(receipt, request_id=""),
                "list-results": replace(receipt, results=[item]),
            }
            return mutations[malformation]

    with pytest.raises(ReviewError):
        dispatch(repo, MalformedSearch()).run_once()
    assert_denied(repo, publication)
    with repo.transaction() as cursor:
        outbox_id = cursor.execute("SELECT outbox_id FROM review_outbox WHERE kind=?", (PUBLISH,)).fetchone()[0]
    assert status(repo, outbox_id) == "failed"
    assert repo.publisher_attempts(outbox_id)[0]["status"] == "uncertain"


def test_exclusion_success_is_validated_and_idempotent(repo):
    publication = approve(repo)
    search = LocalSearch()
    worker = dispatch(repo, search)
    assert worker.run_once()["status"] == "accepted"
    revoke(repo, publication)
    receipt = worker.run_once()
    assert receipt["status"] == "removed"
    assert not search.documents
    assert repo.get_publication(publication["publication_id"])["removal_state"] == "confirmed"
    assert_denied(repo, publication)
    assert repo.reconcile_publisher() == []
    assert worker.run_once() is None


def test_failed_delete_never_confirms_removal(repo):
    publication = approve(repo)
    revoke(repo, publication)

    class FailedDelete(LocalSearch):
        def delete(self, document_id, *, operation_id):
            return SearchDeliveryReceipt(
                operation_id, "delete", "local-failure", (IndexingResult(document_id, False, 503),),
            )

    with pytest.raises(ReviewError):
        dispatch(repo, FailedDelete()).run_once()
    assert repo.get_publication(publication["publication_id"])["removal_state"] == "pending"
    assert dispatch(repo).run_once()["status"] == "removed"


def test_concurrent_revoke_during_upload_has_no_sql_transaction_across_io(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    writer, reviewer = SQLiteReviewRepository(path), SQLiteReviewRepository(path)
    started, release = Event(), Event()

    class BlockingSearch(LocalSearch):
        def upload(self, document, *, operation_id):
            started.set()
            assert release.wait(10), "test upload was not released"
            return super().upload(document, operation_id=operation_id)

    search = BlockingSearch()
    publication = approve(writer)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            uploading = pool.submit(dispatch(writer, search).run_once)
            try:
                assert started.wait(5)
                # A separate connection can commit a revoke while Search blocks.
                revoking = pool.submit(revoke, reviewer, publication)
                assert revoking.result(timeout=5)["status"] == "revoked"
                assert reviewer.claim_publisher(OTHER) is None
            finally:
                release.set()
            assert uploading.result(timeout=5)["status"] == "rejected"
        assert_denied(reviewer, publication)
        assert dispatch(reviewer, search, OTHER).run_once()["status"] == "removed"
        assert not search.documents
    finally:
        writer.close()
        reviewer.close()


def test_late_upload_after_delete_cannot_resurrect_accepted_knowledge(repo):
    publication = approve(repo)
    lease = repo.claim_publisher(OWNER)
    request = repo.prepare_publisher(lease)
    revoke(repo, publication)
    expire_lease(repo, lease)
    search = LocalSearch()
    deletion = dispatch(repo, search, OTHER).run_once()
    assert deletion["status"] == "removal-pending"
    # The abandoned external upload physically finishes AFTER the delete.
    late = search.upload(request.document, operation_id=request.operation_id)
    assert search.documents
    with pytest.raises(LostPublisherLease):
        repo.ack_publisher(lease, late)
    assert_denied(repo, publication)
    observed = repo.observe_late_publisher(lease, late)
    assert observed["status"] == "late"
    assert status(repo, lease.outbox_id) != "processed"
    assert repo.observe_late_publisher(lease, late) == observed
    with pytest.raises(ReviewError, match="changed"):
        repo.observe_late_publisher(lease, replace(late, request_id="different"))
    assert dispatch(repo, search, OTHER).run_once()["status"] == "removed"
    assert not search.documents
    assert repo.get_publication(publication["publication_id"])["removal_state"] == "confirmed"


def test_changed_generation_during_delete_requires_later_delete(repo):
    publication = approve(repo)
    lease = repo.claim_publisher(OWNER)
    request = repo.prepare_publisher(lease)
    revoke(repo, publication)
    expire_lease(repo, lease)
    deletion = repo.claim_publisher(OTHER)
    delete_request = repo.prepare_publisher(deletion)
    repo.observe_late_publisher(lease, success(request))
    assert repo.ack_publisher(deletion, success(delete_request))["status"] == "removal-pending"
    assert dispatch(repo).run_once()["status"] == "removed"


def test_unknown_and_consumer_owned_work_are_left_pending(repo):
    publication = approve(repo)
    repo.invalidate_evidence("event-a", "Correction")
    with repo.transaction() as cursor:
        unknown = repo._outbox(cursor, "unknown-kind", {"publication_id": publication["publication_id"]})
        wrong_case = repo._outbox(cursor, "Publish-approved-revision", publication)
    worker = dispatch(repo)
    assert worker.run_once()["status"] == "removed"
    assert worker.run_once() is None
    pending = repo.pending_outbox()
    assert {row["kind"] for row in pending} == {
        "invalidate-workflows", "unknown-kind", "Publish-approved-revision",
    }
    assert status(repo, unknown) == status(repo, wrong_case) == "pending"


def test_unapproved_or_model_authored_fields_do_not_supply_authority(repo):
    value = result()
    value["postmortem_draft"]["authorship"] = {"kind": "human", "oid": AUTHOR}
    saved = repo.register_result(value)
    assert repo.claim_publisher(OWNER) is None
    with pytest.raises(ReviewError):
        repo.approve_publication(OID, saved["etag"], "unreviewed", approval_body())
    accepted = repo.review(AUTHOR, "incident-a", saved["etag"], "review",
                           {"action": "accept", "reason": "Reviewed"})
    with pytest.raises(ReviewError, match="authors cannot"):
        repo.approve_publication(AUTHOR, accepted["etag"], "self-approve", approval_body())
    assert repo.claim_publisher(OWNER) is None
    publication = repo.approve_publication(OID, accepted["etag"], "independent", approval_body())
    assert dispatch(repo).run_once()["status"] == "accepted"
    assert publication["approved_by_oid"] == OID


def test_document_byte_limit_failure_is_durable_before_io(repo):
    value = result()
    # Valid 2MB review payload, but too large for the bounded publisher projection.
    value["source"]["surfaces"] = ["surface-" + str(n) for n in range(32)]
    value["source_hash"] = digest(value["source"])
    value["evidence_refs"] = [
        {"evidence_id": f"event-{n}", "sha256": "a" * 64,
         "retained_until_utc": "2098-01-01T00:00:00Z", "verified": True}
        for n in range(7000)
    ]
    assert len(canonical(value["evidence_refs"]).encode("utf-8")) > 1_000_000
    assert len(canonical(value).encode("utf-8")) < 2_000_000
    publication = approve(repo, value=value)
    search = LocalSearch()
    with pytest.raises(ReviewError, match="byte limit"):
        dispatch(repo, search).run_once()
    assert not search.operations
    with repo.transaction() as cursor:
        outbox_id = cursor.execute("SELECT outbox_id FROM review_outbox WHERE kind=?", (PUBLISH,)).fetchone()[0]
    assert repo.publisher_attempts(outbox_id)[0]["status"] == "failed"
    revoke(repo, publication)
    assert dispatch(repo, search).run_once()["status"] == "removed"


def test_sqlite_additive_bootstrap_backfills_existing_outbox(tmp_path):
    path = tmp_path / "old-review.sqlite"
    # Construct only the old schema and preserve an already-issued immutable publication.
    with sqlite3.connect(path) as connection:
        for statement in _SQLITE_SCHEMA:
            if "publisher_" not in statement:
                connection.execute(statement)
    upgraded = SQLiteReviewRepository(path)
    publication = approve(upgraded)
    upgraded.close()
    with sqlite3.connect(path) as connection:
        # Simulate old publisher-less ledger without touching original data.
        connection.execute("DROP TABLE review_publisher_attempts")
        connection.execute("DROP TABLE review_publisher_jobs")
        connection.execute("DROP TABLE review_publisher_state")
        before = connection.execute("SELECT * FROM review_outbox").fetchall()
    reopened = SQLiteReviewRepository(path)
    try:
        with reopened.transaction() as cursor:
            assert cursor.execute("SELECT * FROM review_outbox").fetchall() == before
        assert dispatch(reopened).run_once()["status"] == "accepted"
        assert reopened.get_publication(publication["publication_id"])["searchable"]
    finally:
        reopened.close()


def test_reconciliation_is_bounded_fair_and_expiry_is_immediate(repo):
    publications = [approve(repo, f"incident-{n}") for n in range(3)]
    for publication in publications:
        with repo.transaction() as cursor:
            cursor.execute("UPDATE review_publications SET eligible_until='2000-01-01T00:00:00Z' "
                           "WHERE publication_id=?", (publication["publication_id"],))
        assert_denied(repo, publication)
    queued = [repo.reconcile_publisher(limit=1) for _ in range(3)]
    assert all(len(batch) == 1 for batch in queued)
    assert len({batch[0] for batch in queued}) == 3
    for invalid in [0, True, 1001, "1"]:
        with pytest.raises(ReviewError):
            repo.reconcile_publisher(invalid)


def test_two_sqlite_repositories_cannot_claim_same_publication(tmp_path):
    path = tmp_path / "claims.sqlite"
    first, second = SQLiteReviewRepository(path), SQLiteReviewRepository(path)
    try:
        approve(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first.claim_publisher, OWNER)
            b = pool.submit(second.claim_publisher, OTHER)
            assert sum(lease is not None for lease in (a.result(), b.result())) == 1
    finally:
        first.close()
        second.close()


def test_static_sql_migration_and_server_clock_guard_not_live_sql_proof():
    sql = (Path("agentic_itops") / "review" / "migrations" / "002_publisher.sql").read_text()
    assert "BEGIN TRANSACTION" in sql and "COMMIT TRANSACTION" in sql
    assert "COLLATE Latin1_General_100_BIN2" in sql
    assert "datetime2(7)" in sql and "JSON_VALUE" in sql
    assert "CREATE TABLE review.publisher_attempts" in sql
    assert "GRANT " not in sql and "CREATE ROLE" not in sql and "DROP TABLE" not in sql

    class Cursor:
        def __init__(self):
            self.calls = []
        def execute(self, sql, parameters=()):
            self.calls.append((sql, parameters))
            return self
        def fetchone(self):
            return ("matched",)

    cursor = Cursor()
    repository = SqlServerReviewRepository(lambda: None)
    lease = PublisherLease(str(uuid4()), OWNER, 1)
    repository._guard_publisher(cursor, lease, seconds=120)
    statement, parameters = cursor.calls[0]
    assert "DATEADD(second,?,SYSUTCDATETIME())" in statement
    assert "expires_at>SYSUTCDATETIME()" in statement
    assert "OUTPUT inserted.outbox_id" in statement
    assert parameters == (120, lease.outbox_id, OWNER, 1)


def test_final_clock_fence_rolls_back_ack_even_after_eligibility_check(repo, monkeypatch):
    publication = approve(repo)
    clock = [datetime.now(timezone.utc)]
    monkeypatch.setattr(repo, "_now", lambda _: clock[0])
    lease = repo.claim_publisher(OWNER, 1)
    request = repo.prepare_publisher(lease)
    original = repo._publishable

    def advance_after_check(cursor, publication_id):
        checked = original(cursor, publication_id)
        clock[0] += timedelta(seconds=2)
        return checked

    monkeypatch.setattr(repo, "_publishable", advance_after_check)
    with pytest.raises(LostPublisherLease):
        repo.ack_publisher(lease, success(request))
    assert status(repo, lease.outbox_id) == "leased"
    assert repo.publisher_attempts(lease.outbox_id)[0]["status"] == "inflight"
    assert_denied(repo, publication)


def test_dispatcher_records_late_success_without_acknowledgement(repo, monkeypatch):
    publication = approve(repo)
    clock = [datetime.now(timezone.utc)]
    monkeypatch.setattr(repo, "_now", lambda _: clock[0])

    class SlowSearch(LocalSearch):
        def upload(self, document, *, operation_id):
            receipt = super().upload(document, operation_id=operation_id)
            clock[0] += timedelta(seconds=121)
            return receipt

    search = SlowSearch()
    with pytest.raises(LostPublisherLease):
        dispatch(repo, search).run_once()
    with repo.transaction() as cursor:
        outbox_id = cursor.execute("SELECT outbox_id FROM review_outbox WHERE kind=?", (PUBLISH,)).fetchone()[0]
    assert status(repo, outbox_id) == "leased"
    assert repo.publisher_attempts(outbox_id)[0]["status"] == "late"
    assert search.documents
    assert_denied(repo, publication)
    assert dispatch(repo).run_once()["status"] == "accepted"


def test_ack_requires_prepare_and_repeated_prepare_is_not_a_second_external_intent(repo):
    approve(repo)
    lease = repo.claim_publisher(OWNER)
    with pytest.raises(ReviewError, match="prepared"):
        repo.ack_publisher(lease, None)
    repo.prepare_publisher(lease)
    with pytest.raises(ReviewError, match="already prepared"):
        repo.prepare_publisher(lease)
    repo.renew_publisher(lease)


def test_unknown_upload_reconciliation_does_not_hot_loop_or_starve_other_publications(repo, monkeypatch):
    clock = [datetime.now(timezone.utc)]
    monkeypatch.setattr(repo, "_now", lambda _: clock[0])
    first = approve(repo)
    lease = repo.claim_publisher(OWNER)
    repo.prepare_publisher(lease)
    repo.fail_publisher(lease, "timeout", "Outcome unknown")
    revoke(repo, first)
    second = approve(repo, "incident-b")
    worker = dispatch(repo)
    assert worker.run_once()["status"] == "removal-pending"
    assert worker.run_once()["status"] == "accepted"
    assert repo.get_publication(second["publication_id"])["searchable"]
    # The failed ineligible publish is cancelled; it can add one immediate
    # exclusion, but subsequent unresolved-removal retries have a durable delay.
    assert worker.run_once()["status"] == "cancelled"
    assert worker.run_once() is None
    clock[0] += timedelta(seconds=61)
    assert worker.run_once()["status"] == "removal-pending"
    assert worker.run_once() is None
    assert repo.get_publication(first["publication_id"])["removal_state"] == "pending"


def test_accepted_delivery_survives_restart_without_reupload(tmp_path):
    path = tmp_path / "accepted.sqlite"
    repository = SQLiteReviewRepository(path)
    publication = approve(repository)
    receipt = dispatch(repository).run_once()
    repository.close()
    restarted = SQLiteReviewRepository(path)
    try:
        assert restarted.publisher_attempts(receipt["outbox_id"]) == [receipt]
        search = LocalSearch()
        assert dispatch(restarted, search).run_once() is None
        assert not search.operations
        assert restarted.get_publication(publication["publication_id"])["searchable"]
    finally:
        restarted.close()


def test_sqlite_backfill_rejects_orphan_publisher_work_instead_of_silently_skipping(tmp_path):
    path = tmp_path / "orphan.sqlite"
    with sqlite3.connect(path) as connection:
        for statement in _SQLITE_SCHEMA:
            if "publisher_" not in statement:
                connection.execute(statement)
        connection.execute(
            "INSERT INTO review_outbox VALUES (?,?,?,'pending')",
            (str(uuid4()), PUBLISH, json.dumps({"publication_id": str(uuid4()), **OVERLAY})),
        )
    with pytest.raises(ReviewError, match="unknown publication"):
        SQLiteReviewRepository(path)


def test_unexpected_adapter_error_is_recorded_without_secret_payload_and_reraised(repo):
    approve(repo)

    class BuggySearch(LocalSearch):
        def upload(self, document, *, operation_id):
            raise RuntimeError("private provider configuration must not enter SQL receipt")

    with pytest.raises(RuntimeError, match="private provider"):
        dispatch(repo, BuggySearch()).run_once()
    with repo.transaction() as cursor:
        encoded = cursor.execute("SELECT receipt_json FROM review_publisher_attempts").fetchone()[0]
    assert "private provider" not in encoded
    assert json.loads(encoded)["status"] == "uncertain"


def test_invalid_failure_inputs_leave_the_live_lease_unchanged(repo):
    approve(repo)
    lease = repo.claim_publisher(OWNER)
    for code, message in [("", "failed"), ("failure", ""), ("failure", "x" * 2001),
                          ("failure", " " * 3000 + "x"), (None, "failed")]:
        with pytest.raises(ReviewError):
            repo.fail_publisher(lease, code, message)
    assert status(repo, lease.outbox_id) == "leased"
    assert repo.publisher_attempts(lease.outbox_id)[0]["status"] == "claimed"


def test_late_outcome_after_reclaim_cannot_replace_newer_accepted_receipt(repo):
    publication = approve(repo)
    old = repo.claim_publisher(OWNER)
    request = repo.prepare_publisher(old)
    expire_lease(repo, old)
    new = repo.claim_publisher(OTHER)
    repo.prepare_publisher(new)
    accepted = repo.ack_publisher(new, success(request))
    with pytest.raises(ReviewError, match="does not match"):
        repo.observe_late_publisher(replace(old, owner=OTHER), success(request))
    assert repo.observe_late_publisher(old, success(request))["status"] == "late"
    assert repo.publisher_attempts(new.outbox_id, after_token=old.fencing_token) == [accepted]
    assert repo.get_publication(publication["publication_id"])["searchable"]
    assert status(repo, new.outbox_id) == "processed"
    for cursor, limit in [(-1, 1), (True, 1), (0, 0), (0, 1001)]:
        with pytest.raises(ReviewError):
            repo.publisher_attempts(old.outbox_id, after_token=cursor, limit=limit)


def test_late_observation_cannot_bypass_an_active_acknowledgement(repo):
    approve(repo)
    lease = repo.claim_publisher(OWNER)
    request = repo.prepare_publisher(lease)
    with pytest.raises(ReviewError, match="active publisher attempt"):
        repo.observe_late_publisher(lease, success(request))
    assert repo.publisher_attempts(lease.outbox_id)[0]["status"] == "inflight"
    assert repo.ack_publisher(lease, success(request))["status"] == "accepted"


def test_previously_accepted_document_expires_without_waiting_for_maintenance(repo, monkeypatch):
    publication = approve(repo)
    assert dispatch(repo).run_once()["status"] == "accepted"
    monkeypatch.setattr(repo, "_now", lambda _: datetime(2098, 1, 1, tzinfo=timezone.utc))
    assert repo.get_publication(publication["publication_id"])["searchable"] is False
    assert_denied(repo, publication)
    assert repo.reconcile_publisher()
    assert dispatch(repo).run_once()["status"] == "removed"


def test_outbox_payload_cannot_replace_ledger_authority_or_document_content(repo):
    publication = approve(repo)
    with repo.transaction() as cursor:
        cursor.execute(
            "UPDATE review_outbox SET payload_json=? WHERE kind=?",
            (canonical({"publication_id": str(uuid4()), "execution_allowed": True,
                        "approved_by_oid": OWNER, "draft": "model-supplied replacement"}), PUBLISH),
        )
    search = LocalSearch()
    assert dispatch(repo, search).run_once()["status"] == "accepted"
    document = document_for(publication)
    assert search.documents == {document.document_id: document}


def test_expiry_sweep_still_revokes_after_late_upload_was_already_deleted(repo, monkeypatch):
    publication = approve(repo)
    lease = repo.claim_publisher(OWNER)
    request = repo.prepare_publisher(lease)
    monkeypatch.setattr(repo, "_now", lambda _: datetime(2098, 1, 1, tzinfo=timezone.utc))
    repo.observe_late_publisher(lease, success(request))
    assert dispatch(repo).run_once()["status"] == "removed"
    assert repo.get_publication(publication["publication_id"])["status"] == "approved"
    assert repo.reconcile_publisher()
    assert repo.get_publication(publication["publication_id"])["status"] == "revoked"
