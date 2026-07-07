# Manual gates — steps the orchestrator cannot (and should not) automate

The `Deploy-EndToEnd.ps1` orchestrator drives every scriptable step, but the
following are **inherently manual** — either portal-only, approval-gated, or
foundational state that must exist before Phase 0. Confirm each before/around the
matching phase. Source of truth for pre-req items: `sre-agent-pre-req/sre-agent-integration-checklist.md`.

## Before Phase 0 (foundation)

- [ ] Target subscription(s) in a **supported region** (Sweden Central, East US 2,
      Australia East, UK South).
- [ ] **Management group hierarchy** (ALZ) established; target subscriptions placed.
- [ ] **Central Log Analytics workspace** created.
- [ ] **Shared remediation UAMI** created with `Monitoring Reader` at pseudo-root MG
      (reused by both `monitoring-baselines` and `amba-enterprise`).
- [ ] Deployer holds **Owner / User Access Administrator / RBAC Admin**
      (`Microsoft.Authorization/roleAssignments/write`).
- [ ] **Egress allow-list** (HTTP **and** WebSocket): `*.azuresre.ai`, `sre.azure.com`,
      `portal.azure.com`, `api.applicationinsights.io`. Proxy must not strip WebSockets.
- [ ] Workloads already emit logs to Log Analytics; App Insights enabled on app tiers.

## Phase 1 — monitoring-baselines

- [ ] Trigger remediation **waves** after apply (not automatic): AMA → diagnostic
      settings → review private-endpoint audit (`docs/onboarding.md`).
- [ ] Moving `private_endpoint_effect` from `Audit` to **Deny** — change-management
      approval required (prod only, after soak).

## Phase 2 — amba-enterprise

- [ ] Inject `management_subscription_id` (empty in all tfvars) via CI/secret or `-var`.
- [ ] Provide BYO UAMI / Logic App IDs (prod/pre-prod placeholders).
- [ ] Trigger alert remediation **waves**: Notification/Service Health → Platform →
      Compute → LZ services (`docs/coverage-matrix.md`).
- [ ] Portal cleanup of orphaned "Identity not found" role assignments if they appear.
- [ ] Prod deploy approval gate (per pipeline intent).

## Phase 3 — workbooks

- [ ] Do **not** hand-edit generated `workbooks/*.workbook.json` — edit specs + rebuild.
- [ ] Optionally seed sample rows (`New-SampleRecommendations.ps1`) so Top-10 panels
      render before the agent tasks populate `SREAgentRecommendations_CL`.

## Phase 4 — sre-agent

- [ ] **Verify** `Microsoft.App/agents@2026-01-01` is available in your tenant/region.
      If not enumerable, use the **portal**: `sre.azure.com → Create agent`.
- [ ] Choose **Reader** permission + **Review** mode to start; scope managed RGs to the
      smallest blast radius.
- [ ] Assign user roles (Reader / Standard User / Administrator) via **Entra groups**.
- [ ] Verify the agent's managed-identity RBAC (sub `Monitoring Contributor`; per-RG
      `Reader` + `Log Analytics Reader` + `Monitoring Reader`).
- [ ] Confirm inventory: ask the agent *"What Azure resources can you see?"*.

## Phase 5 — scheduled-tasks

- [ ] Tasks deploy **PAUSED**. Review each prompt, then **enable one at a time**
      (`-ActivateTasks` enables all — avoid on first rollout).
- [ ] Response plans / any task that can *act* stay in **Review** run mode until an
      observation period passes.
