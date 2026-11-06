"""Static pins for scripts/Build-RuntimeImages.ps1.

The script builds one runtime image in the deployment's own registry and locks the exact digest. These tests read
its source, so a regression of any proof, lock or context rule fails without running Azure. The behavioural fixtures
live in tests/Test-RuntimeImageBuild.ps1.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "Build-RuntimeImages.ps1"


def script_source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


class BuildRuntimeImagesTests(unittest.TestCase):
    def test_protected_image_build_checks_source_inputs_and_locks_the_actual_digest(self):
        source = script_source()
        self.assertIn("$Manifest.source_commit -ne $Commit", source)
        self.assertIn("'Dockerfile', '.dockerignore'", source)
        self.assertIn("Get-FileHash -LiteralPath $InputFile.FullName", source)
        self.assertIn("'--write-enabled', 'false', '--delete-enabled', 'false'", source)
        self.assertIn("image-provenance.json", source)
        self.assertNotIn("terraform apply", source)
        self.assertNotIn("admin-enabled", source)

    def test_build_has_no_pipeline_or_template_source_dependency(self):
        source = script_source()
        for forbidden in (
            "TEMPLATE_SOURCE_COMMIT",
            "template_commit",
            "BUILD_SOURCEVERSION",
            "idToken",
            "ARM_USE_OIDC",
            "servicePrincipalId",
            "##vso",
            "Invoke-Expression",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_registry_and_identity_come_from_the_deployment_context_not_a_literal(self):
        source = script_source()
        self.assertIn(". (Join-Path $PSScriptRoot 'Context.Common.ps1')", source)
        self.assertIn("Get-AsreContext", source)
        self.assertIn("$AsreContext.registry.name", source)
        self.assertIn("$AsreContext.registry.login_server", source)
        self.assertIn("$AsreContext.subscription_id -cne $SubscriptionId", source)
        self.assertIn("$AsreContext.tenant_id -cne $TenantId", source)
        self.assertIn("Assert-AsreSubscription -Context $AsreContext", source)
        # The subscription is proven before any registry operation runs.
        self.assertLess(
            source.index("Assert-AsreSubscription -Context $AsreContext"),
            source.index("Invoke-AzJson -Arguments @('acr', 'repository', 'list'"),
        )
        # Nothing names a registry, a subscription, a tenant or a name suffix.
        self.assertIsNone(re.search(r"(?i)\b(?:acr|stasre|kv-asre)[a-z0-9-]*[0-9a-f]{10}\b", source))
        self.assertIsNone(re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", source))
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", source))
        self.assertIsNone(re.search(r"\.azurecr\.io", source))

    def test_every_az_call_is_bound_to_the_requested_subscription(self):
        source = script_source()
        self.assertIn("& az @Arguments --subscription $SubscriptionId --only-show-errors --output json", source)
        self.assertIn("& az acr build --registry $Registry --subscription $SubscriptionId", source)
        self.assertIn(
            "& az acr task logs --registry $Registry --run-id $RunId --subscription $SubscriptionId --only-show-errors",
            source,
        )
        self.assertEqual(len(re.findall(r"&\s+az\b", source)), 3)

    def test_the_build_result_is_the_acr_run_status_and_the_log_is_shown_after_it(self):
        source = script_source()
        # Queued without streaming or waiting: a crash of the log stream must not decide the result.
        self.assertIn("--no-wait --timeout 1800", source)
        self.assertIn("'acr', 'task', 'show-run'", source)
        self.assertIn("@('Succeeded', 'Failed', 'Canceled', 'Error', 'Timeout')", source)
        self.assertIn("The actual ACR source build failed; no image provenance will be published.", source)
        # The status is polled first, the best-effort log is shown after it and the tag is locked last.
        poll = source.index("'acr', 'task', 'show-run'")
        logs = source.index("& az acr task logs")
        lock = source.index("'--write-enabled', 'false'")
        self.assertLess(poll, logs)
        self.assertLess(logs, lock)

    def test_source_proof_is_complete_clean_and_bound_to_head_subscription_and_tenant(self):
        source = script_source()
        self.assertIn("'source_commit', 'subscription_id', 'tenant_id', 'source_dirty', 'files'", source)
        self.assertIn("$Manifest.subscription_id -cne $SubscriptionId", source)
        self.assertIn("$Manifest.tenant_id -cne $TenantId", source)
        self.assertIn("$Manifest.source_dirty -isnot [bool] -or $Manifest.source_dirty", source)
        self.assertIn("git -C $Repo rev-parse HEAD", source)
        self.assertIn("-notmatch '^[0-9a-f]{40}$'", source)
        self.assertIn("(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Entry.Value", source)
        # Plan files must stay inside the validated plan directory.
        self.assertIn("[IO.Path]::GetFullPath($ValidatedPlanDirectory) + [IO.Path]::DirectorySeparatorChar", source)

    def test_build_inputs_are_recursive_and_limited_to_source_files(self):
        source = script_source()
        self.assertIn("-Recurse -File", source)
        self.assertIn("@('.py', '.html')", source)
        self.assertIn("@('.py', '.json')", source)
        self.assertIn("__pycache__", source)
        self.assertIn("-cnotin @('operations', 'dashboard')", source)
        self.assertIn("$Repository = \"asre-$ImageKind\"", source)

    def test_dashboard_inputs_include_the_context_module(self):
        # Mirror the script's filter over the real tree so a new dashboard module cannot escape the source proof.
        for kind, extensions in (("dashboard", {".py", ".html"}), ("operations", {".py", ".json"})):
            with self.subTest(kind=kind):
                build_context = ROOT / kind
                self.assertTrue(build_context.is_dir(), f"{kind} build context is missing")
                inputs = {
                    path.relative_to(build_context).as_posix()
                    for path in build_context.rglob("*")
                    if path.is_file()
                    and "__pycache__" not in path.parts
                    and (path.suffix in extensions or path.name in {"requirements.txt", "Dockerfile", ".dockerignore"})
                }
                self.assertIn("context.py", inputs)
                self.assertIn("Dockerfile", inputs)
                self.assertIn(".dockerignore", inputs)
                self.assertIn("requirements.txt", inputs)
                self.assertNotIn("README.md", inputs)

    def test_actual_digest_is_locked_and_verified_before_provenance_is_written(self):
        source = script_source()
        self.assertIn("& az acr build --registry $Registry --subscription $SubscriptionId --image \"${Repository}:$Commit\"", source)
        self.assertIn("--build-arg \"SOURCE_COMMIT=$Commit\"", source)
        self.assertIn("An unlocked source-revision image already exists; refuse to overwrite or silently trust it.", source)
        self.assertIn("-notmatch '^sha256:[0-9a-f]{64}$'", source)
        self.assertIn("\"${Repository}@$($image.digest)\"", source)
        self.assertIn("$manifestImage.digest -ne $image.digest", source)
        self.assertGreaterEqual(source.count("'--write-enabled', 'false', '--delete-enabled', 'false'"), 2)
        self.assertGreaterEqual(source.count("changeableAttributes.writeEnabled"), 3)
        self.assertGreaterEqual(source.count("changeableAttributes.deleteEnabled"), 3)
        # Provenance is the last thing the script does, and it is written to a new directory only.
        self.assertIn("New-Item -ItemType Directory -Path $OutputDirectory", source)
        self.assertLess(source.index("$manifestImage.digest -ne $image.digest"), source.rindex("image-provenance.json"))
        self.assertTrue(source.rstrip().endswith("-Encoding utf8"))

    def test_provenance_records_the_registry_and_digest_but_no_identity_or_template_fields(self):
        source = script_source()
        provenance = source[source.index("@{\n    source_commit = $Commit"):source.rindex("image-provenance.json")]
        for field in (
            "source_commit",
            "built_at",
            "registry = $LoginServer",
            "repository = $Repository",
            "digest = $image.digest",
            "image = \"$LoginServer/$Repository@$($image.digest)\"",
            "write_enabled = $false",
            "delete_enabled = $false",
            "tag_write_enabled = $false",
            "tag_delete_enabled = $false",
        ):
            with self.subTest(field=field):
                self.assertIn(field, provenance)
        for forbidden in ("subscription_id", "tenant_id", "template_commit", "operator"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, provenance)


if __name__ == "__main__":
    unittest.main()
