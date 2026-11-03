"""Contract tests for the deployment-context loader (operations/context.py).

Every other runtime module trusts this loader, so it is tested on its own: the shared mutant list,
the JSON Schema, the byte/encoding/duplicate-key guards, the environment precedence (never a default),
caching, the derived resource ids (against hand-written strings), optional parts and immutability.
"""

import copy
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from operations import context
from operations.context import ContextError
from tests.alt_context import ALT, FIXTURES, ROOT, SYNTHETIC

SCHEMA_FILE = ROOT / "schemas" / "asre-context.v1.schema.json"
MUTANTS_FILE = FIXTURES / "context.mutants.json"
SCHEMA_VALIDATOR = Draft202012Validator(json.loads(SCHEMA_FILE.read_text(encoding="utf-8")))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compact(document):
    return json.dumps(document, separators=(",", ":"))


def apply_mutant(base, mutant):
    document = copy.deepcopy(base)
    for path, value in mutant.get("set", {}).items():
        *parents, leaf = path.split(".")
        node = document
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = value
    for path in mutant.get("delete", []):
        *parents, leaf = path.split(".")
        node = document
        for key in parents:
            node = node[key]
        del node[leaf]
    return document


def loader_accepts(document):
    try:
        context.parse(compact(document))
    except ContextError:
        return False
    return True


def padded(document, size):
    """Compact JSON text padded with trailing blanks to exactly ``size`` UTF-8 bytes."""
    text = compact(document)
    assert len(text.encode("utf-8")) <= size
    return text + " " * (size - len(text.encode("utf-8")))


def fresh(code, *, cwd=None, env=None):
    """Run a snippet in a new interpreter with no ASRE_* variables except those passed in ``env``."""
    variables = {key: value for key, value in os.environ.items() if not key.startswith("ASRE_")}
    variables["PYTHONDONTWRITEBYTECODE"] = "1"
    variables["PYTHONPATH"] = str(ROOT)
    variables.update(env or {})
    return subprocess.run([sys.executable, "-c", code], cwd=cwd or ROOT, env=variables,
                          capture_output=True, text=True, timeout=120, check=False)


SYNTHETIC_DOCUMENT = read_json(SYNTHETIC)
ALT_DOCUMENT = read_json(ALT)
MUTANTS = read_json(MUTANTS_FILE)


class EnvironmentTestCase(unittest.TestCase):
    """Isolated process environment and cache: both are restored after every test."""

    def setUp(self):
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(context.ENV_JSON, None)
        os.environ.pop(context.ENV_FILE, None)
        context.reset()
        self.addCleanup(context.reset)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def write(self, name, data):
        path = self.directory / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return str(path)


class FixtureTests(unittest.TestCase):
    def test_synthetic_and_alternate_fixtures_load(self):
        synthetic = context.parse(SYNTHETIC.read_text(encoding="utf-8"))
        alternate = context.parse(ALT.read_text(encoding="utf-8"))
        self.assertEqual(synthetic.subscription_id, SYNTHETIC_DOCUMENT["subscription_id"])
        self.assertEqual(alternate.subscription_id, ALT_DOCUMENT["subscription_id"])
        self.assertNotEqual(synthetic, alternate)

    def test_text_bytes_and_decoded_object_parse_to_the_same_context(self):
        text = ALT.read_text(encoding="utf-8")
        expected = context.parse(text)
        self.assertEqual(context.parse(text.encode("utf-8")), expected)
        self.assertEqual(context.parse(ALT_DOCUMENT), expected)

    def test_name_suffix_is_derived_from_the_subscription(self):
        for document in (SYNTHETIC_DOCUMENT, ALT_DOCUMENT):
            digest = hashlib.sha256(f"{document['subscription_id']}:asre-demo".encode()).hexdigest()
            self.assertEqual(document["name_suffix"], digest[:10])
        mutated = copy.deepcopy(ALT_DOCUMENT)
        mutated["name_suffix"] = SYNTHETIC_DOCUMENT["name_suffix"]
        with self.assertRaises(ContextError):
            context.parse(compact(mutated))

    def test_module_constants(self):
        self.assertEqual(context.SCHEMA, "asre-context/v1")
        self.assertEqual(context.ENV_JSON, "ASRE_CONTEXT")
        self.assertEqual(context.ENV_FILE, "ASRE_CONTEXT_FILE")
        self.assertEqual(context.GROUPS, ("control", "linux", "patch", "backup"))
        self.assertEqual(context.ROLES, ("linux", "patch", "backup"))
        self.assertEqual(context.IDENTITY_KEYS, ("agent", "runner", "collector", "pull"))


