"""Prove the runtime core takes every deployment value from the deployment context.

Everything here runs in fresh interpreters: first under the alternate context (a different value for every
deployment-specific field), then under the first-phase "core" context that has no ServiceNow section, no agent
endpoint, no operator object id and no bootstrap identity, and finally with no context at all (fail closed).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from tests.alt_context import ALT, ROOT, SYNTHETIC, leaks_in_module, run_with_context

OPERATIONS = ROOT / "operations"
OWNED = ("lifecycle", "cloud", "protocol", "ssh", "worker")
PHASE_7 = ("agent_case", "servicenow", "feedback", "feedback_runtime", "facade_demo")
ALL_MODULES = sorted(path.stem for path in OPERATIONS.glob("*.py") if path.stem != "__init__")
OTHER_MODULES = [name for name in ALL_MODULES if name not in OWNED]
PHASE_1_MODULES = [name for name in ALL_MODULES if name not in PHASE_7]

ALT_DOCUMENT = json.loads(ALT.read_text(encoding="utf-8"))
SYNTHETIC_DOCUMENT = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
ROLES = ("linux", "patch", "backup")


def core_document(*, absent):
    """The alternate context reduced to what the first deployment phase needs."""
    document = json.loads(ALT.read_text(encoding="utf-8"))
    if absent:
        del document["servicenow"], document["operator_object_id"], document["agent"]["endpoint"]
    else:
        document["servicenow"] = None
        document["operator_object_id"] = None
        document["agent"]["endpoint"] = None
    del document["identities"]["bootstrap"]
    return document


def run_json(code, **options):
    result = run_with_context(code, **options)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-3000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def derived_expectation(document):
    """What the runtime must derive from a context document, written from the document's own fields."""
    sub = document["subscription_id"]
    account = document["storage"]["account_name"]
    base = f"/subscriptions/{sub}/resourceGroups"
    targets = {role: document["targets"][role] for role in ROLES}
    ids = {role: f"/subscriptions/{sub}/resourcegroups/{item['resource_group']}/providers/microsoft.compute"
                 f"/virtualmachines/{item['name']}" for role, item in targets.items()}
    vault = document["recovery_vault"]
    return {
        "subscription": sub,
        "targets": [[ids[role], role] for role in ROLES],
        "target_ids": ids,
        "evidence_host": f"{account}.blob.core.windows.net",
        "workspace": f"{base}/{document['resource_groups']['control']}/providers/Microsoft.OperationalInsights"
                     f"/workspaces/{document['log_analytics']['name']}",
        "vault": f"{base}/{vault['resource_group']}/providers/Microsoft.RecoveryServices/vaults/{vault['name']}",
        "container": "iaasvmcontainer;iaasvmcontainerv2;"
                     f"{targets['backup']['resource_group']};{targets['backup']['name']}",
        "item": f"vm;iaasvmcontainerv2;{targets['backup']['resource_group']};{targets['backup']['name']}",
        "addresses": [[role, targets[role]["address"]] for role in ROLES],
        "key_vault": document["key_vault"]["uri"],
        "account": account,
        "table_endpoint": document["storage"]["table_endpoint"],
        "blob_endpoint": document["storage"]["blob_endpoint"],
        "queue_endpoint": f"https://{account}.queue.core.windows.net",
    }


