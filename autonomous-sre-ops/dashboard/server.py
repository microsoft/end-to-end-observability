"""Serve the self-contained page and a closed Azure read adapter."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import AzureCliCredential, CredentialUnavailableError, ManagedIdentityCredential

from dashboard import context as deployment
from dashboard.journey import build_journey, catalog
from dashboard.queries import (
    AGENT_ID, BACKUP_VAULT, LINUX_ID, PATCH_ID, QUERIES, RULE_IDS, RULE_NAMES,
    TENANT, WINDOWS, WORKSPACE_ID, query_for,
)

CTX = deployment.load()

LOG = logging.getLogger("asre.dashboard")
ARM = "https://management.azure.com"
ARM_SCOPE = ARM + "/.default"
SRE_SCOPE = "https://azuresre.dev/.default"
LOG_SCOPE = "https://api.loganalytics.io/.default"
LOG_URL = "https://api.loganalytics.io/v1/workspaces/"
MAX_RESPONSE = 4 * 1024 * 1024
ARM_READS = {
    "agent": ARM + AGENT_ID + "?api-version=2026-01-01",
    "workspace": ARM + WORKSPACE_ID + "?api-version=2023-09-01",
    **{
        name: ARM + resource + "?api-version=2023-12-01"
        for name, resource in zip(RULE_NAMES, RULE_IDS)
    },
}
NATIVE_READS = {
    "threads": "/api/v1/threads",
    "intake": "/api/v2/incidentManagement/incidentFilters",
    "schedule": "/api/v1/scheduledtasks",
    "logs": "/api/v2/extendedAgent/connectors/asre-operations-logs/status",
    "monitor": "/api/v2/extendedAgent/connectors/monitor-client/status",
}
UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
QUERY_COLUMNS = {
    "filesystem": {"bucket", "resource_id", "mount", "sample_count", "used_percent",
                   "min_used", "max_used", "latest_sample"},
    "alerts": {"alert_id", "fired_at", "rule_id", "monitor_condition",
               "alert_state", "severity", "alert_target_id"},
    "patch": {"installation_id", "observed_at", "started_at", "status",
              "installed_patch_count", "failed_patch_count", "reboot_status"},
    "backup": {"job_id", "started_at", "ended_at", "status", "operation",
               "vm", "protected_item_id"},
}


class ReadError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadError("INVALID_RESPONSE", message)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value: object) -> datetime:
    require(isinstance(value, str) and bool(value), "A source timestamp is missing.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReadError("INVALID_RESPONSE", "A source timestamp is invalid.") from error
    require(result.tzinfo is not None, "A source timestamp has no timezone.")
    return result


def identity_client_id(value: object) -> str:
    if (not isinstance(value, str) or re.fullmatch(UUID_PATTERN, value) is None
            or UUID(value).int == 0):
        raise ValueError("A canonical, nonzero managed identity client UUID is required.")
    return value


def hosted_origin(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Hosted mode requires one explicit HTTPS public origin.")
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if (parsed.scheme != "https" or parsed.netloc != hostname
            or len(hostname) > 253 or re.fullmatch(rf"{label}(?:\.{label})+", hostname) is None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or value != "https://" + hostname + parsed.path):
        raise ValueError("The public origin must be a fixed HTTPS hostname without a port, path, or credentials.")
    return value.rstrip("/")


def endpoint_url(value: object) -> str:
    require(isinstance(value, str), "The agent endpoint is missing.")
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise ReadError("INVALID_RESPONSE", "The agent endpoint is malformed.") from error
    require(
        parsed.scheme == "https" and bool(parsed.hostname)
        and parsed.netloc == parsed.hostname
        and parsed.hostname.startswith("asre-demo-cus--")
        and parsed.hostname.endswith(".centralus.azuresre.ai")
        and parsed.path in ("", "/") and not parsed.query and not parsed.fragment,
        "The agent returned an endpoint outside the fixed ASRE scope.",
    )
    return value.rstrip("/")


def collection(value: object, key: str | None = None) -> list:
    if key is not None:
        require(isinstance(value, dict) and key in value, "A source collection is missing.")
        require(not value.get("nextLink") and not value.get("continuationToken"),
                "The source requires pagination; the displayed collection would be incomplete.")
        value = value[key]
    require(isinstance(value, list) and all(isinstance(row, dict) for row in value),
            "A source collection has an invalid shape.")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReadError("REDIRECT_BLOCKED", "Authenticated Azure requests cannot redirect.")


class AzureReads:
    def __init__(self, credential=None, opener=None, *, managed_identity_client_id=None):
        self.managed_identity = managed_identity_client_id is not None
        if self.managed_identity:
            managed_identity_client_id = identity_client_id(managed_identity_client_id)
        if credential is not None:
            self.credential = credential
        elif self.managed_identity:
            self.credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
        else:
            self.credential = AzureCliCredential(tenant_id=TENANT, process_timeout=30)
        self.opener = opener if opener is not None else build_opener(NoRedirect)
        self._tokens = {}
        self._token_lock = threading.Lock()

    def _token(self, scope: str) -> str:
        with self._token_lock:
            token = self._tokens.get(scope)
            if token is None or token.expires_on < time.time() + 120:
                try:
                    token = self.credential.get_token(scope)
                except (CredentialUnavailableError, ClientAuthenticationError) as error:
                    raise ReadError(
                        "AUTH_REQUIRED",
                        "The dashboard managed identity could not authenticate. Check its configuration "
                        "and read access." if self.managed_identity else
                        "Azure authentication failed. Sign in with az login for the configured tenant "
                        "and ensure the signed-in account has read access.",
                    ) from error
                self._tokens[scope] = token
            return token.token

    def _read(self, url: str, scope: str, body: dict | None = None):
        if scope == ARM_SCOPE:
            allowed = body is None and url in ARM_READS.values()
        elif scope == SRE_SCOPE:
            parsed = urlsplit(url)
            origin = endpoint_url(f"{parsed.scheme}://{parsed.netloc}")
            detail_path = re.fullmatch(
                rf"/api/v1/(?:threads/{UUID_PATTERN}(?:/messages)?|approvals/{UUID_PATTERN})",
                parsed.path,
            )
            allowed = body is None and not parsed.query and not parsed.fragment and (
                url in {origin + suffix for suffix in NATIVE_READS.values()} or detail_path is not None
            )
        elif scope == LOG_SCOPE:
            workspace = url.removeprefix(LOG_URL).removesuffix("/query")
            try:
                valid_id = str(UUID(workspace)) == workspace and UUID(workspace).int != 0
            except ValueError:
                valid_id = False
            allowed = (
                valid_id and url == LOG_URL + workspace + "/query"
                and isinstance(body, dict) and set(body) == {"query", "timespan"}
                and any(
                    body == {"query": query_for(name, window), "timespan": WINDOWS[window]}
                    for name in QUERIES for window in WINDOWS
                )
            )
        else:
            allowed = False
        if not allowed:
            raise PermissionError("The dashboard only permits its fixed read operations.")
        request = Request(
            url, method="GET" if body is None else "POST",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self._token(scope),
                     "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=45) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            messages = {
                401: "The source rejected authentication; sign in again.",
                403: "Read access is denied. No additional permissions were automatically granted.",
                429: "The source is rate limited; wait before refreshing.",
            }
            raise ReadError(
                f"HTTP_{error.code}",
                messages.get(error.code, f"The Azure source returned HTTP {error.code}."),
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise ReadError("NETWORK_ERROR", "The Azure source could not be reached.") from error
        require(len(raw) <= MAX_RESPONSE, "The source exceeds the dashboard response-size limit.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise ReadError("INVALID_RESPONSE", "The source did not return valid JSON.") from error
        require(isinstance(value, (dict, list)), "The source returned an invalid envelope.")
        if isinstance(value, dict):
            require(
                not value.get("partialError") and not value.get("Error")
                and not isinstance(value.get("error"), dict)
                and (scope == SRE_SCOPE or not value.get("error")),
                    "The query returned an error or incomplete results.")
        return value

    def agent(self) -> dict:
        value = self._read(ARM_READS["agent"], ARM_SCOPE)
        require(isinstance(value, dict) and str(value.get("id", "")).lower() == AGENT_ID.lower(),
                "The agent resource does not match the fixed scope.")
        p = value.get("properties")
        require(isinstance(p, dict) and isinstance(p.get("actionConfiguration"), dict),
                "The agent properties are incomplete.")
        action = p["actionConfiguration"]
        for key in ("powerState", "provisioningState"):
            require(isinstance(p.get(key), str) and bool(p[key]), "Agent state is unavailable.")
        return {
            "name": CTX.agent.name, "endpoint": endpoint_url(p.get("agentEndpoint")),
            "power": p["powerState"], "provisioning": p["provisioningState"],
            "mode": action.get("mode"), "access": action.get("accessLevel"),
        }

    def workspace(self) -> dict:
        value = self._read(ARM_READS["workspace"], ARM_SCOPE)
        require(isinstance(value, dict) and str(value.get("id", "")).lower() == WORKSPACE_ID.lower(),
                "The workspace resource does not match the fixed scope.")
        p = value.get("properties")
        require(isinstance(p, dict), "The workspace properties are missing.")
        customer_id = p.get("customerId")
        try:
            valid = isinstance(customer_id, str) and str(UUID(customer_id)) == customer_id
        except ValueError:
            valid = False
        require(valid and UUID(customer_id).int != 0, "The workspace customer ID is invalid.")
        return {"name": CTX.log_analytics.name, "customer_id": customer_id}

    def native(self, name: str, endpoint: str):
        return self._read(endpoint_url(endpoint) + NATIVE_READS[name], SRE_SCOPE)

    def rules(self) -> list:
        rows = []
        for name, resource in zip(RULE_NAMES, RULE_IDS):
            value = self._read(ARM_READS[name], ARM_SCOPE)
            require(isinstance(value, dict) and str(value.get("id", "")).lower() == resource.lower(),
                    "An alert rule is outside the fixed scope.")
            p = value.get("properties")
            require(isinstance(p, dict) and isinstance(p.get("enabled"), bool),
                    "An alert rule did not report its enabled state.")
            rows.append({"name": "asre-demo-" + name, "enabled": p["enabled"],
                         "severity": p.get("severity")})
        return rows

    def intake(self, endpoint: str) -> dict:
        rows = collection(self.native("intake", endpoint), "value")
        matches = [row for row in rows if row.get("name") == "asre-azure-monitor-review"]
        require(len(matches) == 1 and isinstance(matches[0].get("properties"), dict),
                "The exact native response plan is missing or ambiguous.")
        p = matches[0]["properties"]
        require(isinstance(p.get("isEnabled"), bool), "The native intake enabled state is unknown.")
        return {"name": "asre-azure-monitor-review", "enabled": p["isEnabled"],
                "mode": p.get("agentMode"), "handler": p.get("handlingAgent"),
                "priorities": p.get("priorities"), "title_contains": p.get("titleContains"),
                "target": p.get("azMonitorFilterSettings", {}).get("targetResource")
                          if isinstance(p.get("azMonitorFilterSettings"), dict) else None}

    def schedule(self, endpoint: str) -> dict:
        rows = collection(self.native("schedule", endpoint))
        matches = [row for row in rows if row.get("name") == "asre-proactive-review"]
        require(len(matches) == 1, "The exact native proactive schedule is missing or ambiguous.")
        p = matches[0]
        require(isinstance(p.get("status"), str) and bool(p["status"]),
                "The native schedule state is unknown.")
        return {"name": p.get("name"), "status": p["status"], "cron": p.get("cronExpression")}

    def connectors(self, endpoint: str) -> list:
        rows = []
        for name in ("logs", "monitor"):
            value = self.native(name, endpoint)
            require(isinstance(value, dict) and isinstance(value.get("healthy"), bool)
                    and isinstance(value.get("status"), str),
                    "A native connector did not report its health.")
            rows.append({"name": "asre-operations-logs" if name == "logs" else "monitor-client",
                         "healthy": value["healthy"],
                         "status": value["status"], "message": value.get("message")})
        return rows

    def threads(self, endpoint: str, window: str) -> list:
        rows = collection(self.native("threads", endpoint), "value")
        duration = {"1h": timedelta(hours=1), "6h": timedelta(hours=6),
                    "24h": timedelta(days=1), "7d": timedelta(days=7)}[window]
        cutoff = datetime.now(timezone.utc) - duration
        result = []
        allowed = {resource.lower() for resource in RULE_IDS}
        for row in rows:
            if row.get("source") != "Incident" or row.get("threadOrigin") != "Trigger":
                continue
            incident = row.get("incidentSource")
            require(isinstance(incident, dict), "Native incident source metadata is missing.")
            if incident.get("incidentType") != "AzMonitor" or str(incident.get("incidentId", "")).lower() not in allowed:
                continue
            created = row.get("createdTimestamp")
            if timestamp(created) < cutoff:
                continue
            status = row.get("status")
            require(isinstance(status, dict) and isinstance(status.get("incidentStatus"), dict),
                    "Native incident status metadata is missing.")
            source_status = status["incidentStatus"].get("status")
            require(source_status is None or isinstance(source_status, str),
                    "The source incident state is invalid.")
            require(isinstance(row.get("id"), str) and isinstance(row.get("title"), str),
                    "Native thread identity is missing.")
            result.append({
                "id": row["id"], "title": row["title"][:300], "created_at": created,
                "updated_at": row.get("modifiedTimestamp"), "rule_id": incident["incidentId"],
                "incident_state": source_status if source_status else "Unknown",
                "origin": "Trigger",
                "plan_id": row.get("incidentDetails", {}).get("filterId") if isinstance(row.get("incidentDetails"), dict) else None,
                "execution_state": row.get("incidentDetails", {}).get("investigationStatus") if isinstance(row.get("incidentDetails"), dict) else None,
                "alert_instance_id": status["incidentStatus"].get("incidentId"),
            })
        return sorted(result, key=lambda row: timestamp(row["created_at"]), reverse=True)

    def incident(self, endpoint: str, identity: str) -> dict:
        require(re.fullmatch(UUID_PATTERN, identity) is not None, "Invalid native incident identity.")
        prefix = endpoint_url(endpoint) + "/api/v1/threads/" + identity
        raw = self._read(prefix, SRE_SCOPE)
        require(isinstance(raw, dict) and raw.get("id") == identity
                and raw.get("source") == "Incident" and raw.get("threadOrigin") == "Trigger",
                "The detail response is not the selected native incident.")
        source, status, details = raw.get("incidentSource"), raw.get("status"), raw.get("incidentDetails")
        require(isinstance(source, dict) and source.get("incidentType") == "AzMonitor"
                and str(source.get("incidentId", "")).lower() in {rule.lower() for rule in RULE_IDS}
                and isinstance(status, dict) and isinstance(status.get("incidentStatus"), dict)
                and isinstance(details, dict), "The incident detail is outside the fixed scope or incomplete.")
        require(isinstance(raw.get("title"), str), "The native incident title is missing.")
        timestamp(raw.get("createdTimestamp"))
        execution = details.get("investigationStatus")
        require(execution is None or isinstance(execution, str), "Native execution state is invalid.")
        thread = {
            "id": identity, "title": raw["title"][:300], "created_at": raw["createdTimestamp"],
            "updated_at": raw.get("modifiedTimestamp"), "rule_id": source["incidentId"],
            "incident_state": status["incidentStatus"].get("status") or "Unknown",
            "alert_instance_id": status["incidentStatus"].get("incidentId"),
            "plan_id": details.get("filterId"), "execution_state": execution, "origin": "Trigger",
        }
        def messages():
            value = self._read(prefix + "/messages", SRE_SCOPE)
            rows = collection(value, "value")
            require(len(rows) <= 2000, "The native message collection exceeds the bounded limit.")
            return rows

        def approvals():
            value = self._read(endpoint + "/api/v1/approvals/" + identity, SRE_SCOPE)
            rows = collection(value)
            require(len(rows) <= 100, "The native approvals collection exceeds the bounded limit.")
            # Return no raw approval prompts or arguments.
            return [{"id": row.get("id")} for row in rows]

        with ThreadPoolExecutor(max_workers=2) as pool:
            message_future = pool.submit(capture, "messages", messages)
            approval_future = pool.submit(capture, "approvals", approvals)
            message_source, approval_source = message_future.result(), approval_future.result()
        try:
            return build_journey(thread, message_source, approval_source)
        except ValueError as error:
            raise ReadError("INVALID_RESPONSE", str(error)) from error

    def query(self, name: str, customer_id: str, window: str) -> list:
        value = self._read(LOG_URL + customer_id + "/query", LOG_SCOPE,
                           {"query": query_for(name, window), "timespan": WINDOWS[window]})
        tables = collection(value, "tables")
        require(len(tables) == 1, "The query did not return one complete result table.")
        table = tables[0]
        columns = table.get("columns")
        rows = table.get("rows")
        require(isinstance(columns, list) and all(isinstance(column, dict) for column in columns)
                and isinstance(rows, list), "The query returned malformed columns or rows.")
        names = [column.get("name") for column in columns]
        require(all(isinstance(column, str) for column in names) and len(set(names)) == len(names)
                and set(names) == QUERY_COLUMNS[name], "The query returned unexpected columns.")
        require(len(rows) <= 1000, "The query exceeded the bounded row limit.")
        result = []
        for row in rows:
            require(isinstance(row, list) and len(row) == len(names), "The query returned malformed row values.")
            item = dict(zip(names, row))
            self._validate_row(name, item)
            result.append(item)
        return result

    @staticmethod
    def _validate_row(name: str, row: dict) -> None:
        if name == "filesystem":
            require(str(row["resource_id"]).lower() == LINUX_ID.lower() and row["mount"] == "/srv/asre-demo",
                    "The filesystem query returned a foreign resource.")
            for key in ("used_percent", "min_used", "max_used"):
                require(isinstance(row[key], (int, float)) and not isinstance(row[key], bool)
                        and math.isfinite(row[key]) and 0 <= row[key] <= 100,
                        "The filesystem query returned an invalid percentage.")
            require(isinstance(row["sample_count"], int) and row["sample_count"] > 0,
                    "The filesystem sample count is invalid.")
            timestamp(row["bucket"])
            timestamp(row["latest_sample"])
        elif name == "alerts":
            require(str(row["rule_id"]).lower() in {resource.lower() for resource in RULE_IDS}
                    and str(row["alert_id"]).lower().startswith(
                        WORKSPACE_ID.lower() + "/providers/microsoft.alertsmanagement/alerts/"),
                    "The alert query returned a foreign record.")
            timestamp(row["fired_at"])
        elif name == "patch":
            require(str(row["installation_id"]).lower().startswith(PATCH_ID.lower() + "/patchinstallationresults/")
                    and isinstance(row["status"], str) and bool(row["status"]),
                    "The installation query returned a foreign or incomplete record.")
            timestamp(row["observed_at"])
        elif name == "backup":
            require(str(row["job_id"]).lower().startswith(BACKUP_VAULT.lower() + "/backupjobs/")
                    and row["vm"] == CTX.targets["backup"].name and row["operation"] == "Backup"
                    and isinstance(row["status"], str) and bool(row["status"]),
                    "The backup query returned a foreign or incomplete record.")
            timestamp(row["started_at"])


def capture(name: str, action) -> dict:
    try:
        data = action()
    except ReadError as error:
        LOG.warning("%s: %s", name, error.code)
        return {"status": "error", "checked_at": utcnow(),
                "error": {"code": error.code, "message": str(error)}}
    return {"status": "empty" if data == [] else "ok", "checked_at": utcnow(),
            "data": data, "at_limit": isinstance(data, list) and len(data) == 1000}


class Dashboard:
    def __init__(self, reads=None, ttl=30):
        self.reads = reads if reads is not None else AzureReads()
        self.ttl = ttl
        self._cache = {}
        self._locks = {window: threading.Lock() for window in WINDOWS}
        self._incident_cache = {}
        self._incident_lock = threading.Lock()

    def incident(self, identity: str, force: bool = False) -> dict:
        if re.fullmatch(UUID_PATTERN, identity) is None:
            raise ValueError("Invalid incident identity.")
        with self._incident_lock:
            cached = self._incident_cache.get(identity)
            if not force and cached and time.monotonic() - cached[0] < 10:
                return {**cached[1], "cached": True}
            contexts = sorted(list(self._cache.values()), key=lambda entry: entry[0], reverse=True)
            context = next((entry[1] for entry in contexts
                            if time.monotonic() - entry[0] < self.ttl
                            and entry[1]["sources"]["threads"]["status"] in ("ok", "empty")
                            and any(row["id"] == identity for row in entry[1]["sources"]["threads"]["data"])), None)
            if context is not None:
                endpoint = context["sources"]["agent"]["data"]["endpoint"]
                member = True
            else:
                endpoint = self.reads.agent()["endpoint"]
                member = any(row["id"] == identity for row in self.reads.threads(endpoint, "7d"))
            if not member:
                raise ReadError("NOT_FOUND", "The incident is not in the fixed ASRE native collection for the last seven days.")
            result = capture("incident", lambda: self.reads.incident(endpoint, identity))
            response = {"schema": "asre-journey/v1", "generated_at": utcnow(),
                        "cached": False, **result}
            self._incident_cache[identity] = (time.monotonic(), response)
            return response

    def snapshot(self, window: str = "24h", force: bool = False) -> dict:
        if window not in WINDOWS:
            raise ValueError("Unsupported time window.")
        with self._locks[window]:
            cached = self._cache.get(window)
            if not force and cached is not None and time.monotonic() - cached[0] < self.ttl:
                return {**cached[1], "cached": True}
            sources = {
                "agent": capture("agent", self.reads.agent),
                "workspace": capture("workspace", self.reads.workspace),
            }
            tasks = {"rules": self.reads.rules}
            if sources["agent"]["status"] == "ok":
                endpoint = sources["agent"]["data"]["endpoint"]
                tasks.update({
                    "intake": lambda: self.reads.intake(endpoint),
                    "schedule": lambda: self.reads.schedule(endpoint),
                    "connectors": lambda: self.reads.connectors(endpoint),
                    "threads": lambda: self.reads.threads(endpoint, window),
                })
            else:
                for name in ("intake", "schedule", "connectors", "threads"):
                    sources[name] = {
                        "status": "error", "checked_at": utcnow(),
                        "error": {"code": "DEPENDENCY_UNAVAILABLE",
                                  "message": "Agent metadata is unavailable; no runtime endpoint was assumed."},
                    }
            if sources["workspace"]["status"] == "ok":
                customer = sources["workspace"]["data"]["customer_id"]
                for name in QUERIES:
                    tasks[name] = lambda name=name: self.reads.query(name, customer, window)
            else:
                for name in QUERIES:
                    sources[name] = {
                        "status": "error", "checked_at": utcnow(),
                        "error": {"code": "DEPENDENCY_UNAVAILABLE",
                                  "message": "The actual workspace ID is unavailable; no ID was assumed."},
                    }
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {name: pool.submit(capture, name, action) for name, action in tasks.items()}
                sources.update({name: future.result() for name, future in futures.items()})
            errors = sum(source["status"] == "error" for source in sources.values())
            snapshot = {
                "schema": "asre-dashboard/v1", "generated_at": utcnow(), "window": window,
                "status": "ok" if errors == 0 else "unavailable" if errors == len(sources) else "partial",
                "cached": False, "cache_seconds": self.ttl, "sources": sources,
                "profiles": catalog(),
                "case_links": {"status": "unavailable",
                               "message": "No verified case-link source is bound to this read-only status adapter."},
            }
            self._cache[window] = (time.monotonic(), snapshot)
            return snapshot


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, dashboard=None, *, host="127.0.0.1",
                 public_origin=None, managed_identity_client_id=None):
        if host not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("The listening host must be 127.0.0.1 or 0.0.0.0.")
        if host == "0.0.0.0":
            self.public_origin = hosted_origin(public_origin)
            identity_client_id(managed_identity_client_id)
        else:
            if public_origin is not None or managed_identity_client_id is not None:
                raise ValueError("Hosted origin and identity settings require --host 0.0.0.0.")
            self.public_origin = None
        self.dashboard = dashboard if dashboard is not None else Dashboard(
            AzureReads(managed_identity_client_id=managed_identity_client_id),
        )
        self.html = Path(__file__).with_name("index.html").read_text(encoding="utf-8").encode("utf-8")
        self.evidence_html = Path(__file__).with_name("evidence.html").read_text(encoding="utf-8").encode("utf-8")
        hashes = {}
        for tag in ("script", "style"):
            blocks = re.findall(rb"<" + tag.encode() + rb"(?:\s[^>]*)?>(.*?)</" + tag.encode() + rb">",
                                self.html + self.evidence_html, re.DOTALL | re.IGNORECASE)
            hashes[tag] = " ".join(
                "'sha256-" + base64.b64encode(hashlib.sha256(block).digest()).decode() + "'"
                for block in blocks
            )
        self.csp = (
            "default-src 'none'; script-src " + hashes["script"] + "; style-src " + hashes["style"]
            + "; connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        super().__init__((host, port), Handler)
        self.allowed_hosts = (
            {urlsplit(self.public_origin).netloc} if self.public_origin else
            {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}
        )
        self.allowed_origins = (
            {self.public_origin} if self.public_origin else
            {"http://" + value for value in self.allowed_hosts}
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "ASREReadOnlyDashboard"
    sys_version = ""

    def _send(self, status: int, data: bytes, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", self.server.csp)
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status: int, value: dict):
        self._send(status, json.dumps(value, allow_nan=False).encode("utf-8"))

    def _trusted(self) -> bool:
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        navigation = (
            self.server.public_origin is not None
            and self.headers.get("Sec-Fetch-Mode") == "navigate"
            and self.headers.get("Sec-Fetch-Dest") == "document"
            and self.path in ("/", "/index.html", "/evidence", "/evidence.html")
        )
        return (
            self.headers.get("Host") in self.server.allowed_hosts
            and (origin is None or origin in self.server.allowed_origins)
            and (fetch_site not in ("cross-site", "same-site") or navigation)
            and self.headers.get("Content-Length", "0") == "0"
            and not self.headers.get("Transfer-Encoding")
        )

    def do_GET(self):
        if not self._trusted():
            self._json(403, {"error": "Only same-origin requests to the configured dashboard host are allowed."})
            return
        target = urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment:
            self._json(400, {"error": "Absolute or malformed request targets are not accepted."})
            return
        if target.path in ("/", "/index.html"):
            self._send(200, self.server.html, "text/html; charset=utf-8")
        elif target.path in ("/evidence", "/evidence.html"):
            self._send(200, self.server.evidence_html, "text/html; charset=utf-8")
        elif target.path == "/healthz" and not target.query:
            self._json(200, {"status": "running", "mode": "read-only",
                             "binding": "hosted" if self.server.public_origin else "loopback"})
        elif target.path == "/favicon.ico" and not target.query:
            self._send(204, b"", "image/x-icon")
        elif (target.path == "/api/dashboard" or target.path.startswith("/api/incidents/")) and self.command != "HEAD":
            try:
                query = parse_qs(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            except ValueError:
                self._json(400, {"error": "Invalid dashboard query parameters."})
                return
            if (set(query) - {"window", "refresh"} or any(len(value) != 1 for value in query.values())
                    or query.get("window", ["24h"])[0] not in WINDOWS
                    or query.get("refresh", ["0"])[0] not in ("0", "1")):
                self._json(400, {"error": "Only a listed window and refresh=0 or 1 are accepted."})
                return
            if target.path.startswith("/api/incidents/"):
                identity = target.path.removeprefix("/api/incidents/")
                if re.fullmatch(UUID_PATTERN, identity) is None or "window" in query:
                    self._json(400, {"error": "Only a canonical incident UUID and optional refresh=0 or 1 are accepted."})
                    return
                try:
                    result = self.server.dashboard.incident(identity, query.get("refresh", ["0"])[0] == "1")
                except ReadError as error:
                    self._json(404 if error.code == "NOT_FOUND" else 503,
                               {"error": {"code": error.code, "message": str(error)}})
                    return
                self._json(200, result)
                return
            self._json(200, self.server.dashboard.snapshot(
                query.get("window", ["24h"])[0], query.get("refresh", ["0"])[0] == "1",
            ))
        else:
            self._json(404, {"error": "This read-only dashboard route does not exist."})

    do_HEAD = do_GET

    def _write_denied(self):
        self._json(405, {"error": "This dashboard cannot write, execute tools, approve actions or trigger work."})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _write_denied

    def log_message(self, format, *args):
        LOG.info("%s %s", self.address_string(), format % args)


def log_source_probe(dashboard: Dashboard) -> None:
    snapshot = dashboard.snapshot("7d", True)
    sources = {
        name: {"status": source["status"],
               "error_code": source.get("error", {}).get("code")}
        for name, source in snapshot["sources"].items()
    }
    report = {
        "schema": "asre-dashboard-source-probe/v1", "generated_at": snapshot["generated_at"],
        "status": snapshot["status"], "sources": sources,
        "provider_access": "Empty ARG results do not qualify provider access.",
    }
    emit = LOG.info if snapshot["status"] == "ok" else LOG.warning
    emit("ASRE_DASHBOARD_SOURCE_PROBE %s", json.dumps(report, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Listening port (default: 8765).")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"),
                        default=os.environ.get("ASRE_DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--public-origin", default=os.environ.get("ASRE_DASHBOARD_PUBLIC_ORIGIN"),
                        help="Exact HTTPS origin; hosted ingress must enforce viewer authentication.")
    parser.add_argument("--managed-identity-client-id",
                        default=os.environ.get("ASRE_DASHBOARD_MANAGED_IDENTITY_CLIENT_ID"))
    parser.add_argument("--probe-sources", action="store_true",
                        help="Log one bounded source-status probe without raw data or changing health semantics.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("The port must be between 1 and 65535.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = DashboardServer(args.port, host=args.host, public_origin=args.public_origin,
                                 managed_identity_client_id=args.managed_identity_client_id)
    except ValueError as error:
        parser.error(str(error))
    address = server.public_origin or f"http://127.0.0.1:{server.server_port}"
    print(f"ASRE dashboard: {address} (read-only; Ctrl+C to stop)", flush=True)
    if args.probe_sources:
        threading.Thread(target=log_source_probe, args=(server.dashboard,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Stopping the local dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
