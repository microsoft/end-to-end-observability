"""Local APT configuration ownership tests; no package manager is invoked."""

import importlib.util
import json
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "workloads" / "linux-demo-service"
sys.path.insert(0, str(SOURCE))
try:
    import guest_control
    SPEC = importlib.util.spec_from_file_location("asre_patch_failure_test", SOURCE / "patch_failure.py")
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)
finally:
    sys.path.remove(str(SOURCE))

SCENARIO = "00000017-000a-4000-8000-000000000017"
OTHER = "00000061-000a-4000-8000-000000000061"


class PatchFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(MODULE, "DIRECTORY", self.directory).start()
        self.directory_patcher = patch.object(MODULE, "require_directory")
        self.directory_patcher.start()

    def test_installs_only_one_complete_owned_configuration_and_removes_it(self):
        MODULE.enable(SCENARIO)
        target = MODULE.path_for(SCENARIO)
        self.assertEqual(target.read_bytes(), MODULE.CONTENT)
        self.assertEqual(list(self.directory.iterdir()), [target])
        self.assertTrue(MODULE.disable(SCENARIO))
        self.assertFalse(MODULE.disable(SCENARIO))
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_wrong_episode_cannot_remove_an_existing_fault(self):
        MODULE.enable(SCENARIO)
        self.assertFalse(MODULE.disable(OTHER))
        self.assertEqual(MODULE.path_for(SCENARIO).read_bytes(), MODULE.CONTENT)

    def test_foreign_or_changed_content_is_never_removed(self):
        target = MODULE.path_for(SCENARIO)
        target.write_bytes(b"foreign configuration\n")
        with self.assertRaises(PermissionError):
            MODULE.disable(SCENARIO)
        self.assertTrue(target.exists())

    def test_existing_fault_cannot_be_overwritten_or_overlapped(self):
        MODULE.enable(SCENARIO)
        for scenario in (SCENARIO, OTHER):
            with self.subTest(scenario=scenario), self.assertRaises(ValueError):
                MODULE.enable(scenario)
        self.assertEqual(list(self.directory.iterdir()), [MODULE.path_for(SCENARIO)])

    def test_publication_failure_leaves_no_partial_configuration(self):
        with patch.object(MODULE.os, "link", side_effect=OSError("unit publication failure")):
            with self.assertRaises(OSError):
                MODULE.enable(SCENARIO)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_directory_and_symlink_shaped_configuration_are_rejected(self):
        target = MODULE.path_for(SCENARIO)
        target.mkdir()
        with self.assertRaises(PermissionError):
            MODULE.disable(SCENARIO)
        target.rmdir()
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFLNK, st_uid=0)):
            with self.assertRaises(PermissionError):
                MODULE.disable(SCENARIO)

    def test_invalid_episode_cannot_produce_a_path(self):
        for scenario in ("", "../foreign", SCENARIO.upper()):
            with self.subTest(scenario=scenario), self.assertRaises(ValueError):
                MODULE.path_for(scenario)

    def test_configuration_fails_real_dpkg_invocation_not_repository_assessment(self):
        self.assertEqual(MODULE.CONTENT, b'DPkg::Pre-Invoke { "/bin/false"; };\n')
        self.assertNotIn(b"Update", MODULE.CONTENT)
        self.assertNotIn(b"Proxy", MODULE.CONTENT)

    def test_native_root_directory_permissions(self):
        self.directory_patcher.stop()
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)):
            MODULE.require_directory()
        for mode, owner in ((stat.S_IFLNK | 0o755, 0), (stat.S_IFDIR | 0o777, 0),
                            (stat.S_IFDIR | 0o755, 1000)):
            with self.subTest(mode=mode, owner=owner):
                with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=owner)):
                    with self.assertRaises(PermissionError):
                        MODULE.require_directory()


class PatchGuestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.policy = self.root / "policy.json"
        self.state = self.root / "state.json"
        self.policy.write_text(json.dumps({
            "owner": "asre-demo", "role": "patch", "faults_enabled": False, "max_ttl_seconds": 1800,
        }))
        self.request = {
            "operation": "block_patch_install", "scenario_id": SCENARIO, "ttl_seconds": 900,
            "byte_limit": 0, "interval_seconds": 2,
        }
        self.addCleanup(patch.stopall)
        patch.object(guest_control, "POLICY_PATH", self.policy).start()
        patch.object(guest_control, "STATE_PATH", self.state).start()
        patch.object(guest_control.os, "geteuid", return_value=0, create=True).start()
        patch.dict(sys.modules, {"patch_failure": MODULE}).start()
        self.commands = patch.object(guest_control, "run").start()
        self.config = patch.object(guest_control.Config, "write").start()
        self.enable = patch.object(MODULE, "enable").start()
        self.disable = patch.object(MODULE, "disable", return_value=True).start()
        self.watchdog = patch.object(guest_control, "install_watchdog").start()

    def test_patch_fault_requires_patch_role_root_authority_and_zero_growth(self):
        policy = json.loads(self.policy.read_text())
        with self.assertRaises(PermissionError):
            guest_control.validate_request(self.request, policy, time.time())
        policy["faults_enabled"] = True
        self.assertEqual(guest_control.validate_request(self.request, policy, time.time()), self.request)
        for role in ("linux", "backup"):
            with self.subTest(role=role), self.assertRaises(PermissionError):
                guest_control.validate_request(self.request, {**policy, "role": role}, time.time())
        for fields in ({"byte_limit": 1}, {"interval_seconds": 1}, {"path": "/foreign"}, {"ttl_seconds": 901}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                guest_control.validate_request({**self.request, **fields}, policy, time.time())

    def test_original_watchdog_is_verified_before_apt_failure_is_enabled(self):
        order = []
        self.watchdog.side_effect = lambda state: order.append("watchdog")
        self.enable.side_effect = lambda scenario: order.append("enable")
        result = guest_control.dispatch(self.request, operator_fault=True)
        self.assertEqual(order, ["watchdog", "enable"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["watchdog_at"], result["expires_at"] - 65)
        self.assertIn("watchdog_verified_at", result)
        self.assertFalse(json.loads(self.policy.read_text())["faults_enabled"])
        self.commands.assert_not_called()
        self.config.assert_not_called()

    def test_failed_watchdog_prevents_package_fault(self):
        self.watchdog.side_effect = ValueError("unit unverified timer")
        with self.assertRaises(ValueError):
            guest_control.dispatch(self.request, operator_fault=True)
        self.enable.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())["status"], "activation_failed")

    def test_activation_failure_retains_original_owned_timer_and_failed_state(self):
        self.enable.side_effect = FileExistsError("unit existing configuration")
        with self.assertRaises(FileExistsError):
            guest_control.dispatch(self.request, operator_fault=True)
        self.watchdog.assert_called_once()
        self.assertEqual(json.loads(self.state.read_text())["status"], "activation_failed")

    def test_duplicate_trigger_never_renews_original_deadline(self):
        first = guest_control.dispatch(self.request, operator_fault=True)
        self.assertEqual(guest_control.dispatch(self.request, operator_fault=True), first)
        self.watchdog.assert_called_once()
        self.enable.assert_called_once_with(SCENARIO)
        for request in ({**self.request, "ttl_seconds": 899}, {**self.request, "scenario_id": OTHER}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                guest_control.dispatch(request, operator_fault=True)
        self.assertEqual(json.loads(self.state.read_text())["expires_at"], first["expires_at"])

    def test_reset_removes_only_owned_configuration_without_service_or_package_commands(self):
        guest_control.dispatch(self.request, operator_fault=True)
        result = guest_control.dispatch({"operation": "restore_patch_install", "scenario_id": SCENARIO})
        self.assertEqual(result["status"], "restored")
        self.assertEqual(result["removed_files"], 1)
        self.disable.assert_called_once_with(SCENARIO)
        self.commands.assert_not_called()
        self.config.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())["recovery_owner"], "operator")

    def test_wrong_reset_episode_cannot_remove_the_active_fault(self):
        guest_control.dispatch(self.request, operator_fault=True)
        with self.assertRaises(ValueError):
            guest_control.dispatch({"operation": "restore_patch_install", "scenario_id": OTHER})
        self.disable.assert_not_called()

    def test_watchdog_uses_same_exact_file_cleanup_and_reports_its_actual_owner(self):
        guest_control.dispatch(self.request, operator_fault=True)
        result = guest_control.recovery("watchdog", SCENARIO)
        self.assertEqual(result["recovery_owner"], "watchdog")
        self.disable.assert_called_once_with(SCENARIO)
        self.assertTrue(result["cleanup_before_deadline"])


if __name__ == "__main__":
    unittest.main()
