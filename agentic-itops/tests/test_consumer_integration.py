from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.contracts import ContractError, Observation
from agentic_itops.consumer.detectors import analyze_series
from agentic_itops.consumer.incidents import derive_candidates
from agentic_itops.consumer.sql_connect import managed_identity_sql_factory
from tests.test_consumer_detectors import series


def candidate_source(surface="upstream"):
    observations, policy, start, reference = series(surface, "step-up")
    analysis = analyze_series(observations, policy, baseline_start=start, reference_time=reference)
    return observations, analysis


def test_detected_episode_becomes_one_evidence_bound_nonoperational_candidate():
    observations, analysis = candidate_source()
    candidates = derive_candidates(analysis, observations)
    assert len(candidates) == 1
    value = candidates[0].value
    assert value["incident_id"] == analysis["episodes"][0]["episode_id"]
    assert value["severity"] is None
    assert value["target"]["id"].startswith("urn:agentic-itops:synthetic-entity:")
    assert not value["target"]["id"].startswith("/subscriptions/")
    assert value["source"]["domain"] == "upstream"
    assert len(value["evidence_refs"]) == 16
    assert value["execution_allowed"] is False
    assert candidates == derive_candidates(analysis, list(reversed(observations)))


def test_missing_or_wrong_scope_cannot_create_a_candidate():
    observations, analysis = candidate_source()
    with pytest.raises(ContractError, match="missing"):
        derive_candidates(analysis, observations[1:])
    changed = deepcopy(analysis)
    changed["run_id"] = "another-run"
    with pytest.raises(ContractError, match="scope"):
        derive_candidates(changed, observations)


def test_normal_rows_are_not_adapted_as_fired_alerts():
    observations, policy, start, reference = series("upstream", "normal")
    analysis = analyze_series(observations, policy, baseline_start=start, reference_time=reference)
    assert derive_candidates(analysis, observations) == ()


def test_service_delivery_timestamp_does_not_change_candidate_semantics():
    observations, analysis = candidate_source()
    first = derive_candidates(analysis, observations)
    row = observations[0].row
    row["TimeGenerated"] = "2026-09-15T00:00:00Z"
    repeated = [Observation.parse(observations[0].producer, row)] + observations[1:]
    assert derive_candidates(analysis, repeated) == first


@pytest.mark.parametrize("server,database,client_id", [
    ("sql.invalid", "db", "00000000-0000-0000-0000-000000000001"),
    ("safe.database.windows.net;Password=x", "db", "00000000-0000-0000-0000-000000000001"),
    ("safe.database.windows.net", "db;Authentication=SqlPassword", "00000000-0000-0000-0000-000000000001"),
    ("safe.database.windows.net", "db", "00000000-0000-0000-0000-000000000000"),
    ("safe.database.windows.net", "db", "not-a-client-id"),
])
def test_sql_settings_reject_fallbacks_and_connection_string_injection(server, database, client_id):
    with pytest.raises(ContractError):
        managed_identity_sql_factory(server, database, client_id)


def test_sql_factory_uses_explicit_identity_and_encrypted_short_lived_connections(monkeypatch):
    driver = pytest.importorskip("mssql_python")
    calls = []
    monkeypatch.setattr(driver, "pooling", lambda **kwargs: calls.append(("pool", kwargs)))
    connection = SimpleNamespace(timeout=None)
    monkeypatch.setattr(driver, "connect",
                        lambda text, **kwargs: calls.append(("connect", text, kwargs)) or connection)
    factory = managed_identity_sql_factory(
        "safe.database.windows.net", "consumer", "00000000-0000-0000-0000-000000000001")
    assert factory() is connection and connection.timeout == 30
    assert calls[0] == ("pool", {"enabled": False})
    _, text, settings = calls[1]
    assert "Encrypt=yes;TrustServerCertificate=no;" in text
    assert "Authentication=ActiveDirectoryMSI;UID=00000000-0000-0000-0000-000000000001;" in text
    assert "Password=" not in text and "Default" not in text
    assert "token_provider" not in settings and settings["autocommit"] is False
