"""Source-kit invariants, also exercised from an isolated copied installation."""

import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from energy_domains_v2 import inventory, paths, replay
from energy_domains_v2.contracts import INVENTORY_SURFACES, SURFACES


class StandaloneKitTests(unittest.TestCase):
    def test_bundled_files_match_provenance(self):
        root = paths.KIT_ROOT / "support"
        manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["format_version"], 1)
        seen = set()
        for entry in manifest["files"]:
            relative = Path(entry["path"])
            self.assertFalse(relative.is_absolute())
            self.assertNotIn("..", relative.parts)
            self.assertNotIn(entry["path"], seen)
            seen.add(entry["path"])
            data = (root / relative).read_bytes()
            self.assertEqual(len(data), entry["bytes"], entry["path"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"], entry["path"])
        self.assertGreater(len(seen), 100)

    def test_installed_domain_packages_are_from_this_kit(self):
        for name in (
            "inventory_movement", "iot_grid_simulator", "iot_upstream_simulator",
            "iot_midstream_simulator", "iot_downstream_refinery_simulator",
            "iot_compact_portfolio_simulator",
        ):
            with self.subTest(package=name):
                source = Path(import_module(name).__file__).resolve()
                self.assertTrue(source.is_relative_to(paths.KIT_ROOT / "support"),
                                f"Install requirements.txt from this kit; external import: {source}")

    def test_all_reviewed_inventory_inputs_are_bundled(self):
        for surface in INVENTORY_SURFACES:
            domain = surface.removesuffix("-inventory")
            variable = f"ENERGY_V2_{domain.upper().replace('-', '_')}_INVENTORY_ROOT"
            with patch.dict(os.environ, {}, clear=True):
                root = inventory.dataset_root(surface)
                self.assertNotIn(variable, os.environ)
            self.assertTrue(root.is_relative_to(paths.KIT_ROOT / "support"))
            for family in ("part_master", "storeroom_location", "part_applicability", "part_stock_txn"):
                self.assertTrue((root / f"{family}.jsonl").is_file(), f"{surface}: {family}")
            # Match the original authored stock inputs, not a newly synthesized substitute.
            provenance = replay._manifest()["provenance"][surface]
            for entry in provenance:
                if entry["name"].endswith(".jsonl"):
                    self.assertEqual(hashlib.sha256((root / entry["name"]).read_bytes()).hexdigest(),
                                     entry["sha256"], f"{surface}: {entry['name']}")

    def test_renaming_and_unrelated_working_directory_do_not_select_parent_assets(self):
        with tempfile.TemporaryDirectory(prefix="v2-paths-") as temporary:
            parent = Path(temporary)
            kit = parent / "another-repository-name"
            shutil.copytree(paths.KIT_ROOT / "energy_domains_v2", kit / "energy_domains_v2",
                            ignore=shutil.ignore_patterns("__pycache__"))
            asset = Path("dashboard") / "static" / "domains.html"
            (kit / "support" / asset).parent.mkdir(parents=True)
            shutil.copy2(paths.KIT_ROOT / "support" / asset, kit / "support" / asset)
            (parent / asset).parent.mkdir(parents=True)
            (parent / asset).write_text("WRONG PARENT ASSET", encoding="utf-8")
            command = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
os.environ.pop('V2_REPOSITORY_ROOT', None)
from energy_domains_v2 import paths, ui
assert paths.KIT_ROOT == pathlib.Path(sys.argv[1]).resolve()
assert paths.source_root() == paths.KIT_ROOT / 'support'
assert 'WRONG PARENT ASSET' not in ui.selector()
assert 'v2-controls' in ui.selector()
"""
            result = subprocess.run([sys.executable, "-I", "-c", command, str(kit)],
                                    cwd=parent, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_complete_replay_remains_packaged_for_all_surfaces(self):
        for surface in SURFACES:
            model = replay.initial_model(surface)
            sample = replay.sample(surface, model, 0, "2026-09-14T12:00:00Z")
            self.assertEqual(len(sample["observations"]), 10, surface)
            self.assertTrue(replay.scenarios(surface), surface)


if __name__ == "__main__":
    unittest.main()
