"""Tests that enforce the offline claim.

The README says the portfolio runs with no Azure subscription, no
Foundry project and no credentials. That is the kind of claim which is
true when written and quietly false three commits later, the first time
someone adds a convenience import at module scope.

So it is a test rather than a sentence.

What these tests prove and do not prove is spelled out in
``docs/architecture.md``. In short: they prove the deterministic half of
the system executes without Azure. They prove nothing about the quality
of model reasoning, which is not testable offline and should not be
claimed as such.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# A subprocess is used so the guard sees a clean interpreter. Running it
# in-process would pass trivially, because pytest has already imported
# half the world by the time the test body executes.
GUARD_SCRIPT = '''
import sys

BANNED = ("azure", "openai", "msal", "requests", "httpx", "urllib3")

class ImportGuard:
    """Raise if the offline path reaches for a cloud SDK or HTTP client."""
    def find_module(self, name, path=None):
        root = name.split(".")[0]
        if root in BANNED:
            raise AssertionError("offline path imported " + name)
        return None

sys.meta_path.insert(0, ImportGuard())
sys.path.insert(0, r"__ROOT__")

from agentic_itops.normalizer import load_directory, normalize_and_correlate
from agentic_itops.safety_gate import ChangeRequest, ChangeSafetyGate
from agentic_itops.hygiene import analyze
from agentic_itops.tools import get_recent_changes, run_kql, search_knowledge
from agentic_itops.orchestrator import OpsOrchestrator, StubReasoner

incidents, failures = normalize_and_correlate(load_directory(r"__DATA__"))
assert incidents and not failures

gate = ChangeSafetyGate()
assert gate.evaluate(ChangeRequest(action="restart", resource_id="x", environment="prod"))

assert search_knowledge("pump cpu saturation")["results"]
assert run_kql("Perf | where CounterName == 'Percentage CPU'")["mode"] == "stub"
assert get_recent_changes(service="rg-refinery-midstream")["mode"] == "stub"

envelopes = [e for i in incidents for e in i.envelopes]
assert analyze(envelopes)["distinct_rules"] > 0

result = OpsOrchestrator(reasoner=StubReasoner()).handle(incidents[0])
assert result.decision["effect"] in ("allow", "deny", "escalate")
assert result.executed is False

loaded = sorted(set(m.split(".")[0] for m in sys.modules) & set(BANNED))
assert not loaded, "cloud modules loaded: " + repr(loaded)
print("OFFLINE_OK")
'''


def test_offline_path_never_imports_a_cloud_sdk():
    """The whole loop must run without touching azure, openai or an HTTP client.

    Azure imports in ``tools/`` and ``orchestrator.py`` are function-local
    precisely so this holds. If someone hoists one to module scope, this
    test fails rather than the claim silently becoming untrue.
    """
    script = GUARD_SCRIPT.replace("__ROOT__", str(ROOT)).replace(
        "__DATA__", str(ROOT / "data" / "alerts")
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "OFFLINE_OK" in proc.stdout, (
        f"offline guard failed.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_no_credentials_are_required():
    """No environment variable is needed for the offline path.

    Guards against a default like ``os.environ["AZURE_..."]`` creeping
    into an import path and turning a laptop-runnable repo into one that
    needs a populated .env before it will start.
    """
    script = (
        "import os, sys\n"
        "for key in list(os.environ):\n"
        "    if key.startswith(('AZURE_', 'AGENTIC_ITOPS_', 'OPENAI_')):\n"
        "        del os.environ[key]\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        "from agentic_itops.normalizer import load_directory, normalize_and_correlate\n"
        f"incidents, failures = normalize_and_correlate(load_directory(r'{ROOT / 'data' / 'alerts'}'))\n"
        "assert incidents and not failures\n"
        "print('NO_CREDS_OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert "NO_CREDS_OK" in proc.stdout, f"{proc.stdout}\n{proc.stderr}"


@pytest.mark.parametrize(
    "module",
    ["agentic_itops.tools.log_analytics",
     "agentic_itops.tools.resource_graph",
     "agentic_itops.tools.change_history",
     "agentic_itops.tools.runbook_index",
     "agentic_itops.orchestrator"],
)
def test_azure_imports_stay_function_local(module: str):
    """Cloud SDK imports must sit inside functions, not at module scope."""
    path = ROOT / Path(module.replace(".", "/") + ".py")
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith(("import azure", "from azure", "import openai", "from openai")):
            pytest.fail(
                f"{module}:{lineno} imports a cloud SDK at module scope: {line.strip()!r}. "
                f"Move it inside the function that needs it."
            )
