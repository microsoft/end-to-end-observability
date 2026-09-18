# Domains V2 to Foundry: an outcome-processing system

Story 93 | Design revision 2 | 2026-09-14 | Proposed architecture, not deployed

## Executive Summary

The goal is not nine agent registrations. It is a repeatable journey from synthetic observations to a reviewable, evidence-backed incident, a safe response proposal, communication drafts, and reusable reviewed learning. The user selected all five outcomes and a **separate agentic-itops review interface**. Domains V2 remains a producer; it receives no Foundry code, output tables, review endpoints or execution hooks.

The previous plan correctly identified missing runtime/tool hosting, unsupported deployment bindings and correctness defects. Its main product gap was that it did not sufficiently define how Foundry results become durable, trustworthy and useful to a person. This revision adds an outcome-processing layer, transactionally persisted lifecycle, authenticated review application, publication workflow and measurable end-to-end acceptance. It retains all prior deployment blockers; a better design does not resolve them.

The boundary is unconditional: `IsSynthetic=true`, `OperationalUse=prohibited`. An operator can accept an analysis or approve a synthetic knowledge publication, but cannot authorize real remediation, physical asset dispatch, tickets or sent communications from these records.

## Architecture Design

The editable logical flow is in `architecture-diagram.drawio`; an Excalidraw
source is also retained with the session review artifacts. Arrows represent
workflow handoffs, not unrestricted agent-to-agent authority.

### Recommended separation of responsibilities

| Plane | Responsibility | Proposed Azure implementation |
|---|---|---|
| Existing producer | Publish neutral observations on nine surfaces | Existing Domains V2 App Service, DCR and Log Analytics; unchanged |
| Evidence and detection | Read bounded observations; validate, deduplicate, build features and detect supported changes | Separate Python lifecycle worker on Azure Functions; observation-reader identity |
| Reasoning | Invoke every defined prompt agent in its appropriate workflow | New Foundry project, approved model deployment, four supported A2A specialist connections |
| Tools | Authenticated normalization, evidence lookup, context, gate and hygiene | Separate Azure Functions tool host; real remote MCP endpoint with transport/identity compatibility gate |
| Outcome processing | Validate every result, bind evidence/gate receipts, commit state and issue review work | Trusted Python services; Azure SQL transaction boundary; Blob evidence store |
| Operator experience | Browse findings, investigate evidence, review proposals/drafts and inspect failures | New Entra-authenticated Python web/API application on App Service; no shared producer identity |
| Learning | Publish only approved synthetic document revisions and retire them safely | Separate publisher Function identity; Azure AI Search index and embedding capability |

This deliberately chooses SQL for relational identities, unique constraints, compare-and-swap reviews, and a transactional work outbox. Blob holds immutable evidence bundles and large response artifacts. SQL, not Search, a conversation thread, browser state or Log Analytics, is the source of truth for workflow state.

Initial sandbox proposal: one App Service web/API app; separate Function apps for worker, tools and publisher; one SQL database; one consumer Blob storage account; Search; Foundry and model/embedding deployments. Function host storage is additional where required. A remote MCP transport may need its own compatible host; prove that before pricing/provisioning. These are functional isolation boundaries, not nine agent containers. Actual SKU, region and network posture need preflight and approval.

Use a SQL-backed work queue/outbox initially, avoiding another broker and a distributed transaction at this scale. Manual intake creates durable work; a bounded relay/worker is invoked explicitly. Scheduled polling remains disabled until approved. Later Service Bus is justified by independent throughput/fan-out requirements, not required merely to demonstrate agents. Never keep a database transaction open while waiting for a model or external tool.

### Alternatives considered

| Alternative | Benefit | Reason not selected as default |
|---|---|---|
| Send every raw row straight to an LLM | Small initial adapter | Poor temporal state, unnecessary cost, weaker reproducibility and label/identity control |
| Deterministic detector plus Foundry investigation plus trusted result processor | Reproducible detection, bounded context, useful reasoning and auditable outcomes | Selected; requires explicit contracts rather than a shortcut |
| LLM-only central coordinator writes all state and publications | Less application code | Lets generated text imply authority and makes retries/approval integrity difficult |
| Integrate review pages into Domains V2 | Familiar UI | User explicitly selected a separate interface and producer-only boundary |
| Deploy nine definitions and use the existing local three-agent loop | Appears fast | Does not meet the required runtime or downstream outcome |
| Blob/Table-only state | Lower initial database footprint | Multi-record review, checkpoint and outbox consistency becomes application complexity |

