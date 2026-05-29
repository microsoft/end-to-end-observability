## Plan: Enterprise AMBA Hybrid Rollout

Deliver AMBA baselines at enterprise scale using an upstream-compatible hybrid model: Terraform orchestrates AMBA policy deployment and parameters, while Azure Policy + remediation deploy and enforce alert rules. Standardization is achieved through AMBA policy initiatives and strict central parameter governance; override flexibility is preserved through approved threshold tags and policy exclusions. Action-group correctness is enforced through AMBA Notification Assets and Alert Processing Rules (APR), including BYON paths for brownfield.

---

## Phase 1 — Foundation & Contract (Days 0–30)

**Steps**
1. Full initiative coverage matrix: Build a coverage matrix from ALL 17 AMBA policy set definitions mapped to service domains and target assignment scopes. Domains: Connectivity (Parts 1 & 2), Management, Identity, VM, VMSS, HybridVM (Arc-enabled), Key Management, Load Balancing, Network Changes, Recovery Services, Storage, Web, Resource & Service Health, Notification Assets. This becomes the enterprise baseline contract.
2. Deployment topology design: Lock a single-enterprise management-group-first topology with subscription-level fallback only for CSP or Azure Lighthouse scenarios. Confirm initiative-to-management-group mapping: platform (Connectivity, Identity, Management) vs landing zones (VM, VMSS, HybridVM, Key Management, Load Balancing, Network Changes, Recovery Services, Storage, Web) vs root (Resource & Service Health, Notification Assets).
3. Parameter governance profile: Define a centrally owned parameter profile from ALZ defaults covering all 34+ parameters (monitoring RG, disable tag conventions, notification mode, severities, `includeAlzAlertsOnly`, exclusions, `telemetryOptOut`). Document allowed vs prohibited parameter edits by environment ring (sandbox, pre-prod, prod).
4. RBAC and identity design: Plan UAMI strategy — decide between AMBA-created default (`id-amba-prod-001`) or BYO UAMI via `bringYourOwnUserAssignedManagedIdentity` parameter. Assign Monitoring Reader role to UAMI at pseudo-root Management Group. Adopt "Monitoring Policy Contributor" least-privileged built-in role (2025-10-01 release) to replace over-provisioned Contributor assignments. Document role assignment lifecycle and orphaned role assignment cleanup procedures.
5. Deployment limit planning: Pre-calculate management group deployment count budget (limit: 800 per MG). Plan proactive stale deployment cleanup cadence. Document region lock-in constraint (first deployment region is permanent unless manual deployment entry deletion is performed).
6. CSP and Azure Lighthouse consideration: Document fallback path for managed tenants or partner scenarios requiring subscription-scope assignment instead of management group scope.
7. VM Insights prerequisite gate: Identify all subscriptions and VMs/Arc servers that require VM Insights Data Collection Rules before VM/HybridVM log alert remediation can succeed. Include VM Insights enablement as a blocking prerequisite in brownfield onboarding checklists.

---

## Phase 2 — Notification, Security & Override Controls (Days 15–45)

**Steps**
8. Notification architecture (no fork): Standardize on Notification Assets initiative as the default path so APR applies action groups to AMBA alerts at subscription scope. Reserve BYON only for approved brownfield subscriptions with existing AG/APR. Configure `includeAlzAlertsOnly` based on enterprise decision (route only AMBA alerts vs all alerts through APR).
9. Action routing acceptance model: Define explicit acceptance criteria for APR behavior: AMBA APR exists, is enabled, contains AddActionGroups action, points to approved AG set, is scoped to subscription with severity filters aligned to ops policy. Validate subscription naming compliance (alphanumeric + underscore + hyphen only) to prevent AG/APR deployment failures.
10. Override governance: Enforce strict centralized override process using approved threshold-tag patterns (`_amba-*-threshold-Override_`) and controlled `policyAssignmentExclusionList` changes. Require change tickets, owner approval, expiry date, and quarterly review for each override/exclusion.
11. Security hardening: Enable Customer-managed keys (CMK) for log search alert queries where required by compliance. Set `telemetryOptOut` per enterprise policy. Document `_deployed_by_amba` tag protection — these tags must NOT be removed or cleanup/maintenance scripts break.
12. Notification suppression and maintenance windows: Configure Suppression APR (`Deploy-AlertProcessingRule-Suppression.json`) for planned maintenance. Document activation/deactivation procedures for temporarily disabling notifications during change windows.

