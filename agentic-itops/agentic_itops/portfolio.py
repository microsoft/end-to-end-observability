"""Offline all-nine manifest compiler and explicitly opt-in SDK construction.

``compile_portfolio(root, bindings=None, mode="trusted_dispatch")`` reads source files only. Missing
deployment bindings produce a blocked, deterministic review plan, never a fake
live endpoint. Supplying malformed/unknown bindings is an error. No credentials,
environment expansion, Azure imports, authentication or network are used by the
compiler. ``build_sdk_definitions(plan)`` is a separate, optional import boundary.

The approved target is trusted function-call dispatch: the real 00 prompt agent
coordinates four real specialists through no-argument delegation functions.
OpenAPI/MCP backends are mediated, not discarded. Only proven, snapshot-scoped
Search remains native. See ``portfolio_dispatch`` for the complete binding API.
Exact deployed *agent* versions belong to the runner's separate version journal;
model source names are never silently treated as deployment bindings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

import yaml

from .portfolio_schemas import (
    CONTRACT_VERSION, INDUSTRIES, PortfolioError, output_schema, validate_schema,
)


AGENT_FOLDERS = {
    "00-ops-orchestrator": "ops-orchestrator",
    "02-triage-dedup": "triage-dedup",
    "03-rca-investigator": "rca-investigator",
    "04-runbook-memory": "runbook-memory",
    "05-remediation-planner": "remediation-planner",
    "07-incident-comms": "incident-comms",
    "08-postmortem-drafter": "postmortem-drafter",
    "09-alert-hygiene": "alert-hygiene",
    "10-industry-signal-pack": "industry-signal-pack",
}
SPECIALISTS = ("rca-investigator", "runbook-memory", "remediation-planner", "incident-comms")
OPERATIONS = {
    "signal_normalizer": ("signal-normalizer.yaml", "normalizeSignals"),
    "change_safety_gate": ("change-safety-gate.yaml", "evaluateChangeRequest"),
    "log_analytics": ("log-analytics.yaml", "runKqlQuery"),
    "resource_graph": ("resource-graph.yaml", "getResourceContext"),
    "change_history": ("change-history.yaml", "getRecentChanges"),
    "alert_hygiene": ("alert-hygiene.yaml", "analyzeAlertHygiene"),
}
TOOLS_BY_AGENT = {
    "ops-orchestrator": ("signal_normalizer", "change_safety_gate"),
    "triage-dedup": ("resource_graph",),
    "rca-investigator": ("change_history", "log_analytics", "resource_graph", "azure_monitor"),
    "runbook-memory": ("search_knowledge",),
    "remediation-planner": ("search_knowledge", "resource_graph"),
    "incident-comms": (),
    "postmortem-drafter": ("search_knowledge", "change_history"),
    "alert-hygiene": ("alert_hygiene",),
    "industry-signal-pack": ("search_knowledge",),
}
LIVE_GATES = (
    "Server-side acceptance of model settings, strict schemas, no-argument FunctionTool wrappers and native Search.",
    "Exact deployed agent versions and version-pinned invocation receipts, separate from model bindings.",
    "Trusted worker dispatch registry, intermediate validation, replay protection and code-owned workflow correlation.",
    "Global nested model/tool budgets and actual memory -> RCA -> planner -> code gate -> comms ordering.",
    "Effective identity/RBAC and project-connection authentication; source roles are intent, not assignments.",
    "Search approved snapshot/revision, filter applicability, revocation and actual vector_semantic_hybrid retrieval.",
    "Real authenticated six-operation backends and exact MCP operation contracts/allowlist; stubs are not live transports.",
    "OpenAPI request/response contracts remain source fidelity, not a hardened observation-query or target authorization boundary.",
    "Grounding, citation revisions/eligibility, exact target and code-gate receipts; schemas alone prove none of these.",
    "Synthetic evidence remains non-operational; publication/sending/execution require separate authorized systems.",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise PortfolioError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _load_yaml(path: Path) -> dict:
    try:
        if path.stat().st_size > 1_000_000:
            raise PortfolioError(f"source exceeds 1 MB: {path.name}")
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
        canonical_json(value)
        if not isinstance(value, dict):
            raise PortfolioError(f"expected object in {path.name}")
        return value
    except PortfolioError:
        raise
    except (OSError, ValueError, TypeError, RecursionError, yaml.YAMLError) as exc:
        raise PortfolioError(f"cannot load {path.name}: {exc}") from exc


def _keys(value: Any, required: set[str], where: str, optional: set[str] | None = None) -> None:
    if not isinstance(value, dict):
        raise PortfolioError(f"{where}: expected object")
    missing, extra = required - set(value), set(value) - required - (optional or set())
    if missing or extra:
        raise PortfolioError(f"{where}: missing {sorted(missing)}, unknown {sorted(extra)}")


def _literal(value: Any, where: str) -> str:
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or any(token in value for token in ("{", "}", "<", ">", "...", "\x00", "\n", "\r"))
            or value.lower() in {"todo", "tbd", "placeholder", "changeme", "unknown", "none",
                                 "deployment_name", "connection_id", "base_url"}
            or re.search(r"(?i)(?:^|[/:._-])(?:your[-_]|replace[-_]?(?:me|with)|placeholder)", value)):
        raise PortfolioError(f"{where}: explicit non-placeholder string required")
    return value


def _url(value: Any, where: str) -> str:
    _literal(value, where)
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise PortfolioError(f"{where}: invalid URL") from exc
    decoded = unquote(value)
    _literal(decoded, where)
    if (parts.scheme != "https" or not host or parts.username or parts.password
            or parts.query or parts.fragment or "\\" in value or any(c.isspace() for c in value)
            or "\\" in decoded or any(segment in {".", ".."} for segment in unquote(parts.path).split("/"))
            or host in {"localhost", "127.0.0.1", "::1", "example.com", "example.org", "example.net"}
            or host.endswith((".invalid", ".test", ".example", ".localhost", ".example.com",
                              ".example.org", ".example.net")) or port == 0):
        raise PortfolioError(f"{where}: concrete HTTPS endpoint without credentials/query required")
    return value


def _path(root: Path, base: Path, relative: Any, where: str) -> Path:
    _literal(relative, where)
    normalized = relative.replace("\\", "/")
    if Path(normalized).is_absolute() or ":" in normalized:
        raise PortfolioError(f"{where}: relative source path required")
    path = (base / normalized).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise PortfolioError(f"{where}: missing file or source path escapes its allowed root")
    return path


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class ToolRequirement:
    name: str
    kind: str
    agents: tuple[str, ...]
    operation_ids: tuple[str, ...]
    source_json: str
    specification_json: str | None
    binding_json: str | None
    required_binding_fields: tuple[str, ...]
    blocked_fields: tuple[str, ...]

    @property
    def source(self) -> dict:
        return json.loads(self.source_json)

    @property
    def binding(self) -> dict | None:
        return json.loads(self.binding_json) if self.binding_json is not None else None

    @property
    def specification(self) -> dict | None:
        return json.loads(self.specification_json) if self.specification_json is not None else None


@dataclass(frozen=True)
class CompiledAgent:
    name: str
    folder: str
    source_owner: str
    source_manifest_json: str
    instructions: str
    schema_json: str
    model_reference: str
    model_deployment: str | None
    temperature: float
    top_p: float
    tool_names: tuple[str, ...]
    connected_agents: tuple[str, ...]
    source_files: tuple[SourceFile, ...]
    content_sha256: str
    function_names: tuple[str, ...] = ()

    @property
    def output_schema(self) -> dict:
        return json.loads(self.schema_json)

    @property
    def source_manifest(self) -> dict:
        return json.loads(self.source_manifest_json)

    @property
    def identity_intent(self) -> dict:
        return self.source_manifest["identity"]


@dataclass(frozen=True)
class FunctionRequirement:
    """Model-facing wrapper plus original code-owned backend contract."""

    name: str
    description: str
    agents: tuple[str, ...]
    declared_type: str
    source_tool: str
    backend_operation: str
    target_agent: str | None
    parameters_json: str
    output_schema_json: str
    backend_request_schema_json: str | None
    backend_binding_json: str | None
    source_spec_sha256: str | None
    auth_requirements_json: str
    backend_output_schema_json: str | None = None
    context_source: str = "trusted_workflow"

    @property
    def parameters(self) -> dict:
        return json.loads(self.parameters_json)

    @property
    def output_schema(self) -> dict:
        return json.loads(self.output_schema_json)

    @property
    def backend_request_schema(self) -> dict | None:
        return json.loads(self.backend_request_schema_json) if self.backend_request_schema_json else None

    @property
    def backend_binding(self) -> dict | None:
        return json.loads(self.backend_binding_json) if self.backend_binding_json else None

    @property
    def auth_requirements(self) -> dict:
        return json.loads(self.auth_requirements_json)

    @property
    def backend_output_schema(self) -> dict:
        return json.loads(self.backend_output_schema_json) if self.backend_output_schema_json else self.output_schema


@dataclass(frozen=True)
class CompiledPortfolio:
    contract_version: str
    agents: tuple[CompiledAgent, ...]
    tools: tuple[ToolRequirement, ...]
    bindings_json: str
    source_inventory: tuple[SourceFile, ...]
    blockers: tuple[str, ...]
    live_gates: tuple[str, ...]
    content_sha256: str
    mode: str = "trusted_dispatch"
    functions: tuple[FunctionRequirement, ...] = ()

    @property
    def bindings_complete(self) -> bool:
        return not self.blockers

    def agent(self, name: str) -> CompiledAgent:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise PortfolioError(f"unknown compiled agent: {name}")

    def functions_for(self, agent_name: str) -> tuple[FunctionRequirement, ...]:
        self.agent(agent_name)
        return tuple(function for function in self.functions if agent_name in function.agents)

    def function_schemas(self, agent_name: str) -> dict[str, dict]:
        return {function.name: function.parameters for function in self.functions_for(agent_name)}

    def to_dict(self) -> dict:
        value = asdict(self)
        value["bindings"] = json.loads(value.pop("bindings_json"))
        for agent in value["agents"]:
            agent["source_manifest"] = json.loads(agent.pop("source_manifest_json"))
            agent["output_schema"] = json.loads(agent.pop("schema_json"))
        for tool in value["tools"]:
            for field, target in (("source_json", "source"), ("specification_json", "specification"),
                                  ("binding_json", "binding")):
                raw = tool.pop(field)
                tool[target] = json.loads(raw) if raw is not None else None
        for function in value["functions"]:
            for field in (
                "parameters", "output_schema", "backend_request_schema",
                "backend_binding", "auth_requirements", "backend_output_schema",
            ):
                raw = function.pop(field + "_json")
                function[field] = json.loads(raw) if raw is not None else None
        value["bindings_complete"] = self.bindings_complete
        value["is_live"] = False
        value["identity_declarations_are_intent_only"] = True
        value["non_sdk_fields"] = {
            "name_display_name_description": "agent registration metadata retained in source_manifest",
            "identity": "declared role intent; effective assignments require separate verification",
            "industry_parameters": "runner input validation; not silently substituted into instructions",
            "backend_auth": "source managed identity intent is retained and must be satisfied by worker backends",
            "mcp_note": "source stub warning retained; not a remote server implementation",
        }
        return value

    def to_json(self) -> str:
        return canonical_json(self.to_dict()) + "\n"


def _openapi(root: Path, folder: Path, tool: dict) -> tuple[Path, dict, tuple[str, ...]]:
    path = _path(root, folder, tool["spec"], f"{tool['name']}.spec")
    filename, operation_id = OPERATIONS[tool["name"]]
    if path != (root / "tools" / "openapi" / filename).resolve():
        raise PortfolioError(f"{tool['name']}: wrong source specification")
    spec = _load_yaml(path)
    _keys(spec, {"openapi", "info", "servers", "paths", "components"}, f"{path.name}")
    if spec["openapi"] != "3.0.3" or not isinstance(spec["paths"], dict) or not spec["paths"]:
        raise PortfolioError(f"{path.name}: unsupported or missing OpenAPI contract")
    servers = spec["servers"]
    if (not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict)
            or servers[0].get("url") != "${AGENTIC_ITOPS_SKILL_BASE_URL}"):
        raise PortfolioError(f"{path.name}: expected explicit deployment-bound source server")
    _keys(servers[0], {"url"}, path.name, {"description"})
    operations = []
    for route, methods in spec["paths"].items():
        if not route.startswith("/") or not isinstance(methods, dict) or not methods:
            raise PortfolioError(f"{path.name}: invalid operation path")
        for method, operation in methods.items():
            if method not in {"get", "post"} or not isinstance(operation, dict):
                raise PortfolioError(f"{path.name}: unsupported operation declaration")
            if (not isinstance(operation.get("operationId"), str)
                    or not isinstance(operation.get("responses"), dict) or not operation["responses"]):
                raise PortfolioError(f"{path.name}: missing operation ID/responses")
            operations.append(operation["operationId"])
    if operations != [operation_id]:
        raise PortfolioError(f"{path.name}: exact operation {operation_id} required, got {operations}")
    from jsonschema import Draft202012Validator

    def validate_nodes(node: Any) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                reference = node["$ref"]
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    raise PortfolioError(f"{path.name}: only resolved local references are supported")
                target = spec
                try:
                    for part in reference[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError) as exc:
                    raise PortfolioError(f"{path.name}: unresolved schema reference") from exc
            for key, item in node.items():
                if key == "schema":
                    if not isinstance(item, dict) or not item:
                        raise PortfolioError(f"{path.name}: missing schema declaration")
                    try:
                        Draft202012Validator.check_schema(item)
                    except Exception as exc:
                        raise PortfolioError(f"{path.name}: invalid OpenAPI schema: {exc}") from exc
                validate_nodes(item)
        elif isinstance(node, list):
            for item in node:
                validate_nodes(item)

    validate_nodes(spec)
    # Only the declared server variable is an unresolved source token.
    material = canonical_json(spec).replace("${AGENTIC_ITOPS_SKILL_BASE_URL}", "")
    if "${" in material or "{{" in material:
        raise PortfolioError(f"{path.name}: unknown placeholder")
    return path, spec, tuple(operations)


def _compile_sources(root: str | Path) -> CompiledPortfolio:
    """Read and preserve sources before applying the approved transport target."""
    root = Path(root).resolve()
    agents_root = root / "agents"
    if not agents_root.is_dir():
        raise PortfolioError("missing agents directory")
    folders = {path.name for path in agents_root.iterdir() if path.is_dir()}
    if folders != set(AGENT_FOLDERS):
        raise PortfolioError(f"exact nine folders required; missing {sorted(set(AGENT_FOLDERS) - folders)}, "
                             f"unknown {sorted(folders - set(AGENT_FOLDERS))}")
    inventory: dict[str, SourceFile] = {}

    def record(path: Path) -> SourceFile:
        source = SourceFile(str(path.relative_to(root)).replace("\\", "/"),
                            hashlib.sha256(path.read_bytes()).hexdigest())
        inventory[source.path] = source
        return source

    manifests = []
    requirements: dict[str, dict] = {}
    for folder, name in AGENT_FOLDERS.items():
        directory = (agents_root / folder).resolve()
        manifest_path = _path(root, directory, "agent.yaml", folder)
        manifest = _load_yaml(manifest_path)
        _keys(manifest, {
            "name", "display_name", "model", "temperature", "top_p", "description", "tools",
            "instructions_file", "identity", "output_schema",
        }, folder, {"connected_agents", "parameters"})
        if manifest["name"] != name:
            raise PortfolioError(f"{folder}: expected exact name {name}")
        for field in ("display_name", "model", "description"):
            if not isinstance(manifest[field], str) or not manifest[field].strip():
                raise PortfolioError(f"{name}: missing {field}")
        _literal(manifest["model"], f"{name}.model")
        for field, maximum in (("temperature", 2), ("top_p", 1)):
            number = manifest[field]
            if type(number) not in (int, float) or not 0 <= number <= maximum:
                raise PortfolioError(f"{name}: invalid {field}")
        identity = manifest["identity"]
        _keys(identity, {"type", "required_roles"}, f"{name}.identity")
        if identity != {"type": "user_assigned_managed_identity",
                        "required_roles": ["Monitoring Reader", "Reader"]}:
            raise PortfolioError(f"{name}: unreviewed identity intent")
        connected = manifest.get("connected_agents", [])
        if connected != (list(SPECIALISTS) if name == "ops-orchestrator" else []):
            raise PortfolioError(f"{name}: exact four specialist connections required only on 00")
        if "connected_agents" in manifest and name != "ops-orchestrator":
            raise PortfolioError(f"{name}: undeclared connected_agents field")
        parameters = manifest.get("parameters")
        if name == "industry-signal-pack":
            expected = {"industry": {"type": "string", "enum": list(INDUSTRIES),
                                     "default": "energy_utilities"}}
            if parameters != expected:
                raise PortfolioError("industry-signal-pack: all six parameter choices must be preserved")
        elif "parameters" in manifest:
            raise PortfolioError(f"{name}: unsupported parameters")
        validate_schema(manifest["output_schema"])
        if manifest["output_schema"] != output_schema(name):
            raise PortfolioError(f"{name}: output schema drift from {CONTRACT_VERSION}")
        instructions_path = _path(directory, directory, manifest["instructions_file"],
                                  f"{name}.instructions_file")
        if instructions_path.stat().st_size > 200_000:
            raise PortfolioError(f"{name}: instructions exceed 200 KB")
        instructions = instructions_path.read_text(encoding="utf-8")
        if not instructions.strip():
            raise PortfolioError(f"{name}: empty instructions")
        source_files = [record(manifest_path), record(instructions_path)]
        tools = manifest["tools"]
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise PortfolioError(f"{name}: tools must be a list of objects")
        if [tool.get("name") for tool in tools] != list(TOOLS_BY_AGENT[name]):
            raise PortfolioError(f"{name}: missing, unknown or reordered tool binding")
        for tool in tools:
            tool_name = tool["name"]
            operations: tuple[str, ...] = ()
            spec = None
            if tool_name in OPERATIONS:
                _keys(tool, {"type", "name", "spec", "auth"}, tool_name)
                if tool["type"] != "openapi" or tool["auth"] != "managed_identity":
                    raise PortfolioError(f"{tool_name}: OpenAPI managed identity intent required")
                path, spec, operations = _openapi(root, directory, tool)
                source_files.append(record(path))
                kind = "openapi"
            elif tool_name == "search_knowledge":
                expected = {"type": "azure_ai_search", "name": tool_name,
                            "index": "agentic-itops-knowledge",
                            "query_type": "vector_semantic_hybrid", "top_k": 3}
                if tool != expected or type(tool.get("top_k")) is not int:
                    raise PortfolioError("Search must preserve index, vector_semantic_hybrid and top_k 3")
                kind = "search"
            elif tool_name == "azure_monitor":
                _keys(tool, {"type", "name", "server_label", "server_url", "auth", "note"}, tool_name)
                if (tool["type"] != "mcp" or tool["server_label"] != "azure-monitor"
                        or tool["server_url"] != "${AZURE_MONITOR_MCP_URL}"
                        or tool["auth"] != "managed_identity"
                        or not isinstance(tool["note"], str) or not tool["note"].strip()):
                    raise PortfolioError("azure_monitor: invalid declared MCP binding")
                kind = "mcp"
            else:
                raise PortfolioError(f"unknown tool: {tool_name}")
            if tool_name in requirements:
                prior = requirements[tool_name]
                if prior["source"] != tool or prior["specification"] != spec:
                    raise PortfolioError(f"{tool_name}: conflicting repeated tool declarations")
                prior["agents"].append(name)
            else:
                requirements[tool_name] = {
                    "kind": kind, "source": tool, "specification": spec,
                    "operations": operations, "agents": [name],
                }
        manifests.append((folder, manifest, instructions, tuple(source_files)))
    for name in SPECIALISTS:
        requirements[name] = {
            "kind": "connected_agent", "source": {"connected_agent": name}, "specification": None,
            "operations": (), "agents": ["ops-orchestrator"],
        }
    compiled_agents = []
    for folder, manifest, instructions, source_files in manifests:
        model = manifest["model"]
        material = {"manifest": manifest, "instructions": instructions,
                    "source_files": [asdict(source) for source in source_files]}
        compiled_agents.append(CompiledAgent(
            manifest["name"], folder,
            "separately_authored_00" if folder.startswith("00-") else "scripts/emit_agent_manifests.py",
            canonical_json(manifest), instructions, canonical_json(manifest["output_schema"]),
            model, None, manifest["temperature"], manifest["top_p"],
            tuple(tool["name"] for tool in manifest["tools"]),
            tuple(manifest.get("connected_agents", [])), source_files, _hash(material),
        ))
    compiled_tools = []
    for name, item in sorted(requirements.items()):
        kind = item["kind"]
        compiled_tools.append(ToolRequirement(
            name, kind, tuple(item["agents"]), item["operations"], canonical_json(item["source"]),
            canonical_json(item["specification"]) if item["specification"] is not None else None,
            None, (), (),
        ))
    material = {
        "contract_version": CONTRACT_VERSION, "agents": [asdict(agent) for agent in compiled_agents],
        "tools": [asdict(tool) for tool in compiled_tools],
        "inventory": [asdict(inventory[path]) for path in sorted(inventory)],
    }
    return CompiledPortfolio(
        CONTRACT_VERSION, tuple(compiled_agents), tuple(compiled_tools), "{}",
        tuple(inventory[path] for path in sorted(inventory)), (),
        LIVE_GATES, _hash(material), mode="source_inventory",
    )


def compile_portfolio(
    root: str | Path, bindings: Mapping | None = None, *, mode: str = "trusted_dispatch",
    search_approvals: Mapping[str, str] | None = None,
    search_budget_approvals: Mapping[str, str] | None = None,
) -> CompiledPortfolio:
    """Compile the approved target; approvals map exact Search fingerprints to evidence IDs.

    The trusted caller, not a model/config self-assertion, supplies the approved
    fingerprint registries after verifying native Search isolation AND bounded
    hidden tool/model rounds in a compatible runtime. A function-only worker
    must not supply budget approvals. Unknown modes
    fail rather than silently reviving the superseded native A2A target.
    """
    if mode != "trusted_dispatch":
        raise PortfolioError(f"unsupported compiler mode: {mode}; approved target is trusted_dispatch")
    from .portfolio_dispatch import compile_dispatch

    return compile_dispatch(_compile_sources(root), bindings, search_approvals=search_approvals,
                            search_budget_approvals=search_budget_approvals)


def build_sdk_definitions(portfolio: CompiledPortfolio) -> dict[str, Any]:
    """Explicitly construct FunctionTool/native Search SDK objects without I/O."""
    from .portfolio_dispatch import build_dispatch_sdk_definitions

    return build_dispatch_sdk_definitions(portfolio)
