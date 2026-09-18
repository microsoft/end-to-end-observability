# Story 93 - complete agentic-itops deployment plan

**Target architecture superseded by the outcome-design revision.** See
`..\us93-outcome-design\outputs\agentic-itops-outcomes\docs\solution-design.md`
and `delivery-plan.md` in that same directory. This document remains the
historical full-source review and deployment-prerequisite baseline; its
unresolved defects and gates have not been fixed or waived.

Date: 2026-09-14. Recreated from a deep source review, not a reduced pilot.
Status: review and recreated plan complete. Independent review's one acceptance correction has been incorporated and directly checked under its authorized closure procedure. Deployment NOT ready.

## Outcome and non-negotiable scope

Deploy and demonstrate **every one of the nine prompt agents defined in `agents`**, plus Signal Normalizer (01), Change-Safety Gate (06), and Alert Hygiene's deterministic code (09). Each definition must have real instructions, validated output, required tools, identity/network access and an exercised workflow. Listing nine names is not completion.

Integrate the existing producer in `iot-dv2-us92-0914-rg` as a separate consumer. Preserve `domainsV2` as producer-only. Do not restore its removed Foundry folder, enable the old add-on, or reuse its identity/Function by assumption.

Story 93 is Active, revision 5, title "Deploy Foundry agents", with no description or acceptance criteria when refreshed for this review. The acceptance criteria here are proposed; no Story fields/comments have been written.

**Research is complete only after coverage and integrated verification pass. Deployment remains blocked until implementation, compatibility and approval gates pass.** No cloud action is authorized by this document.

## What can and cannot be reused

| Source area | Reuse | Required work before deployment |
|---|---|---|
| `agents` | All nine roles, prompts, schemas and declared relationships | Compile against one supported API generation; fill missing lifecycle/publication contracts; no silent agent/tool omission |
| `agentic_itops` | Deterministic adapters, normalization, gate, hygiene and BM25 as tested reference code | Fix concrete data/gate/parity defects, add strict contracts and Domains V2 input; current local three-call loop is not the desired full agent runtime |
| `tools\openapi` | Six generated tool contracts | Implement authenticated host, strong request/result/error schemas, resolved server URLs and limits |
| `tools\mcp` | Four-tool offline stdio demonstration | Replace deployment transport with a supported authenticated remote MCP implementation; no claim the stub is a hosted service |
| `scripts` | Generators and partial deployment logic | Reconcile generated definitions and hand-maintained prompts; replace split legacy/five-agent routes with verified manifest-driven deployment |
| `data` | Reviewed synthetic examples and small starter knowledge corpus | Preserve labels/provenance; add independent domain/agent quality cases and structured indexed metadata |
| `tests`, `evaluations` | Offline regression baseline | Add live-boundary mocks, all-agent schema/tool/routing tests, ingestion/error/lifecycle/isolation tests and approved live quality acceptance |
| `docs`, root config | Intended architecture and demo guidance | Correct documented-versus-implemented contradictions, SDK/environment contract, resource costs and all-agent operational runbook |

Coverage is verified: **102/102 files, 10,638 source lines; 101 full-content reviews and one structured full scan of all 397 bulk-history records.** No file was excluded. All 102 hashes still match the review baseline. The source review and per-file ledger are companion deliverables, not substituted by this architecture summary.

Generator ownership must be resolved explicitly in W0: `emit_agent_manifests.py` overwrites eight manifests, not 00 or the instruction files. The required folder behavior remains the acceptance contract; update the owning generator and generated outputs together if that ownership is retained, with a reviewed diff and drift test. Alternatively approve a folder-authoritative compiler/exporter. Do not blindly regenerate and erase accepted folder changes.

## Every agent's deployment and workflow contract

