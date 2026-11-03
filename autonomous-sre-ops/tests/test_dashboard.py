"""Offline contracts for the fixed read adapter and loopback HTTP surface."""

import hashlib
import http.client
import json
import re
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from azure.core.exceptions import ClientAuthenticationError

from dashboard.queries import (
    AGENT_ID, BACKUP_VAULT, LINUX_ID, PATCH_ID, QUERIES, RULE_IDS,
    WINDOWS, WORKSPACE_ID, query_for,
)
from dashboard.server import (
    ARM_READS, ARM_SCOPE, LOG_SCOPE, LOG_URL, MAX_RESPONSE, NATIVE_READS,
    QUERY_COLUMNS, SRE_SCOPE, AzureReads, Dashboard, DashboardServer,
    NoRedirect, ReadError, capture, endpoint_url, timestamp,
)

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://asre-demo-cus--test.cluster.centralus.azuresre.ai"
CUSTOMER = "00000062-000a-4000-8000-000000000062"
NOW = datetime.now(timezone.utc).isoformat()
HOSTED_ORIGIN = "https://asre-dashboard.example.azurecontainerapps.io"
HOSTED_CLIENT_ID = "12345678-1234-4234-8234-123456789abc"


def transport(value=None, raw=None, error=None):
    response = Mock()
    response.read.return_value = json.dumps(value).encode() if raw is None else raw
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    if error:
        opener.open.side_effect = error
    else:
        opener.open.return_value = response
    return opener


def client(value=None, raw=None, error=None):
    credential = Mock()
    credential.get_token.return_value = SimpleNamespace(token="test-token-do-not-expose", expires_on=time.time()+3600)
    return AzureReads(credential, transport(value, raw, error))


def thread(**changes):
    row = {
        "id": "thread-1", "title": "Native disk investigation",
        "source": "Incident", "threadOrigin": "Trigger", "type": "Prod",
        "createdTimestamp": NOW, "modifiedTimestamp": NOW,
        "incidentSource": {"incidentType": "AzMonitor", "incidentId": RULE_IDS[0]},
        "status": {"incidentStatus": {"status": "resolved", "incidentId": "alert-1"}},
        "startMessage": "private instructions", "lastMessage": "private journal",
    }
    row.update(changes)
    return row


class QueryTests(unittest.TestCase):
    def test_exact_provider_query_parity_with_native_guide(self):
        guide = (ROOT / "agent" / "knowledge" / "asre-live-report-sources-v1.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```kusto\n(.*?)\n```", guide, re.DOTALL)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks, [QUERIES[name] for name in ("alerts", "patch", "backup")])

    def test_windows_change_provider_timestamp_and_stay_closed(self):
        for window in WINDOWS:
            for name in QUERIES:
                with self.subTest(window=window, name=name):
                    query = query_for(name, window)
                    self.assertIn(f"ago({window})", query)
                    self.assertTrue(query.endswith("| limit 1000"))
                    if name == "filesystem":
                        self.assertIn("15m)" if window == "7d" else "5m)", query)
        for name, window in (("shell", "24h"), ("alerts", "24h); take 99"), ("alerts", "")):
            with self.assertRaises(ValueError):
                query_for(name, window)

    def test_filesystem_uses_correct_counter_and_scope(self):
        query = query_for("filesystem", "24h")
        for expected in (LINUX_ID, '"Logical Disk"', '"% Used Space"', '"/srv/asre-demo"', "avg(CounterValue)"):
            self.assertIn(expected, query)


