"""Deterministic companion priorities after mandatory intervention coverage.

Exact set-cover/beam search adds cost without making ten observations complete.
Keep the proven coverage pass, break equal coverage by source-channel utility,
then fill by missing channel roles and explicitly joined equipment coherence.
No asset identifiers, anomaly names or unobserved values are authored here.
"""

GROUPS = {
    "power-grid": (
        ("transformer_temperature", ("top_oil_temperature",)),
        ("transformer_loading", ("loading",)),
        ("ambient_temperature", ("air_temperature",)),
        ("active_power", ("active_power",)),
        ("reactive_power", ("reactive_power",)),
        ("apparent_power", ("apparent_power",)),
        ("voltage", ("voltage",)),
        ("current", ("current",)),
        ("cooling_state", ("cooling_stage",)),
    ),
    "upstream": (
        ("well_pressure", ("tubing_pressure_kpa",)),
        ("lift_state", ("lift_run_state_index",)),
        ("separator_pressure", ("separator_pressure_kpa",)),
        ("well_temperature", ("wellhead_temperature_c",)),
        ("lift_current", ("lift_current_a",)),
        ("separator_gas_rate", ("separator_gas_rate_ksm3d",)),
        ("oil_rate", ("treater_oil_rate_m3d",)),
        ("water_rate", ("fwko_water_rate_m3d",)),
    ),
    "midstream": (
        ("inlet_flow", ("inlet_volume_flow",)),
        ("outlet_flow", ("outlet_volume_flow",)),
        ("pump_temperature", ("bearing_temperature",)),
        ("valve_feedback", ("feedback_position",)),
        ("valve_command", ("command_position",)),
        ("pump_speed", ("speed",)),
        ("pump_power", ("active_power",)),
        ("pump_vibration", ("vibration_velocity_rms",)),
        ("fluid_density", ("flowing_density",)),
    ),
    "downstream": (
        ("temperature", ("temperature_c",)),
        ("pressure", ("pressure_kpa",)),
        ("power", ("power_kw",)),
        ("throughput_index", ("throughput_index",)),
        ("condition_index", ("condition_index",)),
    ),
    "downstream-portfolio": (
        ("corridor_inlet_flow", ("corridor_inlet_flow",)),
        ("corridor_outlet_flow", ("corridor_outlet_flow",)),
        ("throughput_actual", ("throughput_actual",)),
        ("throughput_target", ("throughput_target",)),
        ("yield_share", ("yield_share",)),
        ("inventory_quantity", ("inventory_equivalent_volume",)),
        ("inventory_balance", ("inventory_balance_rate",)),
    ),
}


def _metadata(row):
    return row["context"].get("asset", row["context"])


def utility(surface, row, selected):
    name = row["payload"].get("measurement")
    present = {r["payload"].get("measurement") for r in selected}
    role_score = max((100 - index * 5 for index, (_, names) in enumerate(GROUPS[surface])
                      if name in names and not present.intersection(names)), default=0)
    asset = _metadata(row)
    coherence = 0
    for other in selected:
        peer = _metadata(other)
        other_name = other["payload"].get("measurement")
        # Explicit well identity is stronger than mere site/facility colocation.
        if asset.get("well_id") and asset["well_id"] == peer.get("well_id"):
            coherence = max(coherence, 3)
        if row["entity_id"] == other["entity_id"]:
            coherence = max(coherence, 2)
            if surface == "power-grid":
                electrical = {"active_power", "reactive_power", "apparent_power", "voltage", "current"}
                thermal = {"condition_index", "loading", "top_oil_temperature", "cooling_stage"}
                if any(name in bundle and other_name in bundle for bundle in (electrical, thermal)):
                    coherence = max(coherence, 4)
        if (surface == "downstream-portfolio" and asset.get("module_id")
                and asset["module_id"] == peer.get("module_id")):
            coherence = max(coherence, 3)
        for link in row["context"].get("topology", {}).get("links", []):
            if (link.get("source_kind") == link.get("target_kind") == "asset"
                    and other["entity_id"] in {link["source_id"], link["target_id"]}):
                coherence = max(coherence, 1)
    # Aliased outlet readings and derived massflow do not earn extra roles.
    return role_score, coherence


def report(surface, rows, candidates):
    present = {r["payload"].get("measurement") for r in rows}
    available = {r["payload"].get("measurement") for r in candidates}
    groups = GROUPS[surface]
    result = {
        "policy": "coverage_first_then_missing_roles_and_joined_companions_v1",
        "selected_roles": [role for role, names in groups if present.intersection(names)],
        "available_roles_not_selected": [
            role for role, names in groups if available.intersection(names) and not present.intersection(names)],
        "roles_absent_from_source_candidates": [
            role for role, names in groups if not available.intersection(names)],
        "completeness": "bounded_evidence_not_complete_domain_or_independent_sensor_coverage",
    }
    if surface == "downstream":
        result["source_gap"] = "aggregate_telemetry_has_no_physical_flow_or_vibration_channels"
    return result