FUNCTIONAL = r'''
import base64, io, json, os, sys, time
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from azure.core.exceptions import ResourceNotFoundError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from operations import cloud, lifecycle, protocol, ssh, worker

out = {}


def silent(function, *arguments):
    with redirect_stdout(io.StringIO()) as buffer:
        function(*arguments)
    return buffer.getvalue()


out["lifecycle"] = {
    "subscription": lifecycle.SUBSCRIPTION, "targets": [list(item) for item in lifecycle.TARGETS.items()],
    "targets_type": type(lifecycle.TARGETS).__name__, "evidence_host": lifecycle.EVIDENCE_HOST,
}
out["cloud"] = {"workspace": cloud.WORKSPACE, "vault": cloud.VAULT}
out["ssh"] = {"addresses": [list(item) for item in ssh.ADDRESSES.items()]}
out["worker"] = {
    "vault": worker.VAULT, "account": worker.ACCOUNT, "table_endpoint": worker.TABLE_ENDPOINT,
    "queue_endpoint": worker.QUEUE_ENDPOINT, "queue": worker.QUEUE, "evidence_host": worker.EVIDENCE_HOST,
}

own = OWN["table_endpoint"]
candidates = {
    "own": own, "own_slash": own + "/", "other": OTHER["table_endpoint"], "double_slash": own + "//",
    "path": own + "/x", "http": own.replace("https://", "http://", 1), "upper": own.upper(),
    "query": own + "?sig=x", "port": own + ":443", "userinfo": own.replace("https://", "https://user@", 1),
    "blob": own.replace(".table.", ".blob."), "queue": own.replace(".table.", ".queue."), "empty": "",
    "space": own + " ", "newline": own + "\n", "none": None, "number": 5,
}


def table_accepts(endpoint):
    with patch("azure.data.tables.TableClient") as client, patch("azure.identity.ManagedIdentityCredential"):
        try:
            lifecycle.AzureTable(endpoint, "00000000-0000-4000-8000-000000000000")
        except (ValueError, TypeError):
            return False
        return client.call_args.kwargs["endpoint"] == endpoint


out["table"] = {name: table_accepts(value) for name, value in candidates.items()}

targets = {role: next(key for key, value in lifecycle.TARGETS.items() if value == role)
           for role in ("linux", "patch", "backup")}
reader = cloud.AzureReader(Mock())
with patch.object(reader, "request", return_value={"value": []}) as request:
    reader.backup(targets["backup"])
out["backup_urls"] = [call.args[1] for call in request.call_args_list]
with patch.object(reader, "request", side_effect=lambda *args: {"data": [], "count": 0, "totalRecords": 0}) as request:
    reader.patches(targets["patch"])
out["patch_calls"] = [[call.args[2]["subscriptions"], call.args[2]["query"]] for call in request.call_args_list]
envelope = {"Tables": [
    {"Columns": [{"ColumnName": name} for name in ("SourceTable", "TimeGenerated", "_ResourceId")], "Rows": []},
    {"Columns": [{"ColumnName": name} for name in ("StatusCode", "Severity", "SeverityName")],
     "Rows": [[0, 4, "Info"]]},
]}
with patch.object(reader, "request", return_value=envelope) as request:
    reader.telemetry(targets["linux"])
out["telemetry"] = [request.call_args.args[1], request.call_args.args[2]["query"]]
with patch.object(reader, "patches") as patches, patch.object(reader, "backup") as backup:
    reader.collect()
out["collect_targets"] = [patches.call_args.args[0], backup.call_args.args[0]]

guarded = cloud.AzureReader(Mock(get_token=Mock(return_value=SimpleNamespace(token="fixture-only"))))


def guard(url):
    with patch("operations.cloud.build_opener", side_effect=RuntimeError("the opener was reached")):
        try:
            guarded.request("GET", url)
        except PermissionError:
            return "blocked"
        except RuntimeError:
            return "allowed"
    return "unexpected"


out["guard"] = {
    "own": guard(cloud.ARM + "/subscriptions/" + OWN["subscription"] + "/resourceGroups/x"),
    "other": guard(cloud.ARM + "/subscriptions/" + OTHER["subscription"] + "/resourceGroups/x"),
    "graph": guard(cloud.ARM + "/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01"),
}

public = Ed25519PrivateKey.generate().public_key().public_bytes(
    serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def trust(hosts):
    try:
        return len(ssh.parse_host_trust("\n".join(f"{host} {public}" for host in hosts)))
    except ValueError:
        return "rejected"


addresses = list(ssh.ADDRESSES.values())
out["trust"] = {
    "context": trust(addresses), "extra_host": trust(addresses + ["10.42.0.99"]), "missing": trust(addresses[:2]),
    "foreign": trust(["10.9.9.1", "10.9.9.2", "10.9.9.3"]),
}

with patch.object(worker, "BlobServiceClient") as blob_client:
    worker.blobs("credential")
out["blob_endpoint"] = blob_client.call_args.args[0]

known = "\n".join(f"{host} {public}" for host in addresses)
with patch.dict(os.environ, {"ASRE_SSH_KNOWN_HOSTS_B64": base64.b64encode(known.encode()).decode()}), \
        patch.object(worker, "identity"), patch.object(worker, "SecretClient") as secrets:
    secrets.return_value.get_secret.side_effect = ResourceNotFoundError("fixture secret absent")
    silent(worker.bootstrap)
out["bootstrap"] = {
    "vault": secrets.call_args.args[0],
    "known_hosts_stored": any(call.args[:2] == ("runner-ssh-known-hosts", known)
                              for call in secrets.return_value.set_secret.call_args_list),
}

with patch.object(worker, "identity"), patch.object(worker, "AzureReader") as azure_reader, \
        patch.object(worker, "blobs"):
    azure_reader.return_value.collect.return_value = {"fixture": True}
    out["collect_uri"] = json.loads(silent(worker.collect))["evidence_uri"]

healthy = {"health": {"status": "ok", "mode": "normal"}, "observed_at": time.time(),
           "service": "ActiveState=active\n\nActiveState=active"}
with patch.dict(os.environ, {"ASRE_EXECUTION_MODE": "Review"}), patch.object(worker, "identity"), \
        patch.object(worker, "SecretClient") as secrets, patch.object(worker, "Guest") as guest, \
        patch.object(worker, "AzureReader") as azure_reader, patch.object(worker, "blobs"), \
        patch.object(worker, "TableClient") as table:
    guest.return_value.inspect.return_value = healthy
    azure_reader.return_value.telemetry.return_value = {}
    azure_reader.return_value.collect.return_value = {}
    table.return_value.get_entity.return_value = {"status": "verified"}
    line = silent(worker.verify).strip().splitlines()[-1]
out["verify"] = {
    "table": table.call_args.args[0], "table_name": table.call_args.args[1], "vault": secrets.call_args.args[0],
    "evidence_uri": json.loads(line.split(" ", 1)[1])["evidence_uri"],
    "row_uri": table.return_value.create_entity.call_args.args[0]["evidence_uri"],
}

with patch.dict(os.environ, {"ASRE_EXECUTION_MODE": "Review"}), patch("operations.patch_sensor.run"), \
        patch.object(worker, "identity"), patch.object(worker, "AzureReader"), patch.object(worker, "blobs"), \
        patch.object(worker, "TableClient") as table, patch.object(worker, "QueueClient") as queue, \
        patch.object(worker, "SecretClient") as secrets, patch.object(worker, "Guest"):
    queue.return_value.receive_messages.return_value = iter(())
    status = json.loads(silent(worker.process))
out["process"] = {
    "table": table.call_args.args[0], "queue": queue.call_args.args[0], "queue_name": queue.call_args.args[1],
    "vault": secrets.call_args.args[0], "status": status,
}

out["phase_7_loaded"] = sorted(name for name in sys.modules if name in {"operations." + item for item in PHASE_7})
print(json.dumps(out))
'''

