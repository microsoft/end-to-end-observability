import json

import pytest

from scripts.setup_entra_apps import REVIEW_ROLES, SERVICE_ROLES, application_manifest, save


@pytest.mark.parametrize("review,roles", [(True, REVIEW_ROLES), (False, SERVICE_ROLES)])
def test_only_approved_single_tenant_roles_without_graph_access(review, roles):
    name = f"agentic-itops-us93-{'review' if review else 'services'}"
    manifest = application_manifest(name, review=review)
    assert manifest == application_manifest(name, review=review)
    assert manifest["signInAudience"] == "AzureADMyOrg"
    assert manifest["api"]["requestedAccessTokenVersion"] == 2
    assert manifest["requiredResourceAccess"] == []
    assert [item["value"] for item in manifest["appRoles"]] == list(roles)
    assert len({item["id"] for item in manifest["appRoles"]}) == len(roles)
    assert all(item["allowedMemberTypes"] == ["User" if review else "Application"]
               for item in manifest["appRoles"])
    assert "passwordCredentials" not in manifest


def test_refuse_unapproved_application_names():
    with pytest.raises(ValueError):
        application_manifest("other-tenant-app", review=True)


def test_journal_replacement_preserves_complete_json(tmp_path):
    path = tmp_path / "journal.json"
    path.write_text("{}", encoding="utf-8")
    state = {"pending_step": "create:agentic-itops-us93-review"}
    save(path, state)
    assert json.loads(path.read_text(encoding="utf-8")) == state
    assert not path.with_suffix(".json.pending").exists()


@pytest.mark.parametrize("ambiguous_patch", [False, True])
def test_empty_patch_and_ambiguous_patch_recovery_do_not_recreate_objects(tmp_path, monkeypatch, ambiguous_patch):
    httpx = pytest.importorskip("httpx")
    identity = pytest.importorskip("azure.identity")
    from types import SimpleNamespace
    from uuid import uuid4
    from scripts.setup_entra_apps import GraphFailure, execute

    class Credential:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get_token(self, scope):
            return SimpleNamespace(token="offline-test")

    calls, objects, assignments = [], {}, []
    def transport(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            if request.url.path.endswith("/appRoleAssignedTo"):
                return httpx.Response(200, json={"value": assignments})
            return httpx.Response(200, json=objects[request.url.path])
        value = json.loads(request.content)
        if request.method == "PATCH":
            objects[request.url.path].update(value)
            if ambiguous_patch:
                raise httpx.ReadTimeout("test-only lost response")
            return httpx.Response(204)
        if request.url.path == "/v1.0/applications":
            result = {**value, "id": str(uuid4()), "appId": str(uuid4()), "identifierUris": []}
        else:
            result = {**value, "id": str(uuid4())}
        objects[request.url.path + "/" + result["id"]] = result
        if request.url.path.endswith("/appRoleAssignedTo"):
            assignments.append(result)
        return httpx.Response(201, json=result)

    client_type = httpx.Client
    monkeypatch.setattr(identity, "AzureCliCredential", Credential)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(transport), **kwargs,
    ))
    path = tmp_path / "journal.json"
    tenant, reviewer = str(uuid4()), str(uuid4())
    if ambiguous_patch:
        with pytest.raises(GraphFailure, match="uncertain outcome"):
            execute(tenant, reviewer, path)
        state = execute(tenant, reviewer, path, resume=True)
    else:
        state = execute(tenant, reviewer, path)
    assert state["status"] == "created_requires_readback_and_configuration"
    assert state["pending_step"] is None
    assert len(state["applications"]) == 2
    assert sum(method == "PATCH" for method, _ in calls) == 1
    assert calls.count(("POST", "/v1.0/applications")) == 2
    assert calls.count(("POST", "/v1.0/servicePrincipals")) == 2


@pytest.fixture
def runtime_config():
    from uuid import uuid4
    subscription = str(uuid4())
    return {
        "subscription_id": subscription, "resource_group": "agentic-itops-us93-rg",
        "review_origin": "https://agentic-itops-us93-review.azurewebsites.net",
        "identities": {name: {
            "principal_id": str(uuid4()), "client_id": str(uuid4()),
            "resource_id": (f"/subscriptions/{subscription}/resourceGroups/agentic-itops-us93-rg/"
                            f"providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"),
        } for name in ("review", "worker", "bootstrap")},
    }


@pytest.mark.parametrize("origin", [
    "http://agentic-itops-us93-review.azurewebsites.net",
    "https://agentic-itops-us93-review.azurewebsites.net/",
    "https://agentic-itops-us93-review.azurewebsites.net?redirect=evil",
    "https://user@agentic-itops-us93-review.azurewebsites.net",
    "https://agentic-itops-us93-review.azurewebsites.net:443",
    "https://example.com",
])
def test_runtime_plan_rejects_nonexact_origins(runtime_config, origin):
    from scripts.setup_entra_apps import runtime_plan
    with pytest.raises(ValueError):
        runtime_plan({**runtime_config, "review_origin": origin})


