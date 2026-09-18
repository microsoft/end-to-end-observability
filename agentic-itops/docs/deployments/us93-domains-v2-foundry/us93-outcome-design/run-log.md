# Story 93 outcome-design run

## Outcome and scope

Completed a design review/refinement for all five user-confirmed outcomes in a
separate agentic-itops review interface. Domains V2 remains producer-only; all
nine actual prompt agents and deterministic 01/06/code09 remain required.
The seven architecture deliverables are in
`outputs\agentic-itops-outcomes\docs`. Excalidraw source and supporting research,
review and verification artifacts remain alongside this log in session state.

The previous deep-review deployment plan now points to this target architecture.
Its source defect inventory and unresolved deployment gates remain applicable.
No repository edits, Azure changes/queries, Foundry invocations, uploads,
producer controls, dependency installation, Story updates or commits occurred.

## Evidence and decisions

Reused the same-day 102-file review rather than repeating it. Read the current
producer contract/schema and rich-context documentation; the local optional
domain-details-v1 profile is now covered without claiming it is live.

Two independent work streams supplied operator/result contracts and current
public Foundry capabilities. The parent chose SQL/Blob with a transactional
outbox over the operator reviewer's smaller Tables/Queue alternative because
cross-record review/lineage consistency is central. That tradeoff and unpriced
standing costs are explicit.

The design now separates observation, detector finding, hypothesis, proposal,
gate receipt, review, observed recovery and learning eligibility. It adds five
operator surfaces, typed durable records, exact-version reviews, scoped
publication, reverse-lineage correction/retention handling and measurable
episode-based quality acceptance.

Foundry adds actual agent execution, tools, grounding, tracing and optional
evaluation. The application still owns state, authority and outcome processing.
The conditional A2A gateway option preserves the four actual specialists and
00 orchestrator but does not claim proven authentication/version/budget
compatibility. Managed memory remains an optional disabled preview experiment.

## Verification

Independent reviewer initially returned REVISE with four issues:

1. Propagate late corrections through closure and already published learning.
2. Bound knowledge eligibility by retained evidence and approval lineage.
3. Route postmortem/hygiene branches through validation, state and review in both diagrams.
4. Specify episode TP/FP/FN matching, supported-positive abstention and zero denominators.

All four were corrected in one coherent correction cycle. The same independent
reviewer then returned APPROVE for proposed design completeness, confidence
0.90. It separately reviewed the final public-capability/gateway delta and
retained that verdict. Full history is in `verification.md`.

Final objective checks: 24/24 explicit artifact/contract checks pass; architecture
QA reports zero critical, major or minor findings. The editable diagrams have
17 component nodes and 21 logical edges. XML/JSON structure and text properties
were checked; no browser-rendered visual inspection or application/runtime
validation is claimed. SHA256s for all seven deliverables are saved in
`design-checks.json`.

## Budget and limitations

The declared parent envelope was 45 minutes, 80 parent tool calls and three
delegates. Clock observations span 20:36:53 through 20:59:31 EDT on 2026-09-14
before final handoff bookkeeping, within the envelope. Three delegates were
used; the verifier was reused for narrow follow-ups instead of launching new
reviewers. The manual parent-call tally remained below 80, counting wrapper
calls as well. Token/credit totals were not available and are not presented
as measured usage.

The public researcher reported 15 initial tool calls and no new research on
the gateway follow-up. Its context could return text but not create the
requested file; the parent synthesized its returned evidence into
`foundry-capabilities.md`. Delegate elapsed times include more than the prompt's
suggested ten/twelve-minute estimates; those were not enforceable runtime
timeouts. The overall parent time/delegate-count envelope remained respected.

No live dollars, model quality, source retention/profile, resource permissions
or actual API binding were verified. These remain explicit deployment gates.
There is no permission to implement or provision from this design verdict.

## Retrospective and proposed playbook deltas

What worked: start with the operator's desired outcome, reuse prior source
evidence, separate independent UX/platform questions, and verify the integrated
state/learning flow rather than merely count agent names.

What needed correction: the first diagram bypassed trusted result processing
on lifecycle branches; independent review caught that and the less-visible
retention/late-correction/scoring gaps. Initial architecture QA hit Windows
console encoding; Python UTF-8 mode resolved it without changing shared tools.
A whitespace-sensitive artifact check was normalized rather than changing a
correct document just to satisfy the check.

Proposed reusable lessons, not adopted into any persistent playbook or memory:
design artifact eligibility separately from immutable history; carry correction
and erasure through knowledge descendants; distinguish a provider request cap
from hidden model/tool work; and treat a review click as judgment, never truth
or execution authority.

No durable playbook change was made. The next authorized work would start at
P0 contracts/compatibility and P1 correctness/state foundation, not immediate
cloud deployment.