MATRIX = r'''
import importlib, json, sys

matrix = {}
for name in NAMES:
    try:
        importlib.import_module("operations." + name)
    except BaseException as error:
        matrix[name] = f"{type(error).__module__}.{type(error).__qualname__}: {str(error)[:300]}"
    else:
        matrix[name] = "ok"
loaded = sorted(name for name in sys.modules if name in {"operations." + item for item in PHASE_7})
print(json.dumps({"matrix": matrix, "phase_7_loaded": loaded}))
'''


def functional_code(own_document, other_document):
    def pick(document):
        return {"subscription": document["subscription_id"], "table_endpoint": document["storage"]["table_endpoint"]}

    return (f"OWN = {json.dumps(pick(own_document))}\nOTHER = {json.dumps(pick(other_document))}\n"
            f"PHASE_7 = {json.dumps(list(PHASE_7))}\n" + FUNCTIONAL)


def matrix_code(names):
    return f"NAMES = {json.dumps(list(names))}\nPHASE_7 = {json.dumps(list(PHASE_7))}\n" + MATRIX


def imports_of(tree):
    """(enclosing function name or None, imported module) for every import statement."""
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
            elif isinstance(child, ast.Import):
                found.extend((scope, alias.name) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                module = child.module or ""
                if child.level:
                    module = "operations" + ("." + module if module else "")
                if module == "operations":
                    found.extend((scope, "operations." + alias.name) for alias in child.names)
                else:
                    found.append((scope, module))
            else:
                visit(child, scope)

    visit(tree, None)
    return found


class ContextFiles(unittest.TestCase):
    """Writes the first-phase core contexts once per class."""

    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.core_files = {}
        for label, absent in (("null", False), ("absent", True)):
            path = Path(directory.name) / f"context.core-{label}.json"
            path.write_text(json.dumps(core_document(absent=absent)), encoding="utf-8")
            cls.core_files[label] = path
        cls.directory = Path(directory.name)


class LeakTests(unittest.TestCase):
    def test_the_runtime_core_holds_no_value_that_only_the_synthetic_context_has(self):
        for name in OWNED:
            with self.subTest(module=f"operations.{name}"):
                self.assertEqual(leaks_in_module(f"operations.{name}"), [])

    def test_no_other_runtime_module_holds_such_a_value_either(self):
        self.assertTrue(OTHER_MODULES)
        for name in OTHER_MODULES:
            with self.subTest(module=f"operations.{name}"):
                self.assertEqual(leaks_in_module(f"operations.{name}"), [])


class DerivedValueTests(ContextFiles):
    """Every deployment value the runtime core exposes or uses, under each context shape."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.results = {
            "alt": run_json(functional_code(ALT_DOCUMENT, SYNTHETIC_DOCUMENT), context_file=ALT),
            "synthetic": run_json(functional_code(SYNTHETIC_DOCUMENT, ALT_DOCUMENT), context_file=SYNTHETIC),
            "core-null": run_json(functional_code(ALT_DOCUMENT, SYNTHETIC_DOCUMENT),
                                  context_file=cls.core_files["null"]),
            "core-absent": run_json(functional_code(ALT_DOCUMENT, SYNTHETIC_DOCUMENT),
                                    context_file=cls.core_files["absent"]),
        }

    def check(self, label, document):
        found = self.results[label]
        want = derived_expectation(document)
        with self.subTest(context=label, part="lifecycle"):
            self.assertEqual(found["lifecycle"]["subscription"], want["subscription"])
            self.assertEqual(found["lifecycle"]["targets"], want["targets"])
            self.assertEqual(found["lifecycle"]["targets_type"], "dict")
            self.assertEqual(found["lifecycle"]["evidence_host"], want["evidence_host"])
        with self.subTest(context=label, part="table endpoint"):
            accepted = sorted(name for name, value in found["table"].items() if value)
            self.assertEqual(accepted, ["own", "own_slash"], "accepted a wrong endpoint or rejected the right one")
        with self.subTest(context=label, part="cloud"):
            self.assertEqual(found["cloud"], {"workspace": want["workspace"], "vault": want["vault"]})
            jobs, points = found["backup_urls"]
            self.assertTrue(jobs.startswith("https://management.azure.com" + want["vault"]
                                            + "/backupJobs?api-version=2024-04-01&"), jobs)
            self.assertEqual(points, "https://management.azure.com" + want["vault"]
                             + "/backupFabrics/Azure/protectionContainers/" + want["container"]
                             + "/protectedItems/" + want["item"] + "/recoveryPoints?api-version=2024-04-01")
            self.assertEqual([call[0] for call in found["patch_calls"]], [[want["subscription"]]] * 2)
            for call, table_name in zip(found["patch_calls"], ("patchassessmentresources", "patchinstallationresources")):
                self.assertTrue(call[1].startswith(table_name + " "), call[1])
                self.assertIn(f"startswith '{want['target_ids']['patch']}/'", call[1])
            url, query = found["telemetry"]
            self.assertEqual(url, "https://management.azure.com" + want["workspace"] + "/api/query?api-version=2020-08-01")
            self.assertEqual(query.count(f"tolower(_ResourceId) == '{want['target_ids']['linux']}'"), 3)
            self.assertEqual(found["collect_targets"], [want["target_ids"]["patch"], want["target_ids"]["backup"]])
            self.assertEqual(found["guard"], {"own": "allowed", "other": "blocked", "graph": "allowed"})
        with self.subTest(context=label, part="ssh"):
            self.assertEqual(found["ssh"]["addresses"], want["addresses"])
            self.assertEqual(found["trust"], {"context": 3, "extra_host": "rejected", "missing": "rejected",
                                              "foreign": "rejected"})
        with self.subTest(context=label, part="worker"):
            self.assertEqual(found["worker"], {
                "vault": want["key_vault"], "account": want["account"], "table_endpoint": want["table_endpoint"],
                "queue_endpoint": want["queue_endpoint"], "queue": "agent-requests",
                "evidence_host": want["evidence_host"]})
            self.assertEqual(found["blob_endpoint"], want["blob_endpoint"])
            self.assertEqual(found["bootstrap"], {"vault": want["key_vault"], "known_hosts_stored": True})
            self.assertTrue(found["collect_uri"].startswith(
                f"https://{want['evidence_host']}/evidence/baselines/jobs/"), found["collect_uri"])
            verify = found["verify"]
            self.assertEqual((verify["table"], verify["table_name"], verify["vault"]),
                             (want["table_endpoint"], "Episodes", want["key_vault"]))
            self.assertTrue(verify["evidence_uri"].startswith(
                f"https://{want['evidence_host']}/evidence/baselines/runtime/"), verify["evidence_uri"])
            self.assertEqual(verify["row_uri"], verify["evidence_uri"])
            self.assertEqual(found["process"], {
                "table": want["table_endpoint"], "queue": want["queue_endpoint"], "queue_name": "agent-requests",
                "vault": want["key_vault"], "status": {"status": "processed", "requests": 0, "mode": "Review"}})

    def test_the_alternate_context_drives_every_value(self):
        self.check("alt", ALT_DOCUMENT)

    def test_the_first_phase_core_context_with_null_optional_parts_drives_the_same_values(self):
        self.check("core-null", ALT_DOCUMENT)

    def test_the_first_phase_core_context_with_absent_optional_parts_drives_the_same_values(self):
        self.check("core-absent", ALT_DOCUMENT)

    def test_the_synthetic_context_drives_its_own_values(self):
        self.check("synthetic", SYNTHETIC_DOCUMENT)

    def test_the_synthetic_context_reproduces_the_values_that_were_once_literals(self):
        found = self.results["synthetic"]
        sub = "00000001-000a-4000-a000-000000000001"
        self.assertEqual(found["lifecycle"]["subscription"], sub)
        self.assertEqual(found["lifecycle"]["targets"], [
            [f"/subscriptions/{sub}/resourcegroups/rg-asre-{role}-cus/providers/microsoft.compute"
             f"/virtualmachines/vm-asre-{role}-cus", role] for role in ROLES])
        self.assertEqual(found["lifecycle"]["evidence_host"], "stasre8a327010f9.blob.core.windows.net")
        self.assertEqual(found["cloud"], {
            "workspace": f"/subscriptions/{sub}/resourceGroups/rg-asre-control-cus/providers"
                         "/Microsoft.OperationalInsights/workspaces/law-asre-cus",
            "vault": f"/subscriptions/{sub}/resourceGroups/rg-asre-backup-cus/providers"
                     "/Microsoft.RecoveryServices/vaults/rsv-asre-cus"})
        self.assertEqual(found["ssh"]["addresses"], [["linux", "10.42.0.10"], ["patch", "10.42.0.11"],
                                                     ["backup", "10.42.0.12"]])
        self.assertEqual(found["worker"], {
            "vault": "https://kv-asre-8a327010f9.vault.azure.net/", "account": "stasre8a327010f9",
            "table_endpoint": "https://stasre8a327010f9.table.core.windows.net",
            "queue_endpoint": "https://stasre8a327010f9.queue.core.windows.net", "queue": "agent-requests",
            "evidence_host": "stasre8a327010f9.blob.core.windows.net"})
        self.assertIn("iaasvmcontainer;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus",
                      found["backup_urls"][1])
        self.assertIn("/protectedItems/vm;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus/",
                      found["backup_urls"][1])

    def test_the_two_contexts_really_differ_in_every_derived_value(self):
        alt, synthetic = self.results["alt"], self.results["synthetic"]
        for part, key in (("lifecycle", "subscription"), ("lifecycle", "evidence_host"), ("cloud", "workspace"),
                          ("cloud", "vault"), ("worker", "vault"), ("worker", "account"),
                          ("worker", "table_endpoint"), ("worker", "queue_endpoint")):
            with self.subTest(value=f"{part}.{key}"):
                self.assertNotEqual(alt[part][key], synthetic[part][key])
        self.assertNotEqual(alt["lifecycle"]["targets"], synthetic["lifecycle"]["targets"])

    def test_the_table_endpoint_check_accepts_only_the_context_endpoint(self):
        for label in ("alt", "synthetic", "core-null"):
            table = self.results[label]["table"]
            with self.subTest(context=label):
                self.assertEqual(sorted(name for name, accepted in table.items() if accepted), ["own", "own_slash"])
                self.assertFalse(table["other"])

    def test_no_first_phase_entry_point_imports_a_servicenow_dependent_module(self):
        for label, found in self.results.items():
            with self.subTest(context=label):
                self.assertEqual(found["phase_7_loaded"], [])


class FirstPhaseImportTests(ContextFiles):
    def test_the_first_phase_modules_import_under_the_core_context(self):
        self.assertIn("worker", PHASE_1_MODULES)
        names = ["worker", *[name for name in PHASE_1_MODULES if name != "worker"]]
        for label, path in self.core_files.items():
            result = run_json(matrix_code(names), context_file=path)
            for name in names:
                with self.subTest(context=label, module=f"operations.{name}"):
                    self.assertEqual(result["matrix"][name], "ok")
            with self.subTest(context=label, check="no servicenow-dependent module was imported on the way"):
                self.assertEqual(result["phase_7_loaded"], [])

    def test_the_first_phase_set_covers_the_modules_that_the_deployment_runs(self):
        for name in ("context", "lifecycle", "cloud", "protocol", "ssh", "worker", "patch_provider", "patch_sensor",
                     "trends"):
            self.assertIn(name, PHASE_1_MODULES)
        for name in PHASE_7:
            self.assertNotIn(name, PHASE_1_MODULES)

    def test_every_module_still_imports_under_the_alternate_context(self):
        result = run_json(matrix_code(ALL_MODULES), context_file=ALT)
        for name in ALL_MODULES:
            with self.subTest(module=f"operations.{name}"):
                self.assertEqual(result["matrix"][name], "ok")


class StaticImportTests(unittest.TestCase):
    def parse(self, name):
        return ast.parse((OPERATIONS / f"{name}.py").read_text(encoding="utf-8"))

    def test_no_runtime_core_module_imports_a_servicenow_dependent_module_at_import_time(self):
        forbidden = {f"operations.{name}" for name in PHASE_7}
        for name in OWNED:
            with self.subTest(module=name):
                top_level = {module for scope, module in imports_of(self.parse(name)) if scope is None}
                self.assertEqual(top_level & forbidden, set())
                # protocol reaches the context through lifecycle; the others load it themselves.
                self.assertTrue(top_level & {"operations.context", "operations.lifecycle"}, top_level)

    def test_the_worker_imports_its_two_optional_modules_lazily_inside_their_branches(self):
        tree = self.parse("worker")
        lazy = sorted((scope, module) for scope, module in imports_of(tree)
                      if scope is not None and module.startswith("operations"))
        self.assertEqual(lazy, [("main", "operations.feedback_runtime"), ("process", "operations.patch_sensor")])
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        branch = [node for node in ast.walk(main) if isinstance(node, ast.If) and "feedback" in ast.unparse(node.test)]
        self.assertTrue(any(imports_of(ast.Module(body=node.body, type_ignores=[])) for node in branch))
        process = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "process")
        self.assertEqual(ast.unparse(process.body[0].test), "os.environ.get('ASRE_EXECUTION_MODE') != 'Review'")

    def test_the_worker_modes_and_review_gate_are_unchanged(self):
        source = (OPERATIONS / "worker.py").read_text(encoding="utf-8")
        self.assertIn('choices=("queue", "collect", "bootstrap", "verify", "feedback")', source)
        self.assertEqual(source.count('os.environ.get("ASRE_EXECUTION_MODE") != "Review"'), 2)
        for name in ("ASRE_BOOTSTRAP_CLIENT_ID", "ASRE_RUNNER_CLIENT_ID", "ASRE_COLLECTOR_CLIENT_ID",
                     "ASRE_SSH_KNOWN_HOSTS_B64"):
            self.assertIn(name, source)


class FailClosedTests(unittest.TestCase):
    def test_importing_a_runtime_module_without_a_context_fails_with_a_context_error(self):
        for name in OWNED:
            with self.subTest(module=f"operations.{name}"):
                result = run_with_context(f"import operations.{name}", context_file=None)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("operations.context.ContextError", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_an_unreadable_or_invalid_context_fails_the_same_way(self):
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty.json"
            empty.write_text("{}", encoding="utf-8")
            wrong = Path(directory) / "wrong-suffix.json"
            document = json.loads(ALT.read_text(encoding="utf-8"))
            document["name_suffix"] = "0000000000"
            wrong.write_text(json.dumps(document), encoding="utf-8")
            for label, path in (("missing", Path(directory) / "missing.json"), ("empty object", empty),
                                ("suffix not derived", wrong)):
                for name in ("lifecycle", "worker"):
                    with self.subTest(context=label, module=name):
                        result = run_with_context(f"import operations.{name}", context_file=path)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("operations.context.ContextError", result.stderr)

    def test_a_broken_inline_context_is_not_rescued_by_a_valid_file(self):
        result = run_with_context("import operations.lifecycle", context_file=ALT,
                                  extra_env={"ASRE_CONTEXT": "{broken"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operations.context.ContextError", result.stderr)

    def test_the_inline_context_is_what_the_runtime_jobs_use(self):
        result = run_with_context(
            "import operations.worker as w; print(w.VAULT)", context_file=None,
            extra_env={"ASRE_CONTEXT": json.dumps(core_document(absent=False), separators=(",", ":"))})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), ALT_DOCUMENT["key_vault"]["uri"])


if __name__ == "__main__":
    unittest.main()
