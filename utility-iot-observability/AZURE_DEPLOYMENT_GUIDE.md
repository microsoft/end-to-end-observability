# Azure End-to-End Observability Platform — Deployment Guide

*For: Oil, Gas & Utility Industry IoT Data Ingestion*  
*Synthetic data generated: 2,304 realistic telemetry records (24-hour period)*  
*Deployment region: East US 2 (covering NY State)*

---

## Quick Start (5 Steps)

### 1. Create Azure Resource Group
```bash
az group create \
  --name rg-utility-observability \
  --location eastus2
```

### 2. Deploy IoT Infrastructure (Bicep)
See `infrastructure.bicep` in this directory. Deploys:
- Azure IoT Hub (Standard tier for ~50K devices)
- Event Hub (for buffering high-speed SCADA data)
- Data Explorer cluster (ADX for time-series analytics)
- ADLS Gen2 + containers (cold storage)
- Cosmos DB (device metadata registry)
- Azure Digital Twins (device models)

```bash
az deployment group create \
  --resource-group rg-utility-observability \
  --template-file infrastructure.bicep \
  --parameters environment=dev eventHubThroughputUnits=4
```

### 3. Ingest Synthetic Data
```bash
# Convert JSONL to IoT Hub device messages
python3 ingest_to_iot_hub.py \
  --iothub-connection-string "HostName=...;SharedAccessKeyName=...;SharedAccessKey=..." \
  --data-file utility_telemetry_20260415_214157.jsonl \
  --batch-size 100 \
  --threads 4
```

### 4. Configure Data Pipelines
- **Stream Analytics job** ingests from IoT Hub → ADX (real-time) + ADLS Gen2 (archival)
- **Azure Functions** trigger on anomalies (transformer DGA, meter tampers, pipeline leaks)
- **Log Analytics** + **Monitor** collect diagnostic logs

### 5. Build Dashboards
- **Grafana** queries ADX for real-time metrics (transformer load, voltage, DGA trends)
- **Power BI** connects to ADX for business analytics (outages, demand forecasting)
- **Azure Monitor Workbooks** for operational status

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     IoT Devices (NY State)                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Smart Meters    │  │ Transformers    │  │ Gas Regulators  │  │
│  │ (6 devices)     │  │ (5 devices)     │  │ (3 devices)     │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                     │            │
└───────────┼────────────────────┼─────────────────────┼────────────┘
            │                    │                     │
            └────────────────────┼─────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Azure IoT Hub         │
                    │ (D2C telemetry ingestion)
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Event Hub             │
                    │ (buffering, routing)    │
                    └──┬──────────────┬───────┘
                       │              │
         ┌─────────────▼──┐   ┌──────▼──────────────┐
         │  Stream        │   │ Azure Digital Twins │
         │  Analytics     │   │ (device models)     │
         │  (processing)  │   └─────────────────────┘
         └─────────────────┘
                │
    ┌───────────┼──────────────┐
    │           │              │
  ┌─▼──────┐ ┌──▼────────┐ ┌──▼─────────┐
  │ ADX    │ │ ADLS Gen2 │ │ Cosmos DB   │
  │ (hot)  │ │ (archive) │ │ (metadata)  │
  └────┬───┘ └───────────┘ └─────────────┘
       │
  ┌────▼──────────────┐
  │ Grafana/Power BI  │
  │ Dashboards        │
  └───────────────────┘
```

---

## Data Ingestion — Three Patterns

### Pattern 1: Bulk Historical Load (One-time)
For loading 24 hours of synthetic data to establish baseline:

```python
from azure.iot.hub import IoTHubRegistryManager
from azure.iot.hub.models import Message
import json
from datetime import datetime, timedelta

# Read JSONL file
with open('utility_telemetry_20260415_214157.jsonl') as f:
    for line in f:
        payload = json.loads(line)
        msg = Message(json.dumps(payload))
        msg.properties.add("timestamp", payload["timestamp"])
        msg.properties.add("device_type", payload["device_type"])
        
        # Send to IoT Hub (will route via Stream Analytics to ADX/ADLS)
        registry_manager.send_d2c_message(payload["device_id"], msg)
