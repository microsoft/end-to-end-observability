"""Approved trusted-dispatch target. Compilation and validation are offline.

Binding API (JSON object, no environment expansion or credentials):

* ``models``: source model reference -> explicit deployment name.
* ``openapi``: each declared tool name -> either
  ``{backend: "registered_handler", handler: <registry key>, auth: "managed_identity"}``
  or ``{backend: "openapi_http", base_url: <HTTPS URL>, audience: <MI audience>}``.
  HTTP, if chosen, is called by the worker, never directly by Foundry.
* ``mcp.azure_monitor``: ``backend: "registered_handler"``, ``handler``,
  ``auth: "managed_identity"``, ``allowed_tools`` (actual backend operation names),
  and ``operations`` mapping each exact allowed name to ``function_name``,
  ``description``, ``request_schema`` and ``output_schema``. These must come from
  the actual reviewed backend; the compiler invents no MCP operation or contract.
* ``search.search_knowledge``: ``project_connection_id``, ``snapshot_field``,
  ``snapshot_id``, ``filter``. The effective native filter is the snapshot
  equality AND the parenthesized supplied eligibility filter.
  Alternatively, use ``backend: "registered_handler"``, ``handler``,
  ``auth: "managed_identity"``, the three snapshot/filter fields, and explicit
  backend ``request_schema``/``output_schema``. This emits the budgeted
  ``search_approved_knowledge`` function, not a native model tool.

Native Search additionally requires a *separate trusted approval registry*:
``compile_portfolio(..., search_approvals={search_binding_fingerprint(binding):
evidence_id})``. The caller verifies isolation first; the compiler checks exact
binding/approval correspondence, not whether an evidence ID represents real
authorization. Changing any binding invalidates the approval. Do not build this
registry from model output or a self-asserted flag in the binding document.
It also requires ``search_budget_approvals={fingerprint: evidence_id}`` proving
bounded hidden Search/model rounds in the actual compatible worker runtime.
The current function-only worker supplies no such approval and remains blocked.

The gate wrapper returns either the original evaluated backend result, or
``{"status": "deferred", "reason": <nonempty explanation>}`` while finalized
05 output is unavailable. Deferred is never a gate receipt and cannot release
07. The worker evaluates the exact finalized 05 revision before comms; a
no-action proposal is processed as explicitly non-operational, not coerced.

All function parameters are exactly ``{}``. The worker validates model arguments,
then independently assembles and validates backend requests from its trusted
workflow. It owns identities, targets, raw KQL selection, budgets and versions.
``FunctionRequirement`` retains original backend request/output contracts and
source/auth metadata; none of those are model-facing input parameters.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import json
import re
from typing import Any, Mapping

from .portfolio import (
    CompiledPortfolio, FunctionRequirement, LIVE_GATES, OPERATIONS, SPECIALISTS,
    _hash, _keys, _literal, _url, canonical_json,
)
from .portfolio_schemas import PortfolioError


NO_ARGUMENTS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
GATE_DEFERRED_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["deferred"]},
                   "reason": {"type": "string", "minLength": 1}},
    "required": ["status", "reason"], "additionalProperties": False,
}
DELEGATIONS = {
    name: "delegate_" + name.replace("-", "_") for name in SPECIALISTS
}
SEARCH_FIELDS = ("project_connection_id", "snapshot_field", "snapshot_id", "filter")
TRUSTED_SEARCH_FIELDS = (
    "backend", "handler", "auth", "snapshot_field", "snapshot_id", "filter",
    "request_schema", "output_schema",
)
MCP_FIELDS = ("backend", "handler", "auth", "allowed_tools", "operations")
BACKEND_CONTEXT_RULE = (
    "Call with {} only. The trusted worker derives context, scope, target, query, "
    "version and budget from its workflow; never supply these as model arguments."
)


def _identifier(value: Any, where: str, *, function: bool = False) -> str:
    _literal(value, where)
    pattern = r"[A-Za-z_][A-Za-z0-9_-]{0,63}" if function else r"[A-Za-z_][A-Za-z0-9_.:-]*"
    if not re.fullmatch(pattern, value):
        raise PortfolioError(f"{where}: explicit registered identifier required")
    return value


def _balanced_filter(expression: str) -> None:
    """Prevent a supplied filter from breaking out of its enclosing AND clause."""
    depth, quoted, index = 0, False, 0
    while index < len(expression):
        character = expression[index]
        if character == "'":
            if quoted and index + 1 < len(expression) and expression[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < 0:
                    raise PortfolioError("Search filter cannot escape its scoped clause")
            elif character in ";#":
                raise PortfolioError("Search filter has unsupported separators")
        index += 1
    if quoted or depth:
        raise PortfolioError("Search filter must have balanced parentheses and string literals")


def _snapshot_scope(binding: Mapping) -> dict:
    for key in ("snapshot_field", "snapshot_id", "filter"):
        _literal(binding[key], f"search.search_knowledge.{key}")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", binding["snapshot_field"]):
        raise PortfolioError("Search snapshot_field must be one explicit index field")
    _balanced_filter(binding["filter"])
    snapshot = binding["snapshot_id"].replace("'", "''")
    return {
        "index_name": "agentic-itops-knowledge",
        "query_type": "vector_semantic_hybrid", "top_k": 3,
        "filter": f"({binding['snapshot_field']} eq '{snapshot}') and ({binding['filter']})",
    }


def scoped_search_binding(binding: Mapping) -> dict:
    """Validate a source Search binding and return its exact SDK-facing scope."""
    if not isinstance(binding, Mapping):
        raise PortfolioError("search binding must be an object")
    binding = deepcopy(dict(binding))
    _keys(binding, set(SEARCH_FIELDS), "search.search_knowledge")
    _literal(binding["project_connection_id"], "search.search_knowledge.project_connection_id")
    return {"project_connection_id": binding["project_connection_id"], **_snapshot_scope(binding)}


def trusted_search_binding(binding: Mapping) -> dict:
    if not isinstance(binding, Mapping):
        raise PortfolioError("trusted Search binding must be an object")
    _keys(binding, set(TRUSTED_SEARCH_FIELDS), "search.search_knowledge")
    if binding["backend"] != "registered_handler" or binding["auth"] != "managed_identity":
        raise PortfolioError("trusted Search requires an explicit managed-identity handler")
    _identifier(binding["handler"], "search.search_knowledge.handler")
    _backend_schema(binding["request_schema"], "search.search_knowledge.request_schema")
    _backend_schema(binding["output_schema"], "search.search_knowledge.output_schema")
    return _snapshot_scope(binding)


def search_binding_fingerprint(binding: Mapping) -> str:
    """Fingerprint the complete effective native tool binding for trusted approval."""
    return _hash(scoped_search_binding(binding))


def _backend_schema(schema: Any, where: str) -> dict:
    from jsonschema import Draft202012Validator, FormatChecker

    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise PortfolioError(f"{where}: an explicit object schema is required")
    annotations = {"title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly", "$defs"}
    allowed = set(Draft202012Validator.VALIDATORS) | annotations

    def visit(node: Any) -> None:
        if isinstance(node, bool):
            return
        if not isinstance(node, dict) or set(node) - allowed:
            raise PortfolioError(f"{where}: unsupported or unknown backend schema fields")
        if "format" in node and node["format"] not in FormatChecker.checkers:
            raise PortfolioError(f"{where}: unsupported backend schema format")
        for reference_key in ("$ref", "$dynamicRef"):
            if reference_key not in node:
                continue
            reference = node[reference_key]
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise PortfolioError(f"{where}: only resolved local schema references are allowed")
            target = schema
            try:
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as exc:
                raise PortfolioError(f"{where}: unresolved backend schema reference") from exc
        for keyword in ("properties", "patternProperties", "$defs", "dependentSchemas"):
            for child in node.get(keyword, {}).values():
                visit(child)
        for keyword in ("items", "additionalProperties", "contains", "not", "if", "then", "else",
                        "propertyNames", "unevaluatedItems", "unevaluatedProperties"):
            if keyword in node:
                visit(node[keyword])
        for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
            for child in node.get(keyword, []):
                visit(child)

    try:
        canonical_json(schema)
        Draft202012Validator.check_schema(schema)
        visit(schema)
    except PortfolioError:
        raise
    except Exception as exc:
        raise PortfolioError(f"{where}: invalid backend schema: {exc}") from exc
    return deepcopy(schema)


def _configuration(source: CompiledPortfolio, raw: Mapping | None) -> dict:
    if raw is not None and not isinstance(raw, Mapping):
        raise PortfolioError("bindings must be an object")
    config = deepcopy(dict(raw)) if raw is not None else {}
    groups = {
        "models": {agent.model_reference for agent in source.agents},
        "openapi": set(OPERATIONS), "search": {"search_knowledge"}, "mcp": {"azure_monitor"},
    }
    _keys(config, set(), "bindings", set(groups))
    for category, names in groups.items():
        group = config.setdefault(category, {})
        _keys(group, set(), f"bindings.{category}", names)
        for name, binding in group.items():
            where = f"bindings.{category}.{name}"
            if category == "models":
                _literal(binding, where)
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", binding):
                    raise PortfolioError(f"{where}: invalid deployment name")
            elif category == "search":
                if isinstance(binding, Mapping) and binding.get("backend") == "registered_handler":
                    trusted_search_binding(binding)
                else:
                    scoped_search_binding(binding)
            elif category == "openapi":
                if not isinstance(binding, dict):
                    raise PortfolioError(f"{where}: binding object required")
                if binding.get("backend") == "openapi_http":
                    _keys(binding, {"backend", "base_url", "audience"}, where)
                    _url(binding["base_url"], where)
                    _literal(binding["audience"], where)
                elif binding.get("backend") == "registered_handler":
                    _keys(binding, {"backend", "handler", "auth"}, where)
                    _identifier(binding["handler"], where)
                    if binding["auth"] != "managed_identity":
                        raise PortfolioError(f"{where}: managed identity intent must be retained")
                else:
                    raise PortfolioError(f"{where}: explicit openapi_http or registered_handler backend required")
            else:
                _keys(binding, set(MCP_FIELDS), where)
                if binding["backend"] != "registered_handler" or binding["auth"] != "managed_identity":
                    raise PortfolioError(f"{where}: registered worker handler and managed identity intent required")
                _identifier(binding["handler"], where)
                names = binding["allowed_tools"]
                if (not isinstance(names, list) or not names
                        or any(not isinstance(name, str) for name in names)
                        or len(set(names)) != len(names)):
                    raise PortfolioError(f"{where}: explicit nonempty unique MCP allowlist required")
                for operation in names:
                    _identifier(operation, where)
                _keys(binding["operations"], set(names), f"{where}.operations")
                for operation, contract in binding["operations"].items():
                    _keys(contract, {"function_name", "description", "request_schema", "output_schema"},
                          f"{where}.{operation}")
                    _identifier(contract["function_name"], where, function=True)
                    _literal(contract["description"], where)
                    _backend_schema(contract["request_schema"], f"{where}.{operation}.request_schema")
                    _backend_schema(contract["output_schema"], f"{where}.{operation}.output_schema")
    return config


def _operation_contract(spec: dict) -> tuple[dict, dict, dict]:
    """Keep original POST bodies/GET query schemas for code-owned backend calls."""
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method == "post":
                if operation.get("parameters"):
                    raise PortfolioError("POST parameter bindings require an explicitly supported assembly contract")
                try:
                    if set(operation["requestBody"]["content"]) != {"application/json"}:
                        raise PortfolioError("only the declared JSON request body transport is supported")
                    request = operation["requestBody"]["content"]["application/json"]["schema"]
                except (KeyError, TypeError) as exc:
                    raise PortfolioError("OpenAPI operation has no supported JSON request schema") from exc
            else:
                properties, required = {}, []
                for parameter in operation.get("parameters", []):
                    _keys(parameter, {"name", "in", "schema"}, "OpenAPI query parameter",
                          {"required", "description"})
                    if parameter.get("in") != "query" or "schema" not in parameter:
                        raise PortfolioError("only explicit typed GET query parameters are supported")
                    if type(parameter.get("required", False)) is not bool:
                        raise PortfolioError("OpenAPI parameter required must be a boolean")
                    name = parameter["name"]
                    if name in properties:
                        raise PortfolioError("duplicate OpenAPI query parameter")
                    properties[name] = parameter["schema"]
                    if parameter.get("required", False):
                        required.append(name)
                request = {"type": "object", "properties": properties, "required": required,
                           "additionalProperties": False}
            try:
                response = operation["responses"]["200"]["content"]["application/json"]["schema"]
            except (KeyError, TypeError) as exc:
                raise PortfolioError("OpenAPI operation has no supported JSON response schema") from exc
            return (_backend_schema(request, operation["operationId"] + ".request"),
                    _backend_schema(response, operation["operationId"] + ".response"), {
                "method": method.upper(), "path": path,
                "operation_id": operation["operationId"],
                "request_assembly": "trusted_workflow_only",
                "source_operation": deepcopy(operation),
            })
    raise PortfolioError("missing OpenAPI operation")


def compile_dispatch(
    source: CompiledPortfolio, bindings: Mapping | None,
    *, search_approvals: Mapping[str, str] | None = None,
    search_budget_approvals: Mapping[str, str] | None = None,
) -> CompiledPortfolio:
    config = _configuration(source, bindings)

    def approval_registry(raw: Mapping | None, name: str) -> dict:
        if raw is not None and not isinstance(raw, Mapping):
            raise PortfolioError(f"{name} must map binding SHA256 to trusted evidence ID")
        registry = dict(raw) if raw is not None else {}
        for fingerprint, evidence_id in registry.items():
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise PortfolioError(f"{name} key must be a binding SHA256")
            _literal(evidence_id, f"{name} evidence ID")
        return registry

    approvals = approval_registry(search_approvals, "search_approvals")
    budget_approvals = approval_registry(search_budget_approvals, "search_budget_approvals")
    tools, functions, blockers = [], [], []
    function_names = set()

    def add_function(
        name: str, description: str, requirement, output: dict, request: dict | None,
        operation: str, binding: dict | None, *, target: str | None = None,
    ) -> None:
        if name in function_names or name == "search_knowledge":
            raise PortfolioError(f"duplicate/colliding exposed function name: {name}")
        function_names.add(name)
        declared = requirement.source.get("type", "connected_agent")
        model_output = output
        if name == "evaluateChangeRequest":
            model_output = {"anyOf": [
                {"allOf": [output, {"not": {
                    "required": ["status"], "properties": {"status": {"enum": ["deferred"]}},
                }}]},
                GATE_DEFERRED_SCHEMA,
            ]}
            description += (
                " Read only the exact finalized remediation-planner revision. If it is not finalized, "
                "return explicit deferred status, never a gate decision. Deferred cannot release comms. "
                "Trusted code must evaluate the exact final proposal before delegate_incident_comms."
            )
        functions.append(FunctionRequirement(
            name, description + " " + BACKEND_CONTEXT_RULE, requirement.agents, declared,
            requirement.name, operation, target, canonical_json(NO_ARGUMENTS),
            canonical_json(model_output), canonical_json(request) if request is not None else None,
            canonical_json(binding) if binding is not None else None,
            _hash(requirement.specification) if requirement.specification is not None else None,
            canonical_json({
                "source_auth": requirement.source.get("auth"),
                "identity_intent_only": True,
                "worker_authorization_required": True,
                "no_model_supplied_credentials": True,
            }),
            backend_output_schema_json=canonical_json(output),
        ))

    for requirement in source.tools:
        kind, name = requirement.kind, requirement.name
        bound = config.get(kind, {}).get(name)
        required_fields: tuple[str, ...] = ()
        missing: list[str] = []
        if kind == "connected_agent":
            bound = {
                "backend": "trusted_foundry_delegate", "target_agent": name,
                "version_source": "runtime_version_journal",
                "dispatch_function": DELEGATIONS[name], "context_source": "trusted_workflow",
            }
            add_function(DELEGATIONS[name], f"Delegate this workflow stage to the real {name} agent.",
                         requirement, source.agent(name).output_schema, None, name, bound, target=name)
        elif kind == "openapi":
            required_fields = ("backend", "handler+auth OR base_url+audience")
            if bound is None:
                missing = [f"openapi.{name}: explicit worker backend binding required"]
            request, output, operation = _operation_contract(requirement.specification)
            backend = {**(bound or {}), **operation}
            if operation["operation_id"] == "evaluateChangeRequest":
                backend.update({
                    "requires_finalized_stage": "remediation-planner",
                    "evaluates": "exact_final_planner_output",
                    "deferred_output": {"status": "deferred", "reason": "finalized_planner_output_required"},
                    "comms_requires_authoritative_gate_receipt": True,
                })
            add_function(operation["operation_id"], f"Request the code-owned {operation['operation_id']} stage.",
                         requirement, output, request, operation["operation_id"], backend if bound else None)
            if bound is not None:
                bound = backend
        elif kind == "mcp":
            required_fields = MCP_FIELDS
            if bound is None:
                missing = [f"mcp.{name}.{field}: explicit actual backend binding required"
                           for field in required_fields]
            else:
                for operation in bound["allowed_tools"]:
                    contract = bound["operations"][operation]
                    backend = {
                        "backend": bound["backend"], "handler": bound["handler"], "auth": bound["auth"],
                        "backend_operation": operation, "source_server_label": requirement.source["server_label"],
                        "source_endpoint_declaration": requirement.source["server_url"],
                        "operation_contract_sha256": _hash(contract),
                    }
                    add_function(contract["function_name"], contract["description"], requirement,
                                 contract["output_schema"], contract["request_schema"], operation, backend)
        elif kind == "search":
            if bound is not None and bound.get("backend") == "registered_handler":
                required_fields = TRUSTED_SEARCH_FIELDS
                bound = {
                    **bound, "effective_backend_binding": trusted_search_binding(bound),
                    "scope_source": "trusted_workflow_and_approved_snapshot",
                    "requires_sql_eligibility_revalidation": True,
                    "native_model_tool": False,
                }
                add_function(
                    "search_approved_knowledge",
                    "Retrieve only currently eligible knowledge in the trusted workflow's approved snapshot.",
                    requirement, bound["output_schema"], bound["request_schema"],
                    "search_knowledge", bound,
                )
            else:
                required_fields = (*SEARCH_FIELDS, "separate_verified_binding_approval",
                               "separate_verified_hidden_round_budget_approval")
                if bound is None:
                    missing = [f"search.{name}.{field}: explicit scoped isolation binding required"
                               for field in required_fields]
                else:
                    fingerprint = search_binding_fingerprint(bound)
                    if fingerprint not in approvals:
                        missing = [f"search.{name}: native isolation approval required for binding {fingerprint}"]
                    if fingerprint not in budget_approvals:
                        missing.append(
                            f"search.{name}: compatible-runtime hidden tool/model round budget approval "
                            f"required for binding {fingerprint}; function-only runtime remains blocked"
                        )
                    bound = {
                        **bound, "effective_sdk_binding": scoped_search_binding(bound),
                        "binding_sha256": fingerprint, "isolation_evidence_id": approvals.get(fingerprint),
                        "bounded_runtime_evidence_id": budget_approvals.get(fingerprint),
                        "isolation_verification_owner": "trusted caller, not compiler or model",
                    }
        else:
            raise PortfolioError(f"unsupported declared source kind: {kind}")
        blockers.extend(missing)
        tools.append(replace(
            requirement, binding_json=canonical_json(bound) if bound is not None else None,
            required_binding_fields=required_fields, blocked_fields=tuple(missing),
        ))
    agents = []
    for agent in source.agents:
        deployment = config["models"].get(agent.model_reference)
        if deployment is None:
            blockers.append(f"models.{agent.model_reference}: explicit deployment binding required")
        names = tuple(function.name for function in functions if agent.name in function.agents)
        instructions = agent.instructions
        if "search_approved_knowledge" in names:
            instructions += (
                "\n\nActive trusted Search binding: call search_approved_knowledge with {} only. "
                "No native Search tool is installed for this binding. The trusted backend retains "
                "vector_semantic_hybrid, top_k=3 and the approved snapshot filter, and must bind "
                "producer/run scope and revalidate SQL eligibility. Never provide query, scope, "
                "credentials or snapshot overrides as model arguments.\n"
            )
        agents.append(replace(agent, model_deployment=deployment, function_names=names,
                              instructions=instructions))
    material = {
        "mode": "trusted_dispatch", "source_sha256": source.content_sha256,
        "bindings": config, "tools": [asdict(tool) for tool in tools],
        "functions": [asdict(function) for function in functions],
        "agents": [asdict(agent) for agent in agents],
        "blockers": sorted(set(blockers)), "live_gates": LIVE_GATES,
    }
    return replace(
        source, mode="trusted_dispatch", agents=tuple(agents), tools=tuple(tools), functions=tuple(functions),
        bindings_json=canonical_json(config), blockers=tuple(sorted(set(blockers))),
        content_sha256=_hash(material),
    )


def validate_function_arguments(function: FunctionRequirement, arguments: dict) -> None:
    """Reject any attempt to supply context, scope, target, query, URL or budget."""
    if not isinstance(arguments, dict) or arguments:
        raise PortfolioError(f"{function.name}: model arguments must be exactly {{}}")


def validate_backend_request(function: FunctionRequirement, request: dict) -> None:
    """Validate a *worker-assembled* request; this never assembles or authorizes it."""
    from jsonschema import Draft202012Validator, FormatChecker

    if function.backend_request_schema is None:
        raise PortfolioError("delegation context is a separate trusted workflow contract")
    try:
        canonical_json(request)
        Draft202012Validator(function.backend_request_schema, format_checker=FormatChecker()).validate(request)
    except Exception as exc:
        raise PortfolioError(f"{function.name}: invalid worker backend request: {exc}") from exc


def build_dispatch_sdk_definitions(portfolio: CompiledPortfolio) -> dict[str, Any]:
    if portfolio.mode != "trusted_dispatch" or not portfolio.bindings_complete:
        raise PortfolioError("SDK construction blocked:\n" + "\n".join(portfolio.blockers))
    from azure.ai.projects.models import (
        AISearchIndexResource, AzureAISearchTool, AzureAISearchToolResource, FunctionTool,
        PromptAgentDefinition, PromptAgentDefinitionTextOptions, TextResponseFormatJsonSchema,
    )

    function_tools = {}
    for function in portfolio.functions:
        expected = {
            "type": "function", "name": function.name, "description": function.description,
            "parameters": function.parameters, "output_schema": function.output_schema, "strict": True,
        }
        tool = FunctionTool(
            name=function.name, description=function.description, parameters=function.parameters,
            output_schema=function.output_schema, strict=True,
        )
        if tool.as_dict() != expected:
            raise PortfolioError(f"{function.name}: SDK dropped function contract fields")
        function_tools[function.name] = tool
    search_requirement = next(tool for tool in portfolio.tools if tool.kind == "search")
    search_binding = search_requirement.binding
    search_tool = None
    if not search_binding or search_binding.get("backend") != "registered_handler":
        if (not search_binding or not search_binding.get("isolation_evidence_id")
                or not search_binding.get("bounded_runtime_evidence_id")):
            raise PortfolioError("native Search requires exact isolation AND compatible-runtime budget approvals")
        index = search_binding["effective_sdk_binding"]
        search_tool = AzureAISearchTool(
            azure_ai_search=AzureAISearchToolResource(indexes=[AISearchIndexResource(**index)]))
        if search_tool.as_dict()["azure_ai_search"]["indexes"] != [index]:
            raise PortfolioError("SDK dropped native Search snapshot/filter/connection semantics")
    definitions = {}
    for agent in portfolio.agents:
        tools = [function_tools[name] for name in agent.function_names]
        if "search_knowledge" in agent.tool_names and search_tool is not None:
            tools.append(search_tool)
        definition = PromptAgentDefinition(
            model=agent.model_deployment, instructions=agent.instructions, temperature=agent.temperature,
            top_p=agent.top_p, tools=tools,
            text=PromptAgentDefinitionTextOptions(format=TextResponseFormatJsonSchema(
                name=agent.name.replace("-", "_") + "_result", schema=agent.output_schema, strict=True,
            )),
        )
        serialized = definition.as_dict()
        if (serialized.get("model") != agent.model_deployment
                or serialized.get("instructions") != agent.instructions
                or serialized.get("temperature") != agent.temperature
                or serialized.get("top_p") != agent.top_p
                or serialized.get("text", {}).get("format", {}).get("schema") != agent.output_schema
                or serialized.get("tools") != [tool.as_dict() for tool in tools]):
            raise PortfolioError(f"{agent.name}: SDK dropped prompt, schema, model settings or tools")
        definitions[agent.name] = definition
    return definitions
