"""Count-only acceptance boundary; no inference, model or live Azure calls."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest

from agentic_itops.access_probe import AccessProofError, operator_witness, query_contract
from agentic_itops.consumer.contracts import ContractError, canonical, iso
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_preflight import WorkerAccessPreflight, create_worker_access_preflight


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = "11111111-1111-1111-1111-111111111111"
WORKER = "22222222-2222-2222-2222-222222222222"
REVIEW = "33333333-3333-3333-3333-333333333333"
WORKSPACE = "44444444-4444-4444-4444-444444444444"
NOW = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
CONTRACT = query_contract(WORKSPACE, "approved-retained-run", iso(NOW - timedelta(days=1)), iso(NOW))


def count(rows):
    return {"tables": [{"columns": [{"name": "rows", "type": "long"}], "rows": [[rows]]}]}


def witness():
    return operator_witness(CONTRACT, lambda workspace, query: (200, count(4 if "Audit" in query else 10)), now=NOW)


class Auth:
    def __init__(self, oid=BOOTSTRAP):
        self.oid = oid
    def authenticate(self, headers, role):
        assert role == "Worker.Invoke"
        return self.oid


@pytest.mark.parametrize("denial", [(403, {"error": {"code": "InsufficientAccessError"}}), (200, count(0))])
def test_only_bootstrap_caller_can_prove_worker_access_using_count_only_queries(denial):
    calls = []
    def query(workspace, text):
        calls.append((workspace, text))
        return denial if "Audit" in text else (200, count(10))
    service = WorkerAccessPreflight(Auth(), BOOTSTRAP, WORKER, CONTRACT, query, clock=lambda: NOW)
    proof = service.verify({}, {"witness": witness()})
    assert proof["status"] == "verified"
    assert proof["worker_client_id"] == WORKER and proof["caller_oid"] == BOOTSTRAP
    assert proof["inference_input"] is False and proof["execution_allowed"] is False
    assert len(calls) == 2 and all(text.endswith("| summarize rows=count()") for _, text in calls)
    service.auth = Auth(REVIEW)
    with pytest.raises(ReviewError) as failure:
        service.verify({}, {"witness": witness()})
    assert failure.value.status == 403 and len(calls) == 2


@pytest.mark.parametrize("mutation", [
    {"queries": {"audit": "EnergyInjectionAudit_CL"}},
    {"workspace_id": WORKSPACE},
    {"contract": CONTRACT},
])
def test_caller_cannot_supply_queries_workspace_or_contract(mutation):
    service = WorkerAccessPreflight(
        Auth(), BOOTSTRAP, WORKER, CONTRACT, lambda *args: pytest.fail("no query allowed"), clock=lambda: NOW)
    with pytest.raises(ContractError):
        service.verify({}, {"witness": witness(), **mutation})


def test_witness_cannot_carry_audit_labels_or_stale_authority():
    service = WorkerAccessPreflight(
        Auth(), BOOTSTRAP, WORKER, CONTRACT, lambda *args: pytest.fail("no query allowed"), clock=lambda: NOW)
    with pytest.raises(ContractError):
        service.verify({}, {"witness": {**witness(), "audit_rows_payload": [{"label": "forbidden"}]}})
    old = {**witness(), "observed_at": iso(NOW - timedelta(minutes=6))}
    with pytest.raises(AccessProofError, match="five minutes"):
        service.verify({}, {"witness": old})


@pytest.mark.parametrize("audit", [(500, {"error": {"code": "ServerError"}}), (200, count(4)),
                                   (403, {"error": {"code": "Unknown"}})])
def test_indeterminate_or_allowed_audit_access_is_never_proven_denial(audit):
    service = WorkerAccessPreflight(
        Auth(), BOOTSTRAP, WORKER, CONTRACT,
        lambda workspace, text: audit if "Audit" in text else (200, count(10)), clock=lambda: NOW)
    with pytest.raises(AccessProofError):
        service.verify({}, {"witness": witness()})


def test_preflight_factory_uses_worker_identity_without_runtime_sql_or_models(tmp_path, monkeypatch):
    import agentic_itops.service_preflight as module
    path = tmp_path / "contract.json"
    path.write_bytes(canonical(CONTRACT))
    environment = {
        "ITOPS_ACCESS_PROBE_CONTRACT_PATH": str(path), "AZURE_CLIENT_ID": WORKER,
        "ENTRA_TENANT_ID": BOOTSTRAP, "ENTRA_AUDIENCE": REVIEW,
        "ITOPS_SERVICE_CALLERS_JSON": "[]", "ITOPS_PREFLIGHT_BOOTSTRAP_OID": BOOTSTRAP,
    }
    tokens, calls = [], []
    credential = SimpleNamespace(get_token=lambda scope: tokens.append(scope) or SimpleNamespace(token="worker-only"))
    def identity(env):
        assert env["AZURE_CLIENT_ID"] == WORKER
        return credential
    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer worker-only"
        query = json.loads(request.content)["query"]
        return httpx.Response(403, json={"error": {"code": "InsufficientAccessError"}}) if "Audit" in query \
            else httpx.Response(200, json=count(10))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module, "_identity", identity)
    monkeypatch.setattr(module, "ServiceAuthenticator", lambda *args: Auth())
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    service = create_worker_access_preflight(environment, root=ROOT)
    service.clock = lambda: NOW
    assert service.verify({}, {"witness": witness()})["worker_client_id"] == WORKER
    assert len(tokens) == 2 and len(calls) == 2
    assert all(scope == "https://api.loganalytics.io/.default" for scope in tokens)


def test_preflight_package_exposes_no_inference_routes_or_registration_artifacts(tmp_path):
    spec = importlib.util.spec_from_file_location("preflight_package", ROOT / "scripts" / "package_application_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "contract.json"
    path.write_bytes(canonical(CONTRACT))
    archive = tmp_path / "preflight.zip"
    receipt = module.package("worker", {"access-probe-contract.json": path}, archive, phase="preflight")
    assert receipt["runtime_enabled"] is False and receipt["portfolio_sha256"] is None
    with ZipFile(archive) as zipped:
        assert ".preflight/access-probe-contract.json" in zipped.namelist()
        assert ".runtime/registration-plan.json" not in zipped.namelist()
        assert zipped.read("function_app.py") == (ROOT / "services" / "worker" / "preflight_function_app.py").read_bytes()
        requirements = zipped.read("requirements.txt").decode()
        assert "azure-functions==2.3.0" in requirements
        assert not any(name in requirements for name in ("mssql-python", "openai", "azure-ai-projects",
                                                         "azure-search-documents", "azure-storage-blob", "mcp", "pytest"))
    spec = importlib.util.spec_from_file_location("preflight_function", ROOT / "services" / "worker" / "preflight_function_app.py")
    function = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(function)
    registered = function.app.get_functions()
    assert len(registered) == 1
    bindings = json.loads(registered[0].get_function_json())["bindings"]
    assert next(item["route"] for item in bindings if item["type"] == "httpTrigger") == "preflight/source-access"


def test_preflight_import_does_not_require_model_search_storage_or_native_sql_sdks():
    script = """
import importlib.abc
import runpy
import sys
class NoHeavySDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('mssql_python', 'openai', 'azure.ai', 'azure.search', 'azure.storage', 'mcp')
        if any(fullname == item or fullname.startswith(item + '.') for item in blocked):
            raise ImportError('preflight cannot import ' + fullname)
sys.meta_path.insert(0, NoHeavySDK())
module = runpy.run_path(sys.argv[1])
assert len(module['app'].get_functions()) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "services" / "worker" / "preflight_function_app.py")],
        cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