| Agent folder | Runtime responsibility | Required tools/connections | Evidence needed to accept |
|---|---|---|---|
| `00-ops-orchestrator` | Actual Foundry orchestration, not a dormant definition | Connected specialists: memory, RCA, planner, comms; normalization and gate OpenAPI tools | Trace memory first, RCA, planner, real gate and comms last; reject fabricated/skipped gate decisions |
| `02-triage-dedup` | Severity/business-context judgment after deterministic correlation | Resource Graph | Original fingerprint retained; justified severity/suppression; prohibited suppression rejected; explicit trigger outside the four connected specialists |
| `03-rca-investigator` | Ranked hypotheses with evidence and missing-evidence acknowledgment | Change history, Logs and Resource Graph OpenAPI; Azure Monitor MCP | Correct tool calls and scopes, evidence citations, uncertainty, no audit-label features or invented change/topology |
| `04-runbook-memory` | Institutional-memory retrieval and prohibitions | Azure AI Search `agentic-itops-knowledge`, vector/semantic/hybrid, top_k 3 | Correct runbook/postmortem/action/prohibition metadata; relevant retrieval and honest no-match |
| `05-remediation-planner` | Proposed action, target, classification, preconditions, blast radius and rollback | Same Search index plus Resource Graph | Schema complete; source action prescribed; exact target/radius evaluated by gate, never self-authorized |
| `07-incident-comms` | Engineer/executive/customer drafts | No tools declared | Grounded audience-specific drafts reflect authoritative decision; no sending capability |
| `08-postmortem-drafter` | Closure analysis and retrievable learning | Search and change history; reviewed missing publication contract | Draft supported by incident record; approved versioned write-back is retrieved in a later memory query |
| `09-alert-hygiene` | Explain deterministic alert-history scores | Alert-hygiene OpenAPI | Correct lifecycle/rule counts; do-not-touch boundaries; realistic caveats; independent batch trigger |
| `10-industry-signal-pack` | Industry criticality, controls and additional prohibitions | Search; declared industry parameter | `energy_utilities` used for this case, the other five choices in the six-vertical portfolio preserved; no invented compliance certification |

01 and 06 remain deterministic code tools, never rewritten as prompts. 09 remains code plus prompt. All workflows must run, but postmortem and hygiene are not forced into every incident.

## Runtime composition

**Input workflow:** approved retained/live observation window -> bounded reader -> domain-specific detector -> finding adapter -> deterministic normalization/correlation -> industry context and triage -> normalized incident handed to actual Ops Orchestrator.

**Incident workflow:** Runbook Memory first -> RCA Investigator -> Remediation Planner -> code gate -> Incident Comms. Preserve the four declared specialist relationships. A trusted runtime verifies the gate was called with the reviewed proposal and binds the returned decision to that proposal; an LLM's `gate_decision` string alone is not proof.

**Closure workflow:** explicit incident closure record -> Postmortem Drafter -> authorized review/publication -> versioned knowledge index -> demonstrated later retrieval. Proposed learning does not mutate production runbooks automatically. Synthetic knowledge stays identified/isolated.

**Hygiene workflow:** approved alert-history export or explicitly labeled evaluation fixture -> deterministic noise analysis -> Alert Hygiene agent -> reviewed report. Neutral observations do not become synthetic alert firings merely to feed this workflow.

For an integrated Domains V2 demonstration, also persist an explicitly versioned **detector-derived synthetic firing/recovery history**, distinct from neutral observations and producer audit labels. Feed that history to hygiene only after lifecycle semantics and sufficient observation coverage are established; until then report insufficient data. Exported generic alert fixtures can establish the tool contract but do not prove Domains V2 noise-analysis validity.

Industry/triage/closure/hygiene triggers are new lifecycle wiring. Do not silently add them to the four connected-agent list without documenting the mapping.

## Deployment topology and alternatives

Recommended: a dedicated Foundry account/project and all nine prompt agents; an authenticated Function-style tool host matching the repository contracts; Azure AI Search; a supported remote MCP endpoint; durable state/output storage and operational telemetry; a separate lifecycle runner.

Start the complete portfolio through **manual bounded workflow invocations**, then enable a scheduled/queued Azure lifecycle worker only after acceptance. This stages operation, not agent scope. All nine remain required.

