"""Production-only review app factory; no local credential or database fallback."""

from __future__ import annotations

import ctypes
import os
from typing import Mapping

from .consumer.contracts import ContractError, strict_json
from .consumer.sql_connect import managed_identity_sql_factory
from .consumer.storage import SqlServerWorkflowStore
from .consumer.storage_contract import IntegrityConflict, WorkRequest
from .review import ConsumerWorkAdapter, EntraAuthenticator, SqlServerReviewRepository, create_app
from .review.contracts import identity_oid, source


def verify_sql_native_runtime() -> dict:
    import mssql_python

    provider = mssql_python.get_native_provider_info()
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL
    try:
        loader(provider["driver_path"])
    except OSError as exc:
        raise RuntimeError("SQL native provider cannot load on this host; deployment is not ready") from exc
    return {"provider": provider["id"], "version": provider["version"]}


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if (not isinstance(value, str) or not value.strip()
            or value.startswith("@Microsoft.KeyVault(") or "${" in value):
        raise ContractError(f"{name} is absent or unresolved")
    return value


def _scope_configuration(content: str) -> tuple[dict, dict]:
    if len(content.encode("utf-8")) > 64_000:
        raise ContractError("review scope configuration exceeds its supported bound")
    value = strict_json(content)
    if (not isinstance(value, dict) or set(value) != {"sources", "entitlements"}
            or not isinstance(value["sources"], list) or not isinstance(value["entitlements"], list)):
        raise ContractError("review scope configuration requires sources and entitlements arrays")
    catalog, entitlements = {}, {}
    groups = {}
    for entry in value["sources"]:
        entry = source(entry)
        key = (entry["producer_key"], entry["run_id"])
        domains = groups.setdefault(key, {})
        if entry["domain"] in domains:
            raise ContractError("duplicate source catalog entry")
        domains[entry["domain"]] = entry
    for key, domains in groups.items():
        for domain, entry in domains.items():
            catalog[key if len(domains) == 1 else (*key, domain)] = entry
    for entry in value["entitlements"]:
        if (not isinstance(entry, dict) or not {"oid", "producer_key", "run_id"} <= set(entry)
                or set(entry) - {"oid", "producer_key", "run_id", "domain"}):
            raise ContractError("invalid server-held entitlement entry")
        actor = identity_oid(entry["oid"])
        key = (entry["producer_key"], entry["run_id"])
        domains = groups.get(key, {})
        if not domains or ("domain" in entry and entry["domain"] not in domains):
            raise ContractError("entitlement does not reference an explicit source catalog entry")
        if len(domains) > 1 and "domain" not in entry:
            raise ContractError("multi-domain source requires explicit domain entitlements")
        entitlements.setdefault(actor, set()).add((*key, entry["domain"]) if "domain" in entry else key)
    return catalog, entitlements


def create_review_app(environment: Mapping[str, str] | None = None, *, work_advancer=None, publication_advancer=None):
    environment = os.environ if environment is None else environment
    values = {name: _required(environment, name) for name in (
        "ITOPS_SQL_SERVER", "ITOPS_SQL_DATABASE", "AZURE_CLIENT_ID",
        "ENTRA_TENANT_ID", "ENTRA_AUDIENCE", "ITOPS_PUBLIC_ORIGIN",
        "ITOPS_REVIEW_SECRET_KEY", "ITOPS_REVIEW_SCOPES_JSON", "ITOPS_ALLOW_EASYAUTH",
    )}
    if values["ITOPS_ALLOW_EASYAUTH"] not in ("true", "false"):
        raise ContractError("ITOPS_ALLOW_EASYAUTH must be explicitly true or false")
    catalog, entitlements = _scope_configuration(values["ITOPS_REVIEW_SCOPES_JSON"])
    verify_sql_native_runtime()
    factory = managed_identity_sql_factory(values["ITOPS_SQL_SERVER"], values["ITOPS_SQL_DATABASE"],
                                          values["AZURE_CLIENT_ID"])
    repository = SqlServerReviewRepository(factory)
    queue = ConsumerWorkAdapter(SqlServerWorkflowStore(factory), WorkRequest, conflict_type=IntegrityConflict)
    auth = EntraAuthenticator(values["ENTRA_TENANT_ID"], values["ENTRA_AUDIENCE"],
                              allow_easyauth=values["ITOPS_ALLOW_EASYAUTH"] == "true")
    return create_app({
        "TESTING": False, "PUBLIC_ORIGIN": values["ITOPS_PUBLIC_ORIGIN"],
        "SECRET_KEY": values["ITOPS_REVIEW_SECRET_KEY"],
    }, repository=repository, work_submitter=queue, source_catalog=catalog,
        entitlements=entitlements, authenticator=auth, work_advancer=work_advancer,
        publication_advancer=publication_advancer)
