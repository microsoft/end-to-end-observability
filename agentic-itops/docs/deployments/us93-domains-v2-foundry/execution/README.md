# Story 93 execution checkpoint

Status: foundation infrastructure deployed and verified; full application deployment
incomplete. The sibling design and deep-review packages remain historical records.

## Full-completion run in progress

The September 15 `/idea full` request supersedes the earlier local-only stopping
points. Application integration, real Search delivery and private infrastructure
are being completed for actual Azure deployment; the parent retains approval,
integration and live acceptance ownership.

The Entra prerequisite now exists in Azure: the two approved single-tenant
review/services applications and assignment-required service principals were
created, all five review roles assigned to the designated operator, and their
v2-token/role/no-Graph-permission configuration independently read back. The
original 403 was resolved with an explicitly authorized isolated login, without
changing the original ARM login. A journaled HTTP-204 recovery resumed known
objects instead of creating duplicates. Exact identifiers remain in ignored
deployment state. Redirects, the private-vault sign-in secret and managed-identity
invocation assignments await their hosts and identities.

`agentic_itops/access_probe.py` adds a deployment-only count proof, not an
inference tool: a fresh independent operator witness must establish nonempty
observations and audit controls; actual worker-MI observations must match, and
audit access must be explicitly denied or filtered to zero against that witness.
Incomplete queries and unrelated errors fail closed. The 24 probe and six Entra
setup tests pass. This is not yet live MI authorization evidence.

The subsequent 41-case batch includes journaled runtime-role/redirect preparation
and tampered-probe-contract rejection. Independent scoped review found no
significant issues; it did not establish live Graph propagation or MI identity.
The real operator count transport subsequently returned 10,530 observations and
four audit-control rows at 20:54:56 UTC, September 15. Only aggregate counts were
requested; that historical witness must be refreshed before a worker-MI test.

`scripts/setup_entra_apps.py --runtime-config ... --apply-approved-runtime`
extends `--apply-approved-apps --resume` against the existing ignored journal.
It validates distinct managed identities against Graph, permits only the approved
review-to-worker/publisher, worker-to-tools and bootstrap-to-all-three role matrix,
preserves existing web settings, verifies writes and reconciles observable lost
assignment responses without duplicate grants. This new path has not been
applied to Azure yet; it creates no secret or Graph permission.

Run `scripts/collect_source_witness.py` using the original operator Azure CLI
context with explicit tenant, workspace UUID, RunId and absolute start/end.
Its `--output` must be a new ignored `.build` file. The resulting contract/witness
is a trusted deployment artifact, not a user/model assertion: send it only through
the restricted bootstrap-to-worker acceptance path and complete the probe within
five minutes. The worker uses its own explicit managed identity. A local operator
success must never be labeled consumer authorization.

The deployment plan retains the exact resource/permission/bootstrap approval
gate. Service hosts, private data-plane access, all-nine execution and review/
publication acceptance are not claimed complete by these identity changes.

## Service continuation

The user requested the remaining full solution and confirmed that the USD 500
foundation allowance stays separate. The [service-stage package](services.md)
records the proposed private hosting topology, additional costs, permission
boundaries and execution gates. This is preparation, not another deployment.
The user approved a separate USD 250/month services allowance and continued
implementation; exact cloud resources/permissions still need the validated preview.

The [all-nine registration path](registration.md) now has offline preparation,
explicit Luna sampling selection, durable per-version journals, uncertain-write
reconciliation and exact read-back. Matching receipts bind runtime versions with
project/plan provenance. Its 39 targeted tests pass; the combined
registration/compiler/trusted-Search/runtime regression batch passes 203 tests.
Independent scoped review found no high-confidence issues and exercised
single-attempt SDK transport failures without contacting Azure. This is not a
new full-suite count or live nine-agent acceptance.

The [publisher delivery slice](publisher.md) is also implemented: durable
publish/exclude claims, lease fencing and renewal, validated acknowledgements,
failure receipts, eligibility checks and late-upload removal reconciliation.
Consumer invalidation and unknown-kind work remain pending for their owners.
It has 76 targeted publisher cases and preserves the 110 existing review cases.
Independent publisher review found no significant issues.

After integrating both slices, the **full suite passes 820 tests**. This
supersedes the earlier 687-test source checkpoint, not its live boundaries.
Search delivery remains an injected interface; the real adapter, embeddings,
private hosting, migrations/grants, retrieval wiring and Azure execution remain
release gates. No new Azure writes, agent registrations or schedules occurred.

## September 15 foundation deployment