```

### Pattern 2: Streaming Ingestion (Continuous)
Deploy a device simulator container that continuously generates new data:

```yaml
# deploy_simulator.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: utility-data-simulator
spec:
  schedule: "*/5 * * * *"  # Every 5 minutes
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: simulator
            image: mcr.microsoft.com/azure-cli:latest
            command: ["python", "generate_synthetic_data.py", "/output"]
            env:
            - name: IOTHUB_CONNECTION_STRING
              valueFrom:
                secretKeyRef:
                  name: iot-credentials
                  key: connection-string
            volumeMounts:
            - name: output
              mountPath: /output
          volumes:
          - name: output
            emptyDir: {}
```

### Pattern 3: Edge-to-Cloud via IoT Edge
Deploy an IoT Edge device that preprocesses, filters, and aggregates before sending to cloud:

```json
{
  "runtime": {
    "type": "docker",
    "settings": {
      "minDockerVersion": "19.03"
    }
  },
  "systemModules": {
    "edgeAgent": {
      "type": "docker",
      "settings": {
        "image": "mcr.microsoft.com/azureiotedge-agent:latest"
      }
    },
    "edgeHub": {
      "type": "docker",
      "settings": {
        "image": "mcr.microsoft.com/azureiotedge-hub:latest"
      }
    }
  },
  "modules": {
    "utility-data-processor": {
      "version": "1.0",
      "type": "docker",
      "settings": {
        "image": "myacr.azurecr.io/utility-processor:latest"
      },
      "env": {
        "DataPath": "/data",
        "AggregationInterval": "300"  // 5 minutes
      },
      "createOptions": "{\"HostConfig\": {\"Mounts\": [{\"Source\": \"utility-data\", \"Target\": \"/data\", \"Type\": \"volume\"}]}}"
    }
  },
  "routes": {
    "telemetryToIoTHub": "FROM /messages/* INTO $upstream",
    "alertsToCloud": "FROM /messages/alerts INTO $upstream"
  }
}
```

---

## Azure Data Explorer (ADX) — KQL Queries

### Real-time Dashboard Queries

**Transformer DGA Trending:**
```kusto
utility_telemetry
| where device_type == "transformer" and dga_h2_ppm > 0
| extend hour = bin(timestamp, 1h)
| summarize 
    avg_h2 = avg(dga_h2_ppm),
    avg_ch4 = avg(dga_ch4_ppm),
    max_c2h2 = max(dga_c2h2_ppm),
    fault_count = countif(fault_detected == true)
  by device_id, hour
| render timechart
```

**Substation Voltage Violations:**
```kusto
utility_telemetry
| where device_type == "smart_meter"
| extend voltage_ok = voltage_a >= 114 and voltage_a <= 126
| where not(voltage_ok)
| summarize violation_count = count() by device_id, bin(timestamp, 5m)
| render barchart
```

**Gas Pipeline Pressure Anomalies:**
```kusto
utility_telemetry
| where device_type startswith "pipeline_"
| project device_id, timestamp, pressure_psig = pressure_psig
| extend moving_avg = series_fir(pressure_psig, dynamic([0.2, 0.2, 0.2, 0.2, 0.2]))
| extend anomaly_score = abs(pressure_psig - moving_avg) / moving_avg
| where anomaly_score > 0.15  // 15% deviation
| summarize anomalies = count() by device_id
```

---

## Stream Analytics Job — Configuration

**Input:** Azure IoT Hub / Event Hub  
**Output 1:** ADX (real-time)  
**Output 2:** ADLS Gen2 (Parquet, 1-hour partitions)  
**Output 3:** Azure Queue (for anomaly alerts)

```sql
-- Transform and split streams
WITH processed AS (
  SELECT
    device_type,
    device_id,
    timestamp,
    CAST(EventProcessedUtcTime AS datetime) AS processed_time,
    *
  FROM iothub
  WHERE device_type IS NOT NULL
)

-- Stream 1: Hot path to ADX (all data)
SELECT * INTO adx_output FROM processed;

-- Stream 2: Cold path to ADLS (archive)
SELECT
  device_type,
  device_id,
  DATETIMEFROMPARTS(
    YEAR(timestamp), MONTH(timestamp), DAY(timestamp),
    DATEPART(hour, timestamp), 0, 0, 0
  ) AS hour_bucket,
  *
INTO adls_output FROM processed;

-- Stream 3: Anomaly detection (alerts)
SELECT
  device_id,
  device_type,
  'High DGA H2' AS alert_type,
  dga_h2_ppm AS metric_value,
  timestamp
INTO alerts_output FROM processed
WHERE device_type = 'transformer'
  AND dga_h2_ppm > 400;

