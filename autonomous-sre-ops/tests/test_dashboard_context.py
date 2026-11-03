"""The dashboard follows the deployment context and stays importable without the optional parts.

Expected strings are written out by hand from tests/fixtures/context.alt.json; nothing here calls the
derivation under test. Every context other than the process-wide synthetic one runs in a fresh interpreter,
so the module-level constants of dashboard.queries, dashboard.server and dashboard.journey are exercised
under a deployment the repository never hard-coded.
"""

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.alt_context import ALT, ROOT, SYNTHETIC, leaks_in_module, run_with_context, synthetic_only_values

DASHBOARD = ROOT / "dashboard"
TESTS = ROOT / "tests"

ALT_SUBSCRIPTION = "00000901-000a-4000-8000-000000000901"
ALT_TENANT = "00000902-000a-4000-8000-000000000902"
ALT_CUSTOMER_ID = "00000962-000a-4000-8000-000000000962"
ALT_ORIGIN = "https://alt-instance.service-now.com"
ALT_BASE = "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups"
ALT_CONTROL = ALT_BASE + "/rg-asre-control-cus"
ALT_AGENT = ALT_CONTROL + "/providers/Microsoft.App/agents/asre-demo-cus"
ALT_WORKSPACE = ALT_CONTROL + "/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
ALT_LINUX = ALT_BASE + "/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus"
ALT_PATCH = ALT_BASE + "/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus"
ALT_VAULT = ALT_BASE + "/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"
RULE_PREFIX = ALT_CONTROL + "/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-"
ALT_RULES = tuple(RULE_PREFIX + name for name in
                  ("disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job"))
ALT_CASE_URL = ALT_ORIGIN + "/incident.do?sys_id=" + "0" * 30 + "7b"

SYNTHETIC_BASE = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups"
SYNTHETIC_CONTROL = SYNTHETIC_BASE + "/rg-asre-control-cus"
SYNTHETIC_AGENT = SYNTHETIC_CONTROL + "/providers/Microsoft.App/agents/asre-demo-cus"
SYNTHETIC_WORKSPACE = SYNTHETIC_CONTROL + "/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
SYNTHETIC_LINUX = SYNTHETIC_BASE + "/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus"
SYNTHETIC_PATCH = SYNTHETIC_BASE + "/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus"
SYNTHETIC_VAULT = (SYNTHETIC_BASE
                   + "/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus")
SYNTHETIC_RULE = SYNTHETIC_CONTROL + "/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-disk"
SYNTHETIC_CASE_URL = ("https://example-instance.service-now.com/incident.do?sys_id="
                      "0000000000000000000000000000006a")

STAMP = "2026-01-01T12:00:00+00:00"
EXPECTED_RULE_BLOCK = (
    "| where tostring(p.essentials.alertRule) in~ (\n"
    '    "' + ALT_RULES[0] + '",\n'
    '    "' + ALT_RULES[1] + '",\n'
    '    "' + ALT_RULES[2] + '",\n'
    '    "' + ALT_RULES[3] + '",\n'
    '    "' + ALT_RULES[4] + '")\n'
)