class MutantTests(unittest.TestCase):
    def test_the_mutant_list_is_present_and_unambiguous(self):
        self.assertEqual(MUTANTS["format"], "asre-context-mutants/v1")
        names = [item["name"] for item in MUTANTS["valid"] + MUTANTS["invalid"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(MUTANTS["valid"]), 8)
        self.assertGreaterEqual(len(MUTANTS["invalid"]), 50)
        self.assertEqual(MUTANTS["base"], SYNTHETIC.name)

    def test_every_valid_mutant_loads(self):
        for mutant in MUTANTS["valid"]:
            with self.subTest(mutant=mutant["name"]):
                loaded = context.parse(compact(apply_mutant(SYNTHETIC_DOCUMENT, mutant)))
                self.assertIsInstance(loaded, context.Context)

    def test_every_invalid_mutant_raises_the_context_error_including_cross_field(self):
        for mutant in MUTANTS["invalid"]:
            with self.subTest(mutant=mutant["name"], cross_field=bool(mutant.get("cross_field"))):
                with self.assertRaises(ContextError):
                    context.parse(compact(apply_mutant(SYNTHETIC_DOCUMENT, mutant)))

    def test_mutants_are_independent_of_the_synthetic_base_for_the_optional_parts(self):
        for mutant in MUTANTS["valid"]:
            with self.subTest(valid=mutant["name"]):
                self.assertTrue(loader_accepts(apply_mutant(ALT_DOCUMENT, mutant)))


class SchemaAgreementTests(unittest.TestCase):
    def test_the_schema_is_a_valid_draft_2020_12_schema_and_accepts_both_fixtures(self):
        Draft202012Validator.check_schema(SCHEMA_VALIDATOR.schema)
        self.assertEqual(list(SCHEMA_VALIDATOR.iter_errors(SYNTHETIC_DOCUMENT)), [])
        self.assertEqual(list(SCHEMA_VALIDATOR.iter_errors(ALT_DOCUMENT)), [])

    def test_loader_and_schema_classify_every_mutant_the_same_way(self):
        for group, expected in (("valid", True), ("invalid", False)):
            for mutant in MUTANTS[group]:
                document = apply_mutant(SYNTHETIC_DOCUMENT, mutant)
                by_loader = loader_accepts(document)
                by_schema = SCHEMA_VALIDATOR.is_valid(document)
                with self.subTest(mutant=mutant["name"], group=group):
                    self.assertEqual(by_loader, expected)
                    # The schema cannot express cross-field derivations or regex-engine edge cases.
                    if not mutant.get("cross_field"):
                        self.assertEqual(by_schema, expected)
                    # The loader is never looser than the schema.
                    if by_loader:
                        self.assertTrue(by_schema)

    def test_the_schema_forbids_stored_resource_ids_like_the_loader(self):
        for path in ("identities.agent.resource_id", "targets.linux.resource_id", "agent.resource_id"):
            mutated = apply_mutant(ALT_DOCUMENT, {"set": {path: "/subscriptions/x"}})
            with self.subTest(path=path):
                self.assertFalse(SCHEMA_VALIDATOR.is_valid(mutated))
                self.assertFalse(loader_accepts(mutated))


class PayloadGuardTests(unittest.TestCase):
    def test_the_size_cap_is_exact(self):
        self.assertEqual(context.MAX_BYTES, 65536)
        self.assertEqual(context.parse(padded(ALT_DOCUMENT, 65536)), context.parse(compact(ALT_DOCUMENT)))
        with self.assertRaisesRegex(ContextError, "too large"):
            context.parse(padded(ALT_DOCUMENT, 65537))
        self.assertEqual(context.parse(padded(ALT_DOCUMENT, 65536).encode("utf-8")).name_suffix,
                         ALT_DOCUMENT["name_suffix"])
        with self.assertRaisesRegex(ContextError, "too large"):
            context.parse(padded(ALT_DOCUMENT, 65537).encode("utf-8"))

    def test_the_size_cap_counts_bytes_not_characters(self):
        # 32,768 two-byte characters are 65,536 bytes of payload on their own, so the document is over the cap
        # although it has far fewer than 65,536 characters.
        document = copy.deepcopy(ALT_DOCUMENT)
        document["extra"] = "\u00e9" * 32768
        text = json.dumps(document, ensure_ascii=False)
        self.assertLess(len(text), 65536)
        self.assertGreater(len(text.encode("utf-8")), 65536)
        with self.assertRaisesRegex(ContextError, "too large"):
            context.parse(text)

    def test_text_that_is_not_utf8_or_not_json_is_a_context_error(self):
        valid = compact(ALT_DOCUMENT)
        cases = {
            "invalid utf-8 bytes": b"\xff\xfe" + valid.encode("utf-8"),
            "truncated multi-byte sequence": valid.encode("utf-8") + b"\xc3",
            "bom bytes": b"\xef\xbb\xbf" + valid.encode("utf-8"),
            "bom text": "\ufeff" + valid,
            "lone surrogate text": valid + "\udc80",
            "empty": "",
            "blank": "   \n",
            "not json": "{not json}",
            "single quotes": valid.replace('"', "'"),
            "trailing garbage": valid + "x",
            "trailing second object": valid + " {}",
            "two documents": valid + valid,
            "trailing comma": valid[:-1] + ",}",
            "nan constant": valid.replace('"centralus"', "NaN", 1),
        }
        for label, value in cases.items():
            with self.subTest(case=label), self.assertRaises(ContextError):
                context.parse(value)

    def test_surrounding_whitespace_is_not_data(self):
        self.assertEqual(context.parse("\n  " + compact(ALT_DOCUMENT) + "\r\n\t ").name_suffix,
                         ALT_DOCUMENT["name_suffix"])

    def test_the_root_must_be_an_object(self):
        for value in ("[]", "null", "5", '"text"', "true", "{}", None, 5, [], [ALT_DOCUMENT], object()):
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ContextError):
                context.parse(value)

    def test_extremely_deep_nesting_is_a_context_error_not_a_crash(self):
        for text in ("[" * 60000, '{"a":' * 9000 + "1" + "}" * 9000):
            with self.subTest(size=len(text)), self.assertRaises(ContextError):
                context.parse(text)

    def test_duplicate_keys_are_rejected_at_every_depth(self):
        text = json.dumps(ALT_DOCUMENT)
        spots = {
            "top level": ('"location": "centralus"', '"location": "centralus", "location": "centralus"'),
            "second level": ('"control": "rg-asre-control-cus"',
                             '"control": "rg-asre-control-cus", "control": "rg-asre-control-cus"'),
            "third level": ('"client_id": "00000971-000a-4000-8000-000000000971"',
                            '"client_id": "00000971-000a-4000-8000-000000000971", '
                            '"client_id": "00000971-000a-4000-8000-000000000971"'),
            "fourth level": ('"linux": "0000000000000000000000000000914a"',
                             '"linux": "0000000000000000000000000000914a", '
                             '"linux": "0000000000000000000000000000914a"'),
            "optional section": ('"endpoint": "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai"',
                                 '"endpoint": null, '
                                 '"endpoint": "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai"'),
        }
        self.assertEqual(context.parse(text).name_suffix, ALT_DOCUMENT["name_suffix"])
        for label, (old, new) in spots.items():
            with self.subTest(depth=label):
                self.assertEqual(text.count(old), 1)
                with self.assertRaisesRegex(ContextError, "duplicate key"):
                    context.parse(text.replace(old, new, 1))

    def test_duplicate_keys_that_differ_only_by_value_are_not_resolved_by_last_wins(self):
        text = json.dumps(ALT_DOCUMENT).replace(
            '"location": "centralus"', '"location": "eastus", "location": "centralus"', 1)
        with self.assertRaisesRegex(ContextError, "duplicate key"):
            context.parse(text)

    def test_error_messages_never_echo_the_document(self):
        secret = "00000999-000a-4000-8000-000000000999"
        mutated = copy.deepcopy(ALT_DOCUMENT)
        mutated["subscription_id"] = secret.upper()
        mutated["identities"]["runner"]["client_id"] = secret + "-not-a-guid"
        with self.assertRaises(ContextError) as caught:
            context.parse(compact(mutated))
        self.assertNotIn(secret, str(caught.exception).lower())


