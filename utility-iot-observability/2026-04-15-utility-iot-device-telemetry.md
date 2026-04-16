# Oil, Gas & Utility IoT Observability — Device & Telemetry Research

*Research compiled 2026-04-15 by azbot ⚡ | 6 Exa neural searches, 48+ sources | Cost: ~$0.042*

---

## Executive Summary

Utilities and O&G companies monitor hundreds of device types across their infrastructure. This document catalogs the **major device categories**, the **telemetry data they generate**, realistic **data field schemas**, and an **Azure architecture** for ingesting and observing it all. The synthetic data generators accompanying this document produce realistic fake data for a fictional NY State utility serving the greater NYC/Hudson Valley/Capital Region area.

---

## 1. Electric Utility — Device Inventory

### 1.1 Smart Meters (AMI — Advanced Metering Infrastructure)
**What:** Digital electric meters at every customer premise that report usage, voltage, power quality, and events over a wireless network (e.g., Sensus FlexNet, Itron OpenWay, Landis+Gyr Gridstream).

**Telemetry fields:**
| Field | Type | Unit | Typical Range | Frequency |
|-------|------|------|---------------|-----------|
| `meter_id` | string | — | Unique ID | — |
| `timestamp` | ISO 8601 | — | — | 15-min intervals |
| `kWh_delivered` | float | kWh | 0–50 per interval | 15 min |
| `kWh_received` | float | kWh | 0–20 (solar homes) | 15 min |
| `voltage_a` | float | V | 114–126 (120V nominal) | 15 min |
| `voltage_b` | float | V | 114–126 | 15 min (split-phase) |
| `voltage_c` | float | V | 114–126 | 15 min (3-phase) |
| `current_a` | float | A | 0–200 | 15 min |
| `power_factor` | float | — | 0.80–1.00 | 15 min |
| `frequency` | float | Hz | 59.95–60.05 | 15 min |
| `demand_kW` | float | kW | 0–50 | 15 min |
| `tamper_flag` | bool | — | — | On event |
| `outage_flag` | bool | — | — | On event |
| `rssi` | int | dBm | -120 to -40 | 15 min |

**Standards:** DLMS/COSEM (IEC 62056), ANSI C12.19, CIM IEC 61968-9

### 1.2 Distribution Transformers (Pole-mount & Pad-mount)
**What:** Step-down transformers (4kV–34.5kV → 120/240V) serving neighborhoods. Monitored via add-on sensors (Itron ATVM, GE Kelman, Hitachi TXpert).

**Telemetry fields:**
| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `transformer_id` | string | — | — | — |
| `timestamp` | ISO 8601 | — | — | 1–5 min |
| `oil_temp_top` | float | °C | 20–105 | 5 min |
| `oil_temp_bottom` | float | °C | 15–85 | 5 min |
| `ambient_temp` | float | °C | -20–40 | 5 min |
| `load_kw` | float | kW | 0–500 | 1 min |
| `load_pct` | float | % | 0–150 | 1 min |
| `voltage_primary` | float | kV | 3.8–36 | 1 min |
| `voltage_secondary` | float | V | 114–126 | 1 min |
| `current_a/b/c` | float | A | 0–1000 | 1 min |
| `tap_position` | int | — | 1–33 | On change |
| `dga_h2` | float | ppm | 0–500 | 1 hour |
| `dga_ch4` | float | ppm | 0–200 | 1 hour |
| `dga_c2h2` | float | ppm | 0–50 | 1 hour |
| `dga_c2h4` | float | ppm | 0–200 | 1 hour |
| `dga_c2h6` | float | ppm | 0–100 | 1 hour |
| `dga_co` | float | ppm | 0–1000 | 1 hour |
| `dga_co2` | float | ppm | 0–10000 | 1 hour |
| `moisture_ppm` | float | ppm | 5–50 | 1 hour |
| `bushing_cap_pf` | float | — | 0.3–0.7% | 1 hour |
| `partial_discharge` | float | pC | 0–500 | 1 hour |

**Key insight:** DGA (Dissolved Gas Analysis) is THE critical diagnostic for transformers. 9 key gases indicate different fault types (arcing, overheating, partial discharge).

### 1.3 Substation Equipment
**What:** Major substations (138kV/345kV → 13.8kV/34.5kV) with circuit breakers, capacitor banks, voltage regulators, relays, and RTUs.

