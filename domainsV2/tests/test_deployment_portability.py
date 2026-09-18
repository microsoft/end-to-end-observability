"""Offline deployment-contract checks; Azure CLI is mocked for every script call."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
TARGET = (
    "-SubscriptionId 643febd3-3170-4110-80f9-fc8980b70a7a "
    "-TenantId d3f0c895-c524-4dde-9e90-422349767d24 "
)
PRODUCER = (
    "-Prefix iot-domains-v2-643feb -StorageName iotv2643febd3sa "
    "-Location centralus -DeploymentName test-producer "
)


class DeploymentTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.producer = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
        cls.wrapper = (ROOT / "infra" / "deploy.bicep").read_text(encoding="utf-8")
        cls.association = (ROOT / "infra" / "perimeter-association.bicep").read_text(encoding="utf-8")

    def test_legacy_defaults_and_exact_plan_identity(self):
        for text in [self.producer, self.wrapper]:
            for name in ["publicStorage", "securityPerimeter", "useExistingPlan", "unrestrictedPortal", "deferApplication"]:
                self.assertIn(f"param {name} bool = false", text)
        self.assertIn("= if (!useExistingPlan && !deferApplication)", self.producer)
        self.assertIn("serverFarmId: useExistingPlan ? resourceId('Microsoft.Web/serverfarms', '${prefix}-plan') : plan.id", self.producer)
        self.assertIn("sku: { name: 'B2', tier: 'Basic', capacity: 1 }", self.producer)
        self.assertIn("linuxFxVersion: 'PYTHON|3.11'", self.producer)
        self.assertIn("--workers=1", self.producer)

    def test_perimeter_disables_all_private_networking(self):
        self.assertIn("var privateNetworking = !publicStorage && !securityPerimeter", self.producer)
        definitions = re.findall(r"resource \w+ 'Microsoft.Network/([^']+)' = if \(([^)]+)\)", self.producer)
        private = [(kind, condition) for kind, condition in definitions if not kind.startswith("networkSecurityPerimeters")]
        self.assertEqual(len(private), 5)
        self.assertTrue(all(condition == "privateNetworking" for _, condition in private))
        self.assertIn("...(privateNetworking ? {", self.producer)
        self.assertNotIn("WEBSITE_DNS", self.producer)

    def test_resolved_endpoint_override_is_complete_and_opt_in(self):
        for text in [self.producer, self.wrapper]:
            self.assertIn("param resolvedRuntimeEndpoints object = {}", text)
        for field in ["storageAccountUrl", "logsEndpoint", "dcrImmutableId", "workspaceId"]:
            self.assertIn(f"!empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.{field} :", self.producer)
        self.assertIn("resolvedRuntimeEndpoints: resolvedRuntimeEndpoints", self.wrapper)

    def test_deferred_application_has_no_unguarded_app_runtime_references(self):
        for name in ["app", "scmPolicy", "ftpPolicy", "storageRole", "dcrRole"]:
            self.assertRegex(self.producer, rf"resource {name} '[^']+' = if \(!deferApplication\)")
        self.assertIn("output appName string = '${prefix}-app'", self.producer)
        self.assertIn("output appUrl string = deferApplication ? '' :", self.producer)
        self.assertIn("output principalId string = deferApplication ? '' :", self.producer)
        for line in self.producer.splitlines():
            if "app.identity." in line or "app.properties." in line:
                self.assertIn("deferApplication ? '' :", line)
        self.assertIn("deferApplication: deferApplication", self.wrapper)

    def test_exact_subscription_rule_and_enforced_association(self):
        self.assertEqual(self.producer.count("direction: 'Inbound'"), 1)
        self.assertIn("subscriptions: [{ id: subscription().id }]", self.producer)
        rule = self.producer.split("resource subscriptionInbound ", 1)[1].split("module storagePerimeter ", 1)[0]
        self.assertNotIn("addressPrefixes", rule)
        self.assertNotIn("Outbound", self.producer + self.association)
        self.assertIn("accessMode: 'Enforced'", self.association)
        self.assertNotIn("'Learning'", self.association)
        self.assertIn("name: 'subscription-identities'", self.association)
        self.assertIn("privateLinkResource: { id: privateLinkResourceId }", self.association)

    def test_storage_fails_closed(self):
        self.assertIn("publicNetworkAccess: securityPerimeter ? 'Disabled' : (publicStorage ? 'Enabled' : 'Disabled')", self.producer)
        self.assertIn("defaultAction: securityPerimeter ? 'Deny'", self.producer)
        self.assertIn("allowSharedKeyAccess: false", self.producer)
        self.assertIn("allowBlobPublicAccess: false", self.producer)
        self.assertIn("bypass: 'None'", self.producer)

    def test_association_precedes_app(self):
        self.assertIn("dependsOn: [zoneGroup, dnsLink, storagePerimeter]", self.producer)

    def test_deployment_contains_only_producer_resources(self):
        self.assertFalse((ROOT / "foundry").exists())
        templates = "\n".join(path.read_text(encoding="utf-8")
                              for path in (ROOT / "infra").glob("*.bicep"))
        for excluded in ("Microsoft.CognitiveServices", "Microsoft.ManagedIdentity",
                         "AzureWebJobs", "kind: 'functionapp'"):
            self.assertNotIn(excluded, templates)

    def test_no_added_runtime_roles(self):
        sources = [self.producer]
        sources.append((ROOT / "infra" / "readiness.bicep").read_text(encoding="utf-8"))
        combined = "\n".join(sources)
        self.assertEqual(len(re.findall(r"resource \w+ 'Microsoft.Authorization/roleAssignments@", combined)), 3)
        self.assertEqual(len(re.findall(r"resource \w+ 'Microsoft.Authorization/roleDefinitions@", combined)), 1)
        self.assertNotIn("Microsoft.Authorization", self.association)


@unittest.skipUnless(PWSH, "PowerShell 7 is required for offline script guard checks")
class DeploymentGuardTests(unittest.TestCase):
    def invoke(self, relative_script, arguments, *, tenant=None, exit_code=0, missing_endpoint=False):
        account = json.dumps({
            "id": "643febd3-3170-4110-80f9-fc8980b70a7a",
            "tenantId": tenant or "d3f0c895-c524-4dde-9e90-422349767d24",
            "state": "Enabled",
        })
        script = str(ROOT / relative_script).replace("'", "''")
        command = f"""