### What Foundry contributes

Foundry provides grounded specialist reasoning, model access, managed agent/tool interaction and supported orchestration connections. Search contributes reviewed institutional memory. Structured output support, application validation, tracing and evaluation help constrain and observe those interactions. None makes raw observations into validated incidents automatically.

The trusted application still owns detector policy, exact identity, lifecycle state, budgets, effective authorization, result acceptance and publication. Conversation memory is not approved operational knowledge. A model's confidence is self-reported, not a calibrated probability. Public capability details and remaining SDK gates are recorded in the companion evidence ledger; current bindings must be proven before provisioning.

| Foundry capability | Use in this solution | What it does not replace |
|---|---|---|
| Agent definitions and Responses | Reusable specialist behavior plus actual executions and output items | Durable incident/review state or proof that a deployed agent contributed |
| Tool calling and continuations | Correlate requested tool calls and returned results by actual call IDs; handle explicit pending/failed output items | Application argument/output validation, permission checks or business approvals |
| A2A specialist integration | Preserve the real orchestrator and four specialist relationships | A proven global budget or intermediate validation boundary without the P0 compatibility work |
| Azure AI Search grounding | Retrieve scoped, currently eligible reviewed knowledge with exact provenance | An automatic write-back/learning authority or source-independent truth |
| OpenTelemetry/Application Insights tracing | Correlate parent/child agent invocations, tools, latency and available usage; expose contributions in the review UI | SQL commit readiness; traces can arrive asynchronously and semantic conventions evolve |
| Cloud evaluation | Optional separately approved groundedness/coherence/task-completion scoring of selected conversations | Isolated per-agent benefit, deterministic gate checks or domain validation |
| Conversations / managed memory | Conversations are optional context; managed memory is a separate preview experiment, disabled for this rollout | Approved postmortem knowledge, user isolation by assumption, or indefinite continuation lifetime |

Inspect response items, not only `output_text`. Preserve the distinction between
MCP tool-access approval, operator acceptance and publication approval. Long
human review happens after a durable artifact is committed; verify selected
continuation expiry and expired-state recovery rather than holding a tool call
open indefinitely. A documented function-parameter strict flag or evaluation
agent-version filter is not proof of final-response schema or version-pinned
invocation support.

For quality attribution, record which specialist supplied, contradicted or
rejected each material claim. Actual invocation proves participation, not
usefulness. Use role-specific case rubrics in addition to any conversation-level
cloud evaluation; its agent filter can select conversations containing that
agent without isolating its contribution. The source dates and exact limits
are preserved in the session companion `foundry-capabilities.md`.

## Data Flows and Integrations

### 1. Ingest complete evidence, not alerts

Read only `EnergyObservations_CL` through the new consumer identity. Use the producer's 16-column projection and decode dynamic cells by response metadata, including nested datetimes. Preserve Payload and Context verbatim in normalized JSON along with the source record digest. Discover an exact opaque RunId and select a bounded absolute window; do not assume every surface shares a run.

Identity is the structured tuple `[Domain, Surface, RunId, RecordType, EntityId, LocationId, measurement, tag_id, phase]`, retaining null components. Persist producer/workspace identity separately. EventId deduplication is scoped to that producer, not to whichever browser initiated analysis.

Use overlapping ingestion scans and explicit late-arrival reconciliation, not `ObservedAt > cursor` alone. Store query bounds, requested window, actual coverage and completeness. A timestamp-at-boundary overlap is mandatory. Missing ingestion timestamps or responses too large to partition safely produce visible incomplete work and an operator catch-up request. Never silently use `take` or equate no returned rows with healthy.

On each complete subwindow: write and verify the immutable Blob evidence object first; then in one SQL transaction insert unique received-event references, detector work and the committed subwindow/cursor. An orphan Blob after SQL failure is safe and later reconciled; a cursor pointing at missing evidence is not. On partial/error/truncated query, commit no success cursor for that subwindow. Retries use the same logical window key; overlapping evidence does not create duplicate facts.

