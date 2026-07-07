#!/usr/bin/env python3
"""
generate_workbooks.py — consume an SRE Agent discovery result and create
Azure Monitor workbooks from it.

Discovery is performed upstream by the SRE Agent's built-in discovery functions.
This tool *consumes* that discovered inventory, buckets the resources into the
four workbook domains, binds them to the templates in ../templates, and emits one
deployable workbook per non-empty domain plus a manifest. Output is deterministic
so repeated runs over the same inventory yield byte-identical files.

Invoked by the SRE Workbook Orchestrator agent during the Generate stage.

Accepted input (any one of):
  1. Flat SRE Agent discovery output:
       { "subscriptions": [...], "generatedUtc": "...",
         "resources": [ { "id","name","type","location","resourceGroup",
                          "subscriptionId" }, ... ] }
  2. Already domain-bucketed inventory:
       { "subscriptions": [...], "domains": { "<domain>": { "resources": [...] } } }

Usage:
  python generate_workbooks.py \
      --inventory ./inventory.json \
      --workspace-resource-id /subscriptions/.../workspaces/law-obs \
      --templates-dir ../templates \
      --output-dir ./out

Exit codes:
  0  success (at least one workbook generated, or all domains legitimately empty)
  1  invalid arguments or missing inputs
  2  template or inventory parsing error
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

DOMAINS = ["infrastructure", "application", "network", "storage"]

# Maps Azure resource types to the workbook domain that visualizes them. Used to
# bucket a flat SRE Agent discovery result into the four domains. Lowercased for
# case-insensitive matching against the discovered `type` field.
DOMAIN_TYPE_MAP = {
    "infrastructure": [
        "microsoft.compute/virtualmachines",
        "microsoft.compute/virtualmachinescalesets",
        "microsoft.compute/availabilitysets",
        "microsoft.hybridcompute/machines",
    ],
    "application": [
        "microsoft.web/sites",
        "microsoft.web/serverfarms",
        "microsoft.app/containerapps",
        "microsoft.containerservice/managedclusters",
    ],
    "network": [
        "microsoft.network/virtualnetworks",
        "microsoft.network/networksecuritygroups",
        "microsoft.network/loadbalancers",
        "microsoft.network/applicationgateways",
        "microsoft.network/frontdoors",
        "microsoft.network/publicipaddresses",
    ],
    "storage": [
        "microsoft.storage/storageaccounts",
        "microsoft.compute/disks",
        "microsoft.sql/servers/databases",
        "microsoft.documentdb/databaseaccounts",
    ],
}

# Reverse lookup: resource type -> domain.
TYPE_TO_DOMAIN = {
    rtype: domain
    for domain, rtypes in DOMAIN_TYPE_MAP.items()
    for rtype in rtypes
}


def normalize_inventory(inventory: dict) -> dict:
    """Accept either a flat discovery result or a pre-bucketed inventory and
    return an inventory with a populated `domains` map.

    When the input already carries a `domains` object (option 2) it is returned
    unchanged. Otherwise the flat `resources` list (option 1, the typical SRE
    Agent discovery shape) is bucketed by resource type into the four domains.
    Resource types not mapped to a domain are ignored.
    """
    if inventory.get("domains"):
        return inventory

    buckets = {domain: [] for domain in DOMAINS}
    for res in inventory.get("resources", []) or []:
        rtype = str(res.get("type", "")).lower()
        domain = TYPE_TO_DOMAIN.get(rtype)
        if domain:
            buckets[domain].append(res)

    inventory["domains"] = {
        domain: {"resourceCount": len(items), "resources": items}
        for domain, items in buckets.items()
    }
    return inventory


def log(msg: str) -> None:
    print(f"[generate_workbooks] {msg}", file=sys.stderr)


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def domain_has_resources(inventory: dict, domain: str) -> bool:
    """A domain is renderable when the discovery result has at least one resource in it."""
    domains = inventory.get("domains", {})
    entry = domains.get(domain)
    if not entry:
        return False
    resources = entry.get("resources", [])
    count = entry.get("resourceCount", entry.get("count", len(resources)))
    return count > 0


def build_cross_component_resources(inventory: dict) -> str:
    """Quote-separated subscription scopes for Resource Graph cross-component
    queries. The template wraps the placeholder as ["{{crossComponentResources}}"],
    so each scope must be emitted as its own JSON array element."""
    subs = inventory.get("subscriptions") or []
    if not subs:
        return "{Subscriptions}"
    scopes = [f"/subscriptions/{s}" if not str(s).startswith("/subscriptions/") else str(s) for s in subs]
    return '","'.join(scopes)


def render_template(raw: str, replacements: dict) -> str:
    for key, value in replacements.items():
        raw = raw.replace("{{" + key + "}}", value)
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate discovery-linked workbooks.")
    parser.add_argument("--inventory", required=True, help="Path to inventory JSON.")
    parser.add_argument("--workspace-resource-id", required=True,
                        help="Target Log Analytics workspace resource ID.")
    parser.add_argument("--templates-dir", default=os.path.join(os.path.dirname(__file__), "..", "templates"))
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "out"))
    parser.add_argument("--domains", nargs="*", default=DOMAINS,
                        help="Subset of domains to generate. Defaults to all.")
    args = parser.parse_args()

    if not os.path.isfile(args.inventory):
        log(f"inventory not found: {args.inventory}")
        return 1

    try:
        inventory = load_json(args.inventory)
    except json.JSONDecodeError as exc:
        log(f"failed to parse inventory: {exc}")
        return 2

    # Consume the SRE Agent discovery result: bucket a flat resource list into
    # domains (no-op if the inventory is already domain-bucketed).
    inventory = normalize_inventory(inventory)

    os.makedirs(args.output_dir, exist_ok=True)

    cross_component = build_cross_component_resources(inventory)
    subscriptions = inventory.get("subscriptions") or []
    replacements_base = {
        "workspaceResourceId": args.workspace_resource_id,
        "crossComponentResources": cross_component,
        "sourceSubscriptions": ", ".join(str(s) for s in subscriptions) or "(none)",
        "inventoryTimestamp": inventory.get("generatedUtc", inventory.get("timestamp", "unknown")),
    }

    generated = []
    skipped = []

    for domain in args.domains:
        if domain not in DOMAINS:
            log(f"unknown domain '{domain}', skipping")
            continue

        if not domain_has_resources(inventory, domain):
            log(f"domain '{domain}' has no resources, skipping")
            skipped.append(domain)
            continue

        template_path = os.path.join(args.templates_dir, f"{domain}.workbook.json")
        if not os.path.isfile(template_path):
            log(f"template missing for domain '{domain}': {template_path}")
            return 2

        with open(template_path, "r", encoding="utf-8") as f:
            raw = f.read()

        rendered = render_template(raw, replacements_base)

        try:
            workbook = json.loads(rendered)
        except json.JSONDecodeError as exc:
            log(f"rendered template for '{domain}' is invalid JSON: {exc}")
            return 2

        out_path = os.path.join(args.output_dir, f"{domain}.workbook.json")
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(workbook, f, indent=2, sort_keys=False, ensure_ascii=False)
            f.write("\n")

        domain_entry = inventory.get("domains", {}).get(domain, {})
        generated.append({
            "domain": domain,
            "file": os.path.basename(out_path),
            "displayName": f"Auto-Generated {domain.capitalize()} Workbook",
            "resourceCount": domain_entry.get("resourceCount", domain_entry.get("count", len(domain_entry.get("resources", [])))),
        })
        log(f"generated {out_path}")

    manifest = {
        "schemaVersion": "1.0",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workspaceResourceId": args.workspace_resource_id,
        "sourceInventoryTimestamp": inventory.get("generatedUtc", inventory.get("timestamp", "unknown")),
        "subscriptions": subscriptions,
        "workbooks": generated,
        "skippedDomains": skipped,
    }
    manifest_path = os.path.join(args.output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")
    log(f"wrote manifest {manifest_path} ({len(generated)} workbook(s), {len(skipped)} skipped)")

    if not generated:
        log("no workbooks generated — inventory contained no resources in requested domains")

    return 0


if __name__ == "__main__":
    sys.exit(main())
