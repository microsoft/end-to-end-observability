# Monitoring Baselines Coverage Matrix

The Monitoring Baselines initiative bundles Azure **built-in** policy definitions across three categories. Unlike AMBA (which deploys *alert rules*), this initiative enforces *telemetry collection* and *network posture*.

## Policy Categories

| Category | Effect | Purpose |
|---|---|---|
| Azure Monitor Agent (AMA) | DeployIfNotExists | Installs and configures the Azure Monitor Agent on supported compute |
| Diagnostic Settings | DeployIfNotExists | Routes platform logs/metrics to the central Log Analytics workspace |
| Private Endpoints | Audit (default) / Deny | Flags or blocks monitoring-adjacent resources without private link |

## Azure Monitor Agent (AMA) Policies

| Reference ID | Target Resource | Built-in Policy (display name) | Effect |
|---|---|---|---|
| `ama_linux_vm` | Linux virtualMachines | Configure Linux virtual machines to run Azure Monitor Agent (user-assigned MI) | DeployIfNotExists |
| `ama_windows_vm` | Windows virtualMachines | Configure Windows virtual machines to run Azure Monitor Agent (user-assigned MI) | DeployIfNotExists |
| `ama_linux_vmss` | Linux virtualMachineScaleSets | Configure Linux VM scale sets to run Azure Monitor Agent (user-assigned MI) | DeployIfNotExists |
| `ama_windows_vmss` | Windows virtualMachineScaleSets | Configure Windows VM scale sets to run Azure Monitor Agent (user-assigned MI) | DeployIfNotExists |
| `ama_arc_linux` | HybridCompute/machines (Linux) | Configure Linux Arc-enabled machines to run Azure Monitor Agent | DeployIfNotExists |
| `ama_arc_windows` | HybridCompute/machines (Windows) | Configure Windows Arc-enabled machines to run Azure Monitor Agent | DeployIfNotExists |

## Diagnostic Settings Policies

| Reference ID | Target Resource | Destination |
|---|---|---|
| `diag_keyvault` | KeyVault/vaults | Central Log Analytics workspace |
| `diag_nsg` | Network/networkSecurityGroups | Central Log Analytics workspace |
| `diag_storage` | Storage/storageAccounts | Central Log Analytics workspace |
| `diag_sql_db` | Sql/servers/databases | Central Log Analytics workspace |
| `diag_app_service` | Web/sites | Central Log Analytics workspace |
| `diag_event_hub` | EventHub/namespaces | Central Log Analytics workspace |
| `diag_service_bus` | ServiceBus/namespaces | Central Log Analytics workspace |
| `diag_recovery_vault` | RecoveryServices/vaults | Central Log Analytics workspace |

## Private Endpoint Policies

| Reference ID | Target Resource | Built-in Policy (display name) | Effect |
|---|---|---|---|
| `pe_storage` | Storage/storageAccounts | Storage accounts should use private link | Audit / Deny |
| `pe_keyvault` | KeyVault/vaults | Azure Key Vaults should use private link | Audit / Deny |
| `pe_log_analytics` | Azure Monitor Private Link Scope | AMPLS should block access to non-private-link resources | Audit / Deny |

> The exact built-in definitions are resolved at plan time by display name via `data "azurerm_policy_definition"`. If Microsoft renames a built-in definition, update the display name in `terraform/main.tf` `locals`.

## Deployment Sequencing

| Wave | Action | Rationale |
|---|---|---|
| Wave 1 | Deploy initiative + assignment + remediation role assignments | Identity must have permissions before remediation runs |
| Wave 2 | Remediate AMA policies | Agent install precedes log-based monitoring; 5-10 min per VM |
| Wave 3 | Remediate diagnostic settings policies | Requires the agent and workspace to be in place |
| Wave 4 | Review private endpoint audit results | Flag gaps; escalate to Deny only after review |

## Prerequisites

| Category | Prerequisites |
|---|---|
| AMA | A User Assigned Managed Identity (reused from AMBA) with Virtual Machine Contributor + Monitoring Contributor at the assignment scope |
| Diagnostic Settings | A central Log Analytics workspace; remediation identity has Log Analytics Contributor |
| Private Endpoints | Private DNS zones and AMPLS configured if escalating to Deny |
| All | The Landing Zones management group exists and contains target subscriptions |

## Relationship to AMBA

| Concern | AMBA (`amba-enterprise/`) | Monitoring Baselines (`monitoring-baselines/`) |
|---|---|---|
| What it deploys | Alert rules, action groups, alert processing rules | AMA agent, diagnostic settings, private endpoint posture |
| Policy effect | DeployIfNotExists (alerts) | DeployIfNotExists (AMA/diag) + Audit/Deny (PE) |
| Lifecycle | Tied to upstream AMBA module releases | Independent; built-in definitions only |
| Shared | Management group hierarchy, remediation UAMI | Management group hierarchy, remediation UAMI |
