"""The ServiceNow facade, feedback worker and facade rehearsal follow the deployment context.

Every expected value below is written by hand from tests/fixtures/context.alt.json (or the primary synthetic
fixture for the "must be rejected" side); nothing is derived from the module under test. Scenarios that need
another context run in a fresh interpreter through tests/alt_context.py so the process-wide context cache of
the test process is never disturbed.
"""

import base64
import importlib.util
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from tests.alt_context import ALT, SYNTHETIC, leaks_in_module, run_with_context

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "operations" / "contracts"
LOADER = ROOT / "agent" / "tools" / "servicenow_case.py"
MAP = ROOT / "scripts" / "AgentAssets.map.json"
INDEX = ROOT / "agent" / "knowledge" / "index.json"

SCHEMA_NAMES = (
    "servicenow-case", "servicenow-case-write", "servicenow-note-write",
    "servicenow-feedback-ack", "servicenow-action-audit", "servicenow-adapter-response",
)
ROLES = ("linux", "patch", "backup")

# ---- hand-written alternate-context values (tests/fixtures/context.alt.json) ----
ALT_ORIGIN = "https://alt-instance.service-now.com"
ALT_LABEL = "alt-instance"
ALT_BASE = "/api/x_0000900_asre/case_adapter/v1"
ALT_SCOPE = "x_0000900_asre"
ALT_CLIENT = "0000000000000000000000000000093a"
ALT_GROUP = "0000000000000000000000000000097a"
ALT_CIS = {
    "linux": "0000000000000000000000000000914a",
    "patch": "0000000000000000000000000000901a",
    "backup": "0000000000000000000000000000912a",
}
ALT_RESOURCES = {
    "linux": "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus",
    "patch": "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus",
    "backup": "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-backup-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-backup-cus",
}
ALT_TARGETS = [
    ["/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-linux-cus/providers/microsoft.compute/virtualmachines/vm-asre-linux-cus", "linux"],
    ["/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-patch-cus/providers/microsoft.compute/virtualmachines/vm-asre-patch-cus", "patch"],
    ["/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-backup-cus/providers/microsoft.compute/virtualmachines/vm-asre-backup-cus", "backup"],
]
ALT_AGENT_ID = "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"
ALT_TABLE_ENDPOINT = "https://stasree26b179818.table.core.windows.net"
ALT_RUNNER_CLIENT = "00000928-000a-4000-8000-000000000928"

# ---- hand-written primary synthetic values (tests/fixtures/context.synthetic.json) ----
SYN_ORIGIN = "https://example-instance.service-now.com"
SYN_LABEL = "example-instance"
SYN_SCOPE = "x_0000000_asre"
SYN_GROUP = "0000000000000000000000000000007a"
SYN_CIS = {
    "linux": "0000000000000000000000000000014a",
    "patch": "0000000000000000000000000000001a",
    "backup": "0000000000000000000000000000012a",
}
SYN_SUBSCRIPTION = "00000001-000a-4000-a000-000000000001"
SYN_RESOURCES = {
    "linux": "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus",
    "patch": "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus",
    "backup": "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-backup-cus",
}
SYN_AGENT_ID = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"

MISMATCH = "The ServiceNow contract schemas do not match the deployment context."
GENERIC_LOAD_FAILURE = "The generated case-tool package could not be loaded."
BLOCKED = {"status": "blocked", "message": "The reviewed fixed case-tool runtime package is unavailable."}

PARAMS = {
    "alt": ALT_RESOURCES, "syn": SYN_RESOURCES, "alt_origin": ALT_ORIGIN, "syn_origin": SYN_ORIGIN,
    "alt_label": ALT_LABEL, "syn_label": SYN_LABEL, "alt_scope": ALT_SCOPE, "syn_scope": SYN_SCOPE,
    "alt_group": ALT_GROUP, "syn_group": SYN_GROUP, "alt_cis": ALT_CIS, "syn_cis": SYN_CIS,
    "alt_agent": ALT_AGENT_ID, "syn_agent": SYN_AGENT_ID, "runner_client": ALT_RUNNER_CLIENT,
}

