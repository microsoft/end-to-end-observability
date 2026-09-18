"""V2 stock state over explicitly selected, read-only domain inventories."""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module
import json
import os
from pathlib import Path
import re

from .contracts import Frame, INVENTORY_SURFACES, Scenario
from .inventory_details import load_details, position_details
from .paths import source_root


REPOSITORY_ROOT = source_root()
DATASETS = {
    "power-grid": ("sample_data/inventory-austin", "iot_grid_simulator", "PowerGridInventoryReview"),
    "upstream": ("generated/review/normal_production", "iot_upstream_simulator", "PartsReviewStore"),
    "midstream": ("generated/review", "iot_midstream_simulator", "MidstreamInventoryReview"),
    "downstream": ("generated/review", "iot_downstream_refinery_simulator", "DownstreamInventoryReview"),
}
_CHOICES = (
    ("low_stock", "Low stock", "Reduce one fictional stock position below its opening quantity, to at most 9 in its own UOM. Below 10 is illustrative, not an industrial rule."),
    ("stockout", "Stockout", "Set the selected fictional stock position to an observed physical zero."),
    ("observation_unavailable", "Observation unavailable", "Make the selected quantity unknown (null); do not infer physical zero."),
)


def _domain(surface: str) -> str:
    if surface not in INVENTORY_SURFACES:
        raise ValueError("Unknown inventory surface")
    return surface.removesuffix("-inventory")


def scenarios(surface: str) -> list[Scenario]:
    _domain(surface)
    return [{"id": key, "label": label, "description": description}
            for key, label, description in _CHOICES]


def dataset_root(surface: str) -> Path:
    """Require the actual dataset directory, never discover a 'latest' child."""
    domain = _domain(surface)
    variable = f"ENERGY_V2_{domain.upper().replace('-', '_')}_INVENTORY_ROOT"
    return Path(os.environ.get(variable, str(
        REPOSITORY_ROOT / "domains" / domain / Path(DATASETS[domain][0])
    ))).resolve()


def _opening(surface: str) -> dict:
    domain = _domain(surface)
    root = dataset_root(surface)
    for family in ("part_master", "storeroom_location", "part_applicability", "part_stock_txn"):
        if not (root / f"{family}.jsonl").is_file():
            raise ValueError(f"{domain} requires explicit inventory dataset root with {family}.jsonl")
    reader = getattr(import_module(f"{DATASETS[domain][1]}.dashboard.review"), DATASETS[domain][2])
    source = (reader(root, {"UPSTREAM_PARTS_REVIEW": "1"}).inventory()
              if domain == "upstream" else reader(root).inventory())
    if source.get("source_dataset") != root.name:
        raise ValueError("Inventory reader did not use the explicit source_dataset")
    if not source.get("stock_is_known") or not source.get("stock"):
        raise ValueError("V2 requires known opening stock observations")
    parts = {row["part_master_id"]: row for row in source["parts"]}
    locations = {row["location_id"]: row for row in source["locations"]}
    if len(parts) != len(source["parts"]) or len(locations) != len(source["locations"]):
        raise ValueError("Duplicate inventory catalog identity")
    identities = set()
    for row in source["stock"]:
        key = (row["part_master_id"], row["location_id"])
        quantity = row["quantity"]
        if (key in identities or key[0] not in parts or key[1] not in locations
                or type(quantity) is not int or quantity < 0
                or not isinstance(parts[key[0]].get("uom"), str)
                or not parts[key[0]]["uom"].strip()):
            raise ValueError("Invalid opening quantity, identity, or UOM")
        identities.add(key)
        row["quality"] = "good"
        row["uom"] = parts[key[0]]["uom"]
    json.dumps(source, allow_nan=False)
    source["totals"].pop("units", None)
    source.update(domain=domain, dataset_selection="explicit_root", is_synthetic=True,
                  operational_use="prohibited", stock_basis="v2_simulated_position",
                  stock_note="V2 simulated stock from validated opening balances. "
                  "Unknown observations are null, not zero. Per-SKU/UOM quantities only. "
                  "The illustrative below-10 threshold is not an industrial rule.")
    for location in source["locations"]:
        location["quantity"] = None
    source["details"] = load_details(root, domain, source)
    return source


