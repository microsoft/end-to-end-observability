# Independent design verification — Story 93

**Verdict: REVISE — focused design corrections, not a redesign.**
**Confidence: high (0.86) for document-level completeness and consistency.**

Reviewed the Goal Contract, evidence ledger and all seven deliverables in
`outputs\agentic-itops-outcomes\docs`. This is a design review, not implementation
validation, deployment approval or certification. No source re-audit, Azure/model
calls, installations or external record changes were performed. Diagram review
examined its XML nodes, labels and edges, not a rendered visual inspection.

## What fits the goal

- All five confirmed outcomes have a plausible end-to-end path. The separate
  review application and producer-only Domains V2 boundary are explicit.
- The nine actual prompt agents are preserved: 00, 02, 03, 04, 05, 07, 08, 09 and
  10. Their triggers and contributions are stated. Deterministic 01/06 and the
  separate deterministic contribution to 09 are retained.
- SQL authority, immutable Blob evidence, transactional work/outbox, fencing,
  separate ingestion/processing checkpoints and conservative unknown-call
  accounting form a coherent proposed recovery design. Partial work is visible;
  no exactly-once provider/billing claim is made.
- Proposal-bound receipts, revision-specific review, separate publication
  authority and distinct processing/lifecycle/review/publication axes are sound.
- The synthetic boundary is consistently immutable: acceptance cannot authorize
  execution, sending, dispatch, live suppression or producer controls. Recovery
  is not attributed to an unexecuted proposal.

## Required corrections

### R1 — Define correction propagation across closure and published learning

**Evidence:** `solution-design.md`, sections 2, 5 and 6; `delivery-plan.md`,
E09/E11.

Late evidence creates a new analysis revision without erasing accepted history.
Knowledge revocation/expiry invalidates affected workflows. The missing bridge
is what happens when late evidence or an operator correction invalidates an
incident revision that already has a closed review and a searchable postmortem.
The text does not say whether closure remains historical-only, whether the
publication is suspended, or how descendants are identified. Keeping the old
approval unchanged is necessary for audit but insufficient to define its
continued eligibility for learning.

**Correction:** Add a compact transition/dependency table covering late evidence
after accepted review, after closure and after publication. Specify which
artifacts remain immutable historical records, which become stale/ineligible,
which jobs are invalidated, and which require new gate/review/publication
decisions. State the reopening versus administrative-closure policy explicitly.
Include proposal edits, receipt expiry and no-action outcomes in the table so
comms eligibility does not depend on an implicit interpretation of the gate.

**Acceptance addition:** Publish a reviewed revision, ingest contradictory late
evidence, and demonstrate visible supersession plus the specified exclusion or
re-review policy for its knowledge descendants and affected active workflows.

### R2 — Reconcile evidence retention with knowledge validity

**Evidence:** `responsible-ai.md`, Data Retention and Privacy; `solution-design.md`,
result validation and publication; `delivery-plan.md`, E04/E08 and the 100%
known-source citation-validity requirement.

Detailed evidence/artifacts have a proposed 30-day retention, review/version
metadata 90 days, and knowledge has an independently reviewed expiry. There is
no rule preventing a searchable publication from outliving the evidence and
approval lineage required to validate it. After evidence deletion, the stated
resolvable-citation invariant can fail even though the document remains approved.

**Correction:** Choose an explicit dependency-aware retention rule: retain the
minimum authorized supporting evidence/approval lineage while knowledge is
eligible, or expire/revoke dependent knowledge before those records are removed.
Specify behavior for legal erasure and unavailable source evidence without
silently upgrading a postmortem narrative into source authority.

**Acceptance addition:** Exercise expiry/erasure with dependent searchable
knowledge and prove its resulting eligibility, UI status and retrieval behavior.

### R3 — Make all branch results visibly pass through trusted persistence/review

**Evidence:** `architecture-diagram.drawio`, edges 12–16; `solution-design.md`,
sections 4–6.

The diagram sends 09 directly to the review UI and 08 directly to
publisher/Search, whereas the text requires every stage to be validated and
persisted, with publication approval bound to the exact resulting draft.
“Approval required” on the 08-to-Search arrow does not show the post-draft review
and SQL publication state. The main-chain note does correctly acknowledge
intermediate validation, but does not clearly cover these downstream branches.

