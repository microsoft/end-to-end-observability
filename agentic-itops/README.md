# Agentic ITOps — Foundry agents and skills

Reference implementation of the agent portfolio for the **Agentic ITOps**
solution: agents run IT operations, humans govern. Azure AI Foundry is the
framework; Azure SRE Agent is the Catalyst that opens the door and the
incident-response component inside it.

## Domains V2 deployment planning

The September18 Midstream browser test has generated and delivered 200 neutral
observations over 20 frames, including measurable pump changes and one final
baseline-like frame after automatic recovery. The run is stopped and drained.
The new source and approved non-enforcing test usage settings are deployed.
The archive-loader issue is corrected and actual worker ingestion completed.
The review portal then executed one pump investigation, but its model workflow
failed output validation with no saved result. Live diagnostics confirm
non-enforcing usage mode and preserved accounting; the specific failing schema
field is still unclassified. End-to-end acceptance remains incomplete.
The validation-boundary correction now records bounded agent, boundary, keyword
and safe field paths before legacy error truncation, without rejected values.
It preserves strict validation and terminal failure semantics; this diagnostic
change alone does not establish or fix the original Midstream model defect.
See the checkpoint below.

The [Story 93 deployment package](docs/deployments/us93-domains-v2-foundry/README.md)
contains the complete architecture, delivery plan, diagrams, cost/data/AI
assessments and supporting reviews for Domains V2 feeding all nine Foundry
agents. It proposes a separate operator review interface while keeping
Domains V2 producer-only and synthetic results non-operational. This is a
partially deployed implementation, not a completed all-nine workflow.
Foundation, private application services, authenticated review and version 3 of
the nine Foundry agents are deployed. The first seven-agent reasoning workflow
has persisted its genuine result. The frontend authentication issue is corrected,
and model-free recovery has transferred that result into the live review UI.
The actual analysis is **incomplete / insufficient evidence**, not an accepted
incident: one reasoning stage completed and six report evidence gaps. Acceptance
and closure remain blocked by the existing complete-analysis gate. The two
lifecycle agents and knowledge publication, retrieval and revocation remain
incomplete. The
[application integration checkpoint](docs/deployments/us93-domains-v2-foundry/execution/application-integration.md)
records current live evidence and unfinished gates. The reference demo below
remains distinct.

### Explicit non-enforcing test usage accounting

Default usage policies remain enforced. An explicitly approved test may use
`usage_mode: "observe_only"` in the runtime `budget`, each host's
`ITOPS_EMBEDDING_BUDGET_JSON`, and the operator approval's `limits`.
The approval must also contain:

```json
{
  "usage_policy": {
    "mode": "observe_only",
    "chat_policy_sha256": "<hash of the exact new chat BudgetLimits>",
    "embedding_policy_sha256": "<hash of this host's exact new embedding BudgetLimits>"
  }
}
```

Use new policy versions and `BudgetLimits.parse(...).sha256`; do not fabricate
hashes, inflate numeric limits or reset ledgers. Worker and publisher each need
`ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON`; their embedding policy hashes may
differ because their technical deadlines differ. Remove
`ITOPS_CHAT_LIMIT_AMENDMENT_JSON` when explicitly switching to observe-only.
Existing numeric usage ceilings remain reference values but are not enforced
for newly authorized observe-only workflows. This allows billable usage without
an application spending ceiling; Azure quotas still apply.

Historical hashes, reservations (including unknown outcomes) and accumulated
costs remain unchanged. Diagnostics identify the current mode using
`caps_enforced: false` and a null effective spending limit, without relabeling
historical workflows. Missing or mismatched approvals fail closed.
Source/actor authorization and approval expiry still apply to each future test;
persistent mode configuration is not perpetual authorization. Context/history
bounds, output-token ceilings, execution deadlines, citation validation,
synthetic-only restrictions and human-review gates remain in force. This does
not enable retries, reopen failed attempts or approve knowledge publication.

### Separate synthetic acceptance preparation

