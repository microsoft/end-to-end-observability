"""Offline compiler tests. All scratch sources stay inside this subproject."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from itertools import product
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest
import yaml

from agentic_itops.portfolio import (
    AGENT_FOLDERS, INDUSTRIES, OPERATIONS, SPECIALISTS, TOOLS_BY_AGENT,
    build_sdk_definitions, compile_portfolio,
)
from agentic_itops.portfolio_schemas import (
    ActionPlan, NoActionPlan, PortfolioError, output_schema, validate_schema, validate_stage_output,
)
from agentic_itops.portfolio_dispatch import (
    DELEGATIONS, GATE_DEFERRED_SCHEMA, NO_ARGUMENTS, search_binding_fingerprint, scoped_search_binding,
    validate_backend_request, validate_function_arguments,
)


def test_action_pattern_preserves_original_language_without_lookaround():
    pattern = output_schema("remediation-planner")["properties"]["plan"]["anyOf"][0][
        "properties"]["proposed_action"]["pattern"]
    original, supported = re.compile(r"^(?!none$).+"), re.compile(pattern)
    values = (''.join(chars) for size in range(8) for chars in product("noex\n", repeat=size))
    for value in values:
        assert bool(original.search(value)) == bool(supported.search(value)), repr(value)
    for value in ("", "none", "none\n", "none\nx", "none\n\n", "none\r", "\nnone",
                  "n", "no", "non", "None", "annotate_incident", "\u2028", "none\u2028"):
        assert bool(original.search(value)) == bool(supported.search(value)), repr(value)


@pytest.mark.parametrize("pattern", [
    r"^(?!none$).+", r"(?=x)x", r"(?<=x)y", r"(?<!x)y", r"[^^](?=x)", r"\\(?=x)",
])
def test_provider_lookaround_rejected_before_registration(pattern):
    schema = output_schema("remediation-planner")
    schema["properties"]["plan"]["anyOf"][0]["properties"]["proposed_action"]["pattern"] = pattern
    with pytest.raises(PortfolioError, match="do not support regex lookaround"):
        validate_schema(schema)


@pytest.mark.parametrize("pattern", [
    r"^(?:x|y)$", r"\(\?=literal", r"[(?=]", r"[](?=]", r"[^](?=]",
])
def test_literal_regex_markers_are_not_lookaround(pattern):
    schema = output_schema("remediation-planner")
    schema["properties"]["plan"]["anyOf"][0]["properties"]["proposed_action"]["pattern"] = pattern
    validate_schema(schema)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sources():
    directory = ROOT / (".portfolio-tests-" + uuid4().hex)
    directory.mkdir()
    try:
        shutil.copytree(ROOT / "agents", directory / "agents")
        shutil.copytree(ROOT / "tools" / "openapi", directory / "tools" / "openapi")
        yield directory
    finally:
        shutil.rmtree(directory)


def mutate(root, folder, change):
    path = root / "agents" / folder / "agent.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(value)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def bound_config():
    # Explicit test-only declarations; neither these fixtures nor SDK construction
    # authenticate, contact, or claim that these endpoints/identities exist.
    return {
        "models": {"gpt-4o": "approved-ops-model"},
        "openapi": {name: {"backend": "openapi_http", "base_url": "https://tools.ops.company.net",
                           "audience": "api://approved-ops-tools"} for name in OPERATIONS},
        "search": {"search_knowledge": {
            "project_connection_id": "approved-knowledge-connection",
            "snapshot_field": "snapshot_id", "snapshot_id": "approved-snapshot-1",
            "filter": "synthetic eq true and approved eq true and revoked eq false",
        }},
        "mcp": {"azure_monitor": {
            "backend": "registered_handler", "handler": "approved.monitor",
            "auth": "managed_identity", "allowed_tools": ["query_observations"],
            "operations": {"query_observations": {
                "function_name": "monitorQueryObservations",
                "description": "Reviewed test-only MCP observation operation",
                "request_schema": {
                    "type": "object", "properties": {"window_id": {"type": "string"}},
                    "required": ["window_id"], "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object", "properties": {"status": {"type": "string"}},
                    "required": ["status"], "additionalProperties": False,
                },
            }},
        }},
    }


def approved_portfolio(root=ROOT, config=None):
    config = bound_config() if config is None else config
    # Future bounded-native profile fixture only. The current function-only
    # worker must never manufacture either isolation or hidden-round approval.
    approvals = {search_binding_fingerprint(config["search"]["search_knowledge"]):
                 "reviewed-search-isolation-evidence-1"}
    budget_approvals = {search_binding_fingerprint(config["search"]["search_knowledge"]):
                       "test-only-bounded-runtime-evidence-1"}
    return compile_portfolio(root, config, search_approvals=approvals,
                             search_budget_approvals=budget_approvals)


def sample(schema):
    if "anyOf" in schema:
        return sample(schema["anyOf"][0])
    if "enum" in schema:
        return deepcopy(schema["enum"][0])
    kind = schema["type"]
    if isinstance(kind, list):
        return None
    if kind == "object":
        return {key: sample(item) for key, item in schema["properties"].items()}
    if kind == "array":
        return [sample(schema["items"]) for _ in range(schema.get("minItems", 0))]
    if kind == "string":
        return "supplied-value"
    if kind == "boolean":
        return False
    if kind in ("integer", "number"):
        return schema.get("minimum", 1)
    if kind == "null":
        return None
    raise AssertionError(kind)


def valid_result(name, *, insufficient=False):
    result = sample(output_schema(name))
    result["citations"] = [{"source_id": "source-1", "revision": "1", "locator": None}]
    if insufficient:
        result["status"] = "insufficient_evidence"
        result["citations"] = []
        result["evidence_gaps"] = ["No complete evidence window available"]
    if name == "remediation-planner":
        if insufficient:
            result["plan"] = sample(output_schema(name)["properties"]["plan"]["anyOf"][1])
        else:
            result["plan"]["source_runbook"] = "source-1"
            result["plan"]["proposed_action"] = "restart"
    if name == "ops-orchestrator" and insufficient:
        result["proposed_action"] = "none"
        result["gate_decision"] = "not_evaluated"
    return result


def test_exact_all_nine_and_all_declared_bindings():
    portfolio = compile_portfolio(ROOT)
    assert [(agent.folder, agent.name) for agent in portfolio.agents] == list(AGENT_FOLDERS.items())
    assert portfolio.agent("ops-orchestrator").connected_agents == SPECIALISTS
    assert portfolio.mode == "trusted_dispatch"
    assert set(portfolio.agent("ops-orchestrator").function_names) == {
        "normalizeSignals", "evaluateChangeRequest", *DELEGATIONS.values(),
    }
    assert len(portfolio.tools) == 12
    operations = {operation for tool in portfolio.tools for operation in tool.operation_ids}
    assert operations == {operation for _, operation in OPERATIONS.values()}
    assert len(operations) == 6
    assert [agent.name for agent in portfolio.agents if agent.source_owner == "separately_authored_00"] == [
        "ops-orchestrator"]
    search = next(tool for tool in portfolio.tools if tool.kind == "search")
    assert search.source["query_type"] == "vector_semantic_hybrid"
    assert search.source["top_k"] == 3
    industry = portfolio.agent("industry-signal-pack")
    assert industry.source_manifest["parameters"]["industry"]["enum"] == list(INDUSTRIES)
    assert "file_search" not in portfolio.to_json().lower()
    assert not portfolio.bindings_complete
    assert portfolio.live_gates
    assert portfolio.to_dict()["is_live"] is False
    assert len(portfolio.source_inventory) == 24
    for agent in portfolio.agents:
        assert agent.instructions == (ROOT / "agents" / agent.folder / "instructions.md").read_text(
            encoding="utf-8")
        assert agent.tool_names == TOOLS_BY_AGENT[agent.name]
        assert agent.model_reference == "gpt-4o"
        assert agent.model_deployment is None
        assert agent.top_p == 0.95
        assert agent.identity_intent["required_roles"] == ["Monitoring Reader", "Reader"]
    assert portfolio.agent("incident-comms").temperature == 0.4
    assert portfolio.agent("postmortem-drafter").temperature == 0.3
    assert portfolio.agent("postmortem-drafter").identity_intent == industry.identity_intent


def test_deterministic_content_and_inventory(sources):
    first = compile_portfolio(ROOT)
    second = compile_portfolio(sources)
    assert first.to_json() == second.to_json()
    assert first.content_sha256 == second.content_sha256
    path = sources / "agents" / "07-incident-comms" / "instructions.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nAdditional source text.\n", encoding="utf-8")
    changed = compile_portfolio(sources)
    assert changed.content_sha256 != first.content_sha256
    assert changed.agent("incident-comms").content_sha256 != first.agent("incident-comms").content_sha256
    assert changed.agent("rca-investigator").content_sha256 == first.agent("rca-investigator").content_sha256
    assert first.agent("industry-signal-pack").output_schema == output_schema("industry-signal-pack")


@pytest.mark.parametrize("change", [
    lambda m: m.update(name="wrong-agent"),
    lambda m: m.update(ignored_field=True),
    lambda m: m.pop("tools"),
    lambda m: m.update(tools=[]),
    lambda m: m["tools"][0].update(type="file_search"),
    lambda m: m["tools"][0].update(name="unknown"),
    lambda m: m["tools"][0].update(ignored_field=True),
    lambda m: m["tools"][0].update(auth="anonymous"),
    lambda m: m["identity"].update(required_roles=["Contributor"]),
    lambda m: m.update(instructions_file="../../../../missing.md"),
    lambda m: m.update(instructions_file="${INSTRUCTIONS}"),
    lambda m: m.update(temperature=True),
    lambda m: m.update(top_p=1.1),
    lambda m: m["output_schema"]["properties"].update(unknown={"type": "string"}),
    lambda m: m["output_schema"]["properties"]["citations"].pop("items"),
    lambda m: m["output_schema"]["properties"]["citations"]["items"].update(additionalProperties=True),
    lambda m: m["output_schema"]["properties"]["citations"]["items"].update(unknown=True),
])
def test_reject_manifest_errors(sources, change):
    mutate(sources, "02-triage-dedup", change)
    with pytest.raises(PortfolioError):
        compile_portfolio(sources)


@pytest.mark.parametrize("case", ["missing_agent", "extra_agent", "missing_manifest",
                                  "missing_instructions", "empty_instructions", "duplicate_yaml_key",
                                  "missing_spec", "wrong_operation", "invalid_openapi_schema",
                                  "wrong_connected_agent", "missing_connected_agent",
                                  "search_semantics", "industry_choices"])
def test_fail_closed_inventory_and_contracts(sources, case):
    folder = sources / "agents" / "02-triage-dedup"
    if case == "missing_agent":
        shutil.rmtree(folder)
    elif case == "extra_agent":
        (sources / "agents" / "11-unapproved").mkdir()
    elif case == "missing_manifest":
        (folder / "agent.yaml").unlink()
    elif case == "missing_instructions":
        (folder / "instructions.md").unlink()
    elif case == "empty_instructions":
        (folder / "instructions.md").write_text("  ", encoding="utf-8")
    elif case == "duplicate_yaml_key":
        path = folder / "agent.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\nname: triage-dedup\n", encoding="utf-8")
    elif case == "missing_spec":
        (sources / "tools" / "openapi" / "resource-graph.yaml").unlink()
    elif case in {"wrong_operation", "invalid_openapi_schema"}:
        path = sources / "tools" / "openapi" / "resource-graph.yaml"
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        operation = value["paths"]["/resource"]["get"]
        if case == "wrong_operation":
            operation["operationId"] = "notADeclaredOperation"
        else:
            operation["parameters"][0]["schema"]["type"] = "not-a-type"
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
    elif case in {"wrong_connected_agent", "missing_connected_agent"}:
        mutate(sources, "00-ops-orchestrator", lambda m: m.update(
            connected_agents=["triage-dedup"] if case == "wrong_connected_agent" else list(SPECIALISTS[:3])))
    elif case == "search_semantics":
        mutate(sources, "04-runbook-memory", lambda m: m["tools"][0].update(query_type="simple"))
    elif case == "industry_choices":
        mutate(sources, "10-industry-signal-pack",
               lambda m: m["parameters"]["industry"].update(enum=["energy_utilities"]))
    with pytest.raises(PortfolioError):
        compile_portfolio(sources)


@pytest.mark.parametrize("value", [
    "${DEPLOYMENT}", "{{deployment}}", "<deployment>", "...", "TBD", "", " model ", "model/name",
    "your-deployment", "REPLACE_WITH_DEPLOYMENT", "{deployment}",
])
def test_model_requires_explicit_non_placeholder_binding(value):
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, {"models": {"gpt-4o": value}})


@pytest.mark.parametrize("url", [
    "https://tools.invalid", "https://example.com", "http://tools.ops.company.net",
    "https://localhost", "https://${TOOLS_HOST}", "https://<hostname>",
    "https://tools.ops.company.net?token=secret", "https://user:secret@tools.ops.company.net",
    "https://tools.ops.company.net/path#fragment",
    "https://tools.ops.company.net/../escape", "https://tools.ops.company.net/%2e%2e/escape",
    "https://your-host.azurewebsites.net", "https://tools.ops.company.net/%7Bplaceholder%7D",
])
def test_binding_placeholders_and_credential_urls_fail(url):
    config = bound_config()
    config["openapi"]["signal_normalizer"]["base_url"] = url
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, config)


@pytest.mark.parametrize("case", ["unknown_group", "unknown_tool", "unknown_field", "missing_field",
                                  "auth_dropped", "mcp_unrestricted", "mcp_no_approval", "model_unknown"])
def test_unknown_or_incomplete_explicit_bindings_fail(case):
    config = bound_config()
    if case == "unknown_group":
        config["file_search"] = {}
    elif case == "unknown_tool":
        config["openapi"]["invented"] = config["openapi"]["signal_normalizer"]
    elif case == "unknown_field":
        config["search"]["search_knowledge"]["ignored_top_k"] = 10
    elif case == "missing_field":
        config["search"]["search_knowledge"].pop("filter")
    elif case == "auth_dropped":
        config["mcp"]["azure_monitor"]["auth"] = "anonymous"
    elif case == "mcp_unrestricted":
        config["mcp"]["azure_monitor"]["allowed_tools"] = ["*"]
    elif case == "mcp_no_approval":
        config["mcp"]["azure_monitor"]["require_approval"] = "never"
    elif case == "model_unknown":
        config["models"]["unknown-model"] = "approved-model"
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, config)


def test_complete_bindings_preserved_and_not_live():
    config = bound_config()
    plan = approved_portfolio(config=config)
    assert plan.bindings_complete
    assert json.loads(plan.bindings_json) == config
    assert plan.to_dict()["bindings"] == config
    assert plan.to_dict()["is_live"] is False
    assert plan.live_gates
    assert all(agent.model_deployment == "approved-ops-model" for agent in plan.agents)
    config["models"]["gpt-4o"] = "mutated"
    assert json.loads(plan.bindings_json)["models"]["gpt-4o"] != "mutated"
    manifest = plan.agent("ops-orchestrator").source_manifest
    manifest["top_p"] = 0
    assert plan.agent("ops-orchestrator").source_manifest["top_p"] == 0.95


def test_optional_sdk_serialization_preserves_every_binding(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("network/authentication forbidden in SDK construction")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    plan = approved_portfolio()
    definitions = {name: definition.as_dict() for name, definition in build_sdk_definitions(plan).items()}
    assert set(definitions) == set(AGENT_FOLDERS.values())
    for name, definition in definitions.items():
        agent = plan.agent(name)
        assert definition["model"] == "approved-ops-model"
        assert definition["instructions"] == agent.instructions
        assert definition["temperature"] == agent.temperature
        assert definition["top_p"] == agent.top_p
        assert definition["text"]["format"]["strict"] is True
        assert definition["text"]["format"]["schema"] == agent.output_schema
        assert len(definition["tools"]) == len(agent.function_names) + ("search_knowledge" in agent.tool_names)
    assert [tool["type"] for tool in definitions["ops-orchestrator"]["tools"]] == ["function"] * 6
    assert "file_search" not in json.dumps(definitions)
    search = definitions["runbook-memory"]["tools"][0]["azure_ai_search"]["indexes"][0]
    assert search["query_type"] == "vector_semantic_hybrid" and search["top_k"] == 3
    assert search["filter"] == scoped_search_binding(bound_config()["search"]["search_knowledge"])["filter"]
    rca = definitions["rca-investigator"]
    mcp = next(tool for tool in rca["tools"] if tool["name"] == "monitorQueryObservations")
    assert mcp["parameters"] == NO_ARGUMENTS
    assert mcp["output_schema"] == bound_config()["mcp"]["azure_monitor"]["operations"][
        "query_observations"]["output_schema"]
    operations = set()
    for function in plan.functions:
        assert function.parameters == NO_ARGUMENTS
        if function.declared_type == "openapi":
            assert function.backend_binding["audience"] == "api://approved-ops-tools"
            assert function.auth_requirements["source_auth"] == "managed_identity"
            assert function.source_spec_sha256
            operations.add(function.backend_operation)
    assert operations == {operation for _, operation in OPERATIONS.values()}


def test_unbound_sdk_fails_before_azure_import():
    code = (
        "import sys; from agentic_itops.portfolio import compile_portfolio,build_sdk_definitions;"
        "from agentic_itops.portfolio_schemas import PortfolioError;"
        "\ntry: build_sdk_definitions(compile_portfolio('.'))"
        "\nexcept PortfolioError: pass"
        "\nelse: raise AssertionError('expected blocked SDK')"
        "\nassert not any(x == 'azure' or x.startswith('azure.') for x in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", list(AGENT_FOLDERS.values()))
@pytest.mark.parametrize("insufficient", [False, True])
def test_all_nine_completed_and_insufficient_shapes(name, insufficient):
    validate_schema(output_schema(name))
    result = valid_result(name, insufficient=insufficient)
    parsed = validate_stage_output(name, result, known_citations={"source-1"})
    assert parsed.as_dict() == result
    assert parsed.citation_membership_checked
    assert not parsed.authorized_for_execution
    if name == "remediation-planner":
        assert isinstance(parsed.plan, NoActionPlan if insufficient else ActionPlan)


@pytest.mark.parametrize("name", list(AGENT_FOLDERS.values()))
def test_all_nine_reject_unknown_fields_missing_citations_and_gaps(name):
    value = valid_result(name)
    value["fabricated_extra"] = True
    with pytest.raises(PortfolioError):
        validate_stage_output(name, value)
    value = valid_result(name)
    with pytest.raises(PortfolioError):
        validate_stage_output(name, value, known_citations=set())
    value["citations"] = []
    with pytest.raises(PortfolioError):
        validate_stage_output(name, value)
    value = valid_result(name, insufficient=True)
    value["evidence_gaps"] = []
    with pytest.raises(PortfolioError):
        validate_stage_output(name, value)


@pytest.mark.parametrize("field,value", [
    ("target_resource_id", "invented-target"), ("proposed_action", "annotate_incident"),
    ("classification", "mitigation"), ("rollback", "undo"),
])
def test_no_action_branch_is_distinct(field, value):
    result = valid_result("remediation-planner", insufficient=True)
    result["plan"][field] = value
    with pytest.raises(PortfolioError):
        validate_stage_output("remediation-planner", result)


def test_zero_radius_is_never_coerced_into_an_action():
    value = valid_result("remediation-planner")
    value["plan"]["blast_radius"]["estimated_affected"] = 0
    with pytest.raises(PortfolioError):
        validate_stage_output("remediation-planner", value)
    value = valid_result("remediation-planner", insufficient=True)
    parsed = validate_stage_output("remediation-planner", value)
    assert parsed.plan == NoActionPlan(value["plan"]["rationale"])
    assert parsed.as_dict()["plan"]["proposed_action"] == "none"
    value["plan"]["blast_radius"]["estimated_affected"] = 1
    with pytest.raises(PortfolioError):
        validate_stage_output("remediation-planner", value)


def test_no_action_completed_is_a_valid_reviewable_outcome():
    value = valid_result("remediation-planner", insufficient=True)
    value.update(status="completed", evidence_gaps=[],
                 citations=[{"source_id": "source-1", "revision": "1", "locator": None}])
    assert isinstance(validate_stage_output("remediation-planner", value).plan, NoActionPlan)


def test_explicit_annotation_remains_a_proposal_not_a_fallback():
    value = valid_result("remediation-planner")
    value["plan"].update(proposed_action="annotate_incident", source_runbook=None)
    parsed = validate_stage_output("remediation-planner", value, known_citations={"source-1"})
    assert isinstance(parsed.plan, ActionPlan)
    assert not parsed.authorized_for_execution
    value["status"] = "insufficient_evidence"
    value["evidence_gaps"] = ["Target evidence missing"]
    with pytest.raises(PortfolioError):
        validate_stage_output("remediation-planner", value)


def test_hypotheses_have_typed_citations_and_contradictions():
    value = valid_result("rca-investigator")
    value["hypotheses"] = [{
        "hypothesis": "A supported candidate, not a fact", "confidence": "low",
        "evidence": ["source-1"], "contradicting_evidence": ["source-2"],
        "next_check": "Request bounded follow-up evidence",
    }]
    with pytest.raises(PortfolioError):
        validate_stage_output("rca-investigator", value, known_citations={"source-1", "source-2"})
    value["citations"].append({"source_id": "source-2", "revision": "2", "locator": None})
    assert validate_stage_output("rca-investigator", value, known_citations={"source-1", "source-2"})
    value["hypotheses"][0]["confidence"] = "certain"
    with pytest.raises(PortfolioError):
        validate_stage_output("rca-investigator", value)


def test_document_citation_preserves_exact_revision():
    value = valid_result("runbook-memory")
    document = sample(output_schema("runbook-memory")["properties"]["runbooks"]["items"])
    document.update(doc_id="source-1", revision="2")
    value["runbooks"] = [document]
    with pytest.raises(PortfolioError, match="revision"):
        validate_stage_output("runbook-memory", value, known_citations={"source-1"})
    document["revision"] = "1"
    assert validate_stage_output("runbook-memory", value, known_citations={"source-1"})


@pytest.mark.parametrize("change", [
    lambda schema: schema["properties"]["citations"].pop("items"),
    lambda schema: schema.update(additionalProperties=True),
    lambda schema: schema["properties"]["status"].update(minLenght=1),
    lambda schema: schema["properties"]["status"].update(properties={"ignored": {"type": "string"}}),
])
def test_invalid_or_ignored_schema_fields_fail(change):
    schema = output_schema("triage-dedup")
    change(schema)
    with pytest.raises(PortfolioError):
        validate_schema(schema)


def test_emitter_has_exactly_eight_sources_and_no_drift():
    spec = importlib.util.spec_from_file_location("portfolio_emitter_test", ROOT / "scripts" / "emit_agent_manifests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.AGENT_SPECS) == set(AGENT_FOLDERS) - {"00-ops-orchestrator"}
    for folder in module.AGENT_SPECS:
        actual = yaml.safe_load((ROOT / "agents" / folder / "agent.yaml").read_text(encoding="utf-8"))
        assert actual == module.generated_manifest(folder)
    result = subprocess.run([sys.executable, "scripts\\emit_agent_manifests.py", "--check"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "All 8 generated manifests match; 00 is separately authored." in result.stdout


def test_emitter_check_rejects_drift_without_writing(sources, monkeypatch):
    spec = importlib.util.spec_from_file_location("portfolio_emitter_drift", ROOT / "scripts" / "emit_agent_manifests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.AGENTS = sources / "agents"
    mutate(sources, "07-incident-comms", lambda m: m.update(top_p=0.7))
    before = (sources / "agents" / "07-incident-comms" / "agent.yaml").read_bytes()
    monkeypatch.setattr(sys, "argv", ["emit_agent_manifests.py", "--check"])
    with pytest.raises(SystemExit, match="manifest drift"):
        module.main()
    assert before == (sources / "agents" / "07-incident-comms" / "agent.yaml").read_bytes()


def test_existing_instruction_path_outside_agent_is_rejected(sources):
    mutate(sources, "02-triage-dedup",
           lambda m: m.update(instructions_file="../00-ops-orchestrator/instructions.md"))
    with pytest.raises(PortfolioError, match="escapes"):
        compile_portfolio(sources)


def test_cli_review_plan_and_require_bound():
    result = subprocess.run([sys.executable, "scripts\\compile_portfolio.py"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert len(value["agents"]) == 9
    assert not value["bindings_complete"]
    assert value["agents"][0]["output_schema"]["additionalProperties"] is False
    result = subprocess.run([sys.executable, "scripts\\compile_portfolio.py", "--require-bound"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2
    assert "explicit deployment binding required" in result.stderr


def test_trusted_dispatch_mediates_every_source_without_replacing_root():
    plan = approved_portfolio()
    assert len(plan.agents) == 9
    assert len(plan.functions) == 11
    assert set(plan.function_schemas("ops-orchestrator")) == {
        "normalizeSignals", "evaluateChangeRequest", *DELEGATIONS.values(),
    }
    delegates = [function for function in plan.functions if function.declared_type == "connected_agent"]
    assert {function.target_agent for function in delegates} == set(SPECIALISTS)
    for function in delegates:
        assert function.name == DELEGATIONS[function.target_agent]
        assert function.agents == ("ops-orchestrator",)
        assert function.output_schema == plan.agent(function.target_agent).output_schema
        assert function.backend_binding["version_source"] == "runtime_version_journal"
    for tool in plan.tools:
        if tool.kind == "search":
            continue
        wrappers = [function for function in plan.functions if function.source_tool == tool.name]
        assert wrappers, tool.name
        for function in wrappers:
            assert function.context_source == "trusted_workflow"
            assert function.parameters == NO_ARGUMENTS
            assert function.auth_requirements["worker_authorization_required"] is True
            assert function.declared_type == tool.source.get("type", "connected_agent")
    assert plan.agent("ops-orchestrator").instructions.startswith("# Ops Orchestrator")
    assert "delegate_runbook_memory" in plan.agent("ops-orchestrator").instructions


@pytest.mark.parametrize("injection", [
    {"workflow_id": "forged"}, {"target": "other-resource"}, {"scope": "other-tenant"},
    {"query": "union *"}, {"url": "https://unapproved.ops.company.net"}, {"budget": 1000},
    {"context": {"trusted": True}}, {"agent_version": "latest"},
])
def test_all_wrappers_reject_model_supplied_context(injection):
    for function in approved_portfolio().functions:
        validate_function_arguments(function, {})
        with pytest.raises(PortfolioError, match="exactly"):
            validate_function_arguments(function, injection)


def test_worker_backend_requests_retain_original_contracts():
    plan = approved_portfolio()
    functions = {function.name: function for function in plan.functions}
    kql = functions["runKqlQuery"]
    assert kql.parameters == NO_ARGUMENTS
    assert kql.backend_request_schema["required"] == ["query"]
    assert kql.backend_binding["method"] == "POST"
    assert kql.backend_binding["path"] == "/query"
    validate_backend_request(kql, {"query": "trusted code-owned allowlisted query"})
    with pytest.raises(PortfolioError):
        validate_backend_request(kql, {})
    context = functions["getResourceContext"]
    assert context.backend_request_schema["required"] == ["resource_id"]
    validate_backend_request(context, {"resource_id": "trusted-exact-logical-target"})
    with pytest.raises(PortfolioError):
        validate_backend_request(context, {"resource_id": 123})
    with pytest.raises(PortfolioError, match="separate trusted"):
        validate_backend_request(functions["delegate_runbook_memory"], {})
    mcp = functions["monitorQueryObservations"]
    assert mcp.backend_operation == "query_observations"
    assert mcp.backend_binding["operation_contract_sha256"]
    assert mcp.backend_binding["source_endpoint_declaration"] == "${AZURE_MONITOR_MCP_URL}"
    validate_backend_request(mcp, {"window_id": "trusted-window"})
    with pytest.raises(PortfolioError):
        validate_backend_request(mcp, {"window_id": "trusted-window", "url": "unapproved"})


def test_registered_openapi_handlers_do_not_require_fake_endpoints():
    config = bound_config()
    config["openapi"] = {
        name: {"backend": "registered_handler", "handler": f"approved.{name}", "auth": "managed_identity"}
        for name in OPERATIONS
    }
    plan = approved_portfolio(config=config)
    assert plan.bindings_complete
    for function in plan.functions:
        if function.declared_type == "openapi":
            assert function.backend_binding["handler"].startswith("approved.")
            assert "base_url" not in function.backend_binding
            assert function.auth_requirements["source_auth"] == "managed_identity"


def test_native_search_is_blocked_without_exact_separate_approval():
    config = bound_config()
    plan = compile_portfolio(ROOT, config)
    assert not plan.bindings_complete
    assert len(plan.blockers) == 2
    assert any("native isolation approval required" in blocker for blocker in plan.blockers)
    with pytest.raises(PortfolioError):
        build_sdk_definitions(plan)
    fingerprint = search_binding_fingerprint(config["search"]["search_knowledge"])
    approvals = {fingerprint: "verified-evidence-1"}
    assert not compile_portfolio(ROOT, config, search_approvals=approvals).bindings_complete
    assert compile_portfolio(ROOT, config, search_approvals=approvals,
                             search_budget_approvals=approvals).bindings_complete
    config["search"]["search_knowledge"]["snapshot_id"] = "different-snapshot"
    assert not compile_portfolio(ROOT, config, search_approvals=approvals).bindings_complete


@pytest.mark.parametrize("field", ["project_connection_id", "snapshot_id", "snapshot_field", "filter"])
def test_any_changed_search_binding_invalidates_approval(field):
    config = bound_config()
    approvals = {search_binding_fingerprint(config["search"]["search_knowledge"]): "verified-evidence-1"}
    config["search"]["search_knowledge"][field] += "_changed"
    plan = compile_portfolio(ROOT, config, search_approvals=approvals)
    assert not plan.bindings_complete
    assert any("isolation approval required" in blocker for blocker in plan.blockers)


@pytest.mark.parametrize("filter_text", ["true) or true or (true", "(approved eq true", "name eq 'open", "true;false"])
def test_search_filter_cannot_escape_snapshot_clause(filter_text):
    config = bound_config()
    config["search"]["search_knowledge"]["filter"] = filter_text
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, config)


def test_snapshot_is_always_anded_with_balanced_eligibility_filter():
    binding = bound_config()["search"]["search_knowledge"]
    binding["filter"] = "(approved eq true) or owner eq 'O''Brien'"
    actual = scoped_search_binding(binding)
    assert actual["filter"] == (
        "(snapshot_id eq 'approved-snapshot-1') and ((approved eq true) or owner eq 'O''Brien')"
    )
    assert actual["query_type"] == "vector_semantic_hybrid"
    assert actual["top_k"] == 3
    assert actual["index_name"] == "agentic-itops-knowledge"


@pytest.mark.parametrize("case", [
    "missing_contract", "extra_operation", "unknown_contract_field", "missing_output_schema",
    "invalid_request_schema", "unknown_schema_keyword", "remote_ref", "remote_dynamic_ref",
    "function_collision", "model_scope_arguments",
])
def test_mcp_requires_actual_explicit_operation_contracts(case):
    config = bound_config()
    mcp = config["mcp"]["azure_monitor"]
    contract = mcp["operations"]["query_observations"]
    if case == "missing_contract":
        mcp["operations"] = {}
    elif case == "extra_operation":
        mcp["operations"]["not_allowlisted"] = deepcopy(contract)
    elif case == "unknown_contract_field":
        contract["ignored"] = True
    elif case == "missing_output_schema":
        contract.pop("output_schema")
    elif case == "invalid_request_schema":
        contract["request_schema"]["type"] = "invalid"
    elif case == "unknown_schema_keyword":
        contract["request_schema"]["minLenght"] = 1
    elif case in {"remote_ref", "remote_dynamic_ref"}:
        key = "$ref" if case == "remote_ref" else "$dynamicRef"
        contract["request_schema"][key] = "https://untrusted.ops.company.net/schema"
    elif case == "function_collision":
        contract["function_name"] = "runKqlQuery"
    elif case == "model_scope_arguments":
        contract["parameters"] = {"scope": "model-supplied"}
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, config)


def test_unbound_mcp_reports_requirements_without_inventing_operations():
    plan = compile_portfolio(ROOT)
    assert not any(function.declared_type == "mcp" for function in plan.functions)
    mcp = next(tool for tool in plan.tools if tool.kind == "mcp")
    assert "operations" in mcp.required_binding_fields
    assert "allowed_tools" in mcp.required_binding_fields
    assert mcp.blocked_fields
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, bound_config(), mode="native")


def test_sdk_has_only_functions_and_approved_native_search():
    definitions = build_sdk_definitions(approved_portfolio())
    for definition in definitions.values():
        for tool in definition.as_dict()["tools"]:
            assert tool["type"] in {"function", "azure_ai_search"}
            if tool["type"] == "function":
                assert tool["parameters"] == NO_ARGUMENTS
                assert tool["strict"] is True
                assert "output_schema" in tool


def test_function_only_worker_remains_blocked_despite_isolation_approval():
    config = bound_config()
    fingerprint = search_binding_fingerprint(config["search"]["search_knowledge"])
    plan = compile_portfolio(ROOT, config, search_approvals={fingerprint: "isolation-evidence-1"})
    assert len(plan.agents) == 9
    assert not plan.bindings_complete
    assert len(plan.blockers) == 1
    assert "function-only runtime remains blocked" in plan.blockers[0]
    search = next(tool for tool in plan.tools if tool.kind == "search")
    assert search.binding["isolation_evidence_id"] == "isolation-evidence-1"
    assert search.binding["bounded_runtime_evidence_id"] is None
    with pytest.raises(PortfolioError, match="budget approval"):
        build_sdk_definitions(plan)


def test_gate_wrapper_defers_without_claiming_a_decision():
    from jsonschema import Draft202012Validator

    plan = approved_portfolio()
    gate = next(function for function in plan.functions if function.name == "evaluateChangeRequest")
    assert gate.parameters == NO_ARGUMENTS
    assert gate.backend_binding["requires_finalized_stage"] == "remediation-planner"
    assert gate.backend_binding["evaluates"] == "exact_final_planner_output"
    assert gate.backend_binding["comms_requires_authoritative_gate_receipt"] is True
    deferred = {"status": "deferred", "reason": "finalized_planner_output_required"}
    Draft202012Validator(GATE_DEFERRED_SCHEMA).validate(deferred)
    Draft202012Validator(gate.output_schema).validate(deferred)
    assert not Draft202012Validator(gate.backend_output_schema).is_valid(deferred)
    assert Draft202012Validator(gate.output_schema).is_valid({"effect": "deny", "audit_id": "receipt-1"})
    assert not Draft202012Validator(gate.output_schema).is_valid(
        {**deferred, "effect": "allow", "audit_id": "fabricated"})
    assert not Draft202012Validator(gate.output_schema).is_valid({"status": "deferred", "reason": ""})
    assert "exact finalized" in gate.description
    serialized = plan.to_dict()
    record = next(function for function in serialized["functions"] if function["name"] == gate.name)
    assert record["backend_output_schema"] == gate.backend_output_schema
    assert record["output_schema"] == gate.output_schema


def test_exact_observation_event_ids_and_logical_targets_are_preserved():
    event_id = "producer-event-007"
    target = "urn:agentic-itops:synthetic-entity:" + "a" * 64
    value = valid_result("remediation-planner")
    value["citations"] = [{"source_id": event_id, "revision": None, "locator": None}]
    value["plan"].update(target_resource_id=target, proposed_action="annotate_incident", source_runbook=None)
    result = validate_stage_output("remediation-planner", value, known_citations={event_id})
    assert result.plan.target_resource_id == target
    assert not result.authorized_for_execution
    rca = valid_result("rca-investigator")
    rca["citations"] = deepcopy(value["citations"])
    hypothesis = sample(output_schema("rca-investigator")["properties"]["hypotheses"]["items"])
    hypothesis["evidence"] = ["prose pretending to be a reference"]
    rca["hypotheses"] = [hypothesis]
    with pytest.raises(PortfolioError):
        validate_stage_output("rca-investigator", rca, known_citations={event_id})
    hypothesis["evidence"] = [event_id]
    assert validate_stage_output("rca-investigator", rca, known_citations={event_id})