class ReadClientTests(unittest.TestCase):
    def test_hosted_identity_is_explicit_without_cli_fallback(self):
        with patch("dashboard.server.ManagedIdentityCredential") as managed, \
                patch("dashboard.server.AzureCliCredential") as cli:
            reads = AzureReads(managed_identity_client_id=HOSTED_CLIENT_ID)
            managed.assert_called_once_with(client_id=HOSTED_CLIENT_ID)
            self.assertIs(reads.credential, managed.return_value)
            managed.return_value.get_token.side_effect = ClientAuthenticationError("private identity detail")
            result = capture("agent", lambda: reads._read(ARM_READS["agent"], ARM_SCOPE))
            self.assertEqual(result["error"]["code"], "AUTH_REQUIRED")
            self.assertIn("managed identity", result["error"]["message"])
            self.assertNotIn("private identity", result["error"]["message"])
            cli.assert_not_called()

    def test_local_identity_keeps_existing_cli_behavior(self):
        with patch("dashboard.server.AzureCliCredential") as cli, \
                patch("dashboard.server.ManagedIdentityCredential") as managed:
            reads = AzureReads()
            cli.assert_called_once_with(tenant_id="00000002-000a-4000-9000-000000000002",
                                        process_timeout=30)
            self.assertIs(reads.credential, cli.return_value)
            managed.assert_not_called()

    def test_invalid_managed_identity_is_not_a_cli_fallback(self):
        for identity in ("", "bad", HOSTED_CLIENT_ID.upper(), "00000000-0000-0000-0000-000000000000"):
            with self.subTest(identity=identity), \
                    patch("dashboard.server.AzureCliCredential") as cli, \
                    patch("dashboard.server.ManagedIdentityCredential") as managed:
                with self.assertRaises(ValueError):
                    AzureReads(managed_identity_client_id=identity)
                cli.assert_not_called()
                managed.assert_not_called()

    def test_scope_and_method_allowlist_precedes_authentication(self):
        reads = client({})
        bad = (
            ("https://evil.invalid", ARM_SCOPE, None),
            (ARM_READS["agent"], ARM_SCOPE, {"write": True}),
            (ENDPOINT+"/api/v1/messages", SRE_SCOPE, None),
            ("https://evil.invalid/api/v1/threads", SRE_SCOPE, None),
            (LOG_URL+CUSTOMER+"/query", LOG_SCOPE, {"query":"Perf | take 1","timespan":"PT24H"}),
            (ARM_READS["agent"], "https://evil.invalid/.default", None),
        )
        for url, scope, body in bad:
            with self.subTest(url=url, scope=scope):
                with self.assertRaises((PermissionError, ReadError)):
                    reads._read(url,scope,body)
        reads.credential.get_token.assert_not_called()
        reads.opener.open.assert_not_called()

    def test_known_read_operations_and_token_cache(self):
        reads = client({"value":[]})
        reads._read(ARM_READS["agent"], ARM_SCOPE)
        reads._read(ARM_READS["workspace"], ARM_SCOPE)
        reads.native("threads", ENDPOINT)
        reads.native("intake", ENDPOINT)
        self.assertEqual(reads.credential.get_token.call_count, 2)
        calls = reads.opener.open.call_args_list
        self.assertTrue(all(call.args[0].method == "GET" for call in calls))
        self.assertTrue(all(call.kwargs["timeout"] == 45 for call in calls))

    def test_only_fixed_query_post_is_allowed(self):
        reads = client({"tables":[]})
        for name in QUERIES:
            for window in WINDOWS:
                reads._read(LOG_URL+CUSTOMER+"/query", LOG_SCOPE,
                            {"query":query_for(name,window), "timespan":WINDOWS[window]})
        self.assertTrue(all(call.args[0].method == "POST" for call in reads.opener.open.call_args_list))
        with self.assertRaises(PermissionError):
            reads._read(LOG_URL+CUSTOMER+"/query",LOG_SCOPE,
                        {"query":query_for("alerts","7d"),"timespan":"PT24H"})

    def test_endpoint_validation(self):
        self.assertEqual(endpoint_url(ENDPOINT+"/"),ENDPOINT)
        for value in (
            None, "http://asre-demo-cus--x.centralus.azuresre.ai",
            ENDPOINT+":443", ENDPOINT+"/api", ENDPOINT+"?x=1", ENDPOINT+"#x",
            "https://user@asre-demo-cus--x.centralus.azuresre.ai",
            ENDPOINT+".evil.invalid", "https://other--x.centralus.azuresre.ai",
            "https://[invalid",
        ):
            with self.subTest(value=value), self.assertRaises(ReadError):
                endpoint_url(value)

    def test_redirects_are_blocked(self):
        with self.assertRaises(ReadError) as failure:
            NoRedirect().redirect_request(None,None,302,"redirect",{},"https://evil.invalid")
        self.assertEqual(failure.exception.code,"REDIRECT_BLOCKED")

    def test_denials_and_auth_errors_are_explicit_without_token_or_body(self):
        for code in (401,403,429,500):
            reads = client(error=HTTPError(ARM_READS["agent"],code,"secret provider body",{},None))
            result = capture("agent",lambda:reads._read(ARM_READS["agent"],ARM_SCOPE))
            self.assertEqual(result["status"],"error")
            self.assertEqual(result["error"]["code"],f"HTTP_{code}")
            self.assertNotIn("secret",json.dumps(result))
            self.assertNotIn("test-token",json.dumps(result))
        reads = client({})
        reads.credential.get_token.side_effect=ClientAuthenticationError("secret account detail")
        result = capture("agent",lambda:reads._read(ARM_READS["agent"],ARM_SCOPE))
        self.assertEqual(result["error"]["code"],"AUTH_REQUIRED")
        self.assertNotIn("secret",json.dumps(result))

    def test_network_malformed_oversized_and_partial_data_do_not_look_empty(self):
        reads_list = [
            client(error=URLError("secret network detail")),
            client(raw=b"not json"),
            client(raw=b"x"*(MAX_RESPONSE+1)),
            client({"tables":[], "error":{"code":"PartialError"}}),
            client({"tables":[], "partialError":{"code":"PartialError"}}),
        ]
        for reads in reads_list:
            with self.subTest(reads=reads):
                result = capture("logs",lambda:reads._read(ARM_READS["agent"],ARM_SCOPE))
                self.assertEqual(result["status"],"error")
                self.assertNotIn("data",result)

    def test_connector_info_in_error_field_is_not_health_failure(self):
        reads = client({"healthy":True,"status":"Connected","error":"INFO PingRequest"})
        values = reads.connectors(ENDPOINT)
        self.assertEqual([row["healthy"] for row in values],[True,True])
        self.assertEqual(values[0]["name"],"asre-operations-logs")
        self.assertNotIn("error",values[0])

    def test_schedule_is_resolved_by_owned_name_not_deployment_id(self):
        reads = client([{"id":"new-deployment-id","name":"asre-proactive-review",
                         "status":"Active","cronExpression":"0 */6 * * *"}])
        self.assertEqual(reads.schedule(ENDPOINT)["cron"],"0 */6 * * *")
        self.assertEqual(reads.opener.open.call_args.args[0].full_url,
                         ENDPOINT+NATIVE_READS["schedule"])

    def test_native_intake_uses_properties_enabled_field(self):
        reads=client({"value":[{"name":"asre-azure-monitor-review",
                               "properties":{"isEnabled":True,"agentMode":"Review"}}]})
        self.assertTrue(reads.intake(ENDPOINT)["enabled"])

    def test_threads_use_incident_source_and_trigger_origin_not_status_string(self):
        old=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
        rows=[thread(),thread(source="Conversation"),thread(threadOrigin="UserInitiated"),
              thread(createdTimestamp=old),
              thread(incidentSource={"incidentType":"AzMonitor","incidentId":"/other/rule"})]
        values=client({"value":rows}).threads(ENDPOINT,"24h")
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]["incident_state"],"resolved")
        self.assertNotIn("completed",values[0])
        self.assertNotIn("private",json.dumps(values))

    def test_incomplete_native_shapes_are_errors(self):
        cases=[
            lambda:client({"value":[thread(status="resolved")]}).threads(ENDPOINT,"24h"),
            lambda:client({"value":[],"nextLink":"https://next"}).threads(ENDPOINT,"24h"),
            lambda:client({"value":[]}).schedule(ENDPOINT),
            lambda:client({"value":[]}).intake(ENDPOINT),
            lambda:client({"healthy":None,"status":"Connected"}).connectors(ENDPOINT),
        ]
        for action in cases:
            with self.subTest(action=action), self.assertRaises(ReadError):
                action()

    def test_agent_and_workspace_ids_must_match(self):
        for action in (lambda:client({"id":"/other"}).agent(),
                       lambda:client({"id":"/other"}).workspace()):
            with self.assertRaises(ReadError):
                action()
        reads=client({"id":AGENT_ID,"properties":{
            "agentEndpoint":ENDPOINT,"powerState":"Running","provisioningState":"Succeeded",
            "actionConfiguration":{"mode":"Review","accessLevel":"Low"}}})
        self.assertEqual(reads.agent()["power"],"Running")
        self.assertEqual(client({"id":WORKSPACE_ID,"properties":{"customerId":CUSTOMER}}).workspace()["customer_id"],CUSTOMER)

    def test_empty_query_preserves_empty_not_source_access_proof(self):
        columns=[{"name":name} for name in sorted(QUERY_COLUMNS["alerts"])]
        reads=client({"tables":[{"columns":columns,"rows":[]}]})
        result=capture("alerts",lambda:reads.query("alerts",CUSTOMER,"24h"))
        self.assertEqual(result["status"],"empty")
        self.assertEqual(result["data"],[])

    def test_query_rejects_duplicate_columns_malformed_rows_and_foreign_ids(self):
        columns=sorted(QUERY_COLUMNS["alerts"])
        values={name:"value" for name in columns}
        values.update(rule_id=RULE_IDS[0],fired_at=NOW,
                      alert_id=WORKSPACE_ID+"/providers/Microsoft.AlertsManagement/alerts/alert-1")
        valid={"columns":[{"name":name} for name in columns],
               "rows":[[values[name] for name in columns]]}
        self.assertEqual(len(client({"tables":[valid]}).query("alerts",CUSTOMER,"24h")),1)
        for table in (
            {"columns":[{"name":"a"},{"name":"a"}],"rows":[]},
            {"columns":valid["columns"],"rows":[["short"]]},
            {"columns":valid["columns"],"rows":[valid["rows"][0]]*1001},
        ):
            with self.assertRaises(ReadError):
                client({"tables":[table]}).query("alerts",CUSTOMER,"24h")
        values["rule_id"]="/other/rule"
        with self.assertRaises(ReadError):
            AzureReads._validate_row("alerts",values)

    def test_filesystem_percentage_and_provider_identity_validation(self):
        row={"bucket":NOW,"latest_sample":NOW,"resource_id":LINUX_ID,"mount":"/srv/asre-demo",
             "sample_count":2,"used_percent":1.5,"min_used":1,"max_used":2}
        AzureReads._validate_row("filesystem",row)
        for value in (-1,101,float("nan"),True,None):
            with self.assertRaises(ReadError):
                AzureReads._validate_row("filesystem",{**row,"used_percent":value})
        AzureReads._validate_row("patch",{"installation_id":PATCH_ID+"/patchInstallationResults/a",
                                        "status":"Failed","observed_at":NOW})
        AzureReads._validate_row("backup",{"job_id":BACKUP_VAULT+"/backupJobs/a","status":"Completed",
                                         "vm":"vm-asre-backup-cus","operation":"Backup","started_at":NOW})

    def test_timestamp_requires_explicit_timezone(self):
        self.assertIsNotNone(timestamp(NOW).tzinfo)
        for value in (None,"bad","2026-01-01T12:00:00"):
            with self.assertRaises(ReadError):
                timestamp(value)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.reads=Mock()
        self.reads.agent.return_value={"endpoint":ENDPOINT}
        self.reads.workspace.return_value={"customer_id":CUSTOMER}
        self.reads.rules.return_value=[]
        self.reads.intake.return_value={"enabled":True}
        self.reads.schedule.return_value={"status":"Active"}
        self.reads.connectors.return_value=[]
        self.reads.threads.return_value=[]
        self.reads.query.return_value=[]

    def test_cache_windows_force_and_no_fake_cases(self):
        dashboard=Dashboard(self.reads)
        first=dashboard.snapshot()
        cached=dashboard.snapshot()
        self.assertFalse(first["cached"])
        self.assertTrue(cached["cached"])
        self.assertEqual(self.reads.agent.call_count,1)
        self.assertEqual(first["case_links"]["status"],"unavailable")
        self.assertNotIn("test-token",json.dumps(first))
        dashboard.snapshot(force=True)
        dashboard.snapshot("7d")
        self.assertEqual(self.reads.agent.call_count,3)
        self.reads.query.assert_any_call("alerts",CUSTOMER,"7d")
        with self.assertRaises(ValueError):
            dashboard.snapshot("arbitrary")

    def test_denied_schedule_is_partial_not_green_or_permission_escalation(self):
        self.reads.schedule.side_effect=ReadError("HTTP_403","Read access denied.")
        result=Dashboard(self.reads).snapshot()
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["sources"]["schedule"]["status"],"error")
        self.assertNotIn("data",result["sources"]["schedule"])

    def test_unresolved_dependencies_do_not_fall_back_to_hardcoded_endpoint(self):
        for name in ("agent","workspace","rules"):
            getattr(self.reads,name).side_effect=ReadError("HTTP_403","Denied")
        result=Dashboard(self.reads).snapshot()
        self.assertEqual(result["status"],"unavailable")
        self.reads.query.assert_not_called()
        self.reads.threads.assert_not_called()
        self.reads.intake.assert_not_called()

    def test_unexpected_programming_errors_are_not_swallowed(self):
        with self.assertRaises(TypeError):
            capture("source",lambda:len(1))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard=Mock()
        cls.dashboard.snapshot.return_value={"schema":"asre-dashboard/v1","sources":{}}
        cls.dashboard.incident.return_value={"schema":"asre-journey/v1","status":"ok","data":{}}
        cls.server=DashboardServer(0,cls.dashboard)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self,path="/",method="GET",headers=None):
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=5)
        try:
            connection.request(method,path,headers=headers or {})
            response=connection.getresponse()
            return response.status,dict(response.getheaders()),response.read()
        finally:
            connection.close()

    def test_html_headers_and_exact_csp_hashes(self):
        status,headers,body=self.request()
        self.assertEqual(status,200)
        self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertEqual(headers["X-Content-Type-Options"],"nosniff")
        self.assertNotIn(b"\r",body)
        self.assertNotIn("Access-Control-Allow-Origin",headers)
        self.assertNotIn("'unsafe-inline'",headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        import base64
        for block in re.findall(rb"<script>(.*?)</script>",body,re.DOTALL):
            digest=base64.b64encode(hashlib.sha256(block).digest()).decode()
            self.assertIn("'sha256-"+digest+"'",headers["Content-Security-Policy"])
        self.assertEqual(self.server.server_address[0],"127.0.0.1")

    def test_host_origin_and_cross_site_defenses(self):
        for headers in (
            {"Host":"evil.invalid"},
            {"Origin":"https://evil.invalid"},
            {"Origin":"null"},
            {"Sec-Fetch-Site":"cross-site"},
            {"Sec-Fetch-Site":"same-site"},
            {"Content-Length":"1"},
            {"Transfer-Encoding":"chunked"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/dashboard",headers=headers)[0],403)

    def test_closed_routes_windows_and_no_writes(self):
        for method in ("POST","PUT","PATCH","DELETE","OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.request("/api/dashboard",method=method)[0],405)
        for path in ("/api/execute","/server.py","/../server.py"):
            self.assertEqual(self.request(path)[0],404)
        for query in ("window=30d","url=https://evil.invalid","window=1h&window=7d",
                      "refresh=yes","window=","window","a=1&b=2&c=3"):
            with self.subTest(query=query):
                self.assertEqual(self.request("/api/dashboard?"+query)[0],400)
        self.assertEqual(self.request("/api/dashboard?window=7d&refresh=1")[0],200)
        self.dashboard.snapshot.assert_called_with("7d",True)

    def test_health_does_not_claim_azure_readiness(self):
        status,_,body=self.request("/healthz")
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body),{"status":"running","mode":"read-only","binding":"loopback"})
        self.assertEqual(self.request(method="HEAD")[2],b"")

    def test_journey_route_validation_and_explicit_unavailable(self):
        identity = "00000021-000a-4000-a000-000000000021"
        for path in ("/api/incidents/not-a-uuid", "/api/incidents/"+identity+"/messages",
                     "/api/incidents/"+identity+"?window=24h",
                     "/api/incidents/"+identity+"?action=approve"):
            self.assertEqual(self.request(path)[0],400)
        self.assertEqual(self.request("/api/incidents/"+identity+"?refresh=1")[0],200)
        self.dashboard.incident.assert_called_with(identity,True)
        with patch.object(self.dashboard,"incident",side_effect=ReadError("NOT_FOUND","Not in fixed scope")):
            self.assertEqual(self.request("/api/incidents/"+identity)[0],404)
        for path in ("/evidence","/evidence.html"):
            status,_,body=self.request(path)
            self.assertEqual(status,200)
            self.assertIn(b'id="disk-chart"',body)
        self.assertIn(b'id="canvas"',self.request()[2])


class HostedConfigurationTests(unittest.TestCase):
    def test_hosting_requires_complete_explicit_configuration(self):
        for settings in (
            {"host": "0.0.0.0"},
            {"host": "0.0.0.0", "public_origin": HOSTED_ORIGIN},
            {"host": "0.0.0.0", "managed_identity_client_id": HOSTED_CLIENT_ID},
            {"public_origin": HOSTED_ORIGIN},
            {"managed_identity_client_id": HOSTED_CLIENT_ID},
            {"host": "192.0.2.1"},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                DashboardServer(0, Mock(), **settings)

    def test_invalid_origins_are_rejected_before_listening(self):
        for origin in (
            "", "http://dashboard.example.com", "https://localhost",
            HOSTED_ORIGIN + ":443", HOSTED_ORIGIN + "/api",
            HOSTED_ORIGIN + "?query=1", HOSTED_ORIGIN + "#fragment",
            "https://user@dashboard.example.com", "https://[invalid",
            "https://dashboard.example.com\n", "https://DASHBOARD.example.com",
            "https://dashboard..example.com",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                DashboardServer(0, Mock(), host="0.0.0.0", public_origin=origin,
                                managed_identity_client_id=HOSTED_CLIENT_ID)


class HostedHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = Mock()
        cls.dashboard.snapshot.return_value = {"schema": "asre-dashboard/v1", "sources": {}}
        cls.dashboard.incident.return_value = {"schema": "asre-journey/v1", "status": "ok"}
        cls.server = DashboardServer(0, cls.dashboard, host="0.0.0.0",
                                    public_origin=HOSTED_ORIGIN,
                                    managed_identity_client_id=HOSTED_CLIENT_ID)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path="/", method="GET", headers=None):
        return HttpTests.request(self, path, method, {
            "Host": HOSTED_ORIGIN.removeprefix("https://"), **(headers or {}),
        })

    def test_pages_api_and_hosted_health_with_exact_host(self):
        for path in ("/", "/evidence", "/api/dashboard", "/healthz"):
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.server.server_address[0], "0.0.0.0")
        self.assertEqual(json.loads(self.request("/healthz")[2]),
                         {"status": "running", "mode": "read-only", "binding": "hosted"})

    def test_hosted_mode_retains_closed_origin_and_request_guards(self):
        for headers in (
            {"Host": "evil.invalid"}, {"Host": "127.0.0.1"},
            {"Origin": "https://evil.invalid"}, {"Origin": "null"},
            {"Origin": HOSTED_ORIGIN.replace("https://", "http://")},
            {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
            {"Content-Length": "1"}, {"Transfer-Encoding": "chunked"},
            {"X-Forwarded-Host": "evil.invalid", "Host": "evil.invalid"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("/api/dashboard", headers=headers)[0], 403)
        self.assertEqual(self.request("/api/dashboard", headers={
            "Origin": HOSTED_ORIGIN, "Sec-Fetch-Site": "same-origin",
        })[0], 200)
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self.assertEqual(self.request("/api/dashboard", method)[0], 405)

    def test_login_redirect_navigation_does_not_open_cross_site_api_reads(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                   "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("/", headers=headers)[0], 200)
        self.assertEqual(self.request("/api/dashboard", headers=headers)[0], 403)
        self.assertEqual(self.request("/api/dashboard", headers={
            **headers, "Origin": "https://evil.invalid",
        })[0], 403)


if __name__ == "__main__":
    unittest.main()