SELECT
  device_id,
  device_type,
  'Voltage out of spec' AS alert_type,
  voltage_a AS metric_value,
  timestamp
INTO alerts_output FROM processed
WHERE device_type = 'smart_meter'
  AND (voltage_a < 114 OR voltage_a > 126);
```

---

## Cosmos DB Device Registry Schema

```json
{
  "id": "TX_ALB_001",
  "type": "transformer",
  "assetType": "distribution_transformer",
  "location": {
    "substation": "SS_ALB_001",
    "latitude": 42.729,
    "longitude": -73.79
  },
  "specs": {
    "rating_kva": 75,
    "voltage_primary_kv": 13.8,
    "voltage_secondary_v": 120,
    "installation_date": "2018-05-15",
    "manufacturer": "ABB",
    "model": "PTW25"
  },
  "maintenance": {
    "last_dga_sample": "2026-03-20",
    "next_inspection": "2026-06-20",
    "condition_code": "G (Good)"
  },
  "alerts": [
    {
      "type": "dga_trending_up",
      "severity": "warning",
      "timestamp": "2026-04-15T21:30:00Z"
    }
  ],
  "_etag": "...",
  "_timestamp": 1776288000
}
```

---

## Monitoring & Alerting

### Azure Monitor Alert Rules

**Rule 1: Transformer Fault Detection**
```json
{
  "name": "TransformerDGAFault",
  "condition": {
    "allOf": [
      {
        "query": "utility_telemetry | where device_type == 'transformer' and (dga_h2_ppm > 400 or dga_c2h2_ppm > 20)",
        "timeAggregation": "Average",
        "operator": "GreaterThan",
        "threshold": 0
      }
    ]
  },
  "actions": {
    "actionGroups": ["critical-opex-team"],
    "webhooks": ["https://teams.microsoft.com/webhooks/..."]
  }
}
```

**Rule 2: Meter Tamper Detection**
```json
{
  "name": "MeterTamperAlert",
  "condition": {
    "allOf": [
      {
        "query": "utility_telemetry | where device_type == 'smart_meter' and tamper_flag == true",
        "operator": "GreaterThan",
        "threshold": 0
      }
    ]
  },
  "actions": {
    "actionGroups": ["field-ops", "security-team"],
    "emails": ["security@eseg.local"]
  }
}
```

---

## Cost Estimation (Monthly, NY Territory)

| Service | Usage | Est. Cost |
|---------|-------|-----------|
| **IoT Hub** | 6 devices, 2M msg/mo | $10 |
| **Event Hub** | 1 CU, 8GB throughput | $87 |
| **ADX** | 1 node (D13_v2) | $650 |
| **ADLS Gen2** | 100 GB stored + operations | $35 |
| **Cosmos DB** | Serverless, 5K RU | $20 |
| **Stream Analytics** | 1 SU, 24/7 | $30 |
| **Digital Twins** | 10K queries/day | $8 |
| **Monitor + Logs** | 10 GB ingest/day | $80 |
| **Grafana** (optional) | Managed, 10 users | $50 |
| **Total** | | **~$970/month** |

*Note: Scales to 50K devices with ADX cluster expansion + higher Event Hub tier (~$3-5K/month).*

---

## Next Steps

1. **Deploy infrastructure** using Bicep template
2. **Bulk load** 24 hours of synthetic data
3. **Configure Stream Analytics** pipelines
4. **Build Grafana dashboards** for visualization
5. **Set up anomaly detection** rules
6. **Test alerting** with intentional faults
7. **Plan retention policy** (7+ years for compliance)
8. **Implement data masking** for PII/meters

---

## Useful Commands

```bash
# Check IoT Hub device count
az iot hub device-identity list \
  --hub-name utility-iothub \
  --resource-group rg-utility-observability \
  --output table

# Monitor Stream Analytics job
az stream-analytics job get \
  --job-name utility-processor \
  --resource-group rg-utility-observability \
  --query "properties.{State:state, Errors:diagnostics.runtimeSubErrors}"

# Query ADX (via CLI)
az kusto query \
  --cluster-name utility-adx \
  --database-name operational \
  --kql "utility_telemetry | count"

# Archive ADLS data to cold storage
azcopy copy "https://utilitydata.dfs.core.windows.net/raw/*" \
  "https://utilityarchive.blob.core.windows.net/archive/" \
  --recursive --as-subdir=true
```

---

**Questions?** See the main research document: `2026-04-15-utility-iot-device-telemetry.md`