def initial_model(surface: str, seed: int) -> dict:
    _domain(surface)
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be an integer between 0 and 2147483647")
    opening = _opening(surface)
    eligible = sorted((row["part_master_id"], row["location_id"])
                      for row in opening["stock"] if row["quantity"] >= 10)
    if not eligible:
        eligible = sorted((row["part_master_id"], row["location_id"])
                          for row in opening["stock"] if row["quantity"] > 1)
    if not eligible:
        raise ValueError("Inventory needs a known position above 1 for distinct depletion choices")
    return {"surface": surface, "seed": seed, "opening": opening,
            "stock": deepcopy(opening["stock"]), "focus": list(eligible[seed % len(eligible)])}


def _check_model(surface: str, model: dict) -> None:
    _domain(surface)
    if model.get("surface") != surface:
        raise ValueError("Inventory model belongs to a different surface")


def apply_action(surface: str, model: dict, action: str, scenario: str | None = None) -> dict:
    _check_model(surface, model)
    if action not in ("inject", "recover", "reset"):
        raise ValueError("Unsupported inventory action")
    if (action == "inject" and scenario not in {row[0] for row in _CHOICES}
            or action != "inject" and scenario is not None):
        raise ValueError("Unsupported inventory scenario")
    result = deepcopy(model)
    result["stock"] = deepcopy(result["opening"]["stock"])
    if action == "inject":
        for row in result["stock"]:
            if [row["part_master_id"], row["location_id"]] == result["focus"]:
                row["quantity"] = {"low_stock": min(9, row["quantity"] - 1), "stockout": 0,
                                   "observation_unavailable": None}[scenario]
                row["quality"] = "unavailable" if row["quantity"] is None else "good"
                break
    return result


def frame(surface: str, model: dict, step: int, at: str) -> Frame:
    _check_model(surface, model)
    result = deepcopy(model)
    inventory = deepcopy(result["opening"])
    inventory["stock"] = deepcopy(result["stock"])
    inventory["simulated_at"] = at
    parts = {row["part_master_id"]: row for row in inventory["parts"]}
    locations = {row["location_id"]: row for row in inventory["locations"]}
    for part in inventory["parts"]:
        held = [deepcopy(row) for row in inventory["stock"]
                if row["part_master_id"] == part["part_master_id"]]
        part["held_at"] = held
        part["quantity"] = (sum(row["quantity"] for row in held)
                            if held and all(row["quantity"] is not None for row in held) else None)
    observations = []
    focus = []
    location_fields = ("location_code", "location_name", "location_type", "grid_frame",
                       "grid_east_km", "grid_north_km", "route_frame", "route_position_km",
                       "site_frame", "site_x_m", "site_y_m", "position_claim", "is_on_site")
    for row in inventory["stock"]:
        part, location = parts[row["part_master_id"]], locations[row["location_id"]]
        observations.append({
            "record_type": "inventory_position", "entity_id": row["part_master_id"],
            "location_id": row["location_id"], "simulated_at": at,
            "payload": {"part_master_id": row["part_master_id"], "part_key": part["part_key"],
                        "stock_code": part["stock_code"], "quantity": row["quantity"],
                        "uom": part["uom"], "quality": row["quality"]},
            "context": {"part_name": part["part_name"], "is_synthetic": True,
                        "operational_use": "prohibited", "location_is_fictional": True,
                        **position_details(inventory["details"], row["part_master_id"],
                                           row["location_id"]),
                        **{key: location[key] for key in location_fields if key in location}},
        })
        if [row["part_master_id"], row["location_id"]] == result["focus"]:
            focus.append({**deepcopy(row), "part_name": part["part_name"],
                          "stock_code": part["stock_code"], "uom": part["uom"]})
    current = {"index": 0, "elapsed_minutes": 0, "label": "Current V2 stock",
               "stock": deepcopy(inventory["stock"]), "movements": [], "alerts": [],
               "active_alerts": []}
    snapshot = {
        "inventory": inventory,
        "movement": {"title": "Current V2 stock", "available": False,
                     "unavailable_reason": "Use shared V2 POST controls to change simulated stock.",
                     "threshold": 10, "is_synthetic": True, "operational_use": "prohibited",
                     "focus": focus, "frames": [current]},
    }
    return {"model": result, "snapshot": snapshot, "observations": observations}


def _asset_root(domain: str) -> Path:
    package = import_module(DATASETS[domain][1])
    return Path(package.__file__).parent / "dashboard" / (
        "review_static" if domain == "power-grid" else "static")