---

## Phase 3 — Terraform Orchestration & Onboarding (Days 30–60)

**Steps**
13. Terraform orchestration layer: Implement an internal wrapper (separate enterprise IaC repo) consuming pinned AVM module versions: `terraform-azurerm-avm-ptn-alz` (v0.15.0) for policy assets and role assignments, `terraform-azurerm-avm-ptn-monitoring-amba-alz` (v0.3.0) for monitoring resources, plus supporting modules `resource_group` (v0.2.1) and `user_assigned_managed_identity` (v0.3.4). Pass enterprise-governed parameter files per environment ring. Document module upgrade cadence aligned to AMBA releases.
14. Brownfield onboarding workflow: For existing subscriptions: (a) run preflight inventory (existing alerts, AGs, APRs, tags, exclusions, subscription naming validation), (b) verify VM Insights DCR presence for VM/Arc subscriptions, (c) choose notification mode (AMBA assets vs BYON), (d) deploy assignments, (e) execute staged remediation waves with compliance tracking. Document retry procedures for known transient failures (PolicyDefinitionNotFound, role assignment conflicts).
15. Greenfield onboarding workflow: For new subscriptions, enforce policy assignments before workload onboarding so DeployIfNotExists creates alert assets automatically and APR applies action groups immediately. Include UAMI provisioning and role assignment in the subscription vending pipeline.
16. Initiative assignment sequencing: Deploy in this order to reduce noise: (1) Notification Assets, (2) Resource & Service Health, (3) Identity + Management + Connectivity, (4) VM + VMSS + HybridVM, (5) Key Management + Load Balancing + Network Changes + Recovery Services + Storage + Web.
17. Known issue mitigation runbook: Pre-document remediation for all 9 documented AMBA known issues: VM Insights prerequisite, 800 deployment limit, region lock-in, orphaned role assignments, subscription naming restrictions, PolicyDefinitionNotFound transient failures, InvalidPolicyParameterUpdate during upgrades, location property errors with BYO UAMI, action group edit failures.
18. Cleanup and rollback procedures: Document `Start-AMBA-ALZ-Maintenance.ps1` usage and cleanup ordering: Alerts → Policy Assignments → Policy Set Definitions → Policy Definitions → Role Assignments → UAMI → Action Groups → Alert Processing Rules. Include `RoleAssignments`-only cleanup option for least-privilege migration. Document `_deployed_by_amba` tag dependency for cleanup script functionality.

---

## Phase 4 — CI/CD, Operations & Release Management (Days 45–75)

**Steps**
19. CI/CD quality gates: Keep existing AMBA parity checks (terraform-sync parameter alignment, policy build checks, template generation checks) and add enterprise pipeline gates for: parameter linting, promotion approvals, drift detection between desired parameter sets and active assignments, subscription naming validation pre-deployment, deployment count budget checks.
20. Release and upgrade runbook: Define upstream AMBA release intake process: diff policy sets/params for breaking changes (especially InvalidPolicyParameterUpdate scenarios), clean-then-redeploy when parameter names change between versions, test in pilot management group, execute remediation deltas, then promote. Include rollback via `Start-AMBA-ALZ-Maintenance.ps1` followed by redeployment of previous known-good version.
21. Operational run model: Publish runbooks for: incident routing validation, remediation retries (including transient failure handling), disable-tag handling, threshold override lifecycle, BYON switch procedures, maintenance window suppression activation/deactivation, orphaned alert cleanup for deleted resources, role assignment hygiene.
22. Logical volume exclusion management: Document procedures for excluding specific logical volumes from disk space alerts using the `Exclude-Logical-Volumes` feature for VMs with non-standard disk configurations.