The recovered weather-station result remains unchanged. The approved follow-up
prepares a new, explicitly synthetic analyzer-data scenario from the existing
Domains V2 replay; it is not evidence of a real application outage.
Scenario controls and expected outcomes stay in an evaluator-only artifact,
outside neutral observations and model input.

The activation update is now deployed to worker, tools, publisher and review.
Twenty neutral observations for the separate run are ingested in the existing
Azure Log Analytics workspace and verified against their submitted content.
The explicitly approved operator ingestion role remains assigned at the single
DCR scope. This is **code and data delivery**, not a completed new agent run:
the new source/run is active, consumer-managed-identity ingestion and SQL-derived
detection are complete, and the approved candidate-bound model renewal is active.
The new reasoning attempt failed because a provider response did not complete;
SQL has no new analysis result. All seven reasoning agents were invoked, but
invocation counts are not successful-stage evidence. The user approved bounded
provider diagnostics, an 8,192-token response ceiling, and exactly one replacement
under the unchanged cumulative limits. **That release is deployed to all four
services, and its single replacement has now failed with `ValidationError`.**
The original failure is truthfully visible as a failed diagnostic, not a completed
analysis. The replacement has no persisted model result. Its seven chat calls and
two embeddings are settled; cumulative accounting is USD4.877064082, including
the five unchanged historical unknown chat reservations. No further paid attempt
on that Downstream workflow is authorized. The read-only diagnostic update is now live and identifies
an **enum validation failure in `citations[].source_id`**: a returned citation
did not match the trusted-source allowlist. It exposes no rejected value or model
output and does not weaken validation. No additional model calls were made.
No human decision has been fabricated. See the
[live checkpoint](docs/deployments/us93-domains-v2-foundry/execution/application-integration.md)
for receipts and remaining gates.

All nine new immutable v3 prompt versions are registered and read back in
Foundry, with v1/v2 preserved. The deployed worker packages those v3 bindings;
no new model result is claimed by registration or application deployment.

Activation uses an explicitly renewed, unchanged cumulative model allowance.
Detector-driven reasoning uses an optional
`detector_authorization` inside `ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON`:
exact `producer_key`, `run_id`, `policy_sha256` and `candidate_sha256`. Admission
requires one enabled exact-signal policy, rederives the candidate from complete
SQL-committed observations, and reserves at most one model workflow per
reasoning/postmortem/hygiene kind for that incident. Changed hashes, missing
source receipts and expired approval fail closed. This is not an automatic
review approval or permission for detector fan-out. Already persisted results
remain recoverable without another model request.

Internally generated consumer jobs do not all contain a nested review source.
Their direct review lookup/diagnostics require an optional `root_job_id` context:
the original source-authorized submission and immutable SQL descendant links
must prove the selected child belongs to that source. The related UI forms expose
the context explicitly; an unrelated same-run job is not authorized by it.
The corrective review release is deployed, and actual child lookup, readiness
and budget inspection pass. Existing root advancement remains bounded
to one selected job; no model step is advanced before candidate/budget admission.

Review scope activation preserves the existing Key Vault catalog reference:
bootstrap operation `review-scopes` checks `previous_review_scopes_sha256`,
appends only one run/source and one already-authorized actor entitlement, and
reads the new secret version back. It cannot broaden producer/domain/surface
permissions, remove old entries, or rotate the session/OIDC credentials.
Runtime archive artifacts use the explicit packaging names
`runtime-archive-1.json` through `runtime-archive-8.json`.

The consumer detector supports optional exact `entity_id` / `location_id` /
`tag_id` / `phase` selection. A selector requires entity and location; omitted
tag/phase mean an exact null match, not a wildcard. Policies without a selector
retain their previous matching behavior and approval hashes. Selected policies
need their own exact approval hash; the offline package's rollout policy remains
disabled. Neither these source changes nor offline proof enables a live detector,
invokes models, approves knowledge or changes human review gates.

Run the offline preparation from this subproject; use a new run UUID and an
explicit UTC start:

```powershell
$run = [guid]::NewGuid().ToString()
.\.venv\Scripts\python.exe scripts\prepare_acceptance_scenario.py `
  --run-id $run --start "2026-09-17T22:00:00Z" `
  --output .build\acceptance-analyzer-new
```

