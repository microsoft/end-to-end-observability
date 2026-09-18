# Idea-to-Outcome Loop (v2)

## Purpose

The Idea-to-Outcome Loop turns an initial idea into a finished, evidence-backed result. It combines divergent exploration with convergent execution so that creativity does not replace accuracy and analysis does not replace delivery.

Unlike a single-shot workflow, this loop is designed to **compound**: every non-trivial run ends by harvesting reusable lessons into a versioned playbook, so the loop gets sharper each session rather than starting cold.

## Activation

Activate the loop when the user labels or clearly frames a request as an **Idea**. Unless the user explicitly asks for brainstorming or planning only, continue through execution and verification and return the completed result.

Ask the user only about decisions that materially change the outcome and cannot be resolved safely from available context. Otherwise, make and disclose reasonable assumptions.

## Operating principles

- Separate facts, hypotheses, assumptions, and creative proposals.
- Explore meaningfully different approaches before selecting one.
- Ground factual claims in repository evidenforce, authoritative sources, experiments, or tool output.
- Define success before execution.
- Treat a plan as an intermediate artifact, not the result.
- Right-size the effort to the idea. Do not apply full machinery to a trivial request, and do not under-process a consequential one.
- Use independent verification for nontrivial work. Work is nontrivial when it involves multiple execution steps, material factual claims, code or artifact changes, consequential recommendations, or external side effects.
- Track budget continuously and stop at the hard limit rather than drifting.
- Detect non-convergence early. Stop unproductive retries, record the blocker, and escalate rather than manufacturing success.
- Require human approval for irreversible, destructive, production, financial, security-sensitive, or externally published actions.
- The human remains the judgment layer. Ship results the human can actually read and evaluate; never trade comprehension for speed.

## Safety denylist

The loop must never perform the following without explicit, specific human authorization for that exact action:

- Destructive or irreversible operations (delete, drop, overwrite, force-push, mass update).
- Production, financial, security-sensitive, or legally binding actions.
- Externally published or externally visible communications (email, posts, tickets, messages to third parties).
- Credential, secret, key, or permission changes.
- Actions on resources outside the explicitly authorized scope (e.g., a tenant, account, or environment the user did not name).

If a needed step is on the denylist, stop and request authorization through the escalation package. Do not seek a workaround.

## Full cycle

### 0. Right-size the loop

Before doing anything else, classify the idea into an effort tier and scale the rest of the cycle accordingly:

- **T0 — Trivial.** Single-step, low-consequence, no external side effects. Define the goal and definition of done inline, execute directly, and issue a deliberate self-check verdict. Skip subagents and formal contract artifacts, but do not skip success criteria or verification.
- **T1 — Small.** A few steps, low risk, reversible. Single maker, lightweight verification pass, minimal evidence ledger.
- **T2 — Substantial.** Multi-step, material factual claims or artifact changes. Full contract, 2–3 explorers, separate verifier.
- **T3 — Complex / consequential.** Irreversible, production, high-stakes, or broad-scope. Full contract, parallel explorers, dedicated verifier, and mandatory human approval gates.

Record the chosen tier and the reason. The tier sets subagent fan-out, evidence depth, verification rigor, and budget. Safety and consequence determine the minimum tier; optimize cost and effort only within that safe tier. Disclose material trade-offs.

### 1. Capture intent

Translate the idea into an initial problem statement:

- Desired outcome and audience
- Motivation and expected value
- Known constraints and boundaries
- Available inputs, tools, time, and budget
- Unknowns that could materially affect the outcome

### 2. Establish a Goal Contract

Define:

- One primary goal
- Concrete deliverable
- Measurable success criteria
- Definition of done
- Explicit non-goals
- Accuracy and evidence requirements
- Human approval points
- **Budget envelope** — token, tool-call, wall-clock, and subagent limits, plus the retry ceiling
- **Effort tier** from Stage 0
- **Residual-risk policy** — acceptable risk threshold and the person or role authorized to accept risk above it
- **Artifact storage policy** — session-only by default, or explicit approved project paths for durable artifacts

The Goal Contract may be refined after exploration, but it must be locked before execution.

If the user does not provide a budget, set and disclose a tier-appropriate default before execution. Track only telemetry the runtime exposes. Clearly label estimated token, cost, or time values; never present estimates as measured usage.

### 3. Explore broadly

Generate genuinely different approaches rather than minor variations. Scale the number of explorers to the effort tier and the budget envelope; do not fan out beyond what the budget allows. For substantial and complex ideas, use independent subagents where parallel perspectives improve the result, such as:

- Domain or user-needs exploration
- Technical or operational feasibility
- Creative alternatives and analogies
- Market, ecosystem, or prior-art research
- Risk, ethics, security, and failure analysis

Consult the harvested playbook (see Stage 10) for prior lessons relevant to this class of idea before diverging. Do not expose one explorer's conclusions to another before their independent pass when diversity matters.

### 4. Build an evidence base

Investigate the strongest candidates. Maintain an evidence ledger containing:

- Claim or assumption
- Supporting or contradicting evidence
- Source and date
- Confidence
- Remaining uncertainty
- **Freshness requirement** — for time-sensitive claims, the maximum acceptable age of the evidence; re-verify if the source is older than this threshold

Prefer primary and current sources. Verify important claims through more than one independent source or through direct testing when practical.

### 5. Challenge and converge

Use a critic to test assumptions, identify missing alternatives, find failure modes, and challenge whether the idea solves the stated problem.

Compare candidates using criteria appropriate to the Goal Contract, normally including:

- Outcome fit
- Evidence quality
- Usefulness or expected impact
- Originality
- Feasibility
- Cost and time
- Risk and reversibility

Select an approach, record why it won, and preserve rejected alternatives that may become useful if execution fails.

