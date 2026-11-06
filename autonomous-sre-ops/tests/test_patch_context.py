"""The patch evidence provider follows the deployment context, and only the context.

Every expected string is written by hand from tests/fixtures/context.alt.json; nothing here calls the
derivation under test to build an expectation. The alt-context checks run in fresh interpreters.
"""

import ast
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from tests.alt_context import ALT, ROOT, SYNTHETIC, leaks_in_module, run_with_context, synthetic_only_values
from tests.test_patch_provider import INSTALLATION

ALT_TARGET = (
    "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-patch-cus"
    "/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus"
)
ALT_FOREIGN_TARGET = (
    "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-linux-cus"
    "/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus"
)
ALT_BLOB = "https://stasree26b179818.blob.core.windows.net"
ALT_STORAGE = ALT_BLOB + "/evidence/"
ALT_COMPUTE_URL = "https://management.azure.com" + ALT_TARGET + "/instanceView?api-version=2024-07-01"
SYNTHETIC_TARGET = (
    "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-patch-cus"
    "/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus"
)
SYNTHETIC_STORAGE_HOST = "stasre8a327010f9.blob.core.windows.net"
NAMESPACE = UUID("08b75cc3-ae84-5d90-877b-25f9b9518106")
DIGEST = "0123456789abcdef" * 4
PROVIDER_MODULES = ("operations.patch_provider", "operations.patch_sensor", "operations.trends")


def child_code(body, **values):
    """A snippet that sees ``VALUES`` (JSON data handed over by the parent) and prints one JSON line."""
    return "import json\nVALUES = json.loads(%r)\n%s" % (json.dumps(values), body)


