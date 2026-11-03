"""Closed Compute patch-summary evidence protocol (standard library only).

lastModifiedTime is a raw provider field, NOT a guaranteed package completion
time. Only the latest summary is available; polls cannot recover missed runs.
"""

from __future__ import annotations

import errno
import hashlib
import http.client
import json
import re
import socket
import ssl
from datetime import datetime, timezone
from email.utils import formatdate
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid5

from . import context as deployment

CTX = deployment.load()
TARGET = CTX.targets["patch"].resource_id
API_VERSION = "2024-07-01"
COMPUTE_URL = "https://management.azure.com" + TARGET + "/instanceView?api-version=" + API_VERSION
STORAGE = CTX.storage.blob_endpoint + "/evidence/"
ARCHIVE_PREFIX = "patch-provider/v1/sha256/"
SOURCE = "Microsoft.Compute/virtualMachines/instanceView"
MARKER = "ASRE_PATCH_FAILURE_V1 "
VERSION = "1.0"
SOURCE_LIMIT = 131072
SUMMARY_LIMIT = 24576
ARCHIVE_LIMIT = 32768
LOG_LIMIT = 2048
FRESH_SECONDS = 600
MAINTENANCE_RUN_ID_LIMIT = 2048
TERMINAL = {"Failed", "CompletedWithWarnings"}
COUNTS = {
    "installedPatchCount", "failedPatchCount", "pendingPatchCount",
    "excludedPatchCount", "notSelectedPatchCount",
}
SUMMARY_FIELDS = COUNTS | {
    "installationActivityId", "status", "lastModifiedTime", "startTime", "maintenanceWindowExceeded",
}
SNAPSHOT_FIELDS = {"version", "source", "api_version", "resource_id", "collection_time", "summary"}
EVENT_FIELDS = {
    "version", "resource_id", "installation_id", "status", "failed_patch_count",
    "provider_time", "collection_time", "digest",
}
PATCH_NAMESPACE = UUID("08b75cc3-ae84-5d90-877b-25f9b9518106")
ALERT_QUERY = (
    "ContainerAppConsoleLogs_CL "
    f"| where Log_s startswith_cs '{MARKER}' "
    f"| extend event_json=substring(Log_s, strlen('{MARKER}')) "
    "| extend patch_event=parse_json(event_json) "
    f"| where tostring(patch_event.version) == '{VERSION}' "
    f"| where tostring(patch_event.resource_id) == '{TARGET}' "
    "| extend installation_id=tostring(patch_event.installation_id), status=tostring(patch_event.status), "
    "failed_patch_count=toint(patch_event.failed_patch_count), provider_time=todatetime(patch_event.provider_time), "
    "digest=tostring(patch_event.digest) "
    "| where installation_id matches regex '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
    "| where digest matches regex '^[0-9a-f]{64}$' "
    "| where status in ('Failed', 'CompletedWithWarnings') and failed_patch_count > 0 "
    "| where provider_time > ago(10m) and provider_time <= now() "
    f"| project TimeGenerated=provider_time, resource_id='{TARGET}', observed_at=provider_time, "
    "installation_id, status, failed_patch_count, digest, event_json"
)


class ProviderError(ValueError):
    """Malformed, incomplete, untrusted or unavailable evidence, never no-failure."""


def retry_after(value):
    if value is None:
        return None, True
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value) and int(value) <= 30:
        return int(value), True
    return None, False


class ProviderReadError(ProviderError):
    def __init__(self, reason):
        if reason not in {"timeout", "transport", "throttled", "server-error", "auth", "rejected", "tls", "invalid"}:
            raise ProviderError("Invalid provider failure classification.")
        self.reason = reason
        super().__init__("The fixed provider/archive read failed.")


class TransientProviderError(ProviderReadError):
    def __init__(self, reason, retry_after=None, retry_after_valid=True):
        if (reason not in {"timeout", "transport", "throttled", "server-error"}
                or retry_after is not None and (type(retry_after) is not int or not 0 <= retry_after <= 30)
                or type(retry_after_valid) is not bool):
            raise ProviderError("Invalid transient provider classification.")
        self.retry_after, self.retry_after_valid = retry_after, retry_after_valid
        super().__init__(reason)