# Shared by the fresh-interpreter scenarios: valid facade bodies for any resource/group/CI/origin.
PRELUDE = '''
import copy, json, os
from unittest import mock

from operations import servicenow as sn

EP = "00000025-000a-4000-8000-000000000025"
REF = {"sys_id": "0000000000000000000000000000910a", "number": "INC0900001", "record_type": "incident"}


def evidence(resource):
    return {
        "contract_version": "1.0", "episode_id": EP, "source_event_ids": ["unit-fixture-only"],
        "resource_id": resource, "mode": "reactive", "signal_family": "filesystem",
        "detected_at": "2026-10-16T02:00:00Z", "summary": "Unit fixture, not a real alert",
        "evidence": [{"source": "ServiceNow", "observed_at": "2026-10-16T02:00:00Z",
                      "reference": "fixture", "finding": "Unit fixture only"}],
        "diagnosis": {"finding": "Unit test", "confidence": 0.5, "gaps": ["No live evidence"]},
        "actions": [], "verification": {"status": "not_started", "recovery_owner": "none"},
    }


def case_body(resource, group, ci, origin):
    return {
        "episode_id": EP, "case_reference": dict(REF), "resource_id": resource, "mode": "reactive",
        "signal_family": "filesystem", "state": {"value": "1", "label": "New"},
        "assignment_group": {"sys_id": group, "label": "Unit group"}, "ci": {"sys_id": ci, "label": "Unit CI"},
        "priority": {"value": "4", "label": "Low"}, "short_description": "Unit fixture",
        "description": "Synthetic unit fixture, not a real incident.", "revision": "1",
        "updated_at": "2026-10-16 02:00:00", "investigation_reference": None,
        "evidence": evidence(resource), "instance_url": origin + "/incident.do?sys_id=" + REF["sys_id"],
    }


def health_body(instance, scope):
    return {"version": "0.1.0", "instance": instance, "scope": scope, "execution_mode": "Review",
            "azure_mutations_exposed": False, "human_approval_enabled": False, "trusted_verifier_bound": False,
            "feedback": "fixture", "agent_continuation": "fixture"}


def client(body):
    def transport(method, path, payload, headers, **options):
        return sn.HttpResponse(200, json.dumps({"result": body}).encode())
    return sn.CaseClient(lambda: "unit-token", transport)


def attempt(function):
    try:
        function()
    except Exception as error:
        return type(error).__name__
    return "accepted"


def verdict(contracts, name, value, definition=None):
    try:
        contracts.validate(name, value, definition=definition)
    except sn.ServiceNowError:
        return "rejected"
    return "accepted"
'''