Distinguish the ingestion checkpoint (evidence durably received) from processing progress (detector/agent work completed). A model outage must not discard accepted evidence or require the producer to republish it. Pending work remains durable and visibly incomplete.

### 2. Convert observations into supported findings

The first analysis stage is deterministic, explainable and versioned. Use per-surface feature adapters, warm-up requirements, source-quality checks and domain-owner-approved detector rules. An LLM can suggest a pattern for review but cannot silently create a new deployed threshold.

| Surface group | Supported design direction | Forbidden inference |
|---|---|---|
| Power grid | Same-meter quantity relationships; persistent condition/loading changes, qualified by measurement meaning | Nominal rating as an approved operating limit; night-time PV zero as automatic outage |
| Upstream | Per-well/lift changes only where source context establishes that relationship | Combine unrelated separator, compressor and well readings as one production stream |
| Midstream | Sustained flow/command-feedback/condition changes; distinguish measured from derived quantities | Treat derived mass flow as independent corroboration; prove a mechanical cause from one signature |
| Downstream refinery | Trends in actually supplied pressure/temperature/power/availability and condition indices | Fabricate absent flow or vibration channels |
| Downstream portfolio | Changes in explicitly named index/equivalent-volume quantities | Interpret indices as physical pressure, barrels or independently calibrated sensors |
| Four inventory surfaces | Per-SKU/location baseline deltas, unavailable quantities and observed delivery/quality state | Universal quantity-under-ten rule, available-to-promise stock, or inferred equipment/part compatibility |

The current local contract also documents optional `Context.details_profile="domain-details-v1"`. It enriches context without changing wire SchemaVersion 1. Legacy retained rows can lack it. Accept both explicitly, but version detector semantics and never combine changed measurement selections across runs into one baseline. Its presence in source does not prove the deployed producer emits it.

A finding records the computed condition, rule/version, features and units, reference window, baseline readiness, quality/coverage and evidence EventIds. Classifications are `normal`, `candidate_anomaly`, `data_quality`, `insufficient_evidence`, or `unsupported`. Only supported findings enter canonical incident adaptation. Missing or stale observations never assert recovery.

Correlation is deterministic and order-stable, constrained by producer/run/domain/time and an explicit relationship map. No fabricated ARM IDs. No cross-domain or inventory-to-equipment join merely because names resemble each other. Late evidence produces a new analysis revision and explicit correction/supersession links; it does not erase history or silently change an accepted review.

### 3. Exercise all nine agents

| Agent | Trigger and input | Required contribution and result consumption |
|---|---|---|
| `10-industry-signal-pack` | New run/domain or context-policy revision; typed fictional domain context | Energy/utilities applicability and additional prohibitions; retain all six declared industry choices. Cache only by explicit context/prompt/knowledge versions. |
| `02-triage-dedup` | New or materially revised correlated incident | Severity/business context and suppression recommendation. Deterministic identity remains authoritative; code rejects prohibited suppression. |
| `00-ops-orchestrator` | Accepted triage handoff and evidence bundle | Actual prompt coordinator for its four specialists. Runtime checks required steps and gate receipts; deployment alone is insufficient. |
| `04-runbook-memory` | Orchestrator's first investigation step | Applicable reviewed runbooks/postmortems, canonical IDs, revisions, citations and prohibitions through declared Search hybrid semantics/top_k 3. |
| `03-rca-investigator` | Memory result plus bounded observations/tool evidence | Ranked hypotheses, supporting and contradicting evidence, missing data and confidence qualification. Tool failures remain visible. |
| `05-remediation-planner` | Validated RCA plus applicable runbook actions | Typed response proposal with exact logical target/component, preconditions, blast radius, prohibitions and rollback; explicit no-action/insufficient-evidence is valid. |
| `07-incident-comms` | After actual code-gate receipt | Audience-specific drafts reflecting evidence and authoritative decision; no send tools or dispatch identity. |
| `08-postmortem-drafter` | Deliberately closed/reviewed incident revision | Draft with observed timeline, hypotheses distinguished from conclusions, and no fabricated executed action. Publication is a different identity/workflow. |
| `09-alert-hygiene` | Explicit bounded history-analysis job | Interpret deterministic scores from detector-derived firing/recovery/review history; proposals only, never automatically change thresholds or suppression. |