def failure_chain(error):
    """Outermost-first, bounded traversal; never formats remote exceptions."""
    pending, seen = [error], set()
    while pending and len(seen) < 16:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        for related in (cause, current.reason if isinstance(current, URLError) else None,
                        getattr(current, "inner_exception", None)):
            if isinstance(related, BaseException):
                pending.append(related)


def _named(error, names):
    return any(cls.__name__ in names for cls in type(error).__mro__)


def _retry_header(headers):
    if headers is None:
        return None, True
    if not callable(getattr(headers, "items", None)):
        return None, False
    result, found = (None, True), False
    for number, (name, value) in enumerate(headers.items()):
        if number >= 64 or not isinstance(name, str) or len(name) > 128:
            return None, False
        if name.lower() == "retry-after":
            if found or not isinstance(value, str):
                return None, False
            found, result = True, retry_after(value)
    return result


def classify_failure(error):
    """Shared positive network policy; None means no retry classification."""
    causes = list(failure_chain(error))
    statuses = []
    for cause in causes:
        if isinstance(cause, HTTPError):
            status, headers = cause.code, cause.headers
        elif _named(cause, {"HttpResponseError", "ClientAuthenticationError"}):
            status = getattr(cause, "status_code", None)
            headers = getattr(getattr(cause, "response", None), "headers", None)
        else:
            continue
        if isinstance(status, int) and not isinstance(status, bool):
            statuses.append((status, headers))
    # An explicit rejection cannot become a retry through another cause.
    if any(status in {401, 403} for status, _ in statuses):
        return "auth", False, None, True
    if any(isinstance(cause, ssl.SSLError) or _named(cause, {"SSLError", "CertificateError"}) for cause in causes):
        return "tls", False, None, True
    if isinstance(error, (PermissionError, FileNotFoundError, IsADirectoryError,
                          ValueError, TypeError, AttributeError, KeyError)) or _named(
                              error, {"InvalidURL", "MissingSchema", "NotConnected",
                                      "CannotSendRequest", "ImproperConnectionState", "LineTooLong"}):
        return None
    if statuses:
        status, headers = statuses[0]
        reason = ("timeout" if status == 408 else "throttled" if status == 429 else
                  "server-error" if 500 <= status <= 599 else "rejected")
        delay, valid = _retry_header(headers) if status in {408, 429} or 500 <= status <= 599 else (None, True)
        return reason, status in {408, 429, 500, 502, 503, 504}, delay, valid
    if any(isinstance(cause, TimeoutError) or _named(cause, {
            "ServiceRequestTimeoutError", "ServiceResponseTimeoutError", "Timeout", "ConnectTimeout",
            "ReadTimeout", "ConnectTimeoutError", "ReadTimeoutError"}) for cause in causes):
        return "timeout", True, None, True
    network_errnos = {errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED, errno.EPIPE,
                     errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN, errno.ENETRESET}
    for cause in causes:
        if isinstance(cause, socket.gaierror):
            if cause.errno == socket.EAI_AGAIN:
                return "transport", True, None, True
            continue
        if isinstance(cause, (PermissionError, FileNotFoundError, IsADirectoryError)):
            continue
        if (isinstance(cause, (ConnectionError, http.client.RemoteDisconnected,
                               http.client.IncompleteRead, http.client.BadStatusLine))
                or isinstance(cause, OSError) and cause.errno in network_errnos
                or _named(cause, {"ConnectionError", "NewConnectionError", "ProtocolError"})):
            return "transport", True, None, True
    return None


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise ProviderError("Patch evidence has unexpected or missing fields.")


def encode(value, limit=ARCHIVE_LIMIT):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise ProviderError("Patch evidence is not bounded JSON.") from None
    if len(raw) > limit:
        raise ProviderError("Patch evidence exceeds its byte bound.")
    return raw


def decode(raw, limit=SOURCE_LIMIT):
    def invalid_constant(_):
        raise ProviderError("Nonfinite patch JSON numbers are forbidden.")

    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ProviderError("Duplicate patch JSON fields are forbidden.")
            value[key] = child
        return value

    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ProviderError("Patch evidence exceeds its byte bound.")
    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError()
        encode(value, limit)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("Patch evidence is not a complete strict JSON object.") from None


