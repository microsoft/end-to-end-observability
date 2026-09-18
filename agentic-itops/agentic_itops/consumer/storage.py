"""Transactional evidence receipts and fenced work.

SqlServerWorkflowStore is the production dialect. Its factory must return a fresh
qmark DB-API connection with autocommit disabled (for example mssql-python or
pyodbc). Construction never connects or migrates. Run bootstrap_schema explicitly,
under separate migration authorization, before using a newly provisioned database.

LocalSQLiteWorkflowStore is LOCAL-TEST ONLY. BEGIN IMMEDIATE and its injectable
clock exercise semantics, not SQL Server concurrency, deployment, or durability.
Neither backend writes Blob objects, calls models, or executes remediation.

Migration 002 adds nullable job scope keys. Existing jobs are deliberately
excluded from scoped claims until explicitly running backfill_job_scopes().
Bootstrap applies additive migrations but never silently backfills job identity.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, Callable

from .contracts import ContractError, Observation, ProducerScope, canonical, digest, iso, strict_json, utc
from .logs import CompleteBatch
from .storage_contract import (
    EvidenceRef, IngestCommit, IntegrityConflict, LostLease, WorkLease, WorkRequest,
)


def _text(value: str, name: str, maximum: int) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-16-le")) // 2 > maximum
            or any(ord(char) < 32 for char in value)):
        raise ContractError(f"{name} must be a nonempty bounded string without control characters")
    return value


def _json(value: Any) -> str:
    return canonical(value).decode("utf-8")


def _seconds(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 86400:
        raise ContractError("lease_seconds must be an integer from 1 to 86400")
    return value


def _synthetic(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("work result must be a JSON object")
    for key, expected in (("IsSynthetic", True), ("OperationalUse", "prohibited"),
                          ("execution_allowed", False)):
        if key in value and (type(value[key]) is not type(expected) or value[key] != expected):
            raise ContractError("synthetic work cannot authorize physical execution")
    # An immutable copy also prevents callers mutating a payload during a transaction.
    return strict_json(canonical({
        **value, "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }))


class _WorkflowStore:
    sql_server = False

    def __init__(self, connection_factory: Callable[[], Any]):
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self.connection_factory = connection_factory

    def _table(self, name: str) -> str:
        # Names come only from constants in this module; no caller identifiers are interpolated.
        return ("dbo." if self.sql_server else "") + "consumer_" + name

    @contextmanager
    def _transaction(self):
        connection = self.connection_factory()
        try:
            if self.sql_server:
                if getattr(connection, "autocommit", False):
                    raise ContractError("SQL Server requires autocommit=False")
                cursor = connection.cursor()
                cursor.execute("SET NOCOUNT ON; SET XACT_ABORT ON; "
                               "SET TRANSACTION ISOLATION LEVEL READ COMMITTED; "
                               "IF @@TRANCOUNT=0 BEGIN TRANSACTION;")
            else:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.cursor()
            yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _one(self, cursor, statement: str, parameters: tuple = ()) -> dict[str, Any] | None:
        cursor.execute(statement, parameters)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip((column[0] for column in cursor.description), row))

    def _now(self, cursor) -> datetime:
        if self.sql_server:
            value = self._one(cursor, "SELECT SYSUTCDATETIME() AS now")["now"]
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else utc(value)
        return utc(self.clock())

    def _time(self, value: datetime):
        value = utc(value)
        return (value.replace(tzinfo=None) if self.sql_server else
                value.isoformat(timespec="microseconds").replace("+00:00", "Z"))

    def _locked(self) -> str:
        return " WITH (UPDLOCK, HOLDLOCK)" if self.sql_server else ""

    def _insert_lock(self, cursor) -> None:
        if self.sql_server:
            # A short transaction-scoped insertion mutex prevents unique-key gap-lock
            # conversion deadlocks across different batches/jobs. Claims use row locks.
            cursor.execute(
                "DECLARE @rc int; EXEC @rc = sys.sp_getapplock "
                "@Resource=?, @LockMode='Exclusive', @LockOwner='Transaction', "
                "@LockTimeout=30000; IF @rc < 0 THROW 51000, 'consumer insertion lock failed', 1;",
                ("agentic-itops:consumer:insert:v1",),
            )

    @staticmethod
    def _request(work: WorkRequest) -> tuple[str, str, str]:
        if not isinstance(work, WorkRequest):
            raise ContractError("work must be a WorkRequest")
        _text(work.kind, "kind", 128)
        _text(work.logical_key, "logical_key", 1024)
        if not isinstance(work.payload, dict):
            raise ContractError("work payload must be a JSON object")
        payload = _json(work.payload)
        return (digest([work.kind, work.logical_key]), payload,
                hashlib.sha256(payload.encode("utf-8")).hexdigest())

    @staticmethod
    def _payload_scope(payload: dict[str, Any]) -> tuple[str, str] | None:
        pairs = []
        for source in (payload, payload.get("source")):
            if not isinstance(source, dict) or not ({"producer_key", "run_id"} & source.keys()):
                continue
            producer = _text(source.get("producer_key"), "producer_key", 64)
            run = _text(source.get("run_id"), "run_id", 512)
            pairs.append((producer, run))
        if len(set(pairs)) > 1:
            raise IntegrityConflict("direct and nested work scopes disagree")
        return pairs[0] if pairs else None

    @staticmethod
    def _scope_digest(pair: tuple[str, str]) -> str:
        return digest(list(pair))

    def _enqueue(self, cursor, work: WorkRequest) -> str:
        job_id, payload, payload_sha256 = self._request(work)
        scope = self._payload_scope(strict_json(payload))
        scope_key = None if scope is None else self._scope_digest(scope)
        current = self._one(cursor, f"SELECT kind, logical_key, payload_sha256 FROM "
                            f"{self._table('jobs')}{self._locked()} WHERE job_id=?", (job_id,))
        if current is not None:
            if (current["kind"] != work.kind or current["logical_key"] != work.logical_key
                    or current["payload_sha256"] != payload_sha256):
                raise IntegrityConflict("work idempotency key already has different content")
            return job_id
        cursor.execute(
            f"INSERT INTO {self._table('jobs')} "
            "(job_id, kind, kind_key, scope_key, logical_key, payload_json, payload_sha256, "
            "status, fencing_token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)",
            (job_id, work.kind, digest(work.kind), scope_key, work.logical_key, payload, payload_sha256,
             self._time(self._now(cursor))),
        )
        return job_id

    def enqueue(self, work: WorkRequest) -> str:
        self._request(work)
        with self._transaction() as cursor:
            self._insert_lock(cursor)
            return self._enqueue(cursor, work)

    def commit_ingestion(self, batch: CompleteBatch, evidence: EvidenceRef, *,
                         lease: WorkLease | None = None) -> IngestCommit:
        """Commit only after the caller has written and verified the raw Blob.

        Each revision queues ``coverage``; nonempty revisions also queue
        ``detector``. Both use batch_id as logical_key and carry the exact
        producer/run/window and EvidenceRef fields. Replays return the first
        receipt, including its original counts, rather than a new zero-count receipt.
        A supplied lease must be an active ingest lease whose DB-held payload
        scope matches this batch. None is reserved for explicit standalone use.
        """
        if not isinstance(batch, CompleteBatch) or not isinstance(evidence, EvidenceRef):
            raise ContractError("complete typed batch and EvidenceRef are required")
        _text(evidence.key, "evidence key", 2048)
        artifact = canonical(batch.to_dict())
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        if (evidence.sha256 != artifact_sha256 or type(evidence.byte_length) is not int
                or evidence.byte_length != len(artifact)):
            raise IntegrityConflict("evidence SHA/length does not match the complete canonical batch")
        if (type(batch.duplicate_count) is not int or batch.duplicate_count < 0
                or type(batch.received_row_count) is not int
                or batch.received_row_count != len(batch.records) + batch.duplicate_count):
            raise ContractError("complete batch row counts are inconsistent")
        producer_key, window = batch.producer.key, batch.window
        records = {}
        for record in batch.records:
            observation = Observation.parse(batch.producer, record.observation.row)
            row = observation.row
            if (record.observation.producer.key != producer_key
                    or record.observation.document != observation.document
                    or row["RunId"] != window.run_id or row["Surface"] not in window.surfaces
                    or not window.observed_start <= utc(row["ObservedAt"]) < window.observed_end
                    or not window.ingestion_start <= utc(record.ingested_at) < window.ingestion_end
                    or not window.service_start <= utc(row["TimeGenerated"]) < window.service_end):
                raise ContractError("batch observation is outside its trusted producer/run/window")
            if observation.event_key in records:
                raise ContractError("complete batch records must already be deduplicated")
            records[observation.event_key] = observation
        batch_id = digest([producer_key, window.key, artifact_sha256])
        with self._transaction() as cursor:
            self._insert_lock(cursor)
            if lease is not None:
                job = self._verify_lease(cursor, lease)
                if job["kind"] != "ingest":
                    raise LostLease("ingestion requires an active ingest-kind lease")
                expected_scope = (producer_key, window.run_id)
                if (self._payload_scope(strict_json(job["payload_json"])) != expected_scope
                        or job["scope_key"] != self._scope_digest(expected_scope)):
                    raise IntegrityConflict("batch producer/run does not match the stored ingest job scope")
            previous = self._one(cursor, f"SELECT receipt_json FROM {self._table('batches')}"
                                 f"{self._locked()} WHERE batch_id=?", (batch_id,))
            if previous is not None:
                if lease is not None:
                    self._guarded_update(cursor, lease, "fencing_token=fencing_token", ())
                return self._receipt(strict_json(previous["receipt_json"]))
            inserted = 0
            for event_key, observation in sorted(records.items()):
                row = observation.row
                previous = self._one(
                    cursor, f"SELECT source_sha256 FROM {self._table('events')}"
                    f"{self._locked()} WHERE event_key=?", (event_key,),
                )
                if previous is not None:
                    if previous["source_sha256"] != observation.source_sha256:
                        raise IntegrityConflict("producer EventId already has different source content")
                else:
                    cursor.execute(
                        f"INSERT INTO {self._table('events')} "
                        "(event_key, producer_key, event_id, run_id, observed_at, "
                        "source_sha256, observation_sha256, observation_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (event_key, producer_key, row["EventId"], row["RunId"],
                         self._time(utc(row["ObservedAt"])), observation.source_sha256,
                         observation.sha256, observation.document.decode("utf-8")),
                    )
                    inserted += 1
            payload = {
                "batch_id": batch_id, "producer_key": producer_key, "run_id": window.run_id,
                "window": window.to_dict(),
                "evidence": {"key": evidence.key, "sha256": evidence.sha256,
                             "byte_length": evidence.byte_length},
                "coverage": "observations_present" if records else "no_observations",
                "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
            }
            kinds = ("coverage", "detector") if records else ("coverage",)
            jobs = tuple(self._enqueue(cursor, WorkRequest(kind, batch_id, payload)) for kind in kinds)
            receipt = IngestCommit(batch_id, inserted, batch.received_row_count - inserted, jobs)
            cursor.execute(
                f"INSERT INTO {self._table('batches')} "
                "(batch_id, producer_key, run_id, run_key, window_key, window_json, observed_start, observed_end, "
                "ingestion_start, ingestion_end, artifact_sha256, artifact_bytes, evidence_key, "
                "coverage, receipt_json, committed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, producer_key, window.run_id, digest(window.run_id), window.key, _json(window.to_dict()),
                 self._time(window.observed_start), self._time(window.observed_end),
                 self._time(window.ingestion_start), self._time(window.ingestion_end),
                 evidence.sha256, evidence.byte_length, evidence.key, payload["coverage"],
                 _json({"batch_id": batch_id, "inserted_events": receipt.inserted_events,
                        "duplicate_events": receipt.duplicate_events, "job_ids": list(jobs)}),
                 self._time(self._now(cursor))),
            )
            for event_key in sorted(records):
                cursor.execute(f"INSERT INTO {self._table('batch_events')} (batch_id, event_key) "
                               "VALUES (?, ?)", (batch_id, event_key))
            if lease is not None:
                self._guarded_update(cursor, lease, "fencing_token=fencing_token", ())
            return receipt

    @staticmethod
    def _receipt(value: dict[str, Any]) -> IngestCommit:
        return IngestCommit(value["batch_id"], value["inserted_events"],
                            value["duplicate_events"], tuple(value["job_ids"]))

    @staticmethod
    def _lease(row: dict[str, Any]) -> WorkLease:
        expiry = row["expires_at"]
        if isinstance(expiry, datetime) and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return WorkLease(row["job_id"], row["kind"], row["owner"], row["fencing_token"],
                         utc(expiry), strict_json(row["payload_json"]))

    def claim(self, kind: str, owner: str, lease_seconds: int = 120, *,
              scopes: tuple[tuple[str, str], ...] | None = None, job_id: str | None = None) -> WorkLease | None:
        """Atomically select only trusted exact producer/run pairs when supplied.

        None preserves standalone behavior; () claims nothing. Direct and nested
        payload.source identities are normalized at enqueue, never from a lease.
        """
        _text(kind, "kind", 128)
        _text(owner, "owner", 256)
        _seconds(lease_seconds)
        scope_keys: tuple[str, ...] = ()
        scope_filter = ""
        if scopes is not None:
            if not isinstance(scopes, tuple) or len(scopes) > 1000:
                raise ContractError("scopes must be a tuple with at most 1000 producer/run pairs")
            for pair in scopes:
                if not isinstance(pair, tuple) or len(pair) != 2:
                    raise ContractError("each scope must be a producer/run tuple")
                _text(pair[0], "producer_key", 64)
                _text(pair[1], "run_id", 512)
            scope_keys = tuple(sorted({self._scope_digest(pair) for pair in scopes}))
            if not scope_keys:
                return None
            scope_filter = "AND scope_key IN (" + ",".join("?" for _ in scope_keys) + ") "
        if job_id is not None:
            if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{64}", job_id):
                raise ContractError("an exact job digest is required")
            scope_filter += "AND job_id=? "
            scope_keys += (job_id,)
        with self._transaction() as cursor:
            if self.sql_server:
                row = self._one(cursor,
                    "DECLARE @now datetime2(7)=SYSUTCDATETIME(); "
                    f";WITH candidate AS (SELECT TOP (1) * FROM {self._table('jobs')} "
                    "WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK) "
                    f"WHERE kind_key=? {scope_filter}AND "
                    "(status='queued' OR (status='leased' AND expires_at<=@now)) "
                    "ORDER BY created_at, job_id) "
                    "UPDATE candidate SET status='leased', owner=?, fencing_token=fencing_token+1, "
                    "expires_at=DATEADD(second, ?, SYSUTCDATETIME()) OUTPUT inserted.*;",
                    (digest(kind), *scope_keys, owner, lease_seconds))
            else:
                now = self._now(cursor)
                row = self._one(cursor,
                    f"SELECT * FROM {self._table('jobs')} WHERE kind_key=? {scope_filter}AND "
                    "(status='queued' OR (status='leased' AND expires_at<=?)) "
                    "ORDER BY created_at, job_id LIMIT 1", (digest(kind), *scope_keys, self._time(now)))
                if row is not None:
                    cursor.execute(
                        f"UPDATE {self._table('jobs')} SET status='leased', owner=?, "
                        "fencing_token=fencing_token+1, expires_at=? WHERE job_id=?",
                        (owner, self._time(now + timedelta(seconds=lease_seconds)), row["job_id"]))
                    row = self._one(cursor, f"SELECT * FROM {self._table('jobs')} WHERE job_id=?",
                                    (row["job_id"],))
            return None if row is None else self._lease(row)

    def backfill_job_scopes(self) -> int:
        """Explicit post-migration backfill from DB-held immutable payload identity.

        Unscoped payloads remain NULL; malformed/conflicting scopes abort the
        entire backfill. No job status/owner/fence or payload is changed.
        """
        updated = 0
        with self._transaction() as cursor:
            self._insert_lock(cursor)
            cursor.execute(f"SELECT job_id, payload_json FROM {self._table('jobs')}"
                           f"{self._locked()} WHERE scope_key IS NULL")
            rows = cursor.fetchall()
            for job_id, payload_json in rows:
                scope = self._payload_scope(strict_json(payload_json))
                if scope is not None:
                    cursor.execute(f"UPDATE {self._table('jobs')} SET scope_key=? WHERE job_id=?",
                                   (self._scope_digest(scope), job_id))
                    updated += 1
        return updated

    def _verify_lease(self, cursor, lease: WorkLease) -> dict[str, Any]:
        if (not isinstance(lease, WorkLease) or type(lease.fencing_token) is not int
                or lease.fencing_token < 1):
            raise LostLease("a valid WorkLease is required")
        row = self._one(cursor, f"SELECT * FROM {self._table('jobs')}{self._locked()} WHERE job_id=?",
                        (lease.job_id,))
        if (row is None or row["status"] != "leased" or row["owner"] != lease.owner
                or row["kind"] != lease.kind or row["fencing_token"] != lease.fencing_token
                or self._lease(row).expires_at <= self._now(cursor)):
            raise LostLease("work lease expired, was reclaimed, or does not match its owner/fence")
        return row

    def _guarded_update(self, cursor, lease: WorkLease, assignments: str, parameters: tuple) -> None:
        # Recheck at the mutation statement, not just at transaction entry: the
        # worker may have blocked behind another writer until its lease expired.
        now = "SYSUTCDATETIME()" if self.sql_server else "?"
        values = parameters + (lease.job_id, lease.owner, lease.fencing_token)
        if not self.sql_server:
            values += (self._time(self._now(cursor)),)
        output = " OUTPUT inserted.job_id" if self.sql_server else ""
        cursor.execute(
            f"UPDATE {self._table('jobs')} SET {assignments}{output} "
            f"WHERE job_id=? AND status='leased' AND owner=? AND fencing_token=? AND expires_at>{now}",
            values,
        )
        changed = cursor.fetchone() is not None if self.sql_server else cursor.rowcount == 1
        if not changed:
            raise LostLease("work lease expired before the atomic state change")

    def renew(self, lease: WorkLease, lease_seconds: int = 120) -> WorkLease:
        _seconds(lease_seconds)
        with self._transaction() as cursor:
            self._verify_lease(cursor, lease)
            if self.sql_server:
                self._guarded_update(cursor, lease, "expires_at=DATEADD(second, ?, SYSUTCDATETIME())",
                                     (lease_seconds,))
            else:
                self._guarded_update(cursor, lease, "expires_at=?",
                                     (self._time(self._now(cursor) + timedelta(seconds=lease_seconds)),))
            return self._lease(self._one(cursor, f"SELECT * FROM {self._table('jobs')} WHERE job_id=?",
                                        (lease.job_id,)))

    def complete(self, lease: WorkLease, result: dict[str, Any],
                 next_work: tuple[WorkRequest, ...] = ()) -> None:
        with self._transaction() as cursor:
            self._insert_lock(cursor)
            self._verify_lease(cursor, lease)
            result = _synthetic(result)
            job_ids = tuple(self._enqueue(cursor, work) for work in next_work)
            cursor.execute(
                f"INSERT INTO {self._table('results')} "
                "(job_id, fencing_token, result_json, result_sha256, next_job_ids_json, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (lease.job_id, lease.fencing_token, _json(result), digest(result),
                 _json(list(job_ids)), self._time(self._now(cursor))),
            )
            self._guarded_update(cursor, lease, "status='completed', finished_at=?, expires_at=NULL",
                                 (self._time(self._now(cursor)),))

    def fail(self, lease: WorkLease, code: str, message: str) -> None:
        with self._transaction() as cursor:
            self._verify_lease(cursor, lease)
            _text(code, "error code", 128)
            _text(message, "error message", 4000)
            self._guarded_update(
                cursor, lease,
                "status='failed', error_code=?, error_message=?, finished_at=?, expires_at=NULL",
                (code, message, self._time(self._now(cursor))),
            )

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Read persisted status/payload/result/error and outbox IDs; absent IDs raise KeyError."""
        _text(job_id, "job_id", 64)
        with self._transaction() as cursor:
            row = self._one(cursor, f"SELECT * FROM {self._table('jobs')}{self._locked()} WHERE job_id=?",
                            (job_id,))
            if row is None:
                raise KeyError(job_id)
            result = self._one(cursor, f"SELECT * FROM {self._table('results')} WHERE job_id=?", (job_id,))
            row["payload"] = strict_json(row.pop("payload_json"))
            row["result"] = None if result is None else strict_json(result["result_json"])
            row["next_job_ids"] = () if result is None else tuple(strict_json(result["next_job_ids_json"]))
            row["result_sha256"] = None if result is None else result["result_sha256"]
            for key in ("created_at", "expires_at", "finished_at"):
                value = row[key]
                if isinstance(value, datetime) and value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                row[key] = None if value is None else utc(value)
            return row

    def lookup_evidence(self, producer: ProducerScope, run_id: str,
                        observed_start: datetime, observed_end: datetime) -> tuple[dict[str, Any], ...]:
        """Return committed overlapping subwindows/revisions, never infer gap coverage.

        The producer must be the host-configured ProducerScope, not model payload
        identity. Bounds are half-open. Each entry has batch_id, window, coverage,
        evidence (EvidenceRef), and receipt (IngestCommit); empty coverage is unknown.
        This is an internal repository API, not a caller authentication boundary.
        """
        if not isinstance(producer, ProducerScope):
            raise ContractError("lookup requires an explicit ProducerScope")
        _text(run_id, "run_id", 512)
        start, end = utc(observed_start), utc(observed_end)
        if start >= end:
            raise ContractError("observation lookup window must be nonempty")
        with self._transaction() as cursor:
            cursor.execute(
                f"SELECT batch_id, window_json, coverage, evidence_key, artifact_sha256, "
                f"artifact_bytes, receipt_json FROM {self._table('batches')} "
                "WHERE producer_key=? AND run_key=? AND observed_start<? AND observed_end>? "
                "ORDER BY ingestion_start, ingestion_end, batch_id",
                (producer.key, digest(run_id), self._time(end), self._time(start)),
            )
            return tuple({
                "batch_id": row[0], "window": strict_json(row[1]), "coverage": row[2],
                "evidence": EvidenceRef(row[3], row[4], row[5]),
                "receipt": self._receipt(strict_json(row[6])),
            } for row in cursor.fetchall())