**Correction:** Route 08/09 outputs through the trusted processor and SQL ledger;
show the postmortem draft review/approval before publisher work. Alternatively,
use an explicit all-branches validation/persistence boundary and label the exact
publication-approval handoff. Keep the companion editable diagram synchronized
if it is distributed; its contents were outside this review's requested seven
deliverables.

### R4 — Complete the quality-scoring contract

**Evidence:** `delivery-plan.md`, E14 and Measurable Acceptance.

The proposed thresholds and case counts are honest, but the scoring unit and
abstention treatment are not fully specified. Episodes, findings and correlated
incidents are different units. Reporting abstention coverage alone does not
state whether abstention on a supported labeled anomaly counts as a false
negative. This leaves release scoring open to inconsistent interpretation.

**Correction:** Define TP/FP/FN matching, scoring unit/window, duplicate handling
and zero-denominator behavior. Count abstention on supported positives in the
recall denominator; predeclare unsupported cases independently of predictions.
Keep coverage and uncertainty reporting.

## Arithmetic and cost checks

- Volume: 9 × 10 = 90/minute; × 60 = 5,400/hour; × 120 = 10,800/run.
- Detector matrix: 8 episodes × 9 surfaces = 72. Agent matrix: 6 × 9 = 54.
- Phase estimates sum correctly to 24–40 person-days.
- With only two labeled anomaly episodes per surface, episode-based recall
  >=0.85 requires detecting both; one miss gives 0.50. The stated small-sample
  caveat is appropriate. The complete matrix is correctly not promised inside
  one 60-Response batch.
- Cost formulas and the distinction between serialized size and billable
  ingestion are appropriate. Standing hosting/Search/state/network charges are
  acknowledged. There is no verified monetary estimate or hard dollar cap:
  those are explicitly unpriced, deployment-blocking approvals, not a design
  success claim.

## Explicit residual deployment gates — do not invent platform fixes

**Managed A2A:** The design explicitly recognizes that intermediate validation
and shared nested-call budgeting may not be controllable. Its requirement that
a trusted coordinator validate every stage is therefore conditional, not proven.
P0 must demonstrate supported pause/validation/continuation and budget accounting
while preserving the actual 00 orchestrator and four specialist contributions.
If unavailable, stop for an architecture decision; final-output validation is
not an equivalent substitute.

**Native Search:** The design explicitly acknowledges stale context exposure
before application validation, requires admission-time scope reconciliation and
a supported snapshot/filter restriction, and invalidates affected work with
clean-context replay. This is substantially stronger than filtering final prose.
P0 must prove those restrictions and revocation/expiry behavior for the selected
binding, including an in-flight race. Otherwise that route remains blocked.
Neither post-validation nor index deletion proves immediate context erasure.

Exact API/SDK/version invocation, agent/tool schemas, model lifecycle and regional
availability, caller identities, effective audit-label isolation, A2A target
authorization, MCP hosting/authentication and Search identity remain unknown.
The pending public-capability research may refine these gates; public evidence
alone cannot establish deployed behavior. Domain policies, regional prices,
retention decisions, quality, latency and recovery targets also remain unproven.

**Disposition:** Resolve R1–R4 in the design before claiming design completeness.
The portfolio, outcomes and immutable non-execution boundary need not change.
Even a subsequent APPROVE would approve only the proposed design, with all
implementation, provisioning, identity, compatibility, spend and live acceptance
gates still closed until separately authorized and evidenced.

---

## Narrow correction follow-up — 2026-09-14

**Updated verdict: APPROVE for proposed design completeness.**
**Confidence: high (0.90), limited to the reviewed document contracts.**

The initial REVISE above is preserved as review history. This follow-up examined
only the R1–R4 corrections, the integrated operator-contract additions and the
two corrected diagram representations. It does not reopen the source audit or
establish runtime behavior.