class EnvironmentTests(EnvironmentTestCase):
    def test_inline_context_wins_over_the_context_file(self):
        os.environ[context.ENV_JSON] = ALT.read_text(encoding="utf-8")
        os.environ[context.ENV_FILE] = str(SYNTHETIC)
        self.assertEqual(context.load().name_suffix, ALT_DOCUMENT["name_suffix"])

    def test_the_context_file_is_used_when_there_is_no_inline_context(self):
        os.environ[context.ENV_FILE] = str(ALT)
        self.assertEqual(context.load().name_suffix, ALT_DOCUMENT["name_suffix"])
        context.reset()
        os.environ[context.ENV_JSON] = "  \n"
        os.environ[context.ENV_FILE] = str(SYNTHETIC)
        self.assertEqual(context.load().name_suffix, SYNTHETIC_DOCUMENT["name_suffix"])

    def test_a_broken_inline_context_never_falls_back_to_the_file(self):
        os.environ[context.ENV_JSON] = "{not json"
        os.environ[context.ENV_FILE] = str(ALT)
        with self.assertRaises(ContextError):
            context.load()

    def test_no_configuration_is_a_context_error_and_there_is_no_default_path(self):
        with self.assertRaisesRegex(ContextError, "not configured"):
            context.load()
        os.environ[context.ENV_JSON] = "  "
        os.environ[context.ENV_FILE] = "  "
        with self.assertRaisesRegex(ContextError, "not configured"):
            context.load()

    def test_a_failed_load_is_not_cached(self):
        with self.assertRaises(ContextError):
            context.load()
        os.environ[context.ENV_FILE] = str(ALT)
        self.assertEqual(context.load().name_suffix, ALT_DOCUMENT["name_suffix"])

    def test_unreadable_files_are_context_errors(self):
        os.environ[context.ENV_FILE] = str(self.directory / "missing.json")
        with self.assertRaisesRegex(ContextError, "cannot be read"):
            context.load()
        os.environ[context.ENV_FILE] = str(self.directory)
        with self.assertRaisesRegex(ContextError, "cannot be read"):
            context.load()

    def test_file_content_is_validated_like_inline_content(self):
        valid = compact(ALT_DOCUMENT)
        cases = {
            "non-utf8": (b"\xff\xfe\x00" + valid.encode("utf-8"), "not UTF-8"),
            "utf-8 bom": (b"\xef\xbb\xbf" + valid.encode("utf-8"), "not valid JSON"),
            "utf-16": (valid.encode("utf-16"), None),
            "trailing data": ((valid + "\n{}").encode("utf-8"), "not valid JSON"),
            "trailing text": ((valid + "x").encode("utf-8"), "not valid JSON"),
            "duplicate key": ((valid.replace('"location"', '"location":"centralus","location"', 1)).encode("utf-8"),
                              "duplicate key"),
            "empty": (b"", "not valid JSON"),
        }
        for label, (data, message) in cases.items():
            with self.subTest(case=label):
                context.reset()
                os.environ[context.ENV_FILE] = self.write(label.replace(" ", "-") + ".json", data)
                with self.assertRaises(ContextError) as caught:
                    context.load()
                if message:
                    self.assertIn(message, str(caught.exception))

    def test_the_file_size_cap_is_exact_and_a_larger_file_is_never_read_past_it(self):
        os.environ[context.ENV_FILE] = self.write("exact.json", padded(ALT_DOCUMENT, 65536))
        self.assertEqual(context.load().name_suffix, ALT_DOCUMENT["name_suffix"])
        for size in (65537, 70000, 1_000_000):
            with self.subTest(size=size):
                context.reset()
                os.environ[context.ENV_FILE] = self.write(f"over-{size}.json", padded(ALT_DOCUMENT, size))
                with self.assertRaises(ContextError):
                    context.load()

    def test_the_context_is_cached_until_it_is_reset(self):
        os.environ[context.ENV_FILE] = str(ALT)
        first = context.load()
        self.assertIs(context.load(), first)
        os.environ[context.ENV_FILE] = str(SYNTHETIC)
        os.environ[context.ENV_JSON] = SYNTHETIC.read_text(encoding="utf-8")
        self.assertIs(context.load(), first)
        context.reset()
        second = context.load()
        self.assertIsNot(second, first)
        self.assertEqual(second.name_suffix, SYNTHETIC_DOCUMENT["name_suffix"])
        os.environ.pop(context.ENV_JSON)
        os.environ[context.ENV_FILE] = str(ALT)
        self.assertIs(context.load(), second)
        context.reset()
        self.assertEqual(context.load(), first)

    def test_the_process_environment_is_restored_by_the_fixture(self):
        # Guards the test helper itself: nothing a test sets may leak into the next test or module.
        self.assertNotIn(context.ENV_JSON, os.environ)
        self.assertNotIn(context.ENV_FILE, os.environ)