The [approved foundation](foundation.md) was provisioned in the new
`agentic-itops-us93-rg`, Central US, by deployment
`agentic-itops-us93-1789490133` in 3 minutes 45 seconds. It includes Foundry,
GPT-5.6-luna `2026-07-09` (DataZoneStandard, 50 units), embedding model v1
(DataZoneStandard, 10 units), Search Basic 1x1, SQL Basic 5 DTU/2 GiB, evidence
Storage and LAW/App Insights. Exact configurations were read back from Azure.
SQL and evidence Storage remain network-locked. No application hosts, app
registrations, agents, producer grants or schedules were created.

The foundation allowance is **USD 500/month**, not a hard billing cap, with
**USD 5 total** for synthetic model smoke tests. This supersedes the initial
USD 100 quote, which omitted inherited Defender charges. Existing subscription
protections remain enabled.

Luna's operator-authenticated Responses call succeeded. Embedding verification
exposed two onboarding requirements: use the account OpenAI endpoint, not the
project Responses endpoint, and grant the operator the account-scoped OpenAI
User role. The user approved that additional role; its deterministic assignment
is now applied and declared in IaC. The final combined probe passed at
13:00:46 EDT: Luna returned the expected marker and embeddings returned a finite,
nonzero 1,536-dimensional vector. The new default Application Insights
failure-anomaly rule was disabled at the user's request and read back as
Disabled at 13:15 EDT; the action group and Defender protections are preserved.
This proves neither consumer-MI access nor the all-nine workflow.

The final receipt and probe history are retained under ignored
`.build/foundation`; the source includes a reproducible bounded smoke script
with 18 passing targeted regressions. No commits or agent activations occurred.

## September 15 implementation continuation (historical)

Pre-foundation full-suite checkpoint: **687 tests passed**, including the production
review factory; its Windows native-library probe also passes.
The SQL work-store
delivery is integrated with immutable batch reconstruction and complete
multi-batch snapshot processing, including SQL/Blob restart recovery. The all-nine
compiler, version-bound runtime and review application now have integrated local
contracts; real service backends and deployment remain incomplete.
No billable resources, identities, grants, agents or model calls were created
by that earlier implementation-only continuation.

The later runtime/response/review regression batch passed **127 tests**.
`agentic_itops/runtime.py` now covers the version-bound 10/02 prerequisites,
actual 00 delegation order through 04/03/05/07, exact final proposal gating and
separate 08/09 triggers. Its tests use a fake Responses provider; they verify
application control flow and valid review headers, not actual Azure agents.
Production backend, result/revision, lifecycle and infrastructure wiring remains
incomplete.

Independent implementation review found five defects. All five corrections are
implemented with passing regressions: UUID self-approval casing, opaque RunIds,
durable diagnostic escaping, atomic ingestion fencing and source-partitioned
claims. Independent review confirmed closure of those five findings.
It identified an additional runtime provenance issue: a raw EventId could be
used as a fabricated runbook identity. Document identity/revision now requires
trusted retrieval membership independently of event citations; negative
full-flow and positive guarded-retrieval cases pass. Independent final follow-up
closed that finding and confirmed all 10 runtime tests pass. All six findings
are closed within the reviewed scope; this is not deployment approval.

The [live observation probe](observation-contract-probe.json) validated 10,530
retained records through the actual decoder across all nine surfaces, using
explicit operator credentials. No raw records were saved or inference performed.
This is not a consumer-MI isolation or physical-accuracy test. The new target
resource group remains absent.

See the [runtime entry-point and configuration contract](runtime-entrypoint.md)
for the production-only review factory, migration requirements, trusted Search
binding and remaining release gates.

This continuation ends at **incomplete preparation**. The source and evidence
are saved, but real backend/result/lifecycle/publisher integration and IaC are
unfinished. P4 remains blocked; no Azure deployment, new consumer resources,
agent publication, schedule activation or commit occurred.

The user approved **trusted function-call dispatch** for delegation and tool
backends, retaining native Search only where isolation is proven. The actual
Ops Orchestrator must still coordinate the four actual specialists; all nine
portfolio roles remain required. Native A2A is no longer a release prerequisite.
The existing three-agent demo has not been relabeled as this implementation.