---

## Phase 5 — Scale, Visualization & Specialized Workloads (Days 60–90+)

**Steps**
23. Scale and observability KPIs: Track baseline coverage percent by resource type (target: all 17 initiatives), policy compliance rates, remediation success time, alert-to-action-group routing success, override/exclusion drift, and deployment count utilization per MG. Use these KPIs as promotion gates for later rollout waves.
24. Visualization deployment: Deploy Azure Managed Grafana dashboards and Azure Workbooks for: compliance status by initiative, alert volume and routing health, remediation success/failure trends, override/exclusion inventory, and BYON vs standard notification split.
25. Specialized workload assessment: Evaluate applicability of additional AMBA patterns beyond ALZ: Azure Virtual Desktop (AVD), Azure VMware Solution (AVS), SAP on Azure, Artificial Intelligence, and Networking patterns. Create adoption backlog items for applicable patterns.
26. Delivery roadmap and backlog packaging: Publish the final outputs as (a) a phased 0-30-90 day roadmap with named milestones and review gates and (b) an implementation backlog of epics/stories with dependencies and acceptance criteria for platform engineering, cloud ops, and governance teams.

---

## Relevant Files

### Deployment & Topology
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/deploy/Introduction-to-deploying-the-ALZ-Pattern.md — Scope/topology guidance across ALZ-aligned and unaligned hierarchies.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/deploy/Remediate-Policies.md — Greenfield vs brownfield behavior and remediation procedures.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/deploy/Deploy-with-Terraform.md — Supported Terraform deployment model.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/Resources/Terraform-Module.md — AVM module documentation and versions.

### Parameters & Configuration
- c:/src/azure-monitor-baseline-alerts/patterns/alz/alzArm.param.json — Enterprise baseline parameter surface, exclusions, BYON knobs.
- c:/src/azure-monitor-baseline-alerts/patterns/alz/templates/policySets.bicep — Initiative orchestration and cloud-specific deployment processing.

### Notifications & Action Routing
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Notification-Assets.json — Notification initiative that wires APR + AG logic.
- c:/src/azure-monitor-baseline-alerts/services/AlertsManagement/actionRules/Deploy-AlertProcessingRule-Deploy.json — APR AddActionGroups implementation and BYON conditions.
- c:/src/azure-monitor-baseline-alerts/services/AlertsManagement/actionRules/Deploy-AlertProcessingRule-Suppression.json — Suppression APR for maintenance windows.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Bring-your-own-Notifications.md — BYON decision logic and transition paths.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Temporarily-disabling-notifications.md — Notification suppression procedures.

### Override & Governance
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Threshold-Override.md — Threshold override mechanics and tag conventions.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Exclude_resources_from_policy_assignment.md — MG/subscription exclusion procedures.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Exclude-Logical-Volumes.md — Logical volume exclusion feature.

### Identity & Security
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Bring-your-own-Managed-Identity.md — BYO UAMI feature and configuration.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Switch_To_LeastPrivileges.md — Monitoring Policy Contributor role adoption.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Customer_managed_key_for_log_search_alerts.md — CMK for log queries.

### Cleanup & Maintenance
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/HowTo/Cleaning-up-a-Deployment.md — Full cleanup procedures and Start-AMBA-ALZ-Maintenance.ps1 usage.
- c:/src/azure-monitor-baseline-alerts/docs/content/patterns/alz/Resources/Known-Issues.md — 9 documented deployment blockers and resolutions.

### Policy Set Definitions (all 17 initiatives)
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Connectivity-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Connectivity-Alerts-2.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Management-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Identity-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-VM-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-VMSS-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-HybridVM-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-KeyManagement-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-LoadBalancing-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-NetworkChanges-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-RecoveryServices-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Storage-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-Web-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-ResourceAndServiceHealth-Alerts.json
- c:/src/azure-monitor-baseline-alerts/patterns/alz/policySetDefinitions/Deploy-ServiceHealth-Alerts.json

