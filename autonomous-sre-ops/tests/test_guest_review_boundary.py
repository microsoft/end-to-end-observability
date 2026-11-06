"""Offline behavioral checks for the runner/operator guest authority boundary."""

import contextlib
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

GUEST_DIR = Path(__file__).resolve().parents[1] / "workloads" / "linux-demo-service"
sys.path.insert(0, str(GUEST_DIR))
import demo_common
import guest_control


class ReviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.policy = self.root / "policy.json"
        self.state = self.root / "state.json"
        self.config = self.root / "config.json"
        self.lock = self.root / "control.lock"
        self.scenario_id = str(uuid4())
        self.policy.write_text(json.dumps({
            "owner": "asre-demo", "role": "linux",
            "faults_enabled": False, "max_ttl_seconds": 1800,
        }))
        self.config.write_text('{"unchanged": true}')
        self.write_state()
        self.addCleanup(patch.stopall)
        patch.object(guest_control, "POLICY_PATH", self.policy).start()
        patch.object(guest_control, "STATE_PATH", self.state).start()

    def write_state(self, **changes):
        value = {
            "operation": "start_disk", "scenario_id": self.scenario_id,
            "status": "active", "expires_at": 1234567890,
            "ttl_seconds": 60, "byte_limit": demo_common.CHUNK_BYTES,
            "interval_seconds": 2,
        }
        value.update(changes)
        self.state.write_text(json.dumps(value))
        return value

    def invoke_main(self, arguments, request=None, *, root=True, original=""):
        output = io.StringIO()
        stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
        fcntl = types.SimpleNamespace(flock=Mock(), LOCK_EX=2)
        with patch.object(guest_control.sys, "argv", ["guest_control.py", *arguments]), \
                patch.object(guest_control.sys, "stdin", stdin), \
                patch.object(guest_control.os, "geteuid", return_value=0 if root else 1000, create=True), \
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": original}), \
                patch.dict(sys.modules, {"fcntl": fcntl}), \
                patch.object(guest_control, "Path", return_value=self.lock), \
                contextlib.redirect_stdout(output):
            guest_control.main()
        return json.loads(output.getvalue())

    def test_runner_denies_all_mutating_operations_before_any_side_effect(self):
        before = {path: path.read_bytes() for path in (self.policy, self.state, self.config)}
        requests = [
            {"operation": operation, "scenario_id": self.scenario_id,
             "ttl_seconds": 60, "byte_limit": demo_common.CHUNK_BYTES, "interval_seconds": 2}
            for operation in guest_control.OPERATIONS - {"inspect"}
        ] + [
            {"operation": "--expire-id", "scenario_id": self.scenario_id},
            {"operation": "expire", "scenario_id": self.scenario_id},
            {"operation": "recover_workload", "scenario_id": self.scenario_id, "inspect_only": False},
            None, [],
        ]
        # Fail even with a faults-enabled policy: the runner boundary is not a
        # consequence of the default policy or request-field validation.
        self.policy.write_text(self.policy.read_text().replace("false", "true"))
        before[self.policy] = self.policy.read_bytes()
        with patch.object(guest_control, "atomic_json") as atomic, \
                patch.object(guest_control.Config, "write") as config_write, \
                patch.object(guest_control, "clean_scenario") as cleanup, \
                patch.object(guest_control, "read_state") as read_state, \
                patch.object(guest_control.subprocess, "run") as process, \
                patch.object(guest_control, "urlopen") as health:
            for request in requests:
                with self.subTest(request=request), self.assertRaises(PermissionError):
                    self.invoke_main(["--inspect-only"], request,
                                     original="sudo /usr/bin/python3 guest_control.py --expire-id " + self.scenario_id)
            for mocked in (atomic, config_write, cleanup, read_state, process, health):
                mocked.assert_not_called()
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(self.lock.exists())

    def test_exact_inspection_runs_only_read_operations(self):
        before = self.state.read_bytes(), self.config.read_bytes()
        response = io.BytesIO(b'{"status": "ok"}')
        with patch.object(guest_control.subprocess, "run", return_value=types.SimpleNamespace(stdout="active")) as process, \
                patch.object(guest_control, "urlopen", return_value=response), \
                patch.object(guest_control.Config, "write") as config_write, \
                patch.object(guest_control, "atomic_json") as atomic:
            result = self.invoke_main(["--inspect-only"], {"operation": "inspect"},
                                      original="recover_workload")
        self.assertEqual(result["health"], {"status": "ok"})
        self.assertEqual([call.args[0][0] for call in process.call_args_list], ["systemctl", "journalctl"])
        config_write.assert_not_called()
        atomic.assert_not_called()
        self.assertEqual(before, (self.state.read_bytes(), self.config.read_bytes()))
        self.assertFalse(self.lock.exists())

    def test_caller_fields_cannot_change_inspection_authority(self):
        with patch.object(guest_control, "inspect") as inspect:
            for field in ("inspect_only", "mode", "command", "argv", "SSH_ORIGINAL_COMMAND"):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    self.invoke_main(["--inspect-only"], {"operation": "inspect", field: False})
            inspect.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_extra_argv_cannot_select_operator_or_expiry(self):
        for arguments in (
            ["--inspect-only", "--expire-id", self.scenario_id],
            ["--expire-id", self.scenario_id, "--inspect-only"],
            ["--inspect-only", "--operator"], ["--inspect-only", "--inspect-only"],
            ["--operator"],
        ):
            with self.subTest(arguments=arguments), \
                    patch.object(guest_control, "dispatch") as dispatch, \
                    patch.object(guest_control, "recovery") as recovery, \
                    self.assertRaises(ValueError):
                self.invoke_main(arguments, {"operation": "inspect"})
            dispatch.assert_not_called()
            recovery.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_isolated_cli_rejects_runner_repairs_and_argument_injection(self):
        # Execute the real __main__ exception/exit path in a separate Python
        # process. Emulate only euid; no root, Linux commands, or live paths
        # are needed because every probe must fail before opening any file.
        script = GUEST_DIR / "guest_control.py"
        launcher = (
            "import os,runpy,sys;"
            "os.geteuid=lambda:0;"
            f"sys.path.insert(0,{str(GUEST_DIR)!r});"
            f"sys.argv=[{str(script)!r},*sys.argv[1:]];"
            f"runpy.run_path({str(script)!r},run_name='__main__')"
        )
        for arguments, request in (
            (["--inspect-only"], {"operation": "recover_workload", "scenario_id": self.scenario_id}),
            (["--inspect-only"], {"operation": "start_disk", "scenario_id": self.scenario_id}),
            (["--inspect-only", "--expire-id", self.scenario_id], {"operation": "inspect"}),
            (["--expire-id", self.scenario_id, "--inspect-only"], {"operation": "inspect"}),
        ):
            with self.subTest(arguments=arguments, request=request):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", launcher, *arguments],
                    input=json.dumps(request), text=True, capture_output=True,
                    timeout=15, cwd=self.root,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                         "SSH_ORIGINAL_COMMAND": "--expire-id " + self.scenario_id},
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("Guest operation rejected or failed", result.stderr)
                self.assertNotIn("FileNotFoundError", result.stderr)

    def test_all_entry_points_require_root(self):
        for arguments in ([], ["--inspect-only"], ["--expire-id", self.scenario_id]):
            with self.subTest(arguments=arguments), \
                    patch.object(guest_control, "dispatch") as dispatch, \
                    patch.object(guest_control, "recovery") as recovery, \
                    self.assertRaises(PermissionError):
                self.invoke_main(arguments, {"operation": "inspect"}, root=False)
            dispatch.assert_not_called()
            recovery.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_root_stdin_recovers_as_operator(self):
        self.write_state(operation="start_memory")
        with patch.object(guest_control.Config, "write") as config_write, \
                patch.object(guest_control, "run", return_value="") as run:
            result = self.invoke_main([], {"operation": "recover_workload", "scenario_id": self.scenario_id})
        self.assertEqual(result["recovery_owner"], "operator")
        config_write.assert_called_once_with()
        run.assert_called_once_with(["systemctl", "restart", "asre-demo.service"])
        self.assertEqual(json.loads(self.state.read_text())["status"], "restored")

    def test_root_stdin_restores_backup_agent(self):
        policy = json.loads(self.policy.read_text())
        self.policy.write_text(json.dumps({**policy, "role": "backup"}))
        self.write_state(operation="stop_guest_agent")
        with patch.object(guest_control.Config, "write") as config_write, \
                patch.object(guest_control, "run", return_value="") as run:
            result = self.invoke_main([], {"operation": "restore_guest_agent", "scenario_id": self.scenario_id})
        self.assertEqual(result["recovery_owner"], "operator")
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["systemctl", "start", "walinuxagent.service"],
            ["systemctl", "is-active", "--quiet", "walinuxagent.service"],
        ])
        config_write.assert_not_called()

    def test_stale_root_expiry_never_mutates(self):
        for state in (None, self.write_state(), self.write_state(status="restored")):
            with self.subTest(state=state):
                if state is None:
                    self.state.unlink(missing_ok=True)
                else:
                    self.state.write_text(json.dumps(state))
                expected_id = str(uuid4()) if state and state["status"] == "active" else self.scenario_id
                before = self.state.read_bytes() if self.state.exists() else None
                with patch.object(guest_control, "recovery") as recovery, \
                        patch.object(guest_control, "atomic_json") as atomic, \
                        patch.object(guest_control.subprocess, "run") as process:
                    result = self.invoke_main(["--expire-id", expected_id])
                self.assertEqual(result["status"], "stale_timer")
                recovery.assert_not_called()
                atomic.assert_not_called()
                process.assert_not_called()
                self.assertEqual(before, self.state.read_bytes() if self.state.exists() else None)

    def test_watchdog_keeps_owned_cleanup_and_recovery_attribution(self):
        with patch.object(guest_control.Config, "write") as config_write, \
                patch.object(guest_control, "run", return_value="") as run, \
                patch.object(guest_control, "clean_scenario", return_value=2) as cleanup:
            result = self.invoke_main(["--expire-id", self.scenario_id])
        cleanup.assert_called_once_with(self.scenario_id)
        config_write.assert_called_once_with()
        run.assert_called_once_with(["systemctl", "restart", "asre-demo.service"])
        self.assertEqual(result["removed_files"], 2)
        self.assertEqual(result["recovery_owner"], "watchdog")
        state = json.loads(self.state.read_text())
        self.assertEqual(state["recovery_owner"], "watchdog")
        self.assertEqual(state["scenario_id"], self.scenario_id)

    @unittest.skipUnless(sys.platform == "linux", "Watchdog manifest cleanup requires Linux dir_fd support")
    def test_watchdog_enforces_real_manifest_ownership_and_exact_cleanup(self):
        scenario = self.root / "data" / self.scenario_id
        scenario.mkdir(parents=True)
        owned = scenario / "00000000.bin"
        owned.write_bytes(b"owned fixture only")
        unrelated = self.root / "data" / "unrelated"
        unrelated.write_text("preserve")
        manifest = scenario / "manifest.json"
        value = {"owner": "another-owner", "scenario_id": self.scenario_id, "files": [owned.name]}
        manifest.write_text(json.dumps(value))
        with patch.object(demo_common, "validate_data_root") as volume_check, \
                patch.object(guest_control, "clean_scenario",
                             side_effect=lambda scenario_id: demo_common.clean_scenario(scenario_id, self.root)), \
                patch.object(guest_control.Config, "write"), \
                patch.object(guest_control, "run", return_value=""):
            # Only the disposable fixture mount check is substituted. The
            # production manifest, descriptor, and file ownership checks run.
            with self.assertRaises(ValueError):
                self.invoke_main(["--expire-id", self.scenario_id])
            self.assertTrue(owned.exists())
            self.assertEqual(json.loads(self.state.read_text())["status"], "active")
            manifest.write_text(json.dumps({**value, "owner": "asre-demo"}))
            result = self.invoke_main(["--expire-id", self.scenario_id])
        self.assertEqual(volume_check.call_count, 2)
        self.assertFalse(scenario.exists())
        self.assertEqual(unrelated.read_text(), "preserve")
        self.assertEqual(result["removed_files"], 1)
        self.assertEqual(result["recovery_owner"], "watchdog")

    def test_root_fault_replay_preserves_original_expiry_without_mutation(self):
        original = self.write_state()
        self.policy.write_text(self.policy.read_text().replace("false", "true"))
        request = {key: original[key] for key in (
            "operation", "scenario_id", "ttl_seconds", "byte_limit", "interval_seconds",
        )}
        before = self.state.read_bytes(), self.config.read_bytes()
        with patch.object(guest_control, "atomic_json") as atomic, \
                patch.object(guest_control.Config, "write") as config_write, \
                patch.object(guest_control.subprocess, "run") as process:
            self.assertEqual(self.invoke_main([], request), original)
            with self.assertRaises(ValueError):
                self.invoke_main([], {**request, "ttl_seconds": 120})
        for mocked in (atomic, config_write, process):
            mocked.assert_not_called()
        self.assertEqual(before, (self.state.read_bytes(), self.config.read_bytes()))

    def test_provisioned_access_is_exact_and_baseline_agrees(self):
        root = GUEST_DIR.parents[1]
        command = "/usr/bin/python3 /usr/local/lib/asre-demo/guest_control.py --inspect-only"
        self.assertIn(f'restrict,command="sudo -n {command}"', (GUEST_DIR / "bootstrap.sh").read_text())
        self.assertIn(f'asrerunner ALL=(root) NOPASSWD: {command}\\n', (root / "infra" / "compute.tf").read_text())
        self.assertIn(f'restrict,command="sudo -n {command}" ssh-', (root / "scripts" / "Test-GuestBaseline.sh").read_text())
        self.assertIn(f'restrict,command="sudo -n {command}" ssh-', (root / "scripts" / "Install-RuntimePublicKey.sh").read_text())