### 6. Plan delivery

Convert the selected approach into an executable task graph:

- Outputs and acceptance checks
- Dependencies and ordering
- Agent or human owner
- Tools and permissions
- Validation method
- Budget and retry limit
- Escalation conditions

Parallelize only independent tasks. Keep consequential decisions with the orchestrator.

Three correction attempts is the default maximum. The Goal Contract may set a lower limit; exceeding three requires explicit user authorization.

### 7. Execute

Produce the actual deliverable using the appropriate tools and agents. The orchestrator maintains the Goal Contract, integrates outputs, resolves conflicts, and ensures that partial subagent work does not become false completion.

Execution may include research, design, code, documents, analysis, prototypes, tests, or external-system operations, depending on the goal.

Throughout execution, update the **budget ledger** with available measured telemetry (such as tool calls, elapsed time, and subagent count) and clearly labeled estimates for unavailable values. Enforce limits the runtime can measure or control. When a hard limit is reached, stop and escalate rather than silently continuing.

Apply a **convergence guard**: if two consecutive REVISE cycles do not reduce the number of open failures, treat the loop as non-converging. Stop retrying, record the oscillation, and escalate instead of exhausting the remaining budget.

### 8. Verify independently

A verifier evaluates the integrated result from a skeptical posture. For nontrivial work, the verifier receives the **deliverable, Goal Contract, evidence ledger, and applicable safety constraints** — but not the maker's reasoning chain — and where possible runs as a separate model or persona to preserve independence.

- Re-run objective checks and tests.
- Trace each success criterion to evidence.
- Check factual accuracy, completeness, internal consistency, and usability.
- Confirm constraints, the denylist, and approval gates were respected.
- Look for unsupported claims and unhandled edge cases.

The verdict is `APPROVE`, `REVISE`, or `ESCALATE`, and must include a **confidence level and a residual-risk note** so the human can calibrate how much additional scrutiny to apply. `APPROVE` is valid only when residual risk is within the Goal Contract's threshold or has been accepted by its named decision owner. A `REVISE` verdict returns specific failures to execution. When the Goal Contract's correction limit is exhausted, or the convergence guard trips, stop and escalate unless the user authorizes another budget.

Use a separate verifier agent for nontrivial work when available. If one is unavailable, perform a distinct verification pass against objective checks with fresh context and disclose the reduced independence. For trivial work, deterministic checks or a deliberate self-check are sufficient.

### 9. Finish and deliver

Return the completed outcome, not merely a process report. Include only the context needed to use or evaluate it:

- Result or deliverable
- A concise, human-legible **"what changed and why"** summary so the approver can understand the result without reverse-engineering it
- Important decisions and assumptions
- Verification evidence, including the verifier's confidence and residual risk
- Material limitations or unresolved risks

Update durable state only through an established project mechanism or as part of a user-authorized deliverable. Do not create unsolicited repository changes, external records, or persistent memories.

If verification returns `ESCALATE`, return a safe escalation package instead of claiming success. It must contain the usable partial artifacts, failed success criteria, supporting evidence, actions already attempted, material risks, and the exact user decision or input needed to continue.

### 10. Retrospective and harvest

For every T1 or higher run, close the loop by capturing what should carry forward:

- **What worked** — approaches, sources, and checks worth reusing.
- **What was wasted** — dead ends, budget sinks, and low-value steps to avoid next time.
- **Reusable heuristics** — durable lessons for this class of idea.
- **Run log entry** — the winning approach, sources used, verdicts, and budget spent, recorded for auditability.

Propose these as deltas to a **versioned playbook** and to the run log. Adopt playbook changes only with user approval and only through the Goal Contract's artifact storage policy — never as silent, unversioned memory. This is what allows the loop to become more advanced each session.

## Subagent coordination

- The orchestrator owns the final result and cannot outsource accountability.
- Delegate independent, context-heavy work that benefits from specialization.
- Give each subagent a complete scope, expected output, evidence standard, constraints, and completion criteria.
- Scale subagent fan-out to the effort tier and remaining budget; do not spawn explorers the budget cannot support.
- Avoid duplicate assignments unless deliberate independent comparison is required.
- Use separate maker and verifier agents for nontrivial deliverables, and keep the verifier blind to the maker's reasoning.
- Integrate and verify subagent outputs before relying on them.

## Artifacts

The loop maintains these working artifacts, scaled to the effort tier:

- **Goal Contract** — the locked definition of success and its budget envelope.
- **Evidence ledger** — claims, sources, confidence, and freshness.
- **Budget ledger** — live consumption against the envelope.
- **Run log** — an auditable record of each run's approach, verdicts, and cost.
- **Playbook** — the versioned, approved store of harvested lessons that compounds across sessions.

Artifacts remain in session state unless the Goal Contract authorizes durable project storage. When repository storage is approved, use these canonical paths unless the project defines an established alternative:

- `.idea-loop/playbook.md`
- `.idea-loop/runs/<timestamp>-<idea-slug>.md`

Each run record contains or links its Goal Contract, evidence ledger, budget ledger, verdict, and retrospective. Never write these paths merely because the loop was activated; the storage policy must explicitly authorize it.

## Completion rule

The goal is successfully complete only when:

1. The Goal Contract's deliverable exists, or for T0 the inline deliverable exists.
2. Every success criterion has supporting evidence.
3. Verification returns `APPROVE` with residual risk within the Goal Contract's threshold; for T0, the deliberate self-check returns `APPROVE`.
4. Required human approvals are satisfied.
5. The budget envelope and safety denylist were respected.
6. Remaining limitations are disclosed without presenting partial work as success.

An `ESCALATE` verdict concludes the current loop run but does not complete the goal. Return the escalation package defined above and wait for the required human decision.
