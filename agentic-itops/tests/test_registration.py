from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("azure.ai.projects", reason="install requirements-foundry.txt")
pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")

from agentic_itops.portfolio import AGENT_FOLDERS, compile_portfolio
from agentic_itops.registration import (
    FoundryVersionService, RegisteredVersion, RegistrationError, RegistrationJournal,
    bind_registered_runtime_agents, prepare_registration, project_endpoint, register_portfolio,
)
from agentic_itops.runtime import bind_runtime_agents
from tests.test_portfolio import ROOT
from tests.test_trusted_search_dispatch import trusted_config


ENDPOINT = "https://registration-test.services.ai.azure.com/api/projects/portfolio-test"
MODEL = "gpt-5-6-luna"


@pytest.fixture
def portfolio():
    config = trusted_config()
    config["models"] = {"gpt-4o": MODEL}
    return compile_portfolio(ROOT, config)


@pytest.fixture
def plan(portfolio):
    return prepare_registration(portfolio, ENDPOINT, MODEL, sampling="model_defaults")


class Service:
    def __init__(self):
        self.project_endpoint = ENDPOINT
        self.versions = {}
        self.creates = []
        self.gets = []
        self.timeout_after_create = False
        self.readback_change = False

    def create(self, name, definition, metadata):
        version = str(len(self.versions.get(name, [])) + 1)
        result = RegisteredVersion(name, version, deepcopy(definition), dict(metadata), "active")
        self.versions.setdefault(name, []).append(result)
        self.creates.append(name)
        if self.timeout_after_create:
            self.timeout_after_create = False
            raise TimeoutError("ambiguous external completion")
        return result

    def get(self, name, version):
        self.gets.append((name, version))
        result = next(item for item in self.versions[name] if item.version == version)
        if self.readback_change:
            return replace(result, definition={**result.definition, "instructions": "changed"})
        return result

    def matching(self, name, metadata):
        return [item for item in self.versions.get(name, [])
                if all(item.metadata.get(key) == value for key, value in metadata.items())]


def test_all_nine_explicit_luna_settings_preserve_tools_and_schemas(portfolio, plan):
    again = prepare_registration(portfolio, ENDPOINT, MODEL, sampling="model_defaults")
    assert plan == again
    assert len(plan.document["agents"]) == 9
    for entry in plan.document["agents"]:
        value = entry["definition"]
        assert value["model"] == MODEL
        assert "temperature" not in value and "top_p" not in value
        assert entry["source_sampling"]["temperature"] == portfolio.agent(entry["name"]).temperature
        assert value["text"]["format"]["schema"] == portfolio.agent(entry["name"]).output_schema
        assert all(tool["type"] == "function" and tool["parameters"]["properties"] == {}
                   for tool in value["tools"])
    source = prepare_registration(portfolio, ENDPOINT, MODEL, sampling="source")
    assert source.sha256 != plan.sha256
    assert all("temperature" in item["definition"] for item in source.document["agents"])


@pytest.mark.parametrize("endpoint", [
    "http://host.services.ai.azure.com/api/projects/x",
    ENDPOINT + "/", ENDPOINT + "?token=value", ENDPOINT + "#x",
    ENDPOINT.replace("registration-test", "user:password@registration-test"),
    ENDPOINT.replace("/portfolio-test", "/../other"),
    ENDPOINT.replace(".azure.com", ".azure.com.evil.net"),
    ENDPOINT.replace("/portfolio-test", "/portfolio%2Ftest"),
    ENDPOINT + "\n", "",
])
def test_invalid_endpoints_fail_before_authentication(endpoint):
    with pytest.raises(RegistrationError):
        project_endpoint(endpoint)


def test_partial_or_unbound_portfolio_and_wrong_model_rejected(portfolio):
    with pytest.raises(RegistrationError):
        prepare_registration(compile_portfolio(ROOT), ENDPOINT, MODEL, sampling="model_defaults")
    with pytest.raises(RegistrationError):
        prepare_registration(replace(portfolio, agents=portfolio.agents[:-1]), ENDPOINT, MODEL,
                             sampling="model_defaults")
    with pytest.raises(RegistrationError):
        prepare_registration(portfolio, ENDPOINT, "different-model", sampling="model_defaults")
    with pytest.raises(RegistrationError):
        prepare_registration(portfolio, ENDPOINT, MODEL, sampling="automatic")