The command uses the sibling Domains V2 replay and observation constructor,
validates all 200 emitted rows per case, and selects 20 exact-signal observations.
It requires one detected reference-change episode with observed recovery and
zero episodes in a separately identified normal control. The output contains
neutral observations, a disabled policy, evaluator-only proof and file hashes.
Existing output directories are rejected. No Azure credentials or network
connections are used; these files are **not** live ingestion or model acceptance
receipts. Never feed `evaluator-only.json` to the detector or models.

The [execution checkpoint](docs/deployments/us93-domains-v2-foundry/execution/README.md)
records current prerequisite changes and outstanding deployment gates. The
foundation and application resources are deployed, but a successful full
all-nine lifecycle has not been demonstrated. The existing three-agent demo
is not the planned all-nine workflow.

The approved [foundation stage](docs/deployments/us93-domains-v2-foundry/execution/foundation.md)
uses GPT-5.6-luna with azd/Bicep. It provisions infrastructure only: no application
services or schedules. The subsequent service stage established private
connectivity to SQL and evidence Storage; neither was opened publicly.
Foundation status alone does not imply all-nine application acceptance.

The [service-stage quote and gates](docs/deployments/us93-domains-v2-foundry/execution/services.md)
keep the approved USD 250/month services allowance separate from the USD 500
foundation allowance. The [all-nine registration path](docs/deployments/us93-domains-v2-foundry/execution/registration.md)
uses reviewed plan digests, durable version journals and exact read-back; all
nine existing names now have verified version 2, with version 1 preserved.
The [publisher delivery ledger](docs/deployments/us93-domains-v2-foundry/execution/publisher.md)
now implements fenced publication/revocation work and eligibility-aware
reconciliation. Private service hosting is deployed; actual approved knowledge
publication/retrieval/revocation acceptance remains pending.

The [review runtime entry point](docs/deployments/us93-domains-v2-foundry/execution/runtime-entrypoint.md)
documents required production configuration and the fail-closed app factory.
The [observation contract probe](docs/deployments/us93-domains-v2-foundry/execution/observation-contract-probe.json)
records successful read-only decoding of 10,530 retained producer observations
across all nine surfaces, using the operator identity rather than a consumer MI.

The reference demo **runs without Azure** — no subscription, no Foundry
project, no credentials. That is enforced by a test, not just claimed
([`tests/test_offline_guarantee.py`](tests/test_offline_guarantee.py)).

What that does and does not prove is worth being precise about, because
the honest answer is "half the system":

| | Offline | Needs Azure |
|---|---|---|
| Signal Normalizer, adapters, correlation | **really tested** | — |
| Change-Safety Gate, policy evaluation | **really tested** | — |
| Alert Hygiene scoring | **really tested** | — |
| Knowledge retrieval (BM25) | **really tested** | hybrid semantic ranking |
| Agent loop wiring, contracts, gate enforcement | **really tested** | — |
| **Quality of model reasoning** | **not tested** | yes |
| Tool-call behaviour, auth, live data shapes | **not tested** | yes |

The deterministic half is genuinely exercised: normalization has exactly
one correct answer, the gate is a pure function, and both have real test
suites. The probabilistic half is only *wired* offline — `StubReasoner`
is deterministic templating, not a model, and says nothing about whether
GPT-4o produces good hypotheses.