**SCADA telemetry:**
| Field | Type | Description |
|-------|------|-------------|
| `substation_id` | string | Substation identifier |
| `breaker_status` | enum | OPEN/CLOSED/TRIP |
| `bus_voltage_kv` | float | Bus voltage |
| `bus_current_a` | float | Bus current |
| `real_power_mw` | float | Real power flow |
| `reactive_power_mvar` | float | Reactive power |
| `frequency_hz` | float | System frequency |
| `cap_bank_status` | enum | ON/OFF |
| `regulator_tap` | int | Voltage regulator tap position |
| `relay_alarm` | string | Protection relay event codes |
| `battery_voltage` | float | Station battery DC voltage |

**Protocols:** IEC 61850, DNP3, Modbus, IEC 60870-5-104

### 1.4 Feeder Line Sensors / Fault Indicators
**What:** Line-mounted sensors on distribution feeders for fault detection and load monitoring.

| Field | Type | Unit | Frequency |
|-------|------|------|-----------|
| `sensor_id` | string | — | — |
| `fault_detected` | bool | — | On event |
| `fault_current_a` | float | A | On event |
| `load_current_a` | float | A | 5 min |
| `conductor_temp` | float | °C | 5 min |

---

## 2. Natural Gas Utility — Device Inventory

### 2.1 Gas Smart Meters (AMI)
**What:** Digital gas meters with wireless reporting (Sensus FlexNet, Itron).

| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `meter_id` | string | — | — | — |
| `timestamp` | ISO 8601 | — | — | 1 hour |
| `volume_ccf` | float | CCF | 0–50 | 1 hour |
| `corrected_volume` | float | therms | 0–50 | 1 hour |
| `pressure_psig` | float | psig | 0.25–60 | 1 hour |
| `temperature_f` | float | °F | -10–120 | 1 hour |
| `battery_voltage` | float | V | 2.5–3.6 | Daily |
| `tamper_flag` | bool | — | — | On event |
| `leak_alarm` | bool | — | — | On event |

### 2.2 Pressure Regulation Stations (District Regulators)
**What:** Gate stations and district regulator stations that step down gas pressure from transmission (200–1000 psig) to distribution (0.25–60 psig).

| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `station_id` | string | — | — | — |
| `inlet_pressure` | float | psig | 50–1000 | 1 min |
| `outlet_pressure` | float | psig | 0.25–60 | 1 min |
| `flow_rate_mcfh` | float | MCF/h | 0–5000 | 1 min |
| `gas_temperature` | float | °F | 20–100 | 1 min |
| `regulator_position` | float | % open | 0–100 | 1 min |
| `relief_valve_status` | enum | NORMAL/OPEN | On change |
| `slam_shut_status` | enum | NORMAL/TRIPPED | On change |
| `odorizer_level` | float | % | 0–100 | Daily |

### 2.3 Cathodic Protection Stations
**What:** Protect underground steel pipelines from corrosion by maintaining a negative voltage potential.

| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `cp_station_id` | string | — | — | — |
| `pipe_to_soil_mv` | float | mV | -800 to -1500 | 1 hour |
| `rectifier_voltage` | float | V | 0–100 | 1 hour |
| `rectifier_current` | float | A | 0–50 | 1 hour |
| `ac_supply_voltage` | float | V | 110–130 | 1 hour |
| `rectifier_status` | enum | ON/OFF/FAULT | On change |

---

## 3. Oil & Gas (Upstream/Midstream) — Device Inventory

### 3.1 Pipeline SCADA
**What:** Long-distance pipelines with RTUs at pump stations, valve stations, and metering points.

| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `station_id` | string | — | — | — |
| `pipeline_id` | string | — | — | — |
| `pressure_psig` | float | psig | 0–1500 | 10 sec |
| `temperature_f` | float | °F | 20–150 | 10 sec |
| `flow_rate_bph` | float | bbl/hr | 0–50000 | 10 sec |
| `flow_rate_mcfh` | float | MCF/h | 0–100000 | 10 sec (gas) |
| `pump_status` | enum | RUN/STOP/FAULT | On change |
| `pump_discharge_psi` | float | psig | 0–2000 | 10 sec |
| `pump_suction_psi` | float | psig | 0–500 | 10 sec |
| `valve_position` | float | % | 0–100 | On change |
| `leak_detection_flag` | bool | — | — | On event |
| `pig_passage` | bool | — | — | On event |
| `vibration_mms` | float | mm/s | 0–25 | 1 min |
| `h2s_ppm` | float | ppm | 0–100 | 1 min |

