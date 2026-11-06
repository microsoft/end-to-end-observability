import json
import re
import tempfile
import unittest
from pathlib import Path

from tests import alt_context


class AltContextHelperTests(unittest.TestCase):
    def test_default_run_loads_the_alternate_context(self):
        alternate = json.loads(alt_context.ALT.read_text(encoding="utf-8"))
        result = alt_context.run_with_context("from operations import context; print(context.load().name_suffix)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), alternate["name_suffix"])

    def test_missing_context_fails_closed(self):
        result = alt_context.run_with_context("from operations import context; context.load()", context_file=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ContextError", result.stderr)

    def test_values_unique_to_the_primary_context_are_reported(self):
        suffix = json.loads(alt_context.SYNTHETIC.read_text(encoding="utf-8"))["name_suffix"]
        self.assertIn(suffix, alt_context.synthetic_only_values())
        source = (
            'CLEAN = "nothing-deployment-specific"\n'
            f'LEAKY = {{"vault": "kv-asre-{suffix}"}}\n\n\n'
            "def hidden():\n"
            f'    return "stasre{suffix}"\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "leaky_fixture_module.py").write_text(source, encoding="utf-8")
            leaks = alt_context.leaks_in_module("leaky_fixture_module", pythonpath=[directory])
        self.assertEqual({re.sub(r"\.const\[\d+\]", "", item) for item in leaks}, {"LEAKY['vault']", "hidden"})

    def test_the_context_module_itself_holds_no_primary_context_value(self):
        self.assertEqual(alt_context.leaks_in_module("operations.context"), [])


if __name__ == "__main__":
    unittest.main()