ALT_FUNCTIONAL = '''
from operations import facade_demo as fd
from operations import feedback as fb
from operations import feedback_runtime as fr
from operations.feedback import FeedbackError

out = {}
out["servicenow"] = {
    "ORIGIN": sn.ORIGIN, "BASE": sn.BASE, "CLIENT_ID": sn.CLIENT_ID, "GROUP": sn.GROUP, "SCOPE": sn.SCOPE,
    "CIS": list(sn.CIS.items()), "CIS_type": type(sn.CIS).__name__,
    "TARGETS": list(sn.TARGETS.items()), "TARGETS_type": type(sn.TARGETS).__name__,
}
out["feedback_origin"] = fb.ORIGIN
out["agent_id"] = fr.AGENT_ID
out["facade"] = {"RESOURCE": fd.RESOURCE, "CLIENT_ID": fd.CLIENT_ID}

# run() builds the table client from the context's table endpoint; the worker needs no agent endpoint.
class Stop(Exception):
    pass


captured = {}


def table_client(*arguments, **keywords):
    captured["arguments"] = list(arguments)
    return object()


environment = {"ASRE_EXECUTION_MODE": "Review", "ASRE_SERVICENOW_FEEDBACK_ENABLED": "true",
               "ASRE_RUNNER_CLIENT_ID": P["runner_client"]}
with mock.patch.dict(os.environ, environment), \\
        mock.patch("azure.data.tables.TableClient", side_effect=table_client), \\
        mock.patch("azure.identity.ManagedIdentityCredential"), \\
        mock.patch.object(fr, "Native"), mock.patch.object(fr, "TableStore"), \\
        mock.patch.object(fr, "CaseClient", side_effect=Stop):
    try:
        fr.run()
        out["run"] = "returned"
    except Stop:
        out["run"] = "stopped"
out["table"] = captured.get("arguments", [])[:2]


def agent_document(agent_id, endpoint, **properties):
    document = {"id": agent_id, "properties": {
        "actionConfiguration": {"mode": "Review", "accessLevel": "Low"},
        "powerState": "Running", "provisioningState": "Succeeded", "agentEndpoint": endpoint}}
    document["properties"].update(properties)
    return document


def resolve(document):
    native = fr.Native(credential=object(), opener=object())
    urls = []

    def get(url, scope):
        urls.append([url, scope])
        return copy.deepcopy(document)

    native._get = get
    try:
        native.resolve()
    except FeedbackError:
        return {"result": "rejected", "urls": urls}
    return {"result": "accepted", "endpoint": native.endpoint, "urls": urls}


host = "asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai"
resolutions = {
    "alt_agent_alt_labels": resolve(agent_document(P["alt_agent"], "https://" + host)),
    "alt_agent_synthetic_labels": resolve(agent_document(
        P["alt_agent"], "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai")),
    "alt_agent_trailing_slash": resolve(agent_document(P["alt_agent"], "https://" + host + "/")),
    "alt_agent_lower_case_id": resolve(agent_document(P["alt_agent"].lower(), "https://" + host)),
    "synthetic_agent_id": resolve(agent_document(P["syn_agent"], "https://" + host)),
    "other_agent_name": resolve(agent_document(
        P["alt_agent"], "https://other-agent--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai")),
    "prefixed_agent_name": resolve(agent_document(
        P["alt_agent"], "https://evil-asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai")),
    "other_region": resolve(agent_document(
        P["alt_agent"], "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.eastus.azuresre.ai")),
    "other_domain": resolve(agent_document(
        P["alt_agent"], "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.dev")),
    "suffixed_domain": resolve(agent_document(P["alt_agent"], "https://" + host + ".example.invalid")),
    "plain_http": resolve(agent_document(P["alt_agent"], "http://" + host)),
    "explicit_port": resolve(agent_document(P["alt_agent"], "https://" + host + ":8443")),
    "with_path": resolve(agent_document(P["alt_agent"], "https://" + host + "/x")),
    "not_running": resolve(agent_document(P["alt_agent"], "https://" + host, powerState="Stopped")),
}
out["resolutions"] = {name: value["result"] for name, value in resolutions.items()}
out["resolved_endpoints"] = {name: value["endpoint"] for name, value in resolutions.items()
                             if value["result"] == "accepted"}
out["resolve_urls"] = resolutions["alt_agent_alt_labels"]["urls"]

# Health: the facade identity is bound to the context's instance label and application scope.
def health(instance, scope):
    return attempt(lambda: client(health_body(instance, scope)).health())


out["health"] = {
    "alt": health(P["alt_label"], P["alt_scope"]),
    "synthetic": health(P["syn_label"], P["syn_scope"]),
    "alt_instance_synthetic_scope": health(P["alt_label"], P["syn_scope"]),
    "synthetic_instance_alt_scope": health(P["syn_label"], P["alt_scope"]),
}

# Owned-case context: group, CI, origin-based URL and resource are all bound to the context.
accepted = {}
for role in ("linux", "patch", "backup"):
    body = case_body(P["alt"][role], P["alt_group"], P["alt_cis"][role], P["alt_origin"])
    accepted[role] = client(body).read_case(EP, P["alt"][role]).resource_id
out["accepted_cases"] = accepted


def read(body, resource):
    return attempt(lambda: client(body).read_case(EP, resource))


linux, group, ci, origin = P["alt"]["linux"], P["alt_group"], P["alt_cis"]["linux"], P["alt_origin"]
out["rejected_cases"] = {
    "synthetic_group": read(case_body(linux, P["syn_group"], ci, origin), linux),
    "synthetic_ci": read(case_body(linux, group, P["syn_cis"]["linux"], origin), linux),
    "other_role_ci": read(case_body(linux, group, P["alt_cis"]["patch"], origin), linux),
    "synthetic_origin": read(case_body(linux, group, ci, P["syn_origin"]), linux),
    "synthetic_resource_in_body": read(case_body(P["syn"]["linux"], group, P["syn_cis"]["linux"], origin), linux),
    "synthetic_resource_requested": read(case_body(linux, group, ci, origin), P["syn"]["linux"]),
}
print(json.dumps(out))
'''

CONTRACT_BINDING = '''
contracts = sn.Contracts()
adapter = contracts.validators["urn:asre-demo:servicenow-adapter-response:1"].schema
case = contracts.validators["urn:asre-demo:servicenow-case:1"].schema
out = {
    "health": {"instance": adapter["$defs"]["health"]["properties"]["instance"],
               "scope": adapter["$defs"]["health"]["properties"]["scope"]},
    "resource_id": case["properties"]["resource_id"],
}
roles = ("linux", "patch", "backup")
out["case_evidence"] = {role: [verdict(contracts, "servicenow-case", evidence(P["alt"][role])),
                               verdict(contracts, "servicenow-case", evidence(P["syn"][role]))] for role in roles}
out["context_definition"] = {
    role: [verdict(contracts, "servicenow-adapter-response",
                   case_body(P["alt"][role], P["alt_group"], P["alt_cis"][role], P["alt_origin"]), "context"),
           verdict(contracts, "servicenow-adapter-response",
                   case_body(P["syn"][role], P["alt_group"], P["alt_cis"][role], P["alt_origin"]), "context")]
    for role in roles}
out["health_definition"] = [
    verdict(contracts, "servicenow-adapter-response", health_body(P["alt_label"], P["alt_scope"]), "health"),
    verdict(contracts, "servicenow-adapter-response", health_body(P["syn_label"], P["syn_scope"]), "health"),
    verdict(contracts, "servicenow-adapter-response", health_body(P["alt_label"], P["syn_scope"]), "health"),
    verdict(contracts, "servicenow-adapter-response", health_body(P["syn_label"], P["alt_scope"]), "health"),
]
# Binding is in memory only: the shipped schema files keep their placeholders.
shipped = (os.path.join(os.path.dirname(sn.__file__), "contracts"))
text = open(os.path.join(shipped, "servicenow-adapter-response.schema.json"), encoding="utf-8").read()
out["files_keep_placeholders"] = P["syn_label"] in text and P["alt_label"] not in text
print(json.dumps(out))
'''

