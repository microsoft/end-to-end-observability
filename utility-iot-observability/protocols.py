"""Tier 3 — Protocol-aware payload views.

Transforms canonical Tier 1/2 telemetry records into protocol-native envelopes
that resemble what real OT gateways emit. One protocol is chosen per
device_type:

    smart_meter, gas_meter                                 -> DLMS/COSEM
    transformer                                            -> IEC 61850
    gas_regulator, cathodic_protection, pipeline_valve     -> DNP3
    pipeline_compressor, pipeline_metering                 -> OPC UA

All view builders are PURE FUNCTIONS of the canonical record — no RNG, no
wall-clock. That keeps protocol output byte-identical across seeded runs and
lets operators toggle `--protocol-views` on/off without perturbing the other
five output streams.

Envelope shape (common to all four protocols):

    {
        "timestamp": str,
        "device_id": str,
        "device_type": str,
        "protocol": "dlms-cosem"|"iec-61850"|"dnp3"|"opc-ua",
        "quality": "GOOD"|"UNCERTAIN"|"STALE"|"BAD",
        "native_quality": <int or dict, protocol-specific>,
        "scenario_ids": [...]   # only present if canonical record had it
        "points": [ <protocol-specific point dicts> ]
    }
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Device-type -> protocol mapping
# ---------------------------------------------------------------------------
DEVICE_PROTOCOL: Dict[str, str] = {
    "smart_meter": "dlms-cosem",
    "gas_meter": "dlms-cosem",
    "transformer": "iec-61850",
    "gas_regulator": "dnp3",
    "cathodic_protection": "dnp3",
    "pipeline_valve": "dnp3",
    "pipeline_compressor": "opc-ua",
    "pipeline_metering": "opc-ua",
}


# ---------------------------------------------------------------------------
# Quality translations
# ---------------------------------------------------------------------------
# DLMS access-result (IEC 62056-5-3, ACCESS-RESULT enum, subset):
#   0 = success, 1 = hardware-fault, 2 = temporary-failure,
#   3 = read-write-denied, 4 = object-undefined
_DLMS_ACCESS_RESULT = {
    "GOOD": 0,
    "UNCERTAIN": 1,
    "STALE": 2,
    "BAD": 3,
    # MAINT: manual override / maintenance hold - access denied semantics.
    "MAINT": 3,
}

# IEC 61850 quality bitstring (IEC 61850-7-3). Emitted as a dict so consumers
# can read individual bits without parsing.
def _iec_quality(q: str) -> Dict[str, Any]:
    base = {
        "validity": "good",
        "source": "process",
        "test": False,
        "operatorBlocked": False,
        "detailQual": {
            "overflow": False,
            "outOfRange": False,
            "badReference": False,
            "oscillatory": False,
            "failure": False,
            "oldData": False,
            "inconsistent": False,
            "inaccurate": False,
        },
    }
    if q == "UNCERTAIN":
        base["validity"] = "questionable"
        base["detailQual"]["inaccurate"] = True
    elif q == "STALE":
        base["validity"] = "questionable"
        base["detailQual"]["oldData"] = True
    elif q == "BAD":
        base["validity"] = "invalid"
        base["detailQual"]["failure"] = True
    elif q == "MAINT":
        base["validity"] = "questionable"
        base["operatorBlocked"] = True
        base["source"] = "substituted"
    return base


# DNP3 flag bits (Group 30 / Group 1 object flag byte, IEEE 1815).
DNP3_ONLINE = 0x01
DNP3_RESTART = 0x02
DNP3_COMM_LOST = 0x04
DNP3_REMOTE_FORCED = 0x08
DNP3_LOCAL_FORCED = 0x10
DNP3_OVER_RANGE = 0x20
DNP3_REFERENCE_ERR = 0x40


def _dnp3_flags(q: str) -> int:
    if q == "GOOD":
        return DNP3_ONLINE
    if q == "UNCERTAIN":
        return DNP3_ONLINE | DNP3_REFERENCE_ERR
    if q == "STALE":
        return DNP3_ONLINE | DNP3_REMOTE_FORCED
    if q == "MAINT":
        return DNP3_ONLINE | DNP3_LOCAL_FORCED
    # BAD: ONLINE bit dropped -> consumer treats as invalid
    return DNP3_COMM_LOST


# OPC UA StatusCode (OPC UA Part 4, Annex A, subset):
#   Good                    = 0x00000000
#   Uncertain               = 0x40000000
#   UncertainDataSubNormal  = 0x40A40000
#   Bad                     = 0x80000000
_OPCUA_STATUS = {
    "GOOD": 0x00000000,
    "UNCERTAIN": 0x40000000,
    "STALE": 0x40A40000,
    "BAD": 0x80000000,
    # Uncertain_SubstituteValue - matches manual-override/maintenance semantics.
    "MAINT": 0x40910000,
}


def _status_hex(code: int) -> str:
    return f"0x{code:08X}"


# ---------------------------------------------------------------------------
# DLMS/COSEM mapper
# ---------------------------------------------------------------------------
# OBIS list per device type. Each entry:
#   (obis_code, logical_name, class_id, source_field, unit, scaler)
# class_id values from COSEM: 1=Data, 3=Register, 4=ExtendedRegister
# scaler is the decimal exponent applied to `value` to recover the SI unit
# (per IEC 62056-6-1 "scaler_unit"): raw_value * 10**scaler = value in `unit`.
_DLMS_SMART_METER = [
    ("0-0:96.1.0",   "Device ID",                 1, "device_id",                "",    0),
    ("1-0:1.8.0",    "Active energy import",      3, "kWh_delivered_cumulative", "Wh",  3),   # kWh * 1e3 = Wh
    ("1-0:2.8.0",    "Active energy export",      3, "kWh_received_cumulative",  "Wh",  3),
    ("1-0:1.7.0",    "Active power import",       3, "demand_kw",                "W",   3),
    ("1-0:32.7.0",   "Voltage L1",                3, "voltage_a",                "V",   0),
    ("1-0:52.7.0",   "Voltage L2",                3, "voltage_b",                "V",   0),
    ("1-0:72.7.0",   "Voltage L3",                3, "voltage_c",                "V",   0),
    ("1-0:31.7.0",   "Current L1",                3, "current_a",                "A",   0),
    ("1-0:14.7.0",   "Frequency",                 3, "frequency_hz",             "Hz",  0),
    ("1-0:13.7.0",   "Power factor",              3, "power_factor",             "",    0),
    ("0-0:96.50.0",  "Tamper detected",           1, "tamper_flag",              "",    0),
    ("0-0:96.7.0",   "Outage flag",               1, "outage_flag",              "",    0),
    ("0-0:96.51.0",  "Reverse power flow",        1, "reverse_flow",             "",    0),
]

_DLMS_GAS_METER = [
    ("0-0:96.1.0",   "Device ID",                 1, "device_id",                    "",    0),
    ("7-0:3.0.0",    "Volume (uncompensated)",    3, "volume_ccf_cumulative",        "ft3", 2),   # 1 ccf = 100 ft3
    ("7-0:13.0.0",   "Corrected volume",          3, "corrected_volume_therms_cumulative", "therm", 0),
    ("7-0:3.1.0",    "Volume flow (interval)",    3, "volume_ccf_interval",          "ft3", 2),
    ("7-0:41.0.0",   "Pressure (gauge)",          3, "pressure_psig",                "psi", 0),
    ("7-0:42.0.0",   "Gas temperature",           3, "temperature_f",                "degF", 0),
    ("7-0:96.5.0",   "Battery voltage",           3, "battery_voltage",              "V",   0),
    ("0-0:96.50.0",  "Tamper detected",           1, "tamper_flag",                  "",    0),
    ("0-0:96.50.1",  "Leak alarm",                1, "leak_alarm",                   "",    0),
]


def _to_dlms(record: Dict[str, Any]) -> Dict[str, Any]:
    dt = record["device_type"]
    table = _DLMS_SMART_METER if dt == "smart_meter" else _DLMS_GAS_METER
    q = record.get("quality", "GOOD")
    access_ok = _DLMS_ACCESS_RESULT["GOOD"]
    access_bad = _DLMS_ACCESS_RESULT[q] if q in _DLMS_ACCESS_RESULT else 0

    points: List[Dict[str, Any]] = []
    for obis, ln, class_id, field, unit, scaler in table:
        if field not in record:
            # Optional/conditional field (e.g. reverse_flow only present when
            # the DER scenario fires). Skip entirely to preserve pre-Tier-5
            # byte-identical protocol output when the flag never trips.
            continue
        val = record.get(field)
        # BAD means the canonical record's BAD_FIELD is None; surface that as
        # access-result = read-write-denied on every point whose value is None.
        # All other points carry the overall record's access result.
        point_result = access_bad if val is None else access_ok
        # Scale numeric values up to raw integer units when possible; leave
        # non-numeric (bool/str) values unscaled.
        raw_value: Any = val
        if isinstance(val, bool):
            raw_value = 1 if val else 0
        elif isinstance(val, (int, float)) and scaler != 0:
            raw_value = round(val * (10 ** scaler))
        points.append({
            "obis_code": obis,
            "logical_name": ln,
            "class_id": class_id,
            "value": raw_value,
            "unit": unit,
            "scaler": scaler,
            "access_result": point_result,
        })
    return {
        "timestamp": record["timestamp"],
        "device_id": record["device_id"],
        "device_type": dt,
        "protocol": "dlms-cosem",
        "association_logical_name": "public-client",
        "quality": q,
        "native_quality": access_bad,
        "points": points,
    }


# ---------------------------------------------------------------------------
# IEC 61850 mapper (transformer)
# ---------------------------------------------------------------------------
# Each entry: (ln_class, ln_inst, do_name, da_name, source_field, units)
# data_ref format: <LD>/<LN_class><LN_inst>.<DO>.<DA>
# LN instances are 1-based per IEC 61850-7-2.
_IEC_TRANSFORMER = [
    ("YPTR", 1, "TmpOil",  "mag.f", "oil_temp_top_c",     "Cel"),
    ("YPTR", 1, "TmpOilBot", "mag.f", "oil_temp_bottom_c", "Cel"),
    ("YPTR", 1, "TmpAmb",  "mag.f", "ambient_temp_c",     "Cel"),
    ("YPTR", 1, "Load",    "mag.f", "load_pct",           "%"),
    ("MMXU", 1, "W",       "mag.f", "load_kw",            "kW"),
    ("MMXU", 1, "PhV.phsA", "cVal.mag.f", "voltage_secondary_v", "V"),
    ("MMXU", 1, "PhV.phsB", "cVal.mag.f", "voltage_secondary_v", "V"),
    ("MMXU", 1, "PhV.phsC", "cVal.mag.f", "voltage_secondary_v", "V"),
    ("MMXU", 1, "A.phsA",  "cVal.mag.f", "current_a_phase_a",  "A"),
    ("MMXU", 1, "A.phsB",  "cVal.mag.f", "current_a_phase_b",  "A"),
    ("MMXU", 1, "A.phsC",  "cVal.mag.f", "current_a_phase_c",  "A"),
    ("MMXU", 1, "VPrim",   "mag.f", "voltage_primary_kv",  "kV"),
    ("YLTC", 1, "TapChg",  "valWTr.posVal", "tap_position", ""),
    ("SPDC", 1, "PDAmp",   "mag.f", "partial_discharge_pc", "pC"),
    ("SIML", 1, "H2",      "mag.f", "dga_h2_ppm",   "ppm"),
    ("SIML", 1, "CH4",     "mag.f", "dga_ch4_ppm",  "ppm"),
    ("SIML", 1, "C2H2",    "mag.f", "dga_c2h2_ppm", "ppm"),
    ("SIML", 1, "C2H4",    "mag.f", "dga_c2h4_ppm", "ppm"),
    ("SIML", 1, "C2H6",    "mag.f", "dga_c2h6_ppm", "ppm"),
    ("SIML", 1, "CO",      "mag.f", "dga_co_ppm",   "ppm"),
    ("SIML", 1, "CO2",     "mag.f", "dga_co2_ppm",  "ppm"),
    ("SIML", 1, "H2O",     "mag.f", "moisture_ppm", "ppm"),
    ("SIML", 1, "BushCap", "mag.f", "bushing_cap_pf", "pF"),
    ("GGIO", 1, "FltInd",  "stVal", "fault_detected", ""),
]


def _to_iec61850(record: Dict[str, Any]) -> Dict[str, Any]:
    q = record.get("quality", "GOOD")
    base_quality = _iec_quality(q)
    bad_quality = _iec_quality("BAD")

    points: List[Dict[str, Any]] = []
    ld = "XFMR1"
    for ln_class, ln_inst, do, da, field, units in _IEC_TRANSFORMER:
        val = record.get(field)
        point_q = bad_quality if val is None else base_quality
        if isinstance(val, bool):
            val = bool(val)
        data_ref = f"{ld}/{ln_class}{ln_inst}.{do}.{da}"
        points.append({
            "data_ref": data_ref,
            "ln_class": ln_class,
            "ln_inst": ln_inst,
            "do_name": do,
            "da_name": da,
            "value": val,
            "units": units,
            "quality": point_q,
        })

    return {
        "timestamp": record["timestamp"],
        "device_id": record["device_id"],
        "device_type": record["device_type"],
        "protocol": "iec-61850",
        "logical_device": ld,
        "ied_name": record["device_id"],
        "quality": q,
        "native_quality": base_quality,
        "points": points,
    }


# ---------------------------------------------------------------------------
# DNP3 mapper (regulator, CP, valve)
# ---------------------------------------------------------------------------
# Each entry: (group, variation, index, source_field, is_bool_to_binary)
# Group 30 var 5 = Analog Input with single-precision float and flags.
# Group 1 var 2 = Binary Input with flags.
# Group 20 var 5 = 32-bit Counter with flags.
_DNP3_REGULATOR = [
    (30, 5, 0, "inlet_pressure_psig",    False),
    (30, 5, 1, "outlet_pressure_psig",   False),
    (30, 5, 2, "flow_rate_mcfh",         False),
    (30, 5, 3, "gas_temperature_f",      False),
    (30, 5, 4, "regulator_position_pct", False),
    (30, 5, 5, "odorizer_level_pct",     False),
    (1,  2, 0, "relief_valve_status",    True),   # NORMAL=0, OPEN=1
    (1,  2, 1, "slam_shut_status",       True),   # NORMAL=0, TRIPPED=1
]

_DNP3_CATHODIC = [
    (30, 5, 0, "pipe_to_soil_mv",        False),
    (30, 5, 1, "rectifier_voltage",      False),
    (30, 5, 2, "rectifier_current",      False),
    (30, 5, 3, "ac_supply_voltage",      False),
    (1,  2, 0, "rectifier_status",       True),   # OFF=0, ON=1
]

_DNP3_VALVE = [
    (30, 5, 0, "pressure_psig",          False),
    (30, 5, 1, "temperature_f",          False),
    (30, 5, 2, "valve_position_pct",     False),
    (30, 5, 3, "flow_rate_mcfh",         False),
    (1,  2, 0, "leak_detection_flag",    True),
]


def _dnp3_binary_value(field: str, val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, str):
        # NORMAL/OFF/CLOSED -> 0, everything else -> 1.
        return 0 if val.upper() in ("NORMAL", "OFF", "CLOSED") else 1
    return int(bool(val))


def _to_dnp3(record: Dict[str, Any]) -> Dict[str, Any]:
    dt = record["device_type"]
    if dt == "gas_regulator":
        table = _DNP3_REGULATOR
    elif dt == "cathodic_protection":
        table = _DNP3_CATHODIC
    else:
        table = _DNP3_VALVE

    q = record.get("quality", "GOOD")
    base_flags = _dnp3_flags(q)
    bad_flags = _dnp3_flags("BAD")

    points: List[Dict[str, Any]] = []
    for group, variation, index, field, is_binary in table:
        val = record.get(field)
        point_flags = bad_flags if val is None else base_flags
        if is_binary:
            encoded = _dnp3_binary_value(field, val)
        else:
            encoded = val
        points.append({
            "group": group,
            "variation": variation,
            "index": index,
            "source_field": field,
            "value": encoded,
            "flags": point_flags,
            "flags_hex": f"0x{point_flags:02X}",
            "event_class": 1,
        })

    return {
        "timestamp": record["timestamp"],
        "device_id": record["device_id"],
        "device_type": dt,
        "protocol": "dnp3",
        "quality": q,
        "native_quality": base_flags,
        "native_quality_hex": f"0x{base_flags:02X}",
        "points": points,
    }


# ---------------------------------------------------------------------------
# OPC UA mapper (compressor, metering)
# ---------------------------------------------------------------------------
# Each entry: (browse_name, source_field, ua_type, units)
# ua_type follows OPC UA BuiltInType names.
_OPCUA_COMPRESSOR = [
    ("Pressure",      "pressure_psig",     "Float", "psi"),
    ("Temperature",   "temperature_f",     "Float", "degF"),
    ("Flow",          "flow_rate_mcfh",    "Float", "mcf/h"),
    ("PumpStatus",    "pump_status",       "String", ""),
    ("DischargePsi",  "pump_discharge_psi", "Float", "psi"),
    ("SuctionPsi",    "pump_suction_psi",  "Float", "psi"),
    ("Vibration",     "vibration_mms",     "Float", "mm/s"),
    ("H2S",           "h2s_ppm",           "Float", "ppm"),
]

_OPCUA_METERING = [
    ("Pressure",              "pressure_psig",           "Float", "psi"),
    ("Temperature",           "temperature_f",           "Float", "degF"),
    ("Flow",                  "flow_rate_mcfh",          "Float", "mcf/h"),
    ("VolumeInterval",        "volume_mcf_interval",     "Float", "mcf"),
    ("VolumeCumulative",      "volume_mcf_cumulative",   "Double", "mcf"),
    ("PressureRatio",         "pressure_ratio",          "Float", ""),
]


def _ua_type_name(dt: str) -> str:
    # Folder-style browse path prefix.
    return "PipelineCompressor" if dt == "pipeline_compressor" else "PipelineMetering"


def _to_opcua(record: Dict[str, Any]) -> Dict[str, Any]:
    dt = record["device_type"]
    table = _OPCUA_COMPRESSOR if dt == "pipeline_compressor" else _OPCUA_METERING

    q = record.get("quality", "GOOD")
    base_code = _OPCUA_STATUS[q] if q in _OPCUA_STATUS else _OPCUA_STATUS["GOOD"]
    bad_code = _OPCUA_STATUS["BAD"]

    prefix = f"ns=2;s={_ua_type_name(dt)}/{record['device_id']}"
    source_ts = record.get("source_timestamp", record.get("timestamp"))
    server_ts = record.get("received_timestamp", record.get("timestamp"))

    points: List[Dict[str, Any]] = []
    for browse_name, field, ua_type, units in table:
        val = record.get(field)
        status_code = bad_code if val is None else base_code
        points.append({
            "node_id": f"{prefix}/{browse_name}",
            "browse_name": browse_name,
            "data_type": ua_type,
            "value": val,
            "units": units,
            "status_code": status_code,
            "status_code_hex": _status_hex(status_code),
            "source_timestamp": source_ts,
            "server_timestamp": server_ts,
        })

    return {
        "timestamp": record["timestamp"],
        "device_id": record["device_id"],
        "device_type": dt,
        "protocol": "opc-ua",
        "namespace_uri": "urn:utility-iot-observability:pipeline",
        "namespace_index": 2,
        "quality": q,
        "native_quality": base_code,
        "native_quality_hex": _status_hex(base_code),
        "points": points,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
_DISPATCH = {
    "dlms-cosem": _to_dlms,
    "iec-61850": _to_iec61850,
    "dnp3": _to_dnp3,
    "opc-ua": _to_opcua,
}


def to_protocol_view(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the protocol-native envelope for a canonical telemetry record,
    or None if the device_type has no mapping.
    """
    dt = record.get("device_type")
    proto = DEVICE_PROTOCOL.get(dt or "")
    if proto is None:
        return None
    envelope = _DISPATCH[proto](record)
    # Pass scenario_ids through unchanged. Only include the field when the
    # canonical record had it — preserves off-mode wire-compat.
    if "scenario_ids" in record:
        envelope["scenario_ids"] = list(record["scenario_ids"])
    return envelope


__all__ = [
    "DEVICE_PROTOCOL",
    "to_protocol_view",
]
