"""Deployment context (asre-context/v1) for the operations runtime.

Terraform emits the context and injects it into the runtime jobs as ASRE_CONTEXT
(a JSON document); tests and workstation tools set ASRE_CONTEXT_FILE instead.
There is deliberately no default: a missing, malformed or inconsistent context
raises ContextError. The loader derives every resource ID from names, so the stored
document never carries one. The optional values operator_object_id, servicenow,
identities.bootstrap and agent.endpoint may be omitted or null.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

SCHEMA = "asre-context/v1"
ENV_JSON = "ASRE_CONTEXT"
ENV_FILE = "ASRE_CONTEXT_FILE"
MAX_BYTES = 65536

GROUPS = ("control", "linux", "patch", "backup")
ROLES = ("linux", "patch", "backup")
IDENTITY_KEYS = ("agent", "runner", "collector", "pull")
_ADDRESSES = {"linux": "10.42.0.10", "patch": "10.42.0.11", "backup": "10.42.0.12"}

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SUFFIX = re.compile(r"[0-9a-f]{10}")
_SYSID = re.compile(r"[0-9a-f]{32}")
_ENDPOINT = re.compile(r"https://asre-demo-cus--[a-z0-9]+\.[a-z0-9]+\.centralus\.azuresre\.ai")
_SN_ORIGIN = re.compile(r"https://[a-z0-9-]+\.service-now\.com")
_SN_BASE = re.compile(r"/api/x_[0-9a-z]+_asre/case_adapter/v1")


class ContextError(RuntimeError):
    """The deployment context is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Identity:
    name: str
    resource_id: str
    client_id: str
    principal_id: str


@dataclass(frozen=True)
class Target:
    role: str
    name: str
    resource_group: str
    resource_id: str
    address: str


@dataclass(frozen=True)
class LogAnalytics:
    name: str
    resource_id: str
    customer_id: str


@dataclass(frozen=True)
class Registry:
    name: str
    login_server: str


@dataclass(frozen=True)
class KeyVault:
    name: str
    uri: str


@dataclass(frozen=True)
class Storage:
    account_name: str
    blob_endpoint: str
    table_endpoint: str


@dataclass(frozen=True)
class RecoveryVault:
    name: str
    resource_group: str
    resource_id: str


@dataclass(frozen=True)
class Agent:
    name: str
    resource_id: str
    endpoint: Optional[str]


@dataclass(frozen=True)
class ServiceNow:
    origin: str
    api_base: str
    client_id: str
    facade_client_id: str
    assignment_group: str
    configuration_items: Mapping[str, str]


@dataclass(frozen=True)
class Context:
    subscription_id: str
    tenant_id: str
    location: str
    name_suffix: str
    operator_object_id: Optional[str]
    resource_groups: Mapping[str, str]
    log_analytics: LogAnalytics
    registry: Registry
    key_vault: KeyVault
    storage: Storage
    recovery_vault: RecoveryVault
    identities: Mapping[str, Identity]
    targets: Mapping[str, Target]
    agent: Agent
    servicenow: Optional[ServiceNow]

    def resource_group_id(self, group: str) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_groups[group]}"

    def roles_by_resource_id(self) -> Mapping[str, str]:
        """Lower-cased target VM resource ID -> role, for matching Azure Resource Graph rows."""
        return MappingProxyType({t.resource_id.lower(): role for role, t in self.targets.items()})

    def require_agent_endpoint(self) -> str:
        if not self.agent.endpoint:
            raise ContextError("The agent endpoint is not in the deployment context; create the agent first.")
        return self.agent.endpoint

    def require_servicenow(self) -> ServiceNow:
        if self.servicenow is None:
            raise ContextError("This path needs the optional servicenow section of the deployment context.")
        return self.servicenow


_cache: Optional[Context] = None


def load() -> Context:
    """Return the process-wide context from ASRE_CONTEXT, else ASRE_CONTEXT_FILE; never a default."""
    global _cache
    if _cache is None:
        _cache = parse(_read())
    return _cache


def reset() -> None:
    """Drop the cached context (tests only)."""
    global _cache
    _cache = None


