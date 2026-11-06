"""The case engine follows the deployment context and fails closed without its optional parts.

Expected strings are written out by hand from tests/fixtures/context.alt.json; nothing here calls the
derivation under test. Every context other than the process-wide synthetic one runs in a fresh interpreter.
"""

import ast
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from operations import agent_case as ac
from operations import context as deployment
from tests.alt_context import ALT, ROOT, SYNTHETIC, leaks_in_module, run_with_context, synthetic_only_values

ALT_SUBSCRIPTION = "00000901-000a-4000-8000-000000000901"
ALT_AGENT_CLIENT = "00000971-000a-4000-8000-000000000971"
ALT_RUNNER_CLIENT = "00000928-000a-4000-8000-000000000928"
ALT_FACADE_CLIENT = "0000000000000000000000000000095a"
ALT_CUSTOMER_ID = "00000962-000a-4000-8000-000000000962"
ALT_ENDPOINT = "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai"

ALT_CONTROL = "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-control-cus"
ALT_LINUX = ("/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-linux-cus"
             "/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus")
ALT_PATCH = ("/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-patch-cus"
             "/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus")
ALT_BACKUP = ("/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-backup-cus"
              "/providers/Microsoft.Compute/virtualMachines/vm-asre-backup-cus")
ALT_VAULT = ("/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-backup-cus"
             "/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus")
ALT_ITEM = (ALT_VAULT + "/backupFabrics/Azure/protectionContainers/"
            "IaasVMContainer;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus"
            "/protectedItems/VM;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus")
ALT_WORKSPACE = ("/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-control-cus"
                 "/providers/microsoft.operationalinsights/workspaces/law-asre-cus")
ALT_STORAGE = "https://stasree26b179818.blob.core.windows.net"
ALT_SECRET_URL = ("https://kv-asre-e26b179818.vault.azure.net"
                  "/secrets/asre-facade-client-credential?api-version=7.4")
ALT_LOG_QUERY_URL = "https://api.loganalytics.io/v1/workspaces/00000962-000a-4000-8000-000000000962/query"
RULE_PREFIX = ALT_CONTROL + "/providers/Microsoft.Insights/scheduledQueryRules/"

_DISK_COUNTER = ('| where ObjectName == "Logical Disk" and CounterName == "% Used Space" '
                 '| where InstanceName == "/srv/asre-demo" ')
EXPECTED_QUERY = ('Perf | where _ResourceId =~ "' + ALT_LINUX + '" ' + _DISK_COUNTER
                  + '| summarize Value = max(CounterValue) by _ResourceId | where Value >= 80')
EXPECTED_NOISE_QUERY = ('Perf | where _ResourceId =~ "' + ALT_LINUX + '" '
                        '| where ObjectName == "Logical Disk" and CounterName == "% Used Space" '
                        '| where InstanceName == "/srv/asre-demo" and CounterValue >= 80')
GROWTH_PREFIX = ('Perf | where _ResourceId =~ "' + ALT_LINUX + '" ' + _DISK_COUNTER
                 + "| sort by TimeGenerated asc | serialize previous_value=prev(CounterValue), ")
