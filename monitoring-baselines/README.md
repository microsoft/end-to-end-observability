# Monitoring Baselines

Azure Policy initiative that enforces **monitoring baselines** on supported resources so that diagnostic and logging gaps are flagged and auto-remediated. This component is **separate from AMBA**.

## Why this is separate from AMBA

| Concern | AMBA (`amba-enterprise/`) | Monitoring Baselines (`monitoring-baselines/`) |
|---|---|---|
| Domain | **Alerting** — alert rules, action groups, alert processing rules | **Telemetry collection & posture** — agent, logs, network |
| What it deploys | Metric/log/activity alerts | Azure Monitor Agent, diagnostic settings, private endpoint policies |
| Upstream coupling | Pinned to upstream AMBA module releases | Independent; Azure **built-in** policy definitions only |
| Lifecycle | AMBA release-upgrade cadence | Changes only when baselines change |

Keeping these separate avoids coupling telemetry/governance policy to AMBA's module-version and breaking-change lifecycle. The two components **share** the management group hierarchy and the remediation User Assigned Managed Identity.

## What it enforces

1. **Azure Monitor Agent (AMA)** — DeployIfNotExists installs and configures AMA on Windows/Linux VMs, VM scale sets, and Arc-enabled machines.
2. **Diagnostic settings** — DeployIfNotExists routes platform logs/metrics for supported resources (Key Vault, NSG, Storage, SQL, App Service, Event Hub, Service Bus, Recovery Services) to a central Log Analytics workspace.
3. **Private endpoints** — Audit (default) or Deny posture for monitoring-adjacent resources without private link.

All three are bundled into a **single default initiative** assigned to the Landing Zones management group. Non-compliant resources are visible — with resource-level drill-down — in the posture workbook.

## Structure

```
monitoring-baselines/
├── terraform/                          # Custom initiative built from Azure built-in definitions
│   ├── main.tf                         # Initiative, assignment, remediation role assignments
│   ├── variables.tf
│   ├── outputs.tf
│   ├── providers.tf
│   ├── versions.tf
│   └── environments/
│       ├── sandbox.tfvars
│       ├── pre-prod.tfvars
│       └── prod.tfvars
├── scripts/
│   └── validate-parameters.ps1         # Parameter governance validation
├── pipelines/
│   ├── mb-deploy.yml                   # Deployment pipeline
│   ├── mb-validate.yml                 # PR validation
│   └── mb-drift-detection.yml          # Daily drift + compliance summary
├── docs/
│   ├── coverage-matrix.md              # Policy categories × resources × effects
│   ├── onboarding.md                   # Greenfield + brownfield rollout
│   └── runbooks/
│       ├── known-issues.md
│       └── override-lifecycle.md
└── visualizations/
    ├── workbook-monitoring-posture.json    # Non-compliant resource drill-down
    └── grafana-monitoring-baselines.json
```

## Deployment

```bash
cd monitoring-baselines/terraform

terraform init \
  -backend-config="key=monitoring-baselines-<env>.tfstate" \
  # ...remaining backend config

terraform plan \
  -var-file="environments/<env>.tfvars" \
  -var="management_subscription_id=<sub>" \
  -var="user_assigned_managed_identity_resource_id=<amba-uami-id>" \
  -var="log_analytics_workspace_id=<central-law-id>" \
  -out=tfplan

terraform apply tfplan
```

The secrets (`management_subscription_id`, `user_assigned_managed_identity_resource_id`, `log_analytics_workspace_id`) are injected by the pipelines from repository/environment secrets. The UAMI is the **same identity used by AMBA**.

## Policy effects

| Category | Effect | Rationale |
|---|---|---|
| AMA | DeployIfNotExists | Auto-install the agent; no manual action needed |
| Diagnostic settings | DeployIfNotExists | Auto-create log routing to the central workspace |
| Private endpoints | Audit (default), Deny (optional) | Flag gaps without breaking existing resources; escalate to Deny after soak |

## Acceptance criteria coverage

- ✅ Policies auto-enforce AMA and diagnostic settings on all supported resources (DeployIfNotExists).
- ✅ The default initiative includes AMA, logging (diagnostic settings), and private endpoint policies.
- ✅ Non-compliant resources are visible in the posture workbook with per-resource drill-down.

## Related

- `amba-enterprise/` — the alerting layer (alert rules + routing). See its README for the boundary between the two.
