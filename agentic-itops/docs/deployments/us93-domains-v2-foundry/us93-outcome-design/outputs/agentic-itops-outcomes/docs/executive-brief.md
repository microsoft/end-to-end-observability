# From synthetic telemetry to reviewable operations intelligence

## Business Challenge

Domains V2 already produces synthetic observations across monitoring and inventory surfaces. The next goal is to make that data useful: identify supported abnormal patterns, explain likely causes with evidence, propose safe responses, prepare incident communications, and reuse reviewed lessons. Deploying agent definitions without a consumer, result-processing system and operator experience does not accomplish that goal.

The user confirmed that all five outcomes belong in scope and that the review experience should be separate from Domains V2. The producer should remain independently deployable and should not become a Foundry application. All nine defined prompt agents remain required, including closure/postmortem and alert-hygiene workflows.

## Recommended Solution

Build one integrated outcome-processing solution, not several competing projects. A bounded observation reader and deterministic detector create evidence-linked findings. The complete Foundry portfolio enriches, investigates and explains them. A trusted processor validates the results and persists lifecycle, gate receipts and review decisions. A new authenticated review interface presents the result to an operator.

The important improvement is the contract between those steps. A model hypothesis is not a fact, a response proposal is not an action, an accepted review is not execution permission, and a published postmortem is not automatically an approved procedure.

Three delivery capabilities support that single solution: trustworthy evidence-to-incident processing, the separate review application, and governed learning/quality monitoring. They share versioned state and safety rules rather than being disconnected demonstrations.

## Expected Business Impact

The immediate value is a demonstrable end-to-end workflow that a reviewer can inspect: observation evidence, finding, specialist contribution, proposal, decision and learning. Target measures are detection quality by surface, grounded citation rate, time to a review-ready result, duplicate prevention and reviewer rejection rate.

Proposed pilot engineering targets include a persisted incident view loading within two seconds and p95 admitted-incident reasoning within 120 seconds at concurrency one. These are acceptance proposals, not measured results or Azure guarantees. Financial ROI is not yet calculable: there is no measured manual-review baseline or verified Azure quote. Establish both before making savings claims.

## High-Level Timeline

Plan around five milestones: contract/compatibility decisions; evidence and durable-state foundation; full nine-agent wiring and result validation; operator review and learning; bounded deployment acceptance. The detailed roadmap estimates 24-40 engineering person-days, with parallel work where contracts permit. It is a planning range, not a delivery commitment; platform spikes and approvals can change elapsed time.

No cloud execution, resource changes, model invocation or code implementation occurred in this design review. Existing source defects and API/identity compatibility gates remain prerequisites.

## Why Azure

The producer already uses Azure ingestion and Log Analytics. Foundry supplies managed reasoning and agent/tool integration; Azure AI Search supports reviewed knowledge retrieval; Entra provides operator/service identity; SQL and Blob support durable review state and evidence. This alignment keeps the integration on the existing platform while preserving clear service boundaries.

The tradeoff is a larger consumer footprint than a five-agent script: Search, model/embedding usage, authenticated hosting, state and observability must be priced. Stopping a demo does not stop all standing resource charges.

## Next Steps

The next authorization is local implementation of the Story 93 contract/correctness foundation, not immediate deployment. Approve proposed acceptance targets and the identity/network/model compatibility approach first. Provisioning, spend, model calls, producer generation and external Story updates remain separate approval gates.

Synthetic OperationalUse prohibited is non-overridable. The review application will have no real remediation, dispatch or sending capability, even after a person accepts its analysis.
