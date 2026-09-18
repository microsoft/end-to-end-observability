"""Transactional review ledger, independent of consumer storage.

SQL Server: run migrations/001_review.sql and 002_publisher.sql using the migration identity,
plus 003_search_projection.sql before binding the real Search adapter/retriever,
then SqlServerReviewRepository(connection_factory). The factory returns a
DB-API connection with autocommit=False (including the parent's mssql-python
managed_identity_sql_factory). No credentials are accepted by
the web API. SQL Server SERIALIZABLE + transaction-owned application lock
serialize review CAS/outbox commits across application instances.

Source producer/run entitlements and immutable source comparisons use exact
Python strings, not SQL collation-dependent JSON comparisons. Opaque RunId
case is significant even when the SQL database default collation is CI.

SQLiteReviewRepository is explicitly LOCAL TEST persistence, not SQL proof.
register_result is an INTERNAL trusted-worker API, never an HTTP endpoint.
The worker must validate evidence/citations/gate receipts before projecting.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import RLock
from typing import Protocol
from uuid import uuid4

from .contracts import (
    OVERLAY, ReviewError, canonical, digest, fields, identifier, identity_oid, now_iso,
    require, result_header, run_identifier, text, utc,
)
from .publisher import (
    EXCLUDE, PUBLISH, PUBLISHER_KINDS, DeliveryRequest, LostPublisherLease,
    PublisherLease, SearchDeliveryReceipt, canonical_uuid, document_for,
    validate_lease_seconds, validate_receipt,
)


class ReviewRepository(Protocol):
    def register_result(self, result: dict) -> dict: ...
    def list_incidents(self, scopes: frozenset[tuple[str, str]]) -> list[dict]: ...
    def current_revision(self, incident_id: str) -> int: ...
    def get_revision(self, incident_id: str, revision: int) -> dict: ...
    def review(self, actor: str, incident_id: str, expected: str, key: str, body: dict) -> dict: ...
    def approve_publication(self, actor: str, expected: str, key: str, body: dict) -> dict: ...
    def get_publication(self, publication_id: str) -> dict: ...
    def revoke_publication(self, actor: str, publication_id: str, expected: str,
                           key: str, body: dict) -> dict: ...
    def submit_work(self, submitter, actor: str, route: str, key: str, payload: dict) -> dict: ...
    def submit_projection(self, submitter, advancer, actor: str, route: str, key: str, payload: dict) -> dict: ...
    def submit_replacement(self, submitter, advancer, actor: str, route: str, key: str, payload: dict) -> dict: ...
    def invalidate_evidence(self, evidence_id: str, reason: str) -> list[tuple[str, int]]: ...
    def expire_publications(self) -> list[str]: ...
    def publication_for_publish(self, publication_id: str) -> dict: ...
    def pending_outbox(self) -> list[dict]: ...
    def claim_publisher(self, owner: str, lease_seconds: int = 120, *, outbox_id: str | None = None) -> PublisherLease | None: ...
    def reserve_publication_advance(self, actor: str, route: str, key: str, publication_id: str) -> dict: ...
    def renew_publisher(self, lease: PublisherLease, lease_seconds: int = 120) -> PublisherLease: ...
    def prepare_publisher(self, lease: PublisherLease) -> DeliveryRequest | None: ...
    def ack_publisher(self, lease: PublisherLease, receipt: SearchDeliveryReceipt) -> dict: ...
    def fail_publisher(self, lease: PublisherLease, code: str, message: str) -> dict: ...
    def observe_late_publisher(self, lease: PublisherLease, receipt: SearchDeliveryReceipt) -> dict: ...
    def reconcile_publisher(self, limit: int = 100) -> list[str]: ...
    def publisher_attempts(self, outbox_id: str, *, after_token: int = 0, limit: int = 100) -> list[dict]: ...
    def publication_for_retrieval(self, publication_id: str, document_id: str, content_hash: str,
                                  *, snapshot_id: str | None = None, producer_key: str | None = None,
                                  run_id: str | None = None, analysis_revision: int | None = None,
                                  projection_hash: str | None = None) -> dict: ...
    def bind_publisher_projection(self, lease: PublisherLease, projection: dict) -> None: ...


class _Repository:
    def __init__(self, factory, dialect):
        self.factory = factory
        self.dialect = dialect
        self.lock = RLock()

    def table(self, name):
        return f"review.{name}" if self.dialect == "sqlserver" else f"review_{name}"

    def _now(self, cursor):
        if self.dialect == "sqlserver":
            return cursor.execute("SELECT SYSUTCDATETIME()").fetchone()[0].replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    def _time(self, value):
        return value.replace(tzinfo=None) if self.dialect == "sqlserver" else value.isoformat()

    def _expiry(self, value):
        return value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) else utc(value)

    @contextmanager
    def transaction(self):
        with self.lock:
            connection = self.factory()
            try:
                cursor = connection.cursor()
                if self.dialect == "sqlite":
                    cursor.execute("BEGIN IMMEDIATE")
                else:
                    require(connection.autocommit is False, "SQL connection requires autocommit=False", 503)
                    cursor.execute("SET NOCOUNT ON; SET XACT_ABORT ON; SET TRANSACTION ISOLATION LEVEL SERIALIZABLE; "
                                   "IF @@TRANCOUNT = 0 BEGIN TRANSACTION;")
                    cursor.execute(
                        "DECLARE @r int; EXEC @r = sys.sp_getapplock "
                        "@Resource=N'agentic_itops_review_v1', @LockMode='Exclusive', "
                        "@LockOwner='Transaction', @LockTimeout=10000; "
                        "IF @r < 0 THROW 51000, 'Review ledger lock unavailable', 1;"
                    )
                yield cursor
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if self.dialect != "sqlite" or not self.in_memory:
                    connection.close()

    def _incident(self, cursor, incident_id):
        row = cursor.execute(
            f"SELECT current_revision, source_json, review_state, cas_version "
            f"FROM {self.table('incidents')} WHERE incident_id=?", (incident_id,),
        ).fetchone()
        require(row is not None, "incident not found", 404)
        return {"revision": row[0], "source": json.loads(row[1]), "review_state": row[2], "cas": row[3]}

    def _revision(self, cursor, incident_id, revision):
        row = cursor.execute(
            f"SELECT result_hash, payload_json, eligible FROM {self.table('revisions')} "
            "WHERE incident_id=? AND revision=?", (incident_id, revision),
        ).fetchone()
        require(row is not None, "revision not found", 404)
        incident = self._incident(cursor, incident_id)
        result = json.loads(row[1])
        current = revision == incident["revision"]
        eligible = bool(row[2]) and current
        now = self._now(cursor)
        for ref in result["evidence_refs"]:
            eligible = eligible and utc(ref["retained_until_utc"]) > now
        for parent in result.get("knowledge_dependencies", []):
            eligible = eligible and self._publication(cursor, parent)["eligible"]
        return {
            "result": result, "result_hash": row[0], "current": current,
            "eligible": eligible,
            "review_state": incident["review_state"] if current else "stale",
            "etag": f'"{revision}:{incident["cas"]}:{row[0]}"',
            **OVERLAY,
        }

    def _cas(self, cursor, incident_id, expected):
        incident = self._incident(cursor, incident_id)
        revision = self._revision(cursor, incident_id, incident["revision"])
        require(expected == revision["etag"], "stale revision or review version", 409)
        return incident, revision

    def _retry(self, cursor, actor, route, key, payload):
        identifier(key, "Idempotency-Key")
        row = cursor.execute(
            f"SELECT payload_hash, receipt_json FROM {self.table('receipts')} "
            "WHERE actor=? AND route=? AND idempotency_key=?", (actor, route, key),
        ).fetchone()
        if row:
            require(row[0] == digest(payload), "idempotency key reused with changed payload", 409)
            return json.loads(row[1])
        return None

    def _receipt(self, cursor, actor, route, key, payload, receipt):
        receipt = {"receipt_id": str(uuid4()), "created_at_utc": now_iso(), "actor_oid": actor,
                   **receipt, **OVERLAY}
        cursor.execute(
            f"INSERT INTO {self.table('receipts')} "
            "(receipt_id, actor, route, idempotency_key, payload_hash, receipt_json) VALUES (?,?,?,?,?,?)",
            (receipt["receipt_id"], actor, route, key, digest(payload), canonical(receipt)),
        )
        return receipt

    def _outbox(self, cursor, kind, payload):
        outbox_id = str(uuid4())
        cursor.execute(
            f"INSERT INTO {self.table('outbox')} (outbox_id, kind, payload_json, status) VALUES (?,?,?,?)",
            (outbox_id, kind, canonical({**payload, **OVERLAY}), "pending"),
        )
        if kind in PUBLISHER_KINDS:
            publication_id = canonical_uuid(payload["publication_id"])
            if cursor.execute(
                f"SELECT publication_id FROM {self.table('publisher_state')} WHERE publication_id=?",
                (publication_id,),
            ).fetchone() is None:
                cursor.execute(
                    f"INSERT INTO {self.table('publisher_state')} (publication_id,generation) VALUES (?,0)",
                    (publication_id,),
                )
            cursor.execute(
                f"INSERT INTO {self.table('publisher_jobs')} (outbox_id,publication_id,fencing_token) "
                "VALUES (?,?,0)", (outbox_id, publication_id),
            )
        return outbox_id

    def _invalidate(self, cursor, incident_id, revision, reason, seen=None):
        """Persist reverse-lineage invalidations; external removal/pause is NOT claimed."""
        seen = set() if seen is None else seen
        node = (incident_id, revision)
        if node in seen:
            return
        seen.add(node)
        cursor.execute(
            f"UPDATE {self.table('revisions')} SET eligible=0 WHERE incident_id=? AND revision=?",
            node,
        )
        cursor.execute(
            f"UPDATE {self.table('incidents')} SET review_state='needs_evidence', cas_version=cas_version+1 "
            "WHERE incident_id=? AND current_revision=?", node,
        )
        self._outbox(cursor, "invalidate-workflows", {
            "incident_id": incident_id, "analysis_revision": revision, "reason": reason,
        })
        rows = cursor.execute(
            f"SELECT publication_id FROM {self.table('publications')} "
            "WHERE incident_id=? AND revision=? AND eligible=1", node,
        ).fetchall()
        for row in rows:
            self._revoke(cursor, row[0], reason, seen)

    def _revoke(self, cursor, publication_id, reason, seen=None):
        cursor.execute(
            f"UPDATE {self.table('publications')} SET eligible=0, status='revoked' WHERE publication_id=?",
            (publication_id,),
        )
        self._outbox(cursor, "exclude-publication", {"publication_id": publication_id, "reason": reason})
        cursor.execute(
            f"UPDATE {self.table('publisher_state')} SET accepted_outbox_id=NULL, generation=generation+1 "
            "WHERE publication_id=?", (publication_id,),
        )
        pending = cursor.execute(
            f"SELECT outbox_id,payload_json FROM {self.table('outbox')} "
            "WHERE kind='publish-approved-revision' AND status='pending'",
        ).fetchall()
        for outbox_id, payload in pending:
            if json.loads(payload)["publication_id"] == publication_id:
                cursor.execute(
                    f"UPDATE {self.table('outbox')} SET status='cancelled' WHERE outbox_id=?", (outbox_id,),
                )
        dependents = cursor.execute(
            f"SELECT incident_id, revision FROM {self.table('dependencies')} WHERE publication_id=?",
            (publication_id,),
        ).fetchall()
        for incident_id, revision in dependents:
            self._invalidate(cursor, incident_id, revision, reason, seen)

    def register_result(self, result):
        result = result_header(result)
        incident_id, revision = result["incident_id"], result["analysis_revision"]
        encoded, result_hash = canonical(result), digest(result)
        with self.transaction() as cursor:
            existing = cursor.execute(
                f"SELECT result_hash FROM {self.table('revisions')} WHERE incident_id=? AND revision=?",
                (incident_id, revision),
            ).fetchone()
            if existing:
                require(existing[0] == result_hash, "immutable revision conflict", 409)
                return self._revision(cursor, incident_id, revision)
            result_id = cursor.execute(
                f"SELECT result_id FROM {self.table('revisions')} WHERE result_id=?", (result["result_id"],),
            ).fetchone()
            require(result_id is None, "result ID is already bound", 409)
            prior_results = cursor.execute(
                f"SELECT payload_json FROM {self.table('revisions')}",
            ).fetchall()
            incoming = {ref["evidence_id"]: ref["sha256"] for ref in result["evidence_refs"]}
            for prior in prior_results:
                prior = json.loads(prior[0])
                if (prior["source"]["producer_key"], prior["source"]["run_id"]) != (
                        result["source"]["producer_key"], result["source"]["run_id"]):
                    continue
                for ref in prior["evidence_refs"]:
                    require(ref["evidence_id"] not in incoming or incoming[ref["evidence_id"]] == ref["sha256"],
                            "immutable evidence hash conflict; use a new evidence identity", 409)
            row = cursor.execute(
                f"SELECT current_revision FROM {self.table('incidents')} WHERE incident_id=?", (incident_id,),
            ).fetchone()
            if row:
                incident = self._incident(cursor, incident_id)
                require(canonical(incident["source"]) == canonical(result["source"]),
                        "incident source is immutable", 409)
                require(revision == incident["revision"] + 1, "revision must advance by one", 409)
                self._invalidate(cursor, incident_id, incident["revision"], "superseding analysis")
                state = "needs_evidence" if incident["review_state"] == "closed" else "pending"
                cursor.execute(
                    f"UPDATE {self.table('incidents')} SET current_revision=?, review_state=?, "
                    "cas_version=cas_version+1 WHERE incident_id=?", (revision, state, incident_id),
                )
            else:
                require(revision == 1, "initial revision must be one", 409)
                cursor.execute(
                    f"INSERT INTO {self.table('incidents')} "
                    "(incident_id,current_revision,source_json,review_state,cas_version) VALUES (?,?,?,?,?)",
                    (incident_id, revision, canonical(result["source"]), "pending", 1),
                )
            eligible = True
            for ref in result["evidence_refs"]:
                tombstone = cursor.execute(
                    f"SELECT evidence_id FROM {self.table('evidence_tombstones')} WHERE evidence_id=?",
                    (ref["evidence_id"],),
                ).fetchone()
                eligible = eligible and tombstone is None
            for parent in result.get("knowledge_dependencies", []):
                publication = self._publication(cursor, parent)
                require(publication["source"] == result["source"], "knowledge dependency scope mismatch", 409)
                eligible = eligible and publication["eligible"]
                cursor.execute(
                    f"INSERT INTO {self.table('dependencies')} (publication_id,incident_id,revision) VALUES (?,?,?)",
                    (parent, incident_id, revision),
                )
            cursor.execute(
                f"INSERT INTO {self.table('revisions')} "
                "(incident_id,revision,result_id,result_hash,payload_json,eligible) VALUES (?,?,?,?,?,?)",
                (incident_id, revision, result["result_id"], result_hash, encoded, int(eligible)),
            )
            return self._revision(cursor, incident_id, revision)

    def get_revision(self, incident_id, revision):
        with self.transaction() as cursor:
            result = self._revision(cursor, incident_id, revision)
            rows = cursor.execute(
                f"SELECT receipt_json FROM {self.table('receipts')} ORDER BY receipt_id",
            ).fetchall()
            result["review_history"] = [
                receipt for row in rows if (receipt := json.loads(row[0])).get("incident_id") == incident_id
                and receipt.get("analysis_revision") == revision
            ]
            return result

    def current_revision(self, incident_id):
        with self.transaction() as cursor:
            return self._incident(cursor, incident_id)["revision"]

    def list_incidents(self, scopes):
        with self.transaction() as cursor:
            rows = cursor.execute(
                f"SELECT incident_id, current_revision, source_json FROM {self.table('incidents')} "
                "ORDER BY incident_id"
            ).fetchall()
            return [self._revision(cursor, row[0], row[1]) for row in rows
                    if (json.loads(row[2])["producer_key"], json.loads(row[2])["run_id"]) in scopes]

    def review(self, actor, incident_id, expected, key, body):
        fields(body, {"action", "reason"})
        require(isinstance(body["action"], str) and body["action"] in
                {"accept", "reject", "request_evidence", "close"}, "unknown review action")
        text(body["reason"])
        payload, route = {"body": body, "if_match": expected}, f"/incidents/{incident_id}/reviews"
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            incident, result = self._cas(cursor, incident_id, expected)
            if body["action"] == "accept":
                require(result["eligible"] and result["result"]["status"] == "completed",
                        "only current complete eligible analysis can be accepted", 409)
            state = {"accept": "accepted", "reject": "rejected", "request_evidence": "needs_evidence",
                     "close": "closed"}[body["action"]]
            if body["action"] == "close":
                require(incident["review_state"] == "accepted", "accept exact analysis before closure", 409)
            if body["action"] in {"reject", "request_evidence"}:
                rows = cursor.execute(
                    f"SELECT publication_id FROM {self.table('publications')} "
                    "WHERE incident_id=? AND revision=? AND eligible=1",
                    (incident_id, incident["revision"]),
                ).fetchall()
                for row in rows:
                    self._revoke(cursor, row[0], "review eligibility withdrawn")
            cursor.execute(
                f"UPDATE {self.table('incidents')} SET review_state=?, cas_version=cas_version+1 "
                "WHERE incident_id=?", (state, incident_id),
            )
            return self._receipt(cursor, actor, route, key, payload, {
                "incident_id": incident_id, "analysis_revision": incident["revision"],
                "result_hash": result["result_hash"], "action": body["action"], "reason": body["reason"],
                "review_state": state, "observed_lifecycle": result["result"].get("observed_lifecycle", "unknown"),
                "execution_status": "not_performed",
                "etag": self._revision(cursor, incident_id, incident["revision"])["etag"],
            })

    def approve_publication(self, actor, expected, key, body):
        from .contracts import identity_oid

        actor = identity_oid(actor)
        fields(body, {"incident_id", "analysis_revision", "draft_sha256", "reason", "expires_at_utc"})
        identifier(body["incident_id"], "incident_id")
        require(type(body["analysis_revision"]) is int and body["analysis_revision"] > 0, "invalid revision")
        text(body["reason"])
        expiry = utc(body["expires_at_utc"])
        payload = {"body": body, "if_match": expected}
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, "/publications", key, payload)
            if retry:
                return retry
            require(expiry > self._now(cursor), "publication expiry must be in the future")
            incident, revision = self._cas(cursor, body["incident_id"], expected)
            require(body["analysis_revision"] == incident["revision"], "stale publication revision", 409)
            require(revision["eligible"] and incident["review_state"] in {"accepted", "closed"},
                    "current eligible reviewed analysis required", 409)
            result = revision["result"]
            draft = result.get("postmortem_draft")
            require(draft is not None and result["stage_results"].get("postmortem-drafter", {}).get("status")
                    == "completed", "completed exact postmortem draft required", 409)
            require(body["draft_sha256"] == draft["sha256"], "draft hash mismatch", 409)
            author = draft["authorship"].get("oid")
            require(author is None or identity_oid(author) != actor,
                    "authors cannot approve their own revision", 403)
            refs = result["evidence_refs"]
            require(bool(refs) and all(ref["verified"] for ref in refs), "verified supporting evidence required", 409)
            until = min([expiry] + [utc(ref["retained_until_utc"]) for ref in refs])
            for parent in result.get("knowledge_dependencies", []):
                dependency = self._publication(cursor, parent)
                require(dependency["eligible"], "publication dependency is ineligible", 409)
                until = min(until, utc(dependency["eligible_until"]))
            require(until > self._now(cursor), "supporting evidence expired", 409)
            prior = cursor.execute(
                f"SELECT publication_id FROM {self.table('publications')} "
                "WHERE incident_id=? AND revision=?", (body["incident_id"], incident["revision"]),
            ).fetchone()
            require(prior is None, "publication already exists; create a new analysis/draft revision", 409)
            publication_id = str(uuid4())
            publication = {
                "publication_id": publication_id, "incident_id": body["incident_id"],
                "analysis_revision": incident["revision"], "source": result["source"],
                "result_hash": revision["result_hash"], "draft": draft, "evidence_refs": refs,
                "approved_by_oid": actor, "reason": body["reason"], "approved_at_utc": now_iso(),
                "eligible_until": until.isoformat().replace("+00:00", "Z"),
                "expires_at_utc": body["expires_at_utc"], "indexing_state": "pending",
                "searchable": False, **OVERLAY,
            }
            cursor.execute(
                f"INSERT INTO {self.table('publications')} "
                "(publication_id,incident_id,revision,payload_json,status,eligible,eligible_until) "
                "VALUES (?,?,?,?,?,?,?)",
                (publication_id, body["incident_id"], incident["revision"], canonical(publication),
                 "approved", 1, publication["eligible_until"]),
            )
            self._outbox(cursor, "publish-approved-revision", publication)
            cursor.execute(
                f"UPDATE {self.table('incidents')} SET cas_version=cas_version+1 WHERE incident_id=?",
                (body["incident_id"],),
            )
            return self._receipt(cursor, actor, "/publications", key, payload, {
                **publication, "status": "approved",
                "etag": self._revision(cursor, body["incident_id"], incident["revision"])["etag"],
            })

    def _publication(self, cursor, publication_id):
        row = cursor.execute(
            f"SELECT payload_json,status,eligible,eligible_until FROM {self.table('publications')} "
            "WHERE publication_id=?", (publication_id,),
        ).fetchone()
        require(row is not None, "publication not found", 404)
        value = json.loads(row[0])
        value.update(status=row[1], eligible=bool(row[2]) and utc(row[3]) > self._now(cursor))
        state = cursor.execute(
            f"SELECT accepted_outbox_id,generation,removed_generation FROM {self.table('publisher_state')} "
            "WHERE publication_id=?", (publication_id,),
        ).fetchone()
        if state:
            value["indexing_state"] = "delivered" if state[0] else "pending"
            value["searchable"] = bool(state[0]) and value["eligible"] and row[1] == "approved"
        if row[1] == "revoked":
            value["removal_state"] = "confirmed" if state and state[1] == state[2] else "pending"
        value["etag"] = f'"{digest([publication_id, row[1], bool(row[2]), row[3]])}"'
        return value

    def get_publication(self, publication_id):
        with self.transaction() as cursor:
            return self._publication(cursor, publication_id)

    def publication_for_publish(self, publication_id):
        """Compatibility preflight; publisher writes require prepare + fenced ack."""
        with self.transaction() as cursor:
            return self._publishable(cursor, publication_id)

    def _publishable(self, cursor, publication_id):
        value = self._publication(cursor, publication_id)
        require(value["eligible"] and value["status"] == "approved", "publication is ineligible", 409)
        revision = self._revision(cursor, value["incident_id"], value["analysis_revision"])
        require(revision["eligible"] and revision["review_state"] in {"accepted", "closed"}
                and revision["result_hash"] == value["result_hash"],
                "publication lineage is no longer eligible", 409)
        return value

    def publication_for_retrieval(self, publication_id, document_id, content_hash, *,
                                  snapshot_id=None, producer_key=None, run_id=None,
                                  analysis_revision=None, projection_hash=None):
        """SQL authority gate for EACH Search hit, not a reusable authorization.

        Caller must additionally enforce server-controlled producer/run scope.
        Return ledger content, never trust text/approval flags supplied by Search.
        """
        canonical_uuid(publication_id)
        identifier(document_id, "Search document ID")
        require(isinstance(content_hash, str) and len(content_hash) == 64
                and all(character in "0123456789abcdef" for character in content_hash), "content hash required")
        with self.transaction() as cursor:
            publication = self._publishable(cursor, publication_id)
            document = document_for(publication)
            require(document_id == document.document_id and content_hash == document.content_hash,
                    "Search projection content mismatch", 502)
            require(publication["searchable"], "Search projection is not accepted", 409)
            if any(value is not None for value in (snapshot_id, producer_key, run_id,
                                                   analysis_revision, projection_hash)):
                identifier(snapshot_id, "snapshot_id")
                identifier(producer_key, "producer_key")
                run_identifier(run_id)
                require(type(analysis_revision) is int and analysis_revision > 0, "exact revision required")
                require(isinstance(projection_hash, str) and len(projection_hash) == 64,
                        "projection hash required")
                row = cursor.execute(
                    f"SELECT projection_json FROM {self.table('publisher_projections')} WHERE publication_id=?",
                    (publication_id,),
                ).fetchone()
                require(row is not None, "publication has no scoped Search projection", 502)
                projection = json.loads(row[0])
                require(projection["snapshot_id"] == snapshot_id and digest(projection) == projection_hash
                        and publication["source"]["producer_key"] == producer_key
                        and publication["source"]["run_id"] == run_id
                        and publication["analysis_revision"] == analysis_revision,
                        "publication Search scope or snapshot mismatch", 502)
            return json.loads(document.content_json)

    def bind_publisher_projection(self, lease, projection):
        """Bind immutable destination/snapshot before external I/O; never approve."""
        fields(projection, {
            "publication_id", "analysis_revision", "producer_key", "run_id", "snapshot_id",
            "schema_hash", "search_endpoint", "index_name", "embedding_endpoint",
            "embedding_deployment", "embedding_model", "embedding_model_version", "dimensions",
        })
        canonical_uuid(projection["publication_id"])
        identifier(projection["snapshot_id"], "snapshot_id")
        identifier(projection["producer_key"], "producer_key")
        run_identifier(projection["run_id"])
        require(type(projection["analysis_revision"]) is int and projection["analysis_revision"] > 0,
                "exact projection revision required")
        require(type(projection["dimensions"]) is int and projection["dimensions"] == 1536,
                "projection dimensions must be 1536")
        for name in ("schema_hash", "search_endpoint", "index_name", "embedding_endpoint",
                     "embedding_deployment", "embedding_model", "embedding_model_version"):
            require(isinstance(projection[name], str) and 0 < len(projection[name]) <= 256,
                    "bounded projection metadata required")
        require(len(projection["schema_hash"]) == 64
                and all(c in "0123456789abcdef" for c in projection["schema_hash"]), "schema hash required")
        encoded = canonical(projection)
        with self.transaction() as cursor:
            job = self._publisher_job(cursor, lease)
            require(job["kind"] == PUBLISH, "only publication uploads can bind a projection", 409)
            publication = self._publishable(cursor, job["publication_id"])
            require(projection["publication_id"] == job["publication_id"]
                    and projection["analysis_revision"] == publication["analysis_revision"]
                    and projection["producer_key"] == publication["source"]["producer_key"]
                    and projection["run_id"] == publication["source"]["run_id"],
                    "projection must use immutable SQL publication identity", 409)
            prior = cursor.execute(
                f"SELECT projection_json FROM {self.table('publisher_projections')} WHERE publication_id=?",
                (job["publication_id"],),
            ).fetchone()
            if prior:
                require(prior[0] == encoded, "immutable publisher snapshot/target binding conflict", 409)
            else:
                cursor.execute(
                    f"INSERT INTO {self.table('publisher_projections')} (publication_id,projection_json) VALUES (?,?)",
                    (job["publication_id"], encoded),
                )
            self._guard_publisher(cursor, lease)

    def revoke_publication(self, actor, publication_id, expected, key, body):
        fields(body, {"reason"})
        text(body["reason"])
        route, payload = f"/publications/{publication_id}/revoke", {"body": body, "if_match": expected}
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            publication = self._publication(cursor, publication_id)
            require(expected == publication["etag"], "stale publication", 409)
            self._revoke(cursor, publication_id, body["reason"])
            return self._receipt(cursor, actor, route, key, payload, {
                "publication_id": publication_id, "incident_id": publication["incident_id"],
                "analysis_revision": publication["analysis_revision"], "reason": body["reason"],
                "status": "revoked", "searchable": False, "removal_state": "pending",
            })

    def invalidate_evidence(self, evidence_id, reason):
        """Internal erasure/correction hook: queue exclusion and workflow invalidation."""
        identifier(evidence_id, "evidence_id")
        text(reason)
        with self.transaction() as cursor:
            if cursor.execute(
                f"SELECT evidence_id FROM {self.table('evidence_tombstones')} WHERE evidence_id=?", (evidence_id,),
            ).fetchone() is None:
                cursor.execute(
                    f"INSERT INTO {self.table('evidence_tombstones')} (evidence_id,reason,revoked_at_utc) "
                    "VALUES (?,?,?)", (evidence_id, reason, now_iso()),
                )
            rows = cursor.execute(
                f"SELECT incident_id,revision,payload_json FROM {self.table('revisions')} WHERE eligible=1",
            ).fetchall()
            affected = []
            for incident_id, revision, payload in rows:
                if evidence_id in {ref["evidence_id"] for ref in json.loads(payload)["evidence_refs"]}:
                    self._invalidate(cursor, incident_id, revision, reason)
                    affected.append((incident_id, revision))
            return affected

    def expire_publications(self):
        """Internal scheduled operation; external Search removal remains pending."""
        with self.transaction() as cursor:
            rows = cursor.execute(
                f"SELECT publication_id,eligible_until FROM {self.table('publications')} WHERE eligible=1",
            ).fetchall()
            now = self._now(cursor)
            expired = [row[0] for row in rows if utc(row[1]) <= now]
            for publication_id in expired:
                self._revoke(cursor, publication_id, "supporting evidence or approval expired")
            return expired

    def pending_outbox(self):
        """Internal publisher inspection, not public or a delivery/lease API."""
        with self.transaction() as cursor:
            rows = cursor.execute(
                f"SELECT outbox_id,kind,payload_json,status FROM {self.table('outbox')} WHERE status='pending'",
            ).fetchall()
            return [{"outbox_id": r[0], "kind": r[1], "payload": json.loads(r[2]), "status": r[3]} for r in rows]

    def _publisher_job(self, cursor, lease):
        require(type(lease) is PublisherLease, "typed publisher lease required")
        lease.validate()
        row = cursor.execute(
            f"SELECT j.publication_id,j.owner,j.fencing_token,j.expires_at,o.kind,o.status "
            f"FROM {self.table('publisher_jobs')} j JOIN {self.table('outbox')} o "
            "ON o.outbox_id=j.outbox_id WHERE j.outbox_id=?", (lease.outbox_id,),
        ).fetchone()
        if (row is None or row[1] != lease.owner or row[2] != lease.fencing_token
                or row[4] not in PUBLISHER_KINDS
                or row[5] != "leased" or row[3] is None or self._expiry(row[3]) <= self._now(cursor)):
            raise LostPublisherLease()
        return {"publication_id": row[0], "kind": row[4], "status": row[5]}

    def _guard_publisher(self, cursor, lease, *, seconds=None, finish=False):
        """Last mutation fences again against the database clock, not host time."""
        now = "SYSUTCDATETIME()" if self.dialect == "sqlserver" else "?"
        parameters = ()
        if seconds is not None:
            assignment = "expires_at=DATEADD(second,?,SYSUTCDATETIME())" if self.dialect == "sqlserver" \
                else "expires_at=?"
            parameters = (seconds,) if self.dialect == "sqlserver" else (
                self._time(self._now(cursor) + timedelta(seconds=seconds)),
            )
        else:
            assignment = "expires_at=NULL" if finish else "fencing_token=fencing_token"
        output = " OUTPUT inserted.outbox_id" if self.dialect == "sqlserver" else ""
        returning = " RETURNING outbox_id" if self.dialect == "sqlite" else ""
        parameters += (lease.outbox_id, lease.owner, lease.fencing_token)
        if self.dialect == "sqlite":
            parameters += (self._time(self._now(cursor)),)
        row = cursor.execute(
            f"UPDATE {self.table('publisher_jobs')} SET {assignment}{output} "
            f"WHERE outbox_id=? AND owner=? AND fencing_token=? AND expires_at>{now}{returning}",
            parameters,
        ).fetchone()
        if row is None:
            raise LostPublisherLease()

    def _attempt_receipt(self, cursor, lease, status, **details):
        receipt = {
            "outbox_id": lease.outbox_id, "owner": lease.owner, "fencing_token": lease.fencing_token,
            "status": status, "recorded_at_utc": self._now(cursor).isoformat(),
            **details, **OVERLAY,
        }
        cursor.execute(
            f"UPDATE {self.table('publisher_attempts')} SET status=?,receipt_json=? "
            "WHERE outbox_id=? AND fencing_token=? AND owner=?",
            (status, canonical(receipt), lease.outbox_id, lease.fencing_token, lease.owner),
        )
        return receipt

    def claim_publisher(self, owner, lease_seconds=120, *, outbox_id=None):
        canonical_uuid(owner)
        if outbox_id is not None:
            canonical_uuid(outbox_id)
        validate_lease_seconds(lease_seconds)
        with self.transaction() as cursor:
            now = self._time(self._now(cursor))
            top = "TOP (1) " if self.dialect == "sqlserver" else ""
            limit = " LIMIT 1" if self.dialect == "sqlite" else ""
            selected = "AND j.outbox_id=? " if outbox_id is not None else ""
            parameters = (PUBLISH, EXCLUDE, now, now, now)
            if outbox_id is not None:
                parameters += (outbox_id,)
            row = cursor.execute(
                f"SELECT {top}j.outbox_id,j.fencing_token,j.owner FROM {self.table('publisher_jobs')} j "
                f"JOIN {self.table('outbox')} o ON o.outbox_id=j.outbox_id "
                "WHERE o.kind IN (?,?) AND (o.status IN ('pending','failed') OR "
                "(o.status='leased' AND j.expires_at<=?)) "
                "AND (j.not_before IS NULL OR j.not_before<=?) AND NOT EXISTS ("
                f"SELECT 1 FROM {self.table('publisher_jobs')} busy "
                f"JOIN {self.table('outbox')} bo ON bo.outbox_id=busy.outbox_id "
                "WHERE busy.publication_id=j.publication_id AND bo.status='leased' AND busy.expires_at>?) "
                f"{selected}"
                f"ORDER BY CASE WHEN o.kind=? THEN 0 ELSE 1 END,j.fencing_token,j.outbox_id{limit}",
                (*parameters, EXCLUDE),
            ).fetchone()
            if row is None:
                return None
            if row[1]:
                old = PublisherLease(row[0], row[2], row[1])
                prior = cursor.execute(
                    f"SELECT status FROM {self.table('publisher_attempts')} "
                    "WHERE outbox_id=? AND fencing_token=?", (row[0], row[1]),
                ).fetchone()
                if prior and prior[0] in {"claimed", "inflight"}:
                    self._attempt_receipt(
                        cursor, old, "uncertain" if prior[0] == "inflight" else "abandoned",
                        code="lease-reclaimed", message="No accepted completion before lease expiry",
                    )
            lease = PublisherLease(row[0], owner, row[1] + 1)
            lease.validate()
            if self.dialect == "sqlserver":
                cursor.execute(
                    f"UPDATE {self.table('publisher_jobs')} SET owner=?,fencing_token=?,"
                    "expires_at=DATEADD(second,?,SYSUTCDATETIME()) WHERE outbox_id=?",
                    (owner, lease.fencing_token, lease_seconds, lease.outbox_id),
                )
            else:
                cursor.execute(
                    f"UPDATE {self.table('publisher_jobs')} SET owner=?,fencing_token=?,expires_at=? "
                    "WHERE outbox_id=?",
                    (owner, lease.fencing_token, self._time(self._now(cursor) + timedelta(seconds=lease_seconds)),
                     lease.outbox_id),
                )
            cursor.execute(
                f"UPDATE {self.table('outbox')} SET status='leased' WHERE outbox_id=?", (lease.outbox_id,),
            )
            cursor.execute(
                f"INSERT INTO {self.table('publisher_attempts')} "
                "(outbox_id,fencing_token,owner,status,receipt_json) VALUES (?,?,?,'claimed','{}')",
                (lease.outbox_id, lease.fencing_token, owner),
            )
            self._attempt_receipt(cursor, lease, "claimed")
            return lease

    def renew_publisher(self, lease, lease_seconds=120):
        validate_lease_seconds(lease_seconds)
        with self.transaction() as cursor:
            self._publisher_job(cursor, lease)
            self._guard_publisher(cursor, lease, seconds=lease_seconds)
            return lease

    def _delivery_request(self, cursor, outbox_id, job):
        publication = self._publication(cursor, job["publication_id"])
        # Deletion depends only on immutable identity, not on a possibly oversized
        # document body; a rejected upload must still be removable.
        key = "pub_" + digest([publication["publication_id"], publication["analysis_revision"]])
        document = document_for(publication) if job["kind"] == PUBLISH else None
        return DeliveryRequest(
            digest(["publisher-v1", outbox_id]), "upload" if job["kind"] == PUBLISH else "delete",
            key, document,
        )

    def _queue_exclusion(self, cursor, publication_id, reason, *, delay_seconds=0, expedite=False):
        pending = cursor.execute(
            f"SELECT j.outbox_id FROM {self.table('publisher_jobs')} j "
            f"JOIN {self.table('outbox')} o ON o.outbox_id=j.outbox_id "
            "WHERE j.publication_id=? AND o.kind=? AND o.status IN ('pending','failed')",
            (publication_id, EXCLUDE),
        ).fetchone()
        if pending:
            if expedite:
                cursor.execute(
                    f"UPDATE {self.table('publisher_jobs')} SET not_before=NULL WHERE outbox_id=?",
                    (pending[0],),
                )
            return pending[0]
        outbox_id = self._outbox(cursor, EXCLUDE, {"publication_id": publication_id, "reason": reason})
        if delay_seconds:
            cursor.execute(
                f"UPDATE {self.table('publisher_jobs')} SET not_before=? WHERE outbox_id=?",
                (self._time(self._now(cursor) + timedelta(seconds=delay_seconds)), outbox_id),
            )
        return outbox_id

    def _reject_publish(self, cursor, lease, job, *, delivery=None):
        publication_id = job["publication_id"]
        publication = self._publication(cursor, publication_id)
        if publication["status"] != "revoked":
            self._revoke(cursor, publication_id, "publisher eligibility check rejected publication")
        self._queue_exclusion(cursor, publication_id, "unaccepted upload requires removal")
        cursor.execute(
            f"UPDATE {self.table('publisher_state')} SET accepted_outbox_id=NULL,generation=generation+1 "
            "WHERE publication_id=?", (publication_id,),
        )
        cursor.execute(
            f"UPDATE {self.table('outbox')} SET status='cancelled' WHERE outbox_id=?", (lease.outbox_id,),
        )
        receipt = self._attempt_receipt(cursor, lease, "rejected", delivery=delivery)
        self._guard_publisher(cursor, lease, finish=True)
        return receipt

    def prepare_publisher(self, lease):
        with self.transaction() as cursor:
            job = self._publisher_job(cursor, lease)
            attempt = cursor.execute(
                f"SELECT status FROM {self.table('publisher_attempts')} "
                "WHERE outbox_id=? AND fencing_token=?", (lease.outbox_id, lease.fencing_token),
            ).fetchone()
            require(attempt is not None and attempt[0] == "claimed",
                    "publisher attempt already prepared; renew or retry with a new claim", 409)
            if job["kind"] == PUBLISH:
                try:
                    self._publishable(cursor, job["publication_id"])
                except ReviewError as exc:
                    if exc.status != 409:
                        raise
                    self._reject_publish(cursor, lease, job)
                    return None
            else:
                require(not self._publication(cursor, job["publication_id"])["eligible"],
                        "cannot exclude an eligible publication", 409)
            request = self._delivery_request(cursor, lease.outbox_id, job)
            if job["kind"] == PUBLISH:
                cursor.execute(
                    f"UPDATE {self.table('publisher_state')} SET generation=generation+1 "
                    "WHERE publication_id=?", (job["publication_id"],),
                )
            generation = cursor.execute(
                f"SELECT generation FROM {self.table('publisher_state')} WHERE publication_id=?",
                (job["publication_id"],),
            ).fetchone()[0]
            cursor.execute(
                f"UPDATE {self.table('publisher_attempts')} SET started_generation=? "
                "WHERE outbox_id=? AND fencing_token=?",
                (generation, lease.outbox_id, lease.fencing_token),
            )
            self._attempt_receipt(cursor, lease, "inflight", operation=request.operation,
                                  document_id=request.document_id, operation_id=request.operation_id)
            self._guard_publisher(cursor, lease)
            return request

    def _unsettled_uploads(self, cursor, publication_id):
        return cursor.execute(
            f"SELECT a.outbox_id FROM {self.table('publisher_attempts')} a "
            f"JOIN {self.table('publisher_jobs')} j ON j.outbox_id=a.outbox_id "
            f"JOIN {self.table('outbox')} o ON o.outbox_id=j.outbox_id "
            "WHERE j.publication_id=? AND o.kind=? AND a.status IN ('inflight','uncertain')",
            (publication_id, PUBLISH),
        ).fetchone() is not None

    def ack_publisher(self, lease, receipt):
        with self.transaction() as cursor:
            job = self._publisher_job(cursor, lease)
            attempt = cursor.execute(
                f"SELECT status,started_generation FROM {self.table('publisher_attempts')} "
                "WHERE outbox_id=? AND fencing_token=?", (lease.outbox_id, lease.fencing_token),
            ).fetchone()
            require(attempt is not None and attempt[0] == "inflight", "prepared publisher attempt required", 409)
            delivery = validate_receipt(self._delivery_request(cursor, lease.outbox_id, job), receipt)
            if job["kind"] == PUBLISH:
                try:
                    self._publishable(cursor, job["publication_id"])
                except ReviewError as exc:
                    if exc.status != 409:
                        raise
                    return self._reject_publish(cursor, lease, job, delivery=delivery)
                cursor.execute(
                    f"UPDATE {self.table('publisher_state')} SET accepted_outbox_id=?,removed_generation=NULL "
                    "WHERE publication_id=?", (lease.outbox_id, job["publication_id"]),
                )
                status = "accepted"
            else:
                require(not self._publication(cursor, job["publication_id"])["eligible"],
                        "cannot accept deletion of an eligible publication", 409)
                generation = cursor.execute(
                    f"SELECT generation FROM {self.table('publisher_state')} WHERE publication_id=?",
                    (job["publication_id"],),
                ).fetchone()[0]
                clean = generation == attempt[1] and not self._unsettled_uploads(cursor, job["publication_id"])
                cursor.execute(
                    f"UPDATE {self.table('publisher_state')} SET accepted_outbox_id=NULL,removed_generation=? "
                    "WHERE publication_id=?", (generation if clean else None, job["publication_id"]),
                )
                status = "removed" if clean else "removal-pending"
                if not clean:
                    self._queue_exclusion(
                        cursor, job["publication_id"], "unsettled upload or changed generation", delay_seconds=60,
                    )
            cursor.execute(
                f"UPDATE {self.table('outbox')} SET status='processed' WHERE outbox_id=?", (lease.outbox_id,),
            )
            result = self._attempt_receipt(cursor, lease, status, delivery=delivery)
            self._guard_publisher(cursor, lease, finish=True)
            return result

    def fail_publisher(self, lease, code, message):
        identifier(code, "publisher failure code")
        text(message, "publisher failure", 2000)
        require(len(message) <= 2000, "publisher failure exceeds limit")
        with self.transaction() as cursor:
            job = self._publisher_job(cursor, lease)
            attempt = cursor.execute(
                f"SELECT status FROM {self.table('publisher_attempts')} WHERE outbox_id=? AND fencing_token=?",
                (lease.outbox_id, lease.fencing_token),
            ).fetchone()
            require(attempt is not None and attempt[0] in {"claimed", "inflight"},
                    "live publisher attempt required", 409)
            uncertain = attempt[0] == "inflight"
            result = self._attempt_receipt(
                cursor, lease, "uncertain" if uncertain else "failed", code=code, message=message,
            )
            cursor.execute(
                f"UPDATE {self.table('outbox')} SET status='failed' WHERE outbox_id=?", (lease.outbox_id,),
            )
            if uncertain and job["kind"] == PUBLISH:
                cursor.execute(
                    f"UPDATE {self.table('publisher_state')} SET accepted_outbox_id=NULL "
                    "WHERE publication_id=?", (job["publication_id"],),
                )
                if not self._publication(cursor, job["publication_id"])["eligible"]:
                    self._queue_exclusion(cursor, job["publication_id"], "unconfirmed upload after exclusion")
            self._guard_publisher(cursor, lease, finish=True)
            return result

    def observe_late_publisher(self, lease, receipt):
        """Record an old call's outcome, NEVER acknowledge its outbox or eligibility.

        This may close uncertainty for exactly that attempt, but forces a new
        removal generation. It cannot stop an unknown/crashed remote call.
        """
        require(type(lease) is PublisherLease, "typed publisher lease required")
        lease.validate()
        with self.transaction() as cursor:
            row = cursor.execute(
                f"SELECT a.owner,a.status,j.publication_id,o.kind,j.owner,j.fencing_token,j.expires_at,o.status "
                f"FROM {self.table('publisher_attempts')} a "
                f"JOIN {self.table('publisher_jobs')} j ON j.outbox_id=a.outbox_id "
                f"JOIN {self.table('outbox')} o ON o.outbox_id=j.outbox_id "
                "WHERE a.outbox_id=? AND a.fencing_token=?", (lease.outbox_id, lease.fencing_token),
            ).fetchone()
            require(row is not None and row[0] == lease.owner and row[3] in PUBLISHER_KINDS,
                    "late publisher attempt does not match", 409)
            require(not (row[4] == lease.owner and row[5] == lease.fencing_token and row[7] == "leased"
                         and row[6] is not None and self._expiry(row[6]) > self._now(cursor)),
                    "active publisher attempt must use fenced acknowledgement", 409)
            job = {"publication_id": row[2], "kind": row[3]}
            delivery = validate_receipt(self._delivery_request(cursor, lease.outbox_id, job), receipt)
            require(row[1] in {"inflight", "uncertain", "late"}, "attempt has no unresolved delivery", 409)
            if row[1] == "late":
                prior = json.loads(cursor.execute(
                    f"SELECT receipt_json FROM {self.table('publisher_attempts')} "
                    "WHERE outbox_id=? AND fencing_token=?", (lease.outbox_id, lease.fencing_token),
                ).fetchone()[0])
                require(prior["delivery"] == delivery, "late receipt retry changed its outcome", 409)
                return prior
            result = self._attempt_receipt(cursor, lease, "late", delivery=delivery)
            if job["kind"] == PUBLISH:
                cursor.execute(
                    f"UPDATE {self.table('publisher_state')} SET generation=generation+1,removed_generation=NULL "
                    "WHERE publication_id=?", (job["publication_id"],),
                )
                if not self._publication(cursor, job["publication_id"])["eligible"]:
                    self._queue_exclusion(
                        cursor, job["publication_id"], "late upload requires another deletion", expedite=True,
                    )
            return result

    def publisher_attempts(self, outbox_id, *, after_token=0, limit=100):
        canonical_uuid(outbox_id)
        require(type(after_token) is int and 0 <= after_token < 2**63, "invalid receipt cursor")
        require(type(limit) is int and 1 <= limit <= 1000, "receipt limit must be 1..1000")
        with self.transaction() as cursor:
            top = f"TOP ({limit}) " if self.dialect == "sqlserver" else ""
            tail = f" LIMIT {limit}" if self.dialect == "sqlite" else ""
            rows = cursor.execute(
                f"SELECT {top}receipt_json FROM {self.table('publisher_attempts')} "
                f"WHERE outbox_id=? AND fencing_token>? ORDER BY fencing_token{tail}", (outbox_id, after_token),
            ).fetchall()
            return [json.loads(row[0]) for row in rows]

    def reconcile_publisher(self, limit=100):
        """Bounded recurring sweep. Unknown uploads stay pending, not 'deleted'.

        SQL expiry is enforced on reads even if this maintenance is not running.
        """
        require(type(limit) is int and 1 <= limit <= 1000, "reconciliation limit must be 1..1000")
        with self.transaction() as cursor:
            top = f"TOP ({limit}) " if self.dialect == "sqlserver" else ""
            tail = f" LIMIT {limit}" if self.dialect == "sqlite" else ""
            rows = cursor.execute(
                f"SELECT {top}p.publication_id,p.status,p.eligible_until FROM {self.table('publications')} p "
                f"JOIN {self.table('publisher_state')} s ON s.publication_id=p.publication_id "
                "WHERE s.removed_generation IS NULL OR s.removed_generation<>s.generation OR p.status<>'revoked' "
                f"ORDER BY s.reconciled_at,p.eligible_until,p.publication_id{tail}",
            ).fetchall()
            queued = []
            for publication_id, status, expiry in rows:
                cursor.execute(
                    f"UPDATE {self.table('publisher_state')} SET reconciled_at=? WHERE publication_id=?",
                    (self._time(self._now(cursor)), publication_id),
                )
                if status != "revoked" and utc(expiry) > self._now(cursor):
                    continue
                if status != "revoked":
                    self._revoke(cursor, publication_id, "supporting evidence or approval expired")
                queued.append(self._queue_exclusion(cursor, publication_id, "durable removal reconciliation"))
            return queued

    def reserve_publication_advance(self, actor, route, key, publication_id):
        canonical_uuid(publication_id)
        payload = {"publication_id": publication_id}
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            self._publication(cursor, publication_id)
            top = "TOP (1) " if self.dialect == "sqlserver" else ""
            tail = " LIMIT 1" if self.dialect == "sqlite" else ""
            extract = "JSON_VALUE" if self.dialect == "sqlserver" else "json_extract"
            row = cursor.execute(
                f"SELECT {top}outbox_id FROM {self.table('outbox')} "
                f"WHERE {extract}(payload_json,'$.publication_id')=? AND kind IN (?,?) "
                "AND status IN ('pending','failed','leased') "
                f"ORDER BY CASE WHEN kind=? THEN 0 ELSE 1 END,outbox_id{tail}",
                (publication_id, PUBLISH, EXCLUDE, EXCLUDE),
            ).fetchone()
            return self._receipt(cursor, actor, route, key, payload, {
                "publication_id": publication_id, "outbox_id": row[0] if row else None,
                "status": "selected" if row else "no_queued_work",
            })

    def reserve_work_advance(self, submitter, actor, route, key, root_id):
        from .advancement import select_work
        payload = {"root_job_id": root_id}
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            selected = select_work(submitter, root_id)
            return self._receipt(cursor, actor, route, key, payload, selected)

    def submit_lifecycle(self, submitter, actor, route, key, payload):
        from .work import WorkRequest
        require(payload["lifecycle_request"] in {"postmortem", "hygiene"}, "unsupported lifecycle request")
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            authoritative = {**payload, "requested_by_oid": identity_oid(actor), **OVERLAY}
            job_id = submitter.enqueue(WorkRequest(
                payload["lifecycle_request"], digest(["explicit-lifecycle", authoritative]), authoritative))
            return self._receipt(cursor, actor, route, key, payload,
                                 {"job_id": job_id, "status": "queued", "source": payload["source"]})

    def submit_projection(self, submitter, advancer, actor, route, key, payload):
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            request_key = digest({"actor": identity_oid(actor), "route": route, "key": key})
            job_id = advancer.queue_projection(payload["workflow_id"], request_key)
            queued = submitter.get_job(job_id)
            expected = {"workflow_id": payload["workflow_id"], "source": payload["source"],
                        "producer_key": payload["source"]["producer_key"],
                        "run_id": payload["source"]["run_id"],
                        "result_evidence": payload["result_evidence"], **OVERLAY}
            require(isinstance(queued, dict) and queued.get("kind") == "projection"
                    and queued.get("payload") == expected,
                    "projection queue receipt differs from the completed source/result", 503)
            return self._receipt(cursor, actor, route, key, payload, {
                "job_id": job_id, "workflow_id": payload["workflow_id"], "status": "queued",
                "source": payload["source"], "submission_only": True,
                "inference_performed": False, "embedding_performed": False,
            })

    def submit_replacement(self, submitter, advancer, actor, route, key, payload):
        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            job_id = advancer.queue_replacement(payload["workflow_id"], actor)
            queued = submitter.get_job(job_id)
            work = queued.get("payload") if isinstance(queued, dict) else None
            require(isinstance(work, dict) and queued.get("kind") == "replacement"
                    and set(work) == {"replacement_of", "replacement_approval_id", "requested_by_oid",
                                      "source", "incident_id", "candidate_evidence", *OVERLAY}
                    and work["replacement_of"] == payload["workflow_id"]
                    and work["requested_by_oid"] == identity_oid(actor)
                    and work["source"] == payload["source"]
                    and work["incident_id"] == payload["incident_id"]
                    and work["candidate_evidence"] == payload["candidate_evidence"]
                    and all(type(work[k]) is type(v) and work[k] == v for k, v in OVERLAY.items()),
                    "replacement queue receipt differs from the authorized failed source/candidate", 503)
            identifier(work["replacement_approval_id"], "replacement approval ID")
            return self._receipt(cursor, actor, route, key, payload, {
                "job_id": job_id, "workflow_id": payload["workflow_id"], "status": "queued",
                "source": payload["source"], "submission_only": True,
                "inference_performed": False, "embedding_performed": False,
            })

    def submit_work(self, submitter, actor, route, key, payload):
        """Consumer enqueue MUST durably deduplicate logical_key and reject payload drift.

        The queue commit precedes the receipt transaction commit. On a crash the
        same deterministic logical key recovers that job, not a second request.
        Publication never uses this cross-ledger path; it has its own SQL outbox.
        """
        from .work import WorkRequest

        with self.transaction() as cursor:
            retry = self._retry(cursor, actor, route, key, payload)
            if retry:
                return retry
            logical_key = digest({"actor": actor, "route": route, "key": key})
            kinds = {"/analysis-jobs": "analysis", "/hygiene-jobs": "hygiene",
                     "/investigation-jobs": "investigation"}
            require(route in kinds, "unsupported work submission route")
            kind = kinds[route]
            authority = {"requested_by_oid": identity_oid(actor)} if kind == "investigation" else {}
            job_id = submitter.enqueue(WorkRequest(kind, logical_key, {**payload, **authority, **OVERLAY}))
            require(isinstance(job_id, str) and job_id, "work queue did not return a durable job ID", 503)
            return self._receipt(cursor, actor, route, key, payload, {
                "job_id": job_id, "status": "queued", "source": payload["source"], "window": payload["window"],
            })


class SQLiteReviewRepository(_Repository):
    """Local tests only; :memory: does not create any filesystem artifact."""

    def __init__(self, path=":memory:"):
        self.in_memory = str(path) == ":memory:"
        self.connection = sqlite3.connect(":memory:", check_same_thread=False) if self.in_memory else None
        super().__init__(
            (lambda: self.connection) if self.in_memory else
            (lambda: sqlite3.connect(str(path), timeout=10)), "sqlite",
        )
        with self.transaction() as cursor:
            for statement in _SQLITE_SCHEMA:
                cursor.execute(statement)
            missing = cursor.execute(
                "SELECT o.outbox_id FROM review_outbox o WHERE o.kind IN (?,?) AND NOT EXISTS "
                "(SELECT 1 FROM review_publisher_jobs j WHERE j.outbox_id=o.outbox_id)", (PUBLISH, EXCLUDE),
            ).fetchone()
            require(missing is None, "publisher migration found an unknown publication; repair ledger", 503)

    def close(self):
        if self.connection:
            self.connection.close()


class SqlServerReviewRepository(_Repository):
    def __init__(self, connection_factory):
        super().__init__(connection_factory, "sqlserver")


_SQLITE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS review_incidents (incident_id TEXT PRIMARY KEY, current_revision INTEGER NOT NULL, "
    "source_json TEXT NOT NULL, review_state TEXT NOT NULL, cas_version INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS review_revisions (incident_id TEXT NOT NULL, revision INTEGER NOT NULL, "
    "result_id TEXT NOT NULL UNIQUE, result_hash TEXT NOT NULL, payload_json TEXT NOT NULL, eligible INTEGER NOT NULL, "
    "PRIMARY KEY(incident_id,revision))",
    "CREATE TABLE IF NOT EXISTS review_receipts (receipt_id TEXT PRIMARY KEY, actor TEXT NOT NULL, route TEXT NOT NULL, "
    "idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL, receipt_json TEXT NOT NULL, "
    "UNIQUE(actor,route,idempotency_key))",
    "CREATE TABLE IF NOT EXISTS review_publications (publication_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, "
    "revision INTEGER NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, eligible INTEGER NOT NULL, "
    "eligible_until TEXT NOT NULL, UNIQUE(incident_id,revision))",
    "CREATE TABLE IF NOT EXISTS review_outbox (outbox_id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
    "payload_json TEXT NOT NULL, status TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS review_dependencies (publication_id TEXT NOT NULL, incident_id TEXT NOT NULL, "
    "revision INTEGER NOT NULL, PRIMARY KEY(publication_id,incident_id,revision))",
    "CREATE TABLE IF NOT EXISTS review_evidence_tombstones (evidence_id TEXT PRIMARY KEY, reason TEXT NOT NULL, "
    "revoked_at_utc TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS review_publisher_state (publication_id TEXT PRIMARY KEY "
    "REFERENCES review_publications(publication_id), generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0), "
    "accepted_outbox_id TEXT NULL REFERENCES review_outbox(outbox_id), removed_generation INTEGER NULL, "
    "reconciled_at TEXT NULL)",
    "CREATE TABLE IF NOT EXISTS review_publisher_jobs (outbox_id TEXT PRIMARY KEY REFERENCES review_outbox(outbox_id), "
    "publication_id TEXT NOT NULL REFERENCES review_publications(publication_id), owner TEXT NULL, "
    "fencing_token INTEGER NOT NULL DEFAULT 0 CHECK(fencing_token>=0), expires_at TEXT NULL, not_before TEXT NULL)",
    "CREATE INDEX IF NOT EXISTS review_publisher_jobs_publication ON review_publisher_jobs(publication_id,expires_at)",
    "CREATE TABLE IF NOT EXISTS review_publisher_attempts (outbox_id TEXT NOT NULL "
    "REFERENCES review_publisher_jobs(outbox_id), fencing_token INTEGER NOT NULL CHECK(fencing_token>0), "
    "owner TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN "
    "('claimed','inflight','uncertain','abandoned','failed','rejected','accepted','removed','removal-pending','late')), "
    "receipt_json TEXT NOT NULL, started_generation INTEGER NULL, "
    "PRIMARY KEY(outbox_id,fencing_token))",
    "CREATE TABLE IF NOT EXISTS review_publisher_projections (publication_id TEXT PRIMARY KEY "
    "REFERENCES review_publications(publication_id), projection_json TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS review_publisher_projection_immutable BEFORE UPDATE ON review_publisher_projections "
    "BEGIN SELECT RAISE(ABORT,'immutable publisher projection'); END",
    "INSERT OR IGNORE INTO review_publisher_state (publication_id,generation) "
    "SELECT publication_id,0 FROM review_publications",
    "INSERT OR IGNORE INTO review_publisher_jobs (outbox_id,publication_id,fencing_token) "
    "SELECT o.outbox_id,p.publication_id,0 FROM review_outbox o JOIN review_publications p "
    "ON p.publication_id=json_extract(o.payload_json,'$.publication_id') "
    "WHERE o.kind IN ('publish-approved-revision','exclude-publication')",
    "CREATE TRIGGER IF NOT EXISTS review_revisions_immutable BEFORE UPDATE OF incident_id,revision,result_id,"
    "result_hash,payload_json ON review_revisions BEGIN SELECT RAISE(ABORT,'immutable review revision'); END",
    "CREATE TRIGGER IF NOT EXISTS review_receipts_immutable BEFORE UPDATE ON review_receipts "
    "BEGIN SELECT RAISE(ABORT,'immutable review receipt'); END",
    "CREATE TRIGGER IF NOT EXISTS review_publications_immutable BEFORE UPDATE OF publication_id,incident_id,"
    "revision,payload_json ON review_publications BEGIN SELECT RAISE(ABORT,'immutable publication revision'); END",
)