def child(code, context_file=ALT, spec=None, **options):
    """Run ``code`` in a fresh interpreter under ``context_file``; ``SPEC`` holds the decoded ``spec``."""
    prefix = "import json\nSPEC = json.loads(" + repr(json.dumps(spec)) + ")\n"
    result = run_with_context(prefix + code, context_file=context_file, **options)
    if result.returncode != 0:
        raise AssertionError(f"Child run under {context_file} failed:\n{result.stderr[-2500:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


CONSTANTS_PROBE = """
from dashboard import queries, server
names = ("SUBSCRIPTION", "TENANT", "CONTROL", "AGENT_ID", "WORKSPACE_ID", "LINUX_ID", "PATCH_ID", "BACKUP_VAULT")
print(json.dumps({
    "constants": {name: getattr(queries, name) for name in names},
    "rule_ids": list(queries.RULE_IDS),
    "arm_reads": server.ARM_READS,
    "queries": dict(queries.QUERIES),
    "filesystem_7d": queries.query_for("filesystem", "7d"),
    "filesystem_24h": queries.query_for("filesystem", "24h"),
}))
"""

BINDING_PROBE = """
from unittest.mock import patch
from test_dashboard import client
from dashboard.server import AzureReads, ReadError


def verdict(name, row):
    try:
        AzureReads._validate_row(name, row)
    except ReadError:
        return "rejected"
    return "accepted"


def outcome(action):
    try:
        return action()
    except ReadError:
        return "rejected"


result = {
    "agent": client(SPEC["agent"]).agent(),
    "agent_foreign": outcome(lambda: client(SPEC["agent_foreign"]).agent()),
    "workspace": client(SPEC["workspace"]).workspace(),
    "workspace_foreign": outcome(lambda: client(SPEC["workspace_foreign"]).workspace()),
    "rows": {label: verdict(name, row) for label, name, row in SPEC["rows"]},
}
with patch("dashboard.server.AzureCliCredential") as cli:
    AzureReads()
    result["cli_arguments"] = dict(cli.call_args.kwargs)
print(json.dumps(result))
"""

CASE_PROBE = """
from dashboard import journey, queries, server
from dashboard.journey import CASE_LINK, build_journey


def references(url):
    row = {"id": "message-1", "timeStamp": SPEC["stamp"], "author": {"role": "SREAgent"},
           "messageType": None, "text": "Created [INC0900008](" + url + ")."}
    thread = {"id": "thread-1", "title": "Native incident", "created_at": SPEC["stamp"],
              "updated_at": SPEC["stamp"], "rule_id": journey.catalog()[0]["rule_id"],
              "incident_state": "resolved", "execution_state": "Complete",
              "plan_id": "asre-azure-monitor-review"}
    messages = {"status": "ok", "checked_at": SPEC["stamp"], "data": [row]}
    approvals = {"status": "empty", "checked_at": SPEC["stamp"], "data": []}
    return build_journey(thread, messages, approvals)["case_references"]


def matches(url):
    return CASE_LINK.search("[INC0900008](" + url + ")") is not None


print(json.dumps({
    "subscription": queries.SUBSCRIPTION,
    "arm_agent": server.ARM_READS["agent"],
    "pattern": CASE_LINK.pattern,
    "alt_match": matches(SPEC["alt_url"]),
    "synthetic_match": matches(SPEC["synthetic_url"]),
    "negatives": [matches(url) for url in SPEC["negatives"]],
    "alt_references": references(SPEC["alt_url"]),
    "synthetic_references": references(SPEC["synthetic_url"]),
}))
"""


class LeakTests(unittest.TestCase):
    def test_no_module_keeps_a_value_unique_to_the_primary_synthetic_context(self):
        for name in ("dashboard", "dashboard.queries", "dashboard.server", "dashboard.journey"):
            with self.subTest(module=name):
                self.assertEqual(leaks_in_module(name), [])

    def test_no_dashboard_file_or_bootstrap_helper_carries_a_synthetic_only_value(self):
        files = [path for path in DASHBOARD.iterdir() if path.is_file()]
        files.append(ROOT / "scripts" / "Initialize-DashboardAuthentication.ps1")
        needles = synthetic_only_values()
        self.assertGreater(len(needles), 10)
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for needle in needles:
                with self.subTest(file=path.name, needle=needle):
                    self.assertNotIn(needle, text)


class AltConstantsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = child(CONSTANTS_PROBE)

    def test_resource_constants_follow_the_context(self):
        self.assertEqual(self.found["constants"], {
            "SUBSCRIPTION": ALT_SUBSCRIPTION, "TENANT": ALT_TENANT, "CONTROL": ALT_CONTROL,
            "AGENT_ID": ALT_AGENT, "WORKSPACE_ID": ALT_WORKSPACE, "LINUX_ID": ALT_LINUX,
            "PATCH_ID": ALT_PATCH, "BACKUP_VAULT": ALT_VAULT,
        })

    def test_rule_ids_and_arm_reads_follow_the_context(self):
        self.assertEqual(self.found["rule_ids"], list(ALT_RULES))
        arm = "https://management.azure.com"
        expected = {"agent": arm + ALT_AGENT + "?api-version=2026-01-01",
                    "workspace": arm + ALT_WORKSPACE + "?api-version=2023-09-01"}
        for name, rule in zip(("disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job"),
                              ALT_RULES):
            expected[name] = arm + rule + "?api-version=2023-12-01"
        self.assertEqual(self.found["arm_reads"], expected)

    def test_no_query_text_carries_a_synthetic_only_value(self):
        needles = synthetic_only_values()
        for name, text in self.found["queries"].items():
            for needle in needles:
                with self.subTest(query=name, needle=needle):
                    self.assertNotIn(needle, text.lower())

    def test_filesystem_query_binds_the_linux_target(self):
        text = self.found["queries"]["filesystem"]
        self.assertIn('| where _ResourceId =~ "' + ALT_LINUX + '"\n', text)
        self.assertIn("bin(TimeGenerated, 5m)", self.found["filesystem_24h"])
        self.assertIn("bin(TimeGenerated, 15m)", self.found["filesystem_7d"])
        self.assertIn("ago(7d)", self.found["filesystem_7d"])

    def test_alert_query_lists_the_five_rules_in_the_exact_layout(self):
        text = self.found["queries"]["alerts"]
        self.assertIn('| where subscriptionId =~ "' + ALT_SUBSCRIPTION + '"\n', text)
        self.assertIn(EXPECTED_RULE_BLOCK, text)
        self.assertEqual(text.count("scheduledQueryRules/asre-demo-"), 5)

    def test_patch_and_backup_queries_carry_their_groups_prefixes_and_vm(self):
        patch_text = self.found["queries"]["patch"]
        self.assertIn('| where subscriptionId =~ "' + ALT_SUBSCRIPTION + '"\n', patch_text)
        self.assertIn('| where resourceGroup =~ "rg-asre-patch-cus"\n', patch_text)
        self.assertIn('| where id startswith "' + ALT_PATCH + '/patchInstallationResults/"\n', patch_text)
        backup_text = self.found["queries"]["backup"]
        self.assertIn('| where subscriptionId =~ "' + ALT_SUBSCRIPTION + '"\n', backup_text)
        self.assertIn('| where resourceGroup =~ "rg-asre-backup-cus"\n', backup_text)
        self.assertIn('| where id startswith "' + ALT_VAULT + '/backupJobs/"\n', backup_text)
        self.assertIn('| where tostring(p.entityFriendlyName) == "vm-asre-backup-cus"\n', backup_text)


class AltBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def alert(rule, workspace):
            return {"rule_id": rule, "fired_at": STAMP,
                    "alert_id": workspace + "/providers/Microsoft.AlertsManagement/alerts/alert-1"}

        def backup(vault, vm):
            return {"job_id": vault + "/backupJobs/job-1", "status": "Completed", "vm": vm,
                    "operation": "Backup", "started_at": STAMP}

        def filesystem(resource):
            return {"bucket": STAMP, "latest_sample": STAMP, "resource_id": resource,
                    "mount": "/srv/asre-demo", "sample_count": 2, "used_percent": 1.5,
                    "min_used": 1, "max_used": 2}

        agent = {"agentEndpoint": "https://asre-demo-cus--test.cluster.centralus.azuresre.ai",
                 "powerState": "Running", "provisioningState": "Succeeded",
                 "actionConfiguration": {"mode": "Review", "accessLevel": "Low"}}
        patch_row = {"status": "Failed", "observed_at": STAMP}
        rows = [
            ("alert-alt", "alerts", alert(ALT_RULES[0], ALT_WORKSPACE)),
            ("alert-last-rule", "alerts", alert(ALT_RULES[4], ALT_WORKSPACE)),
            ("alert-synthetic-rule", "alerts", alert(SYNTHETIC_RULE, ALT_WORKSPACE)),
            ("alert-synthetic-workspace", "alerts", alert(ALT_RULES[0], SYNTHETIC_WORKSPACE)),
            ("alert-unlisted-rule", "alerts", alert(RULE_PREFIX + "other", ALT_WORKSPACE)),
            ("patch-alt", "patch", {**patch_row, "installation_id": ALT_PATCH + "/patchInstallationResults/a"}),
            ("patch-synthetic", "patch",
             {**patch_row, "installation_id": SYNTHETIC_PATCH + "/patchInstallationResults/a"}),
            ("backup-alt", "backup", backup(ALT_VAULT, "vm-asre-backup-cus")),
            ("backup-synthetic-vault", "backup", backup(SYNTHETIC_VAULT, "vm-asre-backup-cus")),
            ("backup-vm-case", "backup", backup(ALT_VAULT, "VM-ASRE-BACKUP-CUS")),
            ("backup-vm-one-short", "backup", backup(ALT_VAULT, "vm-asre-backup-cu")),
            ("backup-vm-one-long", "backup", backup(ALT_VAULT, "vm-asre-backup-cusx")),
            ("backup-other-vm", "backup", backup(ALT_VAULT, "vm-asre-patch-cus")),
            ("filesystem-alt", "filesystem", filesystem(ALT_LINUX)),
            ("filesystem-synthetic", "filesystem", filesystem(SYNTHETIC_LINUX)),
        ]
        cls.found = child(BINDING_PROBE, pythonpath=(TESTS,), spec={
            "agent": {"id": ALT_AGENT, "properties": agent},
            "agent_foreign": {"id": SYNTHETIC_AGENT, "properties": agent},
            "workspace": {"id": ALT_WORKSPACE, "properties": {"customerId": ALT_CUSTOMER_ID}},
            "workspace_foreign": {"id": SYNTHETIC_WORKSPACE, "properties": {"customerId": ALT_CUSTOMER_ID}},
            "rows": rows,
        })

    def test_agent_and_workspace_reads_bind_to_the_context_resources(self):
        agent = self.found["agent"]
        self.assertEqual((agent["name"], agent["power"], agent["mode"], agent["access"]),
                         ("asre-demo-cus", "Running", "Review", "Low"))
        self.assertEqual(self.found["workspace"], {"name": "law-asre-cus", "customer_id": ALT_CUSTOMER_ID})
        self.assertEqual(self.found["agent_foreign"], "rejected")
        self.assertEqual(self.found["workspace_foreign"], "rejected")

    def test_rows_of_this_deployment_are_accepted_and_foreign_rows_rejected(self):
        self.assertEqual(self.found["rows"], {
            "alert-alt": "accepted", "alert-last-rule": "accepted",
            "alert-synthetic-rule": "rejected", "alert-synthetic-workspace": "rejected",
            "alert-unlisted-rule": "rejected",
            "patch-alt": "accepted", "patch-synthetic": "rejected",
            "backup-alt": "accepted", "backup-synthetic-vault": "rejected",
            "backup-vm-case": "rejected", "backup-vm-one-short": "rejected",
            "backup-vm-one-long": "rejected", "backup-other-vm": "rejected",
            "filesystem-alt": "accepted", "filesystem-synthetic": "rejected",
        })

    def test_the_local_cli_credential_is_pinned_to_the_context_tenant(self):
        self.assertEqual(self.found["cli_arguments"], {"tenant_id": ALT_TENANT, "process_timeout": 30})


class CaseLinkTests(unittest.TestCase):
    NEGATIVES = (
        SYNTHETIC_CASE_URL,
        ALT_CASE_URL + "&redirect=evil",
        ALT_CASE_URL.replace("https:", "http:"),
        ALT_CASE_URL.replace("incident.do", "other.do"),
        ALT_CASE_URL.replace("alt-instance.service-now", "alt-instance-service-now"),
        ALT_CASE_URL.replace("alt-instance.service", "alt-instancexservice"),
        ALT_CASE_URL.replace(".com/", ".com.evil.invalid/"),
        ALT_CASE_URL.replace("https://", "https://evil."),
        "https://evil.invalid/incident.do?sys_id=" + "a" * 32,
    )

    @classmethod
    def setUpClass(cls):
        cls.found = child(CASE_PROBE, spec={
            "stamp": STAMP, "alt_url": ALT_CASE_URL, "synthetic_url": SYNTHETIC_CASE_URL,
            "negatives": list(cls.NEGATIVES),
        })

    def test_the_pattern_is_built_from_the_context_origin(self):
        pattern = self.found["pattern"]
        self.assertIn(r"https://alt-instance\.service-now\.com/incident\.do\?sys_id=", pattern)
        self.assertNotIn("example-instance", pattern)
        self.assertEqual(self.found["subscription"], ALT_SUBSCRIPTION)

    def test_the_alt_origin_matches_and_the_synthetic_origin_does_not(self):
        self.assertTrue(self.found["alt_match"])
        self.assertFalse(self.found["synthetic_match"])
        self.assertEqual(self.found["negatives"], [False] * len(self.NEGATIVES))

    def test_the_journey_reports_the_alt_case_reference_only(self):
        references = self.found["alt_references"]
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["url"], ALT_CASE_URL)
        self.assertEqual(references[0]["number"], "INC0900008")
        self.assertEqual(references[0]["sys_id"], "0" * 30 + "7b")
        self.assertEqual(references[0]["verification"], "agent_reported")
        self.assertEqual(self.found["synthetic_references"], [])