### Service Alert Definitions (key examples)
- c:/src/azure-monitor-baseline-alerts/services/Compute/virtualMachines/alerts.yaml — VM baseline alerts.
- c:/src/azure-monitor-baseline-alerts/services/ContainerService/managedClusters/alerts.yaml — AKS baseline alerts.
- c:/src/azure-monitor-baseline-alerts/services/App/containerApps/alerts.yaml — Container Apps baseline alerts.
- c:/src/azure-monitor-baseline-alerts/services/Storage/storageAccounts/alerts.yaml — Storage baseline alerts.
- c:/src/azure-monitor-baseline-alerts/services/Network/ — Network resource alerts (multiple sub-resources).
- c:/src/azure-monitor-baseline-alerts/services/Web/sites/alerts.yaml — Web Apps baseline alerts.
- c:/src/azure-monitor-baseline-alerts/services/HybridCompute/machines/alerts.yaml — Arc-enabled server alerts.

### CI/CD & Tooling
- c:/src/azure-monitor-baseline-alerts/.github/workflows/check-terraform-sync-alignment.yml — Existing CI alignment gate.
- c:/src/azure-monitor-baseline-alerts/.github/actions-pester/Test-Terraform-sync.Tests.ps1 — Parameter parity contract used by CI.
- c:/src/azure-monitor-baseline-alerts/.github/workflows/generate-templates.yml — Template generation workflow.
- c:/src/azure-monitor-baseline-alerts/.github/workflows/check-policy-build.yml — Policy build validation.

---

## Verification

1. **Coverage verification**: Generate and review matrix proving ALL 17 initiative domains map to AMBA service alert definitions, with correct assignment scope per initiative.
2. **Parameter governance verification**: Validate enterprise parameter files against AMBA parameter schema and policy assignment expectations for all 34+ parameters.
3. **RBAC verification**: Confirm UAMI exists with Monitoring Reader at pseudo-root MG, Monitoring Policy Contributor role assigned (not legacy Contributor), no orphaned role assignments.
4. **Notification routing verification**: After deployment in pilot subscriptions, confirm APR existence, enabled state, AddActionGroups action, expected action group IDs, severity filters, and `includeAlzAlertsOnly` behavior.
5. **Greenfield verification**: Create test resources in a new subscription and verify DeployIfNotExists produces expected alert artifacts without manual remediation.
6. **Brownfield verification**: Run remediation for selected initiatives and confirm non-compliant resources converge to compliant with expected alerts and routing.
7. **VM Insights verification**: Confirm VM Insights DCR is deployed on target VMs/Arc servers BEFORE running VM/HybridVM log alert remediation; validate log alerts resolve correctly post-remediation.
8. **Override verification**: Apply approved threshold override tags and confirm alert behavior changes; verify compliance state updates and remediation behavior.
9. **BYON verification**: Test both scenarios (own AG with AMBA APR, own AG + own APR) and confirm no duplicate/competing notification assets.
10. **Suppression verification**: Activate suppression APR for maintenance window, confirm alerts are suppressed, deactivate and confirm alerts resume.
11. **Cleanup verification**: Execute `Start-AMBA-ALZ-Maintenance.ps1` in test scope, confirm ordered removal of all AMBA artifacts, verify `_deployed_by_amba` tagged resources are correctly identified.
12. **Security verification**: Confirm CMK applied to log search alerts where required, telemetry opt-out correctly configured, least-privileged role in use.
13. **CI verification**: Run terraform-sync alignment checks plus policy build checks before each promotion wave.
14. **Release verification**: Execute a dry-run of AMBA version upgrade in pilot scope, validate compliance deltas, rollback readiness, and clean-then-redeploy path for breaking parameter changes.
15. **Deployment limit verification**: Confirm deployment count per MG stays below 800; validate stale deployment cleanup automation.
16. **Visualization verification**: Confirm Grafana dashboards and Workbooks display real-time compliance, alert routing health, and KPI data correctly.

---

## Decisions

