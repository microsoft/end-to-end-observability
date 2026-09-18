"""Injection contract; no queue, model, producer or Azure client is created here."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WorkRequest:
    kind: str
    logical_key: str
    payload: dict


class WorkSubmitter(Protocol):
    def enqueue(self, request: WorkRequest) -> str:
        """Durably enqueue; identical logical key/payload returns original ID."""
        ...

    def get_job(self, job_id: str) -> dict | None:
        """Return source, status and optional window/error/result_id; never secrets."""
        ...


class ConsumerWorkAdapter:
    """Bridge to consumer WorkRequest without importing consumer modules here.

    Pass consumer.storage_contract.IntegrityConflict as conflict_type to map
    a recovered-queue idempotency conflict to HTTP 409 rather than HTTP 503.
    """
    def __init__(self, storage, request_type, *, conflict_type=()):
        self.storage = storage
        self.request_type = request_type
        self.conflict_type = conflict_type

    def enqueue(self, request):
        from .contracts import ReviewError

        try:
            return self.storage.enqueue(self.request_type(
                kind=request.kind, logical_key=request.logical_key, payload=request.payload,
            ))
        except self.conflict_type as exc:
            raise ReviewError("work idempotency key reused with changed payload", 409) from exc

    def get_job(self, job_id):
        try:
            return self.storage.get_job(job_id)
        except KeyError:
            return None