def run_json(body, context_file=ALT, pythonpath=(), extra_env=None, timeout=120, **values):
    result = run_with_context(child_code(body, **values), context_file=context_file, pythonpath=pythonpath,
                              extra_env=extra_env, timeout=timeout)
    if result.returncode != 0:
        raise AssertionError(f"child failed ({result.returncode}):\n{result.stderr[-2000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


CONSTANTS = """
import socket


def no_network(*args, **kwargs):
    raise AssertionError("network access at import")


socket.getaddrinfo = no_network
socket.create_connection = no_network
socket.socket.connect = no_network
from operations import context, patch_provider as pp
print(json.dumps({
    "target": pp.TARGET, "compute_url": pp.COMPUTE_URL, "storage": pp.STORAGE,
    "archive_path": pp.archive_path(VALUES["digest"]), "query": pp.ALERT_QUERY,
    "namespace": str(pp.PATCH_NAMESPACE), "marker": pp.MARKER, "api_version": pp.API_VERSION,
    "version": pp.VERSION, "ctx_is_loaded_context": pp.CTX is context.load(),
}))
"""

BINDING = """
from operations import patch_provider as pp
from tests.test_patch_provider import INSTALLATION, NOW, snapshot, summary

PROVENANCE = "Patch summary provenance does not match the fixed Compute source."
EVENT_INVALID = "The patch console event is invalid."
good = snapshot()
raw = pp.encode(good)
digest = pp.digest_for(raw)
event = pp.event(good, digest)
sample = pp.sample(good, digest)
accepted = {
    "observation": pp.validate_observation(snapshot()) == good,
    "archived": pp.archived(raw, digest) == good,
    "event": pp.validate_event(dict(event), NOW) == event,
}


def refusal(call):
    try:
        call()
    except pp.ProviderError as error:
        return str(error)
    return None


rejected = {}
for name, other in VALUES["others"].items():
    bad = {**good, "resource_id": other}
    bad_raw = pp.encode(bad)
    rejected[name] = {
        "observation": refusal(lambda: pp.validate_observation(bad)),
        "archived": refusal(lambda: pp.archived(bad_raw, pp.digest_for(bad_raw))),
        "event": refusal(lambda: pp.validate_event({**event, "resource_id": other}, NOW)),
    }
print(json.dumps({
    "snapshot_resource_id": good["resource_id"], "event_resource_id": event["resource_id"],
    "sample_resource_id": sample["resource_id"], "episode": pp.episode(INSTALLATION),
    "accepted": accepted, "rejected": rejected, "provenance": PROVENANCE, "event_invalid": EVENT_INVALID,
}))
"""

TRANSPORT = """
from unittest.mock import patch
from tests.test_patch_provider import Credential, Http, snapshot, summary
from operations import patch_provider as pp

compute = Http()
compute.objects[pp.COMPUTE_URL] = pp.encode({"patchStatus": {"lastPatchInstallationSummary": summary()}})
with patch.object(pp, "build_opener", return_value=compute):
    pp.read_compute(Credential())
store = Http()
with patch.object(pp, "build_opener", return_value=store):
    digest = pp.archive(Credential(), snapshot())
print(json.dumps({
    "compute": [[r.get_method(), r.full_url] for r in compute.requests],
    "archive": [[r.get_method(), r.full_url] for r in store.requests], "digest": digest,
}))
"""

PHASE_ONE = """
from operations import patch_provider, patch_sensor, trends
print(json.dumps({
    "target": patch_provider.TARGET, "storage": patch_provider.STORAGE,
    "servicenow_null": patch_provider.CTX.servicenow is None,
    "endpoint_null": patch_provider.CTX.agent.endpoint is None,
    "sensor_uses_provider": patch_sensor.provider is patch_provider, "trends": trends.__name__,
}))
"""

BUNDLE = """
import sys
import _asre_case.patch_provider as bundled
print(json.dumps({
    "target": bundled.TARGET, "storage": bundled.STORAGE, "file": bundled.__file__,
    "operations_loaded": "operations" in sys.modules,
    "bundle_context_loaded": "_asre_case.context" in sys.modules,
}))
"""

PROBE = '''\
"""Mirrors patch_sensor.run: a spawned child re-imports the provider with nothing cached in the parent."""

import multiprocessing
import sys


def _child(connection):
    from operations import patch_provider

    connection.send(patch_provider.TARGET)
    connection.close()


def probe():
    assert "operations.context" not in sys.modules and "operations.patch_provider" not in sys.modules
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_child, args=(sender,), daemon=True)
    child.start()
    sender.close()
    child.join(45)
    if child.is_alive():
        child.kill()
        child.join()
        raise AssertionError("the spawned child did not finish")
    assert child.exitcode == 0 and receiver.poll(), "the spawned child failed"
    return receiver.recv()
'''


class FixtureConsistencyTests(unittest.TestCase):
    def test_hand_written_expectations_match_the_alt_fixture(self):
        alt = json.loads(ALT.read_text(encoding="utf-8"))
        self.assertEqual(alt["subscription_id"], "00000901-000a-4000-8000-000000000901")
        self.assertEqual(alt["storage"]["blob_endpoint"], ALT_BLOB)
        self.assertEqual(alt["resource_groups"]["patch"], "rg-asre-patch-cus")
        self.assertEqual(alt["targets"]["patch"]["name"], "vm-asre-patch-cus")
        self.assertEqual(alt["targets"]["patch"]["resource_group"], "rg-asre-patch-cus")
        self.assertEqual(alt["targets"]["linux"]["name"], "vm-asre-linux-cus")
        synthetic = json.loads(SYNTHETIC.read_text(encoding="utf-8"))
        self.assertEqual(synthetic["subscription_id"], "00000001-000a-4000-a000-000000000001")
        self.assertEqual(synthetic["storage"]["blob_endpoint"], "https://" + SYNTHETIC_STORAGE_HOST)
        self.assertNotEqual(ALT_TARGET, SYNTHETIC_TARGET)
        self.assertNotEqual(ALT_STORAGE, "https://" + SYNTHETIC_STORAGE_HOST + "/evidence/")
        self.assertEqual(len(DIGEST), 64)
        self.assertRegex(DIGEST, r"\A[0-9a-f]{64}\Z")


class SourceHygieneTests(unittest.TestCase):
    """R2c-1 / R2c-5: one relative context import, no deployment literal, no fallback."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "operations" / "patch_provider.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_only_relative_context_import_is_added_and_nothing_imports_operations_absolutely(self):
        relative = [(node.module, [(a.name, a.asname) for a in node.names]) for node in ast.walk(self.tree)
                    if isinstance(node, ast.ImportFrom) and node.level > 0]
        self.assertEqual(relative, [(None, [("context", "deployment")])])
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(node.level == 0 and (node.module or "").split(".")[0] == "operations")
            elif isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.split(".")[0] == "operations" for alias in node.names))
        self.assertIsNone(re.search(r"^\s*(from|import)\s+operations\b", self.source, re.MULTILINE))

    def test_no_fallback_no_phase_seven_requirement_and_no_hard_coded_identifier(self):
        self.assertNotIn("ContextError", self.source)
        self.assertNotIn("require_servicenow", self.source)
        self.assertNotIn("require_agent_endpoint", self.source)
        self.assertNotIn("os.environ", self.source)
        self.assertNotIn("getenv", self.source)
        self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", self.source))
        guids = set(re.findall(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", self.source))
        self.assertEqual(guids, {str(NAMESPACE)})
        for needle in ("/subscriptions/", "stasre", ".blob.core.windows.net", "rg-asre-", "vm-asre-"):
            self.assertNotIn(needle, self.source)


class AltContextTests(unittest.TestCase):
    def test_no_module_of_the_stream_still_holds_a_synthetic_only_value(self):
        for module in PROVIDER_MODULES:
            with self.subTest(module=module):
                self.assertEqual(leaks_in_module(module), [])

    def test_constants_follow_the_alt_context_and_fixed_values_stay(self):
        found = run_json(CONSTANTS, digest=DIGEST)
        self.assertEqual(found["target"], ALT_TARGET)
        self.assertEqual(found["compute_url"], ALT_COMPUTE_URL)
        self.assertEqual(found["storage"], ALT_STORAGE)
        self.assertEqual(found["archive_path"], "patch-provider/v1/sha256/" + DIGEST + ".json")
        self.assertEqual(found["storage"] + found["archive_path"],
                         "https://stasree26b179818.blob.core.windows.net/evidence/patch-provider/v1/sha256/"
                         + DIGEST + ".json")
        self.assertEqual(found["namespace"], str(NAMESPACE))
        self.assertEqual(found["marker"], "ASRE_PATCH_FAILURE_V1 ")
        self.assertEqual(found["api_version"], "2024-07-01")
        self.assertEqual(found["version"], "1.0")
        self.assertTrue(found["ctx_is_loaded_context"])
        query = found["query"]
        self.assertEqual(query.count(ALT_TARGET), 2)
        lowered = query.lower()
        for value in synthetic_only_values():
            self.assertNotIn(value, lowered)

    def test_alert_query_text_differs_from_the_synthetic_text_only_by_the_target(self):
        alt = run_json(CONSTANTS, digest=DIGEST)["query"]
        synthetic = run_json(CONSTANTS, context_file=SYNTHETIC, digest=DIGEST)
        self.assertEqual(synthetic["target"], SYNTHETIC_TARGET)
        self.assertEqual(synthetic["query"].count(SYNTHETIC_TARGET), 2)
        self.assertEqual(synthetic["query"].replace(SYNTHETIC_TARGET, ALT_TARGET), alt)

    def test_exact_resource_binding_accepts_only_the_alt_target(self):
        found = run_json(BINDING, others={
            "synthetic": SYNTHETIC_TARGET, "lower_cased_alt": ALT_TARGET.lower(),
            "foreign_vm": ALT_FOREIGN_TARGET, "foreign_text": "foreign"})
        self.assertNotEqual(ALT_TARGET.lower(), ALT_TARGET)
        self.assertEqual(found["snapshot_resource_id"], ALT_TARGET)
        self.assertEqual(found["event_resource_id"], ALT_TARGET)
        self.assertEqual(found["sample_resource_id"], ALT_TARGET)
        self.assertEqual(found["accepted"], {"observation": True, "archived": True, "event": True})
        self.assertEqual(set(found["rejected"]), {"synthetic", "lower_cased_alt", "foreign_vm", "foreign_text"})
        for name, refusals in found["rejected"].items():
            with self.subTest(other=name):
                self.assertEqual(refusals, {"observation": found["provenance"], "archived": found["provenance"],
                                            "event": found["event_invalid"]})

    def test_episode_hashes_the_lower_cased_alt_target_by_hand(self):
        found = run_json(BINDING, others={})
        expected = str(uuid5(NAMESPACE, "patch-installation/v2:" + ALT_TARGET.lower() + ":" + INSTALLATION))
        synthetic = str(uuid5(NAMESPACE, "patch-installation/v2:" + SYNTHETIC_TARGET.lower() + ":" + INSTALLATION))
        self.assertEqual(found["episode"], expected)
        self.assertNotEqual(found["episode"], synthetic)

    def test_transport_talks_only_to_the_alt_endpoints(self):
        found = run_json(TRANSPORT)
        digest = found["digest"]
        self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")
        archive_url = ALT_BLOB + "/evidence/patch-provider/v1/sha256/" + digest + ".json"
        self.assertEqual(found["compute"], [["GET", ALT_COMPUTE_URL]])
        self.assertEqual(found["archive"], [["PUT", archive_url], ["GET", archive_url]])
        hosts = {urlsplit(url).hostname for _, url in found["compute"] + found["archive"]}
        self.assertEqual(hosts, {"management.azure.com", "stasree26b179818.blob.core.windows.net"})
        for _, url in found["compute"] + found["archive"]:
            self.assertNotIn(SYNTHETIC_STORAGE_HOST, url)
            self.assertNotIn("00000001-000a-4000-a000-000000000001", url)

    def test_inline_context_environment_is_authoritative_over_the_file(self):
        inline = json.dumps(json.loads(ALT.read_text(encoding="utf-8")))
        found = run_json(PHASE_ONE, context_file=SYNTHETIC, extra_env={"ASRE_CONTEXT": inline})
        self.assertEqual(found["target"], ALT_TARGET)
        self.assertEqual(found["storage"], ALT_STORAGE)


class PhaseOneContextTests(unittest.TestCase):
    """The patch path runs before the agent exists and before ServiceNow is configured."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def write(self, name, mutate):
        document = json.loads(ALT.read_text(encoding="utf-8"))
        mutate(document)
        path = self.directory / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_imports_without_servicenow_and_agent_endpoint(self):
        def servicenow_null(document):
            document["servicenow"] = None

        def servicenow_absent(document):
            del document["servicenow"]

        def endpoint_null(document):
            document["agent"]["endpoint"] = None

        def endpoint_absent(document):
            del document["agent"]["endpoint"]

        def both_null(document):
            servicenow_null(document)
            endpoint_null(document)

        def both_absent(document):
            servicenow_absent(document)
            endpoint_absent(document)

        def core(document):
            both_absent(document)
            del document["operator_object_id"]
            del document["identities"]["bootstrap"]

        cases = (("servicenow-null", servicenow_null, True, False), ("servicenow-absent", servicenow_absent, True, False),
                 ("endpoint-null", endpoint_null, False, True), ("endpoint-absent", endpoint_absent, False, True),
                 ("both-null", both_null, True, True), ("both-absent", both_absent, True, True),
                 ("core", core, True, True))
        for name, mutate, servicenow_null_expected, endpoint_null_expected in cases:
            with self.subTest(variant=name):
                path = self.write(name + ".json", mutate)
                found = run_json(PHASE_ONE, context_file=path)
                self.assertEqual(found["target"], ALT_TARGET)
                self.assertEqual(found["storage"], ALT_STORAGE)
                self.assertEqual(found["servicenow_null"], servicenow_null_expected)
                self.assertEqual(found["endpoint_null"], endpoint_null_expected)
                self.assertTrue(found["sensor_uses_provider"])
                self.assertEqual(found["trends"], "operations.trends")

    def test_no_context_stops_the_provider_and_the_sensor_but_not_the_pure_trends_module(self):
        for module in ("operations.patch_provider", "operations.patch_sensor"):
            with self.subTest(module=module):
                result = run_with_context(f"import {module}\nprint('imported')", context_file=None)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ContextError", result.stderr)
                self.assertIn("not configured", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("/subscriptions/", result.stdout + result.stderr)
        result = run_with_context(child_code(
            "import sys\nimport operations.trends\n"
            "print(json.dumps({'context_loaded': 'operations.context' in sys.modules}))"), context_file=None)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(json.loads(result.stdout), {"context_loaded": False})

    def test_unreadable_or_malformed_context_is_refused_not_defaulted(self):
        broken = self.directory / "broken.json"
        broken.write_text("{", encoding="utf-8")
        for name, context_file in (("malformed", broken), ("missing", self.directory / "absent.json")):
            with self.subTest(context=name):
                result = run_with_context("import operations.patch_provider\nprint('imported')",
                                          context_file=context_file)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ContextError", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_trends_stays_context_free_under_a_full_context_too(self):
        result = run_with_context(child_code(
            "import sys\nimport operations.trends\n"
            "print(json.dumps({'context_loaded': 'operations.context' in sys.modules}))"))
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(json.loads(result.stdout), {"context_loaded": False})


class BundleAndSpawnTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_bundle_style_package_loads_with_a_relative_import_only(self):
        package = self.directory / "_asre_case"
        package.mkdir()
        shutil.copyfile(ROOT / "operations" / "patch_provider.py", package / "patch_provider.py")
        shutil.copyfile(ROOT / "operations" / "context.py", package / "context.py")
        (package / "__init__.py").write_bytes(b"")
        shutil.copyfile(ALT, package / "context.json")
        found = run_json(BUNDLE, context_file=package / "context.json", pythonpath=(self.directory,))
        self.assertEqual(found["target"], ALT_TARGET)
        self.assertEqual(found["storage"], ALT_STORAGE)
        self.assertEqual(Path(found["file"]).resolve(), (package / "patch_provider.py").resolve())
        self.assertFalse(found["operations_loaded"])
        self.assertTrue(found["bundle_context_loaded"])

    def test_spawned_child_reads_the_context_from_the_environment(self):
        (self.directory / "asre_spawn_probe.py").write_text(PROBE, encoding="utf-8")
        found = run_json(
            "import asre_spawn_probe\nprint(json.dumps({'target': asre_spawn_probe.probe()}))",
            pythonpath=(self.directory,), timeout=90)
        self.assertEqual(found["target"], ALT_TARGET)


if __name__ == "__main__":
    unittest.main()