- **Operating model**: Hybrid — Terraform orchestrates policy deployment; Azure Policy enforces alert creation.
- **Tenant pattern**: Single enterprise tenant (with documented CSP/Lighthouse fallback for managed partners).
- **Override governance**: Centralized strict approval model with ticket, approval, expiry, and quarterly review.
- **Customization posture**: No internal AMBA fork; consume upstream and use supported parameters/features.
- **Identity model**: AMBA-created UAMI (`id-amba-prod-001`) as default; BYO UAMI for enterprise scenarios with pre-existing identity.
- **RBAC model**: Monitoring Policy Contributor (least-privileged) role; phase out legacy Contributor role assignments.
- **Alert routing scope**: `includeAlzAlertsOnly` set per enterprise decision — route only AMBA-deployed alerts by default; expand if needed post-validation.
- **Included scope**: Full 17-initiative baseline, greenfield + brownfield onboarding, governance, Terraform orchestration, CI/CD, operational runbooks, visualization, specialized workload assessment.
- **Excluded scope**: Direct upstream code modifications in AMBA repo, bespoke non-AMBA alert taxonomies, multi-tenant cross-customer alert management.

---

## Ownership Model

| Scope | Primary Owner | Supporting Owner |
|---|---|---|
| Module wrapper and versioning | Platform Engineering | Cloud Operations |
| Environment parameter values and rollout execution | Cloud Operations | Governance Team |
| Override and exclusion policy control | Governance Team | Cloud Operations |
| Quality gates and drift enforcement | Platform Engineering | Governance Team |
| RBAC and identity management | Platform Engineering | Security Team |
| Operational runbooks and incident routing | Cloud Operations | Platform Engineering |
| Visualization and KPI dashboards | Cloud Operations | Platform Engineering |

---

## Implementation Backlog

| Story ID | Epic | Phase | Team | Dependencies | Acceptance Criteria |
|---|---|---|---|---|---|
| AMBA-01 | Baseline Contract | 1 | Platform Engineering | None | Coverage matrix covers all 17 initiatives mapped to service domains and assignment scopes |
| AMBA-02 | Topology Design | 1 | Platform Engineering | AMBA-01 | MG assignment map approved with CSP/Lighthouse fallback documented |
| AMBA-03 | Parameter Governance | 1 | Governance Team | AMBA-02 | Central parameter profile approved for all 34+ params by environment ring |
| AMBA-04 | RBAC & Identity | 1 | Platform Engineering | AMBA-02 | UAMI deployed, Monitoring Policy Contributor assigned, orphaned roles cleaned |
| AMBA-05 | Deployment Limits | 1 | Platform Engineering | AMBA-02 | Deployment count budget documented; stale cleanup cadence defined |
| AMBA-06 | VM Insights Prerequisite | 1 | Cloud Operations | AMBA-01 | VM Insights DCR deployed on all target VMs/Arc servers before alert remediation |
| AMBA-07 | Notification Architecture | 2 | Cloud Operations | AMBA-03 | Notification Assets initiative deployed; APR validated in pilot |
| AMBA-08 | Action Routing Validation | 2 | Cloud Operations | AMBA-07 | All 5 APR acceptance criteria pass in pilot subscriptions |
| AMBA-09 | Override Controls | 2 | Governance Team | AMBA-03 | Override workflow requires ticket, approver, expiry, quarterly review |
| AMBA-10 | Security Hardening | 2 | Security Team | AMBA-04 | CMK configured, telemetry opt-out set, tag protection documented |
| AMBA-11 | Suppression Config | 2 | Cloud Operations | AMBA-07 | Suppression APR tested with activate/deactivate cycle |
| AMBA-12 | Terraform Wrapper | 3 | Platform Engineering | AMBA-03, AMBA-04 | Wrapper deploys all AVM modules with pinned versions across 3 rings |
| AMBA-13 | Brownfield Onboarding | 3 | Cloud Operations | AMBA-07, AMBA-12 | Preflight inventory + first remediation wave reaches compliance target |
| AMBA-14 | Greenfield Onboarding | 3 | Platform Engineering | AMBA-12 | New subscription vending deploys assignments before workload; alerts auto-created |
| AMBA-15 | Initiative Sequencing | 3 | Cloud Operations | AMBA-12 | Ordered deployment validated with no alert noise spike |
| AMBA-16 | Known Issue Runbook | 3 | Cloud Operations | AMBA-13 | All 9 known issues documented with mitigation procedures and tested |
| AMBA-17 | Cleanup Procedures | 3 | Platform Engineering | AMBA-12 | Maintenance script tested end-to-end; ordered cleanup validated |
| AMBA-18 | CI/CD Quality Gates | 4 | Platform Engineering | AMBA-12 | Pipeline blocks drift, invalid params, naming violations, budget overrun |
| AMBA-19 | Release Runbook | 4 | Cloud Operations | AMBA-17, AMBA-18 | Upstream intake tested with breaking change scenario and rollback drill |
| AMBA-20 | Operational Runbooks | 4 | Cloud Operations | AMBA-16 | All runbooks published, peer-reviewed, and dry-run tested |
| AMBA-21 | Logical Volume Exclusions | 4 | Cloud Operations | AMBA-13 | Exclusion procedures documented and validated for non-standard VMs |
| AMBA-22 | KPIs & Promotion Gates | 5 | Governance Team | AMBA-18 | KPI dashboard live; promotion decisions use defined thresholds |
| AMBA-23 | Visualization Deployment | 5 | Cloud Operations | AMBA-22 | Grafana dashboards + Workbooks deployed with real-time data |
| AMBA-24 | Specialized Workloads | 5 | Platform Engineering | AMBA-22 | AVD/AVS/SAP/AI patterns assessed; adoption backlog created |
| AMBA-25 | Roadmap Packaging | 5 | All Teams | AMBA-22 | 0-30-90 roadmap + full backlog published and approved |

