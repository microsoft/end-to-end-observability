"""Generate a non-secret bootstrap approval manifest from deployed service outputs."""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from .bootstrap import canonical


def create(outputs: dict, environment_name: str, scopes: dict, search_index: dict,
           package_directory: Path, expires_at: str, operations: list[str], *,
           probe_contract: dict | None = None, service_application_client_id: str | None = None,
           previous_review_scopes_sha256: str | None = None) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,19}", environment_name):
        raise ValueError("owned foundation environment name is required")
    if datetime.fromisoformat(expires_at.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("approval expiration must include its time zone")
    def value(name):
        matches = [entry for key, entry in outputs.items() if key.casefold() == name.casefold()]
        if len(matches) != 1:
            raise ValueError(f"missing or case-ambiguous deployment output: {name}")
        return matches[0]["value"]
    identities = {item["service"]: item for item in value("ITOPS_SERVICE_IDENTITIES")}
    suffix = re.fullmatch(r"app-bootstrap-([a-z0-9]{13})", value("SERVICE_BOOTSTRAP_NAME"))
    if not suffix:
        raise ValueError("unexpected bootstrap resource name")
    token = suffix[1]
    expected_hosts = {
        "review": f"app-review-{token}.azurewebsites.net",
        "bootstrap": f"app-bootstrap-{token}.azurewebsites.net",
        **{name: f"func-{name}-{token}.azurewebsites.net" for name in ("worker", "tools", "publisher")},
    }
    if value("ITOPS_SERVICE_HOSTNAMES") != expected_hosts:
        raise ValueError("actual Azure hostnames differ from the reviewed network contract; reconcile before bootstrap")
    packages = []
    if "deploy" in operations or "deploy-preflight" in operations:
        services = ("worker-preflight",) if "deploy-preflight" in operations else ("worker", "tools", "publisher")
        for service in services:
            path = package_directory / f"{service}.zip"
            target = service.removesuffix("-preflight")
            packages.append({"service": service, "file": f"packages/{service}.zip",
                             "sha256": sha256(path.read_bytes()).hexdigest(),
                             "scm_host": f"{value(f'SERVICE_{target.upper()}_NAME')}.scm.azurewebsites.net"})
    result = {
        "schema_version": 1, "deployment_id": str(uuid4()), "expires_at": expires_at,
        "bootstrap_client_id": identities["bootstrap"]["clientId"],
        "bootstrap_object_id": identities["bootstrap"]["principalId"],
        "bootstrap_site": value("SERVICE_BOOTSTRAP_NAME"),
        "sql_server": f"sql-{environment_name}-{token}.database.windows.net", "sql_database": "itops",
        "vault_url": f"https://{value('AZURE_KEY_VAULT_NAME')}.vault.azure.net",
        "search_endpoint": f"https://srch-{environment_name}-{token}.search.windows.net",
        "private_hosts": value("ITOPS_PRIVATE_HOSTNAMES"), "operations": operations,
        "runtime_object_ids": {name: identities[name]["principalId"]
                               for name in ("review", "worker", "tools", "publisher")},
        "review_scopes": scopes, "search_index": search_index, "packages": packages,
    }
    if "grants" in operations:
        result["runtime_client_ids"] = {
            name: identities[name]["clientId"] for name in ("review", "worker", "tools", "publisher")
        }
    if "review-scopes" in operations:
        if ("secrets" in operations or not isinstance(previous_review_scopes_sha256, str)
                or not re.fullmatch("[a-f0-9]{64}", previous_review_scopes_sha256)):
            raise ValueError("scope-only activation requires the prior catalog hash and no credential rotation")
        result["previous_review_scopes_sha256"] = previous_review_scopes_sha256
    elif previous_review_scopes_sha256 is not None:
        raise ValueError("prior catalog hash requires the scope-only activation operation")
    if "probe-source" in operations:
        from agentic_itops.service_preflight import validate_probe_contract
        contract = validate_probe_contract(probe_contract)
        application_id = UUID(service_application_client_id or "")
        if not application_id.int or operations != ["probe-source"]:
            raise ValueError("isolated source-probe operation and explicit service API ID required")
        result["source_probe"] = {
            "contract_sha256": contract["sha256"], "worker_client_id": identities["worker"]["clientId"],
            "token_scope": f"api://{application_id}/.default",
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-outputs", type=Path, required=True,
                        help="az deployment sub show --query properties.outputs JSON")
    parser.add_argument("--environment-name", required=True)
    parser.add_argument("--review-scopes", type=Path, required=True)
    parser.add_argument("--search-index", type=Path, required=True)
    parser.add_argument("--package-directory", type=Path, required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--operations", nargs="+", choices=["migrate", "grants", "secrets", "review-scopes", "search", "deploy", "deploy-preflight", "probe-source"],
                        default=["migrate", "grants", "secrets", "search", "deploy"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-contract", type=Path)
    parser.add_argument("--service-application-client-id")
    parser.add_argument("--previous-review-scopes-sha256")
    args = parser.parse_args()
    manifest = create(json.loads(args.service_outputs.read_bytes()), args.environment_name,
                      json.loads(args.review_scopes.read_bytes()), json.loads(args.search_index.read_bytes()),
                      args.package_directory, args.expires_at, args.operations,
                      probe_contract=json.loads(args.probe_contract.read_bytes()) if args.probe_contract else None,
                      service_application_client_id=args.service_application_client_id,
                      previous_review_scopes_sha256=args.previous_review_scopes_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(manifest)
    args.output.write_bytes(raw)
    print(json.dumps({"deployment_id": manifest["deployment_id"], "manifest_sha256": sha256(raw).hexdigest()}))


if __name__ == "__main__":
    main()
