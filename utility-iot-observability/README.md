# Utility IoT Observability — End-to-End Azure Reference Implementation

**Research & Synthetic Data Package for Oil, Gas & Utility Industry Monitoring**

Complete observability solution for electric utilities, natural gas distribution, and oil/gas pipelines deployed in NY State on Azure.

> ⚠️ reference package — This repo contains utility-industry reference architectures and synthetic telemetry. Synthetic data only.

## 📦 What's Included

### Research Documents
- **`2026-04-15-utility-iot-device-telemetry.md`** — Deep research on 25+ device types, industry standards (IEC 61850, DLMS/COSEM, DNP3), data schemas
- **`UTILITY_IOT_RESEARCH_SUMMARY.md`** — Executive summary of all deliverables

### Synthetic Data Generator (Tier 5: DTDL asset hierarchy + DER scenario)
- **`generate_synthetic_data.py`** + **`scenarios.py`** + **`protocols.py`** + **`bad_data.py`** + **`twins.py`** — Tier 1 generator, Tier 2 scenario engine, Tier 3 protocol-view transformers, Tier 4 dirty-data realism layer, and Tier 5 DTDL asset-hierarchy emitter.
  - **Stateful AR(1) walks** per device (slow thermal drift vs. stiff voltage/frequency), not i.i.d. randomness
  - **Monotonic cumulative registers** (kWh_delivered_cumulative, volume_ccf_cumulative, volume_mcf_cumulative) alongside interval deltas — mirrors real AMI/MCF meter reads
  - **Derived cross-signals**: transformer `oil_temp_top_c` follows a thermal model driven by `load_pct`; smart-meter `current_a` is derived from demand/voltage/PF
  - **Quality codes** on every record: `GOOD` / `UNCERTAIN` / `STALE` / `BAD` / **`MAINT`** (Tier 4 maintenance override) — OPC-UA/IEC 61850 style
  - **Scenario engine** (Tier 2): pre-scheduled, labeled failure scenarios — `storm.outage_cluster`, `transformer.incipient_dga`, `regulator.hunting`, `ami.tamper_campaign`, `compressor.degradation`, and Tier 5 **`der.reverse_power_flow`** (solar-DER export pushes `demand_kw` negative, biases feeder voltage up, reduces upstream transformer load). Telemetry records and caused events get `scenario_ids` tags; `scenarios_<ts>.jsonl` stores ground truth.
  - **Protocol-aware views** (Tier 3): every canonical telemetry record is reprojected to a protocol-native envelope in a sixth output file `telemetry_protocol_<ts>.jsonl`. One protocol per device_type: AMI/gas meters → **DLMS/COSEM** (OBIS codes), transformers → **IEC 61850** (logical nodes + quality bitstring), regulator/CP/valve → **DNP3** (groups, indices, flags byte), compressor/metering → **OPC UA** (NodeId + StatusCode). Pure function of canonical telemetry — deterministic, no RNG.
  - **Dirty-data realism layer** (Tier 4): after scenarios, a pipeline applies clock skew/drift, packet loss, delivery delay, flatlining, sensor calibration drift, and scheduled maintenance windows. Telemetry JSONL is sorted by `received_timestamp` to simulate arrival-order delivery. Dirty records flow through Tier 3 so protocol views reflect the same anomalies.
  - **Asset hierarchy + DTDL twins** (Tier 5): seventh output file `asset_hierarchy_<ts>.json` emits DTDL v3 interfaces, twin instances, and relationships (`contains`/`feeds`/`regulates`) rolled up as Utility → Region → Substation → Transformer → Meter (electric) and Region → Regulator → Gas Meter / Pipeline Segment → Compressor/Valve/Metering (gas). Pure function of the inventory — no RNG, no telemetry dependency.
  - **Seven output streams**: telemetry JSONL+CSV, events JSONL (now includes `maintenance.window_started/ended` and `ami.reverse_flow_detected`), audit JSONL (now includes `bad_data_applied` summary), **scenarios JSONL** (Tier 2 ground truth), **protocol JSONL** (Tier 3 OT-native views), **asset_hierarchy JSON** (Tier 5 DTDL document)
  - **Deterministic**: `--seed N` → byte-identical telemetry, events, audit, scenarios, protocol, and hierarchy outputs across all dirty-data modes. `run_id` and filename stamp are derived from seed (not wall-clock) when a seed is supplied.
  - Outputs: `utility_telemetry_<ts>.jsonl/.csv`, `events_<ts>.jsonl`, `audit_<ts>.jsonl`, `scenarios_<ts>.jsonl`, `telemetry_protocol_<ts>.jsonl`, `metadata_<ts>.json` (shared timestamp)