Signal Normalizer 01 and Change-Safety Gate 06 remain deterministic services. Hygiene 09 retains its deterministic implementation as well as its prompt role. The orchestrator's four specialist relationships remain memory, RCA, planner and comms; industry/triage/closure/hygiene are explicitly outside that subgraph.

Sequence: memory -> RCA -> planner -> real gate -> comms. Modern Foundry transport is a compatibility migration, not permission to remove agents. Preferred path is supported A2A to four specialists, with structured JSON carried within its supported text response. A trusted coordinator validates every stage. Do not deploy the classic connected-agent descriptors into a modern API by assumption.

**P0 control spike:** direct managed A2A is acceptable only if intermediate
validation, trusted workflow correlation and bounded accounting can be proven.
Otherwise evaluate authenticated A2A gateway facades over the four real
Foundry specialists. A facade can reserve budget and validate each returned
outcome before synthesis while preserving the actual 00 prompt orchestrator;
this is a conditional application architecture, not a turnkey native feature.
Prove both authentication directions, exact agent invocation, replay protection
and durable pending/error behavior. No model-supplied workflow/budget field is
trusted. A gateway only bounds mediated requests; hidden model/tool fan-out and
alternate paths still require separate limits. If neither composition meets
the contract, stop for an architecture decision. Do not claim that final-only
validation or four gateway calls proves a globally bounded chain.

For generic Azure tools, fictional entities return typed `not_applicable`, never fake Resource Graph success. Real tool acceptance requires a separately authorized real test scope. Preserve and tighten all six declared operations: `normalizeSignals`, `runKqlQuery`, `getResourceContext`, `getRecentChanges`, `evaluateChangeRequest`, `analyzeAlertHygiene`. Observation queries use an allowlisted query compiler/parameters and table isolation; unrestricted model-supplied KQL is not authorized. A changed operation contract must update its emitter, generated specification, agent instructions and tests together.

### 4. Process Foundry outputs before showing them as accepted results

The model output is an untrusted candidate, not the incident record. On each stage:

1. Record stage, attempt, selected agent version, deployment/prompt/tool/index versions and external response/conversation IDs where supplied.
2. Parse the complete response into a strict schema; refuse incomplete JSON, unknown enum values, tool errors disguised as prose and truncated evidence.
3. Resolve every citation against authorized immutable EventIds or exact knowledge document revision/span. Evidence existence and semantic support are separate checks; schema validation alone cannot establish a claim is true.
4. Verify scope, units, domain/run, synthetic provenance and required sequence; apply deterministic prohibitions.
5. For proposals, call the trusted gate with the exact canonical proposal hash and authoritative target/radius. Persist a receipt containing proposal/evidence/policy versions, trusted time, expiry and decision. A model-authored `ALLOW` is not a receipt.
6. Commit the validated candidate or explicit failed/incomplete stage, plus the next work item and operator projection, in one SQL transaction.

The synthetic overlay always sets `execution_allowed=false` and a non-overridable prohibition reason. If useful, a separate `policy_assessment` can explain how a hypothetical request compares with a rule, but neither UI nor API exposes an execution action. A proposal edit invalidates its previous gate receipt and review approval.

Successful RCA followed by failed planner/comms remains visible as partial work with the affected stage marked incomplete. Do not replace failure with a harmless-looking annotation proposal or an overall green success. Provider prompt-injection/content-safety blocks are likewise distinct failures/blocks, not diagnoses.

### 5. Persist typed records, not one opaque conversation blob

| Record | Required durable content |
|---|---|
| `EvidenceBundle` | Producer/run/surface, all source fields, ordered EventIds/digests, query bounds, coverage/completeness, reference clock, Blob hash and schema/profile |
| `FindingRevision` | Logical finding/episode ID, detector version, normalized feature/units, condition, baseline, evidence refs and supersession |
| `IncidentRevision` | Stable incident ID, exact finding membership, observed lifecycle, hypothesis/triage refs, current revision and safety overlay |
| `AgentStageAttempt` | Workflow/stage/attempt IDs, agent/model/prompt versions, input hash, response ref/hash, tools/latency/usage, status/error |
| `ResponseProposal` | Exact action class and logical component target, affected count, preconditions, evidence/runbook refs, rollback, proposal hash |
| `GateReceipt` | Proposal/evidence hash, policy version, trusted timestamp/expiry, technical assessment and immutable no-execution overlay |
| `ReviewDecision` | Authorized actor, role, action, reason, exact reviewed revision/hash, timestamp, expected version and idempotency key |
| `PublicationRevision` | Reviewed draft hash, evidence lineage, synthetic/applicability/expiry metadata, approval, embedding/index version and searchability state |
| `WorkItem/Outbox` | Logical job key, dependency, lease/fencing token, attempt budget, not-before/deadline, next action and error |