| Alternative | Scope fidelity | Tradeoff | Decision |
|---|---|---|---|
| Full portfolio + Function-style tools + manual lifecycle driver | Preserves folder roles and explicit tools | Lowest scheduling complexity; still requires real cloud dependencies and identity testing | First acceptance topology |
| Same portfolio + Azure scheduled/queued driver | Same nine agents | Adds durable cursor, leases, retries, concurrency and operational ownership | Target repeatable deployment, scheduler initially disabled |
| Rebuild everything as custom hosted agent/container | Could preserve behavior after a rewrite | Larger framework/hosting/API migration; nine prompt definitions no longer automatically equivalent | Not default; explicit architectural approval required |
| Existing five-agent FileSearch deployer / old add-on reuse | Does not meet user scope as written | Appears faster but omits contracts or introduces shared-state coupling | Rejected as completion path |

Proposed new RG: `agentic-itops-us93-rg`; names and region are not yet approved/availability-checked. Same subscription `643febd3-3170-4110-80f9-fc8980b70a7a`, tenant `d3f0c895-c524-4dde-9e90-422349767d24`.

### Required resources and deployment outputs

| Resource/capability | Purpose | Required recorded output |
|---|---|---|
| Foundry account/project | Host prompt agents/connections | Resource IDs, endpoint, network posture, project identity and supported API generation |
| Approved chat model deployment | Reasoning for nine agents | Deployment name, model/version/SKU/region, lifecycle evidence and quota |
| Azure AI Search | Declared shared hybrid/semantic/vector knowledge index | Endpoint/connection, index schema/version, vector dimensions/profile, semantic configuration, embedding/vectorization method |
| Embedding capability | Populate/query vector index consistently | Approved model/version, dimensions and identity; or explicitly supported equivalent vectorization configuration |
| Authenticated tool host | Six code/API operations | Actual URL, supported auth audience, caller/backend identities, health and version |
| Remote MCP endpoint | RCA observability tools | Supported transport/version, allowed tools, authentication, approval policy and health |
| Runtime/checkpoint store | Run state, EventId dedup, leases, outputs and publication versions | Separate containers/paths, retention, scoped identity and encryption/network settings |
| Operational telemetry | Invocation/tool errors, usage/latency and audit | Correlation IDs, agent/prompt/model/policy/index versions, redaction and retention |
| Lifecycle worker | Input/context/triage/closure/hygiene triggers | Explicit mode, image/package hash, schedule-disabled state, expiry/concurrency/retry limits |

Do not create nine containers merely because nine prompt folders exist. Supporting host count, SKU and network approach are decisions driven by authentication/availability and cost, not by folder count.

## Source/API compatibility gate before provisioning

The two current deployment scripts target different agent surfaces. The modern script omits required bindings; the older route's descriptor dictionaries and `create_agent` cannot be assumed compatible with versioned projects. Select a tested SDK/API generation and compile all nine definitions before creating resources.

For each binding, record the exact supported type, project connection, caller identity, backend identity, resource audience, endpoint and validation method. The independent public-source findings are in `platform-compatibility.md`; unresolved items are blocking spikes, never success-shaped stubs.

**Verified platform constraint:** current migration guidance marks classic Connected Agents unsupported in new Foundry and recommends A2A. Preferred compatibility path: retain Ops Orchestrator as a prompt agent, expose its four specialist targets through supported incoming A2A, and bind GA A2ATool/protocol 1.0 with appropriate project connections/target permissions. Foundry A2A targets are text-only and nonstreaming, so preserve structured JSON within the supported text contract and validate it. This is a necessary reviewed transport migration, not removal/replacement of the agents. A Foundry workflow is an alternative only after its exact CSDL schema and equivalent responsibilities are proven and approved.

The current migration guide specifies projects SDK >=2.3.0. Select an exact compatible version at or above the documented floor; the floor alone is not a lock. Search binding types and vector_semantic_hybrid/top_k=3 are documented, but actual Search identity has an account-versus-project conflict in Microsoft's guidance. Resolve this configuration before role assignment. Do not copy documented Contributor setup roles and label them proven least-privilege query roles.

