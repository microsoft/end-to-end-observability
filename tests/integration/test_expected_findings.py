r"""Integration test: assert the discovery report matches expected findings.

Prerequisites (this test is opt-in, gated by env vars so it never runs in
the default `pytest discovery/tests` invocation):

    $env:OBS_TEST_REPORT   = ".\out\discovery-report.json"
    $env:OBS_TEST_EXPECTED = ".\tests\fixtures\azure\expected-findings.json"
    pytest tests/integration -q

`expected-findings.json` is written by Deploy-TestFixture.ps1 and contains
the (resourceName, ruleIds[]) pairs the fixture should produce.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPORT_ENV = "OBS_TEST_REPORT"
EXPECTED_ENV = "OBS_TEST_EXPECTED"


def _require_env(name: str) -> Path:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; integration test skipped.")
    path = Path(value)
    if not path.exists():
        pytest.skip(f"{name}={value} does not exist; skipping.")
    return path


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(_require_env(REPORT_ENV).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def expected() -> list[dict]:
    return json.loads(_require_env(EXPECTED_ENV).read_text(encoding="utf-8"))


def _findings_by_name(report: dict) -> dict[str, set[str]]:
    by_name: dict[str, set[str]] = {}
    for f in report.get("findings", []):
        by_name.setdefault(f["resource_name"], set()).add(f["rule_id"])
    return by_name


def test_every_expected_rule_fires(report, expected):
    actual = _findings_by_name(report)
    missing: list[str] = []
    for item in expected:
        name = item["resourceName"]
        for rid in item.get("ruleIds", []):
            if rid not in actual.get(name, set()):
                missing.append(f"{name} -> {rid}")
    assert not missing, f"Missing expected findings: {missing}"


def test_negative_controls_have_no_findings(report, expected):
    actual = _findings_by_name(report)
    leaked: list[str] = []
    for item in expected:
        if item.get("ruleIds"):
            continue
        name = item["resourceName"]
        if actual.get(name):
            leaked.append(f"{name} -> {sorted(actual[name])}")
    assert not leaked, f"Negative controls produced findings: {leaked}"


def test_no_unexpected_rule_ids(report, expected):
    """Catches accidental rule-id renames or new rules without test coverage."""
    expected_ids: set[str] = set()
    for item in expected:
        expected_ids.update(item.get("ruleIds", []))
    actual_ids = {f["rule_id"] for f in report.get("findings", [])}
    # Only assert against fixtures we deployed; ignore findings on resources
    # that weren't part of the fixture.
    fixture_names = {item["resourceName"] for item in expected}
    fixture_findings = [
        f for f in report.get("findings", []) if f["resource_name"] in fixture_names
    ]
    fixture_ids = {f["rule_id"] for f in fixture_findings}
    unexpected = fixture_ids - expected_ids
    assert not unexpected, f"Fixture produced unexpected rule ids: {unexpected}"
