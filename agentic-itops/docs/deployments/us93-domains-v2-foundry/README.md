# Story 93: Domains V2 to Foundry deployment

**Status: foundation deployed and verified; full application deployment incomplete.**

The [current execution checkpoint](execution/README.md) separates implemented
prerequisite fixes from the unfinished application and live acceptance gates.
It also records the user's subsequent preparation and pilot-target approvals;
the archived design documents retain their original review-time status.
The [foundation record](execution/foundation.md) covers the GPT-5.6-luna rollout,
operator permissions, network restrictions and current USD 500/month allowance.
All nine agents and the separate review application still require deployment.

This folder contains the complete planning package for
[User Story 93 - Deploy Foundry agents](https://dev.azure.com/jahunte/End-to-End%20Observability/_workitems/edit/93).
It was copied into the repository on 2026-09-14 at the user's request.
Story 93 remains Active; its acceptance criteria were still empty when checked.
The criteria in this package are proposed, not approved work-item fields.

## Start here

| Deliverable | Contents |
|---|---|
| [Executive brief](us93-outcome-design/outputs/agentic-itops-outcomes/docs/executive-brief.md) | Goal, value, scope, timeline and next authorization |
| [Solution design](us93-outcome-design/outputs/agentic-itops-outcomes/docs/solution-design.md) | Complete data flow, all nine agents, result contracts, persistence, operator review and learning |
| [Delivery plan](us93-outcome-design/outputs/agentic-itops-outcomes/docs/delivery-plan.md) | P0-P4 work packages, dependencies, owners, acceptance cases and rollout gates |
| [Architecture diagram - draw.io](us93-outcome-design/outputs/agentic-itops-outcomes/docs/architecture-diagram.drawio) | Editable logical workflow |
| [Architecture diagram - Excalidraw](us93-outcome-design/architecture-diagram.excalidraw) | Alternative editable source |
| [Data assessment](us93-outcome-design/outputs/agentic-itops-outcomes/docs/data-assessment.md) | Producer contract, rich context, freshness, quality and readiness risks |
| [Responsible AI](us93-outcome-design/outputs/agentic-itops-outcomes/docs/responsible-ai.md) | Non-operation boundary, oversight, evidence retention and learning safeguards |
| [Cost estimation](us93-outcome-design/outputs/agentic-itops-outcomes/docs/cost-estimation.md) | Proposed resource footprint, calculation inputs and unpriced approval gates |

The target flow is observations -> validated detection/correlation -> Foundry
investigation -> validated persisted outcomes -> operator review -> governed
learning. Domains V2 stays producer-only. All nine prompt agents remain in scope,
with separate incident, closure/postmortem and hygiene triggers.

Synthetic `OperationalUse=prohibited` cannot be overridden by a review decision.
There is no real remediation, sending, dispatch or producer-control capability
in this design.

## Supporting research and design verification

| Artifact | Purpose |
|---|---|
| [Operator contract](us93-outcome-design/operator-contract-review.md) | Independent UI, review, lifecycle and publication recommendations |
| [Foundry capabilities](us93-outcome-design/foundry-capabilities.md) | Current documented capabilities, citations and unresolved integration gates |
| [Evidence ledger](us93-outcome-design/evidence-ledger.md) | Facts, proposals, sources and uncertainty |
| [Independent verification](us93-outcome-design/verification.md) | Initial corrections, closure and final design-only approval |
| [Goal contract](us93-outcome-design/goal-contract.json) | Confirmed outcomes and review scope |
| [Artifact checks](us93-outcome-design/design-checks.json) | Saved checks and hashes of the seven architecture deliverables |
| [Design run log](us93-outcome-design/run-log.md) | Decisions, budget, limitations and retrospective |

The independent verifier approved design completeness at confidence 0.90.
This is not deployment approval, live model-quality evidence or a claim that
the existing implementation satisfies the design.

## Full source-review baseline

The [source-review summary](us93-deep-review/review-summary.md) and
[historical deployment plan](us93-deep-review/deployment-plan.md) explain the
original readiness findings. The latter is superseded for target architecture
by the solution design above; its unresolved defects remain prerequisites.

Detailed reviews cover [runtime and tools](us93-deep-review/runtime-review.md),
[agents and documentation](us93-deep-review/agents-docs-review.md),
[data and tests](us93-deep-review/data-tests-review.md), and
[platform compatibility](us93-deep-review/platform-compatibility.md).

The complete source-review directory also retains the 102-file inventory,
per-reviewer coverage, verified coverage, probe scripts/results, deliberate
empty-policy test fixture, original goal contract, correction checks and review
history. These are historical evidence, not new results obtained while copying
the package.

## Using and maintaining this package

The two original artifact directories are retained so their relative references
continue to resolve and the seven reviewed architecture documents remain
byte-for-byte unchanged. This index is the repository entry point.

References to "session-only", original machine paths, review dates and "no
repository changes" inside historical records describe the earlier review.
They do not describe the subsequent authorized transfer into this folder.
Historical inventory hashes refer to the pre-transfer source snapshot; adding
these documents and the README link changes that source tree.

Helper scripts now locate the repository relative to their packaged location,
not the author's home directory. They are review utilities, **not deployment
scripts**. Do not rerun the archived inventory/probes as rollout commands:
they can fail against changed source or replace saved evidence. The inventory's
102-file snapshot must not be relabeled as a fresh audit of this larger tree.

For the current design artifact check, run from `agentic-itops`:

```powershell
python -X utf8 .\docs\deployments\us93-domains-v2-foundry\us93-outcome-design\verify_design.py
```

This checks document/diagram structure and explicit contract coverage, and
refreshes `design-checks.json`; it does not invoke Azure or establish runtime
correctness. No dependency installation is required for this helper.

Implementation, API/version compatibility, A2A control and nested budgeting,
Search identity/isolation, domain acceptance targets, pricing and cloud
deployment remain explicit gates. Preserve the distinction between historical
evidence, proposed decisions and subsequently approved implementation.