Required result header: `schema_version`, `result_id`, `incident_id`, `analysis_revision`, `workflow_id`, `status`, `source`, `evidence_refs`, `stage_results`, `versions`, `created_at_utc`, `IsSynthetic=true`, `OperationalUse=prohibited`, `execution_allowed=false`. Validate nested records, not merely this header. Narrative text may accompany these fields but cannot override them.

Use separate status axes: processing (`queued/running/blocked/incomplete/completed/failed/cancelled`); observed lifecycle (`candidate/active/recovery_observed/resolved/unknown`); human review (`pending/accepted/rejected/needs_evidence/closed`); publication (`draft/approved/publishing/searchable/failed/revoked`). Administrative closure does not change observed recovery. `resolved` requires the approved detector recovery rule and coverage, never a Stop event. These vocabularies are proposed contracts, not existing fields.

A completed analysis can legitimately return insufficient evidence; completed
means the analysis procedure finished, not that the process is healthy. Failed
queries or incomplete source windows remain incomplete work, not evaluated
normal cases. Physical execution is always `not_performed`; a reviewed
hypothesis remains a hypothesis.

#### Corrections and eligibility transitions

Maintain reverse lineage from EventId/evidence bundle to finding/incident
revision, proposal, draft, publication and every workflow that consumed that
knowledge revision. A separate eligibility record can invalidate an immutable
artifact without rewriting its historical content or actor decision.

| Trigger | Historical record | Current eligibility and next work |
|---|---|---|
| Contradictory late evidence after an accepted review | Preserve old evidence, result and acceptance against their exact revision | Create a superseding analysis revision; mark prior conclusions/reviews stale for current use; recompute proposal/gate and require a new review |
| Contradictory evidence after administrative closure | Preserve closure as a historical action, not a current physical-state assertion | Mark incident `needs_evidence` in review and open correction work; detector independently sets active/unknown/recovery state. Do not silently undo the old closure or declare recovery |
| New supported firing after a previously resolved episode | Retain the old resolved episode and decision history | Create a new episode with an explicit recurrence link; only a demonstrated correction of the old episode uses supersession rather than a new firing |
| Correction of evidence supporting searchable knowledge | Preserve the reviewed publication as history, subject to retention/erasure rules | Immediately mark that revision and derived knowledge descendants ineligible; pause/invalidate consuming jobs, exclude/remove from Search, then regenerate and re-review before new publication |
| Proposal edit or expired gate receipt | Retain original proposal/receipt/review history | New proposal hash or fresh gate evaluation; no reuse of an old acceptance. Dependent drafts become stale until revalidated/reviewed |
| Valid typed no-action/insufficient-evidence proposal | Preserve explicit reason, evidence limits, action class `none`, null target and zero affected count | Code validates this distinct schema branch and persists a non-executable/advisory receipt. Comms may explain uncertainty/no action; do not coerce it to annotation |
| Malformed proposal, missing policy or failed gate | Preserve failed attempt and error, not a valid proposal | Main comms stage stays blocked; UI shows a deterministic failure notice. No invented gate receipt or success-shaped draft |

An accepted review therefore remains auditable but is not permanently eligible
after its evidential basis changes. Never auto-publish a corrected postmortem
or tune a detector from the correction.

Unique keys provide effectively-once committed results despite at-least-once work delivery. Leases need fencing tokens checked on every commit; a worker that loses its lease cannot publish. Unknown model-call completion after timeout is recorded, reconciled if supported, and counted conservatively; do not claim exactly-once billing or auto-retry a potentially charged call without accounting.

### 6. Operator review and governed learning

The review application is a product surface, not a raw chat transcript. It must show source coverage/freshness, observed timeline, evidence-linked findings, competing RCA hypotheses, actual agent/tool contributions, response proposals/gate reasons, drafts, review history and processing failures. Every screen and export retains a synthetic/non-operational banner.

