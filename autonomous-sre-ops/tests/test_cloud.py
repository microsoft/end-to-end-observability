import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from operations.cloud import ARM, MAX_RESPONSE, AzureReader, AzureReadError, NoRedirect
from operations.lifecycle import TARGETS


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.reader = AzureReader(Mock(get_token=Mock(return_value=SimpleNamespace(token="unit-test-only"))))
        self.targets = {role: target for target, role in TARGETS.items()}

    def request(self, payload):
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(payload))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch("operations.cloud.build_opener", return_value=opener):
            return self.reader.request("GET", ARM + self.targets["linux"])

    def test_actual_arm_log_analytics_capitalized_envelope(self):
        result = {"Tables": [
            {"TableName": "Table_0", "Columns": [
                {"ColumnName": name} for name in ("SourceTable", "TimeGenerated", "_ResourceId")
            ], "Rows": [["Heartbeat", "fixture-time", self.targets["linux"]]]},
            {"TableName": "Table_2", "Columns": [
                {"ColumnName": name} for name in ("StatusCode", "Severity", "SeverityName")
            ], "Rows": [[0, 4, "Info"], [0, 6, "Stats"]]},
        ]}
        with patch.object(self.reader, "request", return_value=result) as request:
            evidence = self.reader.telemetry(self.targets["linux"])
            self.assertEqual(evidence["rows"], result["Tables"][0]["Rows"])
            self.assertEqual(evidence["row_count"], 1)
            self.assertIn("api-version=2020-08-01", request.call_args.args[1])
            self.assertIn("top 10", request.call_args.args[2]["query"])
            self.assertIn("top 60", request.call_args.args[2]["query"])
            self.assertIn("top 30", request.call_args.args[2]["query"])
            query = request.call_args.args[2]["query"]
            perf = query.split("(Perf ", 1)[1].split("SourceTable='Perf')", 1)[0]
            self.assertLess(perf.index("InstanceName == 'Memory'"), perf.index("top 60"))
            self.assertIn("CounterName in ('% Used Memory', 'Available MBytes Memory')", perf)
            self.assertIn("CounterName == '% Used Space'", perf)
            self.assertIn("InstanceName in ('/', '/srv/asre-demo')", perf)
            self.assertEqual(query.count("InstanceName == 'Memory'"), 1)
            self.assertEqual(query.count("TimeGenerated > ago(30m)"), 3)
            self.assertEqual(query.count(f"tolower(_ResourceId) == '{self.targets['linux']}'"), 3)
            self.assertEqual(request.call_args.args[2]["timespan"], "PT30M")
            self.assertIn("perf_scope", evidence)
            self.assertEqual(evidence["syslog_scope"], "ProcessName, ProcessID, Facility, SeverityLevel, SyslogMessage")
            for column in ("ProcessName", "ProcessID", "Facility"):
                self.assertIn(column, query.rsplit(" | project ", 1)[1])
        result["Tables"][1]["Rows"].append([1234, 2, "Error"])
        with patch.object(self.reader, "request", return_value=result), self.assertRaises(AzureReadError):
            self.reader.telemetry(self.targets["linux"])
        for bad in ({"tables": []}, {"Tables": []}, {"Tables": [{"Columns": [], "Rows": [[1]]}]}):
            with self.subTest(bad=bad), patch.object(self.reader, "request", return_value=bad), self.assertRaises(AzureReadError):
                self.reader.telemetry(self.targets["linux"])

    def test_syslog_warning_retains_component_provenance(self):
        columns = ["SourceTable", "TimeGenerated", "_ResourceId", "ProcessName",
                   "ProcessID", "Facility", "SeverityLevel", "SyslogMessage"]
        rows = [
            ["Syslog", "fixture-time", self.targets["linux"], process, pid,
             "daemon", "warning", "No DCR is associated with this VM."]
            for process, pid in (("MetricsExtension", 2586), ("amacoreagent", 3000))
        ]
        result = {"Tables": [
            {"Columns": [{"ColumnName": name} for name in columns], "Rows": rows},
            {"Columns": [{"ColumnName": name} for name in
                         ("StatusCode", "Severity", "SeverityName")], "Rows": [[0, 4, "Info"]]},
        ]}
        with patch.object(self.reader, "request", return_value=result):
            evidence = self.reader.telemetry(self.targets["linux"])
        self.assertEqual(evidence["columns"], columns)
        self.assertEqual(evidence["rows"], rows)
        self.assertEqual(evidence["row_count"], 2)
        self.assertEqual(evidence["query_status"], "completed")
        result["Tables"][0]["Rows"] = rows * 50
        with patch.object(self.reader, "request", return_value=result):
            self.assertEqual(self.reader.telemetry(self.targets["linux"])["row_count"], 100)
        result["Tables"][0]["Rows"].append(rows[0])
        with patch.object(self.reader, "request", return_value=result), self.assertRaises(AzureReadError):
            self.reader.telemetry(self.targets["linux"])

    def test_graph_tables_are_separate_and_counts_are_verified(self):
        rows = {"data": [], "count": 0, "totalRecords": 0}
        with patch.object(self.reader, "request", side_effect=[dict(rows), dict(rows)]) as request:
            self.reader.patches(self.targets["patch"])
            self.assertEqual(request.call_count, 2)
            self.assertTrue(request.call_args_list[0].args[2]["query"].startswith("patchassessmentresources "))
            self.assertTrue(request.call_args_list[1].args[2]["query"].startswith("patchinstallationresources "))
        with patch.object(self.reader, "request", return_value={**rows, "totalRecords": 1}), self.assertRaises(AzureReadError):
            self.reader.patches(self.targets["patch"])
        with self.assertRaises(PermissionError):
            self.reader.patches(self.targets["backup"])

    def test_backup_requires_real_unpaginated_job_and_recovery_point_envelopes(self):
        with patch.object(self.reader, "request", side_effect=[{"value": []}, {"value": []}]) as request:
            value = self.reader.backup(self.targets["backup"])
            self.assertEqual(value["source"], "AzureBackup")
            self.assertNotIn("2026-10-14T00", request.call_args_list[0].args[1])
        for bad in ({"value": [], "nextLink": "https://do-not-follow.example"}, {"items": []}):
            with self.subTest(bad=bad), patch.object(self.reader, "request", side_effect=[bad, {"value": []}]), self.assertRaises(AzureReadError):
                self.reader.backup(self.targets["backup"])

    def test_bad_partial_large_and_non_json_evidence_is_not_success(self):
        for data in (b"not json", b"[]", b'{"error":{"code":"PartialError"}}', b"x" * (MAX_RESPONSE + 1)):
            with self.subTest(prefix=data[:30]), self.assertRaises(AzureReadError):
                self.request(data)
        self.assertEqual(self.request(b'{"value":[]}'), {"value": []})

    def test_token_redirects_and_out_of_scope_urls_are_forbidden(self):
        with self.assertRaises(AzureReadError):
            NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://not-azure.example/")
        for url in ("https://not-azure.example", ARM + "/subscriptions/another/vm", ARM + ".example/subscriptions/x"):
            with self.subTest(url=url), self.assertRaises(PermissionError):
                self.reader.request("GET", url)
        opener = Mock()
        opener.open.side_effect = HTTPError(ARM, 403, "Forbidden", {}, None)
        with patch("operations.cloud.build_opener", return_value=opener), self.assertRaisesRegex(AzureReadError, "HTTP 403"):
            self.reader.request("GET", ARM + self.targets["linux"])
