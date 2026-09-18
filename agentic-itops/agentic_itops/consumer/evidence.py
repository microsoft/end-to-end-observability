from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from .contracts import ContractError, canonical
from .logs import LogsReader, QueryWindow, WindowTooLarge
from .storage_contract import EvidenceRef, EvidenceStore, IngestCommit, IntegrityConflict, WorkflowStore

if TYPE_CHECKING:
    from azure.storage.blob import ContainerClient


MAX_EVIDENCE_BYTES = 16_000_000


def evidence_ref(content: bytes) -> EvidenceRef:
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_EVIDENCE_BYTES:
        raise ContractError("evidence must be nonempty bounded bytes")
    sha256 = hashlib.sha256(content).hexdigest()
    return EvidenceRef(f"evidence/{sha256}.json", sha256, len(content))


def validate_reference(reference: EvidenceRef) -> None:
    if (not isinstance(reference.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", reference.sha256)
            or reference.key != f"evidence/{reference.sha256}.json"
            or type(reference.byte_length) is not int
            or not 0 < reference.byte_length <= MAX_EVIDENCE_BYTES):
        raise IntegrityConflict("invalid content-addressed evidence reference")


def verify(content: bytes, reference: EvidenceRef) -> bytes:
    validate_reference(reference)
    if len(content) != reference.byte_length or hashlib.sha256(content).hexdigest() != reference.sha256:
        raise IntegrityConflict("evidence content does not match its immutable reference")
    return content


class LocalEvidenceStore:
    """Local development/test storage, never an implicit Azure fallback."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        (self.root / "evidence").mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> EvidenceRef:
        reference = evidence_ref(content)
        target = self.root / "evidence" / f"{reference.sha256}.json"
        temporary = self.root / "evidence" / f".{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink()
        self.read(reference)
        return reference

    def read(self, reference: EvidenceRef) -> bytes:
        validate_reference(reference)
        path = self.root / "evidence" / f"{reference.sha256}.json"
        with path.open("rb") as stream:
            return verify(stream.read(reference.byte_length + 1), reference)


class AzureEvidenceStore:
    def __init__(self, container: ContainerClient):
        self.container = container

    def put(self, content: bytes) -> EvidenceRef:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        reference = evidence_ref(content)
        blob = self.container.get_blob_client(reference.key)
        try:
            blob.upload_blob(content, overwrite=False, metadata={"sha256": reference.sha256},
                             content_settings=ContentSettings(content_type="application/json"))
        except ResourceExistsError:
            pass
        self.read(reference)
        return reference

    def read(self, reference: EvidenceRef) -> bytes:
        validate_reference(reference)
        blob = self.container.get_blob_client(reference.key)
        if blob.get_blob_properties().size != reference.byte_length:
            raise IntegrityConflict("Blob evidence length does not match its reference")
        content = blob.download_blob(offset=0, length=reference.byte_length).readall()
        return verify(content, reference)


class IngestionService:
    def __init__(self, reader: LogsReader, evidence: EvidenceStore, workflow: WorkflowStore):
        self.reader, self.evidence, self.workflow = reader, evidence, workflow

    def ingest(self, window: QueryWindow) -> tuple[IngestCommit, ...]:
        try:
            batch = self.reader.read(window)
            content = canonical(batch.to_dict())
            if len(content) > MAX_EVIDENCE_BYTES:
                raise WindowTooLarge("materialized evidence exceeds the artifact limit")
        except WindowTooLarge:
            if window.ingestion_end - window.ingestion_start <= timedelta(seconds=1):
                raise WindowTooLarge("cannot safely subdivide further; explicit catch-up is required") from None
            midpoint = window.ingestion_start + (window.ingestion_end - window.ingestion_start) / 2
            left = self.ingest(replace(window, ingestion_end=midpoint))
            right = self.ingest(replace(window, ingestion_start=midpoint))
            return left + right
        reference = self.evidence.put(content)
        verify(self.evidence.read(reference), evidence_ref(content))
        if reference != evidence_ref(content):
            raise IntegrityConflict("evidence store returned a different artifact reference")
        return (self.workflow.commit_ingestion(batch, reference),)
