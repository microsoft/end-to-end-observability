from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from .logs import CompleteBatch


class IntegrityConflict(RuntimeError):
    pass


class LostLease(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceRef:
    key: str
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class IngestCommit:
    batch_id: str
    inserted_events: int
    duplicate_events: int
    job_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkRequest:
    kind: str
    logical_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class WorkLease:
    job_id: str
    kind: str
    owner: str
    fencing_token: int
    expires_at: datetime
    payload: dict[str, Any]


class EvidenceStore(Protocol):
    def put(self, content: bytes) -> EvidenceRef: ...
    def read(self, reference: EvidenceRef) -> bytes: ...


class WorkflowStore(Protocol):
    def commit_ingestion(self, batch: CompleteBatch, evidence: EvidenceRef,
                         *, lease: WorkLease | None = None) -> IngestCommit: ...
    def enqueue(self, work: WorkRequest) -> str: ...
    def claim(self, kind: str, owner: str, lease_seconds: int = 120,
              *, scopes: tuple[tuple[str, str], ...] | None = None) -> WorkLease | None: ...
    def renew(self, lease: WorkLease, lease_seconds: int = 120) -> WorkLease: ...
    def complete(self, lease: WorkLease, result: dict[str, Any],
                 next_work: tuple[WorkRequest, ...] = ()) -> None: ...
    def fail(self, lease: WorkLease, code: str, message: str) -> None: ...
    def get_job(self, job_id: str) -> dict[str, Any]: ...
