"""Bounded, synthetic-only Search delivery. No SDK, credentials, or model authority.

Adapters must return actual per-document service outcomes, not enqueue receipts.
Exceptions (including ambiguous timeouts) remain visible and are never successes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from .contracts import OVERLAY, ReviewError, canonical, digest, identifier, require


PUBLISH = "publish-approved-revision"
EXCLUDE = "exclude-publication"
PUBLISHER_KINDS = (PUBLISH, EXCLUDE)
MAX_DOCUMENT_BYTES = 1_000_000


class LostPublisherLease(ReviewError):
    def __init__(self):
        super().__init__("publisher lease expired, reclaimed, or owner/fence mismatch", 409)


def canonical_uuid(value: str) -> str:
    require(isinstance(value, str), "canonical nonzero UUID required")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ReviewError("canonical nonzero UUID required") from exc
    require(parsed.int != 0 and str(parsed) == value, "canonical nonzero UUID required")
    return value


def validate_lease_seconds(value: int) -> int:
    require(type(value) is int and 1 <= value <= 3600, "lease_seconds must be an integer from 1 to 3600")
    return value


@dataclass(frozen=True)
class PublisherLease:
    outbox_id: str
    owner: str
    fencing_token: int

    def validate(self) -> None:
        canonical_uuid(self.outbox_id)
        canonical_uuid(self.owner)
        require(type(self.fencing_token) is int and 0 < self.fencing_token < 2**63,
                "positive publisher fencing token required")


@dataclass(frozen=True)
class SearchDocument:
    document_id: str
    publication_id: str
    analysis_revision: int
    content_json: str
    content_hash: str


@dataclass(frozen=True)
class DeliveryRequest:
    operation_id: str
    operation: Literal["upload", "delete"]
    document_id: str
    document: SearchDocument | None


@dataclass(frozen=True)
class IndexingResult:
    document_id: str
    succeeded: bool
    status_code: int
    error_message: str | None = None


@dataclass(frozen=True)
class SearchDeliveryReceipt:
    operation_id: str
    operation: Literal["upload", "delete"]
    request_id: str
    results: tuple[IndexingResult, ...]


class SearchDelivery(Protocol):
    """Trusted injected adapter; one immutable document per operation.

    Upload is a complete upsert (not merge). Delete is idempotent by key.
    operation_id is stable across retries; it is NOT a Search fencing primitive.
    The adapter must enforce finite network timeouts and translate actual SDK
    per-item results, including failures. Never synthesize successful receipts.
    """

    def upload(self, document: SearchDocument, *, operation_id: str) -> SearchDeliveryReceipt: ...
    def delete(self, document_id: str, *, operation_id: str) -> SearchDeliveryReceipt: ...


class PublisherRepository(Protocol):
    def claim_publisher(self, owner: str, lease_seconds: int = 120, *, outbox_id: str | None = None) -> PublisherLease | None: ...
    def renew_publisher(self, lease: PublisherLease, lease_seconds: int = 120) -> PublisherLease: ...
    def prepare_publisher(self, lease: PublisherLease) -> DeliveryRequest | None: ...
    def ack_publisher(self, lease: PublisherLease, receipt: SearchDeliveryReceipt) -> dict: ...
    def fail_publisher(self, lease: PublisherLease, code: str, message: str) -> dict: ...
    def observe_late_publisher(self, lease: PublisherLease, receipt: SearchDeliveryReceipt) -> dict: ...
    def reconcile_publisher(self, limit: int = 100) -> list[str]: ...
    def bind_publisher_projection(self, lease: PublisherLease, projection: dict) -> None: ...


def document_for(publication: dict) -> SearchDocument:
    # Whitelist immutable ledger content; no model output chooses index, key,
    # approval, credentials, lease, or retrieval filters.
    publication_id = canonical_uuid(publication["publication_id"])
    revision = publication["analysis_revision"]
    require(type(revision) is int and revision > 0, "positive publication revision required")
    document_id = "pub_" + digest([publication_id, revision])
    content = {
        "publication_id": publication_id, "analysis_revision": revision,
        "incident_id": publication["incident_id"], "result_hash": publication["result_hash"],
        "draft": publication["draft"], "source": publication["source"],
        "evidence_refs": publication["evidence_refs"], "eligible_until": publication["eligible_until"],
        **OVERLAY,
    }
    encoded = canonical(content)
    require(len(encoded.encode("utf-8")) <= MAX_DOCUMENT_BYTES, "publisher document exceeds byte limit")
    return SearchDocument(document_id, publication_id, revision, encoded, digest(content))


def validate_receipt(request: DeliveryRequest, receipt: SearchDeliveryReceipt) -> dict:
    require(type(receipt) is SearchDeliveryReceipt, "typed Search delivery receipt required", 502)
    require(receipt.operation_id == request.operation_id and receipt.operation == request.operation,
            "Search receipt operation mismatch", 502)
    identifier(receipt.request_id, "Search request ID")
    require(type(receipt.results) is tuple and len(receipt.results) == 1,
            "exactly one Search indexing result required", 502)
    item = receipt.results[0]
    require(type(item) is IndexingResult and item.document_id == request.document_id,
            "Search result document mismatch", 502)
    require(type(item.succeeded) is bool and type(item.status_code) is int,
            "typed Search result status required", 502)
    require(item.error_message is None or isinstance(item.error_message, str)
            and 0 < len(item.error_message) <= 2000, "invalid Search error message", 502)
    allowed = (200, 201) if request.operation == "upload" else (200,)
    require(item.succeeded and item.status_code in allowed and item.error_message is None,
            "Search did not confirm successful per-document delivery", 502)
    return {
        "operation_id": receipt.operation_id, "operation": receipt.operation,
        "request_id": receipt.request_id, "document_id": item.document_id,
        "status_code": item.status_code, "succeeded": True,
    }


class PublisherDispatcher:
    def __init__(self, repository: PublisherRepository, delivery: SearchDelivery, *,
                 owner: str, lease_seconds: int = 120):
        self.repository = repository
        self.delivery = delivery
        self.owner = canonical_uuid(owner)
        self.lease_seconds = validate_lease_seconds(lease_seconds)
        require(callable(getattr(delivery, "upload", None)) and callable(getattr(delivery, "delete", None)),
                "explicit Search delivery adapter required")

    def run_once(self, *, outbox_id: str | None = None) -> dict | None:
        """One job, no transaction spanning I/O; host owns cadence and renewal.

        Raises on backend/receipt/lease failures after attempting durable failure
        recording. A crashed process instead leaves an inspectable inflight
        attempt; reclaim and reconciliation do not assume that call was aborted.
        """
        if outbox_id is not None:
            canonical_uuid(outbox_id)
        lease = (self.repository.claim_publisher(self.owner, self.lease_seconds, outbox_id=outbox_id)
                 if outbox_id is not None else self.repository.claim_publisher(self.owner, self.lease_seconds))
        if lease is None:
            return None
        try:
            request = self.repository.prepare_publisher(lease)
            if request is None:
                return {"outbox_id": lease.outbox_id, "status": "cancelled", **OVERLAY}
            require(type(request) is DeliveryRequest and request.operation in {"upload", "delete"},
                    "known typed publisher delivery request required")
            if request.operation == "upload":
                require(request.document is not None, "upload document required")
                projection_for = getattr(self.delivery, "projection_for", None)
                if projection_for is not None:
                    require(callable(projection_for), "publisher projection provider must be callable")
                    self.repository.bind_publisher_projection(lease, projection_for(request.document))
                receipt = self.delivery.upload(request.document, operation_id=request.operation_id)
            else:
                receipt = self.delivery.delete(request.document_id, operation_id=request.operation_id)
            validate_receipt(request, receipt)
        except LostPublisherLease:
            raise
        except Exception as exc:
            # Persist a bounded, non-sensitive classification, then propagate the
            # original failure. Unexpected adapter errors are not swallowed.
            try:
                self.repository.fail_publisher(
                    lease, "delivery-unconfirmed", "Search delivery raised or returned an invalid outcome",
                )
            except LostPublisherLease as lost:
                raise lost from exc
            raise
        try:
            return self.repository.ack_publisher(lease, receipt)
        except LostPublisherLease:
            self.repository.observe_late_publisher(lease, receipt)
            raise
