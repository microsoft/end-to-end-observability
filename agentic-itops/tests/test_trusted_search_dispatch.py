from copy import deepcopy

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")

from agentic_itops.portfolio import build_sdk_definitions, compile_portfolio
from agentic_itops.portfolio_dispatch import validate_backend_request, validate_function_arguments
from agentic_itops.portfolio_schemas import PortfolioError
from agentic_itops.runtime import AGENTS, bind_runtime_agents
from tests.test_portfolio import ROOT, bound_config


def trusted_config():
    config = bound_config()
    config["search"]["search_knowledge"] = {
        "backend": "registered_handler", "handler": "test.approved_search",
        "auth": "managed_identity", "snapshot_field": "snapshot_id",
        "snapshot_id": "test-snapshot", "filter": "approved eq true and revoked eq false",
        "request_schema": {
            "type": "object", "properties": {
                "query": {"type": "string", "minLength": 1}, "producer_key": {"type": "string"},
                "run_id": {"type": "string"}, "snapshot_id": {"type": "string"},
            },
            "required": ["query", "producer_key", "run_id", "snapshot_id"], "additionalProperties": False,
        },
        "output_schema": {
            "type": "object", "properties": {
                "status": {"type": "string", "enum": ["complete"]},
                "snapshot_id": {"type": "string"},
                "documents": {"type": "array", "items": {
                    "type": "object", "properties": {"doc_id": {"type": "string"},
                                                     "revision": {"type": "string"}},
                    "required": ["doc_id", "revision"], "additionalProperties": False,
                }},
            },
            "required": ["status", "snapshot_id", "documents"], "additionalProperties": False,
        },
    }
    return config


def test_trusted_search_preserves_declared_semantics_without_native_approvals():
    portfolio = compile_portfolio(ROOT, trusted_config())
    assert portfolio.bindings_complete
    assert len(portfolio.agents) == 9 and len(portfolio.tools) == 12
    function = next(item for item in portfolio.functions if item.name == "search_approved_knowledge")
    assert function.declared_type == "azure_ai_search" and function.source_tool == "search_knowledge"
    assert set(function.agents) == {
        "industry-signal-pack", "runbook-memory", "remediation-planner", "postmortem-drafter",
    }
    binding = function.backend_binding
    assert binding["native_model_tool"] is False
    assert binding["requires_sql_eligibility_revalidation"] is True
    effective = binding["effective_backend_binding"]
    assert effective["query_type"] == "vector_semantic_hybrid" and effective["top_k"] == 3
    assert effective["filter"] == "(snapshot_id eq 'test-snapshot') and (approved eq true and revoked eq false)"
    validate_function_arguments(function, {})
    with pytest.raises(PortfolioError):
        validate_function_arguments(function, {"run_id": "different"})
    with pytest.raises(PortfolioError):
        validate_backend_request(function, {})
    assert compile_portfolio(ROOT, trusted_config()).to_json() == portfolio.to_json()


def test_all_nine_sdk_definitions_and_runtime_bindings_are_function_only():
    pytest.importorskip("azure.ai.projects")
    portfolio = compile_portfolio(ROOT, trusted_config())
    definitions = build_sdk_definitions(portfolio)
    assert set(definitions) == set(AGENTS)
    for definition in definitions.values():
        assert all(tool["type"] == "function" for tool in definition.as_dict()["tools"])
    runtime_agents = bind_runtime_agents(portfolio, {name: "1" for name in AGENTS})
    assert set(runtime_agents) == set(AGENTS)
    for name in ("industry-signal-pack", "runbook-memory", "remediation-planner", "postmortem-drafter"):
        assert "search_approved_knowledge" in runtime_agents[name].definition["functions"]
        assert "search_approved_knowledge" in definitions[name].as_dict()["instructions"]


@pytest.mark.parametrize("change", [
    {"auth": "anonymous"}, {"handler": ""}, {"filter": "approved eq true) or true"},
    {"output_schema": None}, {"project_connection_id": "mixed-native-binding"},
])
def test_unsafe_or_incomplete_trusted_search_bindings_fail(change):
    config = trusted_config()
    config["search"]["search_knowledge"].update(deepcopy(change))
    with pytest.raises(PortfolioError):
        compile_portfolio(ROOT, config)
