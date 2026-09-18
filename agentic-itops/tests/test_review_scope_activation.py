from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from deployment.bootstrap import BootstrapError, _scope_addition, canonical, initialize_review_scopes, load_manifest
from tests.test_service_deployment import environment, manifest


def catalogs():
    source = {"producer_key": "a" * 64, "run_id": "old-run", "domain": "downstream",
              "surfaces": ["downstream", "downstream-inventory"]}
    actor = {"producer_key": "a" * 64, "run_id": "old-run", "domain": "downstream",
             "oid": "11111111-1111-1111-1111-111111111111"}
    previous = {"sources": [source], "entitlements": [actor]}
    desired = {"sources": [source, {**source, "run_id": "new-run", "surfaces": ["downstream"]}],
               "entitlements": [actor, {**actor, "run_id": "new-run"}]}
    return previous, desired


def test_catalog_activation_preserves_previous_scope_and_actor():
    previous, desired = catalogs()
    _scope_addition(previous, desired)
    for field, replacement in (("producer_key", "b" * 64), ("domain", "upstream"),
                               ("surfaces", ["power-grid"])):
        modified = deepcopy(desired)
        modified["sources"][1][field] = replacement
        if field != "surfaces":
            modified["entitlements"][1][field] = replacement
        with pytest.raises(BootstrapError):
            _scope_addition(previous, modified)
    modified = deepcopy(desired)
    modified["entitlements"][1]["oid"] = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(BootstrapError, match="principal"):
        _scope_addition(previous, modified)
    modified = deepcopy(desired)
    modified["sources"].pop(0)
    with pytest.raises(BootstrapError, match="append"):
        _scope_addition(previous, modified)


def test_scope_only_operation_requires_hash_and_cannot_rotate_credentials(tmp_path):
    value = manifest()
    value["operations"] = ["review-scopes"]
    value["previous_review_scopes_sha256"] = "a" * 64
    path = tmp_path / "manifest.json"
    raw = canonical(value)
    path.write_bytes(raw)
    assert load_manifest(path, environment(value, raw)) == value
    value["operations"].append("secrets")
    raw = canonical(value)
    path.write_bytes(raw)
    with pytest.raises(BootstrapError, match="rotate credentials"):
        load_manifest(path, environment(value, raw))


def test_scope_secret_update_is_read_back_and_idempotent_without_other_secrets(monkeypatch):
    previous, desired = catalogs()
    storage = {"review-scopes": canonical(previous).decode()}
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["retry_total"] == 0
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get_secret(self, name):
            calls.append(("get", name))
            assert name == "review-scopes"
            return SimpleNamespace(value=storage[name], properties=SimpleNamespace(version="verified-version"))
        def set_secret(self, name, value):
            calls.append(("set", name))
            assert name == "review-scopes"
            storage[name] = value

    monkeypatch.setattr("azure.keyvault.secrets.SecretClient", Client)
    value = {"vault_url": "https://unit.vault.azure.net", "review_scopes": desired,
             "previous_review_scopes_sha256": sha256(canonical(previous)).hexdigest()}
    result = initialize_review_scopes(value, object())
    assert result["secret_names"] == ["review-scopes"]
    assert result["current_sha256"] == sha256(canonical(desired)).hexdigest()
    assert initialize_review_scopes(value, object()) == result
    assert calls.count(("set", "review-scopes")) == 1
    storage["review-scopes"] = canonical({"sources": [], "entitlements": []}).decode()
    with pytest.raises(BootstrapError, match="changed since approval"):
        initialize_review_scopes(value, object())
    assert calls.count(("set", "review-scopes")) == 1