class SqlServerWorkflowStore(_WorkflowStore):
    """Production SQL SERVER backend; migration is explicit and never automatic."""

    sql_server = True

    def bootstrap_schema(self) -> None:
        with self._transaction() as cursor:
            self._insert_lock(cursor)
            for migration in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
                for statement in re.split(r"(?im)^\s*GO\s*$", migration.read_text(encoding="utf-8")):
                    if statement.strip():
                        cursor.execute(statement)


class LocalSQLiteWorkflowStore(_WorkflowStore):
    """LOCAL-TEST ONLY. SQLite tests do not validate SQL Server locking behavior."""

    def __init__(self, connection_factory: Callable[[], Any], *,
                 clock: Callable[[], datetime] | None = None):
        super().__init__(connection_factory)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def bootstrap_schema(self) -> None:
        with self._transaction() as cursor:
            for statement in _SQLITE_SCHEMA:
                cursor.execute(statement)
            cursor.execute("PRAGMA table_info(consumer_jobs)")
            if "scope_key" not in {row[1] for row in cursor.fetchall()}:
                cursor.execute("ALTER TABLE consumer_jobs ADD COLUMN scope_key TEXT")
            cursor.execute("CREATE INDEX IF NOT EXISTS consumer_jobs_scoped_claim ON "
                           "consumer_jobs(kind_key, scope_key, status, created_at, job_id)")