def test_nine_exact_readbacks_resume_without_creating_and_bind_runtime(tmp_path, plan, portfolio):
    path = tmp_path / "journal.sqlite3"
    service = Service()
    journal = RegistrationJournal(path, plan)
    try:
        receipt = register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        assert set(receipt["versions"]) == set(AGENT_FOLDERS.values())
        assert receipt["live_acceptance"] is False and receipt["execution_allowed"] is False
        assert len(service.creates) == len(service.gets) == 9
        assert len(bind_runtime_agents(portfolio, receipt["versions"])) == 9
        bound = bind_registered_runtime_agents(portfolio, plan, receipt)
        assert len(bound) == 9
        assert all(item.definition["provenance"]["registration_plan_sha256"] == plan.sha256
                   for item in bound.values())
    finally:
        journal.close()
    journal = RegistrationJournal(path, plan)
    try:
        assert register_portfolio(plan, journal, service, approved_sha256=plan.sha256) == receipt
        assert len(service.creates) == 9 and len(service.gets) == 18
    finally:
        journal.close()


def test_changed_plan_or_missing_approval_never_calls_service(tmp_path, plan, portfolio):
    path = tmp_path / "journal.sqlite3"
    service = Service()
    journal = RegistrationJournal(path, plan)
    try:
        with pytest.raises(RegistrationError):
            register_portfolio(plan, journal, service, approved_sha256="0" * 64)
        assert not service.creates
    finally:
        journal.close()
    changed = prepare_registration(portfolio, ENDPOINT, MODEL, sampling="source")
    with pytest.raises(RegistrationError):
        RegistrationJournal(path, changed)


def test_ambiguous_create_reconciles_owned_version_without_duplicate(tmp_path, plan):
    path = tmp_path / "journal.sqlite3"
    service = Service()
    service.timeout_after_create = True
    journal = RegistrationJournal(path, plan)
    first = plan.document["agents"][0]["name"]
    try:
        with pytest.raises(TimeoutError):
            register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        assert journal.row(first)["state"] == "attempting"
        assert journal.row(first)["error_type"] == "TimeoutError"
    finally:
        journal.close()
    journal = RegistrationJournal(path, plan)
    try:
        assert len(register_portfolio(plan, journal, service, approved_sha256=plan.sha256)["versions"]) == 9
        assert service.creates.count(first) == 1
    finally:
        journal.close()


@pytest.mark.parametrize("matches", [0, 2])
def test_unresolved_or_duplicated_attempt_stops_without_retry(tmp_path, plan, matches):
    journal = RegistrationJournal(tmp_path / "journal.sqlite3", plan)
    service = Service()
    service.timeout_after_create = True
    first = plan.document["agents"][0]["name"]
    try:
        with pytest.raises(TimeoutError):
            register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        service.versions[first] *= matches
        with pytest.raises(RegistrationError, match="uncertain create outcome"):
            register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        assert len(service.creates) == 1
    finally:
        journal.close()


def test_readback_mismatch_is_not_verified_and_is_recoverable(tmp_path, plan):
    journal = RegistrationJournal(tmp_path / "journal.sqlite3", plan)
    service = Service()
    service.readback_change = True
    first = plan.document["agents"][0]["name"]
    try:
        with pytest.raises(RegistrationError, match="remote definition differs"):
            register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        assert journal.row(first)["state"] == "created"
        service.readback_change = False
        register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        assert len(service.creates) == 9
    finally:
        journal.close()


def test_concurrent_journal_cannot_start_same_creation(tmp_path, plan):
    path = tmp_path / "journal.sqlite3"
    first = RegistrationJournal(path, plan)
    second = RegistrationJournal(path, plan)
    name = plan.document["agents"][0]["name"]
    try:
        attempt = first.begin(name)
        with pytest.raises(RegistrationError):
            second.begin(name)
        with pytest.raises(RegistrationError):
            second.created(name, "unowned-attempt", "1")
        assert second.row(name)["attempt"] == attempt
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("change", [
    {"version": "latest"}, {"version": 1}, {"name": "other-agent"},
    {"draft": True}, {"status": "failed"}, {"status": "creating"},
    {"metadata": {}},
])
def test_invalid_receipt_does_not_advance(tmp_path, plan, change):
    class InvalidService(Service):
        def create(self, name, definition, metadata):
            return replace(super().create(name, definition, metadata), **change)
    journal = RegistrationJournal(tmp_path / "journal.sqlite3", plan)
    try:
        with pytest.raises(RegistrationError):
            register_portfolio(plan, journal, InvalidService(), approved_sha256=plan.sha256)
        assert journal.row(plan.document["agents"][0]["name"])["state"] == "attempting"
    finally:
        journal.close()