Required spikes: modern equivalent of connected-agent delegation; actual Azure AI Search connection/index binding; managed-identity OpenAPI auth; remote MCP authentication/approval; strict structured-output schema mapping; explicit agent-version invocation.

Preserve manifest model `gpt-4o`, temperature and top_p unless current lifecycle/model support requires an explicitly approved change. Current local Python is not a ready live environment: azure-ai-projects, azure-ai-agents, azure-monitor-query and azure-search-documents are absent; openai is 0.28.1. Later create an isolated compatible locked environment; no shared-environment installation occurred.

Published lifecycle snapshot: gpt-4o 2024-11-20 and gpt-4.1-mini 2025-04-14 are Legacy, with retirement listed as 2027-04-14 in the schedule updated 2026-09-14. gpt-5.1 is listed as the gpt-4o replacement; no replacement is listed for that mini row. This does not select a substitute or establish regional/quota/tool compatibility; plan a model upgrade before retirement and recheck on deployment day.

Postmortem write-back is demanded by instructions but not represented by a write tool in the YAML. Add an explicitly reviewed publication operation/schema and separate scoped identity; do not silently grant Search write to all reasoning agents.

## Correctness fixes are rollout prerequisites

The existing offline tests passing does not waive these reproducible failures:

- Gate must evaluate the proposed target/blast radius, not the original alert's resource/count. Current code can return ALLOW for a proposal whose different target/radius should be denied.
- Empty CLI policy must not reload the permissive sample; missing resource scope must not authorize all resources. Enforce typed policy, trusted time and unconditional synthetic restrictions.
- Preserve Common Alert Schema array metrics; expand Alertmanager batches consistently across direct API, JSON and JSONL ingestion.
- Make correlation unambiguous/order-stable and normalize UTC lifecycle times. Preserve Domains V2 run/series identity, not just generic metric aliases.
- Correct hygiene event/snapshot semantics, empty counts and unsupported self-resolution claims; protect safety-critical rule recommendations.
- Propagate query/model/normalization errors visibly; reject partial/truncated results and malformed agent output. Do not substitute valid-looking annotation plans for failed reasoning.
- Preserve structured evidence rather than slicing JSON; pin agent/model/prompt versions and cap tool/retry loops.
- Implement Search parity, returned documented actions/prohibitions, real context/change semantics, target-aware state, indexing-success gates and safe resumable cleanup.
- Fix release-check integrity: missing/empty datasets currently pass as zero-case suites, retrieval can accept a wrong ID by substring, and the legacy import guard does not reliably intercept imports on the reviewed Python 3.13 environment. Require dataset counts/IDs/schemas and fail-fast offline/no-network enforcement before trusting release gates.
- Preserve runbook action granularity: "restart the polling service, never the host" must not become generic VM restart because the action string is only `restart`. Carry component/service target, prohibitions, preconditions and rollback through retrieval, proposal and gate.

Fixes belong in the relevant generator/runtime/adapter/tool and tests, not merely deployment documentation. Detailed citations and reproductions are in `runtime-review.md` and the two probe-result files.

The declaration review also found missing nested input/output contracts, differing RCA execution order, inconsistent common untrusted-content clauses, reader-only roles conflicting with postmortem write-back, and incompatible small-demo versus full-portfolio documentation. Reconcile these in source and docs; prompts cannot weaken trusted policy. Industry guidance is advisory: safety/OT prohibitions dominate environment downgrades, and a flow/pressure pattern is a hypothesis, not proof of cavitation.

## Domains V2 integration acceptance

Read only `EnergyObservations_CL` from workspace customer ID `aff5d65c-97c3-493f-8710-f1e86817c984`. Preserve the producer-owned 16-field schema and nine-surface mapping. Do not read `EnergyInjectionAudit_CL`, scenario labels or hidden fixture outcomes into inference.

Reader requirements: exact validated RunId; absolute UTC observation windows for historical replay; bounded ingestion scans with overlap/catch-up; full typed Payload/Context materialization; durable EventId dedup; explicit response-limit detection/window subdivision; no checkpoint advancement on partial failure.

