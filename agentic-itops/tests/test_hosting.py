import json

import pytest

pytest.importorskip("flask", reason="install requirements-consumer.txt")
pytest.importorskip("jwt", reason="install requirements-consumer.txt")

from agentic_itops import hosting
from agentic_itops.consumer.contracts import ContractError


def configuration():
    source = {"producer_key": "producer-a", "run_id": "run-a ", "domain": "energy",
              "surfaces": ["upstream"]}
    return {
        "ITOPS_SQL_SERVER": "test.database.windows.net", "ITOPS_SQL_DATABASE": "consumer",
        "AZURE_CLIENT_ID": "00000000-0000-0000-0000-000000000001",
        "ENTRA_TENANT_ID": "00000000-0000-0000-0000-000000000002",
        "ENTRA_AUDIENCE": "test-review", "ITOPS_PUBLIC_ORIGIN": "https://review.example.test",
        "ITOPS_REVIEW_SECRET_KEY": "offline-test-secret-never-production" * 2,
        "ITOPS_ALLOW_EASYAUTH": "true",
        "ITOPS_REVIEW_SCOPES_JSON": json.dumps({
            "sources": [source],
            "entitlements": [{"oid": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                              "producer_key": "producer-a", "run_id": "run-a "}],
        }),
    }


def test_production_factory_has_no_anonymous_or_database_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(hosting, "verify_sql_native_runtime", lambda: {"provider": "test"})
    def connection():
        raise AssertionError("factory must not query or migrate SQL during configuration")
    monkeypatch.setattr(hosting, "managed_identity_sql_factory",
                        lambda *args: calls.append(args) or connection)
    app = hosting.create_review_app(configuration())
    assert app.testing is False and len(calls) == 1
    assert app.test_client().get("/incidents").status_code == 401
    assert calls[0][2] == configuration()["AZURE_CLIENT_ID"]


@pytest.mark.parametrize("name,value", [
    ("ITOPS_SQL_SERVER", ""), ("ITOPS_REVIEW_SECRET_KEY", "@Microsoft.KeyVault(SecretUri=https://unresolved)"),
    ("AZURE_CLIENT_ID", "${AZURE_CLIENT_ID}"), ("ITOPS_ALLOW_EASYAUTH", "yes"),
])
def test_missing_or_unresolved_configuration_fails_before_startup(name, value):
    environment = configuration()
    environment[name] = value
    with pytest.raises(ContractError):
        hosting.create_review_app(environment)


def test_scope_config_preserves_opaque_ids_and_canonicalizes_only_actor_uuids():
    catalog, entitlements = hosting._scope_configuration(configuration()["ITOPS_REVIEW_SCOPES_JSON"])
    assert ("producer-a", "run-a ") in catalog
    assert entitlements["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"] == {("producer-a", "run-a ")}
    value = json.loads(configuration()["ITOPS_REVIEW_SCOPES_JSON"])
    value["entitlements"][0]["run_id"] = "run-a"
    with pytest.raises(ContractError, match="explicit source"):
        hosting._scope_configuration(json.dumps(value))
