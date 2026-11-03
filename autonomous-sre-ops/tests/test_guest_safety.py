import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "workloads" / "linux-demo-service"))
import demo_common
import guest_control
from demo_common import CHUNK_BYTES, MAX_MEMORY_BYTES, Config, atomic_json, clean_scenario


class ConfigurationTests(unittest.TestCase):
    def test_normal_roundtrip(self):
        self.assertEqual(Config.parse(asdict(Config())), Config())

    def test_invalid_configuration_is_not_silently_defaulted(self):
        invalid = [
            {}, {**asdict(Config()), "command": "anything"},
            {**asdict(Config()), "mode": "unknown"},
            {**asdict(Config()), "interval_seconds": float("nan")},
            {**asdict(Config()), "byte_limit": True},
            {**asdict(Config()), "expires_at": float("inf")},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                Config.parse(value)

    def test_active_budget_and_proactive_memory_cap(self):
        for mode, limit in (("memory", 65 * 1024 * 1024), ("memory-oom", MAX_MEMORY_BYTES + 1), ("disk", 4 * 1024**3)):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                Config.parse(asdict(Config(mode, str(uuid4()), time.time() + 60, limit, 2)))

    def test_atomic_write_preserves_read_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            atomic_json(path, {"value": 1})
            original_mode = stat.S_IMODE(path.stat().st_mode)
            atomic_json(path, {"value": 2})
            self.assertEqual(json.loads(path.read_text())["value"], 2)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)
            self.assertEqual(list(Path(directory).glob(".config.json.*")), [])


class GuestPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"owner": "asre-demo", "role": "linux", "faults_enabled": False, "max_ttl_seconds": 1800}
        self.request = {
            "operation": "start_disk", "scenario_id": str(uuid4()),
            "ttl_seconds": 60, "byte_limit": CHUNK_BYTES, "interval_seconds": 2,
        }

    def test_faults_are_disabled_by_default(self):
        with self.assertRaises(PermissionError):
            guest_control.validate_request(self.request, self.policy, time.time())

    def test_operator_authority_is_root_only_and_unavailable_to_inspection(self):
        with patch.object(guest_control.os, "geteuid", return_value=1000, create=True):
            with self.assertRaises(PermissionError):
                guest_control.dispatch(self.request, operator_fault=True)
        with patch.object(guest_control.os, "geteuid", return_value=0, create=True):
            with self.assertRaises(PermissionError):
                guest_control.dispatch({"operation": "inspect"}, inspect_only=True, operator_fault=True)
        with self.assertRaises(ValueError):
            guest_control.validate_request({**self.request, "operator_fault": True}, self.policy, time.time())

    def test_operator_authority_never_enables_persisted_runner_policy(self):
        original = {**self.request, "status": "active", "expires_at": time.time() + 60}
        with tempfile.TemporaryDirectory() as directory:
            policy, state = Path(directory) / "policy.json", Path(directory) / "state.json"
            policy.write_text(json.dumps(self.policy))
            before = policy.read_bytes()
            state.write_text(json.dumps(original))
            with patch.object(guest_control.os, "geteuid", return_value=0, create=True), \
                    patch.object(guest_control, "POLICY_PATH", policy), patch.object(guest_control, "STATE_PATH", state), \
                    patch.object(guest_control, "run") as run:
                self.assertEqual(guest_control.dispatch(self.request, operator_fault=True), original)
                self.assertEqual(policy.read_bytes(), before)
                with self.assertRaises(PermissionError):
                    guest_control.dispatch(self.request)
                with self.assertRaises(ValueError):
                    guest_control.dispatch({**self.request, "ttl_seconds": 901}, operator_fault=True)
                run.assert_not_called()

    def test_operator_disk_headroom_blocks_before_arming_or_scheduling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            policy, state = root / "policy.json", root / "state.json"
            policy.write_text(json.dumps(self.policy))
            device = SimpleNamespace(stat=lambda: SimpleNamespace(st_rdev=root.stat().st_dev))
            with patch.object(guest_control.os, "geteuid", return_value=0, create=True), \
                    patch.object(guest_control, "POLICY_PATH", policy), patch.object(guest_control, "STATE_PATH", state), \
                    patch.object(guest_control, "DATA_ROOT", root), patch.object(guest_control, "Path", return_value=device), \
                    patch.object(guest_control, "validate_data_root"), \
                    patch.object(guest_control.os, "statvfs", return_value=SimpleNamespace(
                        f_blocks=25600, f_bavail=2300, f_frsize=4096), create=True), \
                    patch.object(guest_control, "install_watchdog") as watchdog, \
                    patch.object(guest_control.Config, "write") as config:
                with self.assertRaisesRegex(ValueError, "headroom"):
                    guest_control.dispatch(self.request, operator_fault=True)
                watchdog.assert_not_called()
                config.assert_not_called()
                self.assertFalse(state.exists())
                self.assertFalse(json.loads(policy.read_text())["faults_enabled"])

    def test_operator_cli_uses_locked_dispatch_not_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock"
            fcntl = SimpleNamespace(flock=lambda *args: None, LOCK_EX=2)
            with patch.object(guest_control.os, "geteuid", return_value=0, create=True), \
                    patch.object(guest_control.sys, "argv", ["guest_control.py", "--operator-fault"]), \
                    patch.object(guest_control.sys, "stdin", SimpleNamespace(
                        buffer=io.BytesIO(json.dumps(self.request).encode()))), \
                    patch.object(guest_control.sys, "stdout", io.StringIO()), \
                    patch.object(guest_control, "Path", return_value=lock), \
                    patch.dict(sys.modules, {"fcntl": fcntl}), \
                    patch.object(guest_control, "dispatch", return_value={"status": "unit-fixture"}) as dispatch, \
                    patch.object(guest_control, "recovery") as recovery:
                guest_control.main()
                dispatch.assert_called_once_with(self.request, operator_fault=True)
                recovery.assert_not_called()

    def test_shell_and_path_fields_are_rejected(self):
        for field in ("command", "path", "service"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                guest_control.validate_request({"operation": "inspect", field: "untrusted"}, self.policy, time.time())

    def test_expiry_and_wrong_target_are_rejected(self):
        policy = {**self.policy, "faults_enabled": True}
        with self.assertRaises(ValueError):
            guest_control.validate_request({**self.request, "ttl_seconds": 1801}, policy, time.time())
        with self.assertRaises(PermissionError):
            guest_control.validate_request(self.request, {**policy, "role": "backup"}, time.time())

    def test_backup_recovery_is_independent_and_target_bound(self):
        request = {"operation": "restore_guest_agent", "scenario_id": str(uuid4())}
        with self.assertRaises(PermissionError):
            guest_control.validate_request(request, self.policy, time.time())
        guest_control.validate_request(request, {**self.policy, "role": "backup"}, time.time())

    def test_repair_cannot_return_another_scenarios_old_success(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            state = Path(directory) / "state.json"
            policy.write_text(json.dumps(self.policy))
            state.write_text(json.dumps({"scenario_id": str(uuid4()), "operation": "start_disk", "status": "restored"}))
            with patch.object(guest_control, "POLICY_PATH", policy), patch.object(guest_control, "STATE_PATH", state):
                with self.assertRaises(ValueError):
                    guest_control.dispatch({"operation": "recover_workload", "scenario_id": str(uuid4())})

    def test_replayed_fault_does_not_extend_watchdog(self):
        request = self.request
        original = {**request, "status": "active", "expires_at": time.time() + 60}
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            state = Path(directory) / "state.json"
            policy.write_text(json.dumps({**self.policy, "faults_enabled": True}))
            state.write_text(json.dumps(original))
            with patch.object(guest_control, "POLICY_PATH", policy), patch.object(guest_control, "STATE_PATH", state), patch.object(guest_control, "run") as run:
                self.assertEqual(guest_control.dispatch(request), original)
                run.assert_not_called()
                with self.assertRaises(ValueError):
                    guest_control.dispatch({**request, "ttl_seconds": 120})

    def test_timer_is_installed_before_fault_change(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            state = Path(directory) / "state.json"
            policy.write_text(json.dumps({**self.policy, "role": "backup", "faults_enabled": True}))
            # A successful activation must leave room for cleanup and activation
            # budgets; short lifetimes are covered by fail-closed deadline tests.
            request = {**self.request, "operation": "stop_guest_agent", "byte_limit": 0, "ttl_seconds": 120}
            calls = []
            def execute(args, **kwargs):
                calls.append(args)
                if args[0] == "busctl":
                    if args[7] == "StartTransientUnit":
                        return json.dumps({"type": "o", "data": ["/org/freedesktop/systemd1/job/1"]})
                    if args[-1] == "org.freedesktop.systemd1.Unit":
                        return json.dumps({"type": "a{sv}", "data": [{
                            "LoadState": {"type": "s", "data": "loaded"},
                            "ActiveState": {"type": "s", "data": "active"},
                            "Job": {"type": "(uo)", "data": [0, "/"]},
                        }]})
                    if args[-1] == "org.freedesktop.systemd1.Service":
                        return json.dumps({"type": "a{sv}", "data": [{
                            "RemainAfterExit": {"type": "b", "data": True},
                            "Type": {"type": "s", "data": "simple"}, "User": {"type": "s", "data": "root"},
                            "ExecStart": {"type": "a(sasbttttuii)", "data": [[
                                "/usr/bin/python3", ["/usr/bin/python3", "/usr/local/lib/asre-demo/guest_control.py",
                                "--expire-id", request["scenario_id"]], False, 0, 0, 0, 0, 0, 0, 0]]},
                            "ExecMainStartTimestamp": {"type": "t", "data": 0},
                            "ExecMainExitTimestamp": {"type": "t", "data": 0},
                        }]})
                    armed = json.loads(state.read_text())
                    return json.dumps({"type": "a{sv}", "data": [{
                        "Unit": {"type": "s", "data": f"asre-demo-expiry-{request['scenario_id']}.service"},
                        "NextElapseUSecRealtime": {"type": "t", "data": int(armed["watchdog_at"] * 1_000_000)},
                        "AccuracyUSec": {"type": "t", "data": 1_000_000},
                        "RandomizedDelayUSec": {"type": "t", "data": 0},
                        "RemainAfterElapse": {"type": "b", "data": True},
                        "LastTriggerUSec": {"type": "t", "data": 0},
                    }]})
                return ""
            with patch.object(guest_control, "POLICY_PATH", policy), patch.object(guest_control, "STATE_PATH", state), patch.object(guest_control, "run", side_effect=execute):
                guest_control.dispatch(request)
            self.assertEqual(calls[0][:4], ["busctl", "--json=short", "--", "call"])
            self.assertEqual(calls[0][7], "StartTransientUnit")
            self.assertEqual([args[0] for args in calls], ["busctl"] * 4 + ["systemctl"])
            self.assertEqual([args[-1] for args in calls if args[0] == "busctl" and args[6] == "GetAll"], [
                "org.freedesktop.systemd1.Unit", "org.freedesktop.systemd1.Timer", "org.freedesktop.systemd1.Service",
            ])
            self.assertEqual(calls[0][calls[0].index("RemainAfterExit") + 1:][:2], ["b", "true"])
            self.assertEqual(calls[0][calls[0].index("RemainAfterElapse") + 1:][:2], ["b", "true"])
            self.assertEqual(calls[-1], ["systemctl", "stop", "walinuxagent.service"])

    def test_timer_failure_prevents_backup_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = Path(directory) / "policy.json"
            state = Path(directory) / "state.json"
            policy.write_text(json.dumps({**self.policy, "role": "backup", "faults_enabled": True}))
            request = {**self.request, "operation": "stop_guest_agent", "byte_limit": 0, "ttl_seconds": 120}
            with patch.object(guest_control, "POLICY_PATH", policy), patch.object(guest_control, "STATE_PATH", state), patch.object(guest_control, "run", side_effect=OSError("timer unavailable")) as run:
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(OSError):
                    guest_control.dispatch(request)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(json.loads(state.read_text())["status"], "activation_failed")


@unittest.skipUnless(sys.platform == "linux", "Linux O_NOFOLLOW/dir_fd enforcement is exercised in Ubuntu")
class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.scenario_id = str(uuid4())
        self.scenario = self.root / "data" / self.scenario_id
        self.scenario.mkdir(parents=True)
        self.file = self.scenario / "00000000.bin"
        self.file.write_bytes(b"owned demo data")
        (self.scenario / "manifest.json").write_text(json.dumps({
            "owner": "asre-demo", "scenario_id": self.scenario_id, "files": [self.file.name],
        }))

    def test_os_filesystem_is_rejected(self):
        with self.assertRaises(ValueError):
            demo_common.validate_data_root(self.root)

    def test_cleanup_deletes_only_exact_manifest(self):
        other = self.root / "data" / "unrelated"
        other.write_text("preserve")
        with patch.object(demo_common, "validate_data_root"):
            self.assertEqual(clean_scenario(self.scenario_id, self.root), 1)
        self.assertFalse(self.scenario.exists())
        self.assertEqual(other.read_text(), "preserve")

    def test_unknown_file_causes_no_partial_cleanup(self):
        (self.scenario / "unexpected.txt").write_text("do not delete")
        with patch.object(demo_common, "validate_data_root"), self.assertRaises(ValueError):
            clean_scenario(self.scenario_id, self.root)
        self.assertTrue(self.file.exists())

    def test_symlink_and_hardlink_are_rejected(self):
        original = self.root / "outside.bin"
        original.write_text("preserve")
        self.file.unlink()
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.file.symlink_to(original)
                else:
                    os.link(original, self.file)
                with patch.object(demo_common, "validate_data_root"), self.assertRaises(ValueError):
                    clean_scenario(self.scenario_id, self.root)
                self.assertEqual(original.read_text(), "preserve")
                self.file.unlink()

    def test_traversal_manifest_is_rejected(self):
        manifest = self.scenario / "manifest.json"
        manifest.write_text(json.dumps({"owner": "asre-demo", "scenario_id": self.scenario_id, "files": ["../outside.bin"]}))
        with patch.object(demo_common, "validate_data_root"), self.assertRaises(ValueError):
            clean_scenario(self.scenario_id, self.root)


if __name__ == "__main__":
    unittest.main()
