# Data Sources

This suite reads from four kinds of sources. Tenant-tied IDs and table names are
**parameters or documented placeholders** — never hard-coded in the workbook JSON.

## 1. Azure Resource Graph (ARG)

Tenant-wide inventory, health, alerts, governance. Scoped by the **Subscriptions**
parameter. Tables used across the suite:

| Table | Used for |
| --- | --- |
| `resources` | Inventory by type/region/owner/environment; tagging; public exposure. |
| `resourcecontainers` | Subscription & management-group inventory. |
| `healthresources` | Resource Health (`Available` / `Unavailable` / `Degraded` / `Unknown`). |
| `alertsmanagementresources` | Active alerts (severity, monitorCondition, alertRule, targetResource). |
| `servicehealthresources` | Service Health events. |
| `resourcechanges` | Recent change correlation. |
| `policyresources` | Azure Policy compliance. |
| `advisorresources` | Advisor recommendations. |
| `securityresources` | Defender for Cloud assessments. |
| `authorizationresources` | RBAC posture (role assignments; Owner role GUID `8e3af657-a8ff-443c-a75c-2fe8c4bcb635`). |

Standalone copies: [`queries/resource-graph/inventory.kql`](../queries/resource-graph/inventory.kql),
[`governance.kql`](../queries/resource-graph/governance.kql).

## 2. Log Analytics (KQL)

Scoped by the **LogAnalyticsWorkspaceId** parameter. Every LA query binds the
**TimeRange** parameter (e.g. `where TimeGenerated {TimeRange}`) or it errors.

| Table | Status | Used for |
| --- | --- | --- |
| `SREAgentRecommendations_CL` | **required** | Top 10 section on every page. |
| `Heartbeat` | platform | Agent/AMA heartbeat, missing telemetry. |
| `Usage` | platform | Ingestion volume / cost. |
| `AzureActivity` | platform | Change/deploy/RBAC/policy correlation. |
| `SigninLogs` | platform | Sign-in & risky sign-in views (Entra). |
| `SREAgentActions_CL` | **placeholder** | Automation & remediation audit log. |
| `SREAgentAutomationCatalog_CL` | **placeholder** | Automation catalog. |
| `HealthModelSnapshot_CL` | **placeholder** | Health-model drift detection. |
| `WorkbookQA_CL` / `WorkbookReviewRuns_CL` / `WorkbookVersions_CL` | **placeholder** | Weekly-review outputs & version registry. |

Placeholder tables are referenced by panels but must be supplied per tenant. Until
they exist, those panels are empty by design (and the weekly review flags them).

## 3. Application Insights (KQL, workspace-based)

Application reliability and RCA. Bound to the same workspace via `app()` or
workspace-based AI tables:

| Table | Used for |
| --- | --- |
| `AppRequests` | Request rate, failure rate, p50/p95/p99 latency. |
| `AppExceptions` | Exceptions (group by `ExceptionType`, **not** `type`). |
| `AppDependencies` | Dependency failures and latency. |
| `AppAvailabilityResults` | Availability/synthetic tests. |

## 4. Azure Monitor Metrics

Resource performance panels where metrics beat logs (CPU, memory, IOPS, etc.),
scoped to the selected resources.

---

## Setting up `SREAgentRecommendations_CL` (Logs Ingestion API)

The agent writes recommendations through the modern Logs Ingestion pipeline:

1. **Custom table** — create `SREAgentRecommendations_CL` in the workspace with
   the schema in [recommendation-schema.md](./recommendation-schema.md) (DCR-based
   so column names stay clean).
2. **Data Collection Endpoint (DCE)** — provides the ingestion URL.
3. **Data Collection Rule (DCR)** — maps the incoming stream
   (`Custom-SREAgentRecommendations_CL`) to the table; note its **immutable ID**.
4. **RBAC** — grant the agent's identity **Monitoring Metrics Publisher** on the
   DCR.
5. **Write** — POST rows to
   `https://<dce>.<region>.ingest.monitor.azure.com/dataCollectionRules/<dcr-immutable-id>/streams/Custom-SREAgentRecommendations_CL?api-version=2023-01-01`.

Test the pipeline end-to-end with the sample generator:

```powershell
cd sre-agent-observability-workbooks/scripts
& ./New-SampleRecommendations.ps1 -Count 30 `
    -DcrEndpoint "https://<dce>.<region>.ingest.monitor.azure.com" `
    -DcrImmutableId "<dcr-immutable-id>" `
    -StreamName "Custom-SREAgentRecommendations_CL"

# Or just emit JSON to inspect locally:
& ./New-SampleRecommendations.ps1 -Count 30 -OutFile sample-recommendations.json
```

> **Legacy HTTP Data Collector API:** if you ingest that way instead, Log
> Analytics suffixes column names by type (`Severity_s`, `ConfidenceScore_d`,
> `ApprovalRequired_b`, `LastUpdated_t`). You would then need to update the
> column names in `builder/shared/recommendations.py` and rebuild. The DCR-based
> custom table above avoids this.
