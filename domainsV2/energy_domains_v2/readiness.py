"""Prove a session's immutable warmup baseline is queryable in Azure Logs.

Baseline queries use only EnergyObservations_CL; the fixed access diagnostic
returns only audit visibility, never audit records. HTTP success or ingestion
acceptance alone is not baseline readiness.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import uuid

from azure.core import PipelineClient
from azure.core.exceptions import (
    ClientAuthenticationError, DecodeError, HttpResponseError,
    ServiceRequestError, ServiceResponseError,
)
from azure.core.pipeline.policies import BearerTokenCredentialPolicy, HeadersPolicy, UserAgentPolicy
from azure.core.rest import HttpRequest

from .contracts import SURFACES
from .transport import _BoundedRetryPolicy, _credential, _hosted


QUERY_ENDPOINT = "https://api.loganalytics.azure.com"
QUERY_TOKEN_SCOPE = "https://api.loganalytics.io/.default"
WARMUP_SECONDS = 600
MIN_DISTINCT_TIMESTAMPS = 9
MIN_BASELINE_SPAN_SECONDS = 480
MAX_RESPONSE_BYTES = 64_000
MAX_CAPTURE_FILES = 4096
MAX_CAPTURE_FILE_BYTES = 202_000
MAX_CAPTURE_SCAN_BYTES = 64 * 1024 * 1024
MAX_CAPTURE_EVENTS = 9000
_COLUMNS = {
    "Surface": "string",
    "SampleCount": "long",
    "DistinctObservedAt": "long",
    "FirstObservedAt": "datetime",
    "LastObservedAt": "datetime",
    "LastIngestedAt": "datetime",
}


class ReadinessError(Exception):
    """Safe to expose and persist; never includes a server response or token."""

    def __init__(self, code):
        self.code = code if isinstance(code, str) and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{0,63}", code) else "ReadinessFailed"
        super().__init__(self.code)


def _timestamp(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("An aware timestamp is required")
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise ReadinessError("InvalidReadinessTime") from None


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _uuid(value):
    try:
        if not isinstance(value, str):
            raise ValueError("A UUID string is required")
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ReadinessError("InvalidReadinessId") from None


def _inputs(session_id, started_at, warmup_until, surfaces):
    session_id = _uuid(session_id)
    start, end = _timestamp(started_at), _timestamp(warmup_until)
    if (end - start).total_seconds() != WARMUP_SECONDS:
        raise ReadinessError("InvalidWarmupWindow")
    if not isinstance(surfaces, (list, tuple)) or not 1 <= len(surfaces) <= len(SURFACES):
        raise ReadinessError("InvalidReadinessSurfaces")
    if any(not isinstance(surface, str) or surface not in SURFACES for surface in surfaces):
        raise ReadinessError("InvalidReadinessSurfaces")
    if len(set(surfaces)) != len(surfaces):
        raise ReadinessError("InvalidReadinessSurfaces")
    return session_id, start, end, list(surfaces)


def _empty_surface():
    return {
        "ready": False, "sample_count": 0, "distinct_observed_at": 0,
        "first_observed_at": None, "last_observed_at": None, "last_ingested_at": None,
        "span_seconds": 0.0, "age_seconds": None, "baseline_end_gap_seconds": None,
    }


def _result(now, surfaces, status="pending"):
    return {
        "ready": False, "status": status, "checked_at": _iso(now),
        "surfaces": {surface: _empty_surface() for surface in surfaces},
    }


def _baseline_query(session_id, start, end, surfaces):
    # Every interpolated value is a normalized UUID/time or a surface allowlist member.
    selected = ", ".join(json.dumps(surface) for surface in surfaces)
    return f"""EnergyObservations_CL
| where TimeGenerated >= datetime({_iso(start)}) and TimeGenerated <= datetime({_iso(end)})
| where RunId == "{session_id}" and Surface in ({selected})
| where IsSynthetic == true and OperationalUse == "prohibited"
| where ObservedAt >= datetime({_iso(start)}) and ObservedAt <= datetime({_iso(end)})
| where isnotempty(EventId)
| project Surface, EventId, ObservedAt, IngestedAt=ingestion_time()
| summarize arg_max(IngestedAt, ObservedAt) by Surface, EventId
| summarize Samples=count(), LatestIngestedAt=max(IngestedAt) by Surface, ObservedAt
| summarize SampleCount=sum(Samples), DistinctObservedAt=count(),
    FirstObservedAt=min(ObservedAt), LastObservedAt=max(ObservedAt),
    LastIngestedAt=max(LatestIngestedAt) by Surface