def test_runtime_plan_rejects_shared_or_out_of_scope_identity(runtime_config):
    import copy
    from scripts.setup_entra_apps import runtime_plan
    shared = copy.deepcopy(runtime_config)
    shared["identities"]["worker"] = shared["identities"]["review"]
    outside = copy.deepcopy(runtime_config)
    outside["identities"]["worker"]["resource_id"] = outside["identities"]["worker"]["resource_id"].replace(
        "agentic-itops-us93-rg", "existing-producer-rg")
    for config in (shared, outside):
        with pytest.raises(ValueError):
            runtime_plan(config)


@pytest.mark.parametrize("lost_role_response", [False, True])
def test_runtime_roles_read_back_and_reconcile_without_duplicate_grants(runtime_config, lost_role_response):
    from scripts.setup_entra_apps import GraphFailure, configure_runtime, runtime_plan
    from uuid import uuid4
    plan = runtime_plan(runtime_config)
    review_id, service_id = str(uuid4()), str(uuid4())
    state = {"applications": {
        "agentic-itops-us93-review": {"object_id": review_id},
        "agentic-itops-us93-services": {
            "service_principal_id": service_id,
            "roles": {role: str(uuid4()) for role in SERVICE_ROLES},
        },
    }, "pending_step": None, "completed_steps": []}
    assignments, writes = [], []
    web = {"redirectUris": [], "implicitGrantSettings": {"enableIdTokenIssuance": True}}
    identities = {value["principal_id"]: value for value in plan["identities"].values()}
    lost = False
    def read(endpoint):
        if endpoint == f"/applications/{review_id}":
            return {"web": web.copy()}
        if endpoint.endswith("/appRoleAssignedTo"):
            return {"value": [item.copy() for item in assignments]}
        value = identities[endpoint.rsplit("/", 1)[-1]]
        return {"servicePrincipalType": "ManagedIdentity", "appId": value["client_id"],
                "alternativeNames": ["isExplicit=True", value["resource_id"].lower()]}
    def write(step, endpoint, body, *, method="POST"):
        nonlocal lost
        state["pending_step"] = step
        writes.append((method, endpoint))
        if method == "PATCH":
            web.update(body["web"])
            return {}
        result = {**body, "id": str(uuid4())}
        assignments.append(result)
        if lost_role_response and not lost:
            lost = True
            raise GraphFailure("test lost response after the actual write")
        return result
    def completed(step):
        if step not in state["completed_steps"]:
            state["completed_steps"].append(step)
        if state["pending_step"] == step:
            state["pending_step"] = None
    if lost_role_response:
        with pytest.raises(GraphFailure, match="test lost response"):
            configure_runtime(state, plan, read, write, completed)
    configure_runtime(state, plan, read, write, completed)
    configure_runtime(state, plan, read, write, completed)
    assert len(assignments) == 6
    assert len(writes) == 7
    assert len(state["runtime_assignments"]) == 6
    assert state["pending_step"] is None
    assert web["implicitGrantSettings"]["enableIdTokenIssuance"] is True


def test_runtime_rejects_unverified_managed_identity_before_writes(runtime_config):
    from scripts.setup_entra_apps import GraphFailure, configure_runtime, runtime_plan
    state = {"applications": {"agentic-itops-us93-review": {}, "agentic-itops-us93-services": {}}}
    def no_write(*args, **kwargs):
        raise AssertionError("must fail before mutation")
    with pytest.raises(GraphFailure, match="identity read-back"):
        configure_runtime(state, runtime_plan(runtime_config), lambda _: {"servicePrincipalType": "Application"},
                          no_write, no_write)


@pytest.mark.parametrize("lost_response", [False, True])
def test_projection_contract_upgrade_reconciles_one_patch(lost_response):
    import copy
    from uuid import uuid4
    from scripts.setup_entra_apps import GraphFailure, upgrade_projection_contract
    name = "agentic-itops-us93-services"
    entry = {"object_id": str(uuid4()), "client_id": str(uuid4()), "service_principal_id": str(uuid4())}
    application = {**application_manifest(name, review=False), "appId": entry["client_id"],
                   "optionalClaims": None}
    for role in application["appRoles"]:
        role["origin"] = "Application"
    state = {"applications": {name: entry}, "pending_step": None}
    writes = []
    def read(_):
        return copy.deepcopy(application)
    def write(step, endpoint, body, **kwargs):
        writes.append(body)
        state["pending_step"] = step
        application.update(body)
        if lost_response:
            raise GraphFailure("test lost projection response")
    def completed(step):
        if state["pending_step"] == step:
            state["pending_step"] = None
    if lost_response:
        with pytest.raises(GraphFailure, match="test lost"):
            upgrade_projection_contract(state, read, write, completed)
    upgrade_projection_contract(state, read, write, completed)
    upgrade_projection_contract(state, read, write, completed)
    assert len(writes) == 1
    assert state["projection_contract"] is True and state["pending_step"] is None
    assert set(entry["roles"]) == {*SERVICE_ROLES, "Results.Project"}
    assert application["optionalClaims"]["accessToken"][0]["name"] == "idtyp"
    assert application["requiredResourceAccess"] == []


def test_projection_role_is_only_added_to_worker_matrix(runtime_config):
    from scripts.setup_entra_apps import runtime_plan
    plan = runtime_plan(runtime_config, projection=True)
    assert plan["identities"]["worker"]["roles"] == ["Tools.Invoke", "Results.Project"]
    assert all("Results.Project" not in value["roles"] for name, value in plan["identities"].items()
               if name != "worker")
