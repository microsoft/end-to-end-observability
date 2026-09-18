"""Targeted orchestration checks for the portable deployment runner."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_zip(path: Path, *, name: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}.txt", name)
    return _sha256(path)


@unittest.skipUnless(PWSH, "PowerShell 7 is required for deployment orchestration tests")
class DeployKitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "build").mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="test-deploy-kit-", dir=ROOT / "build")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.build = Path(cls.temporary.name)
        producer_zip = cls.build / "producer-candidate-1" / "domains-v2.zip"
        cls.producer_sha = _write_zip(producer_zip, name="producer")
        cls.config_path = cls.build / "test-config.json"
        cls.config_path.write_text(json.dumps({
            "azure": {
                "subscriptionId": "643febd3-3170-4110-80f9-fc8980b70a7a",
                "tenantId": "d3f0c895-c524-4dde-9e90-422349767d24",
                "location": "centralus",
            },
            "producer": {
                "prefix": "iot-domains-v2-643feb",
                "storageName": "iotv2643febd3sa",
                "approvedIpCidr": "192.0.2.10/32",
                "unrestrictedPortal": False,
                "securityPerimeter": True,
                "bootstrapDeploymentName": "v2-bootstrap-rg",
                "planDeploymentName": "v2-producer-plan",
                "dataDeploymentName": "v2-producer-data",
                "appDeploymentName": "v2-producer-app",
                "readinessDeploymentName": "v2-readiness",
                "packageOutputDirectory": str(producer_zip.parent.relative_to(ROOT)),
                "approvedPackageSha256": cls.producer_sha,
            },
        }, indent=2), encoding="utf-8")

        cls.base_config = cls.config_path.read_text(encoding="utf-8")

    def invoke(self, *, stage: str, action: str, unregistered=(), missing_endpoint=False,
               unrestricted=False,
               approve_resource=False, approve_deployment=False, approve_public=False,
               perimeter=True, location="centralus", approved_sha=None,
               validation_failed=False, local_package=False):
        config = json.loads(self.base_config)
        config["producer"]["unrestrictedPortal"] = unrestricted
        config["producer"]["securityPerimeter"] = perimeter
        config["azure"]["location"] = location
        if approved_sha is not None:
            config["producer"]["approvedPackageSha256"] = approved_sha
        if local_package:
            config["azure"]["subscriptionId"] = "00000000-0000-0000-0000-000000000000"
            config["azure"]["tenantId"] = "00000000-0000-0000-0000-000000000000"
            config["producer"]["packageOutputDirectory"] = str((self.build / "local-package").relative_to(ROOT))
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        provider_list = ", ".join(f"'{namespace}'" for namespace in unregistered)
        switches = []
        if approve_resource:
            switches.append("-ApproveResourceCostsAndRoleAssignments")
        if approve_deployment:
            switches.append("-ApproveDeployment")
        if approve_public:
            switches.append("-ApprovePublicAnonymousAccess")
        switches_text = " ".join(switches)
        script = str(ROOT / "scripts" / "Deploy-Kit.ps1").replace("'", "''")
        config_path = str(self.config_path).replace("'", "''")
        missing_logs = " " if missing_endpoint else "https://test.centralus-1.ingest.monitor.azure.com"
        command = f"""
