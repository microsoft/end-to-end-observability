"""Exercise local live-branch logic with fake SDKs; no network or real SDK use."""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from agentic_itops.tools import log_analytics, runbook_index


def fake_module(name, **members):
    module = types.ModuleType(name)
    module.__dict__.update(members)
    return module


calls = {}


class FakeLogsClient:
    def __init__(self, credential):
        pass

    def query_workspace(self, **kwargs):
        calls["logs"] = kwargs
        return types.SimpleNamespace(status="partial", partial_data=[], partial_error="partial probe")


fake = {
    "azure.identity": fake_module("azure.identity", DefaultAzureCredential=lambda: object()),
    "azure.monitor.query": fake_module(
        "azure.monitor.query", LogsQueryClient=FakeLogsClient,
        LogsQueryStatus=types.SimpleNamespace(SUCCESS="success", PARTIAL="partial", FAILURE="failure")),
}
with patch.dict(sys.modules, fake):
    try:
        log_analytics._run_live("print x=1", "PT1H", "test-workspace")
        partial_error = None
    except AttributeError as exc:
        partial_error = str(exc)
assert partial_error and "tables" in partial_error


class FakeSearchClient:
    def __init__(self, endpoint, index, credential):
        calls["search_credential_type"] = type(credential).__name__

    def search(self, **kwargs):
        calls["search"] = kwargs
        return [{"id": "RB-test", "title": "Test", "kind": "runbook",
                 "documented_actions": ["restart"], "content": "test content", "@search.score": 1.0}]


class FakeKeyCredential:
    def __init__(self, key):
        pass


fake = {
    "azure.core.credentials": fake_module("azure.core.credentials", AzureKeyCredential=FakeKeyCredential),
    "azure.search.documents": fake_module("azure.search.documents", SearchClient=FakeSearchClient),
}
with patch.dict(sys.modules, fake), patch.dict(os.environ, {
    "AZURE_SEARCH_ENDPOINT": "https://unused.invalid", "AZURE_SEARCH_API_KEY": "non-secret-test-placeholder"
}):
    search = runbook_index._search_live("test", 3, "runbook")
assert "documented_actions" not in search["results"][0]
assert set(calls["search"]) == {"search_text", "top", "filter"}

spec = importlib.util.spec_from_file_location("review_deployer", ROOT / "scripts" / "deploy_to_foundry.py")
deployer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployer)
fake_store = types.SimpleNamespace(
    create=lambda **kwargs: types.SimpleNamespace(id="test-store"),
    retrieve=lambda store_id: types.SimpleNamespace(
        file_counts=types.SimpleNamespace(in_progress=0, completed=0, failed=11)))
fake_oai = types.SimpleNamespace(
    files=types.SimpleNamespace(create=lambda **kwargs: types.SimpleNamespace(id="test-file")),
    vector_stores=fake_store)
store_id = deployer.upload_knowledge(fake_oai)
assert store_id == "test-store"
result = {
    "evidenceType": "Mocked SDK/service boundary, not Azure connectivity or API compatibility validation",
    "partialQuery": {"exception": partial_error, "passedTimespanType": type(calls["logs"]["timespan"]).__name__},
    "search": {"request": calls["search"], "result": search, "credentialType": calls["search_credential_type"]},
    "indexing": {"completed": 0, "failed": 11, "returnedStoreDespiteFailure": store_id},
}
(OUT / "live-boundary-probe-results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print("REPRODUCED: partial query tables access; key-only lexical search drops actions; failed indexing returns store")