Series identity: domain, surface, run, record type, entity, location, measurement, tag and phase. Keep null distinct from zero, units and inventory quantity/uom distinct, and source-time/quality/baseline semantics intact. Unsupported data returns insufficient evidence, not healthy. Stopping a producer is not recovery.

**Freshness contract:** the domain owner must define per-surface/per-series cadence, acceptable source age, missing-sample gap and late-arrival expectations in W3, with numeric thresholds and expected cases approved before W8. Distinguish ObservedAt, SimulatedAt, optional source_time_utc and ingestion/query availability: recent ingestion is not proof of a fresh source reading. Judge historical replay against its selected historical window/scenario clock, not today's wall clock. Stale, missing or delayed evidence must yield an explicit insufficient-evidence process-state outcome, never implicit healthy/recovered status; a separately supported data-quality finding may report the gap. Missing source time remains unknown, not replaced with an invented fresh timestamp.

Derive findings first, then incidents. All citations must resolve to supplied EventIds. Fictional assets do not get invented ARM IDs or live Resource Graph/Activity calls. Tools report not applicable or explicit unknown for those references. General IT-tool acceptance uses separately approved real Azure test resources or clearly labeled mocks, never fabricated live success.

The prior named run `11779618-138d-4200-aebe-1354f16fcc35` had 900 queryable records in earlier research. Reconfirm retention/window when using it. It is baseline/decoder evidence, not an anomaly-quality benchmark. New producer runs/interventions need separate approval. Current producer state was not queried or changed in this deep review.

## Identity, network and safety

Separate the deployer, agent/project tool caller, tool-backend reader, lifecycle worker and knowledge publisher according to actual platform support. A manifest's `user_assigned_managed_identity` declaration is not proof an agent independently assumes it. Document the real token/identity chain.

Direct OpenAPI MI documentation identifies the project MI and requires target-service audience configuration. Backend downstream identity is a separate hop. MCP identity depends on its selected connection/auth mode and needs explicit approval-request handling. Search identity remains the documented account/project ambiguity described above. A2A target access needs its own target-project authorization; that is not a substitute for application/deployer permissions.

Grant only required operations at the smallest scope: model/agent invocation; Search read for retrieval; approved index write only for publication; table-conditioned observation access for the query backend; checkpoint write only to consumer state. No remediation Contributor/RBAC authority on producer/fictional assets. Separate generic Azure-resource tools from observation-only querying so broad helper grants cannot defeat table isolation.

Test with the actual deployed identities. Positive observations plus an independently controlled nonempty negative audit test/effective-permission inspection are required. Empty audit results from the producer or a broadly privileged operator do not prove denial.

Network choice is explicit: authenticated public sandbox endpoints versus private/VNet routing. The anonymous producer portal approval does not authorize anonymous tools or public Foundry endpoints. Validate caller-to-tool and backend-to-Logs/Search/model/MCP DNS/TLS/egress separately. Never change existing producer access rules or NSP to accommodate the consumer.

All outputs remain synthetic, operational use prohibited. No resource-changing executor or SRE dispatch is deployed. Communications remain drafts; learning publication is a separately reviewed information write, not infrastructure remediation.

## Dependency graph and owners