_SQLITE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS consumer_events (
        event_key TEXT PRIMARY KEY, producer_key TEXT NOT NULL, event_id TEXT NOT NULL COLLATE BINARY,
        run_id TEXT NOT NULL COLLATE BINARY, observed_at TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        observation_sha256 TEXT NOT NULL, observation_json TEXT NOT NULL,
        UNIQUE (producer_key, event_id))""",
    """CREATE TABLE IF NOT EXISTS consumer_batches (
        batch_id TEXT PRIMARY KEY, producer_key TEXT NOT NULL, run_id TEXT NOT NULL COLLATE BINARY,
        run_key TEXT NOT NULL, window_key TEXT NOT NULL, window_json TEXT NOT NULL, observed_start TEXT NOT NULL,
        observed_end TEXT NOT NULL, ingestion_start TEXT NOT NULL, ingestion_end TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL, artifact_bytes INTEGER NOT NULL, evidence_key TEXT NOT NULL,
        coverage TEXT NOT NULL CHECK (coverage IN ('observations_present', 'no_observations')),
        receipt_json TEXT NOT NULL, committed_at TEXT NOT NULL,
        UNIQUE (producer_key, window_key, artifact_sha256))""",
    """CREATE TABLE IF NOT EXISTS consumer_batch_events (
        batch_id TEXT NOT NULL REFERENCES consumer_batches(batch_id),
        event_key TEXT NOT NULL REFERENCES consumer_events(event_key),
        PRIMARY KEY (batch_id, event_key))""",
    """CREATE TABLE IF NOT EXISTS consumer_jobs (
        job_id TEXT PRIMARY KEY, kind TEXT NOT NULL COLLATE BINARY, kind_key TEXT NOT NULL,
        scope_key TEXT,
        logical_key TEXT NOT NULL COLLATE BINARY, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('queued', 'leased', 'completed', 'failed')),
        owner TEXT COLLATE BINARY, fencing_token INTEGER NOT NULL CHECK (fencing_token >= 0),
        expires_at TEXT, created_at TEXT NOT NULL, finished_at TEXT,
        error_code TEXT, error_message TEXT, UNIQUE (kind, logical_key))""",
    """CREATE TABLE IF NOT EXISTS consumer_results (
        job_id TEXT PRIMARY KEY REFERENCES consumer_jobs(job_id), fencing_token INTEGER NOT NULL,
        result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,
        next_job_ids_json TEXT NOT NULL, completed_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS consumer_jobs_claim ON consumer_jobs(kind_key, status, created_at, job_id)",
    "CREATE INDEX IF NOT EXISTS consumer_batches_scope ON "
    "consumer_batches(producer_key, run_key, observed_start, observed_end)",
) + tuple(
    f"CREATE TRIGGER IF NOT EXISTS consumer_{table}_no_{operation.lower()} "
    f"BEFORE {operation} ON consumer_{table} BEGIN "
    "SELECT RAISE(ABORT, 'immutable consumer ledger'); END"
    for table in ("events", "batches", "batch_events", "results")
    for operation in ("UPDATE", "DELETE")
)
