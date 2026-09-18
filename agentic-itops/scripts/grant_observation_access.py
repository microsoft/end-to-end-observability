"""Apply the explicitly approved exact-table role to a deployed worker identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from scripts.setup_entra_apps import runtime_plan, save


ROOT = Path(__file__).resolve().parents[1]
ROLE = "3b03c2da-16b3-4a49-8834-0f8130efdd3b"
DATA_ACTION = "Microsoft.OperationalInsights/workspaces/tables/data/read"
CONDITION = (
    "(!(ActionMatches{'Microsoft.OperationalInsights/workspaces/tables/data/read'}) OR "
    "(@Resource[Microsoft.OperationalInsights/workspaces/tables:name] StringEquals 'EnergyObservations_CL'))"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--workspace-resource-id", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--apply-approved", action="store_true")
    args = parser.parse_args()
    if any(not path.resolve().is_relative_to(ROOT / ".build") for path in (args.runtime_config, args.journal)):
        raise ValueError("identity configuration and grant journal belong in ignored .build state")
    config = json.loads(args.runtime_config.read_text(encoding="utf-8"))
    runtime_plan(config, projection=True)
    subscription = str(UUID(config["subscription_id"]))
    worker = config["identities"]["worker"]
    principal = str(UUID(worker["principal_id"]))
    scope = args.workspace_resource_id
    parts = scope.strip("/").split("/")
    if (len(parts) != 8 or parts[0].lower() != "subscriptions" or parts[1].lower() != subscription
            or parts[2].lower() != "resourcegroups" or parts[4].lower() != "providers"
            or parts[5].lower() != "microsoft.operationalinsights" or parts[6].lower() != "workspaces"):
        raise ValueError("an exact workspace in the approved subscription is required")
    role_id = f"/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{ROLE}"
    assignment = str(uuid5(NAMESPACE_URL, scope.lower() + "/" + principal + "/" + ROLE))
    resource = scope + "/providers/Microsoft.Authorization/roleAssignments/" + assignment
    properties = {
        "roleDefinitionId": role_id, "principalId": principal, "principalType": "ServicePrincipal",
        "condition": CONDITION, "conditionVersion": "2.0",
    }
    plan = {"resource_id": resource, "properties": properties, "worker_resource_id": worker["resource_id"]}
    if not args.apply_approved:
        print(json.dumps(plan, indent=2))
        return
    import httpx
    from azure.identity import AzureCliCredential

    args.journal.parent.mkdir(parents=True, exist_ok=True)
    record = json.loads(args.journal.read_text(encoding="utf-8")) if args.journal.exists() else {"plan": plan}
    if record["plan"] != plan:
        raise ValueError("grant journal is bound to another approval")
    with AzureCliCredential(process_timeout=60) as credential, httpx.Client(timeout=60) as client:
        headers = {"Authorization": "Bearer " + credential.get_token("https://management.azure.com/.default").token}
        def get(resource_id, api):
            return client.get("https://management.azure.com" + resource_id,
                              params={"api-version": api}, headers=headers)
        identity = get(worker["resource_id"], "2024-11-30")
        identity.raise_for_status()
        actual = identity.json()["properties"]
        if actual["principalId"] != principal or actual["clientId"] != worker["client_id"]:
            raise ValueError("worker identity no longer matches the approved deployment")
        definition = get(role_id, "2022-04-01")
        definition.raise_for_status()
        role = definition.json()["properties"]
        if (role["roleName"] != "Log Analytics Data Reader"
                or {action for permission in role["permissions"] for action in permission["dataActions"]} != {DATA_ACTION}
                or {action for permission in role["permissions"] for action in permission["actions"]} != {
                    "Microsoft.OperationalInsights/workspaces/read", "Microsoft.OperationalInsights/workspaces/query/read",
                }):
            raise ValueError("built-in data-reader role differs from the approved data action")
        response = get(resource, "2022-04-01")
        if response.status_code == 404:
            if record.get("state"):
                raise RuntimeError("previous grant is not observable; reconcile before another PUT")
            record["state"] = "intent"
            args.journal.parent.mkdir(parents=True, exist_ok=True)
            save(args.journal, record)
            response = client.put("https://management.azure.com" + resource,
                                  params={"api-version": "2022-04-01"}, headers=headers,
                                  json={"properties": properties})
            response.raise_for_status()
            response = get(resource, "2022-04-01")
        response.raise_for_status()
        actual = response.json()["properties"]
        if any(actual.get(key) != value for key, value in properties.items()):
            raise RuntimeError("source grant read-back differs from the approved condition/principal/role")
        record["state"] = "verified"
        save(args.journal, record)
    print("Exact-table role assignment verified; actual worker access/denial still requires the private probe.")


if __name__ == "__main__":
    main()
