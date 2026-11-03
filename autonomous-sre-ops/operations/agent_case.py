"""Fixed SRE outbound facade tool. No desktop state, dispatch or repair authority.

Every fresh invocation binds its own managed identity and fixed AzureReader.
Commissioning is frozen in the protected generated package, never model input.
The decoder is bound to the observed native Azure Monitor incident envelope.
"""

from __future__ import annotations

import copy
import contextvars
import hashlib
import http.client
import json
import logging
import math
import re
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid5
from xml.etree import ElementTree

from . import patch_provider as patch

from .servicenow import (
    ORIGIN, SENSITIVE, AmbiguousWriteError, CaseClient, ClientCredentials,
    Contracts, ServiceNowError, canonical_uuid,
)

RESOURCE = (
    "/subscriptions/00000001-000a-4000-a000-000000000001/"
    "resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/"
    "virtualMachines/vm-asre-linux-cus"
)
RULE = (
    "/subscriptions/00000001-000a-4000-a000-000000000001/"
    "resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/"
    "scheduledQueryRules/asre-demo-disk"
)
QUERY = (
    'Perf | where _ResourceId =~ "' + RESOURCE + '" '
    '| where ObjectName == "Logical Disk" and CounterName == "% Used Space" '
    '| where InstanceName == "/srv/asre-demo" '
    '| summarize Value = max(CounterValue) by _ResourceId | where Value >= 80'
)
NAMESPACE = UUID("91b92a24-4bb0-580f-9b2a-05a85c649dd7")
PREFIX = "case-intents/v1/"
STORAGE = "https://stasre8a327010f9.blob.core.windows.net"
CONTAINER = "evidence"
SECRET_URL = "https://kv-asre-8a327010f9.vault.azure.net/secrets/asre-facade-client-credential?api-version=7.4"
UAMI = "00000028-000a-4000-9000-000000000028"
FACADE_CLIENT = "0000000000000000000000000000005a"
LIMIT = 192000
PATCH_DISPLAY_LIMIT = 3072  # Internal canonical-JSON budget, not a platform limit.
# Escaped JSON string content bytes, excluding the surrounding quotes.
PATCH_ERROR_CODE_LIMIT = 48
PATCH_ERROR_MESSAGE_LIMIT = 192
PATCH_DETAIL_MESSAGE_LIMIT = 160
PATCH_DETAIL_LIMIT = 3
_NATIVE_COLLECTION_LIMIT = 1048576
TERMINAL = {"applied", "already_applied"}
RECEIPT_PREFIX = STORAGE + "/evidence/tool-results/"
WORKSPACE = (
    "/subscriptions/00000001-000a-4000-a000-000000000001/"
    "resourcegroups/rg-asre-control-cus/providers/microsoft.operationalinsights/"
    "workspaces/law-asre-cus"
)
SRE_ORIGIN = "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai"
SRE_SCOPE = "https://azuresre.dev/.default"
LOG_QUERY_URL = "https://api.loganalytics.io/v1/workspaces/00000062-000a-4000-8000-000000000062/query"
LOG_WORKSPACE_ID = "00000062-000a-4000-8000-000000000062"
ALERT_FRESHNESS_SECONDS = 900  # Age of this alert, not lifetime after commissioning.
BACKUP_MAX_EVALUATION_LAG_SECONDS = 3600  # Observed stale-window lag is ~1806 s. Stored Backup intents re-validate with this bound; never lower it.
NATIVE_DTO_VERSION = "azmonitor-incident/v1"
NATIVE_RULE_ID = "azmon:v2:arm-rule:000000000000000000000000000000000000000000000000000000000000015A"
GRAPH_QUERY_URL = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"
PATCH_RESOURCE = RESOURCE.replace("linux", "patch")
LEGACY_PATCH_ALERT_QUERY = (
    'arg("").PatchInstallationResources '
    "| where type =~ 'microsoft.compute/virtualmachines/patchinstallationresults' "
    f"| where id startswith '{PATCH_RESOURCE}/patchInstallationResults/' "
    "| extend observed_at=todatetime(properties.lastModifiedDateTime) "
    "| where observed_at > ago(10m) and observed_at <= now() "
    "| where tostring(properties.status) in ('Failed', 'CompletedWithWarnings') "
    "| where toint(properties.failedPatchCount) > 0 "
    f"| project TimeGenerated=observed_at, resource_id='{PATCH_RESOURCE}', observed_at, installation_id=id, "
    "status=tostring(properties.status), failed_patch_count=toint(properties.failedPatchCount), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)
# Exact released Oct 22 query (129d7e78/d360da01), before the Oct 27 alias
# and upper-bound correction. Only frozen v1 intent validation/reconciliation
# and historical-series recognition admit these two forms; neither is fresh
# patch-case authority. Do not synthesize combinations or accept query fragments.
LEGACY_PATCH_OCT22_QUERY = (
    'arg("").PatchInstallationResources '
    "| where type =~ 'microsoft.compute/virtualmachines/patchinstallationresults' "
    f"| where id startswith '{PATCH_RESOURCE}/patchInstallationResults/' "
    "| extend observed_at=todatetime(properties.lastModifiedDateTime) "
    "| where observed_at > ago(10m) "
    "| where tostring(properties.status) in ('Failed', 'CompletedWithWarnings') "
    "| where toint(properties.failedPatchCount) > 0 "
    f"| project resource_id='{PATCH_RESOURCE}', observed_at, installation_id=id, "
    "status=tostring(properties.status), failed_patch_count=toint(properties.failedPatchCount), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)
LEGACY_PATCH_ALERT_QUERIES = (LEGACY_PATCH_ALERT_QUERY, LEGACY_PATCH_OCT22_QUERY)
PATCH_ALERT_QUERY = patch.ALERT_QUERY
NOISE_QUERY = (
    'Perf | where _ResourceId =~ "' + RESOURCE + '" '
    '| where ObjectName == "Logical Disk" and CounterName == "% Used Space" '
    '| where InstanceName == "/srv/asre-demo" and CounterValue >= 80'
)
GROWTH_QUERY = (
    'Perf | where _ResourceId =~ "' + RESOURCE + '" '
    '| where ObjectName == "Logical Disk" and CounterName == "% Used Space" '
    '| where InstanceName == "/srv/asre-demo" '
    "| sort by TimeGenerated asc "
    "| serialize previous_value=prev(CounterValue), previous_time=prev(TimeGenerated) "
    "| summarize sample_count=count(), first_observed=min(TimeGenerated), last_observed=max(TimeGenerated), "
    "first_value=min(CounterValue), last_value=max(CounterValue), "
    "reversals=countif(isnotnull(previous_value) and CounterValue < previous_value), "
    "gaps=countif(isnotnull(previous_time) and TimeGenerated - previous_time > 120s) by _ResourceId "
    "| extend baseline_seconds=(last_observed-first_observed)/1s "
    "| where sample_count >= 6 and baseline_seconds >= 300 and reversals == 0 and gaps == 0 "
    "and last_value < 80 and last_value > first_value "
    "| extend forecast_seconds=(80.0-last_value)/(last_value-first_value)*baseline_seconds "
    "| where forecast_seconds > 0 and forecast_seconds <= 900"
)
BACKUP_RESOURCE = RESOURCE.replace("linux", "backup")
BACKUP_VAULT = BACKUP_RESOURCE.split("/providers/")[0] + "/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"
BACKUP_ITEM = (
    BACKUP_VAULT + "/backupFabrics/Azure/protectionContainers/"
    "IaasVMContainer;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus/"
    "protectedItems/VM;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus"
)
BACKUP_GRAPH_QUERY = (
    "recoveryservicesresources "
    "| where type =~ 'microsoft.recoveryservices/vaults/backupjobs' "
    f"| where id startswith '{BACKUP_VAULT}/backupJobs/' "
    f"| where tostring(properties.backupInstanceId) =~ '{BACKUP_ITEM}' "
    "| where tostring(properties.backupManagementType) == 'AzureIaasVM' "
    "and tostring(properties.operation) == 'Backup' and tostring(properties.status) == 'Failed' "
    "| extend observed_at=todatetime(properties.endTime) | where observed_at > ago(10m) "
    f"| project resource_id='{BACKUP_RESOURCE}', observed_at, job_id=id, status=tostring(properties.status), "
    "protected_item_id=tostring(properties.backupInstanceId), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)


@dataclass(frozen=True)
class CaseProfile:
    name: str
    resource_id: str
    rule_id: str
    query: str
    signal_family: str
    telemetry_source: str
    telemetry_reference: str
    mode: str = "reactive"
    severity: str = "Sev1"
    graph_query: str | None = None


DISK_PROFILE = CaseProfile("disk", RESOURCE, RULE, QUERY, "filesystem", "AzureMonitor", LOG_QUERY_URL)
PATCH_PROFILE = CaseProfile(
    "patch-installation", PATCH_RESOURCE, RULE.replace("asre-demo-disk", "asre-demo-patch-installation"),
    PATCH_ALERT_QUERY,
    "patch-installation", "UpdateManager", patch.COMPUTE_URL,
)
NOISE_PROFILE = CaseProfile(
    "alert-recurrence", RESOURCE, RULE.replace("asre-demo-disk", "asre-demo-noise-overlap"),
    NOISE_QUERY, "alert-recurrence", "AzureMonitor", LOG_QUERY_URL, severity="Sev2",
)
GROWTH_PROFILE = CaseProfile(
    "capacity-growth", RESOURCE, RULE.replace("asre-demo-disk", "asre-demo-capacity-growth"),
    GROWTH_QUERY, "filesystem", "AzureMonitor", LOG_QUERY_URL, mode="proactive", severity="Sev2",
)
BACKUP_PROFILE = CaseProfile(
    "backup-job", BACKUP_RESOURCE, RULE.replace("asre-demo-disk", "asre-demo-backup-job"),
    BACKUP_GRAPH_QUERY.replace("recoveryservicesresources", 'arg("").RecoveryServicesResources', 1),
    "backup-job", "AzureBackup", GRAPH_QUERY_URL, graph_query=BACKUP_GRAPH_QUERY,
)
CASE_PROFILES = {profile.name: profile for profile in (
    DISK_PROFILE, PATCH_PROFILE, NOISE_PROFILE, GROWTH_PROFILE, BACKUP_PROFILE,
)}


class CaseToolError(RuntimeError):
    """Only constant, redacted messages cross the runtime boundary."""


class CaseDiagnostic(CaseToolError):
    STAGES = frozenset({"token", "sre-threads", "sre-thread", "alert", "log-query",
                        "intent-read", "secret", "archive", "compute"})
    REASONS = frozenset({"timeout", "transport", "throttled", "server-error", "auth",
                         "rejected", "tls", "invalid", "budget"})
    TRANSIENT = frozenset({"timeout", "transport", "throttled", "server-error"})

    def __init__(self, stage, reason, retryable, attempts, *, retry_after=None, retry_after_valid=True):
        if (type(stage) is not str or stage not in self.STAGES
                or type(reason) is not str or reason not in self.REASONS
                or type(retryable) is not bool or retryable and reason not in self.TRANSIENT
                or type(attempts) is not int or not 0 <= attempts <= 2
                or retry_after is not None and (type(retry_after) is not int or not 0 <= retry_after <= 30)
                or type(retry_after_valid) is not bool):
            raise CaseToolError("Invalid fixed patch-read diagnostic.") from None
        self.stage, self.reason, self.retryable, self.attempts = stage, reason, retryable, attempts
        self.retry_after, self.retry_after_valid = retry_after, retry_after_valid
        super().__init__("The fixed patch-read dependency did not complete.")

    def result(self):
        return {
            "status": "blocked",
            "message": (
                "A fixed Azure patch-read dependency did not complete; no business writes occurred in this call. "
                "Repeat this identical read once, then stop and report the returned stage and reason."
                if self.retryable else
                "A fixed Azure patch-read dependency did not complete or validate; no business writes occurred in this call. "
                "Do not repeat; report the returned stage and reason and preserve all IDs."
            ),
            "stage": self.stage, "reason": self.reason, "retryable": self.retryable, "attempts": self.attempts,
        }


class _InvalidRead(ValueError):
    pass


_SDK_LOG_GATE = contextvars.ContextVar("asre_patch_sdk_log_gate", default=False)
_SDK_LOG_LOCK = threading.Lock()


class _SDKLogFilter(logging.Filter):
    def filter(self, record):
        return not _SDK_LOG_GATE.get()


_SDK_LOG_FILTER = _SDKLogFilter()


def _install_sdk_log_gate():
    with _SDK_LOG_LOCK:
        for name in ("azure.identity._internal.decorators", "azure.identity._internal.msal_managed_identity_client"):
            logger = logging.getLogger(name)
            if _SDK_LOG_FILTER not in logger.filters:
                logger.addFilter(_SDK_LOG_FILTER)


@contextmanager
def _sdk_log_gate():
    token = _SDK_LOG_GATE.set(True)
    try:
        yield
    finally:
        _SDK_LOG_GATE.reset(token)


def _causes(error):
    return patch.failure_chain(error)


def _token_reason(error):
    failure = patch.classify_failure(error)
    if failure is not None:
        reason, transient, delay, valid = failure
        if transient or reason == "tls":
            return reason, delay, valid
    return "auth", None, True


def _read_failure(error):
    if isinstance(error, CaseDiagnostic):
        return error.stage, error.reason, error.reason in CaseDiagnostic.TRANSIENT, error.retry_after, error.retry_after_valid
    if isinstance(error, patch.ProviderReadError):
        return None, error.reason, isinstance(error, patch.TransientProviderError), getattr(error, "retry_after", None), getattr(error, "retry_after_valid", True)
    failure = patch.classify_failure(error)
    if isinstance(error, HTTPError):
        error.close()
    if failure is not None:
        return (None, *failure)
    if isinstance(error, (URLError, OSError, http.client.HTTPException)):
        return None, "invalid", False, None, True
    if isinstance(error, _InvalidRead):
        return None, "invalid", False, None, True
    if isinstance(error, patch.ProviderError):
        return None, "invalid", False, None, True
    return None


class _PatchReadContext:
    CAPS = {"log-query": 45, "sre-threads": 30, "sre-thread": 30, "alert": 30,
            "intent-read": 30, "secret": 30, "archive": 15, "compute": 15}

    def __init__(self, action, clock=None, sleep=None):
        self.action, self.clock, self.sleep = action, clock or time.monotonic, sleep or time.sleep
        self.started = self.clock()
        self.deadline = self.started + (150 if action == "read" else 90)
        self._closed = False
        self.stage, self.attempts = "token", 0

    @property
    def active(self):
        return not self._closed

    def remaining(self, stage=None, attempts=None):
        tick = self.clock()
        if not math.isfinite(tick) or tick < self.started or tick >= self.deadline:
            raise CaseDiagnostic(stage or self.stage, "budget", False,
                                 self.attempts if attempts is None else attempts) from None
        return self.deadline - tick

    def checkpoint(self, cap=None):
        remaining = self.remaining()
        return remaining if cap is None else min(cap, remaining)

    def close(self, stage):
        if self.active:
            self.remaining(stage, 0)
            self._closed = True

    def request(self, stage, attempt):
        self.remaining(stage, 0)
        self.stage, self.attempts = stage, 0
        for number in (1, 2):
            self.attempts = number
            timeout = min(self.CAPS[stage], self.remaining(stage, number - 1))
            failure = None
            try:
                result = attempt(timeout)
            except Exception as error:
                if isinstance(error, CaseDiagnostic) and error.reason == "budget":
                    raise
                self.remaining(stage, number)
                failure = _read_failure(error)
                if failure is None:
                    raise
            self.remaining(stage, number)
            if failure is None:
                return result
            failed_stage, reason, transient, delay, valid = failure
            failed_stage = failed_stage or stage
            pause = 2 if delay is None else delay
            may_retry = transient and valid and pause < self.remaining(stage, number)
            if number == 2 or not may_retry:
                immediate = may_retry and (delay is None or delay == 0)
                raise CaseDiagnostic(failed_stage, reason, immediate and self.action == "read", number,
                                     retry_after=delay, retry_after_valid=valid) from None
            self.sleep(pause)
            self.remaining(stage, number)


def _patch_context(action):
    return _PatchReadContext(action)


class _MeteredCredential:
    def __init__(self, credential, context):
        self.legacy, self.context = credential, context

    def get_token(self, *args, **kwargs):
        if not self.context.active:
            return self.legacy.get_token(*args, **kwargs)
        self.context.remaining("token")
        try:
            with _sdk_log_gate():
                result = self.legacy.get_token(*args, **kwargs)
        except Exception as error:
            self.context.remaining("token")
            reason, delay, valid = _token_reason(error)
            raise CaseDiagnostic("token", reason, reason in CaseDiagnostic.TRANSIENT and valid,
                                 self.context.attempts, retry_after=delay, retry_after_valid=valid) from None
        self.context.remaining("token")
        return result

    def legacy_token(self, scope):
        if self.context.active:
            with _sdk_log_gate():
                return self.legacy.get_token(scope)
        return self.legacy.get_token(scope)

    def _patch_checkpoint(self, cap=None):
        return self.context.checkpoint(cap) if self.context.active else cap

    def close(self):
        self.legacy.close()


class _PrewriteClient:
    METHODS = frozenset({"health", "read_case", "create_case", "append_note",
                         "reconcile", "acknowledge", "audit", "feedback_pages"})

    def __init__(self, client, context):
        self._client, self._context = client, context

    def __getattr__(self, name):
        if name not in self.METHODS:
            raise CaseToolError("An unbound facade method is not admitted.") from None
        def invoke(*args, **kwargs):
            self._context.close("secret")
            return getattr(self._client, name)(*args, **kwargs)
        return invoke


class Reader(Protocol):
    def investigation(self, investigation_id: str) -> dict: ...
    def alert(self, alert_id: str) -> dict: ...
    def disk(self, alert_id: str) -> dict: ...
    def receipt(self, request_id: str) -> dict: ...
    def signal(self, alert_id: str) -> dict: ...
    def patch_current(self, samples: list[dict]) -> dict: ...


class Intents(Protocol):
    def read(self, name: str) -> bytes: ...
    def create(self, name: str, data: bytes) -> None: ...
    def initial_paths(self) -> list[str]: ...


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise CaseToolError("Unexpected or missing runtime fields.")


def wire(value) -> bytes:
    return _wire(value, LIMIT)


def _wire(value, limit) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise CaseToolError("The runtime JSON is invalid.") from None
    if len(raw) > limit or SENSITIVE.search(raw.decode()):
        raise CaseToolError("Runtime content exceeds its bound or contains suspected credentials.")
    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if re.search(r"secret|password|token|authorization|private.?key|api.?key", key, re.I):
                    raise CaseToolError("Credential-shaped content is not admitted.")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
    inspect(value)
    return raw


def _patch_error_text(text, limit):
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise CaseToolError("The patch error text is not valid UTF-8.") from None
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
    clean, size = [], 0
    for ch in text:
        cost = len(json.dumps(ch, ensure_ascii=True)) - 2
        if size + cost > limit:
            return "".join(clean), True
        clean.append(ch)
        size += cost
    return "".join(clean), False


def _patch_provider_error(error):
    if error is None:
        return None
    code, code_clipped = _patch_error_text(error["code"], PATCH_ERROR_CODE_LIMIT)
    message, message_clipped = _patch_error_text(error["message"], PATCH_ERROR_MESSAGE_LIMIT)
    details = error.get("details", [])
    result = {
        "code": code, "message": message, "detail_count": len(details), "details": [],
        "truncated": code_clipped or message_clipped or len(details) > PATCH_DETAIL_LIMIT
                     or "target" in error or "innererror" in error,
    }
    for detail in details[:PATCH_DETAIL_LIMIT]:
        code, code_clipped = _patch_error_text(detail["code"], PATCH_ERROR_CODE_LIMIT)
        message, message_clipped = _patch_error_text(detail["message"], PATCH_DETAIL_MESSAGE_LIMIT)
        result["details"].append({"code": code, "message": message})
        result["truncated"] |= (code_clipped or message_clipped or "target" in detail
                                or "innererror" in detail or "details" in detail)
    return result


def _patch_native_display(binding, signal, current):
    """Display only, after native_signal has validated ALL complete evidence."""
    first = signal["samples"][0]
    sample = {name: first[name] for name in (
        "resource_id", "installation_id", "observed_at", "status", "failed_patch_count", "digest",
    )}
    sample["provider_error"] = _patch_provider_error(first["snapshot"]["summary"].get("error"))
    latest = current["summary"]
    result = {
        "status": "native_evidence", "binding": copy.deepcopy(binding),
        "signal": {"reference": patch.COMPUTE_URL, "sample_count": len(signal["samples"]), "samples": [sample]},
        "current_provider_observation": {
            "installation_id": latest["installationActivityId"], "status": latest["status"],
            "failed_patch_count": latest["failedPatchCount"], "provider_time": latest["lastModifiedTime"],
            "collection_time": current["collection_time"],
            "relationship": patch.corroborate(current, [row["snapshot"] for row in signal["samples"]]),
        },
    }
    _wire(result, PATCH_DISPLAY_LIMIT)  # Fail closed, never trim authority or proof.
    return result


def decode(raw: bytes | str) -> dict:
    return _decode(raw, LIMIT)


def _decode(raw: bytes | str, limit) -> dict:
    if not isinstance(raw, (str, bytes)) or len(raw) > limit:
        raise CaseToolError("Runtime JSON exceeds its independent bound.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CaseToolError("Duplicate JSON fields are not admitted.")
            result[key] = value
        return result
    try:
        result = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(result, dict):
            raise ValueError()
        _wire(result, limit)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise CaseToolError("Runtime JSON is not a complete valid object.") from None


def stamp(value) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError()
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise CaseToolError("An authoritative timezone-qualified timestamp is required.") from None


def backup_evidence_end(window_start, window_end, fired_at) -> datetime:
    # ARG-sourced alerts can report a window up to ~30 min older than the evaluation that
    # matched, so Backup evidence is bound to [window_start, fired_at], not window_end.
    start, end, fired = stamp(window_start), stamp(window_end), stamp(fired_at)
    if not start < end <= fired or (fired - end).total_seconds() > BACKUP_MAX_EVALUATION_LAG_SECONDS:
        raise CaseToolError("The Backup alert evaluation window or firing lag is outside its bounded allowance.")
    return fired


def canonical_error_details(raw):
    # ARG returns one job's error_details JSON in varying key order; canonical text keeps freeze and re-read equal.
    # Details cut at the query's 1600-char substring are not valid JSON, stay raw, and can still vary.
    if not isinstance(raw, str):
        return raw

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def constant(name):
        raise ValueError()

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=constant)
        if not isinstance(value, (dict, list)):
            return raw
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        canonical.encode("utf-8")
    except (ValueError, RecursionError):
        return raw
    return canonical if len(canonical) <= 1600 else raw


def growth_forecast(samples):
    if len(samples) < 6:
        raise CaseToolError("A growth forecast needs six real observations over five minutes.")
    times = [stamp(sample["observed_at"]) for sample in samples]
    values = [sample["value"] for sample in samples]
    span = (times[-1] - times[0]).total_seconds()
    if (
        span < 300 or any(not 0 < (right - left).total_seconds() <= 120 for left, right in zip(times, times[1:]))
        or any(right < left for left, right in zip(values, values[1:])) or values[-1] <= values[0]
    ):
        raise CaseToolError("Sparse, reset, unordered or flat data cannot establish this growth risk.")
    seconds = (80.0 - values[-1]) / (values[-1] - values[0]) * span
    if not math.isfinite(seconds) or not 0 < seconds <= 900:
        raise CaseToolError("The actual observations do not predict this pre-breach risk within fifteen minutes.")
    return {"baseline_seconds": span, "threshold_percent": 80,
            "seconds_from_last_sample_to_threshold": seconds}


def identifier(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1000 or value != value.strip() or any(ord(c) < 32 for c in value):
        raise CaseToolError("An exact bounded native identity is required.")
    return value


def episode_for(investigation_id: str) -> str:
    return str(uuid5(NAMESPACE, "native-investigation:" + identifier(investigation_id)))


def intent_path(episode: str, recurrence: str = "") -> str:
    canonical_uuid(episode)
    return PREFIX + episode + (
        "/updates/" + str(uuid5(UUID(episode), "recurrence:" + identifier(recurrence))) + ".json"
        if recurrence else "/initial.json"
    )


def checked_path(name):
    if isinstance(name, str) and re.fullmatch(
        re.escape(PREFIX) + r"finite/[0-9a-f-]{36}/reservation\.json", name
    ):
        canonical_uuid(name[len(PREFIX):].split("/")[1])
        return name
    if not isinstance(name, str) or not re.fullmatch(
        re.escape(PREFIX) + r"[0-9a-f-]{36}/(?:initial|updates/[0-9a-f-]{36})\.json", name
    ):
        raise CaseToolError("Only the exact versioned case-intent namespace is admitted.")
    parts = name[len(PREFIX):].split("/")
    canonical_uuid(parts[0])
    if len(parts) == 3:
        canonical_uuid(parts[2][:-5])
    return name


def commissioning_policy():
    # Generated package resource, never a sandbox repository-path assumption.
    return decode((Path(__file__).parent / "commissioning.json").read_bytes())


def validate_commissioning(config, policy):
    keys(policy, {"version", "minimum_not_before"})
    keys(config, {"version", "not_before", "historical_alert_ids", "rule_id", "resource_id",
                  "facade_origin", "facade_client_id", "reader"}, {"expires_at", "finite_commissioning", "profiles"})
    profiles = config.get("profiles", ["disk"])
    if (
        not isinstance(profiles, list) or not profiles or len(profiles) > len(CASE_PROFILES)
        or any(not isinstance(name, str) or name not in CASE_PROFILES for name in profiles)
        or len(set(profiles)) != len(profiles) or "disk" not in profiles
    ):
        raise CaseToolError("Protected case profiles must be a closed, unique release-bound selection.")
    lower = stamp(config["not_before"])
    upper = stamp(config["expires_at"]) if "expires_at" in config else None
    if "finite_commissioning" in config:
        finite = config["finite_commissioning"]
        keys(finite, {"version"})
        if (
            finite["version"] != "1.0" or upper is None
            or not 0 < (upper - lower).total_seconds() <= 600
            or any(not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", config[field])
                   for field in ("not_before", "expires_at"))
        ):
            raise CaseToolError("Finite commissioning requires its closed version and an explicit UTC window of at most 600 seconds.")
    reader = config["reader"]
    keys(reader, {"sre_origin", "workspace_resource_id", "log_analytics_workspace_id", "native_dto_version"})
    if not all(isinstance(config[field], str) for field in ("rule_id", "resource_id", "facade_origin", "facade_client_id")) or not all(
        isinstance(value, str) for value in reader.values()
    ):
        raise CaseToolError("Trusted runtime bindings must be fixed strings.")
    if (
        policy["version"] != "1.0" or config["version"] != policy["version"]
        or lower < stamp(policy["minimum_not_before"]) or upper is not None and upper <= lower
        or config["rule_id"].lower() != RULE.lower() or config["resource_id"].lower() != RESOURCE.lower()
        or config["facade_origin"] != ORIGIN or config["facade_client_id"] != FACADE_CLIENT
        or reader["sre_origin"] != SRE_ORIGIN or reader["workspace_resource_id"].lower() != WORKSPACE.lower()
        or reader["log_analytics_workspace_id"] != LOG_WORKSPACE_ID or reader["native_dto_version"] != NATIVE_DTO_VERSION
        or not isinstance(config["historical_alert_ids"], list) or len(config["historical_alert_ids"]) > 1000
        or any(not isinstance(item, str) or len(item) > 2048 for item in config["historical_alert_ids"])
    ):
        raise CaseToolError("The trusted commissioning configuration violates its versioned release boundary.")


def _azure_attempt(credential, scope, url, payload=None, *, timeout=20, context=None):
    """Internal fixed-read transport; URLs are constructed by AzureReader only."""
    limit = (_NATIVE_COLLECTION_LIMIT
             if scope == SRE_SCOPE and url == SRE_ORIGIN + "/api/v1/threads" and payload is None
             else LIMIT)
    token = credential.get_token(scope).token
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    data = None
    if payload is not None:
        data = wire(payload)
        headers["Content-Type"] = "application/json"
    if context is not None:
        timeout = context.checkpoint(timeout)
    with build_opener(NoRedirect()).open(Request(url, data=data, headers=headers), timeout=timeout) as response:
        if context is not None:
            context.checkpoint()
        raw = response.read(limit + 1)
        if context is not None:
            context.checkpoint()
        if response.status != 200:
            raise _InvalidRead()
    try:
        return _decode(raw, limit)
    except CaseToolError:
        raise _InvalidRead() from None


def _azure_json(credential, scope, url, payload=None):
    try:
        return _azure_attempt(credential, scope, url, payload)
    except Exception:
        raise CaseToolError("The fixed authoritative Azure read did not complete or validate.") from None


def decode_native_incident(raw, profile=DISK_PROFILE):
    """Decode server-owned fields observed on automatic incident 00000029... .

    Azure SRE reports startMessageIsHuman=null for this native trigger. Source,
    origin, author, response plan and structured incident identity must agree;
    no title, alert prose or model-supplied resource establishes authority.
    The fixed rule's actual VM is independently established by ARM and Perf.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("status"), dict) or not isinstance(raw.get("startMessage"), dict):
        raise CaseToolError("The native incident envelope is incomplete.")
    source = raw.get("incidentSource", {})
    details = raw.get("incidentDetails", {})
    status = raw.get("status", {}).get("incidentStatus", {})
    author = raw.get("startMessage", {}).get("author", {})
    if (
        raw.get("source") != "Incident" or raw.get("threadOrigin") != "Trigger"
        or raw.get("startMessageIsHuman", True) is not None
        or raw.get("type") != "Prod" or raw.get("agentMode") != "Review"
        or raw.get("isIncidentTestModeEnabled") is True
        or not isinstance(source, dict) or source.get("incidentType") != "AzMonitor"
        or not isinstance(source.get("incidentId"), str) or source["incidentId"].lower() != profile.rule_id.lower()
        or (source.get("canonicalIncidentId") != NATIVE_RULE_ID if profile.name == "disk"
            else not isinstance(source.get("canonicalIncidentId"), str)
            or not re.fullmatch(r"azmon:v2:arm-rule:[0-9A-F]{64}", source["canonicalIncidentId"]))
        or not isinstance(details, dict) or details.get("filterId") != "asre-azure-monitor-review"
        or details.get("incidentPriority") != profile.severity
        or not isinstance(author, dict) or author.get("role") != "SREAgent" or author.get("userId") != "agent-default"
        or not isinstance(status, dict)
    ):
        raise CaseToolError("The native Azure Monitor trigger envelope does not match its bound schema.")
    try:
        alert = canonical_uuid(status.get("incidentId"))
    except ValueError:
        raise CaseToolError("The native current alert identity is invalid.") from None
    alerts = [alert]
    merge = details.get("mergeInfo")
    if merge is not None:
        if (
            not isinstance(merge, dict) or not isinstance(merge.get("mergedAlertIds"), list)
            or type(merge.get("mergedAlertCount")) is not int
            or not 1 <= merge["mergedAlertCount"] <= 100
        ):
            raise CaseToolError("The native merged-alert envelope is incomplete.")
        try:
            alerts += [canonical_uuid(value) for value in merge["mergedAlertIds"]]
        except ValueError:
            raise CaseToolError("The native merged-alert identity is invalid.") from None
        if len(alerts) != len(set(alerts)) or merge["mergedAlertCount"] != len(alerts):
            raise CaseToolError("The native merged-alert identities are inconsistent.")
        stamp(merge.get("lastFiredAt"))
    return {"platform": "AzMonitor", "alert_ids": [
                WORKSPACE + "/providers/Microsoft.AlertsManagement/alerts/" + identity for identity in alerts],
            "resource_id": profile.resource_id}


class AzureReader:
    """Bounded known native/ARM/Perf/receipt reads, no alert-supplied URLs.

    A release-bound host decoder is mandatory; absent it even a thread with
    plausible fields fails closed. Historical/manual conversations always fail.
    The decoder must derive alert_ids/resource_id/platform from server-owned
    source/incidentSource/incidentDetails, not prose, title or model arguments.
    """

    def __init__(self, credential, native_decoder=None, *, profile=DISK_PROFILE, context=None):
        self.credential, self.native_decoder = credential, native_decoder
        self.profile = profile
        self.context = context

    def _read(self, stage, scope, url, payload=None):
        if self.context is None or not self.context.active:
            return (_azure_json(self.credential, scope, url) if payload is None
                    else _azure_json(self.credential, scope, url, payload))
        return self.context.request(stage, lambda timeout: _azure_attempt(
            self.credential, scope, url, payload, timeout=timeout, context=self.context))

    def _provider(self, stage, call):
        if self.context is None or not self.context.active:
            return call()
        return self.context.request(stage, lambda timeout: call(timeout=timeout))

    def _invalid(self, stage):
        if self.context is not None and self.context.active:
            raise CaseDiagnostic(stage, "invalid", False, self.context.attempts) from None

    def current_investigation(self, not_before, now, historical):
        collection = self._read("sre-threads", SRE_SCOPE,
                                 SRE_ORIGIN + "/api/v1/threads")
        if (
            set(collection) - {"value", "nextLink", "continuationToken"}
            or not isinstance(collection.get("value"), list) or len(collection["value"]) > 200
            or collection.get("nextLink") or collection.get("continuationToken")
        ):
            raise CaseToolError("Native incident discovery requires a complete bounded collection.")
        candidates = []
        for item in collection["value"]:
            wire(item)
            if not isinstance(item, dict):
                raise CaseToolError("The native incident collection contains an invalid entry.")
            source = item.get("incidentSource")
            details = item.get("incidentDetails")
            if (
                item.get("source") != "Incident" or not isinstance(source, dict)
                or not isinstance(source.get("incidentId"), str) or source["incidentId"].lower() != self.profile.rule_id.lower()
                or not isinstance(details, dict) or details.get("filterId") != "asre-azure-monitor-review"
            ):
                continue
            merge = details.get("mergeInfo")
            observed = stamp(merge.get("lastFiredAt") if isinstance(merge, dict)
                             else details.get("incidentCreatedTime"))
            if not not_before <= observed <= now or (now - observed).total_seconds() > ALERT_FRESHNESS_SECONDS:
                continue
            investigation = self.investigation(item.get("id"))
            alert = self.alert(investigation["alert_ids"][0])
            if self.profile.name == "patch-installation" and alert.get("legacy_patch") is True:
                continue  # Historical undimensioned series is not fresh intake.
            fired = stamp(alert["fired_at"])
            if (
                not_before <= fired <= now and (now - fired).total_seconds() <= ALERT_FRESHNESS_SECONDS
                and alert["id"] not in historical and alert["state"] in {"Fired", "Resolved"}
            ):
                candidates.append(investigation["id"])
        if len(candidates) != 1:
            raise CaseToolError("Native discovery requires exactly one fresh matching investigation; none or ambiguity cannot authorize selection.")
        return candidates[0]

    def investigation(self, investigation_id):
        canonical_uuid(investigation_id)
        raw = self._read("sre-thread", SRE_SCOPE,
                          SRE_ORIGIN + "/api/v1/threads/" + investigation_id)
        incident_source = raw.get("incidentSource")
        status = raw.get("status")
        incident_status = status.get("incidentStatus", {}) if isinstance(status, dict) else {}
        if (
            raw.get("id") != investigation_id or raw.get("startMessageIsHuman", True) not in (False, None)
            or not isinstance(raw.get("source"), str) or raw["source"] in {"", "Conversation"}
            or not isinstance(incident_source, dict) or incident_source.get("incidentType") != "AzMonitor"
            or not isinstance(incident_status, dict) or not incident_status.get("incidentId")
            or not isinstance(raw.get("incidentDetails"), dict)
            or not raw["incidentDetails"] or not callable(self.native_decoder)
        ):
            raise CaseToolError("Authoritative operator-free native thread provenance is absent or its positive schema is unbound.")
        decoded = self.native_decoder(copy.deepcopy(raw))
        keys(decoded, {"platform", "alert_ids", "resource_id"})
        stamp(raw.get("createdTimestamp"))
        return {"id": raw["id"], "created_at": raw["createdTimestamp"], **decoded}

    def alert(self, alert_id):
        if not isinstance(alert_id, str) or not re.fullmatch(
            re.escape(WORKSPACE) + r"/providers/Microsoft\.AlertsManagement/alerts/[0-9a-f-]{36}", alert_id, re.I,
        ):
            raise CaseToolError("Only the fixed workspace-extension alert identity can be read.")
        canonical_uuid(alert_id.rsplit("/", 1)[1])
        # 2019-03-01 is the observed supported version; never use supplied links.
        raw = self._read("alert", "https://management.azure.com/.default",
                          "https://management.azure.com" + alert_id + "?api-version=2019-03-01&includeContext=true")
        essentials = raw["properties"]["essentials"]
        condition = raw["properties"]["context"]["context"]["condition"]
        clauses = condition["allOf"]
        legacy = (
            self.profile.name == "patch-installation" and isinstance(clauses, list) and len(clauses) == 1
            and " ".join(clauses[0].get("searchQuery", "").split()) in
            tuple(" ".join(query.split()) for query in LEGACY_PATCH_ALERT_QUERIES)
        )
        if (
            not isinstance(raw.get("id"), str) or raw["id"].lower() != alert_id.lower()
            or essentials["alertRule"].lower() != self.profile.rule_id.lower()
            or essentials["targetResource"].lower() != WORKSPACE.lower() or essentials["severity"] != self.profile.severity
            or essentials["monitorService"] != "Log Alerts V2" or essentials["signalType"] != "Log"
            or condition["windowSize"] != "PT10M" or not isinstance(clauses, list) or len(clauses) != 1
            or not legacy and " ".join(clauses[0]["searchQuery"].split()) != " ".join(self.profile.query.split())
            or clauses[0]["operator"] != "GreaterThan" or clauses[0]["threshold"] != "0"
            or clauses[0]["timeAggregation"] != "Count" or type(clauses[0]["metricValue"]) not in (int, float)
            or not math.isfinite(clauses[0]["metricValue"]) or clauses[0]["metricValue"] < 1
        ):
            raise CaseToolError("The actual ARM alert does not match its fixed profile rule and workspace.")
        result = {"id": alert_id, "rule_id": essentials["alertRule"], "fired_at": essentials["startDateTime"],
                "state": essentials["monitorCondition"], "severity": essentials["severity"],
                "window_start": condition["windowStartTime"], "window_end": condition["windowEndTime"]}
        if self.profile.name == "patch-installation":
            dimensions = clauses[0].get("dimensions")
            if legacy:
                if dimensions not in (None, []):
                    raise CaseToolError("A historical patch series must be undimensioned.")
                result["legacy_patch"] = True
            else:
                if not isinstance(dimensions, list) or len(dimensions) != 1:
                    raise CaseToolError("Exactly one trusted installation dimension is required.")
                keys(dimensions[0], {"name", "value"})
                if dimensions[0]["name"] != "installation_id":
                    raise CaseToolError("The trusted patch dimension has the wrong name.")
                try:
                    result["installation_id"] = patch.guid(dimensions[0]["value"])
                except patch.ProviderError:
                    raise CaseToolError("The trusted patch dimension is not a canonical installation GUID.") from None
        return result

    def disk(self, alert_id):
        alert = self.alert(alert_id)
        start = stamp(alert["window_start"]).isoformat()
        end = stamp(alert["window_end"]).isoformat()
        threshold = "" if self.profile.name == "capacity-growth" else "| where CounterValue >= 80 "
        query = (
            f"Perf | where TimeGenerated between (datetime({start}) .. datetime({end})) "
            f'| where _ResourceId =~ "{self.profile.resource_id}" '
            '| where ObjectName == "Logical Disk" and CounterName == "% Used Space" '
            '| where InstanceName == "/srv/asre-demo" '
            f'{threshold}'
            '| project resource_id=_ResourceId, observed_at=TimeGenerated, object_name=ObjectName, '
            'counter_name=CounterName, instance_name=InstanceName, value=CounterValue | sort by observed_at asc | take 101'
        )
        raw = _azure_json(self.credential, "https://api.loganalytics.io/.default", LOG_QUERY_URL,
                          {"query": query, "timespan": start + "/" + end})
        if "error" in raw or not isinstance(raw.get("tables"), list) or len(raw["tables"]) != 1:
            raise CaseToolError("Complete authoritative Perf evidence is unavailable or partial.")
        table = raw["tables"][0]
        names = [column["name"] for column in table["columns"]]
        if names != ["resource_id", "observed_at", "object_name", "counter_name", "instance_name", "value"] or not 1 <= len(table["rows"]) <= 100:
            raise CaseToolError("The bounded complete Perf sample shape is invalid.")
        if any(not isinstance(row, list) or len(row) != len(names) for row in table["rows"]):
            raise CaseToolError("A complete Perf row is required.")
        return {"alert_id": alert_id, "rule_id": self.profile.rule_id, "resource_id": self.profile.resource_id, "query": self.profile.query,
                "samples": [dict(zip(names, row)) for row in table["rows"]]}

    def receipt(self, request_id):
        canonical_uuid(request_id)
        return _azure_json(self.credential, "https://storage.azure.com/.default", RECEIPT_PREFIX + request_id + ".json")

    def signal(self, alert_id):
        if self.profile.name in {"disk", "alert-recurrence", "capacity-growth"}:
            return self.disk(alert_id)
        if self.profile.name == "patch-installation":
            return self.patch_signal(alert_id)
        if not self.profile.graph_query:
            raise CaseToolError("The authoritative signal reader is not bound.")
        alert = self.alert(alert_id)
        start = stamp(alert["window_start"]).isoformat()
        end = backup_evidence_end(alert["window_start"], alert["window_end"], alert["fired_at"]).isoformat()
        query = self.profile.graph_query.replace("ago(10m)", f"datetime({start})")
        query += f" | where observed_at <= datetime({end}) | take 101"
        raw = _azure_json(self.credential, "https://management.azure.com/.default", GRAPH_QUERY_URL, {
            "subscriptions": [self.profile.resource_id.split("/")[2]], "query": query,
            "options": {"resultFormat": "objectArray", "$top": 101},
        })
        if (
            raw.get("error") or raw.get("$skipToken") or raw.get("skipToken")
            or (raw.get("resultTruncated") is not False and raw.get("resultTruncated") != "false")
            or not isinstance(raw.get("data"), list) or not 1 <= len(raw["data"]) <= 100
            or type(raw.get("count")) is not int or raw["count"] != len(raw["data"])
            or type(raw.get("totalRecords")) is not int or raw["totalRecords"] != raw["count"]
        ):
            raise CaseToolError("Complete terminal provider evidence is unavailable or partial.")
        fields = {"resource_id", "observed_at", "job_id", "status", "protected_item_id", "error_details"}
        samples = []
        for row in raw["data"]:
            keys(row, fields, {"resourceGroup"})
            if "resourceGroup" in row and (
                not isinstance(row["resourceGroup"], str) or row["resourceGroup"].lower() != self.profile.resource_id.split("/")[4].lower()
            ):
                raise CaseToolError("The provider result belongs to another resource group.")
            sample = {name: row[name] for name in fields}
            sample["error_details"] = canonical_error_details(sample["error_details"])
            samples.append(sample)
        return {"alert_id": alert_id, "rule_id": self.profile.rule_id, "resource_id": self.profile.resource_id,
                "query": self.profile.query, "samples": samples}

    def patch_signal(self, alert_id):
        alert = self.alert(alert_id)
        if "installation_id" not in alert:
            raise CaseToolError("Historical patch queries cannot authorize fresh case evidence.")
        start, end = alert["window_start"], alert["window_end"]
        if patch.ticks(end) - patch.ticks(start) != 600 * 10000000:
            raise CaseToolError("The actual patch evaluation window must be ten minutes.")
        query = self.profile.query.replace("ago(10m)", f"datetime({start})").replace(
            "now()", f"datetime({end})",
        )
        query += f" | where installation_id == '{alert['installation_id']}' | take 101"
        raw = self._read("log-query", "https://api.loganalytics.io/.default", LOG_QUERY_URL, {
            "query": query, "timespan": start + "/" + end,
        })
        if ("error" in raw or "partialError" in raw or raw.get("nextLink") or raw.get("continuationToken")
                or not isinstance(raw.get("tables"), list) or len(raw["tables"]) != 1):
            self._invalid("log-query")
            raise CaseToolError("Complete patch console evidence is unavailable or partial.")
        table = raw["tables"][0]
        names = ["TimeGenerated", "resource_id", "observed_at", "installation_id",
                 "status", "failed_patch_count", "digest", "event_json"]
        if (not isinstance(table, dict) or not isinstance(table.get("columns"), list)
                or any(not isinstance(column, dict) or "name" not in column for column in table["columns"])
                or [column["name"] for column in table["columns"]] != names
                or not isinstance(table.get("rows"), list) or not 1 <= len(table["rows"]) <= 100):
            self._invalid("log-query")
            raise CaseToolError("The complete bounded patch console table is invalid.")
        samples, snapshots = [], {}
        try:
            for row in table["rows"]:
                if not isinstance(row, list) or len(row) != len(names):
                    self._invalid("log-query")
                    raise patch.ProviderError("A complete console row is required.")
                item = dict(zip(names, row))
                event = patch.decode(item["event_json"].encode("utf-8"), patch.LOG_LIMIT)
                # Validated at the captured native window, not re-dated now.
                patch.validate_event(event, end)
                if event["digest"] not in snapshots:
                    snapshots[event["digest"]] = self._provider(
                        "archive", lambda **options: patch.read_archive(self.credential, event["digest"], **options))
                value = snapshots[event["digest"]]
                if patch.event(value, event["digest"]) != event:
                    raise patch.ProviderError("The console event differs from its authenticated snapshot.")
                sample = patch.sample(value, event["digest"])
                patch.validate_sample(sample, alert["installation_id"], alert["window_start"], alert["window_end"])
                if (item["resource_id"] != patch.TARGET or item["installation_id"] != sample["installation_id"]
                        or item["status"] != sample["status"] or type(item["failed_patch_count"]) is not int
                        or item["failed_patch_count"] != sample["failed_patch_count"] or item["digest"] != sample["digest"]
                        or stamp(item["observed_at"]) != stamp(sample["observed_at"])
                        or stamp(item["TimeGenerated"]) != stamp(sample["observed_at"])):
                    raise patch.ProviderError("The console row does not match the selected immutable summary.")
                samples.append(sample)
        except (patch.ProviderError, AttributeError, UnicodeError):
            raise CaseToolError("Complete in-window authenticated installation evidence is required.") from None
        return {"alert_id": alert_id, "rule_id": self.profile.rule_id, "resource_id": self.profile.resource_id,
                "query": self.profile.query, "samples": samples,
                "current_provider_observation": self.patch_current(samples)}

    def patch_current(self, samples):
        """One bounded authenticated fixed Compute GET; no ARG or archive writes."""
        try:
            current = self._provider("compute", lambda **options: patch.observation(
                patch.read_compute(self.credential, **options), datetime.now(timezone.utc)))
            patch.corroborate(current, [sample["snapshot"] for sample in samples])
            return current
        except (patch.ProviderError, OSError, AttributeError, TypeError):
            raise CaseToolError("Current fixed Compute corroboration is unavailable or inconsistent; new writes are blocked.") from None


class BlobIntents:
    """Fixed Blob REST read/create; no optional sandbox SDK or retry."""

    def __init__(self, credential, opener=None, *, context=None):
        self.credential = credential
        self.opener = opener or build_opener(NoRedirect())
        self.context = context

    def _read_attempt(self, name, timeout):
        token = self.credential.get_token("https://storage.azure.com/.default").token
        request = Request(STORAGE + "/" + CONTAINER + "/" + checked_path(name), headers={
            "Authorization": "Bearer " + token, "x-ms-version": "2023-11-03",
            "x-ms-date": formatdate(usegmt=True)})
        try:
            with self.opener.open(request, timeout=self.context.checkpoint(timeout)) as response:
                self.context.checkpoint()
                raw = response.read(LIMIT + 1)
                self.context.checkpoint()
                if response.status != 200:
                    raise _InvalidRead()
            try:
                decode(raw)
            except CaseToolError:
                raise _InvalidRead() from None
            return raw
        except HTTPError as error:
            if error.code == 404:
                error.close()
                raise KeyError("Intent not found.") from None
            raise

    def _request(self, name, data=None):
        if data is None and self.context is not None and self.context.active:
            return self.context.request("intent-read", lambda timeout: self._read_attempt(name, timeout))
        url = STORAGE + "/" + CONTAINER + "/" + checked_path(name)
        method = "GET" if data is None else "PUT"
        try:
            token = self.credential.get_token("https://storage.azure.com/.default").token
            headers = {"Authorization": "Bearer " + token, "x-ms-version": "2023-11-03",
                       "x-ms-date": formatdate(usegmt=True)}
            if data is not None:
                headers.update({"If-None-Match": "*", "x-ms-blob-type": "BlockBlob",
                                "Content-Type": "application/json"})
            request = Request(url, data=data, headers=headers, method=method)
            with self.opener.open(request, timeout=20) as response:
                if response.status != (200 if data is None else 201):
                    raise CaseToolError("The fixed intent operation returned an unexpected status.")
                raw = response.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise CaseToolError("The fixed intent response exceeds its bound.")
                return raw
        except HTTPError as error:
            status = error.code
            error.close()
            if data is None and status == 404:
                raise KeyError("Intent not found.") from None
            raise CaseToolError("The fixed intent operation failed; read the original object before retry.") from None
        except Exception:
            raise CaseToolError("The fixed intent operation did not complete or validate.") from None

    def read(self, name):
        raw = self._request(name)
        decode(raw)
        return raw

    def create(self, name, data):
        if self.context is not None:
            self.context.close("intent-read")
        decode(data)
        self._request(name, data)

    def initial_paths(self):
        """One complete authenticated listing; never silently page/truncate."""
        url = STORAGE + "/" + CONTAINER + "?" + urlencode({
            "restype": "container", "comp": "list", "prefix": PREFIX, "maxresults": 257,
        })
        try:
            token = (self.credential.legacy_token("https://storage.azure.com/.default")
                     if isinstance(self.credential, _MeteredCredential)
                     else self.credential.get_token("https://storage.azure.com/.default")).token
            request = Request(url, headers={"Authorization": "Bearer " + token,
                              "x-ms-version": "2023-11-03", "x-ms-date": formatdate(usegmt=True)})
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(LIMIT + 1)
                if response.status != 200 or len(raw) > LIMIT:
                    raise ValueError()
            if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
                raise ValueError()
            root = ElementTree.fromstring(raw)
            blobs, marker = root.find("Blobs"), root.find("NextMarker")
            tags = [entry.tag for entry in root]
            if (root.tag != "EnumerationResults" or blobs is None or marker is None or marker.text
                    or len(tags) != len(set(tags))
                    or set(tags) - {"Prefix", "Marker", "MaxResults", "Blobs", "NextMarker"}):
                raise ValueError()
            entries = list(blobs)
            if len(entries) > 256 or any(entry.tag != "Blob" or len(entry.findall("Name")) != 1 for entry in entries):
                raise ValueError()
            names = [entry.findtext("Name") for entry in entries]
            if len(set(names)) != len(names):
                raise ValueError()
            for name in names:
                checked_path(name)
            initial = [name for name in names if name.endswith("/initial.json")]
            if len(initial) > 32:
                raise ValueError()
            return initial
        except (OSError, ValueError, ElementTree.ParseError, TypeError):
            raise CaseToolError("The complete bounded legacy intent scan failed; no new patch ownership is admitted.") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def managed_identity():
    from azure.identity import ManagedIdentityCredential
    return ManagedIdentityCredential(
        client_id=UAMI,
        connection_timeout=10, read_timeout=20, retry_total=0,
    )


def _secret_attempt(credential, *, timeout=20, context=None):
    """No list, caller-selected name, redirect, desktop or environment secret."""
    token = credential.get_token("https://vault.azure.net/.default").token
    request = Request(SECRET_URL, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    if context is not None:
        timeout = context.checkpoint(timeout)
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        if context is not None:
            context.checkpoint()
        raw = response.read(16385)
        if context is not None:
            context.checkpoint()
        status = response.status
    try:
        if status != 200 or len(raw) > 16384:
            raise ValueError()
        result = json.loads(raw)
        value = result.get("value")
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise _InvalidRead() from None


def exact_secret(credential, *, context=None):
    if context is not None and context.active:
        return context.request("secret", lambda timeout: _secret_attempt(
            credential, timeout=timeout, context=context))
    try:
        return _secret_attempt(credential)
    except Exception:
        raise CaseToolError("The fixed managed-identity credential reference is unavailable.") from None


class _FiniteWindow:
    """One invocation's nonrenewable budget, intersected with absolute expiry."""

    def __init__(self, config, clock, monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.lower, self.upper = stamp(config["not_before"]), stamp(config["expires_at"])
        self.wall, self.tick = clock(), monotonic()
        self.deadline = self.tick + (self.upper - self.wall).total_seconds()

    def remaining(self):
        wall, tick = self.clock(), self.monotonic()
        if (
            wall < self.wall or tick < self.tick or not math.isfinite(tick)
            or not self.lower <= wall < self.upper
        ):
            raise CaseToolError("Finite commissioning expired or its invocation clock moved backwards.")
        self.wall, self.tick = wall, tick
        remaining = min((self.upper - wall).total_seconds(), self.deadline - tick)
        if remaining <= 0:
            raise CaseToolError("Finite commissioning has no remaining submission authority.")
        return remaining

    def current(self):
        self.remaining()
        return self.wall


class CaseTool:
    def __init__(self, reader: Reader, commissioning: dict, intents: Intents, client: CaseClient, clock=None, policy=None,
                 *, monotonic=time.monotonic, profile="disk"):
        self.reader, self.commissioning, self.intents, self.client = reader, commissioning, intents, client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.contracts = Contracts()
        self.policy = commissioning_policy() if policy is None else copy.deepcopy(policy)
        validate_commissioning(self.commissioning, self.policy)
        if not isinstance(profile, str) or profile not in self.commissioning.get("profiles", ["disk"]):
            raise CaseToolError("The selected case profile is not enabled by protected commissioning.")
        self.profile = CASE_PROFILES[profile]
        if "finite_commissioning" in commissioning and profile != "disk":
            raise CaseToolError("Finite bootstrap remains limited to its original disk profile.")
        self.proof_key = "disk" if profile == "disk" else "signal"
        self.monotonic = monotonic
        self._finite_config = wire(commissioning) if "finite_commissioning" in commissioning else None

    def finite_configuration(self):
        if self._finite_config is not None:
            if wire(self.commissioning) != self._finite_config:
                raise CaseToolError("Finite protected configuration cannot move within a loaded tool.")
            return True
        if "finite_commissioning" in self.commissioning:
            raise CaseToolError("Finite protected configuration cannot be rebound within a loaded tool.")
        return False

    def reservation(self, plan):
        # Expiry/configuration/payload/episode are deliberately NOT key material:
        # changing them at the same normalized boundary must conflict, not mint
        # another allowance. The full original configuration is bound in content.
        scope = {
            "not_before": stamp(self.commissioning["not_before"]).isoformat(),
            "rule_id": self.profile.rule_id.lower(), "resource_id": self.profile.resource_id.lower(),
            "facade_origin": ORIGIN, "facade_client_id": FACADE_CLIENT,
        }
        name = PREFIX + "finite/" + str(uuid5(NAMESPACE, "finite/v1:" + wire(scope).decode())) + "/reservation.json"
        value = {
            "version": "1.0", "scope": scope,
            "expires_at": stamp(self.commissioning["expires_at"]).isoformat(),
            "configuration_fingerprint": hashlib.sha256(self._finite_config).hexdigest(),
            "episode_id": plan["episode_id"], "binding": plan["binding"],
            "fingerprints": plan["fingerprints"],
            "proof_fingerprint": hashlib.sha256(wire(plan["proof"])).hexdigest(),
        }
        return checked_path(name), wire(value)

    def check_reservation(self, plan, window, *, create=False):
        self.finite_configuration()
        window.remaining()
        if plan["recurrence_id"]:
            raise CaseToolError("Finite commissioning permits only the original initial intent.")
        name, expected = self.reservation(plan)
        if create:
            try:
                self.intents.create(name, expected)
            except Exception:
                pass  # Lost create/conflict is safe only with exact readback.
        try:
            actual = self.intents.read(name)
            decode(actual)
        except Exception:
            raise CaseToolError("Finite ownership is missing, malformed or uncertain.") from None
        if actual != expected:
            raise CaseToolError("Another owner or configuration already holds this finite boundary.")
        # Storage/authentication latency is part of, never added to, the budget.
        return window.remaining()

    def authority(self, investigation_id, alert_id, *, writing, recurrence=False, window=None):
        identifier(investigation_id)
        identifier(alert_id)
        investigation = self.reader.investigation(investigation_id)
        alert = self.reader.alert(alert_id)
        wire(investigation)
        wire(alert)
        keys(investigation, {"id", "platform", "created_at", "alert_ids", "resource_id"})
        keys(alert, {"id", "rule_id", "fired_at", "state", "severity", "window_start", "window_end"},
             {"installation_id", "legacy_patch"} if self.profile.name == "patch-installation" else ())
        if (
            investigation["id"] != investigation_id or investigation["platform"] != "AzMonitor"
            or investigation["resource_id"] != self.profile.resource_id
            or not isinstance(investigation["alert_ids"], list) or not 1 <= len(investigation["alert_ids"]) <= 100
            or alert_id not in investigation["alert_ids"]
            or alert["id"] != alert_id or alert["rule_id"].lower() != self.profile.rule_id.lower()
            or not re.fullmatch(
                re.escape(WORKSPACE) + r"/providers/Microsoft\.AlertsManagement/alerts/[0-9a-f-]{36}",
                alert_id, re.I,
            )
            or alert["severity"] != self.profile.severity
        ):
            raise CaseToolError("The native investigation and exact profile alert are not authoritatively associated.")
        canonical_uuid(alert_id.rsplit("/", 1)[1])
        created, fired = stamp(investigation["created_at"]), stamp(alert["fired_at"])
        start, end = stamp(alert["window_start"]), stamp(alert["window_end"])
        if not start < end <= fired or (end - start).total_seconds() != 600:
            raise CaseToolError("The native investigation or alert evaluation window is invalid.")
        if writing:
            validate_commissioning(self.commissioning, self.policy)
            lower, now = stamp(self.commissioning["not_before"]), window.current() if window else self.clock()
            upper = stamp(self.commissioning["expires_at"]) if "expires_at" in self.commissioning else None
            historical = self.commissioning["historical_alert_ids"]
            if (
                not isinstance(historical, list) or any(not isinstance(x, str) for x in historical)
                or not lower <= fired <= now or upper is not None and now >= upper
                or not lower <= created <= now or not 0 <= (now - fired).total_seconds() <= ALERT_FRESHNESS_SECONDS
                or alert_id in historical or alert["state"] not in {"Fired", "Resolved"}
            ):
                raise CaseToolError("The alert is historical, stale, paused or outside the trusted commissioning boundary.")
        binding = {
            "investigation_id": investigation_id, "alert_id": alert_id, "resource_id": self.profile.resource_id,
            "rule_id": self.profile.rule_id, "fired_at": alert["fired_at"],
            "window_start": alert["window_start"], "window_end": alert["window_end"],
        }
        if self.profile.name == "patch-installation" and "installation_id" in alert:
            try:
                binding["installation_id"] = patch.guid(alert["installation_id"])
                if patch.ticks(alert["window_end"]) - patch.ticks(alert["window_start"]) != 600 * 10000000:
                    raise patch.ProviderError("Invalid native patch window.")
            except patch.ProviderError:
                raise CaseToolError("The native installation dimension is invalid.") from None
        return binding

    def native_disk(self, binding, disk=None):
        disk = self.reader.disk(binding["alert_id"]) if disk is None else disk
        wire(disk)
        keys(disk, {"alert_id", "rule_id", "resource_id", "query", "samples"})
        compact = lambda text: " ".join(text.split())
        if (
            disk["alert_id"] != binding["alert_id"] or disk["rule_id"].lower() != self.profile.rule_id.lower()
            or disk["resource_id"] != self.profile.resource_id or not isinstance(disk["query"], str)
            or compact(disk["query"]) != compact(self.profile.query)
            or not isinstance(disk["samples"], list) or not 1 <= len(disk["samples"]) <= 100
        ):
            raise CaseToolError("The exact disk rule query and approved VM association are unverified.")
        for sample in disk["samples"]:
            keys(sample, {"resource_id", "observed_at", "object_name", "counter_name", "instance_name", "value"})
            value = sample["value"]
            if (
                sample["resource_id"].lower() != self.profile.resource_id.lower()
                or sample["object_name"] != "Logical Disk" or sample["counter_name"] != "% Used Space"
                or sample["instance_name"] != "/srv/asre-demo" or type(value) not in (int, float)
                or not math.isfinite(value) or not 80 <= value <= 100
                or not stamp(binding["window_start"]) <= stamp(sample["observed_at"]) <= stamp(binding["window_end"])
            ):
                raise CaseToolError("Complete in-window disk evidence does not establish the approved threshold.")
        return disk

    def native_context(self, binding):
        signal = self.native_signal(binding)
        if self.profile.name == "patch-installation":
            return _patch_native_display(binding, signal, self._patch_observation)
        growth = self.profile.name == "capacity-growth"
        result = {"status": "native_evidence", "binding": binding, self.proof_key: {
            "samples": [signal["samples"][0], signal["samples"][-1]] if growth else signal["samples"][:1],
            "sample_count": len(signal["samples"]),
            "reference": self.profile.telemetry_reference,
        }}
        if growth:
            result[self.proof_key]["forecast"] = growth_forecast(signal["samples"])
        if self.profile.name == "backup-job":
            result[self.proof_key]["evidence_window"] = {"start": binding["window_start"], "end": binding["fired_at"]}
        return result

    def native_signal(self, binding, signal=None, *, legacy=False):
        if self.profile.name in {"disk", "alert-recurrence"}:
            return self.native_disk(binding, signal)
        live_patch = signal is None and self.profile.name == "patch-installation" and not legacy
        current = None
        if signal is None:
            signal = copy.deepcopy(self.reader.signal(binding["alert_id"]))
            if live_patch:
                keys(signal, {"alert_id", "rule_id", "resource_id", "query", "samples", "current_provider_observation"})
                current = signal.pop("current_provider_observation")
                wire(current)
        wire(signal)
        keys(signal, {"alert_id", "rule_id", "resource_id", "query", "samples"})
        if (
            signal["alert_id"] != binding["alert_id"] or signal["rule_id"].lower() != self.profile.rule_id.lower()
            or signal["resource_id"] != self.profile.resource_id
            or signal["query"] not in (LEGACY_PATCH_ALERT_QUERIES if legacy else (self.profile.query,))
            or not isinstance(signal["samples"], list) or not 1 <= len(signal["samples"]) <= 100
        ):
            raise CaseToolError("The exact profile signal and approved VM association are unverified.")
        for sample in signal["samples"]:
            if self.profile.name == "capacity-growth":
                keys(sample, {"resource_id", "observed_at", "object_name", "counter_name", "instance_name", "value"})
                if (
                    not isinstance(sample["resource_id"], str) or sample["resource_id"].lower() != self.profile.resource_id.lower()
                    or sample["object_name"] != "Logical Disk" or sample["counter_name"] != "% Used Space"
                    or sample["instance_name"] != "/srv/asre-demo" or type(sample["value"]) not in (int, float)
                    or not math.isfinite(sample["value"]) or not 0 <= sample["value"] < 80
                    or not stamp(binding["window_start"]) <= stamp(sample["observed_at"]) <= stamp(binding["window_end"])
                ):
                    raise CaseToolError("Complete pre-breach filesystem samples are required.")
                continue
            if self.profile.name == "backup-job":
                keys(sample, {"resource_id", "observed_at", "job_id", "status", "protected_item_id", "error_details"})
                if (
                    not isinstance(sample["resource_id"], str) or sample["resource_id"].lower() != BACKUP_RESOURCE.lower()
                    or not isinstance(sample["job_id"], str)
                    or not sample["job_id"].lower().startswith(BACKUP_VAULT.lower() + "/backupjobs/")
                    or sample["status"] != "Failed" or not isinstance(sample["protected_item_id"], str)
                    or sample["protected_item_id"].lower() != BACKUP_ITEM.lower()
                    or not isinstance(sample["error_details"], str) or len(sample["error_details"]) > 1600
                    or not stamp(binding["window_start"]) <= stamp(sample["observed_at"]) <= backup_evidence_end(
                        binding["window_start"], binding["window_end"], binding["fired_at"])
                ):
                    raise CaseToolError("A real failed Backup job for the exact protected item is required.")
                canonical_uuid(sample["job_id"][len(BACKUP_VAULT + "/backupJobs/"):])
                continue
            if not legacy:
                try:
                    patch.validate_sample(sample, binding.get("installation_id"),
                                          binding["window_start"], binding["window_end"])
                except patch.ProviderError:
                    raise CaseToolError("Complete in-window terminal installation failure is required.") from None
                continue
            keys(sample, {"resource_id", "observed_at", "installation_id", "status", "failed_patch_count", "error_details"})
            if (
                not isinstance(sample["resource_id"], str)
                or sample["resource_id"].lower() != self.profile.resource_id.lower()
                or not isinstance(sample["installation_id"], str)
                or not sample["installation_id"].lower().startswith(self.profile.resource_id.lower() + "/patchinstallationresults/")
                or sample["status"] not in {"Failed", "CompletedWithWarnings"}
                or type(sample["failed_patch_count"]) is not int or sample["failed_patch_count"] < 1
                or not isinstance(sample["error_details"], str) or len(sample["error_details"]) > 1600
                or not stamp(binding["window_start"]) <= stamp(sample["observed_at"]) <= stamp(binding["window_end"])
            ):
                raise CaseToolError("Complete in-window terminal installation failure is required.")
            canonical_uuid(sample["installation_id"].rsplit("/", 1)[1])
        if self.profile.name == "capacity-growth":
            growth_forecast(signal["samples"])
        if live_patch:
            try:
                patch.corroborate(current, [row["snapshot"] for row in signal["samples"]])
            except patch.ProviderError:
                raise CaseToolError("The independent current Compute observation is invalid.") from None
            self._patch_observation = current
        return signal

    def proof(self, binding, evidence, supplied=None, window=None, *, legacy=False):
        signal = self.native_signal(binding, None if supplied is None else supplied[self.proof_key], legacy=legacy)
        receipts = {}
        has_alert = False
        has_native_telemetry = False
        if not 2 <= len(evidence["evidence"]) <= 5:
            raise CaseToolError("The case requires an alert and complete native telemetry or Review receipts.")
        for item in evidence["evidence"]:
            reference = item["reference"]
            if reference == binding["alert_id"] and item["source"] == "AzureMonitor":
                if has_alert:
                    raise CaseToolError("Duplicate alert evidence is not admitted.")
                has_alert = True
                if stamp(item["observed_at"]) != stamp(binding["fired_at"]):
                    raise CaseToolError("The alert evidence timestamp differs from the authoritative firing.")
                continue
            telemetry_reference = GRAPH_QUERY_URL if legacy else self.profile.telemetry_reference
            if reference == telemetry_reference and item["source"] == self.profile.telemetry_source:
                if has_native_telemetry or not any(
                    stamp(item["observed_at"]) == stamp(sample["observed_at"]) for sample in signal["samples"]
                ):
                    raise CaseToolError("Native telemetry must reference an independently retrieved in-window sample.")
                has_native_telemetry = True
                continue
            if not reference.startswith(RECEIPT_PREFIX) or not reference.endswith(".json"):
                raise CaseToolError("Evidence references must be exact authoritative alert or immutable Review receipts.")
            request = canonical_uuid(reference[len(RECEIPT_PREFIX):-5])
            if request in receipts:
                raise CaseToolError("Duplicate receipt evidence is not admitted.")
            receipt = self.reader.receipt(request) if supplied is None else supplied["receipts"][request]
            wire(receipt)
            keys(receipt, {"request_id", "resource_id", "mode", "command", "status", "observed_at", "fingerprint", "data"})
            source = {"inspect": "GuestInspection", "telemetry": "AzureMonitor"}.get(receipt["command"])
            if (
                receipt["request_id"] != request or receipt["resource_id"].lower() != self.profile.resource_id.lower()
                or receipt["mode"] != "Review" or receipt["status"] != "completed"
                or source is None or source != item["source"] or not isinstance(receipt["data"], dict) or not receipt["data"]
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["fingerprint"])
                or stamp(receipt["observed_at"]) != stamp(item["observed_at"])
                or not stamp(binding["fired_at"]) <= stamp(receipt["observed_at"]) <= (window.current() if window else self.clock())
            ):
                raise CaseToolError("The complete authoritative Review receipt does not match its case evidence.")
            receipts[request] = receipt
        if not has_alert or not (receipts or has_native_telemetry):
            raise CaseToolError("Both authoritative alert and complete diagnostic evidence are required.")
        if supplied is not None and set(supplied["receipts"]) != set(receipts):
            raise CaseToolError("Every complete frozen receipt must be referenced exactly.")
        return {self.proof_key: signal, "receipts": receipts}

    def payloads(self, episode, binding, evidence, note, recurrence=""):
        self.contracts.validate("servicenow-case", evidence)
        if (
            evidence["episode_id"] != episode or evidence["resource_id"] != self.profile.resource_id
            or evidence["source_event_ids"] != [binding["alert_id"]]
            or stamp(evidence["detected_at"]) != stamp(binding["fired_at"])
            or evidence["mode"] != self.profile.mode or evidence["signal_family"] != self.profile.signal_family
            or "case_reference" in evidence or evidence["actions"]
            or evidence["verification"] != {"status": "not_started", "recovery_owner": "none"}
        ):
            raise CaseToolError("Only evidence-backed profile findings without repair or recovery claims are admitted.")
        create = {
            "transport_version": "1.0", "write_request_id": str(uuid5(UUID(episode), "create/v1")),
            "evidence": evidence, "investigation_reference": binding["investigation_id"],
        }
        note_payload = {
            "transport_version": "1.0", "write_request_id": str(uuid5(UUID(episode), "recurrence:" + recurrence if recurrence else "initial-note/v1")),
            "note": note, "evidence": evidence,
        }
        self.contracts.validate("servicenow-case-write", create)
        self.contracts.validate("servicenow-note-write", note_payload)
        # Same bound/sensitive-content rule as the retained client, before any intent.
        for payload in (create, note_payload):
            if len(wire(payload)) > 48000:
                raise CaseToolError("The canonical facade payload exceeds its transport bound.")
        return {"note": note_payload} if recurrence else {"create": create, "note": note_payload}

    def validate_intent(self, value, episode, recurrence, window=None):
        keys(value, {"version", "episode_id", "recurrence_id", "binding", "proof", "payloads", "fingerprints"})
        binding = value["binding"]
        v2 = type(value["version"]) is int and value["version"] == 2
        keys(binding, {"investigation_id", "alert_id", "resource_id", "rule_id", "fired_at", "window_start", "window_end"}
             | ({"installation_id"} if v2 else set()))
        if v2 and (self.profile.name != "patch-installation" or recurrence):
            raise CaseToolError("Version two ownership is restricted to initial patch installations.")
        try:
            expected_episode = patch.episode(binding["installation_id"]) if v2 else episode_for(binding["investigation_id"])
        except patch.ProviderError:
            raise CaseToolError("The patch installation ownership identity is invalid.") from None
        if (
            type(value["version"]) is not int or value["version"] not in (1, 2) or value["episode_id"] != episode
            or expected_episode != episode or value["recurrence_id"] != recurrence
            or binding["resource_id"] != self.profile.resource_id or binding["rule_id"] != self.profile.rule_id
            or recurrence and binding["alert_id"] != recurrence
        ):
            raise CaseToolError("The immutable intent identity differs from its owned native investigation.")
        if (stamp(binding["window_end"]) - stamp(binding["window_start"])).total_seconds() != 600:
            raise CaseToolError("The frozen native evaluation window is invalid.")
        if v2 and patch.ticks(binding["window_end"]) - patch.ticks(binding["window_start"]) != 600 * 10000000:
            raise CaseToolError("The frozen installation window is not exactly ten minutes.")
        keys(value["payloads"], {"note"} if recurrence else {"create", "note"})
        note = value["payloads"]["note"]
        expected = self.payloads(episode, binding, note["evidence"], note["note"], recurrence)
        fingerprints = {name: hashlib.sha256(wire(payload)).hexdigest() for name, payload in expected.items()}
        if value["payloads"] != expected or value["fingerprints"] != fingerprints:
            raise CaseToolError("The frozen payload, fingerprint or original write identity has changed.")
        # Re-read exact proof through the trusted interface, never accept a model URI
        # or fragment. Persisted proofs must also be complete, not a mutable ledger.
        keys(value["proof"], {self.proof_key, "receipts"})
        if not isinstance(value["proof"][self.proof_key], dict) or not isinstance(value["proof"]["receipts"], dict):
            raise CaseToolError("The immutable complete evidence proof is invalid.")
        self.proof(binding, note["evidence"], value["proof"], window,
                   legacy=self.profile.name == "patch-installation" and not v2)
        wire(value)
        return value

    def load(self, episode, recurrence="", window=None):
        value = decode(self.intents.read(intent_path(episode, recurrence)))
        return self.validate_intent(value, episode, recurrence, window)

    def freeze(self, episode, binding, evidence_json, note, recurrence, window=None):
        evidence = decode(evidence_json)
        if "episode_id" in evidence:
            raise CaseToolError("The episode identity is derived internally, never supplied by the caller.")
        evidence["episode_id"] = episode
        payloads = self.payloads(episode, binding, evidence, note, recurrence)
        value = {
            "version": 2 if self.profile.name == "patch-installation" else 1,
            "episode_id": episode, "recurrence_id": recurrence, "binding": binding,
            "proof": self.proof(binding, evidence, window=window), "payloads": payloads,
            "fingerprints": {name: hashlib.sha256(wire(payload)).hexdigest() for name, payload in payloads.items()},
        }
        self.validate_intent(value, episode, recurrence, window)
        if value["version"] == 2:
            self.patch_write_budget(value)
        if self.finite_configuration():
            if window is None:
                raise CaseToolError("Finite intent creation requires an invocation budget.")
            self.check_reservation(value, window, create=True)
        try:
            self.intents.create(intent_path(episode, recurrence), wire(value))
        except CaseDiagnostic:
            raise
        except Exception:
            pass  # Whether 409/412 or lost response, only readback can proceed.
        try:
            actual = self.load(episode, recurrence, window)
        except KeyError:
            raise CaseToolError("Intent creation is uncertain; no facade write is authorized.") from None
        if wire(actual) != wire(value):
            if value["version"] == 2:
                return actual  # Atomic complete owner wins, including its proof.
            raise CaseToolError("Another immutable payload already owns this native investigation or recurrence.")
        return actual

    def legacy_patch_owner(self, installation_id):
        """Admission barrier only. Delivery must first exclude legacy writers.

        Bounded scans cannot serialize old writers against new ones; the release
        gate must establish no eligible legacy writers before enabling v2.
        """
        try:
            names = self.intents.initial_paths()
            if not isinstance(names, list) or len(names) > 32 or len(set(names)) != len(names):
                raise CaseToolError("The legacy ownership listing is incomplete.")
            owners = []
            for name in names:
                checked_path(name)
                if not name.endswith("/initial.json"):
                    raise CaseToolError("The legacy scan returned a non-initial intent.")
                value = decode(self.intents.read(name))
                keys(value, {"version", "episode_id", "recurrence_id", "binding", "proof", "payloads", "fingerprints"})
                binding = value["binding"]
                if not isinstance(binding, dict) or type(value["version"]) is not int or value["version"] not in (1, 2):
                    raise CaseToolError("The legacy scan encountered an invalid owned intent.")
                if intent_path(value["episode_id"]) != name or value["recurrence_id"]:
                    raise CaseToolError("The legacy scan encountered a conflicting intent identity.")
                profiles = [p for p in CASE_PROFILES.values() if
                            p.resource_id == binding.get("resource_id") and p.rule_id == binding.get("rule_id")]
                if len(profiles) != 1:
                    raise CaseToolError("The legacy scan encountered an unbound profile.")
                # Validate even other-profile records, not just a best-effort
                # filter of potentially corrupt ownership state.
                validator = self
                if profiles[0] != self.profile:
                    config = copy.deepcopy(self.commissioning)
                    config["profiles"] = list(CASE_PROFILES)
                    validator = CaseTool(self.reader, config, self.intents, self.client, self.clock,
                                         self.policy, profile=profiles[0].name)
                validator.validate_intent(value, value["episode_id"], "")
                if profiles[0] == self.profile and value["version"] == 1:
                    installations = {patch.guid(row["installation_id"].rsplit("/", 1)[1])
                                     for row in value["proof"]["signal"]["samples"]}
                    if installation_id in installations:
                        owners.append(value)
            if len(owners) > 1:
                raise CaseToolError("Multiple legacy owners make patch ownership ambiguous.")
            return owners[0] if owners else None
        except (KeyError, OSError, AttributeError, TypeError, patch.ProviderError):
            raise CaseToolError("The complete legacy ownership scan failed; no patch write is authorized.") from None

    def patch_result(self, plan, *, writing=False, owned=False):
        """Only original IDs, even for conflicts, restarts or uncertain sends."""
        observation = self._patch_observation
        output = {"status": "owned" if owned else "pending", "episode_id": plan["episode_id"],
                  "binding": plan["binding"]}

        def before_submit():
            nonlocal observation
            samples = plan["proof"]["signal"]["samples"]
            if observation is None:
                observation = self.reader.patch_current(samples)
            wire(observation)
            try:
                relationship = patch.corroborate(observation, [row["snapshot"] for row in samples])
            except patch.ProviderError:
                raise CaseToolError("Current Compute corroboration does not admit a new submission.") from None
            # Invocation-local observation only, never payloads/frozen proof.
            output["current_provider_observation"] = {**copy.deepcopy(observation), "relationship": relationship}
            return self.patch_write_budget(plan)  # After evidence and authentication latency.

        create = self.deliver(plan, "create", writing=writing, before_submit=before_submit)
        output["create"] = create
        if create["outcome"] not in TERMINAL:
            return output

        case = self.client.read_case(plan["episode_id"], self.profile.resource_id, create["case_reference"])
        note = self.deliver(plan, "note", writing=writing, case=case, before_submit=before_submit)
        output["note"] = note
        if note["outcome"] in TERMINAL:
            if not owned:
                output["status"] = "persisted"
            output["url"] = ORIGIN + "/incident.do?sys_id=" + case.reference["sys_id"]
        return output

    def patch_write_budget(self, plan):
        """Listing/archive/authentication latency never renews fresh authority."""
        validate_commissioning(self.commissioning, self.policy)
        now = self.clock()
        lower = stamp(self.commissioning["not_before"])
        fired = stamp(plan["binding"]["fired_at"])
        remaining = ALERT_FRESHNESS_SECONDS - (now - fired).total_seconds()
        if not lower <= fired <= now or now < lower:
            raise CaseToolError("Patch submission is outside its original commissioning authority.")
        if "expires_at" in self.commissioning:
            remaining = min(remaining, (stamp(self.commissioning["expires_at"]) - now).total_seconds())
        if remaining <= 0 or plan["binding"]["alert_id"] in self.commissioning["historical_alert_ids"]:
            raise CaseToolError("Patch submission expired while reading evidence or authenticating.")
        return remaining

    def run_patch(self, action, binding, evidence_json, findings_note, recurrence_id):
        self._patch_observation = None  # Never reuse a previous invocation's observation.
        if recurrence_id:
            raise CaseToolError("Patch installations own initial cases, not investigation recurrence writes.")
        installation = binding.get("installation_id")
        if installation is None:
            # Recognition of the historical query permits only original intent
            # reads/reconciliation. It can never create an old-query case.
            try:
                plan = self.load(episode_for(binding["investigation_id"]))
            except KeyError:
                raise CaseToolError("Historical undimensioned patch series cannot create a fresh case.") from None
            if plan["binding"] != binding or plan["version"] != 1:
                raise CaseToolError("The historical native alert differs from its frozen owner.")
            return self.patch_result(plan, owned=action in {"create", "note"})
        episode = patch.episode(installation)
        try:
            plan = self.load(episode)
        except KeyError:
            if action == "read":
                return self.native_context(binding)
            if action != "create" or not evidence_json:
                raise CaseToolError("A complete initial installation intent is required before delivery.") from None
            # Validate the actual complete evidence before ownership admission.
            legacy = self.legacy_patch_owner(installation)
            if legacy is not None:
                return self.patch_result(legacy, owned=True)
            plan = self.freeze(episode, binding, evidence_json, findings_note, "")
        owned = plan["binding"] != binding
        if evidence_json:
            evidence = decode(evidence_json)
            if "episode_id" in evidence:
                raise CaseToolError("Caller-supplied episode identities are not admitted.")
            evidence["episode_id"] = episode
            owned = owned or self.payloads(episode, binding, evidence, findings_note) != plan["payloads"]
        # The complete proof is frozen in the atomic owner. Later provider
        # updates do not replace it or mint a new installation episode.
        return self.patch_result(plan, writing=action in {"create", "note"} and not owned, owned=owned)

    def receipt(self, plan, name, result, reference=None):
        self.contracts.validate("servicenow-adapter-response", result, definition="write")
        if result["episode_id"] != plan["episode_id"] or result["write_request_id"] != plan["payloads"][name]["write_request_id"]:
            raise CaseToolError("The facade receipt differs from the frozen original write identity.")
        if result["outcome"] in TERMINAL:
            if reference is not None and result["case_reference"] != reference:
                raise CaseToolError("The facade receipt differs from the immutable owned case.")
            if name == "note" and not re.fullmatch(r"[0-9a-f]{32}", result.get("journal_sys_id", "")):
                raise CaseToolError("A findings note requires its persisted journal identity.")
        return {key: result[key] for key in ("outcome", "episode_id", "write_request_id", "case_reference", "journal_sys_id") if key in result}

    def deliver(self, plan, name, *, writing, case=None, window=None, before_submit=None):
        if writing and self.finite_configuration() and window is None:
            raise CaseToolError("Finite delivery requires the original invocation budget.")
        result = self.client.reconcile(plan["episode_id"], plan["payloads"][name]["write_request_id"])
        safe = self.receipt(plan, name, result, case.reference if case else None)
        # No blind resend on unknown. Explicit not_applied from the facade is the
        # authority to attempt exactly the original frozen ID and identical bytes.
        if writing and result["outcome"] == "not_applied":
            options = {"before_submit": lambda: self.check_reservation(plan, window)} if window is not None else {}
            if self.profile.name == "patch-installation" and plan["version"] == 2:
                if before_submit is None:
                    raise CaseToolError("A new installation submission requires current Compute corroboration.")
                options = {"before_submit": before_submit}
            try:
                result = (
                    self.client.create_case(copy.deepcopy(plan["payloads"][name]), **options) if name == "create"
                    else self.client.append_note(case, copy.deepcopy(plan["payloads"][name]), **options)
                )
            except AmbiguousWriteError:
                result = self.client.reconcile(plan["episode_id"], plan["payloads"][name]["write_request_id"])
            safe = self.receipt(plan, name, result, case.reference if case else None)
        return safe

    def run(self, action, investigation_id="", alert_id="", evidence_json="", findings_note="", recurrence_id=""):
        if action not in {"health", "read", "create", "note", "reconcile"}:
            raise CaseToolError("Only fixed facade health, read, create, internal note and reconcile are admitted.")
        if not all(isinstance(x, str) for x in (investigation_id, alert_id, evidence_json, findings_note, recurrence_id)):
            raise CaseToolError("Tool arguments must be bounded strings.")
        if action == "health":
            if any((investigation_id, alert_id, evidence_json, findings_note, recurrence_id)):
                raise CaseToolError("Health accepts no case or content arguments.")
            self.client.health()
            return {"status": "healthy", "mode": "Review", "origin": ORIGIN}
        if action in {"read", "reconcile"} and (evidence_json or findings_note) or action in {"read", "create"} and recurrence_id:
            raise CaseToolError("Content and recurrence arguments are not admitted for this operation.")
        if bool(evidence_json) != bool(findings_note) or action == "note" and not recurrence_id and evidence_json:
            raise CaseToolError("Complete evidence and findings are paired and cannot replace the frozen initial note.")
        writing = action in {"create", "note"}
        finite = self.finite_configuration()
        if finite and recurrence_id:
            raise CaseToolError("Finite commissioning does not admit recurrence.")
        window = _FiniteWindow(self.commissioning, self.clock, self.monotonic) if finite and writing else None
        if window is not None:
            window.remaining()
        if action == "read" and not investigation_id:
            if alert_id:
                raise CaseToolError("Explicit alert identity requires its native investigation identity.")
            investigation_id = self.reader.current_investigation(
                stamp(self.commissioning["not_before"]), self.clock(), self.commissioning["historical_alert_ids"],
            )
        if action == "read" and not alert_id:
            identifier(investigation_id)
            native = self.reader.investigation(investigation_id)
            if not isinstance(native.get("alert_ids"), list) or not native["alert_ids"]:
                raise CaseToolError("The native current alert identity is unavailable.")
            alert_id = native["alert_ids"][0]
        binding = self.authority(investigation_id, alert_id, writing=writing and not recurrence_id, window=window)
        if self.profile.name == "patch-installation":
            return self.run_patch(action, binding, evidence_json, findings_note, recurrence_id)
        episode = episode_for(binding["investigation_id"])
        if recurrence_id:
            identifier(recurrence_id)
            if recurrence_id == alert_id or action not in {"note", "reconcile"}:
                raise CaseToolError("Recurrence must be a distinct alert within the same owned investigation.")
            initial = self.load(episode)
            if initial["binding"] != binding:
                raise CaseToolError("The recurrence does not belong to the original owned episode.")
            binding = self.authority(investigation_id, recurrence_id, writing=writing, recurrence=True)
        try:
            plan = self.load(episode, recurrence_id, window)
        except KeyError:
            if action == "read":
                return self.native_context(binding)
            if not writing or not evidence_json:
                raise CaseToolError("No immutable intent exists; complete evidence is required before first write.") from None
            plan = self.freeze(episode, binding, evidence_json, findings_note, recurrence_id, window)
        if plan["binding"] != binding:
            if action == "read":
                result = self.native_context(binding)
                result["original_alert_id"] = plan["binding"]["alert_id"]
                return result
            raise CaseToolError("Authoritative native alert identity differs from the frozen intent.")
        if evidence_json:
            evidence = decode(evidence_json)
            if "episode_id" in evidence:
                raise CaseToolError("Caller-supplied episode identities are not admitted.")
            evidence["episode_id"] = episode
            if self.payloads(episode, binding, evidence, findings_note, recurrence_id) != plan["payloads"]:
                raise CaseToolError("Changed payloads cannot replace an immutable original intent.")
        if writing:
            # A stored intent is not authority by itself: verify original evidence
            # against complete immutable receipts and current native association.
            current = self.proof(binding, plan["payloads"]["note"]["evidence"], window=window)
            frozen = plan["proof"]
            if (
                current["receipts"] != frozen["receipts"]
                or {k: v for k, v in current[self.proof_key].items() if k != "samples"}
                != {k: v for k, v in frozen[self.proof_key].items() if k != "samples"}
                or not {wire(sample) for sample in frozen[self.proof_key]["samples"]}
                <= {wire(sample) for sample in current[self.proof_key]["samples"]}
            ):
                raise CaseToolError("The authoritative complete evidence differs from the frozen proof.")
            if window is not None:
                self.check_reservation(plan, window)
        if action == "read":
            case = self.client.read_case(episode, self.profile.resource_id)
            return {"status": "persisted_case", "episode_id": episode, "case_reference": case.reference,
                    "url": ORIGIN + "/incident.do?sys_id=" + case.reference["sys_id"]}
        create_plan = self.load(episode) if recurrence_id else plan
        create = self.deliver(create_plan, "create", writing=writing and not recurrence_id, window=window)
        output = {"episode_id": episode, "create": create, "status": "pending"}
        if create["outcome"] not in TERMINAL:
            return output
        case = self.client.read_case(episode, self.profile.resource_id, create["case_reference"])
        note = self.deliver(plan, "note", writing=writing, case=case, window=window)
        output["note"] = note
        if note["outcome"] in TERMINAL:
            output["status"] = "persisted"
            output["url"] = ORIGIN + "/incident.do?sys_id=" + case.reference["sys_id"]
        return output


_frozen_configuration = None
_configuration_frozen = False


def freeze_runtime_configuration(config):
    """Generated-package bootstrap only; null is an intentionally disabled build."""
    global _frozen_configuration, _configuration_frozen
    if config is not None:
        validate_commissioning(config, commissioning_policy())
    if _configuration_frozen and config != _frozen_configuration:
        raise CaseToolError("The commissioning configuration cannot move within an identical frozen runtime.")
    _frozen_configuration, _configuration_frozen = copy.deepcopy(config), True


def main(action: str, investigation_id: str = "", alert_id: str = "", evidence_json: str = "",
         findings_note: str = "", recurrence_id: str = "", profile: str = "disk") -> dict:
    context = None
    try:
        if action not in {"health", "read", "create", "note", "reconcile"} or not all(
            isinstance(value, str) for value in (investigation_id, alert_id, evidence_json, findings_note, recurrence_id, profile)
        ):
            raise CaseToolError("Only bounded fixed case-tool arguments are admitted.")
        if profile not in CASE_PROFILES:
            raise CaseToolError("The selected case profile is not supported.")
        selected = CASE_PROFILES[profile]
        if action == "health":
            if any((investigation_id, alert_id, evidence_json, findings_note, recurrence_id)):
                raise CaseToolError("Health accepts no case or content arguments.")
        elif not _configuration_frozen or _frozen_configuration is None:
            raise CaseToolError("Protected commissioning configuration is absent; case operations are disabled.")
        if profile == "patch-installation" and action in {"read", "create"}:
            context = _patch_context(action)
            _install_sdk_log_gate()
        credential = managed_identity()
        if context is not None:
            credential = _MeteredCredential(credential, context)
        try:
            decoder = decode_native_incident if profile == "disk" else lambda raw: decode_native_incident(raw, selected)
            options = {"context": context} if context is not None else {}
            reader = AzureReader(credential, decoder, profile=selected, **options)
            provider = ClientCredentials(lambda: exact_secret(credential, **options), client_id=FACADE_CLIENT)
            client = CaseClient(provider.token)
            if context is not None and action == "create":
                client = _PrewriteClient(client, context)
            if action == "health":
                # Readiness of facade auth is independent of native-positive DTO
                # discovery and never authorizes a ticket or proves write RBAC.
                client.health()
                return {"status": "healthy", "mode": "Review", "origin": ORIGIN,
                        "scope": "facade_auth_only", "case_writes_ready": False}
            result = CaseTool(reader, copy.deepcopy(_frozen_configuration), BlobIntents(credential, **options), client, profile=profile).run(
                action, investigation_id, alert_id, evidence_json, findings_note, recurrence_id,
            )
            if context is not None and context.active:
                context.checkpoint()
            return result
        finally:
            credential.close()
    except CaseDiagnostic as diagnostic:
        if context is not None and context.active:
            if action == "create":
                diagnostic = CaseDiagnostic(diagnostic.stage, diagnostic.reason, False, diagnostic.attempts)
            return diagnostic.result()
    except Exception:
        pass
    # No input, SDK/HTTP exception, token, remote message or traceback escapes.
    return {"status": "blocked", "message": "Fixed case operation unavailable or rejected; preserve original investigation/write IDs and use reconciliation. Protected commissioning, native provenance and access must be verified."}