| New source | Implemented behavior and remaining boundary |
|---|---|
| `consumer/contracts.py`, `logs.py` | Strict typed producer contract, nine surfaces, exact source identity, bounded complete queries, duplicate handling, explicit freshness semantics and failure on partial responses. No live observation read performed. |
| `consumer/evidence.py` | Immutable content-addressed local/Azure adapters, verification before SQL commit, bounded window splitting and visible incomplete ingestion. Azure adapter not live-proven. |
| `consumer/detectors.py` | Explicit-policy baseline/reference-change detection, persistence/recovery and unknown coverage. Disabled without policy approval. The 72 software cases are not independently labeled physical-accuracy acceptance. |
| `consumer/incidents.py` | Deterministic episode/evidence adaptation, source-digest checks and logical synthetic-entity URNs. No fabricated ARM IDs or promotion of normal rows to incidents. Cross-entity correlation is not yet implemented. |
| `consumer/sql_connect.py` | Pinned `mssql-python==1.15.0`, native `ActiveDirectoryMSI` with explicit client UUID, TLS certificate verification, short-lived connections and no developer/password fallback. Linux libraries and actual Azure SQL remain unverified. |
| `consumer/budget.py`, `runtime_sql` | Durable pre-invocation reservations, monthly/workflow/tool limits, server-time deadlines, conservative unknown usage, immutable settlement and overrun blocking. SQLite tests are local-only; SQL Server behavior remains an integration gate. |
| `consumer/responses.py` | Exact version references, full stateless function-call history, no automatic retries/truncation, strict output contracts, guard callbacks and shared accounting. Live Responses compatibility and all-nine coordinator wiring remain open. |
| `consumer/storage.py`, `consumer/migrations` | SQL Server work/evidence authority plus explicit local-test SQLite; atomic ingestion/outbox, durable failures, server-time renewable leases and fencing. Actual Azure SQL execution remains unproven. |
| `consumer/processing.py` | Reconstructs and validates immutable committed batches, requires complete declared ingestion coverage, deduplicates source events and persists evidence-linked detector candidates. Missing coverage produces no reasoning candidates. |

The official Functions HTTP template was fetched at tag `v1.0.0`, commit
`6c070b190db943e6a564c4c7ded90b5e553283b9`, into isolated session staging.
No template was initialized over the existing project. Infrastructure composition
must retain its managed identity, host storage and security patterns. Docker CLI
is installed, but its local Linux daemon is not running; this checkpoint does
not claim Linux native-library verification.

Install `requirements-consumer.txt` and run the eight `test_consumer_*` modules
named below from `agentic-itops` to reproduce this parent-owned checkpoint:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r .\requirements-consumer.txt
& .\.venv\Scripts\python.exe -m pytest -q `
  .\tests\test_consumer_observations.py .\tests\test_consumer_evidence.py `
  .\tests\test_consumer_detectors.py .\tests\test_consumer_integration.py `
  .\tests\test_consumer_budget.py .\tests\test_consumer_responses.py `
  .\tests\test_consumer_storage.py .\tests\test_consumer_processing.py
```

Story 93 comment 7699587 records the earlier 160-test continuation checkpoint.
The following sections retain initial-execution evidence; this continuation and
the physical deployment plan supersede their earlier implementation gaps.

## Authorized scope

The approved target is a new `agentic-itops-us93-rg`, Central US preferred,
in subscription `643febd3-3170-4110-80f9-fc8980b70a7a`. Consumer endpoints
require Entra sign-in. The USD 1,000/month planning ceiling is not a spend
authorization or an enforced billing cutoff. Domains V2 stays producer-only;
the existing producer and older Foundry deployment remain unchanged.

The complete all-nine outcome journey remains required. No user decision
reduced it to the existing three-agent harness or five-agent deployment script.
Provisioning, permission changes and model calls await a concrete approved
resource/cost/identity package and the relevant implementation gates.

## Implemented prerequisite changes

| Surface | Change and limitation |
|---|---|
| `safety_gate.py` | Blank/invalid policy handling, explicit scope allowlist, positive non-boolean counts, freeze validation, UTC evaluation, policy/time-bound audit ID and synthetic no-operation rejection. Not yet a durable gate receipt or authenticated provenance binding. |
| `orchestrator.py` | Requires explicit planner action/target/radius; rejects target substitution and gates the proposed count. Failed stages stop rather than becoming annotations/comms. Complete JSON replaces arbitrary truncation; invalid/incomplete model responses raise errors. Still the reference three-agent harness. |
| `cli.py` | Uses validated policy loading, surfaces errors and rejects a caller-selected gate clock in Foundry mode. Offline scenario-time behavior remains. |
| Agent 05 | Generator and emitted manifest require typed blast radius. Instructions preserve verified logical targets and prohibit synthetic execution. |
| Agent 08 | Instructions/description separate drafting from independently approved publication. No publisher service exists yet. |
| Manifest emitter | `--check` verifies the eight generator-owned manifests without writing. Agent 00 remains authored separately; full nine-agent deployment authority/strict schemas are not resolved by this check. |
| SDK probe | A separate pinned environment constructs required tool models and captures the exact serialized version-reference request without networking. No agent is created or invoked. |

`AgentVersionReference` currently accepts positive numeric versions as a local
application restriction. The official wire property is a string; this check
does not prove that every server version identifier must be numeric. The
helper is not yet integrated into the legacy live harness.

## Reproduction

Run from `agentic-itops`:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt -r .\requirements-foundry.txt
& .\.venv\Scripts\python.exe .\scripts\check_foundry_compatibility.py --output .\.azure\foundry-sdk-compatibility.json
& .\.venv\Scripts\python.exe .\scripts\emit_agent_manifests.py --check
& .\.venv\Scripts\python.exe -m pytest .\tests -o addopts='' -q
& .\.venv\Scripts\python.exe .\evaluations\run_evals.py
& .\.venv\Scripts\python.exe -m pip check
```

