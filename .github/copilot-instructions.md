# Copilot instructions

This repo (`microsoft/end-to-end-observability`) is a **monorepo of independent
Azure observability subprojects** that share the SRE-Agent + Azure Monitor theme
but have **separate tooling, no shared root build, and their own READMEs**. Always
`cd` into the relevant subproject before running anything. The root `README.md` is
a setup placeholder — ignore it for technical context.

| Subproject | What it is | Stack |
| --- | --- | --- |
| `sre-agent-observability-workbooks/` | 10 tenant-wide Azure Workbooks, **assembled from code** | Python builder, Terraform, Bicep, PowerShell |
| `auto-generated-workbooks/` | Discovery-linked workbook generation (4 domains) driven by SRE Agent discovery output | Python generator, Terraform, Bicep, PowerShell |
| `amba-enterprise/` | Enterprise Azure Monitor Baseline Alerts (AMBA) hybrid rollout | Terraform (AVM modules) + Azure Policy, GitHub Actions, PowerShell |
| `monitoring-baselines/` | Azure Policy initiative enforcing telemetry collection & posture (AMA + diagnostic settings + private endpoints), **separate from AMBA** | Terraform (built-in policy defs) + Azure Policy, PowerShell |
| `utility-iot-observability/` | Utility/IoT synthetic telemetry research + data generator | Python |
| `sre-agent-pre-req/` | Single integration checklist doc | Markdown only |

## Workbooks-as-code (the two workbook projects)

The most important convention: **workbook JSON is generated, never hand-edited.**

- `sre-agent-observability-workbooks/`: edit `builder/specs/*.py` (one spec per
  workbook) or `builder/shared/*`, then rebuild. `command_center.py` is the
  reference spec. Build + validate:
  ```powershell
  cd sre-agent-observability-workbooks
  python builder/build_workbooks.py        # -> workbooks/NN-slug.workbook.json + manifest.json
  python tests/validate_workbooks.py       # -> "All 10 workbook(s) valid."
  ```
  The validator enforces: valid JSON, the 13 shared parameters (type-9 block),
  the mandatory **Top 10 SRE Agent Recommendations** tile bound to
  `SREAgentRecommendations_CL`, no `{{ }}` placeholder leakage, and that every
  KQL/ARG tile declares `resourceType` + `crossComponentResources`. Run it after
  every change.