class FreshInterpreterTests(unittest.TestCase):
    PRINT_SUFFIX = "from operations import context; print(context.load().name_suffix)"

    def test_nothing_configured_fails_closed_and_ignores_files_that_look_like_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for relative in ("context.json", "context.generated.json", "asre-context.json",
                             "infra/environments/context.generated.json"):
                path = work / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(ALT.read_text(encoding="utf-8"), encoding="utf-8")
            result = fresh(self.PRINT_SUFFIX, cwd=work)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ContextError", result.stderr)
        self.assertNotIn(ALT_DOCUMENT["name_suffix"], result.stdout)

    def test_inline_json_in_the_environment_wins_over_the_file(self):
        result = fresh(self.PRINT_SUFFIX, env={
            "ASRE_CONTEXT": ALT.read_text(encoding="utf-8"), "ASRE_CONTEXT_FILE": str(SYNTHETIC)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), ALT_DOCUMENT["name_suffix"])

    def test_a_broken_inline_context_fails_even_when_the_file_is_valid(self):
        result = fresh(self.PRINT_SUFFIX, env={"ASRE_CONTEXT": "{", "ASRE_CONTEXT_FILE": str(ALT)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ContextError", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_importing_the_loader_reads_no_context(self):
        result = fresh("import operations.context", env={})
        self.assertEqual(result.returncode, 0, result.stderr)


class DerivedIdentifierTests(unittest.TestCase):
    """The expected strings are written out by hand for the alternate context."""

    @classmethod
    def setUpClass(cls):
        cls.loaded = context.parse(ALT.read_text(encoding="utf-8"))

    def test_scalars(self):
        loaded = self.loaded
        self.assertEqual(loaded.subscription_id, "00000901-000a-4000-8000-000000000901")
        self.assertEqual(loaded.tenant_id, "00000902-000a-4000-8000-000000000902")
        self.assertEqual(loaded.location, "centralus")
        self.assertEqual(loaded.name_suffix, "e26b179818")
        self.assertEqual(loaded.operator_object_id, "00000903-000a-4000-8000-000000000903")

    def test_resource_group_ids(self):
        for group in ("control", "linux", "patch", "backup"):
            self.assertEqual(self.loaded.resource_groups[group], f"rg-asre-{group}-cus")
            self.assertEqual(self.loaded.resource_group_id(group),
                             f"/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-{group}-cus")
        with self.assertRaises(KeyError):
            self.loaded.resource_group_id("other")

    def test_log_analytics_registry_vault_storage_and_recovery_vault(self):
        loaded = self.loaded
        self.assertEqual(loaded.log_analytics, context.LogAnalytics(
            "law-asre-cus",
            "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-control-cus"
            "/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus",
            "00000962-000a-4000-8000-000000000962"))
        self.assertEqual(loaded.registry, context.Registry("acrasree26b179818", "acrasree26b179818.azurecr.io"))
        self.assertEqual(loaded.key_vault, context.KeyVault(
            "kv-asre-e26b179818", "https://kv-asre-e26b179818.vault.azure.net/"))
        self.assertEqual(loaded.storage, context.Storage(
            "stasree26b179818", "https://stasree26b179818.blob.core.windows.net",
            "https://stasree26b179818.table.core.windows.net"))
        self.assertEqual(loaded.recovery_vault, context.RecoveryVault(
            "rsv-asre-cus", "rg-asre-backup-cus",
            "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-backup-cus"
            "/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"))

    def test_identities(self):
        expected = {
            "agent": ("00000971-000a-4000-8000-000000000971", "00000972-000a-4000-8000-000000000972"),
            "runner": ("00000928-000a-4000-8000-000000000928", "00000973-000a-4000-8000-000000000973"),
            "collector": ("00000974-000a-4000-8000-000000000974", "00000975-000a-4000-8000-000000000975"),
            "pull": ("00000976-000a-4000-8000-000000000976", "00000977-000a-4000-8000-000000000977"),
            "bootstrap": ("00000978-000a-4000-8000-000000000978", "00000979-000a-4000-8000-000000000979"),
        }
        self.assertEqual(list(self.loaded.identities), list(expected))
        for key, (client, principal) in expected.items():
            self.assertEqual(self.loaded.identities[key], context.Identity(
                f"id-asre-{key}-cus",
                "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-control-cus"
                f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-{key}-cus",
                client, principal))

    def test_targets_and_the_lower_cased_role_lookup(self):
        addresses = {"linux": "10.42.0.10", "patch": "10.42.0.11", "backup": "10.42.0.12"}
        self.assertEqual(list(self.loaded.targets), ["linux", "patch", "backup"])
        for role, address in addresses.items():
            resource_id = (f"/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-{role}-cus"
                           f"/providers/Microsoft.Compute/virtualMachines/vm-asre-{role}-cus")
            self.assertEqual(self.loaded.targets[role], context.Target(
                role, f"vm-asre-{role}-cus", f"rg-asre-{role}-cus", resource_id, address))
        self.assertEqual(list(self.loaded.roles_by_resource_id().items()), [
            ("/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-linux-cus"
             "/providers/microsoft.compute/virtualmachines/vm-asre-linux-cus", "linux"),
            ("/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-patch-cus"
             "/providers/microsoft.compute/virtualmachines/vm-asre-patch-cus", "patch"),
            ("/subscriptions/00000901-000a-4000-8000-000000000901/resourcegroups/rg-asre-backup-cus"
             "/providers/microsoft.compute/virtualmachines/vm-asre-backup-cus", "backup"),
        ])

    def test_agent_and_servicenow(self):
        self.assertEqual(self.loaded.agent, context.Agent(
            "asre-demo-cus",
            "/subscriptions/00000901-000a-4000-8000-000000000901/resourceGroups/rg-asre-control-cus"
            "/providers/Microsoft.App/agents/asre-demo-cus",
            "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai"))
        self.assertEqual(self.loaded.servicenow, context.ServiceNow(
            "https://alt-instance.service-now.com", "/api/x_0000900_asre/case_adapter/v1",
            "0000000000000000000000000000093a", "0000000000000000000000000000095a",
            "0000000000000000000000000000097a",
            {"linux": "0000000000000000000000000000914a", "patch": "0000000000000000000000000000901a",
             "backup": "0000000000000000000000000000912a"}))

    def test_the_whole_context_equals_the_hand_written_expectation(self):
        loaded = self.loaded
        self.assertEqual(
            (loaded.subscription_id, loaded.tenant_id, loaded.location, loaded.name_suffix, loaded.operator_object_id),
            ("00000901-000a-4000-8000-000000000901", "00000902-000a-4000-8000-000000000902", "centralus",
             "e26b179818", "00000903-000a-4000-8000-000000000903"))
        self.assertEqual(dict(loaded.resource_groups), {
            "control": "rg-asre-control-cus", "linux": "rg-asre-linux-cus", "patch": "rg-asre-patch-cus",
            "backup": "rg-asre-backup-cus"})


class OptionalPartTests(unittest.TestCase):
    def test_a_present_optional_part_is_returned(self):
        loaded = context.parse(compact(ALT_DOCUMENT))
        self.assertEqual(loaded.require_agent_endpoint(),
                         "https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai")
        self.assertIs(loaded.require_servicenow(), loaded.servicenow)
        self.assertEqual(loaded.require_servicenow().origin, "https://alt-instance.service-now.com")

    def test_an_absent_or_null_optional_part_raises_only_when_it_is_required(self):
        for label, document in {
            "null": {"set": {"agent.endpoint": None, "servicenow": None, "operator_object_id": None,
                             "identities.bootstrap": None}},
            "absent": {"delete": ["agent.endpoint", "servicenow", "operator_object_id", "identities.bootstrap"]},
        }.items():
            with self.subTest(form=label):
                loaded = context.parse(compact(apply_mutant(ALT_DOCUMENT, document)))
                self.assertIsNone(loaded.agent.endpoint)
                self.assertIsNone(loaded.servicenow)
                self.assertIsNone(loaded.operator_object_id)
                self.assertNotIn("bootstrap", loaded.identities)
                self.assertEqual(list(loaded.identities), ["agent", "runner", "collector", "pull"])
                with self.assertRaisesRegex(ContextError, "agent endpoint"):
                    loaded.require_agent_endpoint()
                with self.assertRaisesRegex(ContextError, "servicenow"):
                    loaded.require_servicenow()
                # Everything the first deployment phase uses is still present.
                self.assertEqual(loaded.agent.name, "asre-demo-cus")
                self.assertEqual(len(loaded.targets), 3)

    def test_an_empty_endpoint_string_is_not_an_endpoint(self):
        self.assertFalse(loader_accepts(apply_mutant(ALT_DOCUMENT, {"set": {"agent.endpoint": ""}})))

    def test_each_optional_part_is_independent(self):
        for path in ("agent.endpoint", "servicenow", "operator_object_id", "identities.bootstrap"):
            with self.subTest(removed=path):
                loaded = context.parse(compact(apply_mutant(ALT_DOCUMENT, {"delete": [path]})))
                self.assertEqual(loaded.agent.endpoint is None, path == "agent.endpoint")
                self.assertEqual(loaded.servicenow is None, path == "servicenow")
                self.assertEqual(loaded.operator_object_id is None, path == "operator_object_id")
                self.assertEqual("bootstrap" in loaded.identities, path != "identities.bootstrap")


class ImmutabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = context.parse(compact(ALT_DOCUMENT))

    def test_every_record_is_a_frozen_dataclass(self):
        loaded = self.loaded
        records = [loaded, loaded.log_analytics, loaded.registry, loaded.key_vault, loaded.storage,
                   loaded.recovery_vault, loaded.agent, loaded.servicenow, loaded.identities["agent"],
                   loaded.targets["linux"]]
        for record in records:
            with self.subTest(record=type(record).__name__):
                self.assertTrue(dataclasses.is_dataclass(record))
                field = dataclasses.fields(record)[0].name
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(record, field, "changed")
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    delattr(record, field)

    def test_mappings_are_read_only(self):
        loaded = self.loaded
        for label, mapping in {
            "resource_groups": loaded.resource_groups, "identities": loaded.identities,
            "targets": loaded.targets, "configuration_items": loaded.servicenow.configuration_items,
            "roles_by_resource_id": loaded.roles_by_resource_id(),
        }.items():
            with self.subTest(mapping=label):
                key = next(iter(mapping))
                with self.assertRaises(TypeError):
                    mapping[key] = "changed"
                with self.assertRaises(TypeError):
                    mapping["other"] = "added"
                with self.assertRaises((TypeError, AttributeError)):
                    del mapping[key]

    def test_loading_twice_never_shares_mutable_state_with_the_input(self):
        document = copy.deepcopy(ALT_DOCUMENT)
        loaded = context.parse(document)
        document["resource_groups"]["control"] = "rg-changed"
        document["targets"]["linux"]["name"] = "vm-changed"
        self.assertEqual(loaded.resource_groups["control"], "rg-asre-control-cus")
        self.assertEqual(loaded.targets["linux"].name, "vm-asre-linux-cus")


if __name__ == "__main__":
    unittest.main()