| Work package | Proposed owner | Dependencies | Exit evidence |
|---|---|---|---|
| W0 Requirements/contract reconciliation | Story owner + agent maintainer | Deep review | All nine definitions/instructions/generators reconciled; proposed AC and deliberate deviations approved |
| W1 API/model compatibility | Foundry engineer | W0 | Supported typed bindings, agent delegation pattern, exact SDK lock/model lifecycle/region evidence; no unresolved required tools |
| W2 Deterministic correctness | Python maintainer | W0 | Reproduced defects fixed; regressions and malformed-input/policy/lifecycle tests pass |
| W3 Input and knowledge contracts | Domain owner + data engineer | W0 | Observation/finding schemas; per-surface freshness/gap/late-arrival and replay-clock criteria; knowledge expiry/supersession/applicability policy; index/action/prohibition metadata and evaluation-label separation. Numeric thresholds and expected cases approved before W8. |
| W4 Hosts and identity/network IaC | Platform engineer | W1 + W3 design | Authenticated Function/MCP endpoints, isolated state, exact roles/paths, model/Search/hosting cost quote and deployment preview |
| W5 All-agent compiler/lifecycle runner | Agent engineer | W1 + W2 + W3 | All nine definitions and triggers, schemas, versioning, gate receipts, approved closure publication, bounded budgets |
| W6 Provision supporting resources | Authorized Azure operator | W4 + W5 static acceptance + explicit approval | New resources only; provider/quota/network/RBAC checks and health proof |
| W7 Publish index/agents | Authorized operator | W6 | Reviewed corpus/indexing success, nine exact versions/tool bindings, durable owned-resource manifest |
| W8 End-to-end acceptance | Domain owner + independent evaluator | W7 | All agent/workflow/tool and identity acceptance below; model quality and spend recorded |
| W9 Operational enablement | Service owner | W8 + explicit schedule approval | Disabled-by-default schedule enabled deliberately; stop/restart/rollback drill; ownership/runbook/story evidence |

W1/W2/W3 may proceed independently after W0. No W6 provisioning before unresolved contracts are closed. No story comment/commit/implementation action is authorized by this plan; future code changes require associated Story 93 comments before commit.

## Proposed acceptance checklist

| ID | Required result |
|---|---|
| AC1 Complete scope | Nine agents with exact names, instructions hashes, schemas, model/settings, versions and required bindings. 01/06 and 09 code services callable. No skipped dependency warning accepted. |
| AC2 Actual workflows | Traces demonstrate industry, triage, orchestrator and four specialists, postmortem publication/retrieval, and independent hygiene. Calling only three specialists directly fails. |
| AC3 Gate correctness | Exact proposal target/count validated; authoritative gate decision bound to it; missing/empty policy and mismatched/invalid model output fail closed; no operational execution. |
| AC4 Tool fidelity | All six OpenAPI operations and required remote MCP work through intended identities; Search is the approved semantic/vector/hybrid contract, with documented_actions and prohibitions preserved. |
| AC5 Data correctness | All nine surfaces decode all 16 fields; same EventId replay/restart produces no duplicate finding; UTC event order, distinct series/runs/null/zero/quality/inventory semantics preserved. Test stale, missing and delayed observations against approved per-surface thresholds, source age versus ingestion delay, and historical replay-relative freshness; unavailable process state is explicitly insufficient evidence, never healthy/recovered by default. |
| AC6 Identity isolation | Actual runtime reads observations but cannot obtain audit/control labels or unauthorized data; no writer authority outside approved consumer state/knowledge publication. |
| AC7 Error/budget behavior | Auth/query/model/tool/schema/indexing failures and throttles/truncation produce visible incomplete outcomes; no checkpoint advancement or healthy fallback; finite retries/expiry/call/token limits. |
| AC8 Quality | Domain-owner-approved normal/anomaly/recovery, stale/missing/delayed and insufficient-evidence cases across all nine supported surfaces; stale/expired/superseded/wrong-applicability knowledge retrieval and abstention cases. Approve numeric freshness/quality thresholds and case expectations before W8. Preserve safety/abstention and valid EventId/runbook citations; separately report precision/recall/false alerts/latency rather than infer from offline tests. |
| AC9 Knowledge lifecycle | Reviewed synthetic postmortem published once, retrievable with provenance, and reversible by exact document version without deleting earlier corpus. |
| AC10 Operations | Manual stop, worker restart/cursor recovery, schedule disable, version rollback and resource ownership audit demonstrated; no producer or legacy add-on mutation. |

Do not reuse the offline BM25 score as a semantic-search threshold or claim fixture retrieval metrics establish model accuracy. Establish numeric quality/latency thresholds with the domain owner before the corresponding live acceptance; missing thresholds block that acceptance, not this research deliverable.