#### Scenario catalogue

| scenario_type | primary assets | mechanism |
|---|---|---|
| `storm.outage_cluster` | transformers + meters in one region | voltage sag, meter `outage_flag`, frequency dip; `ami.last_gasp` / `ami.power_restored` / `xfmr.tap_change` events tagged |
| `transformer.incipient_dga` | one transformer, multi-hour | slow H₂ / CH₄ / C₂H₂ ramp, oil-temp bias |
| `regulator.hunting` | one gas regulator | `outlet_pressure_psig` oscillates; relief valve cycles |
| `ami.tamper_campaign` | cluster of adjacent smart meters | `tamper_flag=true` + demand drop |
| `compressor.degradation` | one pipeline compressor | slow vibration + discharge-temp ramp |

#### Protocol-view mapping

| device_type | protocol | key fields |
|---|---|---|
| `smart_meter`, `gas_meter` | `dlms-cosem` | `obis_code`, `logical_name`, `class_id`, `value`, `unit`, `scaler`, `access_result` |
| `transformer` | `iec-61850` | `data_ref` (`XFMR1/YPTR1.TmpOil.mag.f` etc.), `ln_class`, `do_name`, `da_name`, `quality` bitstring |
| `gas_regulator`, `cathodic_protection`, `pipeline_valve` | `dnp3` | `group` / `variation` / `index` / `value` / `flags` byte (ONLINE, COMM_LOST, REMOTE_FORCED, REFERENCE_ERR) |
| `pipeline_compressor`, `pipeline_metering` | `opc-ua` | `node_id` (`ns=2;s=…`), `browse_name`, `data_type`, `status_code` (Good/Uncertain/Bad) |

Quality codes round-trip: canonical `GOOD/UNCERTAIN/STALE/BAD` map to DLMS `access-result` (0/1/2/3), IEC 61850 validity+detailQual bits, DNP3 flag byte (0x01 / 0x41 / 0x09 / 0x04), and OPC UA StatusCode (0x00000000 / 0x40000000 / 0x40A40000 / 0x80000000).

#### CLI flags (Tier 2/3/4 additions)

- `--scenarios {off,default,heavy}` (default `default`) — scenario intensity. `off` disables scheduling and omits the `scenario_ids` field entirely for Tier 1 wire-compat.
- `--scenario-config PATH` — explicit JSON schedule, overrides `--scenarios`.
- `--protocol-views {off,emit}` (default `emit`) — emit the sixth `telemetry_protocol_<ts>.jsonl` file. Toggling this flag does not perturb any other output file's bytes.
- `--bad-data {off,realistic,heavy}` (default `realistic`) — dirty-data realism intensity. `off` produces clean Tier 1-3 output (telemetry/events/scenarios byte-identical to pre-Tier-4 seeded runs). `realistic` applies conservative rates (0.2% loss, 1% delay, 2s clock skew stddev, etc.); `heavy` applies aggressive rates (2% loss, 5% delay, 10s skew, etc.) for anomaly-detection demos.
- `--bad-data-config PATH` — JSON file with per-dimension overrides (keys match `BadDataConfig` fields: `clock_skew_stddev_s`, `loss_rate`, `delay_rate`, `delay_median_s`, `flatline_per_device_per_day`, `drift_stddev`, `maint_windows_max`).
- `--asset-hierarchy {off,emit}` (default `emit`) — emit the seventh `asset_hierarchy_<ts>.json` file containing DTDL v3 interfaces, twin instances, and relationships. Pure function of the inventory; toggling does not perturb any other output file's bytes.

#### Dirty-data dimensions (Tier 4)

| dimension | mechanism | signal |
|---|---|---|
| clock skew/drift | per-device persistent offset (gaussian) + linear drift (ppm) | `source_timestamp` shifts ± seconds; `received_timestamp` unchanged |
| packet loss | per-device Bernoulli drop | records missing entirely from canonical/protocol streams |
| delayed delivery | per-device lognormal delay injected into `received_timestamp` | canonical JSONL sorted by `received_timestamp` — arrival order |
| flatlining | per-device rare event holds primary field constant for 3-12 intervals | quality stays `GOOD` — the sensor silently lies |
| calibration drift | per-device persistent additive bias on primary field | slow offset visible across the whole run |
| maintenance windows | scheduled (device × interval) overrides | `quality="MAINT"`; `maintenance.window_started/ended` events |