$ErrorActionPreference = 'Stop'
function global:az {{
    $global:LASTEXITCODE = {exit_code}
    if ($args[0] -eq 'account') {{ '{account}'; return }}
    if ($args[0] -eq 'resource') {{
        $id = $args[[Array]::IndexOf($args, '--ids') + 1]
        @{{ id = $id; properties = @{{
            primaryEndpoints = @{{ blob = 'https://iotv2643febd3sa.blob.core.windows.net/' }}
            endpoints = @{{ logsIngestion = '{" " if missing_endpoint else "https://test.centralus-1.ingest.monitor.azure.com"}' }}
            immutableId = 'dcr-11111111111111111111111111111111'
            customerId = '11111111-1111-1111-1111-111111111111'
        }} }} | ConvertTo-Json -Depth 5
        return
    }}
    foreach ($arg in $args) {{
        if ("$arg".StartsWith('@')) {{
            $parameters = Get-Content -Raw -LiteralPath "$arg".Substring(1) | ConvertFrom-Json
            'RESOLVED ' + ($parameters.resolvedRuntimeEndpoints.value | ConvertTo-Json -Compress)
        }}
    }}
    'MOCK_OPERATION ' + ($args -join ' ')
}}
try {{ & '{script}' {TARGET} {arguments} }}
catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}
"""
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30,
        )

    def assert_blocked(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertNotIn("MOCK_OPERATION", result.stdout)

    def test_new_producer_flags_and_existing_defaults(self):
        for flags, expected in [
            ("-ApprovedIpCidr 192.0.2.1/32", ["securityPerimeter=false", "useExistingPlan=false"]),
            ("-SecurityPerimeter $true -UseExistingPlan $true -UnrestrictedPortal $true",
             ["securityPerimeter=true", "useExistingPlan=true", "publicStorage=false"]),
        ]:
            with self.subTest(flags=flags):
                result = self.invoke(Path("scripts") / "Deploy-Infrastructure.ps1", PRODUCER + flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                for value in expected:
                    self.assertIn(value, result.stdout)
                self.assertIn("deployment sub what-if", result.stdout)

    def test_producer_all_actions_keep_optins(self):
        for action, operation in [("WhatIf", "what-if"), ("Validate", "validate"), ("Provision", "create")]:
            result = self.invoke(
                Path("scripts") / "Deploy-Infrastructure.ps1",
                PRODUCER + "-SecurityPerimeter $true -UseExistingPlan $true -UnrestrictedPortal $true "
                f"-Action {action} -ApproveResourceCostsAndRoleAssignments",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"deployment sub {operation}", result.stdout)
            self.assertIn("securityPerimeter=true", result.stdout)
            self.assertIn("useExistingPlan=true", result.stdout)

    def test_perimeter_does_not_implicitly_open_portal(self):
        self.assert_blocked(self.invoke(
            Path("scripts") / "Deploy-Infrastructure.ps1", PRODUCER + "-SecurityPerimeter $true"),
            "ApprovedIpCidr is required")

    def test_deferred_stage_requires_existing_plan_and_passes_flag(self):
        script = Path("scripts") / "Deploy-Infrastructure.ps1"
        self.assert_blocked(self.invoke(script, PRODUCER + "-DeferApplication $true"), "requires UseExistingPlan")
        for deferred in ["true", "false"]:
            result = self.invoke(
                script, PRODUCER + "-UseExistingPlan $true -SecurityPerimeter $true "
                f"-UnrestrictedPortal $true -DeferApplication ${deferred}",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"deferApplication={deferred}", result.stdout)

    def test_resolve_endpoints_is_guarded_and_requires_complete_readback(self):
        script = Path("scripts") / "Deploy-Infrastructure.ps1"
        self.assert_blocked(self.invoke(script, PRODUCER + "-ResolveExistingDataEndpoints"), "requires UseExistingPlan")
        args = PRODUCER + "-UseExistingPlan $true -UnrestrictedPortal $true -ResolveExistingDataEndpoints "
        self.assert_blocked(self.invoke(script, args + "-DeferApplication $true"), "non-deferred")
        self.assert_blocked(self.invoke(script, args, missing_endpoint=True), "missing required endpoint")
        result = self.invoke(script, args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"storageAccountUrl":"https://iotv2643febd3sa.blob.core.windows.net/"', result.stdout)
        self.assertIn('"dcrImmutableId":"dcr-11111111111111111111111111111111"', result.stdout)

    def test_optins_do_not_bypass_approval_or_account_guard(self):
        cases = [
            (Path("scripts") / "Deploy-Infrastructure.ps1",
             PRODUCER + "-SecurityPerimeter $true -UseExistingPlan $true -UnrestrictedPortal $true "),
        ]
        for script, args in cases:
            with self.subTest(script=script):
                self.assert_blocked(self.invoke(script, args + "-Action Provision"), "approval")
                self.assert_blocked(self.invoke(script, args, tenant="wrong-tenant"), "does not match")
                self.assert_blocked(self.invoke(script, args, exit_code=1), "Azure CLI failed")


if __name__ == "__main__":
    unittest.main()