class OptionalContextTests(unittest.TestCase):
    """The dashboard imports and answers with the optional context parts missing (servicenow, endpoint, operator)."""

    VARIANTS = {
        "servicenow-null": (True, lambda ctx: ctx.update(servicenow=None)),
        "endpoint-null": (False, lambda ctx: ctx["agent"].update(endpoint=None)),
        "operator-removed": (False, lambda ctx: ctx.pop("operator_object_id")),
        "all-three-removed": (True, lambda ctx: (ctx.pop("servicenow"), ctx["agent"].pop("endpoint"),
                                                 ctx.pop("operator_object_id"))),
    }

    @classmethod
    def setUpClass(cls):
        folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(folder.cleanup)
        spec = {"stamp": STAMP, "alt_url": ALT_CASE_URL, "synthetic_url": SYNTHETIC_CASE_URL,
                "negatives": list(CaseLinkTests.NEGATIVES)}
        cls.found = {}
        for name, (_, mutate) in cls.VARIANTS.items():
            context = json.loads(ALT.read_text(encoding="utf-8"))
            mutate(context)
            path = Path(folder.name) / f"context.{name}.json"
            path.write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
            cls.found[name] = child(CASE_PROBE, context_file=path, spec=spec)

    def test_every_module_imports_and_keeps_the_alt_deployment(self):
        for name, found in self.found.items():
            with self.subTest(variant=name):
                self.assertEqual(found["subscription"], ALT_SUBSCRIPTION)
                self.assertEqual(found["arm_agent"],
                                 "https://management.azure.com" + ALT_AGENT + "?api-version=2026-01-01")

    def test_without_servicenow_no_case_link_is_ever_recognised(self):
        for name, (without_servicenow, _) in self.VARIANTS.items():
            if not without_servicenow:
                continue
            found = self.found[name]
            with self.subTest(variant=name):
                self.assertIn("(?!)", found["pattern"])
                self.assertNotIn("service-now", found["pattern"])
                self.assertFalse(found["alt_match"])
                self.assertFalse(found["synthetic_match"])
                self.assertEqual(found["negatives"], [False] * len(CaseLinkTests.NEGATIVES))
                self.assertEqual(found["alt_references"], [])
                self.assertEqual(found["synthetic_references"], [])

    def test_with_servicenow_present_only_the_context_origin_is_recognised(self):
        for name, (without_servicenow, _) in self.VARIANTS.items():
            if without_servicenow:
                continue
            found = self.found[name]
            with self.subTest(variant=name):
                self.assertTrue(found["alt_match"])
                self.assertFalse(found["synthetic_match"])
                self.assertEqual(len(found["alt_references"]), 1)
                self.assertEqual(found["synthetic_references"], [])


