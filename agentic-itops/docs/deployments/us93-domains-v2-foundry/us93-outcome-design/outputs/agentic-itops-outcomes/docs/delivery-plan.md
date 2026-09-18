# Story 93: delivery plan for the complete outcome journey

## Scope and Prior Plan Relationship

This plan supersedes the previous deployment plan's target architecture, not its defect evidence or unfulfilled gates. All nine agents, the deterministic 01/06 services, code-plus-prompt 09, real tool bindings and the full workflow remain required. Domains V2 and the old Foundry add-on are not modification or cleanup targets.

The user selected all five outcomes and a separate review interface. This is a completed design deliverable, not authorization to implement, provision or invoke anything. Story 93's fields/comments were not changed.

## Phase Breakdown

Ranges below are engineering person-days, not calendar commitments. Total is 24-40 person-days. They assume one platform/agent engineer and one Python/application engineer with part-time domain-owner and reviewer support; availability, new defects and compatibility spikes can extend elapsed time.

| Phase | Estimate | Outputs and source changes after authorization | Dependencies and exit |
|---|---|---|---|
| P0 Contract and compatibility | 3-5 | Approve schemas, detector semantics, quality targets, network/cost choices. Reconcile manifest/emitter ownership. Prove modern API/tool/version/delegation contract. | Prior W0/W1/W3; exact supported bindings or explicit blocked decision |
| P1 Evidence/state foundation | 5-8 | New observation reader, surface feature adapters/detectors, typed result models, SQL migration/outbox/leases and Blob evidence. Fix existing deterministic correctness defects. | P0 contracts; prior W2/W3; replay/integrity/negative-boundary cases pass offline |
| P2 All-nine reasoning/result runtime | 6-10 | Authenticated six-operation tool host, remote MCP, manifest compiler, industry/triage + actual orchestrator/four specialists, stage validator and exact gate receipts. | P0 API proof + P1; prior W4/W5; every role has an executable contract test |
| P3 Review and governed learning | 5-8 | Separate UI/API, Entra roles, revision-safe reviews, closure/postmortem publication, index revocation and hygiene history/report. | P1 contracts; UI can develop against explicit fixtures in parallel with P2; no fixture result represented as live |
| P4 Provision, prove and hand over | 5-9 | Isolated IaC/package, approved new RG, live identity/tool/agent checks, all-agent outcome acceptance, recovery/rollback drill and operator runbook. | P0-P3 offline gates + explicit spend/deployment approval; prior W6-W9 |

Complete P2 and P3 before calling the release complete. An intermediate UI fixture or manually invoked specialist is development progress, not reduced-scope acceptance.

## Work Breakdown and Existing Defects

The implementation belongs under `agentic-itops`, with its README/docs updated. Proposed new modules cover observation ingestion, detector/feature policies, typed workflow state, persistence, authenticated hosting, publication and UI/API. Final path names should follow the package conventions during implementation; do not add root-repository build tooling.

Repair these existing surfaces rather than papering over them:

- `agentic_itops\orchestrator.py`, `safety_gate.py`, `cli.py`: exact proposal target/radius, strict policy loading, UTC timing, honest failures, complete JSON and code-owned synthetic overlay.
- Adapters/normalizer/envelope/hygiene: array metrics, complete batch expansion, deterministic correlation membership, lifecycle ordering and actual firing/coverage semantics.
- `tools\log_analytics.py`, `runbook_index.py`, `resource_graph.py`, `change_history.py`: real typed responses, partial failure, Search semantics/action metadata, scoped context and explicit unknowns.
- Deployment scripts and emitters: one current manifest-driven nine-agent path, durable resource/version journal, no silent missing tools, strict indexing completion and safe owned-version rollback.
- Tests/evaluations: missing/empty dataset rejection, exact IDs/memberships, supported no-network guard, all-agent/ingestion/result/review/publication quality cases.

Do not mechanically regenerate eight manifests and overwrite accepted folder behavior. Resolve source authority and add a drift check before changing generated outputs.

## Measurable Acceptance

These thresholds are **proposed for approval**, not existing Story criteria or measured performance. Define independent labels before inference. Never use operator injection audit or hidden fixture outcomes as agent features.