### 3.2 Tank Farm Monitoring
**What:** Storage tanks for crude oil, refined products, natural gas liquids.

| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `tank_id` | string | — | — | — |
| `level_ft` | float | ft | 0–60 | 1 min |
| `level_pct` | float | % | 0–100 | 1 min |
| `temperature_f` | float | °F | 30–200 | 5 min |
| `pressure_psig` | float | psig | 0–15 (atmospheric) | 5 min |
| `product_type` | string | — | crude/gasoline/diesel/NGL | — |
| `overfill_alarm` | bool | — | — | On event |
| `leak_detection` | bool | — | — | On event |

### 3.3 Wellhead / Production Monitoring
**What:** Oil/gas wellheads with production sensors.

| Field | Type | Unit | Range | Frequency |
|-------|------|------|-------|-----------|
| `well_id` | string | — | — | — |
| `casing_pressure` | float | psig | 0–5000 | 1 min |
| `tubing_pressure` | float | psig | 0–5000 | 1 min |
| `flow_rate_bpd` | float | bbl/day | 0–10000 | 15 min |
| `gas_rate_mcfd` | float | MCF/day | 0–50000 | 15 min |
| `water_cut_pct` | float | % | 0–100 | 15 min |
| `choke_position` | float | 64ths inch | 0–64 | On change |
| `downhole_temp` | float | °F | 100–350 | 15 min |
| `downhole_pressure` | float | psig | 500–10000 | 15 min |
| `rod_pump_spm` | float | strokes/min | 0–15 | 1 min |
| `esp_frequency_hz` | float | Hz | 30–65 | 1 min |

---

## 4. Azure Landing Architecture

```
IoT Devices → IoT Hub/IoT Central → Event Hub → Azure Data Explorer (ADX)
                                        ↓
                                  Stream Analytics → ADLS Gen2 (Parquet archive)
                                        ↓
                                  Azure Digital Twins (device models)
                                        ↓
                                  Azure Monitor / Grafana / Power BI (dashboards)
```

**Recommended Azure services for this observability platform:**

| Service | Role | Why |
|---------|------|-----|
| **Azure IoT Hub** | Device ingestion | MQTT/AMQP, device identity, D2C telemetry |
| **Azure IoT Operations** | Edge data plane | OPC UA, MQTT broker, edge processing, and modern OT-to-cloud patterns |
| **Azure Event Hub** | Stream buffer | High-throughput event streaming, partitioned |
| **Azure Data Explorer (ADX)** | Hot analytics | Time-series native, KQL queries, sub-second, perfect for SCADA data |
| **ADLS Gen2** | Cold storage | Parquet archive for compliance (7+ years for utilities) |
| **Azure Digital Twins** | Device models | DTDL models for substations, transformers, meters |
| **Cosmos DB** | Device metadata | Asset registry, maintenance records |
| **Azure Monitor** | Observability | Alerts, dashboards, Log Analytics integration |
| **Grafana (managed)** | Dashboards | Real-time operational dashboards (ADX data source) |
| **Stream Analytics** | Processing | Real-time aggregation, anomaly detection |

---

## 5. NY State — Realistic Utility Geography

### Electric Utility Territory (fictional: "Empire State Electric & Gas" — ESEG)
- **Service territory:** Hudson Valley, Capital Region, Southern Tier
- **Substations:** 45 (scattered Albany to Poughkeepsie to Binghamton)
- **Distribution transformers:** ~12,000
- **Smart meters:** ~350,000
- **Feeders:** ~600

### Gas Utility Territory (same company, dual-utility)
- **Gate stations:** 8 (receiving from interstate pipelines)
- **District regulators:** 120
- **Gas meters:** ~180,000
- **Cathodic protection stations:** 85

### Oil/Gas Pipeline (fictional: "Northeast Pipeline Partners" — NEPP)
- **Pipeline:** 180 miles from Port Jervis to Albany (natural gas)
- **Compressor stations:** 3
- **Valve stations:** 12
- **Metering stations:** 6