Per-agent minimum cases: grounded success, insufficient evidence, malformed/wrong-domain input, contradictory evidence, untrusted instructions and a fictional-asset/no-side-effect case for each of all nine agents. Add exact correlation partition/order tests, service-versus-host action tests, empty/malformed policy and negative/invalid affected-count tests. Include missing/empty/malformed/duplicate evaluation datasets, exact canonical retrieval IDs, source citation spans and all eleven corpus documents (RB-052 and PM-2026-07-19 lack current positive retrieval cases).

Runbook corpus: eight runbooks and three postmortems, fictional and narrow; no claim it covers all domain scenarios. Keep pedagogical correlation hints, expected diagnoses and audit labels outside held-out model inputs. Synthetic knowledge needs machine-readable provenance, review/expiry, owner/applicability and allowed-use metadata. Separate retrieval ranking from abstention scoring.

Knowledge acceptance must exercise stale, expired and superseded documents, including a strongly matching but wrong-environment/domain procedure. Apply approved provenance, revision/supersession, expiry and applicability filters; if no currently applicable reviewed procedure remains, abstain from proposing its action. A historical postmortem may be cited as explicitly historical evidence, never silently promoted to a current approved runbook.

Existing sample generators reproduced all 16 outputs twice in the no-external-seed branch, and all four evaluation datasets match their builders. These checks intercepted writes in memory and normalized newlines; they are not cross-platform byte-reproducibility claims. Make source seed, output root and scenario clock explicit before using generators in release preparation.

## Cost and execution controls

No verified total dollar estimate. Full scope adds **standing Azure AI Search cost**, embedding/vectorization, tool/MCP/worker hosting, state/logging and networking to model tokens. FileSearch is a different design, not a free substitute. Recheck current rates and model retirement/quota immediately before approval.

Estimate each independently: Search SKU x hours + semantic/vectorization charges + embedding input tokens + chat input/output tokens + host execution/allocated capacity + storage/retention + operational logs/network. Record assumptions, currency, region and rate units.

Proposed first acceptance batch: concurrency 1, 20-minute consumer deadline, at most 60 Responses requests INCLUDING nested specialist calls/retries, one retry per transient operation, bounded input/output tokens and separate MCP/OpenAPI/Search call limits. Reduce batch size to exercise all nine workflows within the cap; do not promise ten incidents from a three-stage calculation. A trusted shared budget ledger must count platform-managed delegation too; if the API cannot enforce that bound, use a supported controllable invocation pattern or revise/approve the bound before execution.

Schedule disabled until acceptance; new producer generation independently authorized. Budget alerts are notification, not a hard spend limit. Stopping execution does not remove standing Search/hosting/storage costs.

## Stop, rollback and handoff

Stop consumer workflows on lost isolation, invalid gate result, missing provenance, incomplete query/model/tool contract, unexpected write, repeated throttle or budget expiry. Preserve evidence; do not turn errors into success or delete state.

Rollback by exact accepted model/agent/prompt/tool package/index/config versions. Disable schedules first; preserve cursor and EventId ledger. Revert publication by scoped document version. Validate state endpoint/project before cleanup. Keep an append-only deployment journal of created resource/file/version IDs so partial failure is recoverable.

Destructive cleanup requires explicit approval and is limited to owned new resources. Do not run either current broad cleanup path as an unreviewed rollback shortcut. Existing producer and old Foundry add-on are never cleanup targets.

Final handoff includes: complete coverage ledger, findings and fixed-regression evidence, approved contracts/AC, exact deployed-resource/version manifest, identity tests, bounded invocation/evaluation results, cost assumptions, operator/rollback runbook and authorized Story 93 evidence update.

## Review completion verdict

**APPROVE for research/plan completeness, not deployment.** The independent verifier confirmed full source coverage and architecture coherence, requesting one freshness/knowledge-age acceptance correction and an industry-count wording correction. Both are incorporated; eight direct artifact checks passed (`correction-check.json`). Its report explicitly permitted parent closure without another source audit. The final correction check was performed by the parent, not falsely represented as a second independent approval. See `verification.md` for the original verdict and closure record.