| Review surface | Required experience |
|---|---|
| Run and finding inbox | Filter exact run/window/domain/surface; show coverage and failures. Distinguish detected anomaly, insufficient evidence, no finding in evaluated window and not evaluated. Counts of observations are not alert counts. |
| Incident workspace | Stable ID, finding membership, observed versus administrative timeline, severity before/after triage, protected classes, and a nine-role contribution panel with honest triggered/not-triggered/pending/failed status. |
| Analysis and proposal tabs | Supporting/contradicting EventId evidence, separate historical knowledge citations, missing checks, exact component target and trusted receipt. Button text is "Accept analysis - no execution authorized", not ambiguous "Approve". |
| Drafts and learning | Compare/edit-as-new-version, review, deliberate closure and postmortem. Show indexing pending/verified/failed/revoked rather than simply "learned". Copy/download retains draft/synthetic provenance and requires export authorization. |
| Hygiene | Show exact history window, detector/rule versions, transition counts, coverage/denominators, protected rules and unknown action history. Queue an offline evaluation proposal only; no live Apply/Suppress control. |

Authorized actions: start a bounded analysis of retained data, request re-analysis, accept/reject/request evidence for an exact revision, edit a draft as a new revision, close with a reason, approve/revoke a synthetic knowledge publication, and request a bounded hygiene report. No producer control, Execute, Send, suppress-live-alert or deploy button.

Suggested API: `POST /analysis-jobs` returns 202 with a durable job ID; `GET /jobs/{id}`; `GET /incidents` with cursor pagination; `GET /incidents/{id}/revisions/{revision}`; `POST /incidents/{id}/reviews`; `POST /publications`; `POST /hygiene-jobs`. All writes require an authenticated application role, Idempotency-Key and expected revision/If-Match where state is revised. Replaying an identical key/payload returns its original receipt; the same key with a changed payload yields a conflict. A concurrent edit yields 409, not overwrite. Invalid input is 422, forbidden access 403, throttling 429, and dependency failure a visible job failure. Same-origin cookie sessions also require CSRF protection.

Review acceptance is an operator judgment, not ground truth. Rejects and edits become evaluation candidates; they never silently retrain a model or tune a detector. Postmortem publication requires a named reviewer, an exact reviewed revision, verified evidence, expiry/applicability and synthetic partition. The publisher has narrowly scoped index write permission; reasoning agents do not.

Search is a derived projection of the publication ledger. Index the approved immutable revision with searchable metadata, then verify retrieval/filter behavior before marking it searchable. Return exact document IDs and revision hashes; retrieval post-validation checks current approval/revocation/expiry before the application uses a result. Because built-in agent Search may see a stale index entry before that check, do not claim immediate erasure from its context: sensitive revocation must pause affected workflows until removal is verified, and affected candidates must be invalidated. No tenant/customer mixing.

Before admitting a workflow, reconcile approval/expiry/applicability for its
knowledge scope and bind an approved index snapshot/filter plus a bounded
workflow lifetime. The selected Search binding must support that restriction.
If it cannot, block acceptance until a supported equivalent is approved rather
than merely filtering the final prose. Revocation or expiry during an active
workflow invalidates affected results; replay starts with clean context. An
old conversation must not reintroduce removed knowledge.

Historical postmortems may inform hypotheses but are not current approved procedures. Never promote a postmortem recommendation into an executable runbook without a separate source-authority review. Exclude a result's own descendants during held-out evaluation; otherwise the system can congratulate itself by retrieving its earlier answer.

Knowledge eligibility also depends on retained supporting evidence and approval
lineage. By default, `eligible_until` is no later than the earliest scheduled
deletion of any required supporting record or the approved document expiry.
Expire/revoke dependent knowledge before that deletion, or obtain explicit
authorization to retain the minimum necessary support longer. There is no
silent retention extension. Legal erasure or unexpected evidence unavailability
immediately makes dependent knowledge ineligible, triggers Search exclusion and
affected-workflow invalidation, and leaves only permitted audit tombstones.
Missing evidence is never replaced with the generated narrative as authority.

