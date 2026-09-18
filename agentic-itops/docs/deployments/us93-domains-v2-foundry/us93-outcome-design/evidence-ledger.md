# Outcome-design evidence ledger

Date: 2026-09-14. Scope: design only. Local source observations and current
public platform findings are distinguished from proposals.

| ID | Claim / decision | Evidence | Confidence and remaining gate |
|---|---|---|---|
| S1 | The requested outcome includes detection, RCA, proposals/gates, drafts and reviewed learning/hygiene in a separate interface. | Structured user response in this run; goal-contract.json | Confirmed user intent |
| S2 | Prior plan preserved nine agents but left the downstream application/state contract less concrete. | us93-deep-review\deployment-plan.md, runtime-review.md and data-tests-review.md | High; revision 2 makes the product boundary explicit, not a new deployment claim |
| S3 | The producer sends 16 neutral fields, not fired alerts; nine surfaces/four domains; all synthetic. | domainsV2\docs\signal-processing-contract.md, sections Envelope / Nine surfaces / Producer boundary; contracts\energy-observations.v1.schema.json | Direct local source read; actual current deployed contract/profile not queried |
| S4 | Optional rich context supplies qualified facts, partial topology/applicability and no established inventory-to-monitoring join. | domainsV2\docs\rich-observations.md, Boundaries / Populated profile; JSON Schema richInventoryContext | Direct source read; cannot use nominal ratings as limits or derived quantities as independent sensors |
| S5 | Sampling upper bound is 90/minute and 10,800/two-hour run; richer serialization estimate is about 38.32 MB. | signal-processing-contract.md Polling; rich-observations.md Selection and operational limits | Arithmetic over documented caps; not a measured current run or billable ingestion estimate |
| S6 | Existing runtime/deployers are not an all-nine consumer and have exact-gate, error, query, lifecycle and indexing defects. | us93-deep-review\runtime-review.md R01-R23 and saved probe results | Reuse same-day source review; no claim these were fixed |
| S7 | Existing curated corpus/tests are insufficient for live domain/agent quality; labels/hints must be separate. | us93-deep-review\data-tests-review.md findings 1-10 and acceptance additions | Reused independent review; no new runtime quality measurement |
| S8 | Six declared operations have specific names that must be preserved or deliberately migrated. | agentic-itops\tools\openapi: signal-normalizer.yaml:13, log-analytics.yaml:12, resource-graph.yaml:12, change-history.yaml:13, change-safety-gate.yaml:13, alert-hygiene.yaml:12 | Direct targeted operationId search |
| S9 | Responses/tool continuations, tracing/evaluation and preview memory have distinct responsibilities and limits. | foundry-capabilities.md with seven freshly verified official sources and prior A2A source | Independent public research; actual deployment not tested. Exact version pin/strict final schema/continuation expiry remain gates. |
| D6 | A guarded A2A facade may supply intermediate control while invoking the actual four specialists. | Research follow-up in foundry-capabilities.md; solution-design.md P0 control spike | Conditional integration proposal, not a proven turnkey service. Auth/correlation/version/bypass/internal-turn limits remain blocking. |
| D1 | SQL result ledger + Blob evidence + SQL work outbox is preferred over Blob/Table-only state or immediate extra broker. | Design tradeoff in solution-design.md | Proposed engineering decision; transaction, capacity and recovery tests required |
| D2 | Separate authenticated UI/API and worker/tools/publisher identity boundaries implement the selected user journey. | User choice S1; operator-contract-review.md | Independent recommendation integrated: five screens, revision-bound commands, scoped exports and independent knowledge approval. SQL chosen instead of the reviewer's Tables option for cross-record lineage/outbox transactions. Hosting/network/cost approval required. |
| D3 | Typed results, gate receipts, review revisions and publication state are authoritative rather than model prose. | Known defects S6; solution-design.md result model and lifecycle | Design invariant; future adversarial/fault tests must prove enforcement |
| D4 | Human acceptance and postmortem retrieval do not prove ground truth, physical recovery or action efficacy. | Producer neutral/synthetic contract; source corpus prohibitions; data-tests review | High; no operational executor is included |
| D5 | Performance, quality, retention, RPO/RTO, staffing and SKU choices are proposals. | solution-design.md NFR; delivery-plan.md; cost-estimation.md | Unmeasured; owner acceptance and actual regional quotes block live execution |

## Freshness and source boundaries

Same-day source/API review is sufficient for this design artifact. Recheck model
lifecycle, API shapes, hosting, regional availability/quota, effective identities
and prices at the compatibility/provisioning gate; public documentation alone
does not prove a deployed binding works.

No Azure reads/writes, producer controls, model invocations, data uploads,
dependency installations, Story comments or commits were performed in this run.
All new files are session artifacts. Public capability research uses generic
technology questions, not private repository content sent to an outside system.

## Verification interpretation

Architecture QA checks file presence/format and diagram structure; it does not
prove service compatibility or model quality. The final focused reviewer checks
the integrated design against the goal contract. Deployment readiness remains
blocked on the explicit P0-P4 gates regardless of the design review verdict.
