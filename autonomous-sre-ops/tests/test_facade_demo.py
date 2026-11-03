"""Exercise the real CLI entry point in child processes; all HTTP is fake."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from operations.facade_demo import RESOURCE, synthetic

ROOT = Path(__file__).resolve().parents[1]
SECRET = "synthetic-stdin-secret-not-live"

# Inject at the existing HTTPS opener boundary, not a production transport flag.
# No OAuth body or authorization header is ever retained by the fixture.
HARNESS = r'''
import io, json, os, runpy, sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import operations.servicenow as sn

config = json.loads(Path(sys.argv[1]).read_text())
sys.argv = ["operations.facade_demo"] + sys.argv[2:]
state = Path(config["state"])
log = Path(config["log"])
mode = config["mode"]
reference = {"sys_id": "1" * 32, "number": "INC0099999", "record_type": "incident"}
journal = "2" * 32

def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Real network forbidden in CLI tests.")
sys.addaudithook(deny_network)

class Response(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status

class Opener:
    def open(self, request, timeout):
        method = request.get_method()
        path = urlparse(request.full_url).path
        if path == "/oauth_token.do":
            grant = parse_qs(request.data.decode())
            if grant["client_secret"] != ["synthetic-stdin-secret-not-live"]:
                raise RuntimeError("Invalid fixture credential.")
            event = {"method": method, "path": path, "client_id": grant["client_id"], "scope": grant["scope"]}
        else:
            event = {"method": method, "path": path, "body": json.loads(request.data) if request.data else None}
        with log.open("a") as stream:
            stream.write(json.dumps(event) + "\n")
        if path == "/oauth_token.do":
            return Response(200, json.dumps({"access_token": "synthetic-token", "token_type": "Bearer",
                "expires_in": 1800, "scope": sn.SCOPE}).encode())
        if path == sn.BASE + "/health":
            health = {"version": "0.1.0", "instance": "example-instance", "scope": "x_0000000_asre",
                "execution_mode": "Review", "azure_mutations_exposed": False, "human_approval_enabled": False,
                "trusted_verifier_bound": False, "feedback": "fixture", "agent_continuation": "fixture"}
            return Response(200, json.dumps({"result": health}).encode())
        record = json.loads(state.read_text())
        plan = record["intent"]
        evidence = plan["input"]["evidence"]
        episode = evidence["episode_id"]
        name = "note" if path.endswith("/notes") else "create"
        if "/writes/" in path:
            name = "note" if path.endswith(plan["note"]["write_request_id"]) else "create"
        write_id = plan[name]["write_request_id"]
        if method == "GET" and "/writes/" not in path:
            context = {"episode_id": episode, "case_reference": reference, "resource_id": evidence["resource_id"],
                "mode": "reactive", "signal_family": "filesystem", "state": {"value": "1", "label": "New"},
                "assignment_group": {"sys_id": sn.GROUP, "label": "Fixture"},
                "ci": {"sys_id": sn.CIS["linux"], "label": "Fixture"}, "priority": {"value": "4", "label": "Low"},
                "short_description": "Synthetic", "description": "Synthetic; not real-alert proof.",
                "revision": "1", "updated_at": "2026-10-18 00:00:00", "investigation_reference": None,
                "evidence": evidence, "instance_url": sn.ORIGIN + "/incident.do?sys_id=" + reference["sys_id"]}
            if mode == "wrong-owner":
                context["assignment_group"]["sys_id"] = "f" * 32
            if mode == "wrong-ci":
                context["ci"]["sys_id"] = "f" * 32
            return Response(200, json.dumps({"result": context}).encode())
        result = {"outcome": "applied", "episode_id": episode, "write_request_id": write_id,
                  "case_reference": reference}
        if name == "note":
            result["journal_sys_id"] = journal
        if method == "POST":
            # Verify intent AND pending marker really reached disk before remote mutation.
            assert record["writes"][name]["status"] == "pending"
            assert event["body"] == plan[name]
            if mode == name + "-timeout":
                raise TimeoutError("private response body " + "synthetic-stdin-secret-not-live")
            if mode == name + "-malformed":
                return Response(200, b"private response body synthetic-stdin-secret-not-live")
            if mode in {name + "-unknown", name + "-202"}:
                result = {"outcome": "unknown", "episode_id": episode, "write_request_id": write_id,
                          "message": "private response body synthetic-stdin-secret-not-live"}
                return Response(202 if mode.endswith("202") else 200, json.dumps({"result": result}).encode())
            if mode == name + "-202-applied":
                return Response(202, json.dumps({"result": result}).encode())
            if mode == name + "-denied":
                return Response(403, b"private response body synthetic-stdin-secret-not-live")
            if mode == name + "-not-applied":
                result = {"outcome": "not_applied", "episode_id": episode, "write_request_id": write_id,
                          "error_code": "fixture-private-code"}
        if method == "GET" and "/writes/" in path:
            if mode == "reconcile-unknown":
                result = {"outcome": "unknown", "episode_id": episode, "write_request_id": write_id,
                          "message": "private response body synthetic-stdin-secret-not-live"}
            elif mode == "reconcile-wrong-id":
                result["write_request_id"] = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
            elif mode == "reconcile-no-journal":
                result.pop("journal_sys_id", None)
            elif mode == "reconcile-wrong-case":
                result["case_reference"] = {**reference, "sys_id": "f" * 32}
        return Response(200, json.dumps({"result": result}).encode())

sn.build_opener = lambda *args: Opener()
if mode in {"interrupt-create", "interrupt-note"}:
    import operations.facade_demo as demo
    original = demo.save_receipt
    def crash(path, record, name, receipt):
        if mode == "interrupt-" + name:
            os._exit(91)  # Remote success happened; receipt and lock cleanup did not.
        return original(path, record, name, receipt)
    demo.save_receipt = crash
    raise SystemExit(demo.main())
runpy.run_module("operations.facade_demo", run_name="__main__")
'''


class FacadeDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = self.directory / "rehearsal.json"
        self.source = self.directory / "input.json"
        self.config = self.directory / "fake.json"
        self.log = self.directory / "calls.jsonl"

    def cli(self, action, *args, mode="normal", secret=SECRET, real_entry=False):
        self.config.write_text(json.dumps({"state": str(self.state), "log": str(self.log), "mode": mode}))
        command = [sys.executable, "-B"]
        if real_entry:
            command += ["-m", "operations.facade_demo"]
        else:
            command += ["-c", HARNESS, str(self.config)]
        command += [action]
        if action != "readiness":
            command += ["--state", str(self.state)]
        command += list(args)
        result = subprocess.run(command, input=secret, text=True, capture_output=True, cwd=ROOT, timeout=30)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn("private response body", result.stdout + result.stderr)
        return result

    def prepare(self):
        result = self.cli("prepare", "--synthetic")
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.record()

    def record(self):
        return json.loads(self.state.read_text())

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def posts(self):
        return [call for call in self.calls() if call["method"] == "POST" and call["path"] != "/oauth_token.do"]

    def live_send(self, mode="normal"):
        return self.cli("send", "--secret-stdin", "--confirm-new-case", mode=mode)

    def resolve(self, name="create", mode="normal"):
        return self.cli("reconcile", "--secret-stdin", "--write", name, mode=mode)

    def test_actual_module_prepare_and_check_are_offline_and_explicitly_synthetic(self):
        result = self.cli("prepare", "--synthetic", secret="", real_entry=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cli("check", secret="", real_entry=True).returncode, 0)
        record = self.record()
        self.assertEqual(record["intent"]["input"]["kind"], "synthetic")
        self.assertIn("not real-alert proof", record["intent"]["create"]["evidence"]["summary"])
        self.assertIn("not real-alert proof", record["intent"]["note"]["note"])
        self.assertEqual(self.calls(), [])

    def test_prepare_is_repeatable_and_does_not_replace_ids_or_content(self):
        original = self.prepare()
        self.assertEqual(self.cli("prepare", "--synthetic").returncode, 0)
        self.assertEqual(original, self.record())
        self.assertNotEqual(original["intent"]["create"]["write_request_id"], original["intent"]["note"]["write_request_id"])
        self.source.write_text(json.dumps(original["intent"]["input"]))
        self.assertEqual(self.cli("prepare", "--input", str(self.source)).returncode, 0)
        changed = copy.deepcopy(original["intent"]["input"])
        changed["note"] += " Different."
        self.source.write_text(json.dumps(changed))
        self.assertEqual(self.cli("prepare", "--input", str(self.source)).returncode, 2)
        self.assertEqual(original, self.record())

    def test_reference_target_and_provenance_mismatches_fail_before_any_network(self):
        base = synthetic()
        changes = [
            lambda x: x["alert"].update(episode_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
            lambda x: x["alert"].update(id="different-alert"),
            lambda x: x["alert"].update(resource_id=RESOURCE.replace("-linux-", "-patch-")),
            lambda x: x["evidence"].update(mode="proactive"),
            lambda x: x["evidence"].update(signal_family="workload-memory"),
            lambda x: x["evidence"].update(resource_id=RESOURCE.replace("-linux-", "-patch-")),
            lambda x: x["evidence"].update(episode_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
            lambda x: x["evidence"]["evidence"][0].update(reference="https://wrong.invalid/receipt"),
            lambda x: x["receipts"][0].update(request_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
            lambda x: x["receipts"][0].update(mode="Autonomous"),
            lambda x: x["receipts"][0].update(status="accepted"),
            lambda x: x["receipts"][0].update(command="backup_jobs"),
            lambda x: x["receipts"][0].update(resource_id=RESOURCE.replace("-linux-", "-patch-")),
            lambda x: x.update(note="Synthetic but missing proof disclaimer"),
            lambda x: x["evidence"].update(detected_at="2026-02-30T00:00:00Z"),
        ]
        for change in changes:
            value = copy.deepcopy(base)
            change(value)
            self.source.write_text(json.dumps(value))
            with self.subTest(change=change):
                self.assertEqual(self.cli("prepare", "--input", str(self.source)).returncode, 2)
                self.assertFalse(self.state.exists())
        self.assertEqual(self.calls(), [])

    def test_input_is_bounded_nonsecret_and_rejects_duplicate_fields(self):
        for raw in (
            '{"kind":"synthetic","kind":"operator-supplied"}',
            json.dumps({**synthetic(), "client_secret": "unit-only"}),
            json.dumps({**synthetic(), "note": "Authorization: Bearer unit-only"}),
            " " * 48001,
        ):
            self.source.write_text(raw)
            self.assertEqual(self.cli("prepare", "--input", str(self.source)).returncode, 2)
            self.assertFalse(self.state.exists())
        self.assertEqual(self.calls(), [])

    def test_operator_supplied_references_are_not_claimed_verified_offline(self):
        value = synthetic()
        value["kind"] = "operator-supplied"
        self.source.write_text(json.dumps(value))
        result = self.cli("prepare", "--input", str(self.source))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["evidence_proof"], "not verified offline")

    def test_changed_local_ids_or_payloads_are_rejected(self):
        original = self.prepare()
        for change in (
            lambda r: r["intent"]["create"].update(write_request_id="aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"),
            lambda r: r["intent"]["note"].update(note="Different content"),
        ):
            value = copy.deepcopy(original)
            change(value)
            self.state.write_text(json.dumps(value))
            self.assertEqual(self.cli("check").returncode, 2)
            self.assertEqual(self.live_send().returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_readiness_uses_only_pinned_oauth_and_health(self):
        result = self.cli("readiness", "--secret-stdin", "--client-id", "a" * 32)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual([(c["method"], c["path"]) for c in calls], [
            ("POST", "/oauth_token.do"), ("GET", "/api/x_0000000_asre/case_adapter/v1/health")])
        self.assertEqual(calls[0]["client_id"], ["a" * 32])
        self.assertEqual(calls[0]["scope"], ["asre_case_adapter_v1"])

    def test_live_input_and_explicit_new_case_intent_fail_closed(self):
        self.prepare()
        for action, args, secret in (
            ("send", ("--secret-stdin",), SECRET),
            ("send", ("--confirm-new-case",), SECRET),
            ("readiness", ("--secret-stdin",), ""),
            ("readiness", ("--secret-stdin",), "x" * 4097),
            ("readiness", ("--secret-stdin",), "line\nbreak"),
            ("readiness", ("--secret-stdin", "--client-id", "INVALID"), SECRET),
            ("reconcile", ("--secret-stdin",), SECRET),
        ):
            self.assertEqual(self.cli(action, *args, secret=secret).returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_send_persists_original_payloads_and_reads_owned_case_before_note(self):
        original = self.prepare()
        result = self.live_send()
        self.assertEqual(result.returncode, 0, result.stderr)
        posts = self.posts()
        self.assertEqual([p["body"] for p in posts], [original["intent"]["create"], original["intent"]["note"]])
        calls = self.calls()
        note_index = next(i for i, c in enumerate(calls) if c["path"].endswith("/notes"))
        self.assertEqual(calls[note_index - 1]["method"], "GET")
        self.assertEqual(calls[note_index - 1]["path"].split("/")[-1], original["intent"]["input"]["episode_id"])
        self.assertFalse(json.loads(result.stdout)["independent_journal_verified"])
        self.assertEqual(self.live_send().returncode, 0)
        self.assertEqual(len(self.posts()), 2)

    def test_unknown_create_outcomes_never_blindly_repost_or_send_note(self):
        for mode in ("create-timeout", "create-malformed", "create-202", "create-unknown", "create-202-applied", "create-denied"):
            with self.subTest(mode=mode):
                if self.state.exists():
                    self.state.unlink()
                if self.log.exists():
                    self.log.unlink()
                original = self.prepare()
                self.assertEqual(self.live_send(mode).returncode, 2)
                self.assertEqual(self.record()["writes"]["create"]["status"], "pending")
                self.assertEqual(self.live_send().returncode, 2)
                self.assertEqual(len(self.posts()), 1)
                self.assertEqual(self.record()["intent"], original["intent"])
                self.assertEqual(self.record()["writes"]["note"]["status"], "prepared")

    def test_original_create_reconciliation_then_send_only_the_new_note(self):
        original = self.prepare()
        self.assertEqual(self.live_send("create-timeout").returncode, 2)
        self.assertEqual(self.resolve().returncode, 0)
        calls = self.calls()
        self.assertTrue(any(c["path"].endswith("/writes/" + original["intent"]["create"]["write_request_id"]) for c in calls))
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.live_send().returncode, 0)
        self.assertEqual(len(self.posts()), 2)
        self.assertTrue(self.posts()[-1]["path"].endswith("/notes"))

    def test_unknown_reconciliation_stays_pending_and_wrong_receipts_fail(self):
        self.prepare()
        self.live_send("create-timeout")
        for mode in ("reconcile-unknown", "reconcile-wrong-id", "wrong-owner"):
            self.assertEqual(self.resolve(mode=mode).returncode, 2)
            self.assertEqual(self.record()["writes"]["create"]["status"], "pending")
        self.assertEqual(len(self.posts()), 1)

    def test_terminal_rejection_does_not_automatically_retry(self):
        self.prepare()
        self.assertEqual(self.live_send("create-not-applied").returncode, 2)
        self.assertEqual(self.record()["writes"]["create"]["status"], "blocked")
        self.assertEqual(self.live_send().returncode, 2)
        self.assertEqual(len(self.posts()), 1)

    def test_wrong_group_or_ci_prevents_notes_even_after_terminal_reconciliation(self):
        for mode in ("wrong-owner", "wrong-ci"):
            if self.state.exists():
                self.state.unlink()
            if self.log.exists():
                self.log.unlink()
            self.prepare()
            self.assertEqual(self.live_send(mode).returncode, 2)
            self.assertEqual(len(self.posts()), 1)
            self.assertEqual(self.record()["writes"]["note"]["status"], "prepared")

    def test_unknown_note_reconciles_original_journal_and_never_reposts(self):
        self.prepare()
        self.assertEqual(self.live_send("note-timeout").returncode, 2)
        self.assertEqual(self.record()["writes"]["note"]["status"], "pending")
        self.assertEqual(self.live_send().returncode, 2)
        self.assertEqual(self.resolve("note", mode="reconcile-no-journal").returncode, 2)
        self.assertEqual(self.resolve("note", mode="reconcile-wrong-case").returncode, 2)
        self.assertEqual(self.resolve("note").returncode, 0)
        self.assertEqual(self.live_send().returncode, 0)
        self.assertEqual(len(self.posts()), 2)

    def test_interruption_after_remote_create_success_requires_original_reconciliation(self):
        original = self.prepare()
        self.assertEqual(self.live_send("interrupt-create").returncode, 91)
        self.assertEqual(self.record()["writes"]["create"], {"status": "pending", "receipt": None})
        self.assertTrue(self.state.with_name(self.state.name + ".lock").exists())
        self.assertEqual(self.live_send().returncode, 2)
        # Explicit manual stale-lock cleanup, only after child termination.
        self.state.with_name(self.state.name + ".lock").unlink()
        self.assertEqual(self.live_send().returncode, 2)
        self.assertEqual(self.resolve().returncode, 0)
        self.assertEqual(self.live_send().returncode, 0)
        self.assertEqual(len(self.posts()), 2)
        self.assertEqual(self.record()["intent"], original["intent"])

    def test_interruption_after_remote_note_success_never_replays_historical_note(self):
        original = self.prepare()
        self.assertEqual(self.live_send("interrupt-note").returncode, 91)
        self.assertEqual(self.record()["writes"]["note"], {"status": "pending", "receipt": None})
        self.state.with_name(self.state.name + ".lock").unlink()
        self.assertEqual(self.live_send().returncode, 2)
        self.assertEqual(self.resolve("note").returncode, 0)
        self.assertEqual(self.live_send().returncode, 0)
        self.assertEqual(len(self.posts()), 2)
        self.assertEqual(self.record()["intent"], original["intent"])

    def test_prepared_reconciliation_and_existing_lock_do_not_access_network(self):
        self.prepare()
        self.assertEqual(self.resolve().returncode, 2)
        self.state.with_name(self.state.name + ".lock").touch()
        self.assertEqual(self.live_send().returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
