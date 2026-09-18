"""Classify known failures without exposing stored exception text or model output."""
from dataclasses import asdict
import json
import re

from ..consumer.responses import ProviderFailureDetails
from ..consumer.schema_diagnostics import KEYWORDS as SCHEMA_KEYWORDS, from_stored_message, schema_fields
from ..runtime import AGENTS


PORTFOLIO_REASONS = (
    "completed draft requires citations",
    "insufficient evidence requires explicit gaps",
    "nested evidence must appear in citations",
    "citation is not in trusted evidence",
    "document revision must match its citation",
)
ERROR_CODES = {
    "PortfolioError", "AgentFailure", "ValidationError", "ContractError",
    "RateLimitError", "BudgetExceeded", "IntegrityConflict", "LostLease",
}
SCHEMA_HEADER = re.compile(
    r"\n\nFailed validating '([A-Za-z]+)' in schema"
    r"((?:\[(?:'[A-Za-z_][A-Za-z_0-9]{0,63}'|[0-9]{1,5})\]){0,24}):\n"
)


def _schema_failure(message):
    if not isinstance(message, str) or not message.startswith("ValidationError: ") or len(message) > 2000:
        return None
    try:
        # ConsumerWorker stores a JSON-escaped, bounded exception string.
        decoded = json.loads('"' + message + '"')
    except json.JSONDecodeError:
        return None
    matches = list(SCHEMA_HEADER.finditer(decoded))
    if len(matches) != 1 or matches[0][1] not in SCHEMA_KEYWORDS:
        return None
    match = matches[0]
    parts = re.findall(r"\['([A-Za-z_][A-Za-z_0-9]{0,63})'\]|\[([0-9]{1,5})\]", match[2])
    result = {
        "status": "classified", "error_code": "ValidationError",
        "reason": "schema_validation_failed",
        "diagnostic_basis": "retained_jsonschema_exception_structure",
        "schema_keyword": match[1],
        "schema_path": [
            name if name in schema_fields() else "unclassified" if name else int(index)
            for name, index in parts
        ],
    }
    missing = re.match(r"ValidationError: '([A-Za-z_][A-Za-z_0-9]{0,63})' is a required property\n\n", decoded)
    if match[1] == "required" and missing:
        result["missing_property"] = missing[1] if missing[1] in schema_fields() else "unclassified"
    return result


def failure_diagnostic(job: dict) -> dict:
    if job.get("status") != "failed":
        return {"status": "not_failed"}
    code = job.get("error_code")
    if code == "ValidationError":
        structural = from_stored_message(job.get("error_message"))
        if structural is not None:
            return {"status": "classified", "error_code": code,
                    "reason": "schema_validation_failed",
                    "diagnostic_basis": "validation_boundary_metadata", **structural}
        details = _schema_failure(job.get("error_message"))
        if details is not None:
            return details
        message = job.get("error_message")
        if (type(message) is str and len(message) <= 2000
                and message.startswith("ValidationError: ")
                and message.endswith(" [diagnostic text truncated]")):
            return {"status": "unclassified_failure", "error_code": code,
                    "details_unavailable": "stored_diagnostic_truncated"}
    if code == "AgentFailure":
        message = job.get("error_message")
        details = ProviderFailureDetails.from_stored_message(message)
        if message == "AgentFailure: provider response did not complete successfully":
            details = ProviderFailureDetails(None, None, None, None)
        if details is not None:
            return {"status": "classified", "error_code": code,
                    "reason": "provider_response_failed", **asdict(details)}
    if code == "PortfolioError":
        for agent in AGENTS:
            for reason in PORTFOLIO_REASONS:
                if job.get("error_message") == f"PortfolioError: {agent}: {reason}":
                    return {"status": "classified", "error_code": code,
                            "agent": agent, "reason": reason}
    return {"status": "unclassified_failure",
            "error_code": code if type(code) is str and code in ERROR_CODES else "unclassified"}