| project Surface, SampleCount, DistinctObservedAt, FirstObservedAt, LastObservedAt, LastIngestedAt
| take {len(SURFACES)}"""


def _parse_response(body, start, end, now, surfaces, *, local=False):
    if not isinstance(body, dict):
        raise ReadinessError("MalformedQueryResponse")
    if "error" in body:
        # Log Analytics can return HTTP 200 alongside PartialError.
        raise ReadinessError("PartialQueryFailure")
    tables = body.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        raise ReadinessError("MalformedQueryResponse")
    table = tables[0]
    columns, rows = table.get("columns"), table.get("rows")
    if table.get("name") != "PrimaryResult" or not isinstance(columns, list):
        raise ReadinessError("MalformedQueryResponse")
    if len(columns) != len(_COLUMNS) or any(not isinstance(column, dict) for column in columns):
        raise ReadinessError("MalformedQueryResponse")
    names = [column.get("name") for column in columns]
    if any(not isinstance(name, str) for name in names) or len(set(names)) != len(_COLUMNS):
        raise ReadinessError("MalformedQueryResponse")
    if {column["name"]: column.get("type") for column in columns} != _COLUMNS:
        raise ReadinessError("MalformedQueryResponse")
    if not isinstance(rows, list) or len(rows) > len(surfaces):
        raise ReadinessError("MalformedQueryResponse")

    result = _result(now, surfaces)
    seen = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise ReadinessError("MalformedQueryResponse")
        item = dict(zip(names, row))
        surface = item["Surface"]
        if not isinstance(surface, str) or surface not in surfaces or surface in seen:
            raise ReadinessError("UnexpectedQuerySurface")
        seen.add(surface)
        count, distinct = item["SampleCount"], item["DistinctObservedAt"]
        if type(count) is not int or type(distinct) is not int or not 1 <= distinct <= count <= 1000:
            raise ReadinessError("MalformedQueryEvidence")
        try:
            first, last = (_timestamp(item[name]) for name in ("FirstObservedAt", "LastObservedAt"))
            ingested = None if local else _timestamp(item["LastIngestedAt"])
        except ReadinessError:
            raise ReadinessError("MalformedQueryEvidence") from None
        if not start <= first <= last <= end or ingested is not None and (
                ingested < first or (ingested - now).total_seconds() > 60):
            raise ReadinessError("InvalidQueryWindow")
        span = (last - first).total_seconds()
        if (distinct == 1) != (span == 0):
            raise ReadinessError("MalformedQueryEvidence")
        result["surfaces"][surface] = {
            "ready": distinct >= MIN_DISTINCT_TIMESTAMPS and span >= MIN_BASELINE_SPAN_SECONDS,
            "sample_count": count, "distinct_observed_at": distinct,
            "first_observed_at": _iso(first), "last_observed_at": _iso(last),
            "last_ingested_at": _iso(ingested) if ingested is not None else None, "span_seconds": span,
            "age_seconds": max(0.0, (now - last).total_seconds()),
            "baseline_end_gap_seconds": (end - last).total_seconds(),
        }
    result["ready"] = all(item["ready"] for item in result["surfaces"].values())
    result["status"] = "local" if local else "ready" if result["ready"] else "pending"
    return result


def _http_failure(status, prefix="QueryHttp"):
    code = f"{prefix}{status}" if type(status) is int and 100 <= status <= 599 else f"{prefix}Failure"
    return ReadinessError(code)


def _response_json(response):
    try:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ReadinessError("QueryResponseTooLarge")
        return response.json()
    except (DecodeError, ValueError, TypeError):
        raise ReadinessError("MalformedQueryResponse") from None


class AzureReadinessChecker:
    """A bounded, single-table workspace-context query using the app identity.

    Reuses azure-core already required by transport; azure-monitor-query is not
    required. The public endpoint and token audience are fixed, not user input.
    """

    def __init__(self, workspace_id, credential=None, client=None, clock=None):
        self.workspace_id = _uuid(workspace_id)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.client = client if client is not None else PipelineClient(
            base_url=QUERY_ENDPOINT,
            policies=[
                UserAgentPolicy(base_user_agent="energy-domains-v2-readiness"),
                HeadersPolicy(),
                _BoundedRetryPolicy(
                    retry_total=1, retry_connect=1, retry_read=1, retry_status=1,
                    retry_backoff_factor=0.5, retry_backoff_max=1, timeout=30,
                ),
                BearerTokenCredentialPolicy(_credential(credential), QUERY_TOKEN_SCOPE),
            ],
            connection_timeout=5, read_timeout=20,
        )

    def _send(self, query, timespan):
        request = HttpRequest(
            "POST", f"{QUERY_ENDPOINT}/v1/workspaces/{self.workspace_id}/query",
            headers={"Content-Type": "application/json", "Accept": "application/json", "Prefer": "wait=15"},
            json={"query": query, "timespan": timespan},
        )
        try:
            return self.client.send_request(request, connection_timeout=5, read_timeout=20)
        except ClientAuthenticationError:
            raise ReadinessError("QueryAuthenticationFailed") from None
        except (ServiceRequestError, ServiceResponseError):
            raise ReadinessError("QueryTransportFailed") from None
        except HttpResponseError as error:
            raise _http_failure(error.status_code) from None
        except (DecodeError, ValueError, TypeError):
            raise ReadinessError("MalformedQueryResponse") from None

    def check(self, session_id, started_at, warmup_until, surfaces):
        session_id, start, end, selected = _inputs(session_id, started_at, warmup_until, surfaces)
        now = _timestamp(self.clock())
        if now < end:
            return _result(now, selected)
        response = self._send(_baseline_query(session_id, start, end, selected),
                              f"{_iso(start)}/{_iso(end)}")
        if response.status_code != 200:
            raise _http_failure(response.status_code)
        return _parse_response(_response_json(response), start, end, now, selected)

    def access_probe(self):
        """Fixed audit-visibility diagnostic for the previous completed UTC hour.

        A complete boolean aggregate returns a sanitized visibility code, never
        verified denial. A privileged operator must run the identical absolute
        window control before/after the app-MI probe to interpret invisible rows.
        Only explicit 403 InsufficientAccessError verifies denial automatically.
        Baseline check() separately proves observation data is queryable.
        """
        result = {
            "verified": False, "observations_allowed": False, "audit_denied": False,
            "code": "AccessProbeUnverified",
        }
        try:
            response = self._send(
                "EnergyObservations_CL | where TimeGenerated >= ago(5m) | take 0 | project EventId",
                "PT5M",
            )
            if response.status_code != 200:
                raise _http_failure(response.status_code, "ObservationProbeHttp")
            body = _response_json(response)
            if not isinstance(body, dict) or "error" in body:
                raise ReadinessError("ObservationProbeFailed")
            tables = body.get("tables")
            if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
                raise ReadinessError("MalformedProbeResponse")
            table = tables[0]
            if (table.get("name") != "PrimaryResult" or table.get("rows") != []
                    or table.get("columns") != [{"name": "EventId", "type": "string"}]):
                raise ReadinessError("MalformedProbeResponse")
            result["observations_allowed"] = True
            audit_end = _timestamp(self.clock()).replace(minute=0, second=0, microsecond=0)
            try:
                audit_start = audit_end - timedelta(hours=1)
            except OverflowError:
                raise ReadinessError("InvalidAuditWindow") from None
            response = self._send(
                "EnergyInjectionAudit_CL"
                f" | where TimeGenerated >= datetime({_iso(audit_start)})"
                f" and TimeGenerated < datetime({_iso(audit_end)})"
                " | summarize HasAny=count() > 0",
                f"{_iso(audit_start)}/{_iso(audit_end)}",
            )
            if response.status_code == 200:
                body = _response_json(response)
                if not isinstance(body, dict):
                    raise ReadinessError("MalformedAuditProbe")
                if "error" in body:
                    raise ReadinessError("AuditProbePartial")
                tables = body.get("tables")
                if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
                    raise ReadinessError("MalformedAuditProbe")
                table = tables[0]
                rows = table.get("rows")
                if (table.get("name") != "PrimaryResult"
                        or table.get("columns") != [{"name": "HasAny", "type": "bool"}]
                        or not isinstance(rows, list) or len(rows) != 1
                        or not isinstance(rows[0], list) or len(rows[0]) != 1
                        or type(rows[0][0]) is not bool):
                    raise ReadinessError("MalformedAuditProbe")
                if _timestamp(self.clock()).replace(minute=0, second=0, microsecond=0) != audit_end:
                    raise ReadinessError("AuditWindowChanged")
                result["code"] = "AuditRowsVisible" if rows[0][0] else "AuditNoVisibleRows"
                return result
            if response.status_code != 403:
                raise _http_failure(response.status_code, "AuditProbeHttp")
            body = _response_json(response)
            error = body.get("error") if isinstance(body, dict) else None
            if not isinstance(error, dict) or error.get("code") != "InsufficientAccessError":
                raise ReadinessError("AuditDenialUnverified")
            result.update(verified=True, audit_denied=True, code="AccessVerified")
        except ReadinessError as error:
            result["code"] = error.code
        return result


class LocalReadinessChecker:
    """Bounded evidence from the actual capture directory, never Azure evidence.

    A local baseline may permit local demonstrations only. The runtime must not
    interpret status=local or local capture timestamps as Azure queryability.
    """

    def __init__(self, directory=None, clock=None):
        if _hosted():
            raise ReadinessError("HostedLocalReadinessProhibited")
        self.directory = Path(directory) if directory is not None else None
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, session_id, started_at, warmup_until, surfaces):
        session_id, start, end, selected = _inputs(session_id, started_at, warmup_until, surfaces)
        now = _timestamp(self.clock())
        if now < end:
            return _result(now, selected, "local")
        if self.directory is None:
            raise ReadinessError("LocalCaptureNotConfigured")
        events, captured, scanned_bytes = {}, {}, 0
        try:
            if not self.directory.is_dir():
                raise ReadinessError("LocalCaptureUnavailable")
            for index, path in enumerate(self.directory.glob("*.json")):
                if index >= MAX_CAPTURE_FILES:
                    raise ReadinessError("LocalCaptureScanLimit")
                if path.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}\.json", path.name):
                    raise ReadinessError("UnsafeLocalCaptureFile")
                with path.open("rb") as handle:
                    data = handle.read(MAX_CAPTURE_FILE_BYTES + 1)
                scanned_bytes += len(data)
                if len(data) > MAX_CAPTURE_FILE_BYTES or scanned_bytes > MAX_CAPTURE_SCAN_BYTES:
                    raise ReadinessError("LocalCaptureScanLimit")
                try:
                    envelope = json.loads(data)
                    # Reject NaN/Infinity and unsupported JSON before considering any evidence.
                    json.dumps(envelope, allow_nan=False)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    raise ReadinessError("InvalidLocalCapture") from None
                if not isinstance(envelope, dict) or set(envelope) != {"delivery_mode", "table", "records"}:
                    raise ReadinessError("InvalidLocalCapture")
                if envelope["delivery_mode"] != "local_capture" or envelope["table"] not in (
                        "EnergyObservations_CL", "EnergyInjectionAudit_CL"):
                    raise ReadinessError("InvalidLocalCapture")
                records = envelope["records"]
                if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                    raise ReadinessError("InvalidLocalCapture")
                if envelope["table"] != "EnergyObservations_CL":
                    continue
                captured_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                for row in records:
                    surface = row.get("Surface")
                    if row.get("RunId") != session_id or surface not in selected:
                        continue
                    if row.get("IsSynthetic") is not True or row.get("OperationalUse") != "prohibited":
                        continue
                    observed, generated = _timestamp(row.get("ObservedAt")), _timestamp(row.get("TimeGenerated"))
                    if not start <= observed <= end or not start <= generated <= end:
                        continue
                    event_id = row.get("EventId")
                    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
                        raise ReadinessError("InvalidLocalCaptureEvent")
                    key = surface, event_id
                    if key in events and events[key] != observed:
                        raise ReadinessError("ConflictingLocalCaptureEvent")
                    events[key] = observed
                    if len(events) > MAX_CAPTURE_EVENTS:
                        raise ReadinessError("LocalCaptureScanLimit")
                    captured[surface] = max(captured.get(surface, captured_at), captured_at)
        except OSError:
            raise ReadinessError("LocalCaptureUnavailable") from None
        rows = []
        for surface in selected:
            values = [at for (name, _event), at in events.items() if name == surface]
            if values:
                rows.append([surface, len(values), len(set(values)), _iso(min(values)), _iso(max(values)), None])
        body = {"tables": [{
            "name": "PrimaryResult",
            "columns": [{"name": name, "type": kind} for name, kind in _COLUMNS.items()],
            "rows": rows,
        }]}
        result = _parse_response(body, start, end, now, selected, local=True)
        for surface, evidence in result["surfaces"].items():
            evidence["last_captured_at"] = _iso(captured[surface]) if surface in captured else None
        return result

    def access_probe(self):
        return {
            "verified": False, "observations_allowed": False, "audit_denied": False,
            "code": "LocalOnly",
        }


def configured_checker():
    """Match the app's explicit V2 delivery mode without performing a query."""
    hosted = _hosted()
    mode = os.getenv("V2_MODE", "azure" if hosted else "local")
    if hosted and mode != "azure":
        raise ReadinessError("HostedLocalReadinessProhibited")
    if mode == "azure":
        workspace_id = os.getenv("V2_LOG_ANALYTICS_WORKSPACE_ID")
        if not workspace_id:
            raise ReadinessError("MissingWorkspaceId")
        return AzureReadinessChecker(workspace_id)
    if mode == "local":
        root = Path(os.getenv("V2_LOCAL_STATE", str(Path.cwd() / "domainsV2" / ".local")))
        return LocalReadinessChecker(root / "capture")
    raise ReadinessError("InvalidReadinessMode")