- `auto-generated-workbooks/`: `generator/generate_workbooks.py` **consumes SRE
  Agent discovery output** (`resources[]` JSON) and buckets it into four domains
  (infrastructure, application, network, storage), binding `templates/*.workbook.json`
  to a workspace. Empty domains are skipped. Output (`generator/out/`) is git-ignored
  and **deterministic** — repeated runs over the same inventory are byte-identical.
  ```powershell
  python ./generator/generate_workbooks.py --inventory ./discovery-result.json `
    --workspace-resource-id "<law-resource-id>" --templates-dir ./templates --output-dir ./generator/out
  ```
  Never commit `agent-discovery.*.json` (real tenant IDs);
  `generator/sample-discovery-input.json` is the sanitized input-contract example.

Both projects ship **identical workbooks via Terraform _and_ Bicep** with the same
deterministic resource names and `sourceId`, so the PowerShell `Deploy-Workbooks.ps1`
test path and a later `terraform apply` line up one-to-one instead of duplicating.

## KQL / Azure Resource Graph conventions (workbook specs & templates)

These rules come from real query failures — follow them when writing KQL in specs
or templates:
- Every Log Analytics tile must bind the global time picker with
  `| where TimeGenerated {TimeRange}`; omitting it scans full retention and
  triggers "Too many points (>10000)".
- In ARG, `title` is a **reserved word** — alias columns (e.g. `eventTitle`).
- Avoid self-referencing extends in ARG (e.g. `extend kind = tostring(kind)`) —
  causes ParserFailure.
- `StatusCode` in `StorageBlobLogs` is a string — cast with `toint(StatusCode)`
  before numeric comparison.
- In workspace-based App Insights, exception type is `ExceptionType`
  (`AppExceptions`), not lowercase `type`.
- `union withsource=X *` fails (SEM0001) if any workspace table already has column
  `X` — omit `withsource` when the source column is unused.

## PowerShell deploy scripts

Run `az`-based deploy scripts **in-session** with the call operator
(`& ./Deploy-Workbooks.ps1 ...`). Invoking them through a nested
`powershell -File ...` subprocess hangs on the first `az` call.

## amba-enterprise

Terraform `>= 1.9.0`. Hybrid model: Terraform orchestrates AMBA policy + parameters;
Azure Policy + remediation deploy/enforce alerts. Per-ring parameter files live in
`terraform/environments/{sandbox,pre-prod,prod}.tfvars`. Validate before deploy:
```powershell
./scripts/validate-parameters.ps1
terraform -chdir=terraform init
terraform -chdir=terraform apply -var-file=environments/<ring>.tfvars
```
Subscription names must use only alphanumerics, underscores, and hyphens
(`scripts/validate-naming.ps1`). Parameter governance is central — see
`docs/coverage-matrix.md` (17 AMBA initiatives) before changing thresholds.

## utility-iot-observability

Self-contained Python. Regenerate synthetic telemetry with
`python generate_synthetic_data.py .` (writes timestamped JSONL + CSV + metadata).
Upload via `python upload_to_azure.py --mode iothub --connection-string "..." --data-file <jsonl>`.
Synthetic data only — never commit real utility data.

## monitoring-baselines

Azure **Policy initiative** (Terraform, Azure **built-in** definitions only) that
enforces **telemetry collection & posture** — distinct from AMBA's alerting:
- **AMA** (DeployIfNotExists) installs Azure Monitor Agent on VMs/VMSS/Arc.
- **Diagnostic settings** (DeployIfNotExists) route platform logs/metrics to the
  central Log Analytics workspace.
- **Private endpoints** (Audit default, Deny optional) for monitoring-adjacent
  resources.

Boundary: **AMBA = alerting** (alert rules/action groups/APR); **monitoring-baselines
= telemetry collection + network posture**. They **share** the management group
hierarchy and the remediation UAMI, but have independent lifecycles (AMBA tracks
upstream module releases; monitoring-baselines changes only when baselines change).
Built-in defs are resolved at plan time by display name — if Microsoft renames one,
update `terraform/main.tf` `locals`. Validate before deploy:
```powershell
./scripts/validate-parameters.ps1
terraform -chdir=terraform plan -var-file=environments/<env>.tfvars `
  -var="management_subscription_id=<sub>" `
  -var="user_assigned_managed_identity_resource_id=<amba-uami-id>" `
  -var="log_analytics_workspace_id=<central-law-id>"
```
Deploy sequencing (per `docs/coverage-matrix.md`): Wave1 initiative+assignment+roles →
Wave2 remediate AMA → Wave3 remediate diagnostic settings → Wave4 review PE audit.
Diag coverage today: KeyVault, NSG, Storage, SQL, App Service, Event Hub, Service Bus,
Recovery Services (NOT yet AKS, Cosmos DB, APIM, Container Apps).

## Component deployment order (client onboarding)

These subprojects form one onboarding pipeline; **order matters** and each phase
gates the next (running out of order surfaces the very findings the SRE Agent later
reports). Default sequence:
0. **`sre-agent-pre-req`** — providers (incl. `Microsoft.AlertsManagement`,
   `Microsoft.PolicyInsights`), central LAW, management-group hierarchy + shared
   remediation UAMI (+ roles), naming, network allow-list.
1. **`monitoring-baselines`** — telemetry: AMA + diagnostic settings (the root-cause
   fix for "diagnostics off"). Telemetry must exist before alerting.