EXPECTED_LEGACY_PATCH_QUERY = (
    'arg("").PatchInstallationResources '
    "| where type =~ 'microsoft.compute/virtualmachines/patchinstallationresults' "
    f"| where id startswith '{ALT_PATCH}/patchInstallationResults/' "
    "| extend observed_at=todatetime(properties.lastModifiedDateTime) "
    "| where observed_at > ago(10m) and observed_at <= now() "
    "| where tostring(properties.status) in ('Failed', 'CompletedWithWarnings') "
    "| where toint(properties.failedPatchCount) > 0 "
    f"| project TimeGenerated=observed_at, resource_id='{ALT_PATCH}', observed_at, installation_id=id, "
    "status=tostring(properties.status), failed_patch_count=toint(properties.failedPatchCount), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)
EXPECTED_LEGACY_OCT22_QUERY = (
    'arg("").PatchInstallationResources '
    "| where type =~ 'microsoft.compute/virtualmachines/patchinstallationresults' "
    f"| where id startswith '{ALT_PATCH}/patchInstallationResults/' "
    "| extend observed_at=todatetime(properties.lastModifiedDateTime) "
    "| where observed_at > ago(10m) "
    "| where tostring(properties.status) in ('Failed', 'CompletedWithWarnings') "
    "| where toint(properties.failedPatchCount) > 0 "
    f"| project resource_id='{ALT_PATCH}', observed_at, installation_id=id, "
    "status=tostring(properties.status), failed_patch_count=toint(properties.failedPatchCount), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)
EXPECTED_BACKUP_GRAPH_QUERY = (
    "recoveryservicesresources "
    "| where type =~ 'microsoft.recoveryservices/vaults/backupjobs' "
    f"| where id startswith '{ALT_VAULT}/backupJobs/' "
    f"| where tostring(properties.backupInstanceId) =~ '{ALT_ITEM}' "
    "| where tostring(properties.backupManagementType) == 'AzureIaasVM' "
    "and tostring(properties.operation) == 'Backup' and tostring(properties.status) == 'Failed' "
    "| extend observed_at=todatetime(properties.endTime) | where observed_at > ago(10m) "
    f"| project resource_id='{ALT_BACKUP}', observed_at, job_id=id, status=tostring(properties.status), "
    "protected_item_id=tostring(properties.backupInstanceId), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)

DUMP = r"""
import json, sys
import operations.agent_case as ac
names = ("RESOURCE", "RULE", "QUERY", "NOISE_QUERY", "GROWTH_QUERY", "STORAGE", "RECEIPT_PREFIX", "SECRET_URL",
         "UAMI", "FACADE_CLIENT", "WORKSPACE", "SRE_ORIGIN", "LOG_WORKSPACE_ID", "LOG_QUERY_URL", "PATCH_RESOURCE",
         "BACKUP_RESOURCE", "BACKUP_VAULT", "BACKUP_ITEM", "LEGACY_PATCH_ALERT_QUERY", "LEGACY_PATCH_OCT22_QUERY",
         "NATIVE_RULE_ID")
out = {name: getattr(ac, name) for name in names}
out["LEGACY_PATCH_ALERT_QUERIES"] = list(ac.LEGACY_PATCH_ALERT_QUERIES)
out["PROFILES"] = {key: {"resource_id": p.resource_id, "rule_id": p.rule_id, "query": p.query,
                         "graph_query": p.graph_query, "telemetry_reference": p.telemetry_reference}
                   for key, p in ac.CASE_PROFILES.items()}
print(json.dumps(out))
"""

BUNDLE_DUMP = r"""
import json, sys
import _asre_case.agent_case as ac
names = ("RESOURCE", "RULE", "STORAGE", "RECEIPT_PREFIX", "SECRET_URL", "UAMI", "FACADE_CLIENT", "WORKSPACE",
         "SRE_ORIGIN", "LOG_WORKSPACE_ID", "LOG_QUERY_URL", "PATCH_RESOURCE", "BACKUP_RESOURCE", "BACKUP_VAULT",
         "BACKUP_ITEM")
out = {name: getattr(ac, name) for name in names}
out["file"] = ac.__file__
out["operations_modules"] = sorted(n for n in sys.modules if n == "operations" or n.startswith("operations."))
out["bundle_modules"] = sorted(n for n in sys.modules if n.startswith("_asre_case"))
print(json.dumps(out))
"""


def alt_document():
    return json.loads(ALT.read_text(encoding="utf-8"))


def fixture_leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from fixture_leaves(item)
    elif isinstance(value, str):
        yield value


def alt_only_values():
    """Lower-cased alt-context values (8+ characters) that the synthetic context does not share."""
    synthetic = {v.lower() for v in fixture_leaves(json.loads(SYNTHETIC.read_text(encoding="utf-8")))}
    return {v.lower() for v in fixture_leaves(alt_document()) if len(v) >= 8} - synthetic


def run_json(code, **options):
    result = run_with_context(code, **options)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-2000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


class FixturePinTests(unittest.TestCase):
    def test_hand_written_expectations_match_the_alt_fixture(self):
        document = alt_document()
        self.assertEqual(document["subscription_id"], ALT_SUBSCRIPTION)
        self.assertEqual(document["identities"]["agent"]["client_id"], ALT_AGENT_CLIENT)
        self.assertEqual(document["identities"]["runner"]["client_id"], ALT_RUNNER_CLIENT)
        self.assertEqual(document["servicenow"]["facade_client_id"], ALT_FACADE_CLIENT)
        self.assertEqual(document["log_analytics"]["customer_id"], ALT_CUSTOMER_ID)
        self.assertEqual(document["agent"]["endpoint"], ALT_ENDPOINT)
        self.assertEqual(document["storage"]["blob_endpoint"], ALT_STORAGE)
        self.assertEqual(document["key_vault"]["uri"], "https://kv-asre-e26b179818.vault.azure.net/")
        self.assertNotEqual(ALT_AGENT_CLIENT, ALT_RUNNER_CLIENT)


class SourceHygieneTests(unittest.TestCase):
    source = (ROOT / "operations" / "agent_case.py").read_text(encoding="utf-8")

    def test_no_digest_and_only_the_fixed_namespace_guid_are_literals(self):
        self.assertEqual(re.findall(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])", self.source), [])
        guids = re.findall(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", self.source)
        self.assertEqual(guids, ["91b92a24-4bb0-580f-9b2a-05a85c649dd7"])
        self.assertIn('NAMESPACE = UUID("91b92a24-4bb0-580f-9b2a-05a85c649dd7")', self.source)

    def test_imports_are_relative_and_leave_out_the_runtime_modules(self):
        imported = []
        for node in ast.walk(ast.parse(self.source)):
            if isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    imported.append(node.module or "")
                else:
                    imported.append("." * node.level + (node.module or "") + ":" + ",".join(a.name for a in node.names))
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        for name in imported:
            self.assertFalse(name == "operations" or name.startswith("operations."), name)
        relative = [name for name in imported if name.startswith(".")]
        self.assertIn(".:context", relative)
        for forbidden in ("lifecycle", "cloud", "worker"):
            self.assertFalse([name for name in relative if forbidden in name], forbidden)


class AltContextTests(unittest.TestCase):
    def test_module_holds_no_value_unique_to_the_synthetic_context(self):
        self.assertEqual(leaks_in_module("operations.agent_case"), [])

    def test_every_deployment_value_follows_the_alt_context(self):
        values = run_json(DUMP)
        self.assertEqual(values["RESOURCE"], ALT_LINUX)
        self.assertEqual(values["RULE"], RULE_PREFIX + "asre-demo-disk")
        self.assertEqual(values["QUERY"], EXPECTED_QUERY)
        self.assertIn(ALT_LINUX, values["QUERY"])
        self.assertIn('"/srv/asre-demo"', values["QUERY"])
        self.assertEqual(values["NOISE_QUERY"], EXPECTED_NOISE_QUERY)
        self.assertTrue(values["GROWTH_QUERY"].startswith(GROWTH_PREFIX))
        self.assertEqual(values["GROWTH_QUERY"].count(ALT_LINUX), 1)
        self.assertEqual(values["STORAGE"], ALT_STORAGE)
        self.assertEqual(values["RECEIPT_PREFIX"], ALT_STORAGE + "/evidence/tool-results/")
        self.assertEqual(values["SECRET_URL"], ALT_SECRET_URL)
        self.assertEqual(values["UAMI"], ALT_AGENT_CLIENT)
        self.assertNotEqual(values["UAMI"], ALT_RUNNER_CLIENT)
        self.assertEqual(values["FACADE_CLIENT"], ALT_FACADE_CLIENT)
        self.assertEqual(values["FACADE_CLIENT"], values["FACADE_CLIENT"].lower())
        self.assertEqual(values["WORKSPACE"], ALT_WORKSPACE)
        self.assertEqual(values["WORKSPACE"], values["WORKSPACE"].lower())
        self.assertEqual(values["SRE_ORIGIN"], ALT_ENDPOINT)
        self.assertEqual(values["LOG_WORKSPACE_ID"], ALT_CUSTOMER_ID)
        self.assertEqual(values["LOG_QUERY_URL"], ALT_LOG_QUERY_URL)
        self.assertEqual(values["PATCH_RESOURCE"], ALT_PATCH)
        self.assertEqual(values["BACKUP_RESOURCE"], ALT_BACKUP)
        self.assertEqual(values["BACKUP_VAULT"], ALT_VAULT)
        self.assertEqual(values["BACKUP_ITEM"], ALT_ITEM)
        self.assertEqual(values["LEGACY_PATCH_ALERT_QUERY"], EXPECTED_LEGACY_PATCH_QUERY)
        self.assertEqual(values["LEGACY_PATCH_OCT22_QUERY"], EXPECTED_LEGACY_OCT22_QUERY)
        self.assertEqual(values["LEGACY_PATCH_ALERT_QUERIES"],
                         [EXPECTED_LEGACY_PATCH_QUERY, EXPECTED_LEGACY_OCT22_QUERY])
        self.assertIsNone(values["NATIVE_RULE_ID"])

    def test_every_case_profile_binds_the_alt_resource_and_rule(self):
        profiles = run_json(DUMP)["PROFILES"]
        expected = {
            "disk": (ALT_LINUX, "asre-demo-disk"),
            "patch-installation": (ALT_PATCH, "asre-demo-patch-installation"),
            "alert-recurrence": (ALT_LINUX, "asre-demo-noise-overlap"),
            "capacity-growth": (ALT_LINUX, "asre-demo-capacity-growth"),
            "backup-job": (ALT_BACKUP, "asre-demo-backup-job"),
        }
        self.assertEqual(sorted(profiles), sorted(expected))
        for name, (resource, rule) in expected.items():
            with self.subTest(profile=name):
                self.assertEqual(profiles[name]["resource_id"], resource)
                self.assertEqual(profiles[name]["rule_id"], RULE_PREFIX + rule)
        for name in ("disk", "alert-recurrence", "capacity-growth"):
            self.assertEqual(profiles[name]["telemetry_reference"], ALT_LOG_QUERY_URL)
        self.assertEqual(profiles["disk"]["query"], EXPECTED_QUERY)
        self.assertEqual(profiles["alert-recurrence"]["query"], EXPECTED_NOISE_QUERY)
        self.assertTrue(profiles["capacity-growth"]["query"].startswith(GROWTH_PREFIX))
        self.assertIn(ALT_PATCH, profiles["patch-installation"]["query"])
        backup = profiles["backup-job"]
        self.assertEqual(backup["graph_query"], EXPECTED_BACKUP_GRAPH_QUERY)
        self.assertEqual(backup["query"], EXPECTED_BACKUP_GRAPH_QUERY.replace(
            "recoveryservicesresources", 'arg("").RecoveryServicesResources', 1))

    def test_alt_context_reports_the_native_disk_rule_as_not_commissioned(self):
        self.assertIsNone(run_json(DUMP)["NATIVE_RULE_ID"])


class CoreOnlyContextTests(unittest.TestCase):
    """A deployment without the ServiceNow section or agent endpoint: the engine must not import."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def core_file(self, name, *, servicenow, endpoint):
        document = alt_document()
        if servicenow == "null":
            document["servicenow"] = None
        elif servicenow == "absent":
            del document["servicenow"]
        if endpoint == "null":
            document["agent"]["endpoint"] = None
        elif endpoint == "absent":
            del document["agent"]["endpoint"]
        path = Path(self.directory.name) / (name + ".json")
        path.write_text(json.dumps(document), encoding="utf-8")
        return path, document

    def test_import_raises_context_error_without_servicenow_endpoint_or_both(self):
        foreign = {value.lower() for value in synthetic_only_values()}
        own = alt_only_values()
        self.assertTrue(foreign and own)
        cases = [(a, "keep") for a in ("null", "absent")] + [("keep", b) for b in ("null", "absent")] \
            + [(a, a) for a in ("null", "absent")]
        for servicenow, endpoint in cases:
            with self.subTest(servicenow=servicenow, endpoint=endpoint):
                path, document = self.core_file(f"core-{servicenow}-{endpoint}", servicenow=servicenow,
                                                endpoint=endpoint)
                parsed = deployment.parse(json.dumps(document))
                self.assertEqual(parsed.servicenow is None, servicenow != "keep")
                self.assertEqual(parsed.agent.endpoint is None, endpoint != "keep")
                result = run_with_context("import operations.agent_case", context_file=path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ContextError", result.stderr)
                self.assertEqual(result.stdout, "")
                combined = (result.stdout + result.stderr).lower()
                for value in foreign | own:
                    self.assertNotIn(value, combined)

    def test_missing_context_also_fails_closed(self):
        result = run_with_context("import operations.agent_case", context_file=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ContextError", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_complete_context_imports_in_the_same_harness(self):
        path, _ = self.core_file("complete", servicenow="keep", endpoint="keep")
        result = run_with_context("import operations.agent_case as ac; print(ac.SRE_ORIGIN)", context_file=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), ALT_ENDPOINT)


class BundleLayoutTests(unittest.TestCase):
    """The case-tool bundle loads the engine as _asre_case.agent_case with only the context file."""

    def stage(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        package = Path(directory.name) / "_asre_case"
        (package / "contracts").mkdir(parents=True)
        for name in ("agent_case", "patch_provider", "servicenow", "context"):
            shutil.copyfile(ROOT / "operations" / (name + ".py"), package / (name + ".py"))
        (package / "__init__.py").write_text("", encoding="utf-8")
        for schema in sorted((ROOT / "operations" / "contracts").glob("*.schema.json")):
            shutil.copyfile(schema, package / "contracts" / schema.name)
        shutil.copyfile(ALT, package / "context.json")
        return Path(directory.name), package

    def test_bundle_style_package_uses_only_its_own_context_and_no_absolute_import(self):
        base, package = self.stage()
        self.assertEqual(len(list((package / "contracts").glob("*.schema.json"))), 6)
        values = run_json(BUNDLE_DUMP, context_file=package / "context.json", pythonpath=(base,))
        self.assertEqual(Path(values["file"]).resolve(), (package / "agent_case.py").resolve())
        self.assertEqual(values["operations_modules"], [])
        self.assertEqual(values["bundle_modules"], sorted(
            ["_asre_case", "_asre_case.agent_case", "_asre_case.context", "_asre_case.patch_provider",
             "_asre_case.servicenow"]))
        self.assertEqual(values["RESOURCE"], ALT_LINUX)
        self.assertEqual(values["RULE"], RULE_PREFIX + "asre-demo-disk")
        self.assertEqual(values["STORAGE"], ALT_STORAGE)
        self.assertEqual(values["RECEIPT_PREFIX"], ALT_STORAGE + "/evidence/tool-results/")
        self.assertEqual(values["SECRET_URL"], ALT_SECRET_URL)
        self.assertEqual(values["UAMI"], ALT_AGENT_CLIENT)
        self.assertEqual(values["FACADE_CLIENT"], ALT_FACADE_CLIENT)
        self.assertEqual(values["WORKSPACE"], ALT_WORKSPACE)
        self.assertEqual(values["SRE_ORIGIN"], ALT_ENDPOINT)
        self.assertEqual(values["LOG_WORKSPACE_ID"], ALT_CUSTOMER_ID)
        self.assertEqual(values["LOG_QUERY_URL"], ALT_LOG_QUERY_URL)
        self.assertEqual(values["PATCH_RESOURCE"], ALT_PATCH)
        self.assertEqual(values["BACKUP_RESOURCE"], ALT_BACKUP)
        self.assertEqual(values["BACKUP_VAULT"], ALT_VAULT)
        self.assertEqual(values["BACKUP_ITEM"], ALT_ITEM)

    def test_bundle_without_endpoint_fails_closed(self):
        base, package = self.stage()
        document = alt_document()
        document["agent"]["endpoint"] = None
        (package / "context.json").write_text(json.dumps(document), encoding="utf-8")
        result = run_with_context("import _asre_case.agent_case", context_file=package / "context.json",
                                  pythonpath=(base,))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ContextError", result.stderr)


MISSING = object()


def native_thread(profile, canonical):
    """A native incident envelope for the profile; canonical is the platform id, or a missing-key marker."""
    source = {"incidentType": "AzMonitor", "incidentId": profile.rule_id}
    if canonical is not MISSING:
        source["canonicalIncidentId"] = canonical
    return {
        "id": "88888888-8888-4888-8888-888888888888", "source": "Incident", "threadOrigin": "Trigger",
        "startMessageIsHuman": None, "type": "Prod", "agentMode": "Review",
        "createdTimestamp": "2026-10-18T22:03:01Z", "incidentSource": source,
        "incidentDetails": {"filterId": "asre-azure-monitor-review", "incidentPriority": profile.severity,
                            "incidentCreatedTime": "2026-10-18T22:03:00Z"},
        "status": {"incidentStatus": {"incidentId": "11111111-1111-4111-8111-111111111111"}},
        "startMessage": {"author": {"role": "SREAgent", "userId": "agent-default"}},
    }


FIXTURE_ID = "azmon:v2:arm-rule:" + "B" * 64
UNRELATED_ID = "azmon:v2:arm-rule:" + "C" * 64
ALERT_ID = "11111111-1111-4111-8111-111111111111"
BOUND_MESSAGE = "The native Azure Monitor trigger envelope does not match its bound schema."
OTHER_PROFILES = (ac.PATCH_PROFILE, ac.NOISE_PROFILE, ac.GROWTH_PROFILE, ac.BACKUP_PROFILE)


class NativeDiskRuleCommissioningTests(unittest.TestCase):
    """The disk rule's canonical incident id is commissioned after deployment; until then it fails closed."""

    def assert_rejected(self, raw, profile=ac.DISK_PROFILE):
        with self.assertRaises(ac.CaseToolError) as caught:
            ac.decode_native_incident(raw, profile)
        self.assertEqual(str(caught.exception), BOUND_MESSAGE)

    def assert_accepted(self, raw, profile):
        self.assertEqual(ac.decode_native_incident(raw, profile), {
            "platform": "AzMonitor",
            "alert_ids": [ac.WORKSPACE + "/providers/Microsoft.AlertsManagement/alerts/" + ALERT_ID],
            "resource_id": profile.resource_id,
        })

    def test_shipped_default_is_not_commissioned(self):
        self.assertIsNone(ac.NATIVE_RULE_ID)

    def test_uncommissioned_disk_profile_rejects_every_canonical_value(self):
        with patch.object(ac, "NATIVE_RULE_ID", None):
            for label, canonical in (("missing key", MISSING), ("None value", None), ("fixture id", FIXTURE_ID),
                                     ("unrelated id", UNRELATED_ID), ("empty string", ""),
                                     ("not a string", 7)):
                with self.subTest(canonical=label):
                    self.assert_rejected(native_thread(ac.DISK_PROFILE, canonical))

    def test_commissioned_disk_profile_accepts_only_the_exact_id(self):
        with patch.object(ac, "NATIVE_RULE_ID", FIXTURE_ID):
            self.assert_accepted(native_thread(ac.DISK_PROFILE, FIXTURE_ID), ac.DISK_PROFILE)
            for label, canonical in (("missing key", MISSING), ("None value", None), ("unrelated id", UNRELATED_ID),
                                     ("lower-case copy", FIXTURE_ID.lower()), ("padded copy", FIXTURE_ID + " "),
                                     ("prefix only", FIXTURE_ID[:-1]), ("empty string", "")):
                with self.subTest(canonical=label):
                    self.assert_rejected(native_thread(ac.DISK_PROFILE, canonical))

    def test_commissioned_disk_profile_still_binds_rule_and_severity(self):
        with patch.object(ac, "NATIVE_RULE_ID", FIXTURE_ID):
            raw = native_thread(ac.DISK_PROFILE, FIXTURE_ID)
            raw["incidentSource"]["incidentId"] = ac.DISK_PROFILE.rule_id + "-other"
            self.assert_rejected(raw)
            raw = native_thread(ac.DISK_PROFILE, FIXTURE_ID)
            raw["incidentDetails"]["incidentPriority"] = "Sev0"
            self.assert_rejected(raw)

    def test_other_profiles_ignore_the_disk_commissioning_state(self):
        canonical = "azmon:v2:arm-rule:" + "A" * 64
        for commissioned in (None, FIXTURE_ID):
            with patch.object(ac, "NATIVE_RULE_ID", commissioned):
                for profile in OTHER_PROFILES:
                    with self.subTest(profile=profile.name, commissioned=commissioned is not None):
                        self.assert_accepted(native_thread(profile, canonical), profile)
                        self.assert_accepted(native_thread(profile, FIXTURE_ID), profile)
                        for label, bad in (("missing key", MISSING), ("None value", None),
                                           ("lower-case hex", "azmon:v2:arm-rule:" + "a" * 64),
                                           ("short digest", "azmon:v2:arm-rule:" + "A" * 63),
                                           ("wrong prefix", "azmon:v1:arm-rule:" + "A" * 64)):
                            with self.subTest(canonical=label):
                                self.assert_rejected(native_thread(profile, bad), profile)

    def test_disk_native_thread_is_rejected_by_the_reader_until_commissioned(self):
        raw = native_thread(ac.DISK_PROFILE, FIXTURE_ID)
        reader = ac.AzureReader(object(), ac.decode_native_incident)
        with patch.object(ac, "_azure_json", return_value=raw):
            with patch.object(ac, "NATIVE_RULE_ID", None), self.assertRaises(ac.CaseToolError):
                reader.investigation(raw["id"])
            with patch.object(ac, "NATIVE_RULE_ID", FIXTURE_ID):
                self.assertEqual(reader.investigation(raw["id"])["resource_id"], ac.RESOURCE)


if __name__ == "__main__":
    unittest.main()