| ID | Acceptance journey / invariant | Required evidence |
|---|---|---|
| E01 | All nine surfaces decode legacy and enriched shapes without field/unit/identity loss | Schema/semantic tests and retained/live query evidence; profile deployment confirmed, not assumed |
| E02 | A supported anomaly becomes one stable incident, with exact source EventIds | Detector features/rule version, correlation membership and persisted result visible in separate UI |
| E03 | All nine prompt agents make their actual contribution | Agent/version traces for industry, triage, orchestrator and its four specialists, postmortem and independent hygiene |
| E04 | RCA distinguishes evidence, competing hypotheses, missingness and physical uncertainty | Resolvable citations and domain-owner scoring; no fabricated ARM identity or causal certainty |
| E05 | Proposal is evaluated exactly and remains non-operational | Proposal hash/gate receipt matches edited target/count; fake ALLOW, missing policy and service-to-host substitutions fail closed |
| E06 | Reviewer accepts/rejects/requests evidence on an exact revision | Entra role, actor/reason/time, idempotency, If-Match, 409 on stale edit; no execution/send/producer-control route |
| E07 | Incident update and postmortem drafts are useful but unsent | Approved evidence-backed drafts, explicit unknown/executed-none fields and separate closure basis |
| E08 | Approved synthetic postmortem becomes usable knowledge exactly once | Revision/approval lineage, verified Search availability, later memory retrieval and no inference writer permission |
| E09 | Revocation/expiry, correction and evidence erasure stop accepted reuse | Publish, then introduce contradictory late evidence or delete a required support record: invalidate descendants/affected work, exclude exact revision, require new review and verify clean-context retrieval |
| E10 | Hygiene reports real detector episode transitions, coverage and review outcomes | Repeated active samples count once; empty/sparse history insufficient; no inferred self-healing or automatic rule change |
| E11 | Duplicate/out-of-order/late observations and host crashes preserve work | Stable committed IDs, SQL outbox/fencing drill, immutable evidence and revision-safe correction |
| E12 | Query/model/tool/index errors remain visible, not healthy fallbacks | Partial/truncated results, timeout ambiguity, malformed output, lost lease and publication-retry fault cases |
| E13 | Actual deployed identities cannot expose audit/control labels | Positive observation read and independent nonempty negative authorization/effective-grant evidence |
| E14 | Quality and latency are measured by surface | Proposed precision >=0.90 and recall >=0.85 for supported anomaly cases, with per-surface numerators/denominators and abstention coverage; no aggregate hides a failing surface |
| E15 | Safe stop and version rollback work without touching producer/old add-on | Disable worker admission, reconcile in-flight work, restore accepted package/agent/index versions and demonstrate read-only UI recovery |

Proposed minimum detector matrix: eight independently authored episodes per surface (two normal, two anomaly, one recovery sequence, one stale, one missing and one late-arrival), 72 episodes total. Also add malformed/unsupported, null/zero, incompatible units, misleading rich context and conflicting-evidence cases. Include both legacy and enriched contract coverage without mixing their baselines. Small sample rates are descriptive, not statistical proof of field reliability; publish sample counts and uncertainty.

For every agent, require six case types: grounded success, insufficient evidence, malformed/wrong-domain input, contradictory evidence, untrusted instruction and fictional-asset/no-side-effect. At least 54 agent-level cases are separate from the detector matrix. Exact known-source citation validity and non-operational enforcement must be 100% on release cases; semantic support still requires independent evaluation.

The entire matrix is not promised within one 60-call batch. Price and approve batches independently; reserve enough calls for all nine lifecycle roles, failures and retries. Precision/recall excludes explicitly unsupported scenarios only with the unsupported coverage reported separately. Normal cases must not become a stream of LLM-created incidents.

### Scoring contract

Score detector anomaly episodes before incident correlation, not raw rows or
the number of prose hypotheses. Before inference, the independent evaluator
registers each expected episode's producer/run/surface, exact signal membership,
anomaly class and onset/end window plus allowed detection delay. Use one-to-one
matching of predicted episodes to expected episodes within that declared
identity/time window. Matching rules cannot be changed after seeing predictions.

