from uuid import uuid4

import pytest

from deployment.manifest import create


def outputs():
    suffix = "abc123def4567"
    hosts = {
        "review": f"app-review-{suffix}.azurewebsites.net",
        "bootstrap": f"app-bootstrap-{suffix}.azurewebsites.net",
        **{name: f"func-{name}-{suffix}.azurewebsites.net" for name in ("worker", "tools", "publisher")},
    }
    return {
        "ITOPS_SERVICE_IDENTITIES": {"value": [
            {"service": name, "clientId": str(uuid4()), "principalId": str(uuid4())}
            for name in ("review", "worker", "tools", "publisher", "bootstrap")
        ]},
        "SERVICE_BOOTSTRAP_NAME": {"value": f"app-bootstrap-{suffix}"},
        "ITOPS_SERVICE_HOSTNAMES": {"value": hosts},
        "AZURE_KEY_VAULT_NAME": {"value": f"kv-itops-{suffix}"},
        "ITOPS_PRIVATE_HOSTNAMES": {"value": []},
    }


def test_arm_output_name_casing_does_not_change_manifest_targets(tmp_path):
    original = outputs()
    actual_arm = {key.lower(): value for key, value in original.items()}
    args = ("agentic-itops-us93", {}, {}, tmp_path, "2030-01-01T00:00:00Z", ["migrate"])
    left, right = create(original, *args), create(actual_arm, *args)
    left.pop("deployment_id")
    right.pop("deployment_id")
    assert left == right


def test_ambiguous_output_case_fails_closed(tmp_path):
    value = outputs()
    value["servicE_BOOTSTRAP_NAME"] = value["SERVICE_BOOTSTRAP_NAME"]
    with pytest.raises(ValueError, match="case-ambiguous"):
        create(value, "agentic-itops-us93", {}, {}, tmp_path, "2030-01-01T00:00:00Z", ["migrate"])
