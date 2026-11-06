"""Execute the actual backup driver guest program without guest or Azure writes."""

import base64
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class BackupGuestProgramTests(unittest.TestCase):
    episode = "00000023-000a-4000-9000-000000000023"
    helper = b"tested-helper"
    policy = {"role": "backup", "owner": "asre-demo", "faults_enabled": False}
    restored = {
        "scenario_id": episode, "status": "restored", "recovery_owner": "watchdog",
        "cleanup_before_deadline": True, "cleanup_deadline_margin_seconds": 64.1,
    }

    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1].joinpath(
            "scripts", "Invoke-BackupIncident.ps1",
        ).read_text(encoding="utf-8")
        cls.program = source.split("$program = @'\n", 1)[1].split("\n'@", 1)[0]

    def exercise(self, action, state=None, override=None, policy=None):
        binding = {
            "action": action, "scenario_id": self.episode, "absolute_deadline": 1900,
            "admission_cutoff": 1120, "helper_sha256": hashlib.sha256(self.helper).hexdigest(),
        }
        binding.update(override or {})
        files = {
            "/usr/local/lib/asre-demo/guest_control.py": self.helper,
            "/etc/asre-demo/policy.json": json.dumps(self.policy if policy is None else policy).encode(),
        }
        if state is not None:
            files["/var/lib/asre-demo/state.json"] = json.dumps(state).encode()
        calls = []

        class FakePath:
            def __init__(self, name):
                self.name = name

            def read_bytes(self):
                return files[self.name]

            def exists(self):
                return self.name in files

            def iterdir(self):
                if self.name != "/srv/asre-demo/data":
                    raise AssertionError("Unexpected guest directory.")
                return iter(())

        def run(arguments, **kwargs):
            request = json.loads(kwargs["input"])
            calls.append((arguments, request))
            return SimpleNamespace(
                returncode=0, stderr="", stdout=json.dumps({
                    "status": "active" if action == "Trigger" else "restored",
                    "scenario_id": self.episode,
                }),
            )

        encoded = base64.b64encode(json.dumps(binding).encode()).decode()
        output = io.StringIO()
        with patch("pathlib.Path", FakePath), patch("subprocess.run", run), \
                patch("time.time", return_value=1000), patch(
                    "urllib.request.urlopen",
                    lambda *args, **kwargs: io.StringIO('{"mode":"normal","status":"ok"}'),
                ), contextlib.redirect_stdout(output):
            exec(compile(self.program.replace("ASRE_BINDING", encoded), "<backup-guest-fixture>", "exec"), {})
        return json.loads(output.getvalue())["result"], calls

    def test_status_does_not_invoke_helper(self):
        result, calls = self.exercise("Status")
        self.assertEqual(result["status"], "observed")
        self.assertTrue(result["owned_data_empty"])
        self.assertEqual(calls, [])

    def test_trigger_uses_only_remaining_original_lifetime(self):
        result, calls = self.exercise("Trigger")
        self.assertEqual(result["status"], "active")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], {
            "operation": "stop_guest_agent", "scenario_id": self.episode,
            "ttl_seconds": 895, "byte_limit": 0, "interval_seconds": 2,
        })
        self.assertEqual(calls[0][0][-1], "--operator-fault")

    def test_reset_preserves_original_watchdog_proof(self):
        result, calls = self.exercise("Reset", state=copy.deepcopy(self.restored))
        self.assertEqual(result, self.restored)
        self.assertEqual(calls, [])

    def test_reset_active_original_episode_uses_existing_helper(self):
        result, calls = self.exercise("Reset", state={"status": "active", "scenario_id": self.episode})
        self.assertEqual(result["status"], "restored")
        self.assertEqual(calls[0][1], {"operation": "restore_guest_agent", "scenario_id": self.episode})

    def test_expired_admission_rejects_fault(self):
        with self.assertRaisesRegex(RuntimeError, "admission expired"):
            self.exercise("Trigger", override={"admission_cutoff": 999})

    def test_short_remaining_deadline_rejects_fault(self):
        with self.assertRaisesRegex(RuntimeError, "Insufficient original"):
            self.exercise("Trigger", override={"absolute_deadline": 1120})

    def test_foreign_role_rejects_fault(self):
        with self.assertRaisesRegex(RuntimeError, "fixed backup role"):
            self.exercise("Trigger", policy={**self.policy, "role": "linux"})

    def test_foreign_restored_episode_rejects_reset(self):
        with self.assertRaisesRegex(RuntimeError, "owner does not match"):
            self.exercise("Reset", state={**self.restored, "scenario_id": "foreign"})


if __name__ == "__main__":
    unittest.main()