A matched episode is TP; an unmatched predicted anomaly is FP; an unmatched
supported expected anomaly is FN. An abstention or insufficient-evidence result
on a supported labeled positive is FN. Additional duplicate predicted episodes
are unmatched FP and also fail the separate duplicate-prevention invariant.
Correlation quality is scored separately by exact finding-to-incident membership,
so merging unrelated episodes cannot improve detection scores.

Precision is TP/(TP+FP), recall is TP/(TP+FN). A zero denominator is reported as
not estimable, never 100% or PASS 0/0. A release surface needs its predeclared
positive and normal cases; no predictions on a positive set yields recall zero
and fails. Abstention on a normal case adds no FP but reduces reported decision
coverage. Unsupported cases are declared independently of predictions and
reported with counts/reasons; the system cannot self-label a missed anomaly
unsupported to remove it from recall. Score recovery/false recovery and latency
as separate lifecycle metrics. With only two positives per surface, the initial
recall target requires both detected; publish this small-sample limitation.

## Demonstration Script

1. An authorized operator selects retained observations by exact RunId/window in the new review interface. The UI shows nine-surface coverage, profile and freshness, not a claim that the producer is currently running.
2. A bounded job processes a supported scenario. The operator opens its incident, traces the finding to observation EventIds, and sees industry/triage plus the actual orchestrator's memory/RCA/planner/gate/comms contributions.
3. The operator inspects uncertainty and the no-execution gate. They request evidence or accept an exact revision. A conflicting browser review returns a conflict instead of overwriting.
4. Recovery observations, if present, are assessed by the detector. Administrative closure is separately labeled. The postmortem states that no remediation was executed; producer recovery is not attributed to the agent.
5. A knowledge reviewer approves an exact synthetic draft. The publisher verifies indexing. A subsequent independent incident retrieves it as historical evidence with lineage and expiry, not a newly authorized procedure.
6. The operator runs hygiene on the derived transition history, sees exposure/counts/caveats and reviews a proposed tuning recommendation without changing any live alert.

Retained baseline-only data cannot demonstrate anomaly/recovery accuracy. If retention or coverage is insufficient, request a separately approved producer run or use explicitly labeled offline acceptance cases. Never silently upload the static contract examples as fresh observations.

## Dependencies, Team and Engagement Plan

The story owner approves scope, acceptance thresholds and business usefulness. A domain owner approves measurement meaning, recovery rules and case labels. The platform engineer resolves Foundry API/model/identity/network/hosting and costs. The application engineer implements deterministic state, UI and fault handling. An independent evaluator scores evidence and exercises denied paths.

Hold a contract workshop at P0, an evidence/result walkthrough after P1, an all-nine trace review after P2, an operator/publication rehearsal after P3, and an explicit go/no-go before P4 cloud operations. Handover requires owners, stop/rollback instructions, data retention and an approved cost envelope.

After authorized code changes, post an accurate summary to Story 93 and refresh that comment before any commit, following repository workflow. This review did neither.

## Risk Register and Escalation

| Risk | Mitigation / stop condition |
---|---|
| Modern API omits required delegation/schema/version contract | Stop at P0; prove a supported equivalent preserving all roles or obtain an explicit architecture decision |
| Nested A2A calls cannot enforce shared budget or intermediate validation | Do not deploy an unbounded chain; prove a controllable supported composition without silently replacing the prompt orchestrator |
| Search identity or negative audit isolation unresolved | No roles/resource deployment until effective least-privilege paths are demonstrated |
| Fictional context treated as physical fact | Explicit semantics, no invented joins/limits, grounded hypotheses and permanent no-execution boundary |
| Model returns a plausible wrong result | Schema plus citation/scope/gate checks and independent semantic evaluation; human review does not certify physical truth |
| Feedback loop reinforces its own error | Reviewed provenance, separate source authority, expiry/revocation, lineage-aware held-out evaluation |
| SQL/Blob/Search drift or unknown external completion | Immutable objects, transaction/outbox/fencing, conservative call accounting and reconciler; never discard failure state |
| Costs exceed pilot value | Quote all standing resources, cap admitted work and tokens, measure usefulness before longer commitments |

Stop on lost isolation, invalid gate binding, unsupported source meaning, budget exhaustion or unexplained output/provenance mismatch. Preserve evidence. Resume only after the failed invariant is fixed or a decision within the immutable synthetic boundary is approved.