# Keep ordinary discovery unprivileged; root Linux QA must run every real probe.
@unittest.skipUnless(
    sys.platform == "linux" and os.geteuid() == 0,
    "Installer executable fixtures require Linux and root for real ownership/mode checks",
)
class RuntimeEnrollmentTests(unittest.TestCase):
    KEY = base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))).decode("ascii")
    OTHER = base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32, 64))).decode("ascii")

    def setUp(self):
        # Metadata checks execute for real, so Linux QA must run these as root.
        self.assertEqual(os.geteuid(), 0, "Run the Linux fixture suite as root")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.file = self.root / "authorized_keys"
        self.script = self.root / "installer.sh"
        source = (GUEST_DIR.parents[1] / "scripts" / "Install-RuntimePublicKey.sh").read_text()
        destination = "file=/home/asrerunner/.ssh/authorized_keys"
        self.assertEqual(source.count(destination), 1)
        # Only the disposable COPY is redirected; production has no override.
        self.script.write_text(source.replace(destination, f"file={self.file}"))
        self.fixture(b"")

    def fixture(self, content):
        self.file.write_bytes(content)
        os.chown(self.file, 0, 0)
        self.file.chmod(0o644)

    def canonical(self, key=None):
        return (
            'restrict,command="sudo -n /usr/bin/python3 '
            '/usr/local/lib/asre-demo/guest_control.py --inspect-only" '
            f"ssh-ed25519 {key or self.KEY}\n"
        ).encode()

    def invoke(self, arguments=None, *, write_failure=False):
        def prohibit_writes():
            import resource
            import signal
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

        result = subprocess.run(
            ["bash", str(self.script), *(arguments if arguments is not None else [self.KEY])],
            capture_output=True, timeout=15,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            preexec_fn=prohibit_writes if write_failure else None,
        )
        self.assertEqual(list(self.root.glob(".authorized_keys.*")), [], result.stderr)
        return result

    def assert_success(self, expected, arguments=None):
        result = self.invoke(arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"ASRE_RUNTIME_SSH_INSTALLED\n")
        self.assertEqual(self.file.read_bytes(), expected)
        info = self.file.stat()
        self.assertEqual((info.st_uid, info.st_gid, info.st_mode & 0o7777), (0, 0, 0o644))
        self.assertFalse(self.file.is_symlink())

    def assert_rejected(self, arguments=None, *, write_failure=False):
        before = self.file.read_bytes()
        info = self.file.lstat()
        result = self.invoke(arguments, write_failure=write_failure)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(b"ASRE_RUNTIME_SSH_INSTALLED", result.stdout)
        self.assertEqual(self.file.read_bytes(), before)
        after = self.file.lstat()
        self.assertEqual((after.st_ino, after.st_uid, after.st_gid, after.st_mode),
                         (info.st_ino, info.st_uid, info.st_gid, info.st_mode))
        return result

    def test_absent_key_preserves_comments_blanks_and_missing_final_newline(self):
        original = f"# keep\r\n\nssh-ed25519 {self.OTHER} no final newline".encode()
        self.fixture(original)
        expected = self.canonical() + original
        self.assert_success(expected)
        self.assert_success(expected)

    def test_legacy_before_canonical_and_varied_duplicates_are_all_migrated(self):
        unrelated = f"# preserved\nssh-ed25519 {self.OTHER} unrelated\n\n".encode()
        duplicates = (
            f'command="sudo -n /usr/bin/python3 /usr/local/lib/asre-demo/guest_control.py" ssh-ed25519 {self.KEY} old\n'
            f'from="127.0.0.1",restrict,command="echo hello world" ssh-ed25519 {self.KEY} varied\n'
            f"ssh-ed25519 {self.KEY} unrestricted\n"
        ).encode() + self.canonical() + f"ssh-ed25519 {self.KEY}".encode()
        self.fixture(duplicates + b"\n\n" + unrelated)
        expected = self.canonical() + b"\n" + unrelated
        self.assert_success(expected)
        self.assert_success(expected)

    def test_two_existing_keys_survive_enrolling_either_identity(self):
        self.fixture(f"ssh-ed25519 {self.KEY} runtime\nssh-ed25519 {self.OTHER} session\n".encode())
        self.assert_success(self.canonical() + f"ssh-ed25519 {self.OTHER} session\n".encode())
        expected = self.canonical(self.OTHER) + self.canonical()
        self.assert_success(expected, [self.OTHER])
        self.assert_success(expected, [self.OTHER])

    def test_comment_and_quoted_command_lookalikes_are_not_key_fields(self):
        unrelated = (
            f"# ssh-ed25519 {self.KEY}\n"
            f'command="echo ssh-ed25519 {self.KEY}",restrict ssh-ed25519 {self.OTHER} trailing "\n'
            f"ssh-ed25519 {self.OTHER} ssh-ed25519 {self.KEY} unmatched ' comment"
        ).encode()
        self.fixture(unrelated)
        self.assert_success(self.canonical() + unrelated)

    def test_missing_extra_and_malformed_arguments_do_not_modify_file(self):
        self.fixture(f"ssh-ed25519 {self.OTHER}\n".encode())
        for arguments in ([], [self.KEY, "extra"], [""], ["not-a-key"], ["AAAA"],
                          [self.OTHER + "\n"], [self.KEY + " comment"], ["ssh-ed25519 " + self.KEY]):
            with self.subTest(arguments=arguments):
                self.assert_rejected(arguments)

    def test_ambiguous_leading_fields_are_rejected_before_staging(self):
        for line in (
            f'command="unterminated ssh-ed25519 {self.KEY}\n',
            f"unknown unknown ssh-ed25519 {self.KEY}\n",
            "ssh-ed25519\n", "ssh-ed25519 AAAA\n",
        ):
            with self.subTest(line=line):
                self.fixture(self.canonical() + line.encode())
                self.assert_rejected()

    def test_wrong_mode_or_ownership_is_rejected(self):
        for mode, uid, gid in ((0o600, 0, 0), (0o644, 1, 0), (0o644, 0, 1), (0o4644, 0, 0)):
            with self.subTest(mode=mode, uid=uid, gid=gid):
                self.fixture(b"# untouched\n")
                os.chown(self.file, uid, gid)
                self.file.chmod(mode)
                self.assert_rejected()

    def test_symlink_missing_and_nonregular_destinations_are_rejected(self):
        target = self.root / "target"
        self.file.rename(target)
        self.file.symlink_to(target)
        self.assert_rejected()
        self.assertEqual(target.read_bytes(), b"")
        self.file.unlink()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"ASRE_RUNTIME_SSH_INSTALLED", result.stdout)
        self.assertFalse(self.file.exists())
        self.file.mkdir()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"ASRE_RUNTIME_SSH_INSTALLED", result.stdout)
        self.assertTrue(self.file.is_dir())

    def test_pre_replacement_write_failure_leaves_original_and_cleans_stage(self):
        self.fixture(f"ssh-ed25519 {self.OTHER} untouched\n".encode())
        result = self.assert_rejected(write_failure=True)
        self.assertIn(b"File too large", result.stderr)


if __name__ == "__main__":
    unittest.main()
