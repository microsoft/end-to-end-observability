# End-to-End Observability

Enterprise Azure observability platform providing monitoring, alerting, and telemetry collection for Azure Landing Zone (ALZ) environments. Deploys and enforces observability standards at scale using Azure Policy, Terraform, and GitHub Actions.

---

## Architecture

The platform follows a hybrid deployment model: **Terraform** orchestrates Azure Policy deployment at the management group level, while **Azure Policy + remediation tasks** deploy and enforce monitoring resources across all subscriptions.

```
┌─────────────────────────────────────────────────────────────────┐
│                    ALZ Management Group Hierarchy               │
│                                                                 │
│  ┌──────────────────────┐    ┌──────────────────────────────┐   │
│  │   AMBA Enterprise    │    │   Monitoring Baselines        │   │
│  │   (Alerting)         │    │   (Telemetry Collection)      │   │
│  │                      │    │                               │   │
│  │  • Alert Rules       │    │  • AMA Deployment             │   │
│  │  • Action Groups     │    │  • Diagnostic Settings        │   │
│  │  • Alert Processing  │    │  • Private Endpoint Posture   │   │
│  └──────────┬───────────┘    └──────────────┬────────────────┘   │
│             │          Shared UAMI           │                   │
│             └──────────────┬─────────────────┘                   │
│                            ▼                                     │
│              ┌─────────────────────────┐                         │
│              │    Azure SRE Agent      │                         │
│              │  (AI-Assisted Ops)      │                         │
│              └─────────────────────────┘                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

| Directory | Purpose |
|---|---|
| [`amba-enterprise/`](amba-enterprise/) | Enterprise AMBA (Azure Monitor Baseline Alerts) — alerting infrastructure across ALZ |
| [`monitoring-baselines/`](monitoring-baselines/) | Telemetry collection & monitoring posture enforcement via built-in Azure Policy |
| [`sre-agent-pre-req/`](sre-agent-pre-req/) | Azure SRE Agent integration checklist and onboarding guide |
| [`utility-iot-observability/`](utility-iot-observability/) | Utility industry IoT observability reference implementation |
| [`test-data/`](test-data/) | Synthetic test data generator for workbook and dashboard validation |

---

## Modules

### AMBA Enterprise

Deploys alerting infrastructure (alert rules, action groups, alert processing rules) across an ALZ management group hierarchy using the official [AMBA framework](https://azure.github.io/azure-monitor-baseline-alerts/).

- **14 toggleable initiatives** covering Connectivity, Identity, Management, Service Health, Hybrid VM, Key Management, Load Balancing, Network Changes, Recovery Services, Storage, VM, VMSS, Web, and Notification Assets
- **42 Azure resource types** with metric, log, and activity log alerts
- Ring-based deployment promotion (sandbox → pre-prod → prod)
- Daily drift detection with auto-created GitHub issues
- Deployment budget monitoring (Azure 800-deployment limit)
- Brownfield subscription onboarding with preflight inventory scanning

See [`amba-enterprise/README.md`](amba-enterprise/README.md) for full details.

### Monitoring Baselines

Enforces monitoring baselines via a custom Azure Policy initiative built from **built-in** policy definitions. Deliberately separated from AMBA to decouple telemetry governance from the AMBA release lifecycle.

Three policy groups:
- **Azure Monitor Agent (AMA):** 6 DeployIfNotExists policies for Linux/Windows VMs, VMSS, and Arc machines
- **Diagnostic Settings:** 8 DeployIfNotExists policies routing logs for Key Vault, NSG, Storage, SQL, App Service, Event Hub, Service Bus, and Recovery Services
- **Private Endpoints:** 3 Audit/Deny policies for Storage, Key Vault, and Azure Monitor Private Link Scope

See [`monitoring-baselines/README.md`](monitoring-baselines/README.md) for full details.

### SRE Agent Integration

Comprehensive, Microsoft Learn-verified checklist for onboarding [Azure SRE Agent](https://learn.microsoft.com/en-us/azure/sre-agent/) into the enterprise observability stack. Covers:

- Pre-flight requirements (region, identity, networking, firewall allow-lists)
- Agent deployment and RBAC verification
- Run-mode configuration (Review vs Autonomous)
- Integrations: Azure Monitor, App Insights, PagerDuty, ServiceNow, GitHub, Azure DevOps, ADX
- Sample KQL queries for agent telemetry and cost tracking

See [`sre-agent-pre-req/sre-agent-integration-checklist.md`](sre-agent-pre-req/sre-agent-integration-checklist.md).

### Utility IoT Observability

Research and reference implementation for electric, gas, and oil/gas pipeline monitoring targeting NY State utilities. Includes:

- Synthetic data generator producing realistic 24-hour telemetry (2,304 records) with time-of-day patterns, seasonal variation, and device-specific fault signatures
- Multi-mode Azure uploader (IoT Hub, Event Hub, Cosmos DB)
- Deployment guide for IoT Hub, Event Hub, ADX, ADLS Gen2, Cosmos DB, Digital Twins, and Stream Analytics (see [`AZURE_DEPLOYMENT_GUIDE.md`](utility-iot-observability/AZURE_DEPLOYMENT_GUIDE.md))
- Coverage of 25+ device types across smart meters, distribution transformers, substations, gas metering, and pipeline systems

See [`utility-iot-observability/README.md`](utility-iot-observability/README.md).

### Test Data

Generates large-scale synthetic datasets (Azure Resource Graph-shaped) for testing workbook and Grafana dashboard visualizations without a live Azure tenant.

- 11 subscriptions across 3 environment rings
- Pre-generated output: 6,000 AMBA policy states, 6,000 monitoring baselines states, 3,000 alerts, 58 alert rules, and more

---

## Azure Services

| Service | Usage |
|---|---|
| Azure Policy | Core enforcement for alerting and monitoring baselines |
| Azure Monitor | Alerts, action groups, alert processing rules, workbooks |
| Azure Monitor Agent (AMA) | Telemetry collection deployed via policy |
| Log Analytics Workspace | Central destination for diagnostic settings |
| User Assigned Managed Identity | Shared policy remediation identity |
| Management Groups (ALZ) | Policy assignment scope |
| IoT Hub / Event Hub | IoT device telemetry ingestion |
| Azure Data Explorer (ADX) | Time-series analytics and KQL dashboards |
| ADLS Gen2 / Cosmos DB | Cold storage and device metadata |
| Azure Digital Twins | Device modeling (IoT reference) |
| Stream Analytics | Real-time data processing (IoT reference) |
| Azure SRE Agent | AI-assisted incident response |
| Grafana | Dashboard visualization |

---

## Prerequisites

- **Terraform** >= 1.9.0
- **Azure CLI** authenticated with sufficient permissions
- **PowerShell** 7+ for operational scripts
- **Python** 3.x for IoT data generation
- ALZ management group hierarchy already established
- VM Insights DCR deployed on target VMs (for log-based alerts)
- Subscription names: alphanumeric, underscore, and hyphen only

---

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/microsoft/end-to-end-observability.git
cd end-to-end-observability
```

