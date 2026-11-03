"""Hosting boundary and credential bootstrap checks; no Azure writes."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock

from dashboard.server import log_source_probe

ROOT = Path(__file__).resolve().parents[1]


class DashboardHostingTests(unittest.TestCase):
    def test_authentication_bootstrap_fixtures(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell is required for operator bootstrap fixtures.")
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File",
             str(ROOT / "tests" / "Test-DashboardAuthentication.ps1")],
            cwd=ROOT, capture_output=True, text=True, timeout=90, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no actual directory or vault request", result.stdout)

    def test_native_authentication_is_group_restricted(self):
        source = (ROOT / "infra" / "dashboard.tf").read_text(encoding="utf-8")
        self.assertIn('dashboard_group_id   = "00000042-000a-4000-8000-000000000042"', source)
        self.assertIn("allowedPrincipals = { groups = [local.dashboard_group_id] }", source)
        self.assertIn('unauthenticatedClientAction = "RedirectToLoginPage"', source)
        self.assertIn("excludedPaths               = []", source)
        self.assertIn("httpSettings = { requireHttps = true }", source)
        self.assertNotIn("allowedPrincipals = { identities", source)

    def test_template_uses_only_secret_references_and_no_admin_role(self):
        source = (ROOT / "infra" / "dashboard.tf").read_text(encoding="utf-8")
        self.assertIn("keyVaultUrl = var.dashboard.secret_version_uri", source)
        self.assertNotIn("value = var.dashboard.secret", source)
        self.assertNotIn("listSecrets/action", source)
        self.assertNotIn('role_definition_name = "SRE Agent Administrator"', source)
        self.assertNotIn("Microsoft.App/agents/threads/write", source)
        self.assertNotIn("Microsoft.App/agents/threads/approve/action", source)
        self.assertNotIn("Microsoft.OperationalInsights/workspaces/sharedKeys", source)
        self.assertNotIn("jobs/export/action", source)

    def test_staging_and_scale_zero_remain_explicit(self):
        source = (ROOT / "infra" / "dashboard.tf").read_text(encoding="utf-8")
        self.assertIn('external      = var.dashboard.phase == "public"', source)
        self.assertIn("minReplicas = 0", source)
        self.assertIn("maxReplicas = 1", source)
        self.assertIn('args  = var.dashboard.phase == "private" ? ["--probe-sources"] : []', source)
        self.assertIn('"/healthz"', source)
        self.assertIn('name = "Host"', source)

    def test_container_has_closed_copy_inputs_and_nonroot_process(self):
        source = (ROOT / "dashboard" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.13-slim@sha256:", source)
        self.assertIn("USER 65532:65532", source)
        self.assertIn('ENTRYPOINT ["python", "-m", "dashboard.server"]', source)
        self.assertNotIn("COPY . ", source)

    def test_source_probe_logs_only_status_and_error_codes(self):
        dashboard = Mock()
        dashboard.snapshot.return_value = {
            "generated_at": "2026-10-23T14:00:00Z", "status": "partial",
            "sources": {
                "agent": {"status": "ok", "data": {"secret": "must-not-log"}},
                "schedule": {"status": "error",
                             "error": {"code": "HTTP_403", "message": "must-not-log"}},
            },
        }
        with self.assertLogs("asre.dashboard", level="WARNING") as output:
            log_source_probe(dashboard)
        dashboard.snapshot.assert_called_once_with("7d", True)
        self.assertNotIn("must-not-log", output.output[0])
        report = json.loads(output.output[0].split("ASRE_DASHBOARD_SOURCE_PROBE ", 1)[1])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["sources"]["schedule"]["error_code"], "HTTP_403")
        self.assertIn("Empty ARG", report["provider_access"])

    def test_source_probe_marks_success_separately_from_provider_acceptance(self):
        dashboard = Mock()
        dashboard.snapshot.return_value = {
            "generated_at": "2026-10-23T14:00:00Z", "status": "ok",
            "sources": {"agent": {"status": "ok", "data": {}}},
        }
        with self.assertLogs("asre.dashboard", level="INFO") as output:
            log_source_probe(dashboard)
        self.assertIn('"status": "ok"', output.output[0])
        self.assertIn("Empty ARG results do not qualify provider access", output.output[0])


if __name__ == "__main__":
    unittest.main()
