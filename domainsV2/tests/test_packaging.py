"""Exercise source export and Azure ZIP creation from a renamed copy; no Azure calls."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from energy_domains_v2.paths import KIT_ROOT


PWSH = shutil.which("pwsh")


@unittest.skipUnless(PWSH, "PowerShell 7 is required for the packaging tools")
class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (KIT_ROOT / "build").mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="v2-package-", dir=KIT_ROOT / "build")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name).resolve()
        cls.kit = cls.root / "renamed-project"
        cls.invoke(KIT_ROOT / "scripts" / "Export-Kit.ps1", "-OutputDirectory", cls.kit)

    @classmethod
    def invoke(cls, script, *arguments, success=True):
        result = subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), *map(str, arguments)],
            cwd=cls.root, capture_output=True, text=True, timeout=120,
        )
        if success and result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result

    def test_export_omits_local_state_credentials_and_environment_artifacts(self):
        for relative in (
            ".local/state/private.json", ".venv/marker.txt",
            "operator.local.json", "local.settings.json",
            "infra/parameters.local.json", "infra/deploy.json",
        ):
            file = self.kit / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("TEST_NOT_A_SECRET", encoding="utf-8")
        output = self.root / "clean-handoff"
        self.invoke(self.kit / "scripts" / "Export-Kit.ps1", "-OutputDirectory", output)
        for relative in (
            ".local", ".venv", "environment.local.json", "operator.local.json", "local.settings.json",
            "infra/parameters.local.json", "infra/deploy.json",
        ):
            self.assertFalse((output / relative).exists(), relative)
        self.assertTrue((output / "environment.example.json").is_file())
        self.assertTrue((output / "infra" / "main.bicep").is_file())
        self.assertFalse((output / "foundry").exists())
        self.assertFalse(self.invoke(self.kit / "scripts" / "Export-Kit.ps1",
                                     "-OutputDirectory", output, success=False).returncode == 0)

    def test_flat_zip_has_dependencies_and_serves_every_portal(self):
        output = self.root / "producer"
        self.invoke(self.kit / "scripts" / "package.ps1", "-OutputDirectory", output)
        with zipfile.ZipFile(output / "domains-v2.zip") as archive:
            names = archive.namelist()
            self.assertIn("app.py", names)
            self.assertIn("energy_domains_v2/replay_data/manifest.json", names)
            self.assertIn("support/inventory_movement.py", names)
            self.assertNotIn("support/dashboard/app.py", names)
            self.assertFalse(any(name.lower().startswith("foundry/") for name in names))
            self.assertFalse(any("\\" in name or name.startswith("domainsV2/") for name in names))
            self.assertEqual(archive.read("requirements.txt").decode().splitlines()[:2],
                             ["-e ./support", "-e ."])
            stage = self.root / "unpacked-azure-zip"
            archive.extractall(stage)
        command = """
import importlib, os, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root), str(root / 'support')] + [
    str(root / 'support' / 'domains' / domain / 'src')
    for domain in ('power-grid', 'upstream', 'midstream', 'downstream')]
for key in list(os.environ):
    if key.startswith(('V2_', 'ENERGY_V2_', 'WEBSITE_')):
        del os.environ[key]
os.environ.update(V2_MODE='local', V2_WORKER_ENABLED='0', V2_LOCAL_STATE=str(root / '.local'))
for name in ('energy_domains_v2', 'inventory_movement', 'iot_grid_simulator',
             'iot_upstream_simulator', 'iot_midstream_simulator',
             'iot_downstream_refinery_simulator', 'iot_compact_portfolio_simulator'):
    assert pathlib.Path(importlib.import_module(name).__file__).resolve().is_relative_to(root), name
from app import app
client = app.test_client()
routes = ['/', '/api/health/live', '/api/health/ready',
          '/power-grid', '/upstream', '/midstream', '/downstream', '/downstream/portfolio']
routes += ['/' + domain + '/review/inventory'
           for domain in ('power-grid', 'upstream', 'midstream', 'downstream')]
for route in routes:
    response = client.get(route)
    assert response.status_code == 200, (route, response.status_code, response.data[:500])
assert not app.extensions['v2_runtime'].worker_alive
"""
        result = subprocess.run([sys.executable, "-I", "-c", command, str(stage)],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.invoke(self.kit / "scripts" / "package.ps1",
                                       "-OutputDirectory", output, success=False).returncode, 0)

    @unittest.skipUnless(shutil.which("git"), "Git is required to prove checkout portability")
    def test_git_checkout_keeps_hashed_bytes_and_reviewed_data(self):
        source = self.root / "checkout-source"
        self.invoke(self.kit / "scripts" / "Export-Kit.ps1", "-OutputDirectory", source)
        commands = (
            ["init", "--quiet"],
            ["config", "core.autocrlf", "true"],
            ["add", "."],
        )
        for arguments in commands:
            result = subprocess.run(["git", "-C", str(source), *arguments],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
        checkout = self.root / "from-git-index"
        checkout.mkdir()
        # Export the index without committing or touching the user's repository.
        result = subprocess.run(
            ["git", "-C", str(source), "checkout-index", "--all",
             "--prefix", str(checkout) + os.sep],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.invoke(checkout / "scripts" / "Verify-Support.ps1")
        for relative in (
            "energy_domains_v2/replay_data/manifest.json",
            "support/domains/upstream/generated/review/normal_production/part_stock_txn.jsonl",
            "support/domains/downstream/generated/review/part_stock_txn.jsonl",
        ):
            self.assertEqual((checkout / relative).read_bytes(), (source / relative).read_bytes())
        manifest = json.loads((checkout / "support" / "bundle-manifest.json").read_text())
        self.assertEqual(manifest, json.loads((source / "support" / "bundle-manifest.json").read_text()))

    def test_changed_bundle_fails_before_packaging(self):
        copied = self.root / "changed-bundle"
        shutil.copytree(self.kit, copied)
        target = copied / "support" / "inventory_movement.py"
        target.write_bytes(target.read_bytes() + b"\n# changed input\n")
        output = self.root / "rejected"
        result = self.invoke(copied / "scripts" / "package.ps1",
                             "-OutputDirectory", output, success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("integrity mismatch", result.stderr)
        self.assertFalse((output / "domains-v2.zip").exists())

    def test_unlisted_support_code_is_not_silently_dropped_from_the_zip(self):
        copied = self.root / "unlisted-bundle"
        shutil.copytree(self.kit, copied)
        (copied / "support" / "unlisted_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        output = self.root / "unlisted-rejected"
        result = self.invoke(copied / "scripts" / "package.ps1",
                             "-OutputDirectory", output, success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unlisted support file", result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
