import json
import subprocess
import sys
import unittest
from pathlib import Path

POLICY = Path(__file__).resolve().parents[1] / "agent/hooks/monitor-case-policy.py"


class MonitorCasePolicyTests(unittest.TestCase):
    def run_audit(self, value):
        return subprocess.run(
            [sys.executable, str(POLICY)], input=value, text=True, capture_output=True, check=False,
        )

    def test_native_framework_names_and_arguments_never_block_completed_tools(self):
        for name in ("ReadFile", "read_skill_file", "functions.asre_servicenow_case", "native.tool-wrapper"):
            with self.subTest(name=name):
                result = self.run_audit(json.dumps({
                    "hook_event_name": "PostToolUse", "tool_name": name,
                    "tool_input": {"framework_parameter": "private-argument"},
                    "tool_result": {"status": "blocked", "detail": "private-result"},
                    "tool_succeeded": True,
                }))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout), {"decision": "allow"})
                self.assertIn(name, result.stderr)
                self.assertNotIn("private-argument", result.stdout + result.stderr)
                self.assertNotIn("private-result", result.stdout + result.stderr)

    def test_unknown_name_is_redacted_without_replacing_native_permissions(self):
        result = self.run_audit(json.dumps({
            "hook_event_name": "PostToolUse", "tool_name": "private\nname",
        }))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"decision": "allow"})
        self.assertNotIn("private", result.stderr)
        self.assertIn("framework-tool", result.stderr)

    def test_malformed_context_fails_explicitly_without_exposing_input(self):
        for value in ("not-json-private", "[]", "null", "{}", " " * 131073,
                      '{"hook_event_name":"Stop","tool_result":"private"}'):
            with self.subTest(value=value[:20]):
                result = self.run_audit(value)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("private", result.stderr)
                self.assertIn("invalid or oversized context", result.stderr)


if __name__ == "__main__":
    unittest.main()