### Key NY Locations for Synthetic Data
| Asset Type | Location | Lat | Lon |
|-----------|----------|-----|-----|
| Substation | Albany (Colonie) | 42.7284 | -73.7893 |
| Substation | Poughkeepsie | 41.7004 | -73.9209 |
| Substation | Kingston | 41.9270 | -74.0234 |
| Substation | Binghamton | 42.0987 | -75.9180 |
| Substation | Newburgh | 41.5034 | -74.0104 |
| Gate Station | Port Jervis | 41.3720 | -74.6929 |
| Gate Station | Corning | 42.1428 | -77.0547 |
| Compressor | Middletown | 41.4459 | -74.4224 |
| Tank Farm | Selkirk (Albany) | 42.5384 | -73.8569 |
| Wellhead cluster | Tioga County | 42.0300 | -76.3000 |

---

## 6. Recommended Evolution of the Demo Data Package

The current research and generator are a good baseline, but a stronger demo platform should move beyond flat synthetic telemetry into a **scenario-driven utility observability lab**.

### 6.1 Calibrate synthetic behavior using public baselines

Use public reference data to tune ranges, seasonality, and variability:
- **NREL End-Use Load Profiles / ResStock / ComStock** for residential and commercial electric demand shapes
- **Pecan Street** for high-resolution residential usage, solar, EV, and appliance variability
- **PJM Data Miner** for regional/system load behavior
- **EIA natural gas and pipeline data** for gas usage and transmission trend realism

This makes demo datasets easier to defend in front of utility and energy customers.

### 6.2 Add asset hierarchy and digital twin context

Model assets as a system, not just independent devices:

```text
Utility -> Region -> Substation / Gate Station / Pipeline Segment
Substation -> Feeder -> Transformer -> Smart Meter
Pipeline Segment -> Compressor / Valve / Metering Station -> Sensor
```

Use **DTDL** for twin definitions and align major entities to **CIM** where practical. A good demo should allow a user to pivot from telemetry to asset, feeder, station, or pipeline segment context.

### 6.3 Support protocol-aware views

Retain business-friendly JSON, but add alternate views aligned to real OT ecosystems:
- **IEC 61850**-style logical-node groupings for substation assets
- **DLMS/COSEM / OBIS-aware** mappings for AMI meter signals
- **DNP3** analog, binary, and counter points with event-oriented behavior
- **OPC UA-style** node paths for industrial and pipeline assets

This makes the package better for OT-to-IT normalization demos.

### 6.4 Generate realistic bad data

Real telemetry is rarely clean. Synthetic demo data should intentionally include:
- Timestamp drift and clock skew
- Packet loss and communication gaps
- Out-of-order or delayed events
- Stale repeated values / flatlining
- Sensor drift and calibration offsets
- Bad / uncertain quality flags
- Maintenance windows and manual override states

These features improve dashboard, ETL, alerting, and anomaly detection demos.

### 6.5 Add labeled scenario packs

Recommended scenarios:
- **Storm outage and restoration** for electric distribution
- **Transformer incipient fault** via rising DGA gases and thermal stress
- **AMI tamper / theft / reverse flow** cases
- **Gas regulator instability or freeze-off**
- **Pipeline compressor degradation** via vibration and pressure inefficiency
- **DER reverse power flow** causing feeder voltage excursions

Each scenario should emit:
- Telemetry stream
- Affected assets
- Ground-truth anomaly labels
- Expected alert conditions
- Suggested operator interpretation

### 6.6 Evolve the Azure reference path

Support two deployment patterns:

1. **Fast cloud demo:** IoT Hub or Event Hubs -> ADX -> Grafana / Power BI
2. **Modern industrial demo:** Azure IoT Operations -> Event Hubs / Kafka / MQTT -> ADX + Digital Twins + Managed Grafana

This keeps the package useful for both lightweight demos and modern industrial architecture conversations.

---

## Sources
- Databricks/Southern Company: AMI data from 4.6M smart meters
- IEC 61850, DLMS/COSEM, DNP3 protocol standards
- Oracle NMS SCADA documentation
- GE Vernova (Hydran M2, Kelman DGA 900), Hitachi TXpert
- Sensus FlexNet, Itron, Enetics GasPoint RTU specs
- NFM Consulting: Pipeline SCADA architecture
- Resensys, Yokogawa: Pipeline monitoring sensors
- Azure ADX IoT Analytics reference architecture
- Azure Digital Twins DTDL modeling guidance
- Azure IoT Operations overview and observability guidance
- ENTSO-E CIM exchange guidance
- NREL End-Use Load Profiles / ResStock / ComStock
- Pecan Street Dataport
- PJM Data Miner
- U.S. EIA natural gas and pipeline data
