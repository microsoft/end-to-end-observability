"""Operator-only, dry-run-default bootstrap window with durable SQL-admin recovery."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from uuid import UUID, uuid4
from zipfile import ZipFile

from .bootstrap import BootstrapError, Journal, canonical, load_manifest, source_witness


ARM = "https://management.azure.com"
API = "?api-version=2023-08-01"
WEB_API = "?api-version=2024-04-01"
DEPLOYMENT_ID_PATTERN = r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def approval_plan(manifest_path: Path, package: Path, subscription: str, resource_group: str,
                  bootstrap_object_id: str, original_operator_object_id: str) -> dict:
    for identity in (subscription, bootstrap_object_id, original_operator_object_id):
        if not UUID(identity).int:
            raise ValueError("approval target IDs must not be placeholders")
    if bootstrap_object_id == original_operator_object_id:
        raise ValueError("bootstrap and original SQL operator identities must differ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,90}", resource_group):
        raise ValueError("invalid resource group")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if manifest["bootstrap_object_id"].lower() != bootstrap_object_id.lower():
        raise ValueError("approved bootstrap object ID differs from manifest")
    scope = f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
    with ZipFile(package) as archive:
        if archive.read("bootstrap-manifest.json") != raw:
            raise ValueError("bootstrap ZIP does not contain exactly the approved manifest")
    sql_name = manifest["sql_server"].split(".")[0]
    if not re.fullmatch(r"sql-[a-z0-9-]+", sql_name):
        raise ValueError("invalid owned SQL server")
    return {
        "manifest_sha256": sha256(raw).hexdigest(),
        "package_sha256": sha256(package.read_bytes()).hexdigest(),
        "bootstrap_site_id": f"{scope}/providers/Microsoft.Web/sites/{manifest['bootstrap_site']}",
        "sql_admin_id": f"{scope}/providers/Microsoft.Sql/servers/{sql_name}/administrators/ActiveDirectory",
        "temporary_sql_admin_object_id": bootstrap_object_id,
        "restore_sql_admin_object_id": original_operator_object_id,
        "operations": ["stop-bootstrap", "disable-bootstrap-gate", "start-unprivileged-bootstrap",
                       "entra-scm-upload", "stop-bootstrap",
                       *(["temporary-sql-admin"] if set(manifest["operations"]) & {"migrate", "grants"} else []),
                       "bootstrap-settings", "start-bootstrap", "poll-private-journal", "stop-bootstrap",
                       *(["restore-sql-admin"] if set(manifest["operations"]) & {"migrate", "grants"} else []),
                       "remove-bootstrap-secret-setting"],
        "data_operations": manifest["operations"], "deployment_id": manifest["deployment_id"],
    }


class Operator:
    def __init__(self, credential, client):
        self.credential, self.client = credential, client

    def request(self, method, url, *, accepted=(200,), **kwargs):
        parsed = urlparse(url)
        if (parsed.scheme != "https" or
                (parsed.netloc != "management.azure.com"
                 and not re.fullmatch(r"app-bootstrap-[a-z0-9]{13}\.scm\.azurewebsites\.net", parsed.netloc))):
            raise BootstrapError("refuse unexpected authenticated control-plane host")
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = "Bearer " + self.credential.get_token(ARM + "/.default").token
        response = self.client.request(method, url, headers=headers, **kwargs)
        if response.status_code not in accepted:
            raise BootstrapError(f"approved operation failed with HTTP {response.status_code}")
        return response

    def set_admin(self, resource_id, properties, *, attempts=60):
        body = {"properties": {key: properties[key] for key in ("administratorType", "login", "sid", "tenantId")}}
        self.request("PUT", ARM + resource_id + API, json=body, accepted=(200, 201, 202))
        for _ in range(attempts):
            current = self.request("GET", ARM + resource_id + API).json()["properties"]
            if current.get("sid", "").lower() == properties["sid"].lower():
                if current.get("azureADOnlyAuthentication") is not True:
                    raise BootstrapError("SQL Entra-only authentication invariant failed")
                return
            time.sleep(5)
        raise BootstrapError("SQL administrator update did not converge; recovery is required")

    def settings(self, site_id, values: dict, remove=()):
        url = ARM + site_id + "/config/appsettings"
        current = self.request("POST", url + "/list" + WEB_API).json()["properties"]
        for key in remove:
            current.pop(key, None)
        current.update(values)
        self.request("PUT", url + WEB_API, json={"properties": current}, accepted=(200,))


def upload_bootstrap(package: Path, approved_sha256: str, scm: str, operator: Operator,
                     journal: Journal, *, max_polls: int = 180, wait=time.sleep) -> dict:
    payload = package.read_bytes()
    if sha256(payload).hexdigest() != approved_sha256:
        raise BootstrapError("bootstrap ZIP changed after approval")
    previous = journal.data.get("upload")
    if previous:
        deployment_id = previous.get("deployment_id", "")
        if (not isinstance(deployment_id, str) or not re.fullmatch(r"[a-f0-9]{32}", deployment_id)
                or previous.get("sha256") != approved_sha256
                or previous.get("state") not in {"intent", "submitted", "complete"}):
            raise BootstrapError("uncertain or uncorrelated bootstrap upload; explicit reconciliation required")
    else:
        deployment_id = uuid4().hex
    provider_id = previous.get("provider_deployment_id", deployment_id) if previous else deployment_id
    if (not isinstance(provider_id, str)
            or not re.fullmatch(DEPLOYMENT_ID_PATTERN, provider_id)
            or not UUID(provider_id).int):
        raise BootstrapError("invalid persisted provider deployment ID")
    location = f"{scm}/api/deployments/{provider_id}"
    deployer = f"itops-bootstrap-{deployment_id}"
    if previous and (previous.get("status_url") != location or previous.get("deployer") != deployer):
        raise BootstrapError("persisted bootstrap upload correlation differs from its owned target")
    record = {
        "state": "intent", "sha256": approved_sha256, "deployment_id": deployment_id,
        "deployer": deployer, "status_url": location,
    }
    if previous and "provider_deployment_id" in previous:
        record["provider_deployment_id"] = provider_id
    if not previous:
        # Some Kudu versions honor this ID; others allocate their own receipt ID.
        # The unique deployer is persisted before POST for read-only reconciliation.
        journal.save("upload", record)
        operator.request(
            "POST", f"{scm}/api/zipdeploy?isAsync=true&deployer={deployer}", accepted=(202,),
            content=payload, headers={"Content-Type": "application/zip", "SCM-DEPLOYMENT-ID": deployment_id},
        )
        # The supported async Location is /latest?deployer=...&time=..., not
        # necessarily a unique URL. Ignore it and reconcile only our unique deployer.
        journal.save("upload", {**record, "state": "submitted"})
    for _ in range(max_polls):
        response = operator.request("GET", location, accepted=(200, 202, 404, 429, 503))
        if response.status_code == 404 and "provider_deployment_id" not in record:
            inventory = operator.request("GET", f"{scm}/api/deployments", accepted=(200, 202, 429, 503))
            if inventory.status_code == 200:
                entries = inventory.json()
                if (not isinstance(entries, list) or len(entries) > 1000
                        or any(not isinstance(entry, dict) for entry in entries)):
                    raise BootstrapError("invalid or oversized deployment reconciliation inventory")
                matches = [entry for entry in entries if entry.get("deployer") == deployer]
                # Kudu exposes a pending placeholder alongside the real build receipt.
                matches = [entry for entry in matches if not (
                    isinstance(entry.get("id"), str)
                    and re.fullmatch(r"temp-[0-9a-fA-F]{8}", entry["id"])
                    and entry.get("status") == 0 and entry.get("complete") is False)]
                if len(matches) > 1:
                    raise BootstrapError("ambiguous deployment receipts for the persisted request correlation")
                if matches:
                    provider_id = matches[0].get("id")
                    if (not isinstance(provider_id, str)
                            or not re.fullmatch(DEPLOYMENT_ID_PATTERN, provider_id)
                            or not UUID(provider_id).int):
                        raise BootstrapError("invalid correlated provider deployment ID")
                    location = f"{scm}/api/deployments/{provider_id}"
                    record = {**record, "state": "submitted", "provider_deployment_id": provider_id,
                              "status_url": location}
                    journal.save("upload", record)
                    # Do not accept the inventory's success; read this exact receipt independently.
                    continue
        if response.status_code != 200:
            wait(5)
            continue
        status = response.json()
        if (not isinstance(status, dict) or status.get("id") != provider_id
                or status.get("deployer") != deployer):
            raise BootstrapError("bootstrap deployment receipt does not match its persisted request correlation")
        if status.get("status") == 4:
            receipt = {**record, "state": "complete"}
            journal.save("upload", receipt)
            return receipt
        if status.get("status") in (-1, 3, 5, 6):
            raise BootstrapError("bootstrap remote build failed/cancelled")
        wait(5)
    raise BootstrapError("bootstrap remote build polling budget expired; retain correlation and never blindly resubmit")


def verify_runtime_identities(plan: dict, manifest: dict, operator: Operator) -> None:
    group = plan["bootstrap_site_id"].split("/providers/Microsoft.Web/sites/", 1)[0]
    suffix = manifest["bootstrap_site"].removeprefix("app-bootstrap-")
    for service, object_id in manifest["runtime_object_ids"].items():
        target = f"{group}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-{service}-{suffix}"
        actual = operator.request("GET", ARM + target + "?api-version=2023-01-31").json()["properties"]
        if (actual.get("principalId", "").lower() != object_id.lower()
                or actual.get("clientId", "").lower() != manifest["runtime_client_ids"][service].lower()):
            raise BootstrapError("live runtime UAMI object/client pair differs from the approved SQL grant target")


def execute(plan: dict, manifest: dict, package: Path, operator: Operator, journal: Journal,
            *, prepare_only: bool = False) -> dict:
    site_id, admin_id = plan["bootstrap_site_id"], plan["sql_admin_id"]
    needs_sql = bool(set(manifest["operations"]) & {"migrate", "grants"})
    if journal.data.get("approval", plan) != plan:
        raise BootstrapError("recovery journal is bound to a different approval")
    if (journal.data.get("upload", {}).get("state") == "intent"
            and not journal.data["upload"].get("deployment_id")):
        raise BootstrapError("uncertain previous bootstrap upload; reconcile its receipt before retrying")
    if journal.data.get("admin-window", {}).get("state") not in (None, "restored"):
        raise BootstrapError("previous administrator window is unresolved; restore it first")
    values = {
        "ITOPS_BOOTSTRAP_MANIFEST_SHA256": plan["manifest_sha256"],
        "ITOPS_BOOTSTRAP_AUTHORIZED": "true",
        "ITOPS_BOOTSTRAP_ATTEMPT_ID": str(uuid4()),
    }
    if "secrets" in manifest["operations"] and not prepare_only:
        secret = os.environ.get("ITOPS_REVIEW_ENTRA_CLIENT_SECRET", "")
        if len(secret) < 16:
            raise BootstrapError("review OIDC secret environment variable is missing")
        values["ITOPS_REVIEW_ENTRA_CLIENT_SECRET"] = secret
    site = operator.request("GET", ARM + site_id + WEB_API).json()
    identities = site.get("identity", {}).get("userAssignedIdentities", {})
    if len(identities) != 1:
        raise BootstrapError("bootstrap must have exactly its own isolated UAMI")
    identity = next(iter(identities.values()))
    if (identity.get("principalId", "").lower() != plan["temporary_sql_admin_object_id"].lower()
            or identity.get("clientId", "").lower() != manifest["bootstrap_client_id"].lower()):
        raise BootstrapError("bootstrap UAMI differs from authorization")
    if "grants" in manifest["operations"]:
        verify_runtime_identities(plan, manifest, operator)
    settings = operator.request("POST", ARM + site_id + "/config/appsettings/list" + WEB_API).json()["properties"]
    for key, setting in {
        "bootstrap_client_id": "AZURE_CLIENT_ID", "sql_server": "ITOPS_SQL_SERVER",
        "sql_database": "ITOPS_SQL_DATABASE", "vault_url": "ITOPS_KEY_VAULT_URL",
        "search_endpoint": "ITOPS_SEARCH_ENDPOINT",
    }.items():
        if settings.get(setting) != manifest[key]:
            raise BootstrapError("deployed bootstrap target settings differ from approved manifest")
    if settings.get("WEBSITES_ENABLE_APP_SERVICE_STORAGE") != "true":
        raise BootstrapError("persistent bootstrap recovery storage must be enabled")
    prepared = journal.data.get("upload", {}).get("state") == "complete"
    if prepared and (site.get("properties", {}).get("state") != "Stopped"
                     or settings.get("ITOPS_BOOTSTRAP_AUTHORIZED") != "false"):
        raise BootstrapError("prepared bootstrap must be stopped and disarmed before resuming")
    config = operator.request("GET", ARM + site_id + "/config/web" + WEB_API).json()["properties"]
    if config.get("alwaysOn") is not True:
        raise BootstrapError("bootstrap Always On must be enabled and read back before execution")
    if (config.get("ipSecurityRestrictionsDefaultAction") != "Deny"
            or any(rule.get("action") == "Allow" for rule in config.get("ipSecurityRestrictions", []))
            or config.get("scmIpSecurityRestrictionsUseMain") is not False
            or not site.get("properties", {}).get("virtualNetworkSubnetId", "").endswith("/subnets/web-integration")):
        raise BootstrapError("bootstrap must have deny-all main ingress and explicit VNet integration")
    for channel in ("scm", "ftp"):
        policy = operator.request(
            "GET", ARM + site_id + "/basicPublishingCredentialsPolicies/" + channel + WEB_API,
        ).json()["properties"]
        if policy.get("allow") is not False:
            raise BootstrapError("basic publishing authentication must be disabled before bootstrap")
    existing = None
    if needs_sql:
        existing = operator.request("GET", ARM + admin_id + API).json()["properties"]
        if existing.get("sid", "").lower() != plan["restore_sql_admin_object_id"].lower():
            raise BootstrapError("SQL administrator is not the expected original operator; use explicit recovery")
        if existing.get("azureADOnlyAuthentication") is not True:
            raise BootstrapError("SQL must remain Entra-only")
    journal.save("approval", plan)
    original_error = None
    admin_intent = False
    scm = f"https://{manifest['bootstrap_site']}.scm.azurewebsites.net"
    try:
        if not prepared:
            operator.request("POST", ARM + site_id + "/stop" + WEB_API, accepted=(200, 202))
            operator.settings(site_id, {"ITOPS_BOOTSTRAP_AUTHORIZED": "false"},
                              remove=("ITOPS_REVIEW_ENTRA_CLIENT_SECRET", "ITOPS_BOOTSTRAP_MANIFEST_SHA256",
                                      "ITOPS_BOOTSTRAP_ATTEMPT_ID", "ITOPS_SOURCE_PROBE_WITNESS_JSON"))
            # Start disarmed only for a new build, not when resuming its verified receipt.
            operator.request("POST", ARM + site_id + "/start" + WEB_API, accepted=(200, 202))
        upload_bootstrap(package, plan["package_sha256"], scm, operator, journal)
        if not prepared:
            operator.request("POST", ARM + site_id + "/stop" + WEB_API, accepted=(200, 202))
        if prepare_only:
            return {"state": "prepared", "bootstrap_stopped": True, "data_operations_executed": False}
        if "probe-source" in manifest["operations"]:
            witness = source_witness(manifest, os.environ.get("ITOPS_SOURCE_PROBE_WITNESS_JSON", ""))
            values["ITOPS_SOURCE_PROBE_WITNESS_JSON"] = canonical(witness).decode()
        if needs_sql:
            # Persist original administrator BEFORE the first elevation request.
            journal.save("admin-window", {"state": "intent", "resource_id": admin_id,
                                         "original": existing, "approval": plan})
            admin_intent = True
            operator.set_admin(admin_id, {**existing, "sid": plan["temporary_sql_admin_object_id"],
                                         "login": manifest["bootstrap_site"].replace("app-", "id-")})
            journal.save("admin-window", {**journal.data["admin-window"], "state": "temporary"})
        operator.settings(site_id, values)
        operator.request("POST", ARM + site_id + "/start" + WEB_API, accepted=(200, 202))
        receipt_url = scm + f"/api/vfs/data/itops-bootstrap/{manifest['deployment_id']}.json"
        for _ in range(360):
            response = operator.request("GET", receipt_url, accepted=(200, 404))
            if response.status_code == 200:
                receipt = response.json()
                if (receipt.get("manifest", {}).get("sha256") != plan["manifest_sha256"]
                        or receipt.get("attempt", {}).get("id") != values["ITOPS_BOOTSTRAP_ATTEMPT_ID"]):
                    time.sleep(5)
                    continue
                if receipt.get("bootstrap", {}).get("state") == "complete":
                    journal.save("bootstrap-result", receipt)
                    break
                if receipt.get("bootstrap", {}).get("state") == "failed":
                    raise BootstrapError("bootstrap failed; inspect sanitized private journal")
            time.sleep(5)
        else:
            raise BootstrapError("bootstrap execution polling budget expired")
    except BaseException as exc:
        original_error = exc
    finally:
        # Stop first: never continue privileged work after the administrator is restored.
        try:
            operator.request("POST", ARM + site_id + "/stop" + WEB_API, accepted=(200, 202))
        finally:
            try:
                if admin_intent:
                    operator.set_admin(admin_id, existing)
                    journal.save("admin-window", {**journal.data["admin-window"], "state": "restored"})
            finally:
                operator.settings(site_id, {"ITOPS_BOOTSTRAP_AUTHORIZED": "false"},
                                  remove=("ITOPS_REVIEW_ENTRA_CLIENT_SECRET", "ITOPS_BOOTSTRAP_MANIFEST_SHA256",
                                          "ITOPS_BOOTSTRAP_ATTEMPT_ID", "ITOPS_SOURCE_PROBE_WITNESS_JSON"))
    if original_error:
        raise original_error
    return {"state": "complete", "sql_operator_restored": needs_sql, "bootstrap_stopped": True}


def load_source_witness_artifact(manifest: dict, path: Path) -> dict:
    document = json.loads(path.read_bytes())
    if isinstance(document, dict) and set(document) == {"contract", "witness"}:
        from agentic_itops.service_preflight import validate_probe_contract
        contract = validate_probe_contract(document["contract"])
        if contract["sha256"] != manifest["source_probe"]["contract_sha256"]:
            raise BootstrapError("operator witness artifact differs from the approved source contract")
        document = document["witness"]
    return source_witness(manifest, canonical(document).decode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--bootstrap-object-id", required=True)
    parser.add_argument("--restore-operator-object-id", required=True)
    parser.add_argument("--recovery-journal", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--prepare-only", action="store_true",
                        help="Build the approved ZIP and stop/disarm; do not run its data operations")
    parser.add_argument("--approved-package-sha256")
    parser.add_argument("--source-witness", type=Path,
                        help="Fresh parent-generated count-only witness; use --prepare-only before obtaining it")
    args = parser.parse_args()
    plan = approval_plan(args.manifest, args.package, args.subscription, args.resource_group,
                         args.bootstrap_object_id, args.restore_operator_object_id)
    if not args.execute:
        print(json.dumps(plan, sort_keys=True, indent=2))
        return
    if args.approved_package_sha256 != plan["package_sha256"]:
        raise BootstrapError("explicit approval of exact ZIP SHA256 is required")
    from azure.identity import AzureCliCredential
    import httpx
    manifest = json.loads(args.manifest.read_bytes())
    # Revalidate approval expiration, all target contracts and operations before any network calls.
    load_manifest(args.manifest, {
        "ITOPS_BOOTSTRAP_MANIFEST_SHA256": plan["manifest_sha256"], "ITOPS_BOOTSTRAP_AUTHORIZED": "true",
        **{env: manifest[key] for key, env in {
            "bootstrap_client_id": "AZURE_CLIENT_ID", "bootstrap_site": "WEBSITE_SITE_NAME",
            "sql_server": "ITOPS_SQL_SERVER", "sql_database": "ITOPS_SQL_DATABASE",
            "vault_url": "ITOPS_KEY_VAULT_URL", "search_endpoint": "ITOPS_SEARCH_ENDPOINT",
        }.items()},
    })
    with AzureCliCredential(process_timeout=60) as credential, httpx.Client(timeout=120, follow_redirects=False) as client:
        try:
            if args.source_witness:
                os.environ["ITOPS_SOURCE_PROBE_WITNESS_JSON"] = canonical(
                    load_source_witness_artifact(manifest, args.source_witness)).decode()
            result = execute(plan, manifest, args.package, Operator(credential, client), Journal(args.recovery_journal),
                             prepare_only=args.prepare_only)
        finally:
            os.environ.pop("ITOPS_SOURCE_PROBE_WITNESS_JSON", None)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
