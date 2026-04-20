"""Tier 5 - asset hierarchy + DTDL v3 twin emitter.

Pure function of the inventory dict. Produces a single JSON document:

    {
      "dtmi_base": "dtmi:com:eseg:utility;1",
      "dtdl_interfaces": [ ... DTDL v3 interface docs ... ],
      "twin_instances":  [ ... one twin per asset, with $dtId + $metadata.$model ],
      "relationships":   [ {source, target, name}, ... ],
      "regions":         [ {id, code, name, parent_id}, ... ]
    }

Hierarchy rules (derived from naming conventions in DEFAULT_LOCATIONS):

  Utility (root)
    |-- Region (ALB/POK/KIN/BIN/NEW) - from 3-letter prefix in device_id
    |     |-- Substation (SS_<REG>_NNN)  - from transformer.substation
    |     |     |-- Transformer (TX_<REG>_NNN)
    |     |     |     |-- SmartMeter   (MTR_<REG>_<TXN><MNUM>)
    |     |-- Regulator (REG_<REG>_NNN)
    |     |     |-- GasMeter (GMR_<REG>_<...>)
    |     |-- CathodicProtection (CP_GAS_NNN) - gas region
    |-- PipelineSegment
          |-- PipelineCompressor / PipelineValve / PipelineMetering

Relationship vocabulary:
  contains   - parent asset contains child (region->substation, etc.)
  feeds      - transformer->meter (electrical feeder relationship)
  regulates  - regulator->gas meter (pressure district)

No RNG, no wall-clock, no side effects.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


DTMI_BASE = "dtmi:com:eseg:utility"
DTMI_VER = "1"


REGION_NAMES: Dict[str, str] = {
    "ALB": "Albany",
    "POK": "Poughkeepsie",
    "KIN": "Kingston",
    "BIN": "Binghamton",
    "NEW": "Newburgh",
}


def _dtmi(name: str) -> str:
    return f"{DTMI_BASE}:{name};{DTMI_VER}"


DEVICE_DTMI: Dict[str, str] = {
    "smart_meter": _dtmi("SmartMeter"),
    "transformer": _dtmi("DistributionTransformer"),
    "gas_meter": _dtmi("GasMeter"),
    "gas_regulator": _dtmi("GasRegulator"),
    "cathodic_protection": _dtmi("CathodicProtection"),
    "pipeline_compressor": _dtmi("PipelineCompressor"),
    "pipeline_valve": _dtmi("PipelineValve"),
    "pipeline_metering": _dtmi("PipelineMetering"),
}


# DTDL telemetry contents per device type. Keys must match Tier 1 record
# field names. Units follow DTDL v3 semantic type vocabulary where possible.
DTDL_TELEMETRY: Dict[str, List[Dict[str, Any]]] = {
    "smart_meter": [
        {"@type": ["Telemetry", "Energy"], "name": "kWh_delivered_cumulative", "schema": "double", "unit": "kilowattHour"},
        {"@type": ["Telemetry", "Energy"], "name": "kWh_received_cumulative", "schema": "double", "unit": "kilowattHour"},
        {"@type": ["Telemetry", "Energy"], "name": "kWh_delivered_interval", "schema": "double", "unit": "kilowattHour"},
        {"@type": ["Telemetry", "Energy"], "name": "kWh_received_interval", "schema": "double", "unit": "kilowattHour"},
        {"@type": ["Telemetry", "Voltage"], "name": "voltage_a", "schema": "double", "unit": "volt"},
        {"@type": ["Telemetry", "Voltage"], "name": "voltage_b", "schema": "double", "unit": "volt"},
        {"@type": ["Telemetry", "Voltage"], "name": "voltage_c", "schema": "double", "unit": "volt"},
        {"@type": ["Telemetry", "Current"], "name": "current_a", "schema": "double", "unit": "ampere"},
        {"@type": "Telemetry", "name": "power_factor", "schema": "double"},
        {"@type": ["Telemetry", "Frequency"], "name": "frequency_hz", "schema": "double", "unit": "hertz"},
        {"@type": ["Telemetry", "Power"], "name": "demand_kw", "schema": "double", "unit": "kilowatt"},
        {"@type": "Telemetry", "name": "tamper_flag", "schema": "boolean"},
        {"@type": "Telemetry", "name": "outage_flag", "schema": "boolean"},
        {"@type": "Telemetry", "name": "reverse_flow", "schema": "boolean"},
        {"@type": "Telemetry", "name": "rssi_dbm", "schema": "integer"},
    ],
    "transformer": [
        {"@type": ["Telemetry", "Temperature"], "name": "oil_temp_top_c", "schema": "double", "unit": "degreeCelsius"},
        {"@type": ["Telemetry", "Temperature"], "name": "oil_temp_bottom_c", "schema": "double", "unit": "degreeCelsius"},
        {"@type": ["Telemetry", "Temperature"], "name": "ambient_temp_c", "schema": "double", "unit": "degreeCelsius"},
        {"@type": "Telemetry", "name": "load_pct", "schema": "double"},
        {"@type": ["Telemetry", "Power"], "name": "load_kva", "schema": "double", "unit": "kilowatt"},
        {"@type": ["Telemetry", "Voltage"], "name": "voltage_primary_kv", "schema": "double", "unit": "kilovolt"},
        {"@type": ["Telemetry", "Voltage"], "name": "voltage_secondary_v", "schema": "double", "unit": "volt"},
        {"@type": "Telemetry", "name": "tap_position", "schema": "integer"},
        {"@type": "Telemetry", "name": "partial_discharge_pc", "schema": "double"},
        {"@type": "Telemetry", "name": "dga_h2_ppm", "schema": "double"},
        {"@type": "Telemetry", "name": "dga_ch4_ppm", "schema": "double"},
        {"@type": "Telemetry", "name": "dga_c2h2_ppm", "schema": "double"},
        {"@type": "Telemetry", "name": "moisture_ppm", "schema": "double"},
        {"@type": "Telemetry", "name": "bushing_temp_c", "schema": "double"},
    ],
    "gas_meter": [
        {"@type": "Telemetry", "name": "volume_ccf_interval", "schema": "double"},
        {"@type": "Telemetry", "name": "volume_ccf_cumulative", "schema": "double"},
        {"@type": "Telemetry", "name": "volume_therm_interval", "schema": "double"},
        {"@type": ["Telemetry", "Pressure"], "name": "pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": ["Telemetry", "Temperature"], "name": "gas_temp_f", "schema": "double", "unit": "degreeFahrenheit"},
        {"@type": "Telemetry", "name": "tamper_flag", "schema": "boolean"},
    ],
    "gas_regulator": [
        {"@type": ["Telemetry", "Pressure"], "name": "inlet_pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": ["Telemetry", "Pressure"], "name": "outlet_pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": ["Telemetry", "Temperature"], "name": "gas_temp_f", "schema": "double", "unit": "degreeFahrenheit"},
        {"@type": "Telemetry", "name": "regulator_position_pct", "schema": "double"},
        {"@type": "Telemetry", "name": "relief_valve_status", "schema": "string"},
        {"@type": "Telemetry", "name": "odorizer_level_pct", "schema": "double"},
    ],
    "cathodic_protection": [
        {"@type": ["Telemetry", "Voltage"], "name": "pipe_to_soil_mv", "schema": "double", "unit": "millivolt"},
        {"@type": ["Telemetry", "Current"], "name": "rectifier_current_a", "schema": "double", "unit": "ampere"},
        {"@type": ["Telemetry", "Voltage"], "name": "rectifier_voltage_v", "schema": "double", "unit": "volt"},
        {"@type": "Telemetry", "name": "rectifier_status", "schema": "string"},
    ],
    "pipeline_compressor": [
        {"@type": ["Telemetry", "Pressure"], "name": "suction_pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": ["Telemetry", "Pressure"], "name": "discharge_pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": ["Telemetry", "Temperature"], "name": "temperature_f", "schema": "double", "unit": "degreeFahrenheit"},
        {"@type": ["Telemetry", "Velocity"], "name": "vibration", "schema": "double"},
        {"@type": ["Telemetry", "AngularVelocity"], "name": "rpm", "schema": "double"},
        {"@type": "Telemetry", "name": "pump_status", "schema": "string"},
    ],
    "pipeline_valve": [
        {"@type": ["Telemetry", "Pressure"], "name": "pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": "Telemetry", "name": "valve_position_pct", "schema": "double"},
        {"@type": "Telemetry", "name": "valve_state", "schema": "string"},
    ],
    "pipeline_metering": [
        {"@type": ["Telemetry", "Pressure"], "name": "pressure_psig", "schema": "double", "unit": "poundPerSquareInch"},
        {"@type": "Telemetry", "name": "flow_rate_mcf", "schema": "double"},
        {"@type": "Telemetry", "name": "volume_mcf_cumulative", "schema": "double"},
        {"@type": ["Telemetry", "Temperature"], "name": "gas_temp_f", "schema": "double", "unit": "degreeFahrenheit"},
    ],
}


_DEVICE_RE = re.compile(r"^[A-Z]+_([A-Z]{3})_")


def _region_of(device_id: str) -> str:
    m = _DEVICE_RE.match(device_id)
    return m.group(1) if m else "UNK"


def _parse_meter_txnum(meter_id: str) -> Optional[str]:
    # MTR_ALB_001001 -> TX_ALB_001
    m = re.match(r"^MTR_([A-Z]{3})_(\d{3})\d{3}$", meter_id)
    if not m:
        return None
    return f"TX_{m.group(1)}_{m.group(2)}"


def _region_id(code: str) -> str:
    return f"RGN_{code}"


def _dtdl_interface(device_type: str, display_name: str) -> Dict[str, Any]:
    contents: List[Dict[str, Any]] = [
        {"@type": "Property", "name": "device_id", "schema": "string"},
        {"@type": "Property", "name": "region_id", "schema": "string"},
        {"@type": "Property", "name": "parent_asset_id", "schema": "string"},
        {"@type": "Property", "name": "latitude", "schema": "double"},
        {"@type": "Property", "name": "longitude", "schema": "double"},
    ]
    contents.extend(DTDL_TELEMETRY.get(device_type, []))
    return {
        "@context": ["dtmi:dtdl:context;3"],
        "@id": DEVICE_DTMI[device_type],
        "@type": "Interface",
        "displayName": display_name,
        "contents": contents,
    }


def _twin(asset_id: str, device_type: str, region_id: str,
          parent_id: Optional[str], lat: Optional[float],
          lon: Optional[float], extras: Optional[Dict[str, Any]] = None
          ) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "$dtId": asset_id,
        "$metadata": {"$model": DEVICE_DTMI[device_type]},
        "device_id": asset_id,
        "region_id": region_id,
    }
    if parent_id:
        body["parent_asset_id"] = parent_id
    if lat is not None:
        body["latitude"] = lat
    if lon is not None:
        body["longitude"] = lon
    if extras:
        body.update(extras)
    return body


def build_hierarchy(inventory: Dict[str, Any]) -> Dict[str, Any]:
    regions: Dict[str, Dict[str, Any]] = {}
    twins: List[Dict[str, Any]] = []
    relationships: List[Dict[str, str]] = []
    substations_seen: Dict[str, Dict[str, Any]] = {}

    # --- Regions from substation ids -------------------------------------
    for ss in inventory.get("substations", []):
        code = _region_of(ss["id"])
        regions.setdefault(code, {
            "id": _region_id(code),
            "code": code,
            "name": REGION_NAMES.get(code, code),
            "parent_id": "UTIL_ESEG",
        })
        substations_seen[ss["id"]] = ss
        relationships.append({"source": _region_id(code), "target": ss["id"],
                              "name": "contains"})

    # Utility root twin
    twins.append({
        "$dtId": "UTIL_ESEG",
        "$metadata": {"$model": _dtmi("Utility")},
        "device_id": "UTIL_ESEG",
        "region_id": "",
        "name": "Empire State Electric & Gas",
    })

    # Substation twins
    for ss in inventory.get("substations", []):
        code = _region_of(ss["id"])
        twins.append(_twin(ss["id"], "transformer",  # reuse DTDL-less stub
                           _region_id(code), _region_id(code),
                           ss.get("lat"), ss.get("lon"),
                           {"$metadata": {"$model": _dtmi("Substation")},
                            "name": ss.get("name", ss["id"])}))

    # Transformers
    transformer_ids: set = set()
    for tx in inventory.get("transformers", []):
        code = _region_of(tx["id"])
        parent_ss = tx.get("substation")
        transformer_ids.add(tx["id"])
        twins.append(_twin(tx["id"], "transformer", _region_id(code),
                           parent_ss, tx.get("lat"), tx.get("lon"),
                           {"rating_kva": tx.get("rating_kva")}))
        if parent_ss:
            relationships.append({"source": parent_ss, "target": tx["id"],
                                  "name": "contains"})

    # Smart meters - map to transformer via naming convention
    for mtr in inventory.get("meters", []):
        code = _region_of(mtr["id"])
        tx_id = _parse_meter_txnum(mtr["id"])
        if tx_id not in transformer_ids:
            tx_id = None  # avoid orphan relationships when inventory gap exists
        twins.append(_twin(mtr["id"], "smart_meter", _region_id(code),
                           tx_id, mtr.get("lat"), mtr.get("lon"),
                           {"customer_class": mtr.get("type")}))
        if tx_id:
            relationships.append({"source": tx_id, "target": mtr["id"],
                                  "name": "feeds"})
        else:
            relationships.append({"source": _region_id(code), "target": mtr["id"],
                                  "name": "contains"})

    # Regulators
    for reg in inventory.get("regulators", []):
        code = _region_of(reg["id"])
        twins.append(_twin(reg["id"], "gas_regulator", _region_id(code),
                           _region_id(code), reg.get("lat"), reg.get("lon"),
                           {"inlet_psi": reg.get("inlet_psi")}))
        relationships.append({"source": _region_id(code), "target": reg["id"],
                              "name": "contains"})

    # Gas meters - map to regulator by region
    regulators_by_region: Dict[str, str] = {}
    for reg in inventory.get("regulators", []):
        code = _region_of(reg["id"])
        regulators_by_region.setdefault(code, reg["id"])
    for gm in inventory.get("gas_meters", []):
        code = _region_of(gm["id"])
        parent = regulators_by_region.get(code, _region_id(code))
        twins.append(_twin(gm["id"], "gas_meter", _region_id(code),
                           parent, gm.get("lat"), gm.get("lon"),
                           {"customer_class": gm.get("type")}))
        relationships.append({"source": parent, "target": gm["id"],
                              "name": "regulates"})

    # Cathodic protection stations
    for cp in inventory.get("cp_stations", []):
        # CP stations use GAS region, not geographic prefix. Attribute to a
        # dedicated gas pipeline region so they have a parent.
        region_id = "RGN_GASOPS"
        regions.setdefault("GASOPS", {
            "id": region_id, "code": "GASOPS",
            "name": "Gas Operations",
            "parent_id": "UTIL_ESEG",
        })
        twins.append(_twin(cp["id"], "cathodic_protection", region_id,
                           region_id, cp.get("lat"), cp.get("lon")))
        relationships.append({"source": region_id, "target": cp["id"],
                              "name": "contains"})

    # Pipeline stations - rolled up under a single pipeline segment
    pipe_seg_id = "PIPE_SEG_NE"
    if inventory.get("pipeline_stations"):
        twins.append({
            "$dtId": pipe_seg_id,
            "$metadata": {"$model": _dtmi("PipelineSegment")},
            "device_id": pipe_seg_id,
            "region_id": "RGN_PIPELINE",
            "name": "Northeast Pipeline Segment",
        })
        regions.setdefault("PIPELINE", {
            "id": "RGN_PIPELINE", "code": "PIPELINE",
            "name": "Northeast Pipeline",
            "parent_id": "UTIL_ESEG",
        })
        relationships.append({"source": "RGN_PIPELINE", "target": pipe_seg_id,
                              "name": "contains"})
    _type_map = {
        "compressor": "pipeline_compressor",
        "valve": "pipeline_valve",
        "metering": "pipeline_metering",
    }
    for ps in inventory.get("pipeline_stations", []):
        dt = _type_map.get(ps.get("type", ""), "pipeline_metering")
        twins.append(_twin(ps["id"], dt, "RGN_PIPELINE", pipe_seg_id,
                           ps.get("lat"), ps.get("lon"),
                           {"name": ps.get("name")}))
        relationships.append({"source": pipe_seg_id, "target": ps["id"],
                              "name": "contains"})

    # Emit region twins + utility->region edges now that all regions are
    # discovered (electric/GAS/PIPELINE).
    for code, r in sorted(regions.items()):
        twins.append({
            "$dtId": r["id"],
            "$metadata": {"$model": _dtmi("Region")},
            "device_id": r["id"],
            "region_id": r["id"],
            "parent_asset_id": "UTIL_ESEG",
            "name": r["name"],
            "code": code,
        })
        relationships.append({"source": "UTIL_ESEG", "target": r["id"],
                              "name": "contains"})

    # DTDL interfaces ------------------------------------------------------
    interfaces = [
        {
            "@context": ["dtmi:dtdl:context;3"],
            "@id": _dtmi("Utility"),
            "@type": "Interface",
            "displayName": "Utility",
            "contents": [
                {"@type": "Property", "name": "name", "schema": "string"},
            ],
        },
        {
            "@context": ["dtmi:dtdl:context;3"],
            "@id": _dtmi("Region"),
            "@type": "Interface",
            "displayName": "Region",
            "contents": [
                {"@type": "Property", "name": "name", "schema": "string"},
                {"@type": "Property", "name": "code", "schema": "string"},
            ],
        },
        {
            "@context": ["dtmi:dtdl:context;3"],
            "@id": _dtmi("Substation"),
            "@type": "Interface",
            "displayName": "Substation",
            "contents": [
                {"@type": "Property", "name": "name", "schema": "string"},
                {"@type": "Property", "name": "latitude", "schema": "double"},
                {"@type": "Property", "name": "longitude", "schema": "double"},
            ],
        },
        {
            "@context": ["dtmi:dtdl:context;3"],
            "@id": _dtmi("PipelineSegment"),
            "@type": "Interface",
            "displayName": "PipelineSegment",
            "contents": [
                {"@type": "Property", "name": "name", "schema": "string"},
            ],
        },
    ]
    interface_display = {
        "smart_meter": "SmartMeter",
        "transformer": "DistributionTransformer",
        "gas_meter": "GasMeter",
        "gas_regulator": "GasRegulator",
        "cathodic_protection": "CathodicProtection",
        "pipeline_compressor": "PipelineCompressor",
        "pipeline_valve": "PipelineValve",
        "pipeline_metering": "PipelineMetering",
    }
    for dt, name in interface_display.items():
        interfaces.append(_dtdl_interface(dt, name))

    # Stable sort for byte-identical output across seeded runs.
    twins.sort(key=lambda t: t["$dtId"])
    relationships.sort(key=lambda r: (r["source"], r["target"], r["name"]))
    region_list = sorted(
        [{"id": r["id"], "code": r["code"], "name": r["name"],
          "parent_id": r.get("parent_id", "")}
         for r in regions.values()],
        key=lambda r: r["id"],
    )

    return {
        "dtmi_base": f"{DTMI_BASE};{DTMI_VER}",
        "dtdl_interfaces": interfaces,
        "twin_instances": twins,
        "relationships": relationships,
        "regions": region_list,
    }
