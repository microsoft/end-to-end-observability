# Story 93 — downstream result and operator contract

**Independent design recommendation; not implemented or deployment-verified.** Scope is downstream results, review, learning and hygiene. No producer changes, repository inspection, Azure/model calls, installations or external writes were performed. The only authored artifact is this session report.

## Evidence and constraints

- **[G]** `goal-contract.json` in this directory: confirmed outcomes/destination, success criteria, non-goals and immutable risk boundary.
- **[R1]** `..\us93-deep-review\agents-docs-review.md`: B1–B6, I1, I3–I4 establish missing lifecycle contracts, synthetic-use risk, untyped outputs, publication gaps and hypothesis/safety distinctions.
- **[R2]** `..\us93-deep-review\deployment-plan.md`: “Every agent's deployment and workflow contract,” “Runtime composition,” “Correctness fixes are rollout prerequisites,” and “Domains V2 integration acceptance.”

These are session-review findings, not independently reverified source functionality. Original source references below are inherited from R1, not new file inspections. R2 is explicitly a historical prerequisite baseline superseded for target architecture.

**Boundary:** `OperationalUse=prohibited` is server-owned, immutable and non-overridable for this synthetic feed. Review acceptance approves an artifact's quality, never physical action. No resource-change executor, automatic ticket creation or communication-sending capability exists in the proposed application. Even `annotate_incident` cannot authorize an external incident-system write. [G; R1 B3]

## 1. Exactly what the separate interface shows

Propose an authenticated **agentic-itops Review** application, separate from the unchanged Domains V2 producer:

| Screen | Visible content and permitted actions |
|---|---|
| **Run and finding inbox** | Run/window/domain/surface filters; processing completeness, freshness/coverage, validated finding counts and failures. Separate **detected anomaly**, **insufficient evidence**, **no finding in evaluated window**, and **not evaluated**. Observation counts never appear as alert counts. Open source-evidence snapshot or incident. |
| **Incident workspace** | Stable incident ID, detector lifecycle versus operator case status, synthetic/prohibited banner, severity before/after triage with rationale, industry context and protected classes. Evidence timeline, correlated findings and explicit unknowns. Accept/reject/request revision of named artifact versions; deliberately close/reopen the review case with reason. |
| **Analysis and proposal tabs** | Ranked RCA hypotheses with confidence category, supporting/contradicting EventId citations and missing checks. Retrieved knowledge versions in a separate panel. Proposed action, component target, preconditions, blast radius, rollback, source runbook and **trusted gate receipt**. “Accepted analysis — no execution authorized” replaces an ambiguous “Approve” button. No Execute control or executable handoff. |
| **Drafts and learning** | Engineer/executive/customer drafts; deliberate-closure postmortem; edit-as-new-version, compare, accept or reject. Copy/download only with synthetic/draft watermark and provenance. Learning candidates remain unpublished until separately reviewed and published to the synthetic namespace. Show index pending/verified/failed/revoked, not simply “learned.” |
| **Hygiene** | Independent batch report: detector/rule versions, actual recorded firing/recovery transitions, window, coverage, denominators, deterministic scores and model explanation. Protected rules, unknown action history and insufficient-data reasons remain visible. Recommendations are report-only; optional “queue offline evaluation proposal,” never apply/suppress. |

Every incident exposes a nine-role contribution view: invocation IDs, inputs/outputs, versions, timing, validation and truthful pending/skipped/not-applicable/failed reasons. Not all nine must run on every incident, but portfolio acceptance must exercise every role through its proper trigger. [G; R1 B1, B5–B6; R2 Runtime composition]

**Required ordering:** deterministic 01 normalization/correlation → **10 industry** → **02 triage** → **00 orchestrator**, whose specialist sequence is **04 memory → 03 RCA → 05 planner → deterministic 06 gate → 07 comms**. **08 postmortem** runs only after deliberate case closure. **09 hygiene**, code plus agent, runs independently against recorded detector lifecycle history. A simulated workflow ending is not implicit closure. Render unavailable/not-applicable tools honestly; fictional assets never acquire invented ARM identities. [R1 B1, B4; R2]