def _read() -> str:
    inline = os.environ.get(ENV_JSON, "")
    if inline.strip():
        return inline
    path = os.environ.get(ENV_FILE, "").strip()
    if not path:
        raise ContextError(f"The deployment context is not configured; set {ENV_JSON} or {ENV_FILE}.")
    try:
        with open(Path(path), "rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except OSError:
        raise ContextError("The deployment context file cannot be read.") from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ContextError("The deployment context file is not UTF-8.") from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ContextError("The deployment context has a duplicate key.")
        result[key] = value
    return result


def parse(document: Any) -> Context:
    """Validate a context document (JSON text or an already decoded object) and derive its IDs."""
    if isinstance(document, bytes):
        try:
            document = document.decode("utf-8", "strict")
        except UnicodeDecodeError:
            raise ContextError("The deployment context is not UTF-8.") from None
    if isinstance(document, str):
        try:
            size = len(document.encode("utf-8"))
        except UnicodeEncodeError:
            raise ContextError("The deployment context is not valid UTF-8 text.") from None
        if size > MAX_BYTES:
            raise ContextError("The deployment context is too large.")
        try:
            document = json.loads(document, object_pairs_hook=_pairs)
        except (ValueError, RecursionError):
            raise ContextError("The deployment context is not valid JSON.") from None
    root = _object(document, "$", ("schema", "subscription_id", "tenant_id", "location", "name_suffix",
                                   "resource_groups", "log_analytics", "registry", "key_vault", "storage",
                                   "recovery_vault", "identities", "targets", "agent"),
                   ("operator_object_id", "servicenow"))
    _equals(root["schema"], "$.schema", SCHEMA)
    sub = _match(root["subscription_id"], "$.subscription_id", _GUID)
    tenant = _match(root["tenant_id"], "$.tenant_id", _GUID)
    _equals(root["location"], "$.location", "centralus")
    suffix = _match(root["name_suffix"], "$.name_suffix", _SUFFIX)
    if suffix != hashlib.sha256(f"{sub}:asre-demo".encode()).hexdigest()[:10]:
        raise ContextError("Invalid deployment context at $.name_suffix: not derived from the subscription.")
    operator = root.get("operator_object_id")
    if operator is not None:
        operator = _match(operator, "$.operator_object_id", _GUID)

    groups = _object(root["resource_groups"], "$.resource_groups", GROUPS)
    for group in GROUPS:
        _equals(groups[group], f"$.resource_groups.{group}", f"rg-asre-{group}-cus")
    base = f"/subscriptions/{sub}/resourceGroups"
    control = f"{base}/{groups['control']}"

    law = _object(root["log_analytics"], "$.log_analytics", ("name", "customer_id"))
    _equals(law["name"], "$.log_analytics.name", "law-asre-cus")
    log_analytics = LogAnalytics(
        law["name"], f"{control}/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus",
        _match(law["customer_id"], "$.log_analytics.customer_id", _GUID))

    registry = _object(root["registry"], "$.registry", ("name", "login_server"))
    _equals(registry["name"], "$.registry.name", f"acrasre{suffix}")
    _equals(registry["login_server"], "$.registry.login_server", f"acrasre{suffix}.azurecr.io")

    vault = _object(root["key_vault"], "$.key_vault", ("name", "uri"))
    _equals(vault["name"], "$.key_vault.name", f"kv-asre-{suffix}")
    _equals(vault["uri"], "$.key_vault.uri", f"https://kv-asre-{suffix}.vault.azure.net/")

    storage = _object(root["storage"], "$.storage", ("account_name", "blob_endpoint", "table_endpoint"))
    _equals(storage["account_name"], "$.storage.account_name", f"stasre{suffix}")
    _equals(storage["blob_endpoint"], "$.storage.blob_endpoint", f"https://stasre{suffix}.blob.core.windows.net")
    _equals(storage["table_endpoint"], "$.storage.table_endpoint", f"https://stasre{suffix}.table.core.windows.net")

    rsv = _object(root["recovery_vault"], "$.recovery_vault", ("name", "resource_group"))
    _equals(rsv["name"], "$.recovery_vault.name", "rsv-asre-cus")
    _equals(rsv["resource_group"], "$.recovery_vault.resource_group", groups["backup"])
    recovery_vault = RecoveryVault(
        rsv["name"], rsv["resource_group"],
        f"{base}/{rsv['resource_group']}/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus")

    raw_identities = _object(root["identities"], "$.identities", IDENTITY_KEYS, ("bootstrap",))
    identities = {}
    for key, entry in raw_identities.items():
        if key == "bootstrap" and entry is None:
            continue
        item = _object(entry, f"$.identities.{key}", ("client_id", "principal_id"))
        name = f"id-asre-{key}-cus"
        identities[key] = Identity(
            name, f"{control}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}",
            _match(item["client_id"], f"$.identities.{key}.client_id", _GUID),
            _match(item["principal_id"], f"$.identities.{key}.principal_id", _GUID))

    raw_targets = _object(root["targets"], "$.targets", ROLES)
    targets = {}
    for role in ROLES:
        item = _object(raw_targets[role], f"$.targets.{role}", ("name", "resource_group", "address"))
        _equals(item["name"], f"$.targets.{role}.name", f"vm-asre-{role}-cus")
        _equals(item["resource_group"], f"$.targets.{role}.resource_group", f"rg-asre-{role}-cus")
        _equals(item["address"], f"$.targets.{role}.address", _ADDRESSES[role])
        targets[role] = Target(
            role, item["name"], item["resource_group"],
            f"{base}/{item['resource_group']}/providers/Microsoft.Compute/virtualMachines/{item['name']}",
            item["address"])

    raw_agent = _object(root["agent"], "$.agent", ("name",), ("endpoint",))
    _equals(raw_agent["name"], "$.agent.name", "asre-demo-cus")
    endpoint = raw_agent.get("endpoint")
    if endpoint is not None:
        endpoint = _match(endpoint, "$.agent.endpoint", _ENDPOINT)
    agent = Agent(raw_agent["name"], f"{control}/providers/Microsoft.App/agents/asre-demo-cus", endpoint)

    servicenow = None
    if root.get("servicenow") is not None:
        raw = _object(root["servicenow"], "$.servicenow",
                      ("origin", "api_base", "client_id", "facade_client_id", "assignment_group",
                       "configuration_items"))
        items = _object(raw["configuration_items"], "$.servicenow.configuration_items", ROLES)
        servicenow = ServiceNow(
            _match(raw["origin"], "$.servicenow.origin", _SN_ORIGIN),
            _match(raw["api_base"], "$.servicenow.api_base", _SN_BASE),
            _match(raw["client_id"], "$.servicenow.client_id", _SYSID),
            _match(raw["facade_client_id"], "$.servicenow.facade_client_id", _SYSID),
            _match(raw["assignment_group"], "$.servicenow.assignment_group", _SYSID),
            MappingProxyType({role: _match(items[role], f"$.servicenow.configuration_items.{role}", _SYSID)
                              for role in ROLES}))

    return Context(
        subscription_id=sub, tenant_id=tenant, location="centralus", name_suffix=suffix,
        operator_object_id=operator, resource_groups=MappingProxyType(dict(groups)),
        log_analytics=log_analytics, registry=Registry(registry["name"], registry["login_server"]),
        key_vault=KeyVault(vault["name"], vault["uri"]),
        storage=Storage(storage["account_name"], storage["blob_endpoint"], storage["table_endpoint"]),
        recovery_vault=recovery_vault, identities=MappingProxyType(identities),
        targets=MappingProxyType(targets), agent=agent, servicenow=servicenow)


def _object(value: Any, path: str, required, optional=()) -> dict:
    if not isinstance(value, dict):
        raise ContextError(f"Invalid deployment context at {path}: expected an object.")
    missing = [key for key in required if key not in value]
    if missing:
        raise ContextError(f"Invalid deployment context at {path}: missing {', '.join(missing)}.")
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        raise ContextError(f"Invalid deployment context at {path}: unknown {', '.join(map(str, unknown))}.")
    return value


def _match(value: Any, path: str, pattern: re.Pattern) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContextError(f"Invalid deployment context at {path}: unexpected form.")
    return value


def _equals(value: Any, path: str, expected: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise ContextError(f"Invalid deployment context at {path}: unexpected value.")
    return value