def test_sdk_adapter_preserves_exact_definition_and_disables_retries(plan):
    from azure.ai.projects.models import AgentVersionDetails, PromptAgentDefinition

    calls = []
    entry = plan.document["agents"][0]
    metadata = {"itops_attempt": "test"}
    receipt = AgentVersionDetails({
        "name": entry["name"], "version": "1", "metadata": metadata,
        "definition": entry["definition"], "status": "active", "draft": False,
    })
    def create(**kwargs):
        calls.append(kwargs)
        assert isinstance(kwargs["definition"], PromptAgentDefinition)
        assert kwargs["definition"].as_dict() == entry["definition"]
        return receipt
    client = SimpleNamespace(_config=SimpleNamespace(endpoint=ENDPOINT),
                             agents=SimpleNamespace(create_version=create))
    result = FoundryVersionService(client).create(entry["name"], entry["definition"], metadata)
    assert result.version == "1" and result.status == "active"
    assert calls[0]["retry_total"] == 0 and calls[0]["draft"] is False


def test_reconciliation_scan_is_bounded(plan):
    rows = [SimpleNamespace(metadata={}) for _ in range(3)]
    client = SimpleNamespace(_config=SimpleNamespace(endpoint=ENDPOINT),
                             agents=SimpleNamespace(list_versions=lambda **kwargs: iter(rows)))
    with pytest.raises(RegistrationError, match="scan bound"):
        FoundryVersionService(client, max_versions=2).matching("ops-orchestrator", {"itops_attempt": "owned"})


def test_wrong_project_never_creates_a_version(tmp_path, plan):
    journal = RegistrationJournal(tmp_path / "journal.sqlite3", plan)
    service = Service()
    service.project_endpoint = ENDPOINT + "-other"
    try:
        with pytest.raises(RegistrationError, match="different project"):
            register_portfolio(plan, journal, service, approved_sha256=plan.sha256)
        assert not service.creates
    finally:
        journal.close()


def test_cli_offline_plan_never_authenticates_and_refuses_overwrite(tmp_path, portfolio, monkeypatch):
    from scripts import register_portfolio as command
    import azure.identity

    def forbidden(*args, **kwargs):
        raise AssertionError("offline preparation attempted authentication")
    monkeypatch.setattr(azure.identity, "AzureCliCredential", forbidden)
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", forbidden)
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(command, "compile_portfolio", lambda *args, **kwargs: portfolio)
    bindings = tmp_path / "bindings.json"
    bindings.write_text("{}", encoding="utf-8")
    output = tmp_path / ".build" / "plan.json"
    monkeypatch.setattr("sys.argv", [
        "register_portfolio.py", "--bindings", str(bindings), "--project-endpoint", ENDPOINT,
        "--model-deployment", MODEL, "--sampling", "model_defaults", "--output", str(output),
    ])
    assert command.main() == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["azure_writes"] is False and len(result["plan"]["agents"]) == 9
    original = output.read_bytes()
    assert command.main() == 2
    assert output.read_bytes() == original


def test_cli_missing_approval_fails_before_auth_and_journal(tmp_path, portfolio, monkeypatch):
    from scripts import register_portfolio as command
    import azure.identity

    def forbidden(*args, **kwargs):
        raise AssertionError("unapproved registration attempted authentication")
    monkeypatch.setattr(azure.identity, "AzureCliCredential", forbidden)
    monkeypatch.setattr(command, "ROOT", tmp_path)
    monkeypatch.setattr(command, "compile_portfolio", lambda *args, **kwargs: portfolio)
    bindings = tmp_path / "bindings.json"
    bindings.write_text("{}", encoding="utf-8")
    output, journal = tmp_path / ".build" / "receipt.json", tmp_path / ".build" / "journal.sqlite3"
    monkeypatch.setattr("sys.argv", [
        "register_portfolio.py", "--bindings", str(bindings), "--project-endpoint", ENDPOINT,
        "--model-deployment", MODEL, "--sampling", "model_defaults", "--output", str(output),
        "--apply", "--journal", str(journal), "--credential", "azure_cli",
        "--tenant-id", "11111111-1111-1111-1111-111111111111",
    ])
    assert command.main() == 2
    assert not journal.exists() and not output.exists()


@pytest.mark.parametrize("change", [
    {"project_endpoint": ENDPOINT + "-other"}, {"plan_sha256": "0" * 64},
    {"portfolio_sha256": "0" * 64}, {"live_acceptance": True},
    {"execution_allowed": True}, {"status": "prepared"}, {"schema_version": True},
    {"versions": {"ops-orchestrator": "latest"}},
])
def test_runtime_rejects_incomplete_or_mismatched_receipts(tmp_path, portfolio, plan, change):
    journal = RegistrationJournal(tmp_path / "journal.sqlite3", plan)
    try:
        receipt = register_portfolio(plan, journal, Service(), approved_sha256=plan.sha256)
        with pytest.raises((RegistrationError, ValueError)):
            bind_registered_runtime_agents(portfolio, plan, {**receipt, **change})
    finally:
        journal.close()
