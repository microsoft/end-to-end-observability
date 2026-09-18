# Deep review: all agentic-itops files

Date: 2026-09-14. Story 93, "Deploy Foundry agents", Active revision 5, description/criteria absent. Scope is review plus recreated deployment plan, not implementation or deployment.

## Result

**102/102 files reviewed and accounted for; project content unchanged.** There are 10,638 lines across the inventory. 101 files received full-content review. The 179,772-byte bulk history received a structured full scan of all 397 records and representative human-content reads of all 17 rule/state shapes; it was not read prose-by-prose record by record. No generated, hidden or source file was excluded from the on-disk inventory.

The project is a useful offline reference, **not ready to deploy as the requested full nine-agent Domains V2 consumer**. Deployment work includes correctness fixes, actual hosts/tools/identity integration, a current API migration and all-agent quality acceptance. This is not solved by removing agents or deploying the existing script unchanged.

## Coverage by ownership

| Area | Files | Method / evidence |
|---|---:|---|
| Runtime, adapters, policy, Python tools | 22 | Parent full-content trace |
| External tool specs/MCP | 8 | Parent full-content review; six generated spec parity checks |
| Root configuration (excluding README) | 5 | Parent full-content review |
| Deployment/OpenAPI generation scripts | 3 | Parent full-content review |
| Nine agent definitions/instructions, five docs, README, manifest emitter | 25 | Independent full-content review |
| Data, evaluations, tests, sample-data generator | 39 | Independent full-content review except bulk structured scan |
| **Total** | **102** | `coverage-verified.json`; no missing/duplicate assignments |

`inventory.json` contains path/size/line count/SHA256/owner; `coverage-verified.json` adds review method and file-specific outcome. Baseline and final hash checks confirm no project content changes.

## Most consequential findings

| Priority | Finding | Evidence | Required gate |
|---|---|---|---|
| Blocker | Five definitions published, only three specialists invoked; no complete nine-agent lifecycle or actual Foundry orchestrator path | `scripts\deploy_to_foundry.py:42-48,103-132`; `agentic_itops\orchestrator.py:317-400` | Nine exact agents plus actual industry/triage, four-specialist orchestration, closure and hygiene traces |
| Blocker | Classic connected-agent descriptor unsupported in current new Foundry | Current Microsoft migration/A2A documentation, `platform-compatibility.md` | Preserve logical four-specialist edges through proven supported A2A migration or approved equivalent workflow |
| Blocker | No production HTTP tool/MCP/lifecycle host or infrastructure assets | Full inventory; `tools\mcp\azure_monitor_mcp_stub.py:168-188`; all OpenAPI server placeholders | Authenticated real endpoints, deployment package/IaC, caller/backend identity and network acceptance |
| Blocker | No neutral-observation reader/detector/adapter; current KQL/input are generic alert fixtures | `cli.py:49-54,147-153`; `orchestrator.py:408-441` | Domains V2 contract, detector, finding and lifecycle history with strict observation-only provenance |
| Blocker | Gate evaluates original incident resource/count instead of proposed target/radius | `orchestrator.py:380-391`; offline probe returned ALLOW for a different SCADA/40-resource proposal | Gate exactly the validated proposal; no real execution deployed |
| Blocker | Empty CLI YAML reloads sample policy; missing scope can authorize arbitrary targets | `cli.py:105-109`; `safety_gate.py:130-143,193-215`; probes | Strict policy loading/schema, trusted mode/time and immutable synthetic prohibition |
| Blocker | Search live path is key-only lexical search and drops prescribed actions; deployer substitutes FileSearch | `runbook_index.py:249-276`; `deploy_to_foundry.py:112-119`; fake-SDK probe | Actual declared Search semantics/metadata/identity, not backend-name substitution |
| Blocker | Partial query, failed indexing and invalid model output are not handled as reliable failure states | `log_analytics.py:90-117`; `deploy_to_foundry.py:68-100`; `orchestrator.py:236-267` | Typed errors, no incomplete checkpoints, indexing terminal success, strict agent schemas and bounded evidence |
| Blocker | Missing safety dataset can yield PASS 0/0 | `evaluations\evaluators.py:84-87,200`; `evaluations\run_evals.py:186-190`; independent probe | Require expected suites/case counts/IDs/schemas; fail unavailable datasets |
| Important | Common Alert Schema metric arrays are dropped; raw Alertmanager batches lose alerts | `adapters\base.py:29-36`; `azure_monitor.py:108-127`; `prometheus.py:40-53`; probes | Shared ingestion expansion and complete typed extraction |
| Important | Correlation depends on input order; string-sorted timestamps misstate resolution | `normalizer.py:43-66,188-205`; probes | Explicit ambiguity/run/window handling and UTC lifecycle ordering |
| Important | Hygiene miscounts event streams/empty input and overstates self-healing | `hygiene.py:41-48,95-102,140-171`; bulk generator/fixture review | Define snapshot/event contract and denominators; no engineering-action inference from resolved status |
| Important | Runbook service restart can be flattened into VM-level `restart` | `data\runbooks\RB-014-pump-controller-cpu-saturation.md:33-43,63-65`; gate fixture | Preserve component target and prohibitions through proposal/gate |
| Important | Postmortem publication promised but no write tool; reader-only posture conflicts with it | `agents\08-postmortem-drafter\agent.yaml:6-18,39-43`; `docs\governance.md:144-146` | Separate reviewed/versioned synthetic publisher; draft is not publication |
| Important | Evaluations accept substring document IDs and have weak membership/offline assertions | `evaluations\evaluators.py:165`; `tests\test_skills.py:86-90,222-229`; `test_offline_guarantee.py:33-41` | Exact IDs/partitions, supported fail-fast no-network guard and per-agent live/quality cases |
| Important | Eight manifests can be overwritten by their emitter; 00/instructions are separately maintained | `scripts\emit_agent_manifests.py:67-275` | Explicit source ownership/drift contract; preserve all folder behaviors |