`quality="MAINT"` round-trips to DLMS access-result=3, IEC 61850 `validity=questionable`+`operatorBlocked=true`+`source=substituted`, DNP3 flags=`0x11` (ONLINE | LOCAL_FORCED), and OPC UA StatusCode `0x40910000` (Uncertain_SubstituteValue).

#### `scenario_ids` tagging semantics

- On a telemetry record, `scenario_ids` lists the scenarios **active on that device** at that sample.
- On an event, `scenario_ids` lists the scenarios whose handler **declared that event type as caused** (not just scenarios active at the time).
- Protocol-view records pass `scenario_ids` through unchanged from the canonical record.
- In `--scenarios off`, the field is omitted from canonical and protocol records alike to preserve Tier 1 wire schema.

### Pre-Generated Datasets
- **`utility_telemetry_20260415_214157.jsonl`** — 2,304 records, ready to stream to Azure
- **`utility_telemetry_20260415_214157.csv`** — Human-readable spreadsheet format
- **`metadata_20260415_214157.json`** — Dataset statistics

### Azure Deployment Blueprint
- **`AZURE_DEPLOYMENT_GUIDE.md`** — Complete infrastructure roadmap
  - Bicep templates
  - 3 ingestion patterns (bulk, streaming, edge)
  - Stream Analytics configuration
  - ADX KQL queries for dashboards
  - Cost estimation ($970/month basic, $3–5K scaled)
  - Monitoring & alerting rules

## 🏗️ Device Coverage

**Electric Utilities (6 devices):**
- Smart meters (voltage, current, power factor, tamper/outage flags)
- Distribution transformers (DGA, oil temperature, load, tap position)
- Substations (voltage regulation, breaker status, frequency)

**Natural Gas (3 devices):**
- Smart gas meters (volume, pressure, temperature, leak alarms)
- Pressure regulators (inlet/outlet, flow rate, relief valve status)
- Cathodic protection stations (pipe-to-soil potential, rectifier status)

**Oil/Gas Pipelines (3 devices):**
- Compressor stations (pressure, temperature, flow, vibration, H₂S)
- Valve stations (pressure, valve position, leak detection)
- Metering stations (flow measurement, pressure ratio, volume)

## 🌍 Geographic Scope

NY State utility territory (fictional Empire State Electric & Gas + Northeast Pipeline Partners):
- **Locations:** Albany, Poughkeepsie, Kingston, Binghamton, Newburgh, Port Jervis
- **Realistic coordinates:** All lat/lon verified against NY State geography

## 🚀 Quick Start

### 1. Review the Data
```bash
# Open in Excel
excel utility_telemetry_20260415_214157.csv

# Or inspect JSONL
head -3 utility_telemetry_20260415_214157.jsonl
```

### 2. Generate More Data
```bash
# Backward-compatible positional form (24h, default inventory, random seed)
python generate_synthetic_data.py .

# Tier 1 CLI (recommended) — reproducible, custom horizon and start time
python generate_synthetic_data.py \
  --output-dir ./out \
  --hours 72 \
  --seed 42 \
  --start-time 2026-04-15T00:00:00Z

# Optional flags:
#   --inventory path/to/inventory.json   (override embedded DEFAULT_LOCATIONS)
#   --good-quality-prob 0.985            (tune GOOD rate; rest is UNCERTAIN/STALE/BAD)
```
Each run produces four files with a shared timestamp:
`utility_telemetry_<ts>.jsonl`, `utility_telemetry_<ts>.csv`, `events_<ts>.jsonl`, `audit_<ts>.jsonl`, and `metadata_<ts>.json`.

### 3. Deploy to Azure
```bash
python upload_to_azure.py \
  --mode iothub \
  --connection-string "HostName=youriot.azure-devices.net;..." \
  --data-file utility_telemetry_20260415_214157.jsonl
```

See `AZURE_DEPLOYMENT_GUIDE.md` for full infrastructure setup.

## 📊 Research Highlights

- **48+ authoritative sources** analyzed (IEC, ANSI, API standards + vendor docs)
- **DGA (Dissolved Gas Analysis)** identified as #1 transformer fault indicator
- **SCADA protocols:** DNP3 still dominant in North America
- **Real-world patterns:** Validated against utility industry standards
- **Calibration sources identified:** NREL load profiles, Pecan Street, PJM, and EIA can ground synthetic patterns in public data
- **Realistic bad-data patterns:** clock drift, packet loss, stale values, out-of-order events, and maintenance windows should be first-class demo features
- **Canonical modeling path:** CIM, IEC 61850, DLMS/COSEM, DNP3, OPC UA, and DTDL provide a stronger target model than flat JSON alone
- **Azure architecture:** ADX + Event Hub remains strong for cloud demos, with Azure IoT Operations adding a modern edge-native path