2. **`amba-enterprise`** — alerting on top of that telemetry (VM/VMSS/HybridVM **log**
   alerts need AMA present, else they fail to remediate — AMBA known-issues #1).
3. **workbooks** (`sre-agent-observability-workbooks` + `auto-generated-workbooks`) —
   dashboards exist before tasks write `_CL` rows.
4. **SRE Agent** — create + RBAC + integrations, Review mode.
5. **Scheduled tasks** — deploy disabled, enable one at a time; results render in the
   workbooks.

## Conventions across the repo

- Each subproject is independently versioned and documented — when changing one,
  update **that subproject's** README/docs, not the root.
- Terraform + Bicep are kept as **parity paths** in the workbook projects; a change
  to one usually needs the matching change in the other.
- Generated artifacts and live-tenant captures are git-ignored; commit the
  source-of-truth (specs, templates, sanitized samples), not the output.

## Work items (Azure DevOps)

User stories and tasks for this repo are tracked in an Azure DevOps project named
**"End-to-End Observability"**. Subproject READMEs reference these directly
(e.g. `auto-generated-workbooks` implements Story **#33**, Epic **#34**).

**Work-item-driven workflow — follow this on every change:**
1. Almost all work associates to an **Epic, User Story, or Task**. When the user
   says we're working on one, treat that work item as the context for the change.
2. If the user starts work **without** naming an Epic / User Story / Task, **ask
   which one before proceeding** — do not assume or start coding.
3. Pull the work item's acceptance criteria and map the implementation back to it.
4. After making code changes, **summarize what changed and post it as a comment
   on the associated work item** (update the comment thread on that Epic/Story/Task).
5. **Before committing to the git repo, update the work item's comment section**
   with the latest summary of changes. The comment update is a required pre-commit
   step, not optional.

## Handling SRE Agent findings / errors (root-cause triage)

When an SRE Agent run (or any diagnostic) reports an error or finding, **separate the
ERROR from the FIX, then fix from the root cause** — not the symptom. The objective is
a **deployable, repeatable client-rollout pattern**: onboarding a new client plus a
gated remediation library should reproduce the same clean outcome every time.

For each finding:
1. **State the error** (what was observed) separately from the **proposed fix**.
2. **Verify before acting** — confirm the finding is real (read-only checks); the
   agent can misdiagnose (e.g. recommending an RBAC grant that already exists).
3. **Classify the fix by root cause** and route it to the right home:
   - **Pre-req / onboarding** (e.g. missing diagnostic settings, unregistered resource
     providers, baseline RBAC) → fold into `sre-agent-pre-req` as a checklist step
     (and optional setup script) so it is closed **before** the agent runs and never
     recurs on a new client. Not a one-off remediation.
   - **Remediation** (a real, environment-specific defect) → a gated, `-WhatIf`-safe
     script + approval PR (mechanism per `operating-model.md`); per-incident.
   - **Hygiene** (low-risk cleanup) → document the routine.
   - **Misdiagnosis** (agent's stated fix is wrong) → correct it and, where useful,
     harden the detector so it doesn't repeat.
4. This triage applies to **all error types**, not just pre-req. A defect that also
   has a preventable cause usually gets **both**: a one-time remediation for the
   existing state *and* a pre-req guardrail so new clients never hit it.

## Recommended MCP servers

This repo is Terraform/Azure-heavy. The **HashiCorp Terraform MCP server** is the
most useful — it surfaces up-to-date module/provider registry docs and (with a TFC/TFE
token) workspace, run, and variable-set operations, which directly supports the
`amba-enterprise/` AVM-module work and the Terraform paths in both workbook projects.

Add it to your Copilot CLI MCP config, e.g.:

```json
{
  "mcpServers": {
    "terraform": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "hashicorp/terraform-mcp-server"]
    }
  }
}
```

Set a `TFE_TOKEN`/`TFC` token in the server env only if you need live workspace/run
tooling; registry doc lookups work without one.