PROBES = '''
import importlib

out = {}
for name in P["probes"]:
    try:
        module = importlib.import_module(name + ".servicenow")
        contracts = module.Contracts()
        adapter = contracts.validators["urn:asre-demo:servicenow-adapter-response:1"].schema
        case = contracts.validators["urn:asre-demo:servicenow-case:1"].schema
        out[name] = {"outcome": "ok",
                     "instance": adapter["$defs"]["health"]["properties"]["instance"],
                     "scope": adapter["$defs"]["health"]["properties"]["scope"],
                     "resource_id": case["properties"]["resource_id"]}
    except Exception as error:
        out[name] = {"outcome": type(error).__name__, "message": str(error)}
print(json.dumps(out))
'''


def run_child(body, *, prelude="", params=None, **options):
    code = "import json\nP = json.loads(%r)\n" % json.dumps(params or PARAMS) + prelude + textwrap.dedent(body)
    result = run_with_context(code, **options)
    if result.returncode != 0:
        raise AssertionError("The child interpreter failed:\n" + result.stderr[-3000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def alt_document():
    return json.loads(ALT.read_text(encoding="utf-8"))


class LeakTests(unittest.TestCase):
    def test_no_primary_context_value_survives_in_the_phase_7_modules(self):
        for module in ("servicenow", "feedback", "feedback_runtime", "facade_demo"):
            with self.subTest(module=module):
                self.assertEqual(leaks_in_module("operations." + module), [])


class SourceShapeTests(unittest.TestCase):
    def test_bundled_module_uses_relative_context_import_and_no_repository_import(self):
        source = (ROOT / "operations" / "servicenow.py").read_text(encoding="utf-8")
        self.assertIn("from . import context as deployment\n", source)
        self.assertNotRegex(source, r"(?m)^\s*(from|import)\s+operations\b")
        self.assertNotIn("from operations.lifecycle", source)

    def test_primary_context_gives_the_historic_values_as_plain_dicts_in_order(self):
        import inspect

        from operations import servicenow as sn
        self.assertIs(type(sn.CIS), dict)
        self.assertIs(type(sn.TARGETS), dict)
        self.assertEqual(list(sn.CIS.items()), list(SYN_CIS.items()))
        self.assertEqual(list(sn.TARGETS.values()), ["linux", "patch", "backup"])
        self.assertEqual(list(sn.TARGETS), [value.lower() for value in SYN_RESOURCES.values()])
        self.assertEqual((sn.ORIGIN, sn.GROUP), (SYN_ORIGIN, SYN_GROUP))
        self.assertEqual(list(inspect.signature(sn.Contracts).parameters), [])

    def test_primary_context_leaves_every_bound_schema_equal_to_its_file(self):
        from operations import servicenow as sn
        contracts = sn.Contracts()
        for name in SCHEMA_NAMES:
            with self.subTest(schema=name):
                loaded = json.loads((CONTRACTS / (name + ".schema.json")).read_text(encoding="utf-8"))
                bound = contracts.validators["urn:asre-demo:" + name + ":1"].schema
                self.assertEqual(json.dumps(bound), json.dumps(loaded))

    def test_each_contracts_instance_owns_its_date_time_checker(self):
        from operations import servicenow as sn
        first, second = sn.Contracts(), sn.Contracts()
        self.assertIsNot(first.validators, second.validators)
        self.assertEqual(sn.Contracts.__module__, "operations.servicenow")


class AltContextTests(unittest.TestCase):
    def test_alt_context_drives_every_value_and_the_exact_bindings(self):
        out = run_child(ALT_FUNCTIONAL, prelude=PRELUDE)
        self.assertEqual(out["servicenow"], {
            "ORIGIN": ALT_ORIGIN, "BASE": ALT_BASE, "CLIENT_ID": ALT_CLIENT, "GROUP": ALT_GROUP,
            "SCOPE": "asre_case_adapter_v1",
            "CIS": [[role, ALT_CIS[role]] for role in ROLES], "CIS_type": "dict",
            "TARGETS": ALT_TARGETS, "TARGETS_type": "dict",
        })
        self.assertEqual(out["feedback_origin"], ALT_ORIGIN)
        self.assertEqual(out["agent_id"], ALT_AGENT_ID)
        self.assertEqual(out["facade"], {"RESOURCE": ALT_RESOURCES["linux"], "CLIENT_ID": ALT_CLIENT})
        self.assertEqual(out["run"], "stopped")
        self.assertEqual(out["table"], [ALT_TABLE_ENDPOINT, "Episodes"])

        host = "asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai"
        self.assertEqual(out["resolutions"], {
            "alt_agent_alt_labels": "accepted", "alt_agent_synthetic_labels": "accepted",
            "alt_agent_trailing_slash": "accepted", "alt_agent_lower_case_id": "accepted",
            "synthetic_agent_id": "rejected", "other_agent_name": "rejected", "prefixed_agent_name": "rejected",
            "other_region": "rejected", "other_domain": "rejected", "suffixed_domain": "rejected",
            "plain_http": "rejected", "explicit_port": "rejected", "with_path": "rejected", "not_running": "rejected",
        })
        self.assertEqual(out["resolved_endpoints"], {
            "alt_agent_alt_labels": "https://" + host,
            "alt_agent_synthetic_labels": "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai",
            "alt_agent_trailing_slash": "https://" + host,
            "alt_agent_lower_case_id": "https://" + host,
        })
        self.assertEqual(out["resolve_urls"], [[
            "https://management.azure.com" + ALT_AGENT_ID + "?api-version=2026-01-01",
            "https://management.azure.com/.default"]])

        self.assertEqual(out["health"], {
            "alt": "accepted", "synthetic": "ServiceNowError",
            "alt_instance_synthetic_scope": "ServiceNowError", "synthetic_instance_alt_scope": "ServiceNowError",
        })
        self.assertEqual(out["accepted_cases"], {role: ALT_RESOURCES[role].lower() for role in ROLES})
        self.assertEqual(out["rejected_cases"], {
            "synthetic_group": "ServiceNowError", "synthetic_ci": "ServiceNowError",
            "other_role_ci": "ServiceNowError", "synthetic_origin": "ServiceNowError",
            "synthetic_resource_in_body": "ServiceNowError", "synthetic_resource_requested": "PermissionError",
        })

    def test_contracts_are_bound_to_the_context_and_the_files_are_not_touched(self):
        out = run_child(CONTRACT_BINDING, prelude=PRELUDE)
        self.assertEqual(out["health"], {"instance": {"const": ALT_LABEL}, "scope": {"const": ALT_SCOPE}})
        self.assertEqual(out["resource_id"], {"enum": [ALT_RESOURCES[role] for role in ROLES]})
        self.assertEqual(out["case_evidence"], {role: ["accepted", "rejected"] for role in ROLES})
        # The adapter-response context definition $refs the case schema's resource_id.
        self.assertEqual(out["context_definition"], {role: ["accepted", "rejected"] for role in ROLES})
        self.assertEqual(out["health_definition"], ["accepted", "rejected", "rejected", "rejected"])
        self.assertTrue(out["files_keep_placeholders"])


class ContractStructureTests(unittest.TestCase):
    """A schema that does not have the exact expected shape must fail closed, never be skipped or widened."""

    @staticmethod
    def contracts():
        return {name: json.loads((CONTRACTS / (name + ".schema.json")).read_text(encoding="utf-8"))
                for name in SCHEMA_NAMES}

    @classmethod
    def mutations(cls):
        def health(schemas):
            return schemas["servicenow-adapter-response"]["$defs"]["health"]

        def case(schemas):
            return schemas["servicenow-case"]["properties"]

        def instance_extra_key(schemas):
            health(schemas)["properties"]["instance"]["type"] = "string"

        def instance_not_const(schemas):
            health(schemas)["properties"]["instance"] = {"enum": [SYN_LABEL]}

        def scope_extra_key(schemas):
            health(schemas)["properties"]["scope"]["type"] = "string"

        def scope_missing(schemas):
            del health(schemas)["properties"]["scope"]

        def health_missing(schemas):
            del schemas["servicenow-adapter-response"]["$defs"]["health"]

        def health_not_an_object(schemas):
            schemas["servicenow-adapter-response"]["$defs"]["health"] = []

        def enum_two(schemas):
            del case(schemas)["resource_id"]["enum"][2]

        def enum_four(schemas):
            case(schemas)["resource_id"]["enum"].append(SYN_RESOURCES["linux"] + "-extra")

        def enum_not_strings(schemas):
            case(schemas)["resource_id"]["enum"][1] = 7

        def resource_extra_key(schemas):
            case(schemas)["resource_id"]["type"] = "string"

        def resource_missing(schemas):
            del case(schemas)["resource_id"]

        return {
            "instance_extra_key": instance_extra_key, "instance_not_const": instance_not_const,
            "scope_extra_key": scope_extra_key, "scope_missing": scope_missing,
            "health_missing": health_missing, "health_not_an_object": health_not_an_object,
            "enum_two": enum_two, "enum_four": enum_four, "enum_not_strings": enum_not_strings,
            "resource_extra_key": resource_extra_key, "resource_missing": resource_missing,
        }

    @staticmethod
    def write_package(root, name, texts):
        package = Path(root) / name
        (package / "contracts").mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"")
        for module in ("context.py", "servicenow.py"):
            (package / module).write_bytes((ROOT / "operations" / module).read_bytes())
        for schema, text in texts.items():
            (package / "contracts" / (schema + ".schema.json")).write_text(text, encoding="utf-8", newline="\n")

    def test_unmutated_rendered_and_mutated_schema_copies(self):
        with tempfile.TemporaryDirectory() as root:
            texts = {name: (CONTRACTS / (name + ".schema.json")).read_text(encoding="utf-8")
                     for name in SCHEMA_NAMES}
            self.write_package(root, "_probe_baseline", texts)
            # What the render step produces: the same files with the target context's values.
            rendered = {
                name: text.replace(SYN_LABEL, ALT_LABEL).replace(SYN_SCOPE, ALT_SCOPE)
                .replace(SYN_SUBSCRIPTION, "00000901-000a-4000-8000-000000000901")
                for name, text in texts.items()}
            self.write_package(root, "_probe_rendered", rendered)
            names = ["_probe_baseline", "_probe_rendered"]
            for label, mutate in self.mutations().items():
                schemas = self.contracts()
                mutate(schemas)
                package = "_probe_" + label
                self.write_package(root, package, {name: json.dumps(value, indent=2) + "\n"
                                                   for name, value in schemas.items()})
                names.append(package)
            out = run_child(PROBES, params={"probes": names}, pythonpath=(root,))

        bound = {"outcome": "ok", "instance": {"const": ALT_LABEL}, "scope": {"const": ALT_SCOPE},
                 "resource_id": {"enum": [ALT_RESOURCES[role] for role in ROLES]}}
        self.assertEqual(out["_probe_baseline"], bound)
        self.assertEqual(out["_probe_rendered"], bound)
        for label in self.mutations():
            with self.subTest(mutation=label):
                self.assertEqual(out["_probe_" + label], {"outcome": "ServiceNowError", "message": MISMATCH})


class PhaseOneContextTests(unittest.TestCase):
    """ServiceNow modules refuse to import without the servicenow section; a Phase 1 context has none and must not need them."""

    MODULES = ("operations.servicenow", "operations.facade_demo", "operations.feedback", "operations.feedback_runtime")
    IMPORT_ALL = '''
        import importlib
        from operations import context

        out = {}
        for name in %r:
            try:
                importlib.import_module(name)
                out[name] = "imported"
            except context.ContextError:
                out[name] = "ContextError"
            except Exception as error:
                out[name] = type(error).__name__
        print(json.dumps(out))
    '''

    @staticmethod
    def variant(**changes):
        document = alt_document()
        for key, value in changes.items():
            if key == "endpoint":
                document["agent"]["endpoint"] = value
            elif value == "absent":
                del document[key]
            else:
                document[key] = value
        return document

    def run_variant(self, directory, name, document, modules, **options):
        path = Path(directory) / (name + ".json")
        path.write_text(json.dumps(document), encoding="utf-8")
        return run_child(textwrap.dedent(self.IMPORT_ALL) % (list(modules),), context_file=path, **options)

    def test_modules_raise_context_error_without_servicenow(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, document in (
                ("servicenow_null", self.variant(servicenow=None)),
                ("servicenow_absent", self.variant(servicenow="absent")),
                ("both_null", self.variant(servicenow=None, endpoint=None)),
            ):
                with self.subTest(context=name):
                    out = self.run_variant(directory, name, document, self.MODULES)
                    self.assertEqual(out, {module: "ContextError" for module in self.MODULES})

    def test_servicenow_and_facade_import_when_only_the_agent_endpoint_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            out = self.run_variant(directory, "endpoint_null", self.variant(endpoint=None),
                                   ("operations.servicenow", "operations.facade_demo"))
        self.assertEqual(out, {"operations.servicenow": "imported", "operations.facade_demo": "imported"})

    def test_servicenow_without_any_context_raises_context_error_not_import_error(self):
        out = run_child(textwrap.dedent(self.IMPORT_ALL) % (["operations.servicenow"],), context_file=None)
        self.assertEqual(out, {"operations.servicenow": "ContextError"})


class BundleStyleImportTests(unittest.TestCase):
    def test_servicenow_loads_as_a_bundled_package_with_only_the_bundled_context(self):
        with tempfile.TemporaryDirectory() as root:
            package = Path(root) / "_asre_case"
            (package / "contracts").mkdir(parents=True)
            (package / "__init__.py").write_bytes(b"")
            for module in ("context.py", "servicenow.py"):
                (package / module).write_bytes((ROOT / "operations" / module).read_bytes())
            (package / "context.json").write_bytes(ALT.read_bytes())
            for name in SCHEMA_NAMES:
                (package / "contracts" / (name + ".schema.json")).write_bytes(
                    (CONTRACTS / (name + ".schema.json")).read_bytes())
            out = run_child('''
                import sys
                import importlib

                module = importlib.import_module("_asre_case.servicenow")
                contracts = module.Contracts()
                adapter = contracts.validators["urn:asre-demo:servicenow-adapter-response:1"].schema
                print(json.dumps({
                    "operations_loaded": any(n == "operations" or n.startswith("operations.") for n in sys.modules),
                    "client_module": module.CaseClient.__module__,
                    "origin": module.ORIGIN, "base": module.BASE,
                    "instance": adapter["$defs"]["health"]["properties"]["instance"],
                    "targets": list(module.TARGETS.values()),
                }))
            ''', context_file=None, pythonpath=(root,),
                extra_env={"ASRE_CONTEXT_FILE": str(package / "context.json")})
        self.assertEqual(out, {
            "operations_loaded": False, "client_module": "_asre_case.servicenow",
            "origin": ALT_ORIGIN, "base": ALT_BASE, "instance": {"const": ALT_LABEL},
            "targets": ["linux", "patch", "backup"],
        })


STUB_AGENT_CASE = '''"""Test stub for the bundled case engine: loads the bundled context like the real one."""
from . import context, servicenow

CTX = context.load()
RESOURCE = CTX.targets["linux"].resource_id
ORIGIN = servicenow.ORIGIN
SERVICENOW = servicenow
FROZEN = []


def freeze_runtime_configuration(value):
    if value == "reject":
        raise ValueError("The stub rejects this configuration.")
    FROZEN.append(value)


def main(*arguments):
    return {"status": "stub", "arguments": list(arguments)}
'''


class CaseToolLoaderTests(unittest.TestCase):
    AMBIENT_FILE = str(SYNTHETIC)

    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ))
        self.ambient_json = SYNTHETIC.read_text(encoding="utf-8")
        self.loaders = []
        self.addCleanup(self.clean_up)

    def clean_up(self):
        for loader in self.loaders:
            directory = getattr(loader, "_case_directory", None)
            if directory is not None:
                directory.cleanup()
        for name in tuple(sys.modules):
            if name == "_asre_case" or name.startswith("_asre_case."):
                sys.modules.pop(name, None)

    @staticmethod
    def members(**overrides):
        names = {
            "_asre_case/__init__.py": b"",
            "_asre_case/agent_case.py": STUB_AGENT_CASE.encode(),
            "_asre_case/patch_provider.py": b'"""Test stub."""\n',
            "_asre_case/servicenow.py": (ROOT / "operations" / "servicenow.py").read_bytes(),
            "_asre_case/context.py": (ROOT / "operations" / "context.py").read_bytes(),
            "_asre_case/context.json": ALT.read_bytes(),
            "_asre_case/commissioning.json": b'{"version": "1", "minimum_not_before": "2026-01-01T00:00:00Z"}\n',
            "_asre_case/runtime-configuration.json": b"null\n",
        }
        for name in SCHEMA_NAMES:
            names["_asre_case/contracts/" + name + ".schema.json"] = (CONTRACTS / (name + ".schema.json")).read_bytes()
        names.update(overrides)
        return names

    def loader_for(self, members, duplicates=()):
        buffer = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            warnings.simplefilter("ignore")
            for name, data in members.items():
                if data is not None:
                    archive.writestr(name, data)
            for name in duplicates:
                archive.writestr(name, members[name])
        spec = importlib.util.spec_from_file_location("servicenow_case_under_test", LOADER)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        loader.__CASE_TOOL_BUNDLE__ = base64.b64encode(buffer.getvalue()).decode("ascii")
        self.loaders.append(loader)
        return loader

    def set_ambient(self):
        os.environ["ASRE_CONTEXT"] = self.ambient_json
        os.environ["ASRE_CONTEXT_FILE"] = self.AMBIENT_FILE
        return dict(os.environ)

    def test_template_keeps_its_marker_and_never_loads_the_context_at_call_time(self):
        source = LOADER.read_text(encoding="utf-8")
        self.assertEqual(source.splitlines().count('__CASE_TOOL_BUNDLE__ = ""'), 1)
        self.assertNotRegex(source, r"\.load\(")
        self.assertEqual(LOADER.read_bytes().count(b"\r"), 0)

    def test_bundled_context_wins_over_the_ambient_environment_and_both_variables_are_restored(self):
        loader = self.loader_for(self.members())
        before = self.set_ambient()
        runtime = loader._runtime()
        self.assertEqual(runtime.__name__, "_asre_case.agent_case")
        self.assertEqual(runtime.RESOURCE, ALT_RESOURCES["linux"])
        self.assertEqual(runtime.ORIGIN, ALT_ORIGIN)
        self.assertEqual(runtime.SERVICENOW.CaseClient.__module__, "_asre_case.servicenow")
        self.assertEqual(runtime.FROZEN, [None])
        self.assertEqual(dict(os.environ), before)
        self.assertIs(loader._runtime(), runtime)
        self.assertEqual(loader.main("create", "i", "a", "{}", "n", "r", "disk"),
                         {"status": "stub", "arguments": ["create", "i", "a", "{}", "n", "r", "disk"]})

    def test_absent_variables_stay_absent_after_a_successful_load(self):
        os.environ.pop("ASRE_CONTEXT", None)
        os.environ.pop("ASRE_CONTEXT_FILE", None)
        before = dict(os.environ)
        runtime = self.loader_for(self.members())._runtime()
        self.assertEqual(runtime.RESOURCE, ALT_RESOURCES["linux"])
        self.assertEqual(dict(os.environ), before)
        self.assertNotIn("ASRE_CONTEXT", os.environ)
        self.assertNotIn("ASRE_CONTEXT_FILE", os.environ)

    def test_only_one_ambient_variable_present_is_restored_exactly(self):
        os.environ.pop("ASRE_CONTEXT", None)
        os.environ["ASRE_CONTEXT_FILE"] = self.AMBIENT_FILE
        before = dict(os.environ)
        self.loader_for(self.members())._runtime()
        self.assertEqual(dict(os.environ), before)

    def test_runtime_configuration_is_passed_through_including_null(self):
        for text, expected in ((b"null\n", None), (b'{"mode": "Review"}\n', {"mode": "Review"})):
            with self.subTest(configuration=text):
                runtime = self.loader_for(self.members(**{"_asre_case/runtime-configuration.json": text}))._runtime()
                self.assertEqual(runtime.FROZEN, [expected])
                self.clean_up()

    def test_invalid_packages_fail_generically_restore_the_environment_and_leave_no_modules(self):
        context_text = ALT.read_text(encoding="utf-8")
        invalid_context = json.loads(context_text)
        invalid_context["location"] = "elsewhere"
        cases = {
            "missing_member": self.loader_for(self.members(**{"_asre_case/patch_provider.py": None})),
            "extra_member": self.loader_for(self.members(**{"_asre_case/extra.py": b""})),
            "duplicate_member": self.loader_for(self.members(), duplicates=("_asre_case/agent_case.py",)),
            "missing_context_json": self.loader_for(self.members(**{"_asre_case/context.json": None})),
            "missing_context_module": self.loader_for(self.members(**{"_asre_case/context.py": None})),
            "malformed_context_json": self.loader_for(self.members(**{"_asre_case/context.json": b"{not json"})),
            "invalid_context_json": self.loader_for(self.members(
                **{"_asre_case/context.json": json.dumps(invalid_context).encode()})),
            "rejected_configuration": self.loader_for(self.members(
                **{"_asre_case/runtime-configuration.json": b'"reject"\n'})),
        }
        for label, loader in cases.items():
            with self.subTest(package=label):
                before = self.set_ambient()
                with self.assertRaises(RuntimeError) as caught:
                    loader._runtime()
                self.assertEqual(str(caught.exception), GENERIC_LOAD_FAILURE)
                self.assertEqual(dict(os.environ), before)
                self.assertFalse([name for name in sys.modules if name == "_asre_case" or name.startswith("_asre_case.")])
                self.assertIsNone(loader._case_runtime)
                self.assertEqual(loader.main("create"), BLOCKED)

    def test_an_unrelated_loaded_package_is_refused_and_left_alone(self):
        import types

        unrelated = types.ModuleType("_asre_case")
        sys.modules["_asre_case"] = unrelated
        loader = self.loader_for(self.members())
        before = self.set_ambient()
        with self.assertRaises(RuntimeError) as caught:
            loader._runtime()
        self.assertEqual(str(caught.exception), GENERIC_LOAD_FAILURE)
        self.assertIs(sys.modules["_asre_case"], unrelated)
        self.assertEqual(dict(os.environ), before)

    def test_missing_bundle_is_still_refused(self):
        spec = importlib.util.spec_from_file_location("servicenow_case_empty", LOADER)
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        self.assertEqual(loader.__CASE_TOOL_BUNDLE__, "")
        self.assertEqual(loader.main("create"), BLOCKED)


class IndexTests(unittest.TestCase):
    def test_knowledge_index_lists_four_existing_files_and_none_is_inert(self):
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        self.assertEqual(index["files"], [
            "operating-boundaries.md", "asre-environment-v1.md", "asre-investigation-v1.md",
            "asre-live-report-sources-v1.md",
        ])
        inert = set(json.loads(MAP.read_text(encoding="utf-8"))["inert"])
        for name in index["files"]:
            with self.subTest(file=name):
                self.assertTrue((ROOT / "agent" / "knowledge" / name).is_file())
                self.assertNotIn("agent/knowledge/" + name, inert)
        self.assertIn("agent/knowledge/asre-servicenow-review-v1.md", inert)
        self.assertTrue((ROOT / "agent" / "knowledge" / "asre-servicenow-review-v1.md").is_file())


if __name__ == "__main__":
    unittest.main()
