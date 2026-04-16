# Utility IoT Observability — End-to-End Azure Reference Implementation

**Research & Synthetic Data Package for Oil, Gas & Utility Industry Monitoring**

Complete observability solution for electric utilities, natural gas distribution, and oil/gas pipelines deployed in NY State on Azure.

> ⚠️ reference package — This repo contains utility-industry reference architectures and synthetic telemetry. Synthetic data only.

## 📦 What's Included

### Research Documents
- **`2026-04-15-utility-iot-device-telemetry.md`** — Deep research on 25+ device types, industry standards (IEC 61850, DLMS/COSEM, DNP3), data schemas
- **`UTILITY_IOT_RESEARCH_SUMMARY.md`** — Executive summary of all deliverables

### Synthetic Data Generator
- **`generate_synthetic_data.py`** — Production-ready Python script
  - Generates realistic 24-hour (or longer) telemetry datasets
  - Time-of-day patterns (peak demand 5–9 PM)
  - Seasonal patterns (gas usage 5x in winter)
  - Device-specific fault signatures
  - Outputs: JSONL (Event Hub), CSV (Excel), metadata JSON

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
python generate_synthetic_data.py .
# Creates new JSONL + CSV with current timestamp
```

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
❌ Scenario engine, twin graph seed, and protocol-specific payload outputs (recommended next additions)

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