### 2. Deploy AMBA Enterprise

```bash
cd amba-enterprise/terraform
cp environments/sandbox.tfvars terraform.tfvars  # adjust values
terraform init
terraform plan
terraform apply
```

### 3. Deploy Monitoring Baselines

```bash
cd monitoring-baselines/terraform
cp environments/sandbox.tfvars terraform.tfvars  # adjust values
terraform init
terraform plan
terraform apply
```

### 4. Generate test data (optional)

```powershell
cd test-data
./generate-test-data.ps1
./validate-data.ps1
```

See each module's README plus the onboarding guides ([AMBA greenfield](amba-enterprise/docs/onboarding-greenfield.md), [AMBA brownfield](amba-enterprise/docs/onboarding-brownfield.md), [Monitoring Baselines](monitoring-baselines/docs/onboarding.md)) for detailed deployment guidance.

---

## CI/CD Pipelines

Both AMBA Enterprise and Monitoring Baselines include GitHub Actions workflow files under their respective `pipelines/` directories (GitHub Actions will only execute workflows placed in `.github/workflows/`).

| Pipeline | Trigger | Purpose |
|---|---|---|
| `*-deploy.yml` | Push to `main` | Full validate → plan → apply with ring-based promotion |
| `*-validate.yml` | Pull request | Parameter governance, `terraform validate/fmt`, policy diff |
| `*-drift-detection.yml` | Daily schedule | Drift detection via `terraform plan`, auto-creates issues on drift |

---

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a Contributor License Agreement (CLA). For details, visit <https://cla.opensource.microsoft.com>.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information, see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com).

---

## Security

To report a security vulnerability, please follow the [Microsoft Security Response Center (MSRC) process](https://aka.ms/security.md). **Do not report security vulnerabilities through public GitHub issues.**

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.