That boundary is not theoretical. Deploying to a real project
([docs/deploying-to-azure.md](docs/deploying-to-azure.md)) immediately
surfaced three defects the offline suite could not have caught — see
[architecture.md](docs/architecture.md#what-offline-testing-proves).

```bash
cd agentic-itops
pip install -r requirements.txt

python -m agentic_itops demo          # the full incident loop
python -m pytest tests -q             # unit and prerequisite regression tests
python evaluations/run_evals.py       # 41 evaluation cases
```

---

## What this is not

It is not an observability platform, and adding one is out of scope. The
customer already has signals. This layer **normalizes them, reasons over
them, grounds that reasoning in the customer's own operational knowledge,
and hands execution to something accountable.**

| Layer | Who | Build here? |
|---|---|---|
| Collect telemetry — Azure Monitor, Datadog, Splunk, Prometheus | existing tooling | **No** |
| Normalize → reason → recommend | **these agents** | **Yes** |
| Execute the change | Azure SRE Agent, runbooks, automation | **No — wrap it** |
| Visualize | Workbooks, Grafana | **No** |

---

## The portfolio

**CODE** = deterministic, unit-tested, bound to Foundry as an OpenAPI tool.
**PROMPT** = a Foundry agent: instructions plus declarative tool bindings.

| # | Agent / skill | Host | Status |
|---|---|---|---|
| 0 | [Ops Orchestrator](agents/00-ops-orchestrator) | PROMPT | instructions + manifest |
| 1 | **Signal Normalizer** | **CODE** | implemented, tested |
| 2 | [Triage & Dedup](agents/02-triage-dedup) | PROMPT | instructions + manifest |
| 3 | [RCA Investigator](agents/03-rca-investigator) | PROMPT | instructions + manifest |
| 4 | [Runbook & Memory](agents/04-runbook-memory) | PROMPT | instructions + BM25 backend |
| 5 | [Remediation Planner](agents/05-remediation-planner) | PROMPT | instructions + manifest |
| 6 | **Change-Safety Gate** | **CODE** | implemented, tested |
| 7 | [Incident Comms](agents/07-incident-comms) | PROMPT | instructions + manifest |
| 8 | [Postmortem Drafter](agents/08-postmortem-drafter) | PROMPT | instructions + manifest |
| 9 | **Alert Hygiene Analyst** | **CODE** + PROMPT | implemented, tested |
| 10 | [Industry Signal Pack](agents/10-industry-signal-pack) | PROMPT | 6 verticals |

### The rule that decides code vs prompt

Code when it must be **deterministic, auditable, an authorization boundary,
or high-volume**. Prompt when it needs **judgement, synthesis, or handling
patterns nobody anticipated**.

Two consequences of that rule are not negotiable:

**The Change-Safety Gate is code.** It is an authorization boundary, and the
text it reasons over — alert descriptions, log lines, ticket bodies — is
attacker-influenceable. A prompt can be argued out of its instructions. If
the component deciding *"may this agent restart that production cluster"* is
a model, there is no defensible answer for an AI Workload Security Assessment.

**The Signal Normalizer is code.** Schema translation has exactly one correct
answer. If the same payload normalized differently on two runs the fingerprint
would change, deduplication would break, and every downstream agent would be
reasoning about a different incident.

---

## What the demo shows

```bash
python -m agentic_itops demo
```

Two scenarios ship in `data/alerts`:

**1. Four tools, one problem.** Azure Monitor, Datadog and Prometheus each
alert on the same CPU saturation on `vm-pump-ctrl-07`, and a control-room
operator opens a ServiceNow ticket about it. Four signals, four consoles,
**one incident** — including the human ticket, which is the half of the
picture most correlation layers drop.

```
INCIDENT  89ffe49f7da1  [CRITICAL]
  4 signal(s) from azure_monitor, datadog, prometheus, servicenow   <-- CORRELATED
  [ 6.20] runbook:    RB-014: CPU saturation on midstream pump controllers
  [ 3.14] postmortem: PM-2026-06-03: ...polling extension upgrade
  1. [  high] Recent change 'extensions/write' at 13:16 altered behaviour
             evidence: landed 6 minutes before CPU stepped from ~44% to ~78%
  action: restart   (prescribed by RB-014)
  EFFECT: ESCALATE -> on_call_engineer
```

**2. The gate says no.** A Sev0 on the SCADA historian. RB-021 documents the
fix as a restart, the planner proposes exactly that — and the gate refuses,
because OT assets are outside agent authority regardless of severity:

```
  action: restart   (prescribed by RB-021)
  EFFECT: DENY
    - OT boundary: operational technology assets are out of scope for agent action
```

A demo that only shows success proves nothing about governance. This one
shows the runbook, the agent and the policy disagreeing, and the policy
winning.

**Nothing executes.** Even an ALLOW only marks the action ready to hand to
Azure SRE Agent. Foundry is the brain; SRE Agent is the hands.

---

## CLI

```bash
python -m agentic_itops normalize --input data/alerts      # correlate signals
python -m agentic_itops search    --query "pump cavitation"
python -m agentic_itops gate      --action restart --resource <arm-id> --env prod
python -m agentic_itops hygiene   --input data/alert-history   # the ADR artifact
python -m agentic_itops demo      --limit 2
```

An explicitly supplied blank policy denies rather than loading the sample
policy. Policy types and freeze windows are checked before evaluation; an
allowed action also needs an explicit resource allowlist. The gate rejects
invalid affected counts and prohibits operational use of requests marked
synthetic, including dry runs. Hosting code must bind provenance from trusted
evidence; an optional request field alone is not an end-to-end isolation boundary.
The reference orchestrator now aggregates restrictive source markers across all
contributing envelopes before reasoning; model output cannot clear them.

The demo evaluates the planner's explicit target and radius. A changed target
or malformed/failed plan stops the pipeline instead of becoming an annotation.
`--at` remains available for deterministic offline scenarios but is rejected
with `--foundry`; that CLI mode uses current UTC time. Foundry responses must
complete and contain a JSON object, and oversized evidence is rejected rather
than truncated. RCA and communications also require their stage-specific typed
content; empty objects fail instead of producing a successful result. These checks
do not yet provide the planned full nine-agent schema,
citation, version-pinning or durable gate-receipt validation.

`hygiene` is the one to reach for first in a real engagement. It runs against
exported alert history with **no live incident and no production access**, so
it can be delivered inside an Architecture Design Review before any security
gate has been cleared:

```
ALERT HYGIENE ANALYSIS
  total firings:     397        rules flagged:     5
  potential volume reduction: 87.7%
```

---

## Layout

```
agentic-itops/
├── agentic_itops/          # the Python package
│   ├── envelope.py         # canonical incident envelope + fingerprinting
│   ├── classify.py         # symptom / severity / environment mapping
│   ├── normalizer.py       # skill #1 — normalize + correlate
│   ├── safety_gate.py      # skill #6 — the authorization boundary
│   ├── hygiene.py          # skill #9 — noise scoring
│   ├── orchestrator.py     # agent #0 + the offline demo harness
│   ├── adapters/           # one file per source system
│   ├── policies/           # change-safety policy as data
│   └── tools/              # stubbed tool implementations
├── agents/                 # prompt-hosted agent definitions
├── tools/openapi/          # generated OpenAPI specs for Foundry binding
├── tools/mcp/              # MCP stub server
├── data/                   # alerts, runbooks, postmortems, fixtures
├── evaluations/            # datasets + runner
├── tests/                  # pytest
└── docs/
```

## Documentation

| Document | What it covers |
|---|---|
| [architecture.md](docs/architecture.md) | design decisions, prompt-vs-code, MCP vs OpenAPI |
| [walkthrough.md](docs/walkthrough.md) | run it end to end, with expected output |
| [deploying-to-azure.md](docs/deploying-to-azure.md) | live Foundry deployment, and the API gotchas |
| [hackathon-guide.md](docs/hackathon-guide.md) | the two-day build plan and pre-work |
| [governance.md](docs/governance.md) | AIWSA mapping, the gate, the untrusted-input posture |
| [evaluations/README.md](evaluations/README.md) | what is measured and why |

---

## Going live

Everything runs stubbed by default. Two environment variables switch it:

```bash
AGENTIC_ITOPS_LIVE_AZURE=1     # Log Analytics, Resource Graph, Activity Log
AGENTIC_ITOPS_LIVE_SEARCH=1    # Azure AI Search instead of local BM25
```

See [`.env.example`](.env.example). The tool contracts are identical either
way, so going live is configuration and not a rewrite.

---

## Status

Sample implementation for the Agentic ITOps solution accelerator. The code
skills are real and tested; the prompt-hosted agents ship as instruction sets
and manifests, ready to provision into a Foundry project.

Sample data is synthetic. `vm-pump-ctrl-07`, `contoso.com` and the runbook
corpus are fictional.