class SyntheticContextTests(unittest.TestCase):
    def test_the_synthetic_case_url_is_still_recognised(self):
        found = child(CASE_PROBE, context_file=SYNTHETIC, spec={
            "stamp": STAMP, "alt_url": ALT_CASE_URL, "synthetic_url": SYNTHETIC_CASE_URL, "negatives": [],
        })
        self.assertTrue(found["synthetic_match"])
        self.assertFalse(found["alt_match"])
        self.assertEqual([item["url"] for item in found["synthetic_references"]], [SYNTHETIC_CASE_URL])
        self.assertEqual(found["alt_references"], [])
        self.assertEqual(found["subscription"], "00000001-000a-4000-a000-000000000001")


class NoContextTests(unittest.TestCase):
    def test_modules_that_read_the_context_refuse_to_import_without_one(self):
        for name in ("queries", "journey", "server"):
            with self.subTest(module=name):
                result = run_with_context(f"import dashboard.{name}", context_file=None)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ContextError", result.stderr)

    def test_the_package_itself_imports_without_a_context(self):
        result = run_with_context("import dashboard", context_file=None)
        self.assertEqual(result.returncode, 0, result.stderr)


class ContainerClosureTests(unittest.TestCase):
    def test_the_vendored_context_is_byte_identical_to_the_operations_copy(self):
        self.assertEqual((DASHBOARD / "context.py").read_bytes(), (ROOT / "operations" / "context.py").read_bytes())

    def test_the_dockerfile_copies_every_module_and_never_the_whole_context(self):
        text = (DASHBOARD / "Dockerfile").read_text(encoding="utf-8")
        copies = [line.split() for line in text.splitlines() if line.startswith("COPY ")]
        self.assertFalse(re.search(r"(?m)^COPY\s+(?:--\S+\s+)*\.\s", text))
        runtime = [parts for parts in copies if parts[-1] == "./dashboard/"]
        self.assertEqual(len(runtime), 1)
        copied = set(runtime[0][1:-1])
        self.assertIn("context.py", copied)
        modules = {path.name for path in DASHBOARD.glob("*.py")}
        self.assertEqual(modules, {name for name in copied if name.endswith(".py")})
        self.assertLessEqual({"index.html", "evidence.html"}, copied)

    def test_the_dockerignore_admits_exactly_what_the_dockerfile_copies(self):
        lines = (DASHBOARD / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "*")
        admitted = {line[1:] for line in lines[1:] if line.startswith("!")}
        self.assertIn("context.py", admitted)
        self.assertLessEqual({path.name for path in DASHBOARD.glob("*.py")}, admitted)

    def test_no_dashboard_module_imports_outside_its_own_container_closure(self):
        for path in sorted(DASHBOARD.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    roots = {(node.module or "").split(".")[0]} if node.level == 0 else {"<relative>"}
                else:
                    continue
                with self.subTest(file=path.name, line=node.lineno):
                    self.assertFalse(roots & {"operations", "tests", "scripts", "<relative>"}, roots)

    def test_the_client_side_case_link_check_is_not_pinned_to_one_instance(self):
        html = (DASHBOARD / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("example-instance", html)
        self.assertNotIn("alt-instance", html)
        self.assertIn(r"https:\/\/[a-z0-9-]+\.service-now\.com\/incident\.do\?sys_id=[0-9a-f]{32}$", html)


if __name__ == "__main__":
    unittest.main()
