"""Count-only source isolation proof, separate from inference input."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable
from uuid import UUID

from .consumer.contracts import ContractError, iso, utc


class AccessProofError(RuntimeError):
    pass


def _validate_contract(contract: dict) -> None:
    if not isinstance(contract, dict) or set(contract) != {"workspace_id", "run_id", "start", "end", "queries", "sha256"}:
        raise AccessProofError("source probe contract is incomplete")
    expected = query_contract(contract["workspace_id"], contract["run_id"], contract["start"], contract["end"])
    if contract != expected:
        raise AccessProofError("source probe queries or digest differ from their declared scope")


def query_contract(workspace_id: str, run_id: str, start: str, end: str) -> dict:
    workspace_id = str(UUID(workspace_id))
    if (not isinstance(run_id, str) or not run_id or len(run_id) > 180
            or any(ord(character) < 32 for character in run_id)):
        raise ContractError("explicit bounded source RunId required")
    if not utc(start) < utc(end):
        raise ContractError("source probe requires a positive absolute UTC window")
    start, end = iso(start), iso(end)
    window = f"| where TimeGenerated >= datetime({start}) and TimeGenerated < datetime({end})"
    queries = {
        "observations": (
            "EnergyObservations_CL\n" + window +
            "\n| where RunId == " + json.dumps(run_id) + "\n| summarize rows=count()"
        ),
        "audit": "EnergyInjectionAudit_CL\n" + window + "\n| summarize rows=count()",
    }
    document = {"workspace_id": workspace_id, "run_id": run_id, "start": start, "end": end, "queries": queries}
    document["sha256"] = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    return document


def count_result(status: int, value: dict) -> int:
    if status != 200 or not isinstance(value, dict) or value.get("error"):
        raise AccessProofError("source probe did not return a complete successful query")
    try:
        table, = value["tables"]
        if table["columns"] != [{"name": "rows", "type": "long"}]:
            raise AccessProofError("source probe returned an unexpected schema")
        row, = table["rows"]
        count, = row
    except (KeyError, TypeError, ValueError) as exc:
        raise AccessProofError("source probe returned an incomplete count") from exc
    if type(count) is not int or count < 0:
        raise AccessProofError("source probe returned an invalid count")
    return count


def operator_witness(
    contract: dict, query: Callable[[str, str], tuple[int, dict]], *, now: datetime | None = None,
) -> dict:
    _validate_contract(contract)
    counts = {name: count_result(*query(contract["workspace_id"], text))
              for name, text in contract["queries"].items()}
    if counts["observations"] == 0 or counts["audit"] == 0:
        raise AccessProofError("both observation and independent audit witness counts must be nonzero")
    return {
        "contract_sha256": contract["sha256"], "observed_at": iso(now or datetime.now(timezone.utc)),
        "observation_rows": counts["observations"], "audit_rows": counts["audit"],
        "witness_kind": "operator_count_only", "inference_input": False,
    }


def verify_managed_identity_access(
    contract: dict, witness: dict, query: Callable[[str, str], tuple[int, dict]],
    *, now: datetime | None = None,
) -> dict:
    _validate_contract(contract)
    now = now or datetime.now(timezone.utc)
    if (witness.get("contract_sha256") != contract["sha256"]
            or witness.get("witness_kind") != "operator_count_only"
            or witness.get("inference_input") is not False):
        raise AccessProofError("source isolation requires a matching trusted operator witness")
    age = (utc(now) - utc(witness["observed_at"])).total_seconds()
    if not 0 <= age <= 300:
        raise AccessProofError("refresh the independent source witness within five minutes of the MI probe")
    if any(type(witness.get(key)) is not int or witness[key] <= 0 for key in ("observation_rows", "audit_rows")):
        raise AccessProofError("source isolation requires known-nonempty independent counts")
    observation_rows = count_result(*query(contract["workspace_id"], contract["queries"]["observations"]))
    if observation_rows != witness["observation_rows"]:
        raise AccessProofError("MI observation count differs from the independent source witness")
    status, result = query(contract["workspace_id"], contract["queries"]["audit"])
    if status == 200:
        if count_result(status, result) != 0:
            raise AccessProofError("MI can read the forbidden audit table")
        denied_by = "filtered_to_zero_against_nonempty_witness"
    elif (status == 403 and isinstance(result, dict)
          and isinstance(result.get("error"), dict)
          and result["error"].get("code") == "InsufficientAccessError"):
        denied_by = "explicit_insufficient_access"
    else:
        raise AccessProofError("audit probe was indeterminate, not a proven authorization denial")
    return {
        "contract_sha256": contract["sha256"], "verified_at": iso(now),
        "observation_rows": observation_rows, "audit_access": "denied", "denial_evidence": denied_by,
        "independent_audit_rows": witness["audit_rows"], "inference_input": False,
        "execution_allowed": False,
    }


class LogsCountClient:
    """The caller explicitly supplies operator or managed-identity credentials."""

    def __init__(self, credential, client):
        self.credential, self.client = credential, client

    def query(self, workspace_id: str, text: str) -> tuple[int, dict]:
        workspace_id = str(UUID(workspace_id))
        token = self.credential.get_token("https://api.loganalytics.io/.default").token
        response = self.client.post(
            f"https://api.loganalytics.azure.com/v1/workspaces/{workspace_id}/query",
            headers={"Authorization": "Bearer " + token},
            json={"query": text}, timeout=60,
        )
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise AccessProofError("source query returned an unreadable response") from exc