| Finding | Follow-up evidence and disposition |
|---|---|
| R1 — Correction propagation | **Closed at design level.** The new Corrections and eligibility transitions table distinguishes historical acceptance/closure from current eligibility, recurrence from correction, and proposal edits/receipt expiry from valid no-action and failed-gate branches. Reverse lineage explicitly invalidates derived knowledge and consuming jobs; revised results need fresh review. E09 now tests contradictory late evidence and support deletion after publication. |
| R2 — Retention dependency | **Closed at design level.** Knowledge `eligible_until` is bounded by required evidence/approval retention and document expiry. Longer minimal-support retention requires explicit authorization; erasure or unexpected unavailability invalidates dependencies and affected work. `responsible-ai.md` matches this rule and retains honest provider/backup deletion limitations. |
| R3 — Diagram consistency | **Closed at logical-design level.** Draw.io routes 08/09 candidate outputs through validation and SQL, records exact review from UI to SQL, and shows approved publication outbox plus indexing/revocation status return. The companion Excalidraw has 17 functional rectangles and 21 arrows; inspected corrected-branch coordinates reach the corresponding nodes. Both retain the conditional intermediate-validation/A2A note and no-execution boundary. No rendered visual QA was performed. |
| R4 — Scoring contract | **Closed at design level.** Episode-level one-to-one identity/time matching now defines TP/FP/FN, supported-positive abstention as FN, duplicate predictions as FP, independent unsupported declarations and zero-denominator reporting. Correlation, recovery and latency are scored separately. The two-positive small-sample limitation remains explicit. |

The five operator surfaces now distinguish evaluated versus unevaluated data,
actual role contributions, exact proposal receipts, draft/publication states and
bounded hygiene history. Explicit no-execution acceptance wording, scoped
evidence/export access, immutable revision review, same-key/payload replay and
changed-payload conflicts are consistent with the state design. Human knowledge
review and the publisher service identity are distinguished. Retaining SQL for
the stated transactional/revision/outbox requirements is a coherent design
choice; this review does not establish its selected tier's capacity.

**No remaining design-completeness blocker was identified in this correction
delta.** The initial arithmetic/cost observations and all deployment gates remain
in force. In particular, managed A2A intermediate control/shared budgeting and
native Search restriction/revocation behavior are requirements awaiting proof,
not supported-runtime claims. Exact APIs, versions, identities, models,
hosting, regional quotes, policy values and live acceptance remain unresolved.
The public-capability research was still pending for this follow-up; a material
change in those claims requires a separate narrow delta review.

This APPROVE authorizes neither implementation nor cloud operations. It does
not demonstrate execution, deployment, quality, deletion guarantees or cost
enforcement. The immutable synthetic/non-operational boundary remains intact.

### Final supplied-capability evidence delta — 2026-09-14

**Verdict unchanged: APPROVE for proposed design completeness, confidence 0.90.**

Reviewed `foundry-capabilities.md` as supplied research evidence and the targeted
capability mapping, P0 control-spike, cost and diagram-note changes. The source
links, update dates and research provenance are attributed to the independent
research report; I did not independently fetch or verify those web sources.

The delta does not overclaim that documented integration surfaces prove the
proposed composition works:

- Responses, tool-result correlation and continuations are distinguished from
  application validation and durable business state. Tool-access, operator and
  publication approvals remain separate. Continuation expiry, exact final-output
  schema support and version-pinned invocation are explicitly unverified.
- Tracing proves neither committed readiness nor semantic correctness.
  Conversation-level evaluation is optional, separately approved and not
  represented as isolated per-agent usefulness. Role-specific evaluation remains
  required. Preview managed memory is disabled, not an alternative unreviewed
  learning channel.
- The authenticated A2A facade is explicitly a conditional controller over the
  four actual specialists, preserving the actual 00 prompt orchestrator. Neither
  the evidence note nor the design calls it a turnkey supported configuration.
  Both authentication directions, trusted workflow correlation, replay handling,
  exact invocation and intermediate control remain P0 gates. A mediated request
  limit is correctly not equated to a hidden provider-model/tool-turn limit;
  bypass paths and internal fan-out still require proven bounds.
- Gateway hosting/telemetry/downstream usage and optional cloud-evaluation
  charges are disclosed as additional quote/approval inputs, not free
  capabilities or included acceptance usage.
- The revised logical diagram note makes native or mediated control conditional
  on P0 proof. No topology change was needed for this narrowly described
  controller alternative; this check did not repeat diagram rendering or counts.

The supplied evidence also retains the native Search stale-context limitation,
retrieval-time eligibility requirements and clean-context replay boundary.
No new claim weakens the immutable synthetic/no-execution contract or turns a
review approval into execution permission.

The parent's reported zero architecture-QA findings and 24/24 artifact checks
are artifact-level results, not independently reproduced here and not runtime,
identity, deployment or model-quality evidence. All previously stated
deployment-blocking gates remain closed pending separate authorization and
proof. If neither native nor mediated orchestration can satisfy the required
control contract, the explicit stop-for-architecture-decision condition still
applies.