On 2026-09-14: 124 pytest cases passed, all 41 existing evaluation cases
passed, manifest drift checking passed, and pip reported no broken
requirements. The initial probe failed because `httpx` was missing; adding
its explicit pinned dependency fixed that failure. After independent review
corrections, **151 pytest cases and all 41 existing evaluations pass**.
The repeat probe reports
zero network requests, no authentication and live acceptance not performed.

These checks do not measure the new detector's per-surface precision/recall,
all-nine model quality, tenant authorization, review concurrency, publication
revocation or crash recovery. None of delivery criteria E01-E15 is claimed
complete from these local results.

## SDK evidence and remaining gates

Pinned compatibility candidates: `azure-ai-projects==2.6.1`,
`openai==3.8.0`, `azure-identity==1.25.3`, `PyYAML==6.0.2`,
`httpx==0.28.1`. This is not a complete production dependency lock.

Primary sources inspected during execution:

- [Foundry runtime components](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/runtime-components):
  Responses requests carry `agent_reference`.
- [AgentReference API](https://learn.microsoft.com/en-us/java/api/com.azure.ai.agents.models.agentreference?view=azure-java-stable)
  and [generated serializer](https://github.com/Azure/azure-sdk-for-java/blob/main/sdk/ai/azure-ai-agents/src/main/java/com/azure/ai/agents/models/AgentReference.java):
  its serialized fields include `type`, `name` and `version`.
- [A2A tool guide](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/agent-to-agent):
  the current Python guidance requires projects SDK 2.5.0 or later.

The offline test confirms client serialization, not server-side routing or
enforcement. P0 still needs actual model allocation, all-nine strict
schemas and tool bindings, version routing, authenticated trusted dispatch with
intermediate validation/shared budgets, Search snapshot isolation/revocation,
and real MCP/OpenAPI behavior.

P1-P3 still need integrated SQL/outbox processing, the actual nine-agent journey,
authenticated backend/review interfaces and governed publication/hygiene.
The continuation above supplies reader/detector/evidence/runtime foundations,
not completion of those phases. P4 requires prepared infrastructure, validation,
specific cloud approval and live positive/negative acceptance.

## Read-only Azure preflight

The [completed preflight](preflight/azure-preflight.md) and its retail,
model-catalog and scenario JSON are preserved byte-for-byte in `preflight`.
The target RG does not exist. `Microsoft.Search`, `Microsoft.Sql` and
`Microsoft.App` require registration approval. B1/storage/account headroom
was found; effective Flex memory quota, SQL/Search quotas and actual model
allocation remain unverified.

The conditional incremental pilot is **USD452.96/month**, including
**USD91.77 standing costs**, at the report's specified usage allowances.
It is not a final all-in quote or spending authorization. Extra gateway/
hosted-agent compute, paid evaluations, source-query billing and policy add-ons
must be resolved before enabling them.

The same-model candidate is `gpt-4o` 2024-11-20 (Legacy, documented retirement
2027-04-14); the embedding candidate is `text-embedding-3-small` v1 with
1536 dimensions. DataZoneStandard constrains inference to the US data zone,
not Central US alone. The inspected embedding catalog lacks regional Standard,
so processing residency is a user decision, not implied by the resource region.

The existing observation-only role and table condition establish a candidate
permission pattern only. New-identity effective grants and known-nonempty
negative audit-table tests still require approved provisioning.

## Execution review and handoff

A delegated implementation conductor and three child attempts lacked editing/
shell tools and produced no implementation. Parent tools completed the
prerequisite changes above. Read-only Azure preflight is complete. Independent
source review returned REVISE on provenance propagation, empty stage results
and malformed deny rules. Corrections and 27 additional regression cases are
implemented. Narrow independent follow-up returned **APPROVE, high confidence**,
closing all three findings; its 30 targeted regression cases pass and the
marker-free offline demo is preserved. This is approval of those corrections,
not deployment or E01-E15 acceptance.

No resources, identities, permissions, model deployments, uploaded data,
published agents, commits or automatic schedules were created in this run.
The user separately approved provider registration and US data-zone processing
as a planning constraint. Search, SQL and Microsoft.App are now Registered;
Search Basic quota is 0/12 used and SQL ServerQuota is 0/250 used. These current
facts supersede the preserved initial preflight's registration/quota gaps.
See [approval and follow-up evidence](approval-followup.md). No billable
resources, identities, grants or model calls were authorized by that approval.
The authoritative phase state is
[`agentic-itops\.azure\deployment-plan.md`](../../../../.azure/deployment-plan.md).