def guid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ProviderError("A canonical nonzero installation activity GUID is required.") from None
    return value


def timestamp(value):
    # Compute can return seven fractional digits. Keep the original string in
    # evidence even though Python's comparison clock has microsecond precision.
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|\+00:00)", value,
    ):
        raise ProviderError("An exact UTC provider timestamp is required.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderError("The provider timestamp is invalid.") from None


def ticks(value):
    """Compare all seven provider fractional digits without re-dating evidence."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise ProviderError("The comparison clock must be UTC.")
        value = value.isoformat()
    parsed = timestamp(value)
    fraction = re.search(r"\.(\d+)", value)
    seconds = (parsed.replace(microsecond=0) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
    return int(seconds) * 10000000 + int(
        fraction[1].ljust(7, "0") if fraction else "0",
    )


def validate_summary(summary):
    fields(summary, SUMMARY_FIELDS, {"error", "maintenanceRunId"})
    encode(summary, SUMMARY_LIMIT)
    if "maintenanceRunId" in summary and (
        not isinstance(summary["maintenanceRunId"], str)
        or len(summary["maintenanceRunId"]) > MAINTENANCE_RUN_ID_LIMIT
    ):
        raise ProviderError("The optional maintenance run ID must be a bounded string.")
    guid(summary["installationActivityId"])
    modified, started = ticks(summary["lastModifiedTime"]), ticks(summary["startTime"])
    if started > modified or type(summary["maintenanceWindowExceeded"]) is not bool:
        raise ProviderError("Patch summary timing or maintenance status is invalid.")
    if not isinstance(summary["status"], str) or summary["status"] not in TERMINAL | {"Succeeded", "InProgress", "Unknown"}:
        raise ProviderError("The patch installation status is invalid.")
    if any(type(summary[name]) is not int or not 0 <= summary[name] <= 1000000 for name in COUNTS):
        raise ProviderError("Patch counts must be bounded nonnegative integers.")
    if "error" in summary:
        def error(value, depth=0):
            fields(value, {"code", "message"}, {"target", "details", "innererror"})
            if depth > 3 or any(not isinstance(value[k], str) or len(value[k]) > 4096
                                for k in ("code", "message") + (("target",) if "target" in value else ())):
                raise ProviderError("Patch provider errors exceed their shape bound.")
            if "details" in value:
                if not isinstance(value["details"], list) or len(value["details"]) > 16:
                    raise ProviderError("Patch provider error details are incomplete.")
                for child in value["details"]:
                    error(child, depth + 1)
            if "innererror" in value:
                fields(value["innererror"], {"exceptiontype", "errordetail"})
                if any(not isinstance(v, str) or len(v) > 4096 for v in value["innererror"].values()):
                    raise ProviderError("Patch inner error is invalid.")
        error(summary["error"])
    return summary


def validate_observation(value):
    """Current corroboration is not the archived failure/freshness predicate."""
    fields(value, SNAPSHOT_FIELDS)
    if (value["version"] != VERSION or value["source"] != SOURCE or value["api_version"] != API_VERSION
            or value["resource_id"] != TARGET):
        raise ProviderError("Patch summary provenance does not match the fixed Compute source.")
    validate_summary(value["summary"])
    ticks(value["collection_time"])
    encode(value)
    return value


def validate_snapshot(value):
    validate_observation(value)
    summary = value["summary"]
    age = ticks(value["collection_time"]) - ticks(summary["lastModifiedTime"])
    if not 0 <= age < FRESH_SECONDS * 10000000:
        raise ProviderError("The provider time is future or stale at collection.")
    encode(value)
    return value


def observation(raw, now):
    ticks(now)
    value = decode(raw)
    if any(name in value for name in ("error", "Error", "partialError", "nextLink", "continuationToken")):
        raise ProviderError("Compute returned an error or partial envelope, not patch evidence.")
    status = value.get("patchStatus")
    if not isinstance(status, dict) or "lastPatchInstallationSummary" not in status:
        raise ProviderError("The latest installation summary is missing; assessment is not installation evidence.")
    result = {
        "version": VERSION, "source": SOURCE, "api_version": API_VERSION,
        "resource_id": TARGET, "collection_time": now.astimezone(timezone.utc).isoformat(),
        "summary": status["lastPatchInstallationSummary"],
    }
    return validate_observation(result)


def snapshot(raw, now):
    return validate_snapshot(observation(raw, now))


def corroborate(current, snapshots):
    """Identity consistency only; never infer ordering, failure or recovery."""
    latest = validate_observation(current)["summary"]
    if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 100:
        raise ProviderError("Complete bounded archived installation evidence is required.")
    installation = None
    for value in snapshots:
        saved = validate_snapshot(value)["summary"]
        if saved["status"] not in TERMINAL or saved["failedPatchCount"] <= 0:
            raise ProviderError("Current state cannot substitute for an archived failure.")
        if installation is not None and saved["installationActivityId"] != installation:
            raise ProviderError("Every archive must identify the same installation.")
        installation = saved["installationActivityId"]
        # Conservative unresolved identity-consistency guard, not a claimed
        # Microsoft guarantee that startTime is immutable.
        if latest["installationActivityId"] == installation and ticks(latest["startTime"]) != ticks(saved["startTime"]):
            raise ProviderError("The same installation has an inconsistent start instant.")
    return ("current summary identifies the archived installation"
            if latest["installationActivityId"] == installation
            else "current summary identifies another installation")


def qualifying(value):
    summary = validate_snapshot(value)["summary"]
    return summary["status"] in TERMINAL and summary["failedPatchCount"] > 0


def digest_for(raw):
    return hashlib.sha256(raw).hexdigest()


def archive_path(digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProviderError("An exact snapshot digest is required, never a caller URL.")
    return ARCHIVE_PREFIX + digest + ".json"


def archived(raw, digest):
    archive_path(digest)
    if digest_for(raw) != digest:
        raise ProviderError("The authenticated archive digest differs from its address.")
    value = validate_snapshot(decode(raw, ARCHIVE_LIMIT))
    if encode(value) != raw:
        raise ProviderError("The snapshot is not the canonical immutable archive.")
    return value


def event(value, digest):
    if not qualifying(value) or digest_for(encode(value)) != digest:
        raise ProviderError("Only a verified qualifying snapshot can emit a failure event.")
    summary = value["summary"]
    result = {
        "version": VERSION, "resource_id": TARGET, "installation_id": summary["installationActivityId"],
        "status": summary["status"], "failed_patch_count": summary["failedPatchCount"],
        "provider_time": summary["lastModifiedTime"], "collection_time": value["collection_time"], "digest": digest,
    }
    encode(result, LOG_LIMIT - len(MARKER))
    return result


def validate_event(value, now):
    fields(value, EVENT_FIELDS)
    guid(value["installation_id"])
    archive_path(value["digest"])
    if (value["version"] != VERSION or value["resource_id"] != TARGET
            or not isinstance(value["status"], str) or value["status"] not in TERMINAL
            or type(value["failed_patch_count"]) is not int or not 1 <= value["failed_patch_count"] <= 1000000):
        raise ProviderError("The patch console event is invalid.")
    modified, collected, current = ticks(value["provider_time"]), ticks(value["collection_time"]), ticks(now)
    if not modified <= collected <= current or not 0 <= current - modified < FRESH_SECONDS * 10000000:
        raise ProviderError("Late or future patch events cannot be emitted.")
    encode(value, LOG_LIMIT - len(MARKER))
    return value


def episode(installation_id):
    return str(uuid5(PATCH_NAMESPACE, "patch-installation/v2:" + TARGET.lower() + ":" + guid(installation_id)))


def sample(value, digest):
    if not qualifying(value) or digest_for(encode(value)) != digest:
        raise ProviderError("A qualifying immutable summary is required.")
    summary = value["summary"]
    return {
        "resource_id": TARGET, "observed_at": summary["lastModifiedTime"],
        "installation_id": summary["installationActivityId"], "status": summary["status"],
        "failed_patch_count": summary["failedPatchCount"],
        "error_details": encode(summary["error"]).decode("ascii")[:1600] if "error" in summary else "",
        "digest": digest, "snapshot": value,
    }


def validate_sample(value, installation_id, start, end):
    fields(value, {"resource_id", "observed_at", "installation_id", "status", "failed_patch_count",
                   "error_details", "digest", "snapshot"})
    snapshot_value = archived(encode(value["snapshot"]), value["digest"])
    if (type(value["failed_patch_count"]) is not int
            or value != sample(snapshot_value, value["digest"]) or value["installation_id"] != guid(installation_id)
            or not ticks(start) < ticks(value["observed_at"]) <= ticks(end)):
        raise ProviderError("Every sample must match the selected installation and actual native window.")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(credential, scope, url, *, data=None, limit, opener=None, timeout=5):
    headers = {"Authorization": "Bearer " + credential.get_token(scope).token, "Accept": "application/json"}
    if scope == "https://storage.azure.com/.default":
        headers.update({"x-ms-version": "2023-11-03", "x-ms-date": formatdate(usegmt=True)})
    if data is not None:
        headers.update({"If-None-Match": "*", "x-ms-blob-type": "BlockBlob", "Content-Type": "application/json"})
    request = Request(url, data=data, headers=headers, method="GET" if data is None else "PUT")
    checkpoint = getattr(credential, "_patch_checkpoint", None) if data is None else None
    if checkpoint is not None:
        timeout = checkpoint(timeout)
    try:
        with (opener or build_opener(NoRedirect())).open(request, timeout=timeout) as response:
            if checkpoint is not None:
                checkpoint()
            raw = response.read(limit + 1)
            if checkpoint is not None:
                checkpoint()
            if response.status != (200 if data is None else 201) or len(raw) > limit:
                if data is None:
                    raise ProviderReadError("invalid")
                raise ProviderError("The fixed provider/archive response is incomplete or oversized.")
            return raw
    except HTTPError as error:
        failure = classify_failure(error) if data is None else None
        error.close()
        if data is None:
            reason, transient, delay, valid = failure or ("invalid", False, None, True)
            if transient:
                raise TransientProviderError(reason, delay, valid) from None
            raise ProviderReadError(reason) from None
        raise ProviderError("The fixed provider/archive HTTP read or create failed.") from None
    except Exception as error:
        if isinstance(error, ProviderError):
            raise
        if data is None:
            failure = classify_failure(error)
            if failure is not None:
                reason, transient, delay, valid = failure
                if transient:
                    raise TransientProviderError(reason, delay, valid) from None
                raise ProviderReadError(reason) from None
            if isinstance(error, (URLError, OSError, http.client.HTTPException, ValueError)) or _named(
                    error, {"ServiceRequestError", "ServiceResponseError"}):
                raise ProviderError("The fixed provider/archive transport did not complete.") from None
            raise
        if not isinstance(error, (URLError, OSError)):
            raise  # PUT exception handling remains exactly the legacy boundary.
        raise ProviderError("The fixed provider/archive transport did not complete.") from None


def read_compute(credential, *, timeout=5):
    return _request(credential, "https://management.azure.com/.default", COMPUTE_URL, limit=SOURCE_LIMIT, timeout=timeout)


def read_archive(credential, digest, *, timeout=5):
    raw = _request(credential, "https://storage.azure.com/.default",
                   STORAGE + archive_path(digest), limit=ARCHIVE_LIMIT, timeout=timeout)
    return archived(raw, digest)


def archive(credential, value):
    raw = encode(validate_snapshot(value))
    digest = digest_for(raw)
    # Exactly one create attempt. A conflict OR uncertain response is resolved
    # only by authenticated read of the original address, never another PUT.
    try:
        _request(credential, "https://storage.azure.com/.default", STORAGE + archive_path(digest),
                 data=raw, limit=1024)
    except (ProviderError, OSError):
        pass  # The mandatory readback below is the sole success authority.
    actual = read_archive(credential, digest)
    if encode(actual) != raw:
        raise ProviderError("The actual archive bytes differ from the captured summary.")
    return digest
