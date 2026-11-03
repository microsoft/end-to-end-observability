"""Test bootstrap: every test process starts from the synthetic deployment context.

The context must be in the environment before any module that reads it is imported;
pytest imports this file before collecting the test modules. Tests that need another
context run a subprocess with ASRE_CONTEXT_FILE pointing at tests/fixtures/context.alt.json.
"""

import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

os.environ["ASRE_CONTEXT_FILE"] = str(FIXTURES / "context.synthetic.json")
os.environ.pop("ASRE_CONTEXT", None)
