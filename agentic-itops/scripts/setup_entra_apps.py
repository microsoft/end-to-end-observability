"""Provision approved applications, review assignments and scoped runtime bindings.

No secrets, Graph API permissions or tenant administrator assignments are created.
Unknown write outcomes require manual reconciliation; never discard the journal.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5


ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROLES = ("Viewer", "Analyst", "Reviewer", "KnowledgeReviewer", "Operator")
SERVICE_ROLES = ("Worker.Invoke", "Tools.Invoke", "Publisher.Invoke")
GRAPH = "https://graph.microsoft.com/v1.0"
INVOCATION_ROLES = {
    "review": ("Worker.Invoke", "Publisher.Invoke"),
    "worker": ("Tools.Invoke",),
    "bootstrap": SERVICE_ROLES,
}


def application_manifest(name: str, *, review: bool, projection: bool = False) -> dict:
    if name != f"agentic-itops-us93-{'review' if review else 'services'}":
        raise ValueError("only the two explicitly approved application names are supported")
    if review and projection:
        raise ValueError("projection is an application-only service role")
    roles = REVIEW_ROLES if review else SERVICE_ROLES + (("Results.Project",) if projection else ())
    return {
        "displayName": name, "signInAudience": "AzureADMyOrg",
        "api": {"requestedAccessTokenVersion": 2},
        "requiredResourceAccess": [],
        "appRoles": [{
            "id": str(uuid5(NAMESPACE_URL, f"agentic-itops/us93/{name}/{role}")),
            "allowedMemberTypes": ["User" if review else "Application"],
            "description": f"Agentic ITOps {role} role for the isolated synthetic pilot",
            "displayName": role, "isEnabled": True, "value": role,
        } for role in roles],
        **({"web": {"redirectUris": [], "implicitGrantSettings": {
            "enableIdTokenIssuance": True, "enableAccessTokenIssuance": False,
        }}} if review else {}),
    }


def save(path: Path, state: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(state, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class GraphFailure(RuntimeError):
    pass


def runtime_plan(config: dict, *, projection: bool = False) -> dict:
    if not isinstance(config, dict) or set(config) != {"subscription_id", "resource_group", "review_origin", "identities"}:
        raise ValueError("runtime configuration must declare the exact approved deployment scope")
    subscription = str(UUID(config["subscription_id"]))
    if config["resource_group"] != "agentic-itops-us93-rg":
        raise ValueError("runtime identities must belong to the approved new resource group")
    origin = config["review_origin"]
    if not isinstance(origin, str):
        raise ValueError("review origin must be a string")
    url = urlsplit(origin)
    if (url.scheme != "https" or url.username or url.password or url.port
            or url.path or url.query or url.fragment
            or not re.fullmatch(r"[a-z0-9-]+\.azurewebsites\.net", url.hostname or "")
            or origin != "https://" + url.hostname):
        raise ValueError("review origin must be the exact HTTPS App Service origin")
    if not isinstance(config["identities"], dict) or set(config["identities"]) != set(INVOCATION_ROLES):
        raise ValueError("only the review, worker and isolated bootstrap identities receive invocation roles")
    matrix = {**INVOCATION_ROLES, **({"worker": ("Tools.Invoke", "Results.Project")} if projection else {})}
    identities = {}
    for name, value in config["identities"].items():
        if not isinstance(value, dict) or set(value) != {"principal_id", "client_id", "resource_id"}:
            raise ValueError("each runtime identity needs its principal, client and resource IDs")
        prefix = (f"/subscriptions/{subscription}/resourceGroups/agentic-itops-us93-rg/"
                  "providers/Microsoft.ManagedIdentity/userAssignedIdentities/")
        resource = value["resource_id"]
        if (not isinstance(resource, str) or not resource.lower().startswith(prefix.lower())
                or not re.fullmatch(r"[A-Za-z0-9_-]+", resource[len(prefix):])):
            raise ValueError("runtime identity is outside the approved resource group or resource type")
        identities[name] = {
            "principal_id": str(UUID(value["principal_id"])), "client_id": str(UUID(value["client_id"])),
            "resource_id": resource, "roles": list(matrix[name]),
        }
    if any(len({value[key].lower() for value in identities.values()}) != len(identities)
           for key in ("principal_id", "client_id", "resource_id")):
        raise ValueError("review, worker and bootstrap identities must remain distinct")
    return {
        "subscription_id": subscription, "resource_group": config["resource_group"],
        "review_redirect_uri": origin + "/.auth/login/aad/callback", "identities": identities,
    }


def upgrade_projection_contract(state: dict, read, write, completed) -> None:
    name = "agentic-itops-us93-services"
    entry = state.get("applications", {}).get(name)
    if not entry or not entry.get("service_principal_id"):
        raise GraphFailure("projection upgrade requires the existing verified service application journal")
    legacy = application_manifest(name, review=False)
    desired = application_manifest(name, review=False, projection=True)
    endpoint = f"/applications/{entry['object_id']}"
    claim = {"name": "idtyp", "source": None, "essential": False, "additionalProperties": []}
    optional = {"accessToken": [claim], "idToken": [], "saml2Token": []}

    def inspect(application):
        if (application.get("appId") != entry["client_id"] or application.get("displayName") != name
                or application.get("signInAudience") != "AzureADMyOrg"
                or application.get("requiredResourceAccess") != []
                or application.get("api", {}).get("requestedAccessTokenVersion") != 2):
            raise GraphFailure("service application scope differs from the approved projection upgrade")
        roles = application.get("appRoles", [])
        role_keys = set(legacy["appRoles"][0])
        role_map = {item["value"]: {key: item.get(key) for key in role_keys} for item in roles}
        legacy_map = {item["value"]: item for item in legacy["appRoles"]}
        desired_map = {item["value"]: item for item in desired["appRoles"]}
        claims = application.get("optionalClaims") or {}
        old_claims = all(not value for value in claims.values())
        new_claims = (
            set(claims) <= set(optional) and claims.get("accessToken") == [claim]
            and not claims.get("idToken") and not claims.get("saml2Token")
        )
        if len(role_map) != len(roles):
            raise GraphFailure("duplicate application roles require reconciliation")
        if role_map == desired_map and new_claims:
            return "upgraded"
        if role_map == legacy_map and old_claims:
            return "legacy"
        raise GraphFailure("unexpected roles or optional claims; refusing an unapproved contract replacement")

    step = "projection:contract"
    if inspect(read(endpoint)) != "upgraded":
        if state.get("pending_step") == step or state.get("projection_contract"):
            raise GraphFailure("projection patch is not observable; no automatic replay")
        write(step, endpoint, {"appRoles": desired["appRoles"], "optionalClaims": optional}, method="PATCH")
        if inspect(read(endpoint)) != "upgraded":
            raise GraphFailure("projection contract write was not verified")
    entry["roles"] = {role["value"]: role["id"] for role in desired["appRoles"]}
    state["projection_contract"] = True
    completed(step)


def configure_runtime(state: dict, plan: dict, read, write, completed) -> None:
    if state.get("runtime_plan") not in (None, plan):
        raise GraphFailure("runtime plan differs from the journaled approval")
    state["runtime_plan"] = plan
    review = state["applications"]["agentic-itops-us93-review"]
    services = state["applications"]["agentic-itops-us93-services"]
    for name, expected in plan["identities"].items():
        principal = read(f"/servicePrincipals/{expected['principal_id']}")
        if (principal.get("servicePrincipalType") != "ManagedIdentity"
                or principal.get("appId") != expected["client_id"]
                or expected["resource_id"].lower() not in {
                    item.lower() for item in principal.get("alternativeNames", []) if isinstance(item, str)
                }):
            raise GraphFailure(f"{name}: identity read-back does not match the approved managed identity")
    current = read(f"/applications/{review['object_id']}")
    web = current.get("web", {})
    redirect = plan["review_redirect_uri"]
    if web.get("redirectUris") != [redirect]:
        if web.get("redirectUris"):
            raise GraphFailure("refusing to replace unexpected review redirects")
        if state["pending_step"] == "runtime:review-redirect":
            raise GraphFailure("review redirect patch is not observable; no automatic replay")
        write("runtime:review-redirect", f"/applications/{review['object_id']}",
              {"web": {**web, "redirectUris": [redirect]}}, method="PATCH")
        verified = read(f"/applications/{review['object_id']}")
        if verified.get("web", {}).get("redirectUris") != [redirect]:
            raise GraphFailure("review redirect write was not verified")
    completed("runtime:review-redirect")
    endpoint = f"/servicePrincipals/{services['service_principal_id']}/appRoleAssignedTo"
    assignments = read(endpoint)
    if assignments.get("@odata.nextLink"):
        raise GraphFailure("unexpected service-role pagination; inspect before continuing")
    actual = assignments["value"]
    state.setdefault("runtime_assignments", {})
    for name, identity in plan["identities"].items():
        allowed = {services["roles"][role] for role in identity["roles"]}
        if any(item.get("appRoleId") not in allowed for item in actual
               if item.get("principalId") == identity["principal_id"]):
            raise GraphFailure(f"{name}: unexpected invocation grant exists; inspect before continuing")
        for role in identity["roles"]:
            step = f"runtime:role:{name}:{role}"
            body = {"principalId": identity["principal_id"], "resourceId": services["service_principal_id"],
                    "appRoleId": services["roles"][role]}
            matches = [item for item in actual if all(item.get(key) == value for key, value in body.items())]
            if len(matches) > 1:
                raise GraphFailure("duplicate runtime role assignments require reconciliation")
            if matches:
                assignment = matches[0]
            else:
                if state["pending_step"] == step or step in state["completed_steps"]:
                    raise GraphFailure("journaled runtime assignment is not observable; no automatic replay")
                assignment = write(step, endpoint, body)
                verified = read(endpoint)
                if verified.get("@odata.nextLink"):
                    raise GraphFailure("runtime assignment read-back unexpectedly paginated")
                actual = verified["value"]
                if not any(item.get("id") == assignment.get("id")
                           and all(item.get(key) == value for key, value in body.items()) for item in actual):
                    raise GraphFailure("runtime role assignment was not verified")
            recorded = state["runtime_assignments"].get(step)
            if recorded is not None and recorded != assignment["id"]:
                raise GraphFailure("runtime role assignment ID changed outside the journal")
            state["runtime_assignments"][step] = assignment["id"]
            completed(step)


def execute(tenant: str, reviewer: str, path: Path, *, resume: bool = False,
            runtime_config: dict | None = None, approve_projection: bool = False) -> dict:
    import httpx
    from azure.identity import AzureCliCredential

    initial = {
        "schema_version": 1, "tenant_id": tenant, "reviewer_object_id": reviewer,
        "status": "starting", "applications": {}, "completed_steps": [],
        "pending_step": None, "secrets_created": False, "directory_grants": False,
    }
    if resume:
        state = json.loads(path.read_text(encoding="utf-8"))
        if (state.get("schema_version") != 1 or state.get("tenant_id") != tenant
                or state.get("reviewer_object_id") != reviewer
                or set(state.get("applications", {})) - {
                    "agentic-itops-us93-review", "agentic-itops-us93-services",
                }):
            raise GraphFailure("journal scope does not match the approved setup")
    else:
        state = initial
        with path.open("x", encoding="utf-8") as handle:
            json.dump(state, handle)
        save(path, state)

    def completed(step):
        if step not in state["completed_steps"]:
            state["completed_steps"].append(step)
        if state["pending_step"] == step:
            state["pending_step"] = None
        save(path, state)

    def read(client, credential, endpoint):
        token = credential.get_token("https://graph.microsoft.com/.default").token
        response = client.get(GRAPH + endpoint, headers={"Authorization": "Bearer " + token})
        if response.status_code != 200:
            raise GraphFailure(f"read-back failed: HTTP {response.status_code}; no write retry")
        return response.json()

    def write(client, credential, step, endpoint, body, *, method="POST"):
        token = credential.get_token("https://graph.microsoft.com/.default").token
        state["pending_step"] = step
        save(path, state)
        try:
            response = client.request(method, GRAPH + endpoint, headers={"Authorization": "Bearer " + token}, json=body)
        except httpx.RequestError as exc:
            state["status"] = "uncertain_write"
            state["error"] = {"type": type(exc).__name__}
            save(path, state)
            raise GraphFailure(f"{step}: uncertain outcome; reconcile the existing journal before retry") from exc
        if response.status_code not in (200, 201, 204):
            try:
                error = response.json().get("error", {})
            except ValueError:
                error = {}
            state["status"] = "blocked"
            state["error"] = {
                "step": step, "http_status": response.status_code,
                "code": error.get("code", "unparseable_error"),
                "request_id": response.headers.get("request-id"),
            }
            save(path, state)
            raise GraphFailure(f"{step}: HTTP {response.status_code} {state['error']['code']}; administrator action may be required")
        if response.status_code == 204:
            return {}
        try:
            result = response.json()
        except ValueError as exc:
            state["status"] = "uncertain_write"
            save(path, state)
            raise GraphFailure(f"{step}: success response was not readable; reconcile before retry") from exc
        return result

    with AzureCliCredential(tenant_id=tenant, process_timeout=60) as credential, httpx.Client(timeout=60) as client:
        if approve_projection or state.get("projection_contract") or state.get("pending_step") == "projection:contract":
            if not resume:
                raise GraphFailure("projection contract changes must resume the existing journal")
            if not approve_projection and not state.get("projection_contract"):
                raise GraphFailure("pending projection contract requires its explicit approval flag")
            upgrade_projection_contract(
                state, lambda endpoint: read(client, credential, endpoint),
                lambda step, endpoint, body, **kwargs: write(client, credential, step, endpoint, body, **kwargs),
                completed,
            )
        plan = runtime_plan(runtime_config, projection=bool(state.get("projection_contract"))) if runtime_config is not None else None
        for review in (True, False):
            name = f"agentic-itops-us93-{'review' if review else 'services'}"
            manifest = application_manifest(name, review=review,
                                            projection=not review and bool(state.get("projection_contract")))
            entry = state["applications"].get(name)
            if entry is None:
                if state["pending_step"] == f"create:{name}":
                    raise GraphFailure("unresolved application creation; reconcile its object ID before resuming")
                application = write(client, credential, f"create:{name}", "/applications", manifest)
                entry = state["applications"][name] = {
                    "object_id": str(UUID(application["id"])), "client_id": str(UUID(application["appId"])),
                    "roles": {role["value"]: role["id"] for role in manifest["appRoles"]},
                }
                completed(f"create:{name}")
            else:
                application = read(client, credential, f"/applications/{entry['object_id']}")
                actual_roles = {item["value"]: item for item in application.get("appRoles", [])}
                if (application.get("appId") != entry["client_id"] or application.get("displayName") != name
                        or application.get("signInAudience") != "AzureADMyOrg"
                        or application.get("api", {}).get("requestedAccessTokenVersion") != 2
                        or application.get("requiredResourceAccess") != []
                        or set(actual_roles) != set(entry["roles"])):
                    raise GraphFailure("application read-back differs from the approved configuration")
                for role in manifest["appRoles"]:
                    if any(actual_roles[role["value"]].get(key) != value for key, value in role.items()):
                        raise GraphFailure("application role differs from the approved definition")
            if not review:
                audience = f"api://{entry['client_id']}"
                current = read(client, credential, f"/applications/{entry['object_id']}")
                if current.get("identifierUris") != [audience]:
                    if current.get("identifierUris"):
                        raise GraphFailure("refusing to replace an unexpected service audience")
                    if state["pending_step"] == f"audience:{name}":
                        raise GraphFailure("audience patch is not yet observable; no automatic retry")
                    write(client, credential, f"audience:{name}", f"/applications/{entry['object_id']}",
                          {"identifierUris": [audience]}, method="PATCH")
                entry["resource_uri"] = audience
                completed(f"audience:{name}")
            if "service_principal_id" in entry:
                principal = read(client, credential, f"/servicePrincipals/{entry['service_principal_id']}")
                if principal.get("appId") != entry["client_id"] or principal.get("appRoleAssignmentRequired") is not True:
                    raise GraphFailure("service-principal read-back differs from approved configuration")
            else:
                if state["pending_step"] == f"principal:{name}":
                    raise GraphFailure("unresolved service-principal creation; reconcile its ID first")
                principal = write(client, credential, f"principal:{name}", "/servicePrincipals", {
                    "appId": entry["client_id"], "appRoleAssignmentRequired": True,
                })
                entry["service_principal_id"] = str(UUID(principal["id"]))
                completed(f"principal:{name}")
            if review:
                recorded = {item["role"]: item["assignment_id"] for item in entry.setdefault("reviewer_assignments", [])}
                assignments = read(client, credential, f"/servicePrincipals/{entry['service_principal_id']}/appRoleAssignedTo")
                if assignments.get("@odata.nextLink"):
                    raise GraphFailure("unexpected assignment pagination; inspect the owned application")
                actual = {item["id"]: item for item in assignments["value"]}
                for role, role_id in entry["roles"].items():
                    if role in recorded:
                        item = actual.get(recorded[role], {})
                        if (item.get("principalId") != reviewer or item.get("appRoleId") != role_id
                                or item.get("resourceId") != entry["service_principal_id"]):
                            raise GraphFailure("reviewer assignment no longer matches the journal")
                        continue
                    if state["pending_step"] == f"reviewer-role:{role}":
                        raise GraphFailure("unresolved role assignment; reconcile its ID first")
                    assignment = write(
                        client, credential, f"reviewer-role:{role}",
                        f"/servicePrincipals/{entry['service_principal_id']}/appRoleAssignedTo",
                        {"principalId": reviewer, "resourceId": entry["service_principal_id"], "appRoleId": role_id},
                    )
                    entry["reviewer_assignments"].append({"role": role, "assignment_id": assignment["id"]})
                    completed(f"reviewer-role:{role}")
        if plan is not None:
            configure_runtime(
                state, plan, lambda endpoint: read(client, credential, endpoint),
                lambda step, endpoint, body, **kwargs: write(client, credential, step, endpoint, body, **kwargs),
                completed,
            )
        state["status"] = ("runtime_roles_and_redirect_verified_secret_pending" if plan is not None
                           else "created_requires_readback_and_configuration")
        save(path, state)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--reviewer-object-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply-approved-apps", action="store_true")
    parser.add_argument("--resume", action="store_true", help="verify and resume a known partial journal without recreating objects")
    parser.add_argument("--runtime-config", type=Path, help="ignored exact approved host/UAMI configuration")
    parser.add_argument("--apply-approved-runtime", action="store_true")
    parser.add_argument("--approve-projection-contract", action="store_true",
                        help="approve the worker-only Results.Project role definition and idtyp token claim")
    args = parser.parse_args()
    try:
        tenant, reviewer = str(UUID(args.tenant_id)), str(UUID(args.reviewer_object_id))
        if bool(args.runtime_config) != args.apply_approved_runtime:
            raise ValueError("runtime configuration and its explicit approval flag must be supplied together")
        if args.apply_approved_runtime and not (args.resume and args.apply_approved_apps):
            raise ValueError("runtime configuration must resume the existing approved application journal")
        if args.approve_projection_contract and not (args.resume and args.apply_approved_apps):
            raise ValueError("projection contract upgrade must resume the existing approved application journal")
        config = None
        if args.runtime_config:
            config_path = args.runtime_config.resolve()
            if not config_path.is_relative_to(ROOT / ".build"):
                raise ValueError("runtime identity configuration belongs in ignored .build state")
            config = json.loads(config_path.read_text(encoding="utf-8"))
        path = args.output.resolve()
        if (not path.is_relative_to(ROOT / ".build") or (path.exists() and not args.resume)
                or (args.resume and (not path.is_file() or not args.apply_approved_apps))):
            raise ValueError("use a new ignored .build file, or explicitly --resume an existing approved journal")
        path.parent.mkdir(parents=True, exist_ok=True)
        if args.apply_approved_apps:
            state = execute(tenant, reviewer, path, resume=args.resume, runtime_config=config,
                            approve_projection=args.approve_projection_contract)
            print(f"Entra setup: {state['status']}; journal: {path}")
        else:
            plan = {
                "tenant_id": tenant, "reviewer_object_id": reviewer, "azure_writes": False,
                "applications": [application_manifest("agentic-itops-us93-review", review=True),
                                 application_manifest("agentic-itops-us93-services", review=False)],
                "deferred": ["redirect URIs", "30-day secret in private vault", "managed-identity app-role assignments"],
            }
            with path.open("x", encoding="utf-8") as handle:
                json.dump(plan, handle, sort_keys=True, indent=2)
            print(f"Prepared Entra setup: {path}")
        return 0
    except (ValueError, OSError, GraphFailure) as exc:
        print(f"Entra setup stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