## 🎯 Use Cases

✅ **Proof of Concept** — Show stakeholders the full end-to-end pipeline (2–3 weeks)  
✅ **Development & Testing** — Build + test Stream Analytics, ADX dashboards, alert rules  
✅ **Customer Demos** — Live observability with real-looking data  
✅ **Migration Baseline** — Ready for real utility data on day 1  
✅ **Alert Tuning & Analytics Validation** — Test KQL anomalies, quality filters, and dashboard behavior against labeled scenarios  
✅ **Digital Twin / OT-IT Storytelling** — Show how field protocols normalize into Azure-ready asset graphs and observability flows  

## 📁 File Structure

```
utility-iot-observability/
├── README.md                                    (this file)
├── generate_synthetic_data.py                   (data generator)
├── upload_to_azure.py                           (Azure uploader)
├── AZURE_DEPLOYMENT_GUIDE.md                    (infrastructure blueprint)
├── 2026-04-15-utility-iot-device-telemetry.md  (device research)
├── UTILITY_IOT_RESEARCH_SUMMARY.md              (executive summary)
├── utility_telemetry_20260415_214157.jsonl     (2,304 records)
├── utility_telemetry_20260415_214157.csv       (spreadsheet view)
└── metadata_20260415_214157.json                (statistics)
```

## 💡 Key Insights

1. **Device diversity** — Utilities manage 25+ distinct sensor types; no one-size-fits-all
2. **Standards matter** — IEC 61850, DLMS/COSEM, DNP3 are non-negotiable
3. **Calibration beats pure randomness** — Public load and pipeline datasets make synthetic profiles more defensible
4. **Correlation matters** — Good demo data should behave like a system, not isolated sensors
5. **Messy data is essential** — Packet loss, drift, jitter, and bad quality flags make demos more credible
6. **Scenario packs add the most value** — Storms, DGA degradation, regulator instability, and tamper events create better demos than generic streams
7. **Azure is ready** — ADX + Event Hub handles cloud analytics, while Azure IoT Operations strengthens edge-to-cloud observability stories

## 🔭 Recommended V2 Extensions

The strongest next step is to evolve this package from a flat synthetic telemetry generator into a **scenario-driven utility observability lab**.

Recommended upgrades:
- **Asset hierarchy + twin graph** — Model utility, region, substation, feeder, station, asset, and sensor relationships using DTDL/CIM-aligned structures
- **Scenario engine** — Generate labeled events such as storms, incipient transformer faults, gas pressure instability, pipeline compressor degradation, and AMI tamper cases
- **Protocol-aware views** — Preserve business-friendly JSON while adding IEC 61850, DLMS/COSEM, DNP3, and OPC UA-friendly mappings
- **Data quality realism** — Inject quality flags, latency, missing samples, sequence resets, stale reads, and maintenance states
- **Calibration profiles** — Tune electric and gas behavior using NREL, Pecan Street, PJM, and EIA reference datasets
- **Azure outputs** — Emit raw telemetry, enriched telemetry, anomaly labels, asset registry data, DTDL models, and ADX/KQL demo assets

## 🔍 What's NOT Included

❌ Real utility data (for obvious reasons)  
❌ ML anomaly detection (out of scope)  
❌ Custom Grafana dashboards (template provided in AZURE_DEPLOYMENT_GUIDE.md)  
❌ Compliance/regulatory frameworks (reference only)  
❌ Scenario engine (labeled storms/DGA/tamper campaigns), twin graph seed, and protocol-specific payload outputs (Tier 2+ additions)
❌ Event Hub upload path — `upload_to_azure.py` advertises `--mode eventhub` in `choices=` but only `iothub` and `cosmosdb` are implemented today

## 📞 Questions?

See the detailed research documents:
- Device types & schemas → `2026-04-15-utility-iot-device-telemetry.md`
- Azure architecture → `AZURE_DEPLOYMENT_GUIDE.md`
- Data generation → `generate_synthetic_data.py` (well-commented)

## 📄 License

MIT (see LICENSE file)

---

**Generated:** 2026-04-15  
**Research cost:** $0.042 (Exa API)  
**Status:** Strong baseline with clear V2 expansion path ✅
**Access:** Private (synthetic data only)
