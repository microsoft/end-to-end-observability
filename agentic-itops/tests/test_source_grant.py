import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import grant_observation_access as script


@pytest.mark.parametrize("broad_role", [False, True])
def test_exact_condition_readback_and_no_duplicate_grant(tmp_path, monkeypatch, broad_role):
    httpx = pytest.importorskip("httpx")
    identity = pytest.importorskip("azure.identity")
    subscription = str(uuid4())
    folder = tmp_path / ".build"
    folder.mkdir()
    config = {
        "subscription_id": subscription, "resource_group": "agentic-itops-us93-rg",
        "review_origin": "https://app-review-example.azurewebsites.net",
        "identities": {name: {
            "principal_id": str(uuid4()), "client_id": str(uuid4()),
            "resource_id": (f"/subscriptions/{subscription}/resourceGroups/agentic-itops-us93-rg/"
                            f"providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"),
        } for name in ("review", "worker", "bootstrap")},
    }
    config_file, journal = folder / "runtime.json", folder / "grant.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    scope = f"/subscriptions/{subscription}/resourceGroups/source-rg/providers/Microsoft.OperationalInsights/workspaces/source"
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["grant", "--runtime-config", str(config_file),
                                    "--workspace-resource-id", scope, "--journal", str(journal), "--apply-approved"])
    class Credential:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_token(self, scope): return SimpleNamespace(token="offline")
    assignment, writes = {}, []
    def transport(request):
        if request.method == "PUT":
            writes.append(json.loads(request.content))
            assignment.update(writes[-1])
            return httpx.Response(201, json=assignment)
        if "roleDefinitions" in request.url.path:
            return httpx.Response(200, json={"properties": {
                "roleName": "Log Analytics Data Reader", "permissions": [{
                    "actions": ["*/read"] if broad_role else ["Microsoft.OperationalInsights/workspaces/read",
                                                            "Microsoft.OperationalInsights/workspaces/query/read"],
                    "dataActions": [script.DATA_ACTION],
                }],
            }})
        if "userAssignedIdentities" in request.url.path:
            worker = config["identities"]["worker"]
            return httpx.Response(200, json={"properties": {
                "principalId": worker["principal_id"], "clientId": worker["client_id"],
            }})
        return httpx.Response(200, json=assignment) if assignment else httpx.Response(404)
    original_client = httpx.Client
    monkeypatch.setattr(identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs))
    if broad_role:
        with pytest.raises(ValueError, match="data-reader role"):
            script.main()
        assert not writes
    else:
        script.main()
        script.main()
        assert len(writes) == 1
        assert writes[0]["properties"]["condition"] == script.CONDITION
        assert writes[0]["properties"]["conditionVersion"] == "2.0"
        assert json.loads(journal.read_text())["state"] == "verified"