---

## 0-30-90 Day Roadmap

| Window | Primary Outcomes | Exit Criteria |
|---|---|---|
| Days 0–30 | Freeze enterprise baseline contract (all 17 initiatives), finalize MG topology, approve parameter governance profile, deploy UAMI + least-privileged role, validate deployment budget, enable VM Insights prerequisites, document CSP/Lighthouse fallback | Signed baseline contract; approved topology; RBAC deployed; VM Insights ready; deployment budget confirmed |
| Days 31–60 | Deploy Notification Assets + Service Health in pilot, validate APR routing, configure suppression APR, deploy Terraform wrapper with all AVM modules, execute first brownfield remediation wave, stand up greenfield vending pipeline, implement security hardening (CMK, telemetry, tags) | Pilot shows expected APR behavior; Terraform deploys all 3 rings; brownfield compliance converging; greenfield auto-creates alerts; security controls active |
| Days 61–90 | Expand to platform + landing zone MGs, enforce CI/CD gates, publish all operational/release/cleanup runbooks, deploy Grafana/Workbook dashboards, implement KPI-based promotion gates, assess specialized workloads, package final roadmap | Production rollout wave complete; CI gates blocking drift; runbooks tested; KPI dashboard live; specialized workload assessment done; final roadmap approved |

---

## Further Considerations

1. **Terraform wrapper ownership**: Platform engineering owns module wrapper/versioning; cloud operations owns environment parameter values and rollout execution.
2. **Promotion strategy**: Pilot MG → platform subscriptions → workload landing zones by risk tier.
3. **Noise control**: Start with stricter severity filtering in APR for initial waves, then widen once signal quality is proven.
4. **Module version pinning**: Pin AVM module versions and upgrade only during planned release intake windows.
5. **Subscription naming compliance**: Validate subscription names before onboarding (alphanumeric + underscore + hyphen only) to prevent AG/APR deployment failures.
6. **Deprecated initiative cleanup**: Remove references to deprecated Service Health and Landing Zone initiatives if upgrading from older AMBA versions.
7. **Multi-region considerations**: Document that deployment region is locked after first deployment; plan region selection carefully before initial rollout.
8. **Monitoring Packs**: Evaluate Azure Monitoring Packs as supplementary coverage for workload-specific scenarios not covered by ALZ pattern.