def _adapt_html(html: str, surface: str) -> str:
    """Adapt source text in memory; never rewrite legacy bundles or assets."""
    domain = _domain(surface)
    if domain == "upstream":
        html = re.sub(
            r"function renderBanner\(\) \{.*?\n      function renderOverview",
            'function renderBanner() { $("banner-summary").textContent = "V2 fictional inventory"; '
            '$("banner-body").textContent = "V2-only simulated stock; source datasets unchanged. '
            'Not for operational use. Log delivery is reported by the shared controls."; }\n'
            '      function renderOverview', html, count=1, flags=re.S)
        html = html.replace(
            "baseline.stock_is_known ? held.reduce",
            "baseline.stock_is_known && held.length && held.every(r => r.quantity !== null) ? held.reduce")
    if domain == "downstream":
        html = html.replace("known && held.length ? held.reduce",
                            "known && held.length && held.every(row => row.quantity !== null) ? held.reduce")
    if domain == "midstream":
        html = html.replace(
            ' || quantityStatus(opening.get(stockKey(row))) === "unknown"', "")
    replacements = {
        "Local Only": "V2 Current State",
        "Local alerts only.": "V2 stock observations.",
        "Read-only opening records; browser-only movement.": "V2 simulated stock; source records unchanged.",
        "No Azure Monitor publication, real facilities, or physical inter-store transfers.": "No real facilities or physical inter-store transfers. Shared controls report V2 log delivery.",
        "not connected to Azure": "log delivery reported by shared V2 controls",
        "no Azure connection": "delivery reported by shared V2 controls",
        "Portal-only events; no Azure delivery.": "V2 delivery is reported by shared controls.",
        "Restricted &mdash; synthetic data, not a publication path": "V2 fictional inventory",
        "a repeatable, portal-only movement overlay": "V2-only mutable simulated stock",
        "Local only": "V2 simulated inventory",
        "Local-only": "V2 simulated",
        "local-only": "V2 simulated",
        "Read-only": "V2 current state",
        "read-only": "V2 current state",
        "Local review.": "V2 current stock.",
        "Hosted review; movement remains in this browser only.": "V2 current stock.",
        "Not sent to Azure. Playback stays in this browser tab. No writes.": "Log delivery is reported by shared V2 controls.",
        "Never sent to Azure.": "Shared V2 controls report log delivery.",
        "Not published": "Not available",
        "portal-only movement": "V2 simulated stock",
        "portal-only": "V2 simulated",
        "Local alert context": "Stock display context",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    # Keep all nodes needed by the original renderers, but remove obsolete
    # local replay/alert affordances from layout and accessibility trees.
    style = """
<style>
#playback, #attention, section[aria-labelledby="movement-title"],
section[aria-labelledby="alerts-title"], section[aria-labelledby="activity-title"],
section:has(> #active-alerts), section:has(> #alert-summary),
.hero[aria-labelledby="demo-title"], #play, #step, #reset, #speed,
#timeline, #stages, #playback-state { display: none !important; }
details:has(> #activity) { display: none !important; }
</style>
"""
    shim = f'<script src="/{domain}/review/inventory/studio.js"></script>'
    if shim not in html:
        html = html.replace("</head>", shim + "</head>", 1)
    return html.replace("</head>", style + "</head>", 1)


def register_routes(app, runtime) -> None:
    from flask import jsonify, send_file
    from .ui import render_portal

    def add(surface: str) -> None:
        domain = _domain(surface)
        base = f"/{domain}/review/inventory"

        def page():
            original = (_asset_root(domain) / "inventory.html").read_text(encoding="utf-8")
            return render_portal(_adapt_html(original, surface), surface)

        def current():
            return jsonify(deepcopy(runtime.snapshot(surface)))

        def inventory_data():
            return jsonify(deepcopy(runtime.snapshot(surface)["inventory"]))

        def studio():
            return send_file(Path(__file__).parent / "inventory_assets" / "studio.js",
                             mimetype="application/javascript")

        def basemap():
            path = _asset_root(domain) / "basemap.json"
            if not path.exists() and domain == "downstream":
                path = _asset_root("midstream") / "basemap.json"
            return send_file(path, mimetype="application/json") if path.exists() else (jsonify({}), 404)

        for suffix, function in (("", page), ("/studio.js", studio), ("/basemap.json", basemap)):
            app.add_url_rule(base + suffix, f"v2_{domain}_inventory_{function.__name__}",
                             function, methods=["GET"])
        app.add_url_rule(f"/api{base}", f"v2_{domain}_inventory_data", inventory_data, methods=["GET"])
        app.add_url_rule(f"/api{base}/movement", f"v2_{domain}_inventory_current", current, methods=["GET"])

    for surface in INVENTORY_SURFACES:
        add(surface)