$ErrorActionPreference = 'Stop'
$unregistered = @({provider_list})
function global:az {{
    $global:LASTEXITCODE = 0
    if ('{action}' -eq 'Package') {{ throw 'Packaging must not call Azure' }}
    if (${str(validation_failed).lower()} -and $args[0] -eq 'deployment' -and $args[2] -eq 'validate') {{
        $global:LASTEXITCODE = 1
        'MOCK_VALIDATION_FAILED'
        return
    }}
    if ($args[0] -eq 'account') {{
        @{{ id = '643febd3-3170-4110-80f9-fc8980b70a7a'; tenantId = 'd3f0c895-c524-4dde-9e90-422349767d24'; state = 'Enabled' }} |
            ConvertTo-Json -Compress
        return
    }}
    if ($args[0] -eq 'provider') {{
        $namespace = $args[[Array]::IndexOf($args, '--namespace') + 1]
        if ($unregistered -contains $namespace) {{ 'NotRegistered' }} else {{ 'Registered' }}
        return
    }}
    if ($args[0] -eq 'resource') {{
        $id = $args[[Array]::IndexOf($args, '--ids') + 1]
        @{{ id = $id; properties = @{{
            primaryEndpoints = @{{ blob = 'https://iotv2643febd3sa.blob.core.windows.net/' }}
            endpoints = @{{ logsIngestion = '{missing_logs}' }}
            immutableId = 'dcr-11111111111111111111111111111111'
            customerId = '11111111-1111-1111-1111-111111111111'
        }} }} | ConvertTo-Json -Depth 5 -Compress
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
try {{ & '{script}' -ConfigPath '{config_path}' -Stage {stage} -Action {action} {switches_text} }}
catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}
"""
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )

    def assert_blocked(self, result, text):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(text, result.stderr)

    def test_environment_example_has_portable_defaults(self):
        data = json.loads((ROOT / "environment.example.json").read_text(encoding="utf-8"))
        self.assertEqual(data["azure"]["location"], "centralus")
        self.assertTrue(data["producer"]["securityPerimeter"])
        self.assertFalse(data["producer"]["unrestrictedPortal"])
        self.assertEqual(set(data), {"azure", "producer"})
        self.assertEqual(data["producer"]["approvedPackageSha256"], "")

    def test_removed_addon_stages_are_rejected_before_azure_calls(self):
        for stage in ("FoundryInfrastructure", "FoundryPackage", "FoundryPublish"):
            with self.subTest(stage=stage):
                result = self.invoke(stage=stage, action="WhatIf")
                self.assert_blocked(result, "ValidateSet")
                self.assertNotIn("MOCK_OPERATION", result.stdout)

    def test_readiness_uses_only_producer_names(self):
        result = self.invoke(stage="Readiness", action="WhatIf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("readiness.bicep", result.stdout)
        self.assertIn("workspaceName=iot-domains-v2-643feb-law", result.stdout)
        self.assertIn("appName=iot-domains-v2-643feb-app", result.stdout)

    def test_bootstrap_and_plan_are_separate_group_creation_then_b2_plan_steps(self):
        bootstrap = self.invoke(stage="BootstrapResourceGroup", action="WhatIf")
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        self.assertIn("deployment sub what-if", bootstrap.stdout)
        self.assertIn("bootstrap-resource-group.bicep", bootstrap.stdout)
        plan = self.invoke(stage="Plan", action="Validate")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("deployment group validate", plan.stdout)
        self.assertIn("infra\\plan.bicep", plan.stdout)

    def test_producer_app_uses_existing_plan_and_resolved_existing_endpoints(self):
        result = self.invoke(stage="ProducerApp", action="WhatIf")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("useExistingPlan=true", result.stdout)
        self.assertIn("deferApplication=false", result.stdout)
        self.assertIn('"storageAccountUrl":"https://iotv2643febd3sa.blob.core.windows.net/"', result.stdout)
        self.assertIn("deployment sub what-if", result.stdout)

    def test_runner_blocks_missing_provider_and_public_portal_without_explicit_approval(self):
        blocked = self.invoke(stage="ProducerData", action="WhatIf", unregistered=("Microsoft.Network",))
        self.assert_blocked(blocked, "missing namespaces: Microsoft.Network")
        public = self.invoke(stage="ProducerApp", action="Provision", unrestricted=True,
                             approve_resource=True)
        self.assert_blocked(public, "ApprovePublicAnonymousAccess")

    def test_publish_requires_approval_and_matching_producer_hash(self):
        producer = self.invoke(stage="ProducerPublish", action="Publish", approve_deployment=True)
        self.assertEqual(producer.returncode, 0, producer.stderr)
        self.assertIn("webapp deploy", producer.stdout)
        self.assertIn("producer-candidate-1\\domains-v2.zip", producer.stdout)

        self.assert_blocked(self.invoke(stage="ProducerPublish", action="Publish"),
                            "ApproveDeployment")
        mismatch = self.invoke(stage="ProducerPublish", action="Publish",
                               approve_deployment=True, approved_sha="0" * 64)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertNotIn("webapp deploy", mismatch.stdout)

    def test_private_mode_and_string_booleans_are_rejected(self):
        self.assert_blocked(self.invoke(stage="ProducerData", action="WhatIf", perimeter=False),
                            "securityPerimeter must be true")
        self.assert_blocked(self.invoke(stage="ProducerApp", action="WhatIf", unrestricted="false"),
                            "must be a JSON boolean")

    def test_bootstrap_region_is_bound_to_this_environment(self):
        self.assert_blocked(self.invoke(stage="BootstrapResourceGroup", action="WhatIf",
                                       location="westeurope"), "Only centralus")

    def test_provision_validates_first_and_does_not_create_on_validation_failure(self):
        result = self.invoke(stage="BootstrapResourceGroup", action="Provision", approve_resource=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index("deployment sub validate"),
                        result.stdout.index("deployment sub create"))
        failed = self.invoke(stage="BootstrapResourceGroup", action="Provision",
                             approve_resource=True, validation_failed=True)
        self.assert_blocked(failed, "Azure CLI failed")
        self.assertNotIn("deployment sub create", failed.stdout)

    def test_local_packaging_needs_no_azure_identity_or_calls(self):
        result = self.invoke(stage="ProducerPackage", action="Package", local_package=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.build / "local-package" / "domains-v2.zip").is_file())


if __name__ == "__main__":
    unittest.main()