Hygiene consumes actual synthetic detector transition history, not every neutral observation and not the old randomized snapshot dataset as operational truth. Count firing transitions and unique episodes with exposure/coverage; repeated active samples are not repeated incidents. Report suppression/review outcome denominators and uncertainty. Recovery following no recorded action means only that no action was recorded, not proven self-healing.

## Security and Governance

Use Entra authentication and server-side human roles: Viewer, Analyst, Reviewer,
KnowledgeReviewer and Operator. The Publisher is a separate service identity,
not an agent or unrestricted human index-write role. A human who authors a
learning revision cannot approve that same revision; a reviewer can independently
approve an AI-authored draft. Enforce tenant/run/scope entitlement on every
record, evidence object and export, not only list-page filters. The browser
never calls Foundry/Search directly with service credentials. Reviewers cannot
change immutable source evidence or grant execution authority. Separate
deployer, worker, tool backend, UI/API and publisher privileges; use managed
identity where supported and approved secret storage otherwise.

The observation identity must read observations and fail an effective negative audit-label test against a known nonempty denied scope. Absence of audit rows is not sufficient. Generic Azure-resource access and observation-only tools must not share broad inherited grants that defeat the boundary. No arbitrary resource URL, query, project, index or Blob key is accepted from generated text.

Same-tenant identity, Search caller identity, A2A target authorization, OpenAPI audience, remote MCP auth/approval, structured-output support and exact version invocation remain blocking compatibility tests. Authenticated public sandbox ingress versus private networking is an explicit pre-deployment decision. Existing producer NSP/access rules must not be weakened.

Logs capture metadata and redacted structured outcomes by default, not full prompts, raw Context or credentials. Evidence storage, SQL, traces, conversations and Search each need documented retention and deletion handling; removing a Search document alone does not erase other copies. The application rejects untrusted instructions from observations, retrieved documents and tool text; policy remains code-owned.

## Non-Functional Requirements

Proposed pilot targets, requiring owner approval before live acceptance:

| Concern | Target and interpretation |
|---|---|
| Source volume | At most 90 observations/minute and 10,800 per two-hour all-surface run under the current producer cap; duplicates and richer payloads add overhead |
| Detection | Evaluate a complete queryable window within two publication intervals; latency clock starts at queryability, not portal injection |
| Reasoning | p95 <=120 seconds from admitted incident work to validated result at concurrency 1, excluding explicit approval pauses; initial target, not a platform promise |
| UI | p95 <=2 seconds to load a persisted incident summary at pilot load; background work remains asynchronous |
| Correctness | No duplicate committed findings/reviews/publications on replay; no false recovery from empty/stale data; every accepted citation resolvable |
| Availability | Pilot is restartable, not a contracted SLA. Durable checkpoints/evidence survive host restart; show dependency degradation |
| Recovery | Restore a committed incident/review/publication chain and resume leases/outbox in a drill; proposed RPO <=24h and RTO <=4h for regional loss, subject to actual backup/region selection |
| Cost bound | First acceptance batch concurrency 1, <=20 minutes and <=60 Responses including nested calls/retries; count tools/tokens separately |

Use resource/version limits and a trusted shared budget ledger. Nested A2A invocations must be metered and bounded or the chosen route remains blocked. Proposed initial per-workflow tool cap is 30, aggregate tool cap 180, and one retry for a transient operation. Token and dollar limits are approved from the selected model's quote before execution. Do not mistake a budget alert for a hard spend cutoff. Pilot quality evaluation needs multiple separately approved batches, not an impossible promise to cover every agent/surface/adversarial case in 60 calls.

A 20-minute ceiling is admission/execution policy, not a guarantee an already-submitted provider request can be instantly cancelled or unbilled. Stop admitting new work at exhaustion, reconcile in-flight calls, record uncertain cost and invalidate late commits through fencing/expiry checks.

## Deployment Readiness and Plan Relationship

This revision replaces the previous plan as the **target product architecture**. The previous deep review remains the source audit and prerequisite defect inventory; its W0-W9 gates are retained and mapped in `delivery-plan.md`. No code, SDK compatibility, live model quality or end-to-end deployment claim is made.

The first usable milestone is a separate review application displaying a persisted, evidence-linked result from retained Domains V2 data, then exercising all nine roles across incident, closure/learning and hygiene workflows. Milestone staging must not become a reduced five-agent release. Only an accepted complete portfolio and full outcome journey satisfy Story 93.
