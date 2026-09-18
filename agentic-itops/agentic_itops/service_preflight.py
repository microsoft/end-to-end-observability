"""Bootstrap-caller-only, worker-identity count proof; never inference input."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID

import httpx

from .access_probe import LogsCountClient, query_contract, verify_managed_identity_access
from .consumer.contracts import ContractError, strict_json, utc
from .hosting import _required
from .review.contracts import ReviewError
from .service_auth import ServiceAuthenticator
from .service_runtime import _document, _identity


WITNESS_FIELDS = {
    "contract_sha256", "observed_at", "observation_rows", "audit_rows",
    "witness_kind", "inference_input",
}


def validate_probe_contract(document):
    if not isinstance(document, dict) or set(document) != {
            "workspace_id", "run_id", "start", "end", "queries", "sha256"}:
        raise ContractError("explicit server-owned count-probe contract required")
    expected = query_contract(document["workspace_id"], document["run_id"],
                              document["start"], document["end"])
    if document != expected:
        raise ContractError("deployed source probe queries/hash differ from the fixed contract")
    if UUID(document["workspace_id"]).int == 0:
        raise ContractError("source preflight workspace cannot be a placeholder")
    if utc(document["end"]) - utc(document["start"]) > timedelta(days=3):
        raise ContractError("source isolation count window is bounded to three days")
    return expected


class WorkerAccessPreflight:
    def __init__(self, auth, bootstrap_oid, worker_client_id, contract, query, *, clock=None):
        self.auth = auth
        self.bootstrap_oid, self.worker_client_id = str(UUID(bootstrap_oid)), str(UUID(worker_client_id))
        if UUID(self.bootstrap_oid).int == 0 or UUID(self.worker_client_id).int == 0:
            raise ContractError("preflight caller and worker identity must be explicit nonzero UUIDs")
        self.contract = validate_probe_contract(contract)
        self.query = query
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def verify(self, headers, body):
        caller = self.auth.authenticate(headers, "Worker.Invoke")
        if caller != self.bootstrap_oid:
            raise ReviewError("source preflight is restricted to the approved bootstrap caller", 403)
        if (not isinstance(body, dict) or set(body) != {"witness"}
                or not isinstance(body["witness"], dict) or set(body["witness"]) != WITNESS_FIELDS):
            raise ContractError("only the trusted operator's count-only witness is accepted")
        proof = verify_managed_identity_access(
            self.contract, body["witness"], self.query, now=self.clock())
        return {
            **proof, "status": "verified", "worker_client_id": self.worker_client_id,
            "caller_oid": caller, "probe_kind": "worker_managed_identity_count_only",
            "inference_input": False, "execution_allowed": False,
        }


def create_worker_access_preflight(environment=None, *, root=None):
    """No registration, models, Search, Blob or SQL initialization is required."""
    environment = os.environ if environment is None else environment
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    contract = validate_probe_contract(_document(environment, "ITOPS_ACCESS_PROBE_CONTRACT_PATH", root))
    auth = ServiceAuthenticator(
        _required(environment, "ENTRA_TENANT_ID"), _required(environment, "ENTRA_AUDIENCE"),
        strict_json(_required(environment, "ITOPS_SERVICE_CALLERS_JSON")))
    worker_client_id = _required(environment, "AZURE_CLIENT_ID")
    credential = _identity(environment)
    client = httpx.Client(timeout=60, follow_redirects=False)
    count_client = LogsCountClient(credential, client)
    return WorkerAccessPreflight(
        auth, _required(environment, "ITOPS_PREFLIGHT_BOOTSTRAP_OID"), worker_client_id,
        contract, count_client.query)