This table groups deployment/correctness findings, not vulnerability severity ratings. The full findings, caveats and additional documentation mismatches are in the companion reviews.

## Measured checks and their limits

- Parent baseline: 67 pytest cases pass; 41/41 evaluation cases pass; legacy nine-agent dry-run displays all nine and exits 0. No models/services invoked.
- Parent focused probes: 13 reproducible behavior/default hazards plus six-spec structural generation parity. Additional fake SDKs demonstrate partial-result `.tables` error, key-only lexical Search dropping actions, and failed-indexing store returned despite zero successful files.
- Independent data review: all 397 bulk records parse; 41 evaluation rows read; 16 sample outputs reproduce twice in memory with no external seed; four dataset outputs match builders. Existing retrieval coverage omits positive cases for RB-052 and PM-2026-07-19.
- Offline guarantees remain limited: these are not live SDK/model authorization or quality tests. The Python 3.13 import-hook finding means passing the existing offline test is not itself a robust no-network proof.
- All 102 repository file hashes remain unchanged. No Azure control/data-plane request, model call, upload, provider registration, permission/network change, producer control or Story write occurred in this deep-review run. The only external project operation was a read-only Story 93 refresh; public documentation was researched independently.

## Platform facts and unresolved gates

Current migration guidance: modern SDK floor >=2.3.0; classic Connected Agents unsupported; GA A2A 1.0 is the documented alternative with incoming-endpoint/target-authorization prerequisites. Search supports the declared vector_semantic_hybrid/top_k=3 binding, but documentation conflicts on account versus project MI. OpenAPI direct MI identifies project MI and target audience; backend identity remains separate. MCP needs real remote auth and approval continuation.

Published base-model schedule: gpt-4o 2024-11-20 and gpt-4.1-mini 2025-04-14 are Legacy, retirement listed 2027-04-14. This does not prove chosen region/model/tool support or tenant quota. Exact supported version-pinned invocation fields, strict-output constructor/subset, Search/vectorization identity and final networking remain pre-deployment gates. No unsupported API fields or universal per-agent UAMI behavior are assumed.

## Recreated plan

`deployment-plan.md` is the current plan, superseding both earlier session plans. It keeps all nine prompt agents and all deterministic skills, uses a real orchestrator plus supported specialist delegation, builds the observation/history bridge and shared Search/HTTP/MCP services, and stages manual full-portfolio acceptance before enabling scheduling. It includes ten proposed acceptance criteria, W0-W9 task dependencies/owners, approval boundaries, costs/budgets, rollback and handoff.

## Evidence files

- `agents-docs-review.md`: complete intended-agent/tool/schema matrix, instructions/generator ownership and 19 documentation discrepancies.
- `data-tests-review.md`: full data/corpus counts, evaluation/test defects, generation checks, mandatory action prohibitions and acceptance proposals.
- `runtime-review.md`: 23 runtime/tool/deployment/configuration findings with source citations.
- `platform-compatibility.md`: independent current Microsoft references, supported versus unverified API/identity/model contracts.
- `contract-probe-results.json`, `live-boundary-probe-results.json`: direct local reproductions.
- `inventory.json`, `coverage-verified.json`: per-file accountability and immutable source snapshot.

## Verification status

**Complete: APPROVE for review/plan completeness.** Independent integrated verification confirmed source accountability, full scope and architecture, with one narrow requested addition for stale/missing/delayed observations and expired knowledge. That addition and the six-industry wording correction are incorporated and pass eight direct artifact checks. The verifier permitted parent closure without another source audit; the final correction check is parent-performed, not a second independent review. Deployment/model/identity acceptance remains intentionally unperformed and blocked on the explicit gates.
