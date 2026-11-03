"""Durable one-sequence ownership and truthful asynchronous operation states."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlparse
from uuid import UUID

SUBSCRIPTION = "00000001-000a-4000-a000-000000000001"
TARGETS = {
    f"/subscriptions/{SUBSCRIPTION}/resourcegroups/rg-asre-{role}-cus/providers/microsoft.compute/virtualmachines/vm-asre-{role}-cus": role
    for role in ("linux", "patch", "backup")
}
ALLOWED_ACTIONS = {
    "linux": {"recover_workload"},
    "patch": {"recover_selected_patch"},
    "backup": {"recover_guest_agent_and_backup"},
}
TERMINAL = {"verified", "failed", "escalated"}
EVIDENCE_HOST = f"stasre{hashlib.sha256(f'{SUBSCRIPTION}:asre-demo'.encode()).hexdigest()[:10]}.blob.core.windows.net"
VERIFICATION_CHECKS = {
    "recover_workload": {"service_healthy", "resource_below_threshold", "stable_observation_window"},
    "recover_selected_patch": {"installation_succeeded", "approved_version_installed"},
    "recover_guest_agent_and_backup": {"job_completed", "new_recovery_point"},
}


class ConflictError(RuntimeError):
    pass


class Table(Protocol):
    def get(self, partition: str, key: str) -> tuple[dict, str]: ...
    def create_batch(self, entities: list[dict]) -> None: ...
    def replace(self, entity: dict, etag: str) -> None: ...
    def finish(self, entity: dict, etag: str, lock: dict, lock_etag: str) -> None: ...


def normalized_target(value: str) -> str:
    if not isinstance(value, str) or value.lower() not in TARGETS:
        raise PermissionError("The resource is outside the exact demonstration target allowlist.")
    return value.lower()


def canonical_uuid(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("A canonical UUID is required.")
    return value


@dataclass(frozen=True)
class CaseProof:
    episode_id: str
    target_id: str
    sys_id: str
    record_number: str
    audited: bool

    def validate(self) -> None:
        canonical_uuid(self.episode_id)
        normalized_target(self.target_id)
        if not re.fullmatch(r"[0-9a-f]{32}", self.sys_id):
            raise ValueError("An immutable ServiceNow sys_id is required.")
        if not re.fullmatch(r"(INC|TASK|CHG|PRB)[0-9]+", self.record_number):
            raise ValueError("A supported ServiceNow record number is required.")
        if self.audited is not True:
            raise PermissionError("The authenticated integration must confirm the case audit before repair.")


@dataclass(frozen=True)
class Repair:
    request_id: str
    action: str
    case: CaseProof

    def entity(self) -> dict:
        self.case.validate()
        canonical_uuid(self.request_id)
        target = normalized_target(self.case.target_id)
        if self.action not in ALLOWED_ACTIONS[TARGETS[target]]:
            raise PermissionError("This action does not belong to the target's role.")
        fingerprint = hashlib.sha256(json.dumps({
            "episode": self.case.episode_id, "target": target, "action": self.action,
            "case": self.case.sys_id,
        }, sort_keys=True).encode()).hexdigest()
        return {
            "PartitionKey": hashlib.sha256(target.encode()).hexdigest(),
            "RowKey": self.request_id,
            "episode_id": self.case.episode_id,
            "target_id": target,
            "action": self.action,
            "case_sys_id": self.case.sys_id,
            "case_number": self.case.record_number,
            "fingerprint": fingerprint,
            "status": "reserved",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class OperationLedger:
    def __init__(self, table: Table) -> None:
        self.table = table

    def reserve(self, repair: Repair, *, mode: str) -> tuple[dict, bool]:
        if mode != "Autonomous":
            raise PermissionError("Review/ReadOnly paths cannot reserve execution.")
        entity = repair.entity()
        lock = {"PartitionKey": entity["PartitionKey"], "RowKey": "target-lock", "request_id": repair.request_id}
        budget = {
            "PartitionKey": entity["PartitionKey"], "RowKey": f"budget-{repair.case.episode_id}",
            "request_id": repair.request_id,
        }
        try:
            self.table.create_batch([entity, lock, budget])
        except ConflictError:
            try:
                existing, _ = self.table.get(entity["PartitionKey"], entity["RowKey"])
            except KeyError as error:
                raise ConflictError("Another operation owns the target or this episode's repair budget is spent.") from error
            if existing["fingerprint"] != entity["fingerprint"]:
                raise ConflictError("An idempotency key cannot be reused for another operation.")
            return existing, False
        return entity, True

    def submitted(self, partition: str, request_id: str, external_operation_id: str) -> dict:
        if not external_operation_id or len(external_operation_id) > 2048:
            raise ValueError("Persist the actual Azure operation/job ID, not a fabricated success.")
        entity, etag = self.table.get(partition, canonical_uuid(request_id))
        if entity["status"] != "reserved":
            raise ConflictError("Only a reserved operation can record its first submission.")
        entity.update({
            "status": "submitted", "external_operation_id": external_operation_id,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })
        self.table.replace(entity, etag)
        return entity

    def uncertain_submission(self, partition: str, request_id: str, error: str) -> None:
        entity, etag = self.table.get(partition, canonical_uuid(request_id))
        if entity["status"] != "reserved" or not error:
            raise ConflictError("A submission ambiguity must be recorded against its reserved operation.")
        entity.update({"status": "submission_unknown", "error": error})
        self.table.replace(entity, etag)
        # The lock and spent budget deliberately survive an ambiguous Azure submission.

    def finish(
        self, partition: str, request_id: str, *, status: str,
        evidence_operation_id: str, observed_at: datetime, recovery_owner: str,
        evidence_uri: str, verification_checks: dict[str, bool],
        failure_reason: str | None = None,
    ) -> dict:
        if status not in TERMINAL or not evidence_uri or observed_at.tzinfo is None:
            raise ValueError("Terminal status requires timestamped authoritative evidence.")
        entity, etag = self.table.get(partition, canonical_uuid(request_id))
        if entity["status"] != "submitted":
            raise ConflictError("An unsubmitted or ambiguous operation cannot be declared recovered.")
        if evidence_operation_id != entity["external_operation_id"]:
            raise ValueError("Evidence belongs to a different Azure operation.")
        if observed_at <= datetime.fromisoformat(entity["submitted_at"]):
            raise ValueError("Recovery evidence must be fresher than action submission.")
        if recovery_owner not in {"agent", "watchdog", "manual"}:
            raise ValueError("Recovery ownership must be explicit.")
        if status == "verified" and recovery_owner != "agent":
            raise ValueError("Watchdog/manual recovery cannot be claimed as autonomous agent recovery.")
        if status == "verified":
            required = VERIFICATION_CHECKS[entity["action"]]
            if set(verification_checks) != required or any(verification_checks.get(check) is not True for check in required):
                raise ValueError("Fresh scenario-specific recovery checks have not all passed.")
        elif not failure_reason:
            raise ValueError("Failed/escalated operations require an explicit reason.")
        evidence = urlparse(evidence_uri)
        if (
            evidence.scheme != "https" or evidence.netloc != EVIDENCE_HOST
            or not re.fullmatch(r"/evidence/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.json", evidence.path)
            or evidence.query or evidence.fragment
        ):
            raise ValueError("Evidence must reference the dedicated private container without credentials or SAS.")
        lock, lock_etag = self.table.get(partition, "target-lock")
        if lock["request_id"] != request_id:
            raise ConflictError("The operation no longer owns the target lock.")
        entity.update({
            "status": status, "observed_at": observed_at.isoformat(),
            "recovery_owner": recovery_owner, "evidence_uri": evidence_uri,
            "verification_checks": json.dumps(verification_checks, sort_keys=True),
        })
        if failure_reason is not None:
            entity["failure_reason"] = failure_reason
        self.table.finish(entity, etag, lock, lock_etag)
        return entity


class AzureTable:
    """Use the pre-created Operations table; credentials and account keys are never stored."""

    def __init__(self, endpoint: str, managed_identity_client_id: str) -> None:
        from azure.data.tables import TableClient
        from azure.identity import ManagedIdentityCredential

        if not re.fullmatch(r"https://stasre[a-f0-9]{10}\.table\.core\.windows\.net/?", endpoint):
            raise ValueError("Only the dedicated operational storage endpoint is accepted.")
        self.client = TableClient(
            endpoint=endpoint, table_name="Operations",
            credential=ManagedIdentityCredential(client_id=managed_identity_client_id),
        )

    def get(self, partition: str, key: str) -> tuple[dict, str]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = self.client.get_entity(partition_key=partition, row_key=key)
        except ResourceNotFoundError as error:
            raise KeyError(key) from error
        return dict(entity), entity.metadata["etag"]

    def create_batch(self, entities: list[dict]) -> None:
        from azure.data.tables import TableTransactionError

        try:
            self.client.submit_transaction([("create", entity) for entity in entities])
        except TableTransactionError as error:
            if error.status_code != 409:
                raise
            raise ConflictError("An operation, target lock or episode budget already exists.") from error

    def replace(self, entity: dict, etag: str) -> None:
        from azure.core import MatchConditions
        from azure.data.tables import UpdateMode

        self.client.update_entity(
            entity, mode=UpdateMode.REPLACE, etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )

    def finish(self, entity: dict, etag: str, lock: dict, lock_etag: str) -> None:
        from azure.core import MatchConditions
        from azure.data.tables import UpdateMode

        self.client.submit_transaction([
            ("update", entity, {"mode": UpdateMode.REPLACE, "etag": etag, "match_condition": MatchConditions.IfNotModified}),
            ("delete", lock, {"etag": lock_etag, "match_condition": MatchConditions.IfNotModified}),
        ])