## 2. Typed durable records and orthogonal states

Use versioned, closed schemas (`additionalProperties=false`), typed nested records, UTC timestamps, bounded strings/lists and enumerations. Unknown values are explicit null/unknown, not zero or empty “success.” The backend validates agent output and source references before publishing a visible result. Raw malformed output is quarantined, not substituted with a plausible plan.

**Common immutable envelope:** `schemaVersion`, `recordType`, `recordId`, `revision`, `scopeId`, `runId`, optional `incidentId`, `createdAt`, `producerKind` (detector/model/human/system), `parentRefs[]`, `payloadHash`, `provenance={synthetic:true, operationalUse:"prohibited", sourceWindow, evidenceSnapshotRefs[]}`, and applicable detector/prompt/model/agent/tool/policy/index versions. References bind **ID + revision + hash**, not latest-by-name. Host-supplied metadata cannot be overwritten by model JSON.

| Record type | Required typed payload |
|---|---|
| `EvidenceSnapshot` | Immutable allowed-observation bytes/reference; content hash; EventIds; observed/simulated/source/availability times separately; source identity; units/quality; selected window and coverage. Missing source time stays unknown. Approved static context and tool responses are separately typed evidence, not observations. |
| `Finding` / `DetectorTransition` | Rule/version, series key, evaluated window, evidence refs, threshold/comparison explanation; stable finding/episode ID. Transition is firing/recovery with actual detector time and supporting samples. Gaps are data-quality states, not recovery. |
| `IncidentRevision` | Correlation-policy version, finding refs, original fingerprint, industry/triage output refs, detector state, operator-case state, severity and rationale. Merge/split/reopen creates lineage; nothing silently disappears. |
| `AgentContribution` | Role, invocation/attempt ID, input refs, output/artifact refs, delegation/tool-call receipts, start/end, validation result and errors. Orchestrator summary references specialist outputs rather than replacing them. |
| `Analysis` | Hypotheses `{id, statement, confidence:low\|medium\|high, supports[], contradicts[], missingChecks[], limitations[]}`; retrieved knowledge refs separately. Confidence is a model category, not a calibrated probability or proof. |
| `Proposal` / `GateDecision` | Proposal: exact action verb, component/service target, target kind, affected set/count, preconditions, prohibitions, blast radius, rollback and runbook/version. Receipt: exact proposal hash, trusted policy/context hash, time/expiry, matched rules and reasons; `policyEffect:ALLOW\|REQUIRE_APPROVAL\|DENY`; separate `operationalAuthorization:PROHIBITED`. Ordinary ALLOW cannot override provenance. |
| `CommunicationDraft` / `PostmortemDraft` | Audience, body/structured timeline, claim-level citations, redactions, unknowns, author/version, authoritative gate and action-state refs. Postmortem binds a deliberate closure revision and separates proposed, simulated and observed events. |
| `ReviewDecision` / `CommandReceipt` | Actor ID and effective scope/role, artifact revision/hash, expected case version, decision/reason, command/idempotency ID, server timestamp and resulting revisions. Model output cannot manufacture either record. |
| `LearningCandidate` / `PublicationReceipt` | Claim-level evidence, uncertainty, source case/closure/postmortem versions, lineage, review refs, synthetic namespace, document ID/version, publication job/attempt, index verification and revocation/supersession refs. |
| `HygieneReport` | Detector-history snapshot/hash, detector/rule versions, event counts and denominators, coverage/minimum-data outcome, deterministic score inputs/results, model explanation, protected rules, recommendations and explicit unknown operator-action history. |

Do not compress these dimensions into one green “resolved” badge:

- **Evidence basis:** `OBSERVED` means a source measurement was supplied **within synthetic data**; `DETECTED` means a versioned detector derived a finding; `MODEL_HYPOTHESIS` remains an inference.
- **Review:** `UNREVIEWED | ACCEPTED | REJECTED | REVISION_REQUESTED`. A **reviewed hypothesis is still a hypothesis**, not an observed fact.
- **Simulation:** `NOT_SIMULATED | SIMULATED`; bind any simulated outcome to a scenario/evaluation artifact, never claim physical action.
- **Physical execution:** fixed `NOT_PERFORMED`; no command may transition it. “Physically executed” is a distinct outcome this application cannot produce.
- **Processing:** `QUEUED | RUNNING | COMPLETE | INCOMPLETE | FAILED | CANCELLED`; coverage is independently `SUFFICIENT | INSUFFICIENT | UNKNOWN`. COMPLETE can honestly contain an insufficient-evidence answer.
- **Detector episode:** `ACTIVE | RECOVERED | UNKNOWN`; **operator case:** `OPEN | IN_REVIEW | CLOSED`. Closing a case does not mark the process healthy; restored telemetry does not prove an action caused recovery.
- **Publication:** `DRAFT | REVIEWED | QUEUED | INDEXING | VERIFIED | FAILED | REVOKED | SUPERSEDED`.

These refinements address reviewed schema and false-resolution gaps. [R1 I1, B3, B5–B6; R2 Correctness fixes and Domains V2 integration acceptance]

## 3. Review commands, concurrency, authorization and evidence

Proposed command API: `AcceptAnalysis`, `RejectArtifact`, `RequestRevision`, `SaveDraftRevision`, `CloseCase`, `ReopenCase`, `ReviewLearning`, `RequestPublication`, `RevokePublication`, `ReviewHygiene`, `RequestOfflineEvaluation`. Every command names scope, object ID/revision/hash, expected aggregate version and idempotency key. None accepts an execution request or editable operational-use flag.

- Use optimistic concurrency: atomically compare expected ETag/version, append audit/decision and update projection plus outbox in the same aggregate partition. Stale commands return conflict with latest version; never silently last-write-wins. Identical idempotency key+payload returns the prior receipt; changed payload with the same key conflicts.
- Publication workers use leases, bounded retries and deterministic document/version IDs. Cross-service writes are **at least once**, with reconciliation—not claimed exactly once. “Verified” requires a successful read-back of the exact indexed ID/version/hash; timeout remains pending/failed, never false success.
- Entra sign-in and server-enforced application roles/scopes: **Viewer** reads; **Analyst** proposes draft revisions; **Reviewer** accepts/rejects/closes; **KnowledgeReviewer** approves learning; **Publisher service** alone writes the designated index. A learning author cannot approve their own candidate. Models have no human roles or publication authority.
- Authorize every API, evidence fetch, search and export by tenant/scope/run entitlement; do not trust browser filters or IDs supplied by the model. Worker, API and publisher identities are separate; no resource-change, ticket or communication credentials.
- Snapshot allowed evidence before evaluation and retain immutable revisions, hashes and tool receipts. Use retention-controlled immutable storage as an implementation requirement; hashes alone do not prevent deletion or prove truth. UI shows evidence unavailable if retention/access expires, and blocks new acceptance/publication that requires unverifiable citations.
- New evidence, revised proposal/target, policy change, expiry or reopened case invalidates dependent acceptance/publication eligibility; historical decisions remain intact. Recompute/review new revisions. Published items implicated by reopening enter retrieval quarantine pending review; prior audit receipts remain.

## 4. Safe publication and hygiene feedback

**Publication is an explicit future approval-gated capability, not proven repo behavior.** Draft → independently reviewed claims → authorized publication job → synthetic-only index → exact-version verification → eligible retrieval. Keep read agents read-only; do not solve the missing write tool by granting all agents Search write access. [R1 B6; R2 Source/API compatibility gate]

Mandatory server-side retrieval filters enforce namespace, synthetic provenance, review/publication status and current non-revocation. A production corpus cannot ingest these documents. Copy/export preserves the same prohibition. Reviewed publication does not “de-synthesize” information.

Prevent self-reinforcement: retain claim-level source lineage; do not count a model output, its paraphrase or a later document citing it as independent corroboration. Exclude the current incident and its derived documents from that incident's grounding retrieval. Cross-incident reuse is explicitly **prior reviewed guidance**, never new observational evidence or confirmed causality. Preserve disputed/missing evidence and negative outcomes. No automatic prompt, detector, threshold, runbook or model updates.

