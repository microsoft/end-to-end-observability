import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "agent" / "hooks" / "review-case-policy.py"
SPEC = importlib.util.spec_from_file_location("asre_review_case_policy", POLICY)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class ReviewCasePolicyTests(unittest.TestCase):
    def call(self, name, args):
        return policy.permitted({"tool_name": name, "tool_input": args})

    def note(self):
        return f"ASRE REVIEW | INC0900001 | {uuid4()}\nFixture-only findings.\n{policy.FOOTER}"

    def test_native_case_read_is_pinned_to_one_sys_id(self):
        self.assertTrue(self.call("GetServiceNowIncident", {"incidentSystemId": policy.CASE_ID}))
        for value in ("INC0900001", str(uuid4()), "0" * 32, policy.CASE_ID + " "):
            with self.subTest(value=value):
                self.assertFalse(self.call("GetServiceNowIncident", {"incidentSystemId": value}))

    def test_internal_correlated_review_note_is_allowed(self):
        args = {"incidentSystemId": policy.CASE_ID, "discussionEntry": self.note(), "customerFacing": False}
        self.assertTrue(self.call("PostServiceNowDiscussionEntry", args))
        args["customerFacing"] = "false"
        self.assertTrue(self.call("ServiceNowPluginDefinition.PostServiceNowDiscussionEntry", json.dumps(args)))

    def test_customer_comments_and_unrelated_cases_are_blocked(self):
        args = {"incidentSystemId": policy.CASE_ID, "discussionEntry": self.note()}
        for value in (True, "true", "False", 0, None):
            with self.subTest(customer_facing=value):
                self.assertFalse(self.call("PostServiceNowDiscussionEntry", {**args, "customerFacing": value}))
        self.assertFalse(self.call("PostServiceNowDiscussionEntry", {**args, "incidentSystemId": "0" * 32}))

    def test_uncorrelated_oversized_or_nonreview_notes_are_blocked(self):
        for note in ("unstructured note", self.note().replace(policy.FOOTER, "Resolved"),
                     self.note() + ("x" * 6001), self.note() + "\0"):
            with self.subTest(note_length=len(note)):
                self.assertFalse(self.call("PostServiceNowDiscussionEntry", {
                    "incidentSystemId": policy.CASE_ID, "discussionEntry": note,
                }))

    def test_only_trusted_skill_paths_are_readable(self):
        for skill in policy.SKILLS:
            self.assertTrue(self.call("ReadFile", {"filePath": f"skills/{skill}/SKILL.md"}))
            self.assertTrue(self.call("ReadFile", {"path": f"/workspace/skills/{skill}/SKILL.md"}))
            self.assertTrue(self.call("read_skill_file", {"skill_name": skill}))
        for path in ("../skills/asre-linux-rca/SKILL.md", "skills/asre-linux-rca/secret.txt",
                     "skills/unapproved/SKILL.md", "skills/asre-linux-rca/../SKILL.md",
                     "C:\\skills\\asre-linux-rca\\SKILL.md", "skills/asre-linux-rca/%53KILL.md"):
            with self.subTest(path=path):
                self.assertFalse(self.call("ReadFile", {"filePath": path}))
        self.assertFalse(self.call("ReadFile", {
            "filePath": "/private", "path": "skills/asre-linux-rca/SKILL.md",
        }))

    def test_fixed_evidence_commands_and_complete_result_identity(self):
        target = sorted(policy.TARGETS)[0]
        for action in ("inspect", "telemetry", "patch_jobs", "backup_jobs"):
            self.assertTrue(self.call("asre-review-evidence", {"resource_id": target, "action": action}))
        for page in (0, "0", 31):
            self.assertTrue(self.call("asre-review-evidence", {
                "resource_id": target, "action": "result", "request_id": str(uuid4()), "page": page,
            }))
        for changes in ({"action": "repair"}, {"resource_id": "/subscriptions/unrelated"},
                        {"request_id": ""}, {"page": -1}, {"page": True}, {"page": "01"}):
            self.assertFalse(self.call("asre-review-evidence", {
                "resource_id": target, "action": "result", "request_id": str(uuid4()), **changes,
            }))

    def test_mutation_delegation_and_knowledge_writes_are_not_tools(self):
        for name in ("UpdateServiceNowIncident", "ResolveServiceNowIncident",
                     "AcknowledgeServiceNowIncident", "UploadKnowledgeDocument",
                     "WriteFile", "RunAzCliWriteCommands", "ExecutePythonCode", "handoff"):
            with self.subTest(name=name):
                self.assertFalse(self.call(name, {"incidentSystemId": policy.CASE_ID}))
        self.assertTrue(self.call("SearchMemory", {"query": "ASRE approved environment"}))
        self.assertFalse(self.call("SearchMemory", {"query": " "}))

    def test_native_hook_denies_ambiguous_json_and_bad_context(self):
        for text in ('{}', '[]', 'not json', '{"tool_name":"ReadFile","tool_name":"RunShell"}',
                     '{"tool_name":"GetServiceNowIncident","tool_input":{"incidentSystemId":"'
                     + policy.CASE_ID + '","IncidentSystemId":"' + "0" * 32 + '"}}',
                     " " * 65537):
            result = subprocess.run([sys.executable, str(POLICY)], input=text, text=True,
                                    capture_output=True, check=True)
            body = json.loads(result.stdout)
            self.assertFalse(body["ok"])
            self.assertEqual(body["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
