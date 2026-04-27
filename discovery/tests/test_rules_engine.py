"""Smoke tests for the rules engine (no Azure calls)."""

from pathlib import Path

from discovery.rules_engine import evaluate, load_rules

RULES = Path(__file__).resolve().parent.parent / "rules" / "default.yaml"


def test_load_default_rules():
    rules = load_rules(RULES)
    assert rules
    ids = {r.id for r in rules}
    assert "APPSVC-001" in ids


def test_appservice_missing_diagnostics_triggers_finding():
    rules = load_rules(RULES)
    resources = [
        {
            "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Web/sites/app1",
            "name": "app1",
            "type": "microsoft.web/sites",
            "subscriptionId": "s",
            "resourceGroup": "rg",
            "location": "eastus",
            "tags": {"owner": "team-a"},
            "hasDiagnosticSettings": False,
        }
    ]
    findings = evaluate(resources, rules)
    rule_ids = {f.rule_id for f in findings}
    assert "APPSVC-001" in rule_ids


def test_keyvault_with_diag_but_no_workspace_sink():
    rules = load_rules(RULES)
    resources = [
        {
            "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv1",
            "name": "kv1",
            "type": "microsoft.keyvault/vaults",
            "subscriptionId": "s",
            "resourceGroup": "rg",
            "location": "eastus",
            "tags": {},
            "hasDiagnosticSettings": True,
            "diagnosticSinkWorkspaceIds": [None],
        }
    ]
    findings = evaluate(resources, rules)
    assert any(f.rule_id == "KV-002" for f in findings)