Hygiene consumes **actual recorded detector firing/recovery events**, although the underlying plant observations are synthetic. It does not consume observation-row counts as firings, producer audit labels as truth, or guessed engineer actions. Publish its reviewed report separately from detector configuration. A proposed noise reduction goes through offline evaluation against held-out histories and protected-rule checks; any later configuration change is a separate authorized implementation. Never infer “self-resolved without intervention” when intervention history is unknown. [R1 B5, I3; R2 Runtime composition]

## 5. Minimal concrete hosting recommendation

**Proposal:** one dedicated **Azure App Service** application serving a small server-rendered UI and authenticated review API; Entra authentication; one **Azure Storage** account with Blob evidence/artifacts, Tables for aggregate projections/audit/outbox, and Queue jobs consumed by the separately proposed lifecycle worker. Reuse the proposed Foundry/Search/tool-host architecture, not the producer's Function or identity. Tables transactions require one aggregate partition; cross-aggregate publication uses receipts/reconciliation. Large payloads stay in Blob, not Table rows.

This is a small resource footprint, not a measured lowest-cost or proven-compatible choice. Private access, region/SKU, backup/retention, authorization mapping, availability, deployment compatibility and spend require approval and verification. No provisioning is authorized here.

**Alternatives:** Static Web Apps + Functions for a separate SPA/API when that frontend model is preferred, at the cost of more split-host authentication and network decisions; Container Apps for an already containerized UI/API/worker, with more image/runtime operations; relational SQL instead of Tables if cross-aggregate transactions/reporting justify another service. A workbook can summarize results but is not the default authoritative review-command interface.

## 6. Eight decisive end-to-end acceptance examples

All are **proposed tests**, not results obtained during this design.

1. **All-nine contribution:** bounded synthetic observations produce cited findings; receipts prove 10 → 02 → 00 and 04 → 03 → 05 → code 06 → 07. Deliberate closure triggers 08; an independent lifecycle-history batch triggers code+agent 09. All nine have schema-valid persisted outputs and visible contributions; missing specialist evidence fails portfolio acceptance.
2. **Truth versus appearance:** stale/missing source samples and late ingestion yield explicit insufficient/unknown state, not healthy/recovered. UI distinguishes synthetic observation, deterministic detection, model hypothesis and reviewed hypothesis; unsupported change/topology claims remain unknown. Producer stop cannot close a detector episode.
3. **Unbreakable prohibition:** a reviewer accepts a proposal whose ordinary policy effect is ALLOW. Stored/UI authorization still says PROHIBITED and physical execution NOT_PERFORMED. Crafted execute/ticket/send/flag-change requests are denied, with no external side effects.
4. **Grounding and target integrity:** inject an unknown EventId, invented ARM ID or altered service/host target into otherwise valid output. Backend quarantines invalid claims; old gate receipt cannot validate the revised proposal hash. Missing evidence is surfaced, not replaced with a successful fallback plan.
5. **Concurrency and recovery:** two reviewers act on the same revision; one succeeds and the other conflicts. Replay the first command and crash/retry the worker after persistence. One logical decision/result remains, attempts are auditable, no acknowledged work disappears, and no publication is falsely marked verified.
6. **Isolation and immutability:** a viewer/cross-scope user guesses an evidence URL and submits closure/publication commands; access is denied. Attempted snapshot mutation is blocked by configured storage controls, and hash mismatch fails validation. Every accepted artifact resolves to immutable authorized evidence and the exact reviewed version.
7. **Closure-to-learning without circular proof:** case closure starts a grounded, unsent postmortem; independent review publishes an explicitly uncertain lesson only to synthetic knowledge. A later distinct incident retrieves the exact verified version as guidance; the source incident cannot retrieve its own derivatives as corroboration. Revoke/reopen quarantines eligibility; production and unreviewed retrieval return no such document.
8. **Honest hygiene:** recorded firing/recovery history with sufficient coverage yields reproducible counts/denominators and a reviewed report. Sparse history instead yields insufficient data; no row-to-firing conversion or invented intervention claim. Protected safety/OT rules remain untouched, and any offline-evaluation proposal changes no live rule.

