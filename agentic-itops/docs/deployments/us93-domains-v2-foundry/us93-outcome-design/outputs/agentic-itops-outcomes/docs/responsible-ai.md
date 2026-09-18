# Responsible AI and safety boundaries

## AI Use Case Classification

This design is a synthetic advisory demonstration, not autonomous industrial control, an engineering safety system or an authorized remediation service. All results retain OperationalUse prohibited. No legal EU AI Act classification or Microsoft Responsible AI approval has been performed. The accountable owner must assess classification and intended-use changes before any real-world use; synthetic evidence cannot justify a claim of regulatory exemption or production readiness.

The applicable engineering risk is that convincing language can be mistaken for confirmed diagnosis or authority. The solution therefore separates observations, computed findings, model hypotheses, response proposals, gate receipts, human judgments and observed recovery.

## Fairness and Bias

The existing corpus and curated examples are narrow and can overemphasize familiar pump, SCADA and portal scenarios. Evaluate by all nine surfaces and record families rather than a single aggregate score. Report unsupported or sparse-data cases explicitly instead of allowing easier surfaces to hide gaps. Preserve five other industry choices without claiming energy acceptance validates them.

Reject demographic or customer-specific inferences from fictional names and free text. Collect enough review examples to identify systematic overconfidence, but do not silently treat acceptance clicks as labels or train on personal reviewer information.

## Transparency and Explainability

Show citations to exact EventIds and knowledge revisions, numerical feature/units, freshness, missing evidence, contradictory observations and the responsible agent stage. Keep hypotheses visibly separate from observed facts. Model-generated confidence is not a calibrated probability.

A trace is useful for tool calls, latency and reproducibility, not permission to expose internal chain-of-thought. Display concise supplied rationale and grounded evidence, not hidden reasoning. Record model, prompt, detector, policy, tool and corpus versions to explain why re-analysis differs.

## Human Oversight

Operators can accept or reject a revision, request evidence, edit drafts as new versions, close an incident administratively and approve publication. Those actions are scoped and auditable. A stale revision or edited proposal invalidates prior approval. Knowledge publication requires a separate authorized reviewer/publisher path.

There is no human override of synthetic non-operation. No Execute, Send, live suppression, physical dispatch or customer-ticket endpoint exists in this design. An approved analysis is still advisory. The code gate must bind the exact proposal, distinguish service from host actions and retain permanent SCADA/rotation/mechanical-response prohibitions in the source corpus.

## Data Retention and Privacy

Proposed pilot retention is 30 days for detailed evidence/agent artifacts, 90 days for review/version metadata and explicit reviewed expiry for knowledge documents. These are owner-approval defaults, not claims about current service settings. Shorter source retention may limit reconstruction; evidence copies require separate authorization and minimization.

Knowledge cannot remain eligible longer than its required supporting evidence
and approval lineage. Its effective expiry is the earliest supporting-record
deletion or approved knowledge expiry, unless longer minimal-evidence retention
is explicitly authorized. Expire/revoke and remove dependent Search revisions
before deletion; never extend retention silently. Legal erasure or an unavailable
evidence dependency immediately invalidates dependent knowledge and affected
workflows, with a visible unavailable/stale status and only legally permitted
audit tombstones retained. A past approval does not override erasure.

An erasure workflow identifies SQL, Blob, Search, telemetry, conversations and backups separately. Preserve only the minimum non-content audit metadata legally allowed. Provider retention and deletion behavior must be verified for the chosen API and region; do not promise immediate erasure from model context or backups.

## Model Monitoring

Monitor schema failure, unsupported/insufficient-evidence rates, citation validity, ground-truth-independent grounding checks, contradictory-evidence handling, latency, cost and review rejection by surface. Offline unit tests and successful agent registration are not reasoning quality measurements.

Use held-out scenario groups separated by run/entity family and knowledge lineage. A previously generated postmortem cannot enter the retrieval corpus for evaluating its own parent case. Publish quality regressions, route disagreement to review and roll back exact accepted versions. Automatic model switching, detector tuning and knowledge ingestion are disabled.

## Content Safety

Treat observations, retrieved prose and tool outputs as untrusted data. Use provider content-safety controls where supported, validate application outputs, limit tools and block prompt-injection attempts to obtain audit labels or operational authority. No filter alone guarantees safe or correct outputs.

A safety block is a visible blocked stage; it must not become normal status or a fabricated harmless proposal. Drafts that lack evidence or contradict a gate are not accepted communications. Test arbitrary URL/table requests, invented citations, fake gate strings, instructions embedded in Context, expired documents and synthetic-flag removal across every agent.
