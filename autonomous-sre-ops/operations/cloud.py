"""Read authoritative Azure evidence; no caller-supplied URLs, queries or commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from operations.lifecycle import SUBSCRIPTION, TARGETS, normalized_target

ARM = "https://management.azure.com"
WORKSPACE = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-asre-control-cus/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
VAULT = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"
MAX_RESPONSE = 4 * 1024 * 1024


class AzureReadError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AzureReadError("Azure evidence requests cannot redirect authenticated tokens.")


class AzureReader:
    def __init__(self, credential) -> None:
        self.credential = credential

    def request(self, method: str, url: str, body: dict | None = None) -> dict:
        if not url.startswith(ARM + "/subscriptions/" + SUBSCRIPTION + "/") and not url.startswith(
            ARM + "/providers/Microsoft.ResourceGraph/"
        ):
            raise PermissionError("The evidence URL is outside the fixed Azure management API.")
        token = self.credential.get_token("https://management.azure.com/.default")
        request = Request(
            url, method=method, data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token.token, "Content-Type": "application/json"},
        )
        try:
            with build_opener(NoRedirect).open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE + 1)
        except HTTPError as error:
            raise AzureReadError(f"Azure evidence request failed with HTTP {error.code}.") from error
        if len(raw) > MAX_RESPONSE:
            raise AzureReadError("The Azure evidence response exceeded its independent size bound.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise AzureReadError("Azure returned malformed JSON evidence.") from error
        if not isinstance(value, dict) or "error" in value or "Error" in value:
            raise AzureReadError("Azure returned an unexpected evidence response.")
        return value

    def patches(self, resource_id: str) -> dict:
        target = normalized_target(resource_id)
        if TARGETS[target] != "patch":
            raise PermissionError("Patch evidence is restricted to the patch target.")
        evidence = {}
        for table in ("patchassessmentresources", "patchinstallationresources"):
            evidence[table] = self.request(
                "POST", ARM + "/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01",
                {
                    "subscriptions": [SUBSCRIPTION],
                    "query": (
                        table + f" | where tolower(id) startswith '{target}/' "
                        "| project id, type, properties | limit 200"
                    ),
                    "options": {"resultFormat": "objectArray"},
                },
            )
            result = evidence[table]
            rows = result.get("data")
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise AzureReadError("Resource Graph returned invalid patch rows.")
            if result.get("$skipToken") or result.get("count") != len(rows) or result.get("totalRecords") != len(rows):
                raise AzureReadError("Patch evidence is incomplete or its row counts disagree.")
            result["at_row_limit"] = len(rows) == 200
        return {"source": "UpdateManager", "resource_id": target, **evidence}

    def backup(self, resource_id: str) -> dict:
        target = normalized_target(resource_id)
        if TARGETS[target] != "backup":
            raise PermissionError("Backup evidence is restricted to the protected backup target.")
        jobs = self.request(
            "GET", ARM + VAULT + "/backupJobs?api-version=2024-04-01&"
            + urlencode({"$filter": "startTime ge '" + (
                datetime.now(timezone.utc) - timedelta(days=7)
            ).strftime("%Y-%m-%dT%H:%M:%SZ") + "'"}),
        )
        container = "iaasvmcontainer;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus"
        item = "vm;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus"
        points = self.request(
            "GET", ARM + VAULT + "/backupFabrics/Azure/protectionContainers/" + container
            + "/protectedItems/" + item + "/recoveryPoints?api-version=2024-04-01",
        )
        for result in (jobs, points):
            if not isinstance(result.get("value"), list) or result.get("nextLink"):
                raise AzureReadError("Backup evidence is malformed or exceeds the single-page bound.")
        return {"source": "AzureBackup", "resource_id": target, "jobs": jobs, "recovery_points": points}

    def telemetry(self, resource_id: str) -> dict:
        target = normalized_target(resource_id)
        scope = f"| where TimeGenerated > ago(30m) | where tolower(_ResourceId) == '{target}'"
        capacity = (
            " | where (InstanceName == 'Memory' and CounterName in ('% Used Memory', 'Available MBytes Memory'))"
            " or (CounterName == '% Used Space' and InstanceName in ('/', '/srv/asre-demo'))"
        )
        branches = [
            f"({table} {scope}{capacity if table == 'Perf' else ''} | top {limit} by TimeGenerated desc | extend SourceTable='{table}')"
            for table, limit in (("Heartbeat", 10), ("Perf", 60), ("Syslog", 30))
        ]
        result = self.request(
            "POST", ARM + WORKSPACE + "/api/query?api-version=2020-08-01",
            {"query": "union " + ", ".join(branches) + (
                " | project SourceTable, TimeGenerated, _ResourceId, Computer, CounterName, "
                "InstanceName, CounterValue, SeverityLevel, ProcessName, ProcessID, Facility, "
                "SyslogMessage | order by TimeGenerated desc"
            ), "timespan": "PT30M"},
        )
        tables = result.get("Tables")
        if not isinstance(tables, list) or not tables:
            raise AzureReadError("The ARM Log Analytics query did not return its Tables envelope.")
        for table in tables:
            if not isinstance(table, dict) or not isinstance(table.get("Columns"), list) or not isinstance(table.get("Rows"), list):
                raise AzureReadError("Log Analytics returned malformed table evidence.")
            if not all(isinstance(column, dict) for column in table["Columns"]):
                raise AzureReadError("Log Analytics returned invalid column definitions.")
            if any(not isinstance(row, list) or len(row) != len(table["Columns"]) for row in table["Rows"]):
                raise AzureReadError("Log Analytics returned inconsistent row/column evidence.")
        data = []
        statuses = []
        for table in tables:
            names = [column.get("ColumnName") for column in table["Columns"]]
            if len(set(names)) != len(names) or not all(isinstance(name, str) for name in names):
                raise AzureReadError("Log Analytics returned ambiguous column definitions.")
            if {"SourceTable", "TimeGenerated", "_ResourceId"}.issubset(names):
                data.append((names, table["Rows"]))
            if {"StatusCode", "Severity", "SeverityName"}.issubset(names):
                statuses.extend(dict(zip(names, row)) for row in table["Rows"])
        if len(data) != 1 or not statuses or not any(row["SeverityName"] == "Info" for row in statuses):
            raise AzureReadError("Log Analytics omitted its authoritative data or completion status.")
        if any(row["StatusCode"] != 0 or not isinstance(row["Severity"], int) or row["Severity"] < 4 for row in statuses):
            raise AzureReadError("Log Analytics reported an incomplete or failed query.")
        columns, rows = data[0]
        if len(rows) > 100:
            raise AzureReadError("Log Analytics exceeded the bounded telemetry row limit.")
        return {
            "source": "AzureMonitor", "resource_id": target, "query_status": "completed",
            "columns": columns, "rows": rows, "row_count": len(rows), "window": "PT30M",
            "perf_scope": "Memory: % Used Memory, Available MBytes Memory; % Used Space: /, /srv/asre-demo",
            "syslog_scope": "ProcessName, ProcessID, Facility, SeverityLevel, SyslogMessage",
        }

    def collect(self) -> dict:
        return {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "patch": self.patches(next(target for target, role in TARGETS.items() if role == "patch")),
            "backup": self.backup(next(target for target, role in TARGETS.items() if role == "backup")),
        }
