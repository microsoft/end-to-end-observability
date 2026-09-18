# Story 93 application integration

## Current checkpoint: Midstream ingestion complete; AI result validation failed

### Validation-boundary diagnostic correction

The Responses engine previously raised verbose JSON Schema exceptions directly
at four boundaries: static agent output, dynamically trusted citations, function
arguments and function output. The worker stores at most 2,000 escaped characters;
a sufficiently large rejected value removes the structural validation header
that the legacy review classifier needs. A local regression reproduces this
observability defect. It is not yet proof of the exact Midstream schema failure.
Responses requests use `store=False`, and failed outputs do not append successful
stage receipts, so unavailable model output must not be reconstructed or inferred.

The correction preserves `ValidationError`, every validator, settled accounting,
failed-job immutability and zero automatic retries. Each boundary now emits
allowlisted agent/boundary/keyword, safe schema and instance paths, JSON type and
bounded union-branch causes. Missing required fields come from the trusted schema,
not parsing rejected values. Unknown names are redacted. The entire escaped
record stays below 1,800 characters, before the worker's existing limit, and the
original value-bearing exception is not chained. The review supports both new
structural metadata and old records, explicitly identifying legacy truncation
when details are unavailable. No rejected value, enum contents, full schema,
provider response, prompt, credential or tool payload is emitted.

Release `midstream-validation-r1` changes only two existing common-code members
and adds one helper to worker/review, against their respective verified current
packages. Tools/publisher remain byte-identical redeliveries required by the
existing bootstrap contract. Runtime/archive bytes, Foundry registrations,
source catalog, settings, approvals, identities and network remain unchanged.
Live delivery and any subsequent controlled execution are separate acceptance
steps; the original Midstream result remains failed.

### Latest live outcome - September18 16:47 UTC

The one-file archive-loader correction is deployed as worker
`b47070d2-2842-4dc8-a96b-66db84cd1479`; exact package/indexing verification passed
and bootstrap is disabled/disarmed. The previously unclaimed root was manually
continued after reconciliation and completed at 16:40:23Z. Both actual worker-MI
ingestion slices completed as queried by 16:41:38Z. The initial ingestion blocker
is resolved, not bypassed.

The visible review controls prepared one neutral pump investigation from
SQL-committed observations. Preparation root
`05c45347e36f32fe84db657e8741dea5d37832989dde5e9a3fc0756bd6cad015`
produced candidate SHA256
`7b58f1b4adfd3c59c321862a05866b285de4a6b6e88a23a79179359c23e3229c`.
SQL, projection authentication and Search readiness passed. Live diagnostics
confirmed `observe_only`, `caps_enforced=false` and null effective spending
limits, with historical totals and unknown reservations preserved.

Model workflow
`af6b0f7cba1680b2e93e7e4d67374476a30d1928afb3321ea89a4180856e4182`
was advanced once at 16:44:01Z. Review returned HTTP503; SQL reconciliation
confirmed **failed /no saved result /no projection child**. Safe diagnostics
report `ValidationError /unclassified_failure`. The specific rule, field,
responsible agent and rejected value remain unknown; this is not proof of
the earlier Downstream citation error recurring.

Worker invocation `cbb82a7c-31c5-4b1f-bdc7-42d29add26b0` returned HTTP422 after
101.66 seconds, before the 180-second deadline, with no budget stop reason.
Usage: **16 settled chat calls /11 dispatches /3 settled embeddings**,
USD0.335293156 conservative accounted usage. Cumulative totals are
116 chat calls /73 dispatches /20 embeddings /USD5.212357238, including five
unchanged historical unknown chat reservations. These are not Azure invoices.
Six agent names occur in call accounting; successful stage output is not proven.
No retry, human decision, closure or knowledge publication occurred.

Evidence: `.build/midstream-archive-r1/live-release-verification.json` and the
`midstream-ingest-slice*`, `midstream-investigation-*`, `midstream-model-*`
session browser receipts. The test reached real AI execution but **did not pass
end-to-end acceptance**. Earlier checkpoints below retain the sequence of events.

### Earlier Midstream checkpoint - before archive correction deployment

The user requested a new Midstream-only simulation and review-portal analysis.
Run `979970bd-bde2-465b-a768-7b0f18aeec3d` ran for 20 minutes from
2026-09-18T14:11:08Z, passed its ten-minute queryable baseline, and received one
five-minute browser intervention at 14:24:34Z. It automatically recovered and
stopped; a 14:40:14Z read confirmed no pending/parked records or delivery errors.

Independent Azure Log Analytics capture at 14:41:45Z verified **200 unique
neutral observations /20 frames**, including 80 measurements for the actual
fictional pump. Vibration changes from approximately 1.400 to 2.229-2.466 mm/s;
power changes from approximately 246.2 to 258.3-261.9 kW across five frames.
The final 14:30:22Z frame returns to baseline values. One post-intervention
frame does not prove sustained recovery. Scenario/evaluator labels are excluded
from agent evidence. Operator readback is not worker-managed-identity SQL
ingestion proof; that and the new analysis remain pending.

The user explicitly authorized application AI usage-cap removal for future
tests while retaining authentication, source/actor/expiry approval, synthetic
restrictions, citation validation, execution timeouts, human review, technical
token/context bounds and no automatic retries. Accounting and historical
unknown reservations must remain intact; default configurations stay enforced.
This permits billable usage without an application spending ceiling.
After clarification that the deployed worker allows three embedding calls
per workflow (five is only a contract maximum), and that the remaining three
could cover this test, the user explicitly reconfirmed the same policy choice.
Publisher retains its 120-second deadline; worker retains 180 seconds.
The implementation and exact observe-only settings are now deployed on all
relevant services, with host-specific policy/approval hashes read back.
Effective runtime behavior has not yet passed acceptance.

Delivery receipts and Function indexing were verified at 15:48:07Z; review
retains anonymous HTTP401, and bootstrap is disabled/disarmed. Source registration
became visible in a fresh authenticated browser after the post-build review
restart. The first model-free root was queued successfully:
`a58e761a81ab81fa6a4881f2faa6ea48e4d0d32cee40a77203db2964bce30314`.
Its one advancement returned review HTTP503 at 15:57:41Z. Independent worker
telemetry shows application HTTP422 in 2.35 seconds, not a provider model failure.
SQL lookup confirms the root is still queued, without children, failure or
result. No paid call or repeat advancement occurred.

The worker is Running/enabled/Normal. Exact complete-factory reproduction found
`ContractError: archived detector policies must remain disabled`: archive2
truthfully retains Downstream's historical enabled detector, and the loader
rejected it before claiming the active job. The local correction preserves
historical metadata while archived workers remain recovery-only, with zero
detectors and projection handling only. Complete-factory regressions now cover
active root advancement into 15-minute and nine-minute ingest slices, plus
rejection of archived ingestion/detection/reasoning. The one-file worker repair
is validated and prepared for deployment; no configuration or archive edits.
Correlated exception and
warning queries returned no records. This is a runtime acceptance blocker,
not proof of completed consumer ingestion or analysis.

Evidence: `.build/midstream-r1/source-preflight.json`,
`.build/midstream-r1/settings-before.json`,
`.build/midstream-r1/live-release-verification.json`,
`.build/midstream-r1/settings-activation-verified.json`, and the dedicated browser operation
journals. Existing failed Downstream workflows have not been retried or changed.

### Live diagnostic release - September18 13:55 UTC

The user-approved review-only diagnostic release is live as provider deployment
`801c33f6-51d7-4691-a9f6-068920101e0c`. Exact-version startup is recorded at
13:54:20Z; provider/runtime verification at 13:55:11Z also confirms anonymous
HTTP401. Existing review identity and resource-scoped evidence-reader,
telemetry-publisher and Key Vault roles are unchanged.

The authenticated read at 13:55:32Z classified the retained replacement failure:
`schema_keyword=enum`, path
`properties / citations / items / properties / source_id`.
The returned citation source ID failed the runtime's trusted-source enum
validation. This identifies the rejected field/rule, **not** the rejected value,
the responsible agent, or proof that changing the prompt alone will fix it.
The existing allowlist remains enforced.

The classifier returns only recognized structural metadata from the bounded
retained exception; it does not echo model output, rejected values, schema
contents, URLs or arbitrary exception text. Unknown field names are redacted;
malformed/ambiguous records and records truncated before their header remain
explicitly unclassified. All-history accounting is unchanged:
**100 chat requests /62 dispatches /17 embeddings /USD4.877064082**.
No job advancement, inference, embedding, approval or publication was performed.

Only `agentic_itops/review/diagnostics.py` changed in the 77-member review ZIP.
ZIP SHA256:
`e0c1272d2c8cd4ebf772d8ae5c7921f1ca721d0e304ea0855f6cbbb8b6d91b58`.
Fresh preparation/validation/deployment completed; 169 focused checks passed.
Ordinary platform warm-up completed without a restart or repeat upload.
Evidence: `.build/schema-diagnostics-r1/live-release-verification.json`,
`.build/review-schema-diagnostics-r1-journal.json`, and authenticated session
receipt `schema-diagnostics-live-budget-01.json`.

### Replacement execution

The user explicitly approved bounded provider diagnostics, an 8,192-token
per-response ceiling, and one replacement of the failed exact-candidate
reasoning attempt. Expiry remains September19 02:00 UTC; cumulative USD10,
144 model requests, 96 dispatches and 20 embeddings are unchanged.
Review is deployed as `01beaae9-3b48-4a16-945b-106a966aeb0e`, with exact-version
startup, anonymous HTTP401 and the new authenticated control verified.
All four service deliveries were independently verified at
2026-09-18T13:14:05Z; bootstrap is disabled/disarmed with temporary settings
removed. The one-replacement authorization was applied/read back at 13:15:42Z.

| Service | Provider deployment receipt |
| --- | --- |
| Worker | `b7562418-db9b-4cb9-a9da-29703ffc2ba2` |
| Review | `01beaae9-3b48-4a16-945b-106a966aeb0e` |
| Tools | `e94e0509-1db1-42f8-a7e7-a0d981010201` |
| Publisher | `37d5f36a-8972-40c5-aff1-984deb50b2b3` |

Model-free preparation
`bde7c8dad0a1b6d0b40db74343d8d7180955764e84e4e45a1d560a6b4040e5ce`
completed at 13:17:30Z. The live UI independently shows the original failed
workflow's diagnostic at revision1, processing **failed**, observed lifecycle
**unknown**, review **pending**, with no validated proposal or gate receipt.
Its result hash is
`c5d4bfaf7c24abcee80616c801a887023c09581b97905dd19107be9284e1bf2f`.
This is not a successful model result, acceptance or closure.

Preparation queued exact replacement
`dee48f5a3a186085e7353e7c3ad95382c06793f47e3b3bec2abba19eb5212c53`.
Fresh authenticated preflight confirmed queued/no result, exact candidate hash,
SQL, projection authentication, fixed-vector Search and unchanged accounting.
It was advanced **once** at 13:22:32Z. The UI returned HTTP503; it was not retried.
Worker telemetry records that invocation returning HTTP422 after 56.47 seconds.
Following ordinary SSO renewal, SQL-backed lookup at 13:27:59Z confirms
**failed / no result / no child projection**. Budget diagnostics identify
`ValidationError`; the then-deployed safe diagnostics did not identify its field.
No correlated exception/error trace was returned by the exact-invocation query.
Do not infer a rejected value, failing agent or token-limit failure.

This replacement used **7 chat requests /4 dispatches /2 embeddings**, all
provider calls settled, under the approved new policy hash
`b6e7118703748cf70e252984834f33a732d6978d6d233a36c313135b271c0e2f`.
The 13:28:10Z all-history totals are **100 requests /62 dispatches /17 embeddings /
USD4.877064082**, including five preserved historical unknown chat reservations.
Remaining allowance is 44 requests /34 dispatches /3 embeddings /USD5.122935918.
These are conservative accounting totals, not an Azure billing invoice.

**Downstream paid work is stopped:** the one replacement is consumed. No third attempt,
budget reset, guard relaxation, human decision or knowledge publication occurred.
Correction: the live worker's three-call workflow allowance fits the three
remaining embeddings; the earlier five-call estimate was a contract maximum,
not the deployed setting. It does not renew the consumed replacement permission.
The separately approved read-only diagnostic release above
now identifies the failing field/rule without another model call.
Full all-nine acceptance, lifecycle processing,
knowledge flow and runtime negative-DML proof remain unfinished.

Evidence: `.build/replacement-r1/live-release-verification.json`,
`.build/replacement-r1/approval-verified.json`, and session browser receipts
`replacement-prepare-queue-01`, `replacement-prepare-advance-01`,
`replacement-paid-preflight-lookup-01`, `replacement-paid-preflight-readiness-01`,
`replacement-paid-preflight-budget-01`, `replacement-paid-advance-01`,
`replacement-reconciled-lookup-01` and `replacement-reconciled-budget-01`.

### Deployed replacement contract

Provider diagnostics are implemented with this bounded stored format:
`AgentFailure: provider_response_failure_v1 agent=<token> status=<token> incomplete_reason=<token> provider_error_code=<token>`.
Each token is allowlisted; missing or unknown metadata is `unclassified`.
Raw provider messages, model output, URLs and credentials are never echoed.
Review recognizes only the strict format; legacy or malformed failures remain
safely classified. Accounting settles before failure is raised, without retry
or further dispatch. This does not recover the missing provider reason from
the already failed live attempt.

The replacement path uses a server-held `replacement_authorization` containing
the exact `failed_workflow_id` and `max_output_tokens: 8192`, alongside the
existing actor, expiry and detector candidate binding. An Operator queues
`POST /jobs/<failed-id>/replacement-jobs` with an empty body, CSRF protection
for cookie authentication, and an idempotency key. Optional `root_job_id`
context authorizes legacy children through their immutable SQL lineage.
The private worker independently checks the failed first attempt, candidate
derivation, settled provider accounting and all-history remaining capacity.

Preparation is model-free: review independently derives a **failed-workflow
diagnostic** from SQL/evidence into revision1, explicitly without retained
successful model outputs. The original failed application row remains without
a model result; its queue state and charges are unchanged. Preparation then
queues one deterministic replacement, whose paid advancement is a separate
explicit action. An atomic guard reserves revision2 once, rejects third attempts
and indeterminate replay, and preserves all prior history. Failed diagnostics
are not eligible for successful analysis acceptance, closure or publication.

No SQL schema, role, network, credential or agent-version change was made.
The existing bootstrap requires three Function packages; tools and publisher
therefore reused their exact previously deployed ZIP bytes, while worker
and review received the correction. Fresh validation, frozen package/source
verification, live release verification and post-deploy preflight completed
before the single paid advancement above.

## Previous checkpoint: provider failure after verified candidate admission

The source-context correction is deployed as
`48a941d5-17bf-4f11-9093-b9fd53b201fd`; exact runtime startup, anonymous HTTP401
and the unchanged review Key Vault Secrets User role are verified. Actual
authenticated child lookup, readiness and budget inspection succeed.
Worker evidence SHA256 is
`90f1e1b7c2315ed1bc6da8b468799f5e299cdaf4f8a0277de5303ccdd8b48da8`,
exactly matching the independently derived expected candidate. Readiness also
proved SQL access, worker-to-review authentication and model-free Search.

The user-approved exact-candidate renewal was applied and read back at
03:32:40Z. At 03:34:08Z the single authorized reasoning attempt began. Its UI
request returned HTTP503, and independent SQL lookup confirmed **failed, no
result, no projection**. Azure worker exception telemetry at 03:36:21Z reports
`AgentFailure: provider response did not complete successfully`.
The existing mapping does not preserve provider status or incomplete reason;
do not claim proven output-token exhaustion. The configured response limit is
2,048 tokens, and the failure occurred before the workflow deadline.

All seven reasoning agents have settled calls, but this is not proof of
successful stage outputs. This attempt consumed 18 requests / 12 dispatches /
3 embeddings. At 03:36:53Z all-history totals are **93 requests / 58 dispatches /
15 embeddings / USD4.834850548**, including five unchanged historical unknown
chat reservations. Remaining cumulative allowance: 51 requests / 38 dispatches /
5 embeddings / USD5.165149452. No resets, automatic retry, replacement workflow,
fabricated acceptance/closure or knowledge approval occurred.

The one-attempt-per-incident/kind guard intentionally blocks blind reruns.
The user has now authorized the bounded diagnostic improvement and one replacement
described above; subsequent deployment and execution are recorded above. Lifecycle agents,
knowledge acceptance and runtime negative-DML proof remain incomplete.

Artifacts: `.build/review-descendant-r1-live.json`,
`.build/activation-r1/model-approval-verified.json`, and session browser receipts
`activation-model-readiness-01`, `activation-model-budget-01`,
`activation-reasoning-advance-01`, `activation-reasoning-reconcile-01` and
`activation-reasoning-budget-01`.

## Previous checkpoint: activation deployed and actual ingestion/detection complete

Activation-r1 completed on worker `94c1bcdd-3699-47ab-9a69-81adcf0727e5`,
tools `68a997ab-fb58-4743-829b-7874141bf3fa`, publisher
`3b3a27c8-62b2-4da8-a844-7d40c6e394e3` and review
`9ec6ba3c-ce54-496d-a3c2-aec97334042f`. All three Functions index their expected
routes, review logs confirm exact-version startup, anonymous review remains
HTTP401, and bootstrap is disabled/disarmed with temporary settings removed.
Evidence: `.build/activation-r1/live-release-verification.json`.

Scope-only Key Vault activation retained old entries and credentials; new
catalog SHA256 is `68ecb0b818bb649f77044e73c036e7a46cea98ff81bd55c81bbac677866278d7`.
The existing reference remains resolved, and ordinary SSO plus a fresh GET
recovered a transient dependency response during restart. No auth bypass or
credential change was used. The new source is visible in the authenticated UI.
Original completed workflow lookup succeeds through archived runtime routing.

The actual worker identity ingested the Azure observations into immutable
evidence and SQL under batch
`1155809d13f926677fc15e73276e2b1b8dcd91d0d93f36d1fb333848d7c43af8`.
Root `66811fa44d48e4c462bd10fa1ba51027a7087a87c2aef2f82192874bcffe4726`,
ingest `33b5dea14e83e1462b6afac5a348960a6070939e321808f24adfe763eea5d3ab`,
coverage and detector jobs completed. Both derivations returned the same
complete-as-queried snapshot
`72533dd9be8a90a50de45c3a382f2e12fac4742f12850b391fb24b8fe327dc0f`
and deduplicated one reasoning job:
`ada1cfc0e280c5397d5867d6560cb790b76994580249f3131202c7ea4a129c6c`.
That model job remains queued; model authorization has not been renewed on the
worker, and no new inference or embedding call has occurred.

**Observed error:** direct child advancement returned HTTP403 `scope forbidden`
because internal consumer payloads lack nested review source metadata.
Existing authorized-root advancement successfully completed those two model-free
children. **Correction in progress:** optional explicit root context for child
lookup/diagnostics/recovery, verified only through bounded immutable SQL lineage
and existing source authorization. No stored job, scope catalog or permission is
rewritten. Fresh candidate readiness/accounting and the review-only correction
must precede model admission. Human lifecycle and knowledge gates remain pending.

## History: activation preparation and authorization

All nine **version 3** prompt agents have now been registered and read back in
the existing Foundry project. The exact registration plan changes instructions
only; tools, schemas, model and sampling are unchanged. All 276 focused
guidance/portfolio/registration checks pass. Versions 1 and 2 remain intact.
The running worker is still bound to version 2 until the application release;
registration alone does not claim successful reasoning or lifecycle completion.
Receipt: `.build/activation-r1/registration-receipt.json`.

The user approved the remaining implementation/deployment plan and renewed
model authorization through **2026-09-19T02:00:00Z**, under the unchanged
all-history USD10 / 144 model requests / 96 dispatches / 20 embeddings limits.
The renewal is not yet applied to the worker setting; no new inference has
been started. Ordinary SSO renewal restored the budget read after an expired
browser session returned HTTP401. The 02:02:22Z authoritative response still
reports 75 requests / 46 dispatches / 12 embeddings and USD4.698913912,
including all five unknown chat reservations.

An actual operator-identity Log Analytics query at 01:57:59Z read the exact
20 previously verified observations. Frozen activation bounds are
22:00--22:20Z on September 17 for observations and 01:30--01:35Z on September 18
for ingestion. The approved exact-signal policy, enabled without changing its
calibration, hashes to
`76e4620183918758f768c0baae3787a64b84809ec3c97807a2fc9d822d204f50`.
Local derivation from that Azure response produced expected candidate hash
`90f1e1b7c2315ed1bc6da8b468799f5e299cdaf4f8a0277de5303ccdd8b48da8`.
This is **not yet a consumer-MI read, SQL ingestion receipt, or admitted model
workflow**. Live detection must independently reproduce the hash before
candidate-specific model admission.

Implemented source changes require exact source/policy/candidate authorization,
complete SQL-committed provenance, ongoing approval-expiry checks and an atomic
single model attempt per incident/workflow kind. Existing human closure remains
mandatory for lifecycle work. A scope-only bootstrap operation preserves the
private Key Vault reference and session/OIDC credentials while checking the
previous catalog hash and retaining old access. Runtime routing and prompt
clarifications are being completed separately; deployment remains pending.

Frozen preparation artifacts are under `.build/activation-r1`. Existing
selector release, original case and upload receipts below remain unchanged.

## Previous checkpoint: selector release and 20 observations delivered to Azure

Selector-r1 is deployed on all four services. Provider receipts:

| Service | Completed deployment |
| --- | --- |
| Worker | `f8a22de5-92c8-4e3e-a299-3594695dde8d` |
| Tools | `1784e918-6578-4130-b8ac-5722d7a761b2` |
| Publisher | `987dfa73-7cd4-4723-9758-48fece062ae2` |
| Review | `9a834799-6fbe-400d-9a92-aa743abe2253` |

Functions were restarted only after completed builds and index 8/8/1 routes.
Actual worker-to-review authentication, SQL evidence access and guarded Search
readiness passed at 2026-09-18T01:31:19Z without inference or embedding.
Bootstrap cleanup verifies stopped/disarmed/disabled, with temporary manifest
and attempt settings removed. The authenticated saved revision API returns the
same result hash and ETag; the complete final budget response is unchanged.

The user explicitly amended the initial no-new-role plan and requested a
**permanent** Monitoring Metrics Publisher assignment for their current account
on `iot-dv2-us92-0914-dcr` only. Assignment
`1426ae29-f08c-4f3a-baf7-4aaac166073c` is retained intentionally. Inherited Owner
alone does not grant the required Telemetry/Write dataAction. No producer import
endpoint, producer code change, additional identity grant or broader scope was
introduced.

The single 20-row upload returned HTTP 204 at 01:31:19Z, request
`c3095815-a856-4145-b4b8-80188ef70352`. Independent Azure readback at 01:35:52Z
verified **20 physical rows / 20 distinct EventIds**, exact run
`eeea15c9-1ced-4fbc-a5e9-062237b1fd1e` and complete observation content.
Server-side ingestion time is `2026-09-18T01:31:23.0589543Z`.
Azure dynamic JSON represents `Payload.clock_uncertainty_ms` as `3` rather than
submitted `3.0`; this explicit, value-preserving normalization is the only
permitted comparison adjustment. Original and queried canonical hashes are
retained separately, not falsely reported as byte-identical.

Proof artifacts (git-ignored): `.build/selector-r1-live-verification.json`,
`.build/scenario-operator-ingestion-role-r1.json`,
`.build/acceptance-analyzer-r2-upload-r1.json` and
`.build/acceptance-analyzer-r2-azure-readback-r2.json`. Original offline artifacts
and the first readback receipt remain immutable. Only neutral observations
were sent; the normal control and evaluator-only answers were not uploaded.

**Not yet activated:** runtime source registration still selects the original
run; the scenario detector policy remains disabled and model approval expired.
There is no new scenario workflow, human acceptance/closure or knowledge
approval/publication. All-nine end-to-end acceptance remains unfinished.
Delivery does not authorize operational use.

### Delivery history: initial timeout and recovery

The user approved deploying the prepared code and neutral observations to the
existing environment, without new roles, model calls or review decisions.
The selector-r1 release changes only the detector selector module and adds the
offline evaluator module; all runtime artifacts and nine v2 bindings are
byte-identical to projection-recovery-r1. The focused suite now has 315 passing
checks, including service packaging.

Review deployment `9a834799-6fbe-400d-9a92-aa743abe2253` completed at
2026-09-18T00:51:24Z; startup identified that exact version at 00:54:17Z.
During transition, ordinary sign-in returned a dependency-unavailable response.
After startup, the authenticated dashboard again showed the preserved case.
The actual 01:07:00Z budget response is unchanged: 75 model requests,
46 dispatches, 12 embeddings and USD4.698913912 conservatively.

The first bootstrap upload timed out before receiving a provider receipt.
Read-only reconciliation found no matching deployment and the previous
September 17 deployment still active. Bootstrap was stopped/disarmed/disabled
and the original journal preserved. A distinct second attempt uses the same
frozen package, verifies healthy SCM/no pending upload before submission, and
retains guaranteed cleanup. That second attempt completed successfully as
recorded above.

The initial preflight found zero rows for the new run in the producer workspace.
The producer managed identity had DCR ingestion permission, but no import API.
The subsequent explicit permanent user grant and verified upload are recorded
above. The validated source artifact is under
`agentic-itops/.build/acceptance-analyzer-r2`, not under `domainsV2`.
No detector activation, source rebinding or model authorization has occurred.

## Offline follow-up: separately labeled analyzer evidence package prepared

The user approved a separate synthetic acceptance scenario and its bounded
offline preparation plan, preserving the live weather-station result and human
gates. `scripts/prepare_acceptance_scenario.py` now calls the existing Domains V2
`analyzer_delay` replay and observation constructor, with no Azure/network access.
Scenario controls and expected answers remain evaluator-only, not telemetry.

The actual producer emitted 200 rows in each changed/normal case. An exact
entity/location/tag/phase selector matched 20 observations per case. Baseline
median 0.992142 and a 0.02 change threshold yielded one episode for four
0.952142 samples followed by observed recovery at 0.992142. The normal control
yielded zero episodes. For the 22:00Z simulated start, onset is 22:12Z,
firing confirmation 22:14Z and observed recovery 22:18Z. This proves a
synthetic numeric reference change, not an operational incident or intervention.

196 focused checks pass, including actual producer integration with sockets
denied, byte-identical replay, old policy-hash compatibility, no unintended
target matching and rejection of stale/missing/mislabeled/conflicting evidence.
Frozen package `.build/acceptance-analyzer-r2` includes neutral JSONL, a disabled
rollout policy, evaluator proof and file hashes. Exact identities/source hashes
are recorded in `.azure/deployment-plan.md`.

**At that offline checkpoint:** no new Azure ingestion, source registration, model run, approved
knowledge, result or human decision. The existing single-run runtime and
operator-only approval guard require a separately reviewed rollout/admission
plan; merely uploading this fixture would not resolve the approved-knowledge
cold start or guarantee a complete seven-agent result. The current recovered
weather-station result remains incomplete and unchanged.

## Prior checkpoint: review transfer recovered; incomplete analysis blocks acceptance

Projection-recovery-r1 is deployed on all four hosts, with 8/8/1 Function indexing
and verified bootstrap stop/disarm/disable cleanup. Existing frontend client
authorization, roles, private networking, budgets and nine v2 bindings are
unchanged. Actual dependency/authentication readiness passed at 21:52:33Z.

Recovery job `4b9e86ba81c6823479775428dded9848ec9d937e434458689bcf426fc2e84b3b`
completed as **review_projected** at 21:53:41Z, without rerunning the saved
reasoning result. The authenticated UI now displays case
`investigation-c4b40d824089167bf98d42eddbcea9fd0d33bbfed1fea251da15ed905d7c0901`,
revision 1, result hash
`9c4ccbb2811b105292aa88fe0f14dfbd03023941c5ffc64c4d2b4b44ca9610d9`.
The original completed reasoning and failed projection remain immutable.
The entire before/after budget response is equal: 75 requests, 46 dispatches,
12 embeddings, USD4.698913912 conservatively, including five unknown reservations.

**Actual result:** processing `incomplete`, human review `pending`, observed
lifecycle `unknown`. RCA completed; the other six invoked reasoning stages
report `insufficient_evidence`. Both lifecycle stages remain `not-triggered`.
The retained synthetic weather-station series repeats three temperatures; it
does not establish a real incident, customer impact or a discrete change.
Independent baseline, authorized resource context and approved knowledge were
unavailable. No action is proposed; the trusted gate receipt says
`not_evaluated` / `no_action_proposed`, with execution prohibited.

**Remaining gate:** incomplete analysis cannot be accepted by the existing
review contract, and closure requires prior acceptance. Do not relabel this
result completed, fabricate acceptance/closure, or renew model approval just
to retry unchanged evidence. A reviewed evidence/scenario decision is needed
before further reasoning; the current model approval is expired. Human review,
the two lifecycle agents, knowledge approval/publication/retrieval/revocation,
and runtime negative-DML acceptance remain outstanding.

Exact delivery IDs, ETag and proof locations are in `.azure/deployment-plan.md`.

## Previous recovery checkpoint: frontend fixed; explicit model-free transfer being delivered

The specifically approved two-client EasyAuth policy is applied, with exact
read-back proving all other settings preserved. Human sign-in and the actual
worker-to-review `Results.Project` probe passed at 20:24:31Z after review reload.
The original projection was then reconciled as failed (`advanced: null`);
completed reasoning and its immutable child receipts were not reset.

The local recovery UI submits only an authorized completed workflow ID to
`POST /jobs/<id>/projection-jobs`, with an empty body, Operator role, CSRF and
actor-bound idempotency. It uses the existing private enqueue API and verifies
the returned SQL job against the parent's exact source/result receipt. A separate
source-bound projection job is advanced explicitly; no model is rerun and no
review decision is implied.

Projection execution and read-only job lookup use a model-free factory. Expired
model approval or an unrelated model-only amendment setting cannot prevent
recovery of an existing result; model paths retain their strict checks.
376 Python and three UI checks pass, including preserved failed/parent records,
legacy result recovery, unknown-response deduplication and pending human review.
These recovery changes were subsequently deployed as recorded above. Runtime budgets, agent versions,
permissions and the newly corrected frontend policy remain unchanged.

## Latest checkpoint: projection diagnostic release live; frontend rejection identified

Projection-r1 is deployed on all four hosts; Functions indexing is 8/8/1 and
bootstrap cleanup is verified. Its same-role read-only probe now fails closed
before model admission when review projection authentication is unavailable.
The Azure EasyAuth detector reports that the worker client does not match the
allowed applications. The worker's existing `Results.Project` role and backend
caller allowlist are correct. ARM's empty client allowlist does not establish
the effective frontend behavior; no permission was added speculatively.

The proposed fix is to explicitly allow **only** the existing review application
and worker managed-identity client at the review frontend. It is awaiting
specific authorization. Tenant/audiences, mandatory authentication, backend
worker OID/role checks and real human approval gates must remain unchanged.
After that change, verify normal human access and the actual machine probe,
then reconcile/recover projection without rerunning the completed reasoning.

The 17:47:12Z budget receipt confirms no additional inference or embedding spend:
75 requests / 46 dispatches / 12 embeddings / USD4.698913912 conservatively.
Temporary application logging was restored to its original Off state and
verified; the platform detector supplied the useful diagnosis. Deployment IDs,
hashes and exact evidence locations are in `.azure/deployment-plan.md`.

## Current checkpoint: genuine reasoning result persisted; review transfer blocked

Allowance-r1 is live on all four services. After completed deployment, Functions
were explicitly restarted and the new budget contract was verified. Function
indexing remains 8/8/1; bootstrap is stopped, disarmed and disabled. Exact
delivery IDs/hashes are in `.azure/deployment-plan.md`.

The ninth reasoning workflow
`594f4cb1c023f43d213b0a631c0d1bf7ec0ce98ac61dcef0be1ad38fc22a74ef`
completed at 2026-09-17T16:35:39Z, with 19 settled model requests, 12 dispatches
and three embeddings. Seven actual agents and trusted gate validation produced
the genuine 54,798-byte result at
`evidence/9c4ccbb2811b105292aa88fe0f14dfbd03023941c5ffc64c4d2b4b44ca9610d9.json`.
Output status/quality and human acceptance remain unverified.

The user-approved SQL chat limit is now actually USD9.90, plus USD0.10 for
embeddings. The atomic amendment preserved all historical charges/deadlines and
was disarmed at 16:38:03Z. Totals: 75 requests / 46 dispatches / 12 embeddings /
USD4.698913912 conservative accounting, including five unknown reservations.
Remaining cumulative authorization: 69 / 50 / 8 / USD5.301086088.
This is not an Azure subscription billing cap.

**Error:** projection child
`6d306b2bacf5b0bb3aaac80a63b3f67fe025a4014a75f37df35b7533bec915cc`
received an internal HTTP403 at 16:41:39Z. Public advancement must use the
source-scoped completed reasoning parent, whose immutable child receipts select
the pending projection without rerunning the model. A flat internal child does
not itself carry the domain needed by public authorization.

**Diagnosis/fix:** the worker already has `Results.Project`; configured identity,
audience/scope and caller allowlist agree. No extra role or network change is
justified. A local read-only projection authentication probe and safe fixed-code
errors are being prepared to identify the rejecting layer and to include this
dependency in pre-model readiness. The probe writes no result or budget rows,
and does not replace full result projection acceptance.

The two lifecycle agents, real human closure/knowledge decisions, actual
publication/retrieval/revocation and runtime negative-DML acceptance remain
outstanding. No fabricated result, synthetic human judgment, model retry, or
accounting reset was used to claim completion.

## Previous checkpoint: coordinator release live; timeout and allowance follow-up

Coordinator-r1 delivered all four services, independent indexing 8/8/1 and
bootstrap cleanup verified. Provider IDs and exact hashes are recorded in
`.azure/deployment-plan.md`. Normal review sign-in renewed at 15:26:00Z;
model-free SQL/Blob/Search readiness passed at 15:26:25Z.

The eighth reasoning job
`deba22fbdaf0313b701f3d2ccad731b774834619e076c0c350eb03b2a40d58e4`
ran once, then failed with `APITimeoutError` at 15:30:52.202Z, just before its
15:30:52.616Z budget deadline. 18 provider calls (17 settled/one unknown),
11 dispatches and two embeddings; no complete result or children.
Totals: 56 calls / 34 dispatches / nine embeddings / USD4.213767404 conservatively.
Remaining approved cumulative allowance: 88 / 62 / 11 / USD5.786232596.
The still-stricter SQL month limits leave USD0.786232596 combined usable headroom.
No failed identity, deadline or charge was reset.

Local follow-up adds an explicit atomic month-limit amendment for one fresh
workflow, optional expanded operator-envelope metadata and bounded future
180-second reasoning / 210-second review forwarding. It also exposes actual
monthly limits and per-agent/dispatch accounting through read-only diagnostics.
195 targeted checks pass; these follow-up changes are **not deployed** yet.
The live SQL allowance has not yet increased to the approved USD10.

### Runtime allowance contract

`ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON` still requires `approval_id`,
`actor_oid` and `expires_at_utc`. Optional `limits` must explicitly supply all of
`max_total_nanodollars`, `max_total_model_calls`, `max_total_dispatches`,
`max_total_embedding_calls` and `max_workflow_seconds`. Omission retains the
original envelope; malformed or partial limits are rejected. The HTTP reasoning
ceiling is 180 seconds; the default operator envelope remains 120 seconds.
Total request/dispatch/embedding ceilings still require all-history sequential
admission; this does not add SQL-enforced global count limits.

`ITOPS_CHAT_LIMIT_AMENDMENT_JSON` is trusted deployment configuration, never a
model tool or public request parameter. Its exact fields are `approval_id`,
`workflow_id`, `scope_key`, `month_key`, `policy_hash`,
`expected_limit_nanodollars`, `new_limit_nanodollars`,
`expected_accounted_nanodollars` and `expires_at_utc`.
The approval ID/expiry must match the current actor-bound operator approval;
the approval ID must also equal the new budget policy version. Scope, policy
hash and new limit must match the configured ledger.

Only opening the named, previously unadmitted workflow can apply the amendment.
SQL time, current month, old limit, existing charge and absence of reserved or
overrun calls are checked in one transaction. The limit update and new workflow
row commit together; charges and prior workflow/call/tool rows stay unchanged. Reopening cannot
reapply the change or reset a deadline. Remove the one-workflow amendment setting
after reconciling that workflow, before later lifecycle jobs; retain the approved
budget policy and month limit. No new permissions or SQL schema are required.
Read-only budget/readiness paths never apply the amendment.

## Previous authorization checkpoint: expanded recovery, previous charges retained

The user raised cumulative recovery ceilings to USD10, 144 model requests,
96 dispatches and 20 embeddings. Prior consumption is included: remaining
authorization is USD6.809002058 / 106 requests / 73 dispatches / 13 embeddings.
This is not an Azure billing cap or a reset batch. Existing stricter SQL month
limits remain USD4.90 chat plus USD0.10 embeddings for the next source-only
coordinator-progress delivery. Configuration alone cannot amend an existing
month row; do not bypass it with a new scope or erase historical reservations.
Admission must respect both authorization and actual durable guards.

Version-2 agents, per-workflow caps, service identities and real human gates
remain unchanged. The coordinator-progress source update is being validated
for delivery; it has not yet been demonstrated to prevent early finalization.

## Previous checkpoint: version 2 deployed; full workflow still blocked

Regex-r1 is live on worker/tools/publisher/review with the verified version-2
registration receipt for the same nine names; v1 is preserved. Independent
function indexing is 8/8/1 and bootstrap is stopped/disarmed/disabled.
Provider IDs respectively:
`1b370dcc-a2c6-4499-b658-2a8e9884629d`,
`b31e649c-9506-4dec-86ee-9d1d6e998a5e`,
`d981f6f1-fffc-4721-845a-3e8ea099e149`,
`1efe95e9-a78d-4caf-a666-901f7ece15e5`.
Normal sign-in renewal restored authenticated review access; window-bound
automation retains process/origin/document checks. SQL/Blob/fixed-vector Search
readiness passed without model calls or altered historical charges.

Future worker embedding workflows now allow three calls, preserving scope,
prices, deadline and monthly allowance. Policy hash:
`15e6efe97b9f19ab3497969a7b436c25665beaa3d6c733d31f91d0d890da2845`.
Publisher settings and immutable historical accounting were not changed.

Seventh reasoning job
`5d29466002153389e2898321858366fd6a3cca371abe938353d1cfbb8dd1f01f`
ran once at 14:22:52Z on September 17, then failed at 14:25:03Z:
`AgentFailure: coordinator finished before all specialist/gate stages`.
All 18 model calls settled, with 12 dispatches and three embeddings; no provider
schema/replay failure in this attempt. The exact omitted stage is not established
by the retained error. No-action plans already receive a bound gate receipt.
Failure preceded the deadline; no failed job was retried or result fabricated.

Aggregate consumption: 38 model calls/23 dispatches/seven embeddings,
USD3.190997942 conservative accounting, including four unknown model reservations.
Remaining original allowance: 34 calls/25 dispatches/three embeddings/
USD1.809002058. **Another investigation is not admitted:** all three remaining
embeddings are reserved for postmortem, publication and actual retrieval.
The operator admission baseline was strengthened to retain the seventh attempt.

Local-only follow-up supplies explicit validated/pending specialist stages,
next delegate and finalization eligibility in runtime context, and includes
fixed missing-stage names/gate presence in future completeness errors. Strict
failure remains: no automatic continuation after an invalid final, fabricated
specialists, retries, new agent versions or weaker human gates. 91 focused checks
pass; this follow-up has not been deployed or proven to prevent model early
finalization. The retained live diagnostic cannot identify the omitted stage.

Full acceptance remains incomplete: no persisted complete initial review result,
genuine lifecycle decisions, postmortem/hygiene completion, knowledge approval,
publication, real retrieval/revocation or runtime negative-DML proof.
Latest evidence: `.build/regex-model-exceptions-r1.json`,
`.build/regex-embedding-reserve-r1.json`, regex-r1 deployment journals and
`regex-reasoning-status-01.json` / `regex-reasoning-budget-01.json`.
Exact release hashes and execution details are in `.azure/deployment-plan.md`.

## Previous checkpoint: continuation fix live; schema-only versions authorized

The user explicitly selected "Authorize schema-only versions and continue
bounded recovery" for plan
`775717249d01ac6a2603ba10822c7186612a0094eb325428d8ea9b2f08e9b0f2`.
The same nine names receive a coherent verified version set; v1 is preserved.
No new named agents, infrastructure, permissions, budget or human decisions.

Replay-r1 delivered all four services, independent indexing8/8/1; bootstrap
cleanup and unchanged five-role inventory verified. Provider IDs:
worker `6b2af211-c67a-41ab-9eae-c558084d4731`,
tools `12cf8cbc-eaea-4b2d-b99d-e29c01525f9a`,
publisher `b1d4a9a2-3271-465f-b295-531693fe1a3b`,
review `f8a21dd5-8e20-47c4-8073-45764caf780a`.
Live model-free readiness13:15:55Z passed with Search request
`e9621cd0-b299-11f1-b6cc-3aced2da4294`, zero hits, unchanged scope accounting.

Admission06 and `us93-operator-replay-recovery-20260917-v1` (expiry14:46:55Z)
preceded one advance of reasoning
`f32f68d1e4bdc1b4904d05b773216d3fccf5073e65a4f662c2d2d83b9b9af2c2`.
Browser tab navigation interrupted polling; no resend occurred. A dedicated,
explicitly handle-bound review window reconciled the terminal failure and
budgets without weakening the process/origin/document checks.

At13:20:45Z Foundry rejected the planner's registered output schema:
invalid_json_schema, unsupported regex lookaround at
plan.anyOf[0].properties.proposed_action.pattern,
request `fe0f70d8b31377b480ff2c2aad0bf99a`. The sixth workflow had13 settled
requests before this14th unknown reservation,9 dispatches and2 embeddings.
Thus the replay fix progressed beyond the prior failure, but no complete
seven-stage result or all-nine acceptance exists. Total all-history consumption:
20 model calls/11 dispatches/4 embeddings/USD2.730932974 conservative accounting.
Remaining original allowance:52 calls/37 dispatches/6 embeddings/USD2.269067026.

Prepared an equivalent lookaround-free pattern in portfolio_schemas.py,
regenerated the planner manifest, and added pre-registration rejection of
unsupported lookaround. Exhaustive short-string and newline/Unicode edge
comparisons preserve the original no-action distinction;274 targeted checks pass.
Offline plan `.build/registration/regex-plan-r1.json`:
`775717249d01ac6a2603ba10822c7186612a0094eb325428d8ea9b2f08e9b0f2`.
Only the planner output pattern and its copy in the orchestrator tool contract
change; all other definition values/bindings match the deployed registration.
No version has been created and this schema repair is not deployed.

Updating an immutable registered schema requires a new version set, not another
application-only ZIP repair. The existing registrar handles the same nine agent
names as one coherent verified set, preserving v1. Explicit version-update
authorization was subsequently granted above, clarifying the no-new-resource
boundary. No additional named agents, infrastructure, permissions, financial
allowances or fabricated human decisions are proposed. Do not advance failed
jobs while waiting. Evidence: replay deployment/browser journals and
`.build/replay-model-exceptions-r2.json`.

## Previous checkpoint: readiness live; SDK continuation repair validated

Readiness-r1 is delivered and independently indexed8/8/1. Worker/tools/publisher
provider receipts: `e00f587e-cded-450b-909c-b6fc4ac74c1b`,
`2a2cc3a4-b07d-4977-b38d-24137559e3ba`,
`b8685e7f-37ec-4392-9f6e-0286c9c3b41d`; review:
`35b56fa0-857f-4ce5-aa83-75f7a2942fb9`. Bootstrap cleanup verified.
Its initial pre-upload AlwaysOn gate failed; restoring declared AlwaysOn=true
also changed virtualApplications (root site\wwwroot, /, preloadEnabled=true).
Other top-level properties compared equal; old virtualApplications values and
the cause of drift are unknown. Only the reconciled cleanup-only/no-upload
journal was resumed. Functions/review were deployed sequentially.

Authenticated readiness passed12:37:53Z on September17: SQL job and hash-verified
Blob readable, actual Search query accepted with fixed vector, zero hits,
request `9932e276-b294-11f1-9cbf-f2176905f6f4`. Inference/embedding false,
all-history budget totals unchanged. This is not publication/retrieval acceptance.

Fresh admission and approval `us93-operator-readiness-recovery-20260917-v1`
(expires14:10:29Z) preceded root
`ee5ede6c197341cd58fbdfdfb8a51c83f56d9130c10d89522b8d022a26b52d3b`.
Reasoning child
`313f904ae00dc7779a90c192826bc1956e7a7c60b3831bd70de99f05bc7c6f57`
ran once12:43:57Z, then terminal failed. Search and embedding completed;
the second Foundry request failed HTTP400 invalid_payload/schema mismatch,
request `3746bf3768e616b88de89271b7dca070`. No successful agent stage or
automatic retry. This attempt:2 model calls(1 settled/1 unknown),1 dispatch,
1 embedding,601717226 nanos. Totals:6 calls/2 dispatches/2 embeddings,
USD1.804511962 conservatively accounted, not invoiced. Remaining original
allowance:66 calls/46 dispatches/8 embeddings/USD3.195488038.

Model-free reproduction found SDK-invented null defaults in replayed reasoning
and function-call items. Azure v1 InputItemFunctionToolCall.namespace and
InputItemReasoningItem.content/status are optional, not nullable. Changed the
transport to model_dump(mode="json", by_alias=True, exclude_unset=True), preserving
explicit response values, IDs, aliases and complete history. Two genuine-SDK
continuation cases failed before the fix and pass afterward;233 targeted checks
pass. The failed live request body was not captured; the generic400 does not
identify all offending fields. Corrected live continuation remains unproven.
No budgets, deadlines, tool authority, provider storage, versions or human gates
changed. Reference:
https://github.com/Azure/azure-rest-api-specs/blob/main/specification/ai/data-plane/OpenAI.v1/azure-v1-v1-generated.json

Evidence: readiness-r1 deployment journals, readiness-live-probe-02,
readiness-after-probe-budget-01, readiness-reasoning-once-01,
readiness-reasoning-status-01, readiness-reasoning-budget-01 and
`.build/readiness-model-exceptions-r1.json`. Fifth failed identity is immutable.

## Bounded autonomous recovery authorization

The user selected bounded-autonomous-recovery: remove the separate workflow
attempt-count ceiling, while preserving original USD5/72 model requests/48
dispatches/10 embeddings. At authorization, remaining capacity is
USD3.797205264,68 model requests,47 dispatches,9 embeddings. No new roles,
resources, agents/factories or fabricated human decisions are authorized.
Every failed job/ledger remains immutable, without deadline or charge resets.

The delivered readiness release fixes Search and adds model-free readiness to the
existing review/worker path. It authenticates Operator and source entitlement
before the private worker reads the selected SQL job and hash-verified candidate,
then sends the genuine production Search query with a fixed, non-embedding
probe vector. It returns only scoped proof metadata, never documents or raw
stored errors. It does not call a model/embedding, open a ledger, publish or
advance work. This is request-contract/read-access proof, not semantic quality
or full runtime isolation acceptance.

Read-only budget diagnostics also aggregate all history in both configured
budget scopes, including old policies/months and unknown reservations. These
totals support sequential admission against the original limits; they are not
misrepresented as a new global SQL-enforced request cap. Reconcile current
totals before each workflow, refuse overlapping unresolved attempts, and keep
per-workflow ceilings inside the remaining aggregate allowance.

Source/test checks, package freezing and live readiness delivery are complete.
The broad Python run passed505 checks; its expected-route assertion was updated
for the new worker endpoint, then that check and18 readiness/bridge cases passed.
Both Node UI tests passed. Runtime registration/dependency/budget assets are
unchanged, and existing worker Search/evidence authority was verified.
Proof `.build/readiness-r1-package-proof.json` binds deploy-only manifest
`0bd8539f-e288-4b41-8ce2-dcbcfc3fdcd1`, expiring
`2026-09-17T14:56:30Z`, bootstrap SHA256
`80cfbc28fa9cb5be8bccb0310d65bcad4fabc55c7edc9446789f6a3bb70e6cc2`.
Verified worker/tools/publisher indexing is8/8/1. The review is also updated;
delivery is sequential to avoid shared B1 build contention.
The previous approval expired at05:37:13 UTC; a new
bounded workflow identity must wait for live readiness and budget admission.

## Previous checkpoint: Foundry accepted the request; Search rejected retrieval

The newly authorized child ran exactly once at04:09 UTC. Foundry accepted the
agent_reference request without text and returned a tool call. Embedding
completed, then Search rejected the retrieval with InvalidRequestParameter:
semanticMaxWaitInMilliseconds requires semanticErrorHandling=partial.
This is a different, verified dependency contract failure, not the previous
Foundry parameter error or another diagnosed citation rejection. No completed
agent stage or citation-validation success is implied by the tool call.

The local correction removes only semantic_max_wait_in_milliseconds. It retains
semantic_error_mode=fail, bounded transport timeouts, retries0, partial-response
rejection, source/revision/eligibility filters and human gates.200 targeted
Search SDK wire/runtime/integration tests pass; the fixture now rejects the
actual incompatible parameter combination. This Search correction was later
packaged and delivered in readiness-r1.

The fourth attempt is terminal failed, with1 model request,36546 accounted
tokens,20230430 nanos;1 tool dispatch;1 embedding request,33 tokens,726 nanos.
Totals across all four immutable attempts:4 model calls,1 dispatch,1 embedding,
1202794736 accounted nanos (USD1.202794736, conservative rather than an invoice).
Two earlier unknown reservations remain intact. Remaining original ceilings:
USD3.797205264,68 model requests,47 dispatches and9 embeddings.
No new workflow identity, retry, refund or deadline reset was performed.

Evidence: `.build/reference-model-exceptions-r1.json` and the unique browser
actions reference-reasoning-once-01, reference-reasoning-budget-01 and
reference-reasoning-status-01. The specifically authorized replacement is used.
The operator's direct Search-scope assignment query returned no assignments;
no permission was added for an alternative data-plane probe. Model-free
dependency readiness needs to precede any further model-led acceptance attempt.
Verdict: ESCALATE for the attempt boundary/non-convergence, not APPROVE.

## Previous checkpoint: six-attempt continuation explicitly authorized

The user selected authorize-six-total: one more replacement, six total workflow
attempts, unchanged USD5/72 calls/48 dispatches/10 embeddings. This supersedes
the prior attempt-limit stop below. Human acceptance/closure and knowledge
publication judgments remain required.

Ordinary browser renewal succeeded at04:07:24 UTC. The worker approval
`us93-operator-reference-recovery-20260917-v1` expires `2026-09-17T05:37:13Z`.
Full settings comparison verified that only this approval JSON changed; no
deadline, ledger, budget, agent version or other configuration was reset.
Audit: `.build/operator-reference-approval-r1.json`; Story93 comment7724583.
Authenticated diagnostics again confirmed the preserved third-attempt reservation.

Root `43cfb2b786bbd59cf43abb409c6ccb01b5a7939a01d837db8f0c5db2fb8876c6`
prepared the retained evidence by04:08:35 UTC, receipt
`9470c3b8-8a75-4692-83b9-6e23797c982a`; candidate
`d0242dfb3def2bdae45e314b6d08a49e2ed1c9be07a7226c141f5751cf2f84de`,
277977 bytes. Reasoning child
`f46d2c1dc558895a0a074c07c7ad27294eed1e34c2f6fe57c11951d9d0b1b1ac`
was reserved and subsequently failed as described above. Prior failure IDs are journaled
outside the new domain question. Three prior failures and their complete
conservative accounting remain immutable.

## Previous checkpoint: incompatible request rejected and compatibility repair delivered

The remaining approved replacement ran exactly once on September17 at02:16 UTC.
Its review request returned503. Read-only budget diagnostics and terminal graph
reconciliation then confirmed the failed reasoning child; advanced:null did not
replay inference. App Insights identifies HTTP400 invalid_payload, parameter
`text`: `Not allowed when agent is specified`, request
`5e5c1ef4cd7933f401b3e435693e84e2`. The citation release introduced this
incompatibility; it was not another rate-limit event or verified output failure.

The source repair removes the unsupported override, preserving exact registered
agent versions and their output contracts. A trusted runtime citation catalogue
and refreshed local enum still reject invented IDs; the enum is not enforced by
the provider. Successful receipts label its digest
`citation_validation_schema_sha256`. Provider enum limits no longer apply to
this local-only schema; complete-context byte bounds still fail before spending.
No schema-repair fallback, automatic retry, source substitution or weakened gate.

The compatibility repair passed258 targeted tests and package validation.
An additional real-SDK serialization regression passed with a mock HTTP
transport: the actual serialized request contains the exact agent_reference and
omits text; it makes no network/model call. This is not live provider proof.
Each frozen Function ZIP changes only consumer/responses.py. The existing review
release is retained, avoiding an unnecessary restart. Proof:
`.build/reference-compatible-r1-package-proof.json`.
Deploy-only delivery completed under manifest
`97ae92b3-afe3-4167-8d6e-bc46149d44b4`; its bootstrap ZIP SHA256 is
`333ee7baa2a999a2576d223892066025eb2fa52b451c3fa517855c5c6ed6df3a`.
By02:45 UTC, all three exact provider deliveries completed and independent
indexing verified7/8/1:

| Service | Provider deployment ID | SHA256 |
| --- | --- | --- |
| Worker | `5ce94d0d-4fe9-40a4-b359-8e102ae76083` | `5554debbf96f5af09a0419e373c8a87f6a4d75aa404ca3166f36bc13f7f1f287` |
| Tools | `7395665e-3ead-4103-8753-6c0ca8f618a8` | `31f6624ecb4e03fc4f36d020db778409879673396f2f09dadacc758157156458` |
| Publisher | `377e7881-93cd-4a69-a1a5-e21bc047b23e` | `246d5da25acdf0b405d2863333d7cdd5c5471500ea00fa09d5ba5ccd70228977` |

`.build/bootstrap-reference-compatible-r1-journal.json` records verified cleanup:
bootstrap stopped/disarmed/disabled, manifest and attempt settings removed. The
five existing bootstrap role assignments are unchanged. No SQL-admin window
occurred. Authenticated browser budget lookup reference-postdeploy-budget-01
succeeded at02:45:01 UTC and confirmed unchanged third-attempt accounting.
Review remains available at https://app-review-gcjgvzpp44b4i.azurewebsites.net.

No further model attempt or recovery approval is authorized. The current approval
`us93-operator-citation-recovery-20260917-v1` expires
`2026-09-17T03:44:43Z`; its existence is not permission to create another attempt.

### Completed citation release and browser recovery

The previous citation release did deploy. Function provider receipts:
worker `75319c1c-89d6-479a-99d9-4518fceaaa19`,
tools `1a61a03c-6ba5-420b-804f-89c32beecf21`,
publisher `b5a03871-b643-4d42-8ba1-d8f5e376238a`;
independent indexing was7/8/1. Bootstrap was stopped/disarmed/disabled.
Review receipt `e899711f-61e7-4fd2-bbbc-c6a5dda78a41` has an exact successful
startup at02:02:40 UTC. Following an observed230-second startup timeout, the
review-only startup allowance was changed to600 seconds at02:07:58 UTC, with
all other settings unchanged. That guardrail did not cause the earlier success.

Ordinary same-browser navigation to the fixed sign-in URL restored authenticated
budget access at02:13:48 UTC without exporting credentials or bypassing auth.
Approval read-back verified only the worker approval JSON changed.
Root `491817f49b75277dffa1f8ebc1515174716849e3d612647dde125d4a3a33304f`
prepared the existing committed evidence at02:15:42 UTC; receipt
`edf4815e-87e1-412e-b2f5-6d3c26a3e97c`. It remained an operator-requested
investigation, not a detected incident. Reasoning
`c5e695ea88e42da5c9d010951d959f05974f67de62ac42f51583df60b86a76bc`
then failed as described above.

| Attempt | Accounted nanodollars | API requests | Tools | Embeddings |
| --- | ---: | ---: | ---: | ---: |
| Original429, unknown reservation | 581555040 | 1 | 0 | 0 |
| Rejected citation, settled usage | 19453500 | 1 | 0 | 0 |
| Incompatible request400, unknown reservation | 581555040 | 1 | 0 | 0 |
| Total, conservative rather than invoice | 1182563580 | 3 | 0 | 0 |

Missing usage is not refunded. The specifically authorized replacement is used;
five total attempts cannot fit these three failures plus three successful stages.
The USD5/72 requests/48 dispatches/10 embeddings limits remain unchanged.
No successful initial workflow, all-nine evidence, human acceptance/closure,
Search publication/retrieval/revocation or live negative-DML proof is claimed.

### Evidence and lesson

Evidence: `.build/citation-model-exceptions-r1.json`,
`.build/bootstrap-citation-r1-journal.json`,
`.build/review-citation-r1-journal.json`,
`.build/review-startup-allowance-r1.json`,
`.build/operator-citation-approval-r1.json`, and uniquely journaled browser
actions citation-reasoning-once-01, citation-failure-budget-01 and
citation-terminal-graph-01. Provider receipt/error correlation worked; assuming
two documented parameters were composable wasted an authorized attempt.
The regression must reject text alongside agent_reference. Local mocks establish
request shape and guards, not actual provider success or semantic citation support.
Verdict: ESCALATE for full acceptance; no silent attempt-budget increase.

## Historical checkpoint: citation release prepared (provider assumption disproved)

At01:30 UTC on September17, authenticated budget diagnostics returned the exact
failure: `industry-signal-pack`, `citation is not in trusted evidence`. This was
verified without inference; the prior diagnostic release's exact startup was
also confirmed at00:40:21 UTC. The earlier authentication blocker is not present
in this successful request; its earlier generic failures are not retroactively
assigned an unverified cause.

The attempted correction constrained each request's citations[].source_id with a strict
JSON-schema enum from the trusted workflow evidence set, not from model input.
Trusted retrieval can expand that set for later requests. The caller also supplies
the eligible IDs as explicit runtime state. Provider enforcement was assumed,
not verified, and the live400 above invalidates that assumption. Local
validation enforces membership; original revision, semantic-support, source,
eligibility/revocation and human gates remain intact. No fabricated citation is
repaired or silently accepted. Original registered agent contracts/versions stay
immutable, and per-request schema hashes are recorded separately.

Schema limits are checked before reservation, without sampling/truncation.
References: https://ai.azure.com/api-reference/responses/ and
https://developers.openai.com/api/docs/guides/structured-outputs.
A fixed same-origin Renew sign-in link uses normal platform authentication and
does not replay work, export tokens or weaken JWT validation.

All359 targeted tests pass. Worker/tools runtime registration and budget assets
and all service dependency profiles are unchanged. Frozen deploy-only manifest
`575a0a3f-d951-4121-b66e-a8d66f26acb8` expires `2026-09-17T04:41:11Z`.
The explicitly approved correction mechanism temporarily enables only the
existing bootstrap for delivery, then stops/disarms/disables it. No new roles,
resources, secrets or agent versions. Package proof:
`.build/citation-package-proof-r1.json`.

| Artifact | SHA256 |
| --- | --- |
| Bootstrap | `b012f59ee742097f17c7da7d85c5abdb062909662a7bf8abc094110b6df02157` |
| Manifest | `5c8390141c28128e0f52ca29e227c0da473712785bf0e5b7d4a16f1f7fdf8ea5` |
| Worker | `5e3b90888b3c8b361487c3ac05fc245a1d485cc249915e603f2b6ca6a40e1e6c` |
| Tools | `3d5a1e8db16643d71c7b739f1aa6abd3ad102fcefc3d1bf3dc2ed2970ad87cce` |
| Publisher | `856758468e2366081cdcda9a613080f154806e65c5902ae9b4860e2291b8abfc` |
| Review | `ef44b4e5278a8a808a453ce59e687bdf78344f890ed6a4c1584895f26e5f70f5` |

Delivery and a separately recorded bounded recovery configuration remain pending.
The additional approved replacement is unused. Combined accounting remains
USD0.60100854, two requests, zero dispatches and zero embeddings. Preserve five
total attempts, USD5/72 requests/48 dispatches/10 embeddings. Autonomous routine
execution is authorized; it is not human acceptance of unseen analysis or
knowledge publication.

## Previous checkpoint: recovery deployed; model output rejected

### September 17 pause: authenticated diagnosis not yet available

The review-only safe classifier is implemented and248 targeted tests passed.
The frozen ZIP, SHA256
`5d87411915595f8497a401bc240d8fb61a5c2d8121925a352fb7691d5aeea409`,
changes only review `app.py` and new `diagnostics.py`. It adds exact allowlisted
PortfolioError classifications to the existing Operator/source-scoped budget
lookup and never returns raw stored exceptions or model output. Provider delivery
receipt `d38bec74-7d8c-467b-9d51-7f36d2242b41` completed; exact runtime startup and
authenticated classification remain unverified. Evidence:
`.build/review-failure-diagnostics-r1-journal.json`.

At00:49 UTC the user restored the Edge dashboard, but budget lookup returned401
`invalid authentication`. A page remaining open is not proof that its current
requests authenticate. The user cancelled the subsequent fresh-sign-in request.
Browser-dependent work is paused, without weakening JWT validation or exporting
credentials. No additional model call was made. The fifth-attempt authorization
below remains unused, conditional on establishing and correcting the exact
validation defect. No new bootstrap activation was needed for this review ZIP.

The user subsequently approved diagnosis, correction and one additional
controlled replacement: five total workflow attempts, unchanged financial and
request/tool/embedding limits. Further inference remains gated on identifying
and correcting the exact validation failure. A review-only diagnostic enhancement
is being prepared: the existing Operator/source-scoped budget endpoint will
classify exact known static failure reasons without returning raw exception text,
model output or arbitrary strings. Unknown failures stay explicitly unclassified.

The approved capacity correction succeeded and read-back confirms 300,000 TPM,
DataZoneStandard, the same model/version, RAI policy and NoAutoUpgrade setting.
The frozen Function bundle completed with worker/tools/publisher indexed at
7/8/1 functions. Bootstrap was stopped, disarmed and disabled with read-back;
there was no SQL-admin elevation. Review provider receipt
`4633d4dc-e0f3-4adb-837d-f51653c1a75d` is identified in actual startup logs
at 23:19:14 UTC. Authentication properties are unchanged and all three Key Vault
references are Resolved. After fresh human sign-in, Operator budget inspection
succeeded through the existing browser.

The original failed attempt has one unknown Luna call, zero dispatches and no
embedding admission. Its full 581555040-nanodollar reservation remains charged.
The separately approved recovery identity `us93-operator-recovery-20260916-v1`
was applied with expiry `2026-09-17T01:06:59Z`; full settings read-back confirms
only that approval setting changed. The deployed new-workflow API ceiling is23,
with unchanged monthly scopes and USD5 total.

Replacement root
`68b16c9c0d5963d770b3e78a2d0ff1e453a811c6d922ece377233a275c0c6a94`
prepared committed evidence, then reasoning child
`ce381e9d7127d3f97aa22c2c0d4038c02e2c2957c6ed149c09b394552abbcbd6`
failed after exactly one explicit advancement. This time the provider returned
usage: one settled Luna call, 32965 accounted tokens, 19453500 nanodollars,
zero dispatches and no embedding admission. Terminal graph reconciliation
returned `PortfolioError`, `advanced: null`; it did not invoke the worker again.
The exact validation reason is not yet exposed by existing safe diagnostics.
Do not label this another rate-limit failure or infer successful stage completion.

Combined durable accounting is two requests, zero dispatches, zero embeddings
and 601008540 nanodollars (USD0.60100854, conservative accounting, not an invoice).
Both failed attempts remain immutable. The one authorized replacement has been
consumed: no additional inference may run without a new explicit approval.
Finishing three successful stages after these two failures would require five
total workflow attempts, exceeding the currently approved four, even though
three new23-call ceilings would still keep the global total at71 <=72.

Evidence: `.build/bootstrap-recovery-r1-journal.json`,
`.build/review-recovery-r1-journal.json`,
`.build/luna-capacity-recovery-readback-r1.json`,
`.build/operator-recovery-approval-r1.json`, and the session browser journals
`recovery-original-budget-01`, `recovery-reasoning-budget-01`,
`recovery-terminal-graph-reconcile-01`. All-nine successful inference, human
lifecycle decisions and governed Search acceptance remain incomplete.

## Previous checkpoint: ingestion complete; controlled model recovery pending

Authenticated operation now succeeds through the user's existing Edge window,
using scoped accessibility controls without exporting cookies or tokens. The
earlier generic authentication rejection did not recur in these operations;
its original cause remains unproved. Historical checkpoints below are retained.

Analysis `97dc08c91dd0e23b25c06db5611f0cb1ba5f423d44722f2cf94aaba2d32500fb`
uses the exact observation window `2026-09-14T23:03:30Z` to
`2026-09-15T01:03:30Z`. All eight ingestion slices and sixteen coverage/detector
children completed with durable evidence receipts. Coverage was
`complete_as_queried`, with zero detector candidates. This is not proof that
all nine model agents ran or that the synthetic environment was healthy.

An independently witnessed operator investigation prepared 117 observations
for `asset-weather-station-001`, `fictional-region-new-york-city`, power-grid.
Its first reasoning job,
`677cc5ef5c8a8dbedb6b671aa8c89a0b72b6310e163c48dbfc9c57b0eb9d1639`,
failed on its one explicit advancement. Application Insights records HTTP 429,
`rate_limit_exceeded`, source `rapi`, request
`335a3cbc675db55769e0a489d9c582ec`. No retry was made. Missing provider token
metrics do not establish zero cost; the unknown-usage reservation is retained.

**Approved correction, not yet deployed:** increase the existing DataZoneStandard
Luna deployment from 50,000 to 300,000 TPM, preserve all observation content via
lossless model-only common-field factoring, and expose read-only historical
budget diagnostics through the existing private worker and Operator review UI.
No authoritative evidence, tool output schema, JWT checks, roles or SQL grants
change. The original worker approval expired at `2026-09-16T22:04:18Z`.

The user explicitly approved one replacement investigation under a separately
recorded recovery approval, preserving the failed attempt and shared money
ledgers. Total workflow attempts increase from three to four only for this
failure recovery. The total remains USD 5, at most 72 Luna requests, 48 mediated
dispatches and ten embeddings; no automatic model retries. Read authoritative
accounting before admitting the replacement, and enforce the remaining totals.
The new approval must link back to the failed job, not silently reset it.

Local validation passed 335 targeted tests before the additional capacity
regression was added; Bicep compilation and whitespace checks passed.
Live capacity, package delivery, historical accounting, all-nine successful
provider evidence, genuine human lifecycle decisions and Search publication,
retrieval and revocation remain outstanding.

## Historical blocker: signed-token rejection after UI deployment

The user subsequently reported `invalid authentication`. At 21:26:44 UTC,
independent read-back confirmed unchanged full platform authentication
properties and Resolved Key Vault references. The application returns this
generic error for signature/issuer/audience/time/key-retrieval failures; the
underlying cause has not been established. A stale/expired ID token is only
a hypothesis. Explicit logout and fresh sign-in were requested before any
new submission or advancement. Do not disable authentication or relax expiry.

The user-selected guided browser flow is awaiting a successful fresh-session
result, a receipt with the exact retained window, and first advancement.
Live ingestion/model execution/human lifecycle/Search acceptance remains
unfinished. An independent offline headless Edge check at 21:29:57 UTC verified
the deployed source's six UTC input patterns and actual JavaScript preservation
of seconds/microseconds without using any user session or making Azure requests.

## September 16 authenticated submission checkpoint

The corrected referrer ZIP deployed with provider receipt
`b98d216b-a03c-4030-b4d6-32e20c3f4a6e`; startup logs identify that exact deployed
version as running at 20:35:09 UTC. The user's fresh authenticated analysis POST
returned a durable queued receipt, clearing the earlier middleware 403.
The original ambiguous request was separately confirmed `job not found` via
authenticated browser GET.

Two subsequent user submissions did not match the required worker window:
`35edfc00eaa1f5c32bb9cbe84ad4e11a57273c8e1d8ee8265eadd2a52cc2cc90`
used 23:01 to 13:01, and
`cfa0813abafacd9a7c3e6780c38218b91d8a78402830bc89602ac06a50ed974c`
used 23:03 to 01:03 without the required 30 seconds. Neither was advanced.
They remain immutable queued requests, not completed ingestion.

Work-window inputs have been changed from locale-dependent date pickers to
explicit UTC text fields, requiring seconds and a final Z and allowing up to
six fractional digits. Analysis, investigation and hygiene forms use the same
format; JavaScript preserves the literal timestamp without local-time conversion.
The worker's exact server-held window validation remains unchanged. For this
run, copy `2026-09-14T23:03:30Z` and `2026-09-15T01:03:30Z`.
The user approved the exact template-only ZIP, SHA256
`0875523efce152e5643b1e381e8445098d5c4409d0515b1164a0bd4d89686d7d`.
All 134 related regressions passed, including exact seconds/microseconds in
receipts and all six input patterns. Provider receipt
`76f9a014-538b-4108-8350-166ee0f069cc` succeeded; startup logs identify that
exact deployment as running at 21:06:34 UTC. Evidence:
`.build\review-utc-r1-journal.json`.

The next live gate is a fresh correctly timed browser submission, then one
exact-root advancement to create the eight bounded ingestion slices. The user
chose guided steps in their current browser; no browser credentials were
exported and no isolated browser was opened. The correctly timed receipt and
first advancement result have not yet been received. Neither existing
mismatched-window root should be advanced. No ingestion, all-nine model
execution or publication success is inferred from a queued receipt.

Worker approval expiry was extended only to `2026-09-16T22:04:18Z`, retaining
the original approval ID/actor and every other setting. Full read-back matched;
the existing single $5 batch and shared ledgers remain unchanged. Human review,
closure, knowledge approval and revocation remain separate decisions.

## September 16 sign-in restoration, 19:37 UTC

At the user's explicit request and confirmation, auth-only deployment
`us93-review-signin-restore-20260916` restored the original Entra provider and
enabled platform authentication while retaining the enabled token store.
Deployment succeeded; live properties exactly match the approved template,
and the original Key Vault sign-in-secret reference is Resolved.

The explicit sign-in endpoint returns 302 to Microsoft Entra with the expected
client ID and callback:
https://app-review-gcjgvzpp44b4i.azurewebsites.net/.auth/login/aad?prompt=login&post_login_redirect_uri=%2F

Anonymous homepage requests still return 401, including browser-style HTML
Accept requests; automatic homepage redirection is not confirmed. Human login
must be reconfirmed using the explicit sign-in endpoint. The referrer-policy
ZIP remains undeployed, prior analysis-job reconciliation remains outstanding,
and model acceptance remains expired. No code, roles, consent, credentials,
model settings or bootstrap configuration changed during this restoration.
Evidence is in `.build\review-signin-restore-01.journal.json`.

Source and Azure execution evidence are separate; deployment is not live acceptance.
The parent deployment workflow owns Azure changes, role approval, migrations,
registration, bounded model calls and acceptance. No host schedules or queue
triggers are installed. Synthetic/non-operational enforcement is permanent.

## September 16 runtime checkpoint

All three Function ZIP deliveries completed through private bootstrap. Runtime
deployment `agentic-itops-us93-runtime-20260916` succeeded; bootstrap is disabled,
worker/tools/publisher remain private, and the approved runtime caller matrix is
active. Review is enabled with Always On and all three Key Vault references
resolved. The first ZIP submission timed out without a correlated receipt.
After explicit approval, a separate second submission completed with provider
receipt `eec2f5b5-6362-4be5-a097-93005496c510`. Actual site startup succeeded at
16:29 UTC. Anonymous root and projection requests return 401.

Worker (six) and tools (eight) functions indexed. Publisher did not: actual
Application Insights exceptions identify `FunctionLoadError` because Python
declared `request` while the decorator bound `req`. The source correction uses
`req` consistently and tests the actual SDK binding metadata against the Python
signature. The separately approved corrected package was deployed, and Azure
now indexes `publisher_run_once`. Bootstrap is disabled/disarmed again.
A successful build receipt alone is insufficient.

Human sign-in remains blocked, not validated. The lab admin first received
AADSTS50105 because the five reviewer roles correctly belong to the original
guest operator, not that admin. Live Graph read-back verified the assignment.
The assigned reviewer then reached the tenant's admin-consent gate. The review
application has no required Graph data permissions and no existing OAuth consent;
its actual OIDC request is only `openid profile email`. The existing isolated
admin context has Global Reader, which cannot grant another user's consent.
The user approved reviewer-only sign-in consent and an isolated sign-in by an
authorized tenant administrator. No role elevation, tenant-wide consent, or
assignment-required bypass is authorized. Model workflows, data-feed acceptance
and governed Search publication/revocation remain outstanding.
The user subsequently cancelled the administrator sign-in interaction; its
pending device-login process was stopped. No consent grant was applied.

### Later sign-in follow-up

A fresh login subsequently verified that the user had separately assigned
Global Administrator to the original reviewer. An external `AllPrincipals`
consent for only `openid profile email` was discovered; the user explicitly
chose to leave it unchanged. This session did not create that consent or role.

After the consent gate cleared, review returned `authentication required`.
The deployed App Service token store was disabled even though
`ITOPS_ALLOW_EASYAUTH=true`. The application deliberately requires the signed
`X-MS-TOKEN-AAD-ID-TOKEN`, not the unsigned principal header. App Service supplies
provider token headers only with its token store enabled:
https://learn.microsoft.com/azure/app-service/configure-authentication-oauth-tokens

The onboarding correction enables the review token store in Bicep, with an
infrastructure regression check alongside existing signed-token authentication
tests. The existing host requires an exact, separately approved authsettings-only
deployment and a fresh browser sign-in; unrelated live settings must be preserved.
Do not log, export or request browser tokens. Signature, issuer, audience, tenant,
roles, source entitlements, CSRF and human decision gates remain unchanged.

Deployment `us93-review-tokenstore-20260916` subsequently succeeded using the
explicitly approved auth-only template. Fresh ARM read-back matched the full
approved properties document exactly; token store and required authentication
are enabled, and bootstrap remains disabled. The user then reported the same
`authentication required` response; the observed sign-in failure is not resolved.
A safe browser diagnostic reporting only provider name and ID-token presence
was requested but cancelled. Fresh-session status and token forwarding therefore
remain unverified; do not assume either is the remaining root cause.
The earlier model-acceptance approval remains expired; this auth correction
does not renew it or authorize model requests.

### Browser POST middleware rejection

The user later confirmed dashboard access after an explicit top-level logout
and forced sign-in. A retained-data analysis submission then returned an empty
403 with an Azure middleware request ID, not the application's JSON error.
The supplied non-credential request details show a same-origin request with
Origin present and Referer absent; the response policy was `no-referrer`.
Microsoft documents Easy Auth's browser/session-cookie POST CSRF checks here:
https://learn.microsoft.com/azure/app-service/overview-authentication-authorization#mitigation-of-cross-site-request-forgery

The source correction uses `Referrer-Policy: same-origin`. It permits a Referer
only to the same trusted origin and still suppresses it cross-origin. No CORS
allowlist, redirect domain, CSRF token, role or origin check is loosened. Tests
assert the header on normal and error responses and retain existing invalid
origin/CSRF rejection coverage. A new exact review ZIP approval/deployment and
actual browser POST proof are required. The prior idempotency key must first be
reconciled with its deterministic job ID; do not assume the empty 403 is a
successful queue submission.

Before this correction was deployed, the user disabled platform authentication.
At 19:08 UTC, read-back showed platform authentication disabled and its Entra
client ID absent; /health was 200 and / returned application-generated 401.
The signed-token requirement remains active. No settings were restored and no
authentication bypass was implemented. The prepared referrer correction remains
undeployed; restoring sign-in requires a separately approved configuration change.

## Implemented boundaries

| Source | Responsibility |
|---|---|
| `agentic_itops/service_runtime.py` | Production worker/tools factories; explicit UAMI only, bounded source configuration, receipt-bound nine-agent runtime |
| `application_results.py`, `consumer/runtime_sql/application.sql` | SQL-fenced immutable attempt/result journal and revision allocation |
| `application_jobs.py` | Ingestion fan-out, reasoning, explicit lifecycle/hygiene jobs and durable result projection |
| `service_backends.py` | Six mediated original OpenAPI operations and two bounded Azure Monitor context operations |
| `service_search.py` | Real `ApprovedKnowledgeRetriever`, durable embedding reservations, independent SQL eligibility/revocation checks |
| `service_auth.py` | Signature-verified Entra app-only tokens, exact tenant/audience/client/object/role allowlists |
| `service_preflight.py` | Bootstrap-caller-only source isolation proof using the worker's own UAMI; no inference initialization |
| `service_projection.py` | Independent app-only projection mount on the review host; the worker has **no review approval writes** |
| `services/worker`, `services/tools` | Actual Python Functions v2 entrypoints and host timeouts |
| `scripts/build_service_bindings.py` | Actual deployed host/settings → compiler binding contracts |
| `scripts/package_application_service.py` | Deterministic source ZIP with flattened Linux remote-build dependencies |

`ManagedIdentityCredential(client_id=...)` and SQL
`Authentication=ActiveDirectoryMSI;UID=...` select the host's explicit UAMI.
There is no CLI credential, default credential chain, SQLite fallback, automatic
migration, simulated provider success, native Search tool or invented isolation
approval. Registration artifacts must match the compiled portfolio, exact
project endpoint and all nine numeric versions. Every model binds explicitly to
`gpt-5-6-luna` with `model_defaults`.

## Deployment configuration

Keep tenant-bound JSON in ignored `.azure` state and generated ZIPs in `.build`.
The packager embeds only the named non-secret runtime artifacts under `.runtime`;
it never copies `.env`, local settings, registration journals or credentials.
Environment values must be resolved; empty values, `${...}` and unresolved vault
references fail startup.

Shared worker/tools settings:

| Setting | Contract |
|---|---|
| `AZURE_CLIENT_ID` | Nonzero UAMI client UUID; different for each host |
| `ITOPS_SQL_SERVER`, `ITOPS_SQL_DATABASE` | Explicit private SQL hostname/database |
| `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` | Service API v2 token tenant and exact audience |
| `ITOPS_SERVICE_CALLERS_JSON` | Array of `{oid, client_id, roles}`; exact app principal and application roles |
| `ITOPS_EVIDENCE_BLOB_ENDPOINT`, `ITOPS_EVIDENCE_CONTAINER` | Actual private commercial Azure Blob endpoint/container |
| `ITOPS_RUNTIME_CONFIG_PATH` | `.runtime/runtime.json` in a packaged host |
| `ITOPS_BINDINGS_PATH` | `.runtime/bindings.json` |

Worker-only settings:

- `ITOPS_PROJECT_ENDPOINT`: actual commercial Foundry project endpoint.
- `ITOPS_REGISTRATION_PLAN_PATH`: `.runtime/registration-plan.json`.
- `ITOPS_REGISTRATION_RECEIPT_PATH`: `.runtime/registration-receipt.json`.
- `ITOPS_TOOLS_ORIGIN`: bare HTTPS Function host origin, **without `/api`**.
- `ITOPS_TOOLS_AUDIENCE`: actual tools API audience.
- `ITOPS_TOOLS_TOKEN_SCOPE`: explicitly configured `api://<service-app-id>/.default`
  resource scope for outbound managed-identity token acquisition.
- `ITOPS_REVIEW_ORIGIN`: actual HTTPS B1 review host origin.
- `ITOPS_PROJECTION_AUDIENCE`: actual service-token audience accepted by the
  independently authenticated review projection mount.
- `ITOPS_PROJECTION_TOKEN_SCOPE`: explicitly configured
  `api://<service-app-id>/.default` resource scope for worker-to-review calls.
- `ITOPS_SEARCH_ENDPOINT`, `ITOPS_SEARCH_INDEX`, `ITOPS_SEARCH_SNAPSHOT_ID`,
  `ITOPS_SEARCH_SCOPES_JSON`, `ITOPS_OPENAI_ENDPOINT`,
  `ITOPS_EMBEDDING_DEPLOYMENT`, `ITOPS_EMBEDDING_MODEL`,
  `ITOPS_EMBEDDING_MODEL_VERSION`, `ITOPS_EMBEDDING_DIMENSIONS`:
  the real Search adapter settings, with exactly the worker's producer/run
  scope. The embedding endpoint is the **account OpenAI endpoint**, not the
  Foundry project endpoint.
- `ITOPS_EMBEDDING_BUDGET_JSON`: explicit separately approved `BudgetLimits`
  pricing/limits for query embeddings; deadline cannot exceed reasoning.
- `ITOPS_EMBEDDING_BUDGET_SCOPE`: the **same explicitly approved monthly ledger
  scope as the publisher** when both share one embedding allowance. The worker
  does not create a separate automatically hashed bucket that could duplicate
  that allowance. Separate scopes require separately allocated, approved caps.

The existing review settings from `runtime-entrypoint.md` remain mandatory.
The integrated B1 factory additionally needs `ITOPS_PROJECTION_AUDIENCE`,
`ITOPS_PROJECTION_CALLERS_JSON`, and the evidence Blob endpoint/container.
Its projection allowlist contains only the worker UAMI's object/client IDs and
`Results.Project`; browser user tokens are not accepted at this mount.

Application roles used by these new hosts are `Worker.Invoke`, `Tools.Invoke`
and `Results.Project`. Entra tokens must contain `idtyp=app`, `azp`, `oid`,
`tid`, `roles`, valid v2 issuer/audience and validity timestamps. Configure the
API application's optional `idtyp` claim. No delegated `scp` token, EasyAuth
principal header or Function key substitutes for this verification. Tenant
app-role assignments and host allowlists are separate required controls.
For the approved Entra v2 API contract, the JWT `aud` is the service application's
**GUID**, while the token request scope is **`api://<app-id>/.default`**.
They are distinct settings: do not append `/.default` to the bare audience GUID
or configure the JWT verifier with the resource URI instead of its actual `aud`.

### Runtime JSON contract

`RuntimeConfiguration.parse` requires exactly:

```text
schema_version: 1
source:
  workspace_resource_id, workspace_id, run_id, surfaces[]
  baseline_start, reference_time, ingestion_start, ingestion_end
evidence_retained_until
detector_policies[]
approved_detector_policy_hashes[]
budget: complete explicit BudgetLimits configuration
gate_policy: explicit deterministic ChangeSafetyGate policy object
```

All timestamps have explicit timezones. Observation and ingestion spans are
positive and at most two hours; endpoints cannot be in the future. The configured
run is opaque, exact and fixed: never inferred from a model, browser or "latest"
query. `evidence_retained_until` is an operator-approved retention commitment,
not a promise created by the Blob adapter; verify actual retention controls.
An initial analysis job must match the exact configured observation window.

`detector_policies: []` is valid and remains disabled. An enabled `DetectorPolicy`
requires both its external `approval_reference` and its exact `policy.sha256` in
`approved_detector_policy_hashes`. Changing thresholds invalidates that hash.
The operator must approve calibrated thresholds and retained-data acceptance
first, then replace configuration through controlled deployment. No factory
silently enables policies merely to produce nine-agent results.

The model budget deadline is at most 120 seconds. Each HTTP call claims at most
one SQL job. Analysis submits at most eight immutable 15-minute ingestion slices.
Ingestion uses at most two source query attempts and an admission-time duration
bound; it does not silently truncate or declare partial results complete.
Worker host timeout is 180 seconds, below the normal HTTP frontend timeout;
tools timeout is 45 seconds. An interrupted model attempt is durable
**indeterminate**, not an automatically retried inference.

## Real operations, without fabricated ARM resources

The worker constructs the original OpenAPI argument contract itself and sends
an app-only token plus immutable context reference. The tools host independently
loads that context, verifies its candidate against the active SQL attempt/fence,
reconstructs the request and rejects differences. Model functions still accept
exactly `{}`.

| HTTP path | Original operation | Actual synthetic-source behavior |
|---|---|---|
| POST `/api/normalize` | `normalizeSignals` | Verified deterministic detector candidate membership/fingerprint and source observations; does not fabricate raw alerts |
| POST `/api/evaluate` | `evaluateChangeRequest` | Exact finalized policy/proposal/evidence-bound denial receipt; deferred when no finalized action exists |
| POST `/api/query` | `runKqlQuery` | Replays the immutable committed `EnergyObservations_CL` query evidence for this candidate, explicitly labeled as replay rather than a new live query |
| GET `/api/resource` | `getResourceContext` | Typed `not_applicable` for a synthetic entity lacking an authorized ARM mapping |
| GET `/api/changes` | `getRecentChanges` | Typed `not_applicable`, **not** a claim that Azure has zero changes |
| POST `/api/analyze` | `analyzeAlertHygiene` | Real observed detector transitions and explicit insufficient alert-rule history; no invented noise scores or savings |

The actual live Logs Query HTTP adapter is used by ingestion and accepts only
code-compiled bounded queries against `EnergyObservations_CL`.
`EnergyInjectionAudit_CL` is never inference input. Positive observation access
and denial against independently known-nonempty audit data remain separate live
identity acceptance tests.

POST `/api/mcp` implements stateless JSON-RPC initialization, initialized
notification, ping, tools/list and tools/call. Its only context tools are
`get_observation_context` and `get_platform_context`; the former reads committed
observations and the latter explicitly returns synthetic ARM inapplicability.
POST `/api/dispatch` is the worker's authenticated context envelope transport.
Neither interface accepts an arbitrary KQL query, ARM target or workspace.

Search uses the real adapter's schemas and the fixed registered handler
`itops.approved-knowledge.v1`. Retrieval is vector/semantic hybrid, snapshot-
scoped and top-three, with independent SQL `publication_for_retrieval` checks
before use and throughout reasoning. Queries come from source metadata.
No empty-result catch hides SDK errors. Publication/retention revocation is
rechecked even when content remains in a stale Search index.

## Durable execution and review

### Restricted source-access preflight

POST `/api/preflight/source-access` is separate from all model tools, evidence
ingestion and result projection. It uses the parent-owned `access_probe.py`
contracts and a `LogsCountClient` constructed with the **worker's own explicit
ManagedIdentityCredential**, never a caller-supplied or bootstrap credential.
The request caller must satisfy both `Worker.Invoke` and the exact configured
`ITOPS_PREFLIGHT_BOOTSTRAP_OID`.

Deploy a trusted count-query contract file and set
`ITOPS_ACCESS_PROBE_CONTRACT_PATH` to it. The endpoint accepts exactly
`{"witness": <operator_witness artifact>}`; it accepts no workspace, run, query,
contract override or audit-label payload. The deployed contract fixes those
values, validates its query/hash and bounds the count window to three days.
The independent operator witness must be refreshed within five minutes.
Observation counts must match. Audit access must produce the parent's supported
explicit denial or zero against that known-nonempty independent witness; partial
responses, other errors and nonzero audit counts are never a successful proof.
Returned proof includes the worker client ID and caller OID, remains
`inference_input=false`, and is not written to the inference evidence store.

The initial **preflight-only package** exposes only this endpoint, with no
inference routes. It needs no SQL connection, registration receipt, model,
Search or runtime budget configuration:

```powershell
.\.venv\Scripts\python.exe scripts\package_application_service.py `
  --host worker --phase preflight `
  --probe-contract .azure\access-probe-contract.json --output .build\worker-preflight.zip
```

Its required settings are `AZURE_CLIENT_ID` (worker UAMI), `ENTRA_TENANT_ID`,
`ENTRA_AUDIENCE` (service GUID), `ITOPS_SERVICE_CALLERS_JSON`,
`ITOPS_PREFLIGHT_BOOTSTRAP_OID`, and
`ITOPS_ACCESS_PROBE_CONTRACT_PATH=.preflight/access-probe-contract.json`.
That `.preflight` setting belongs only to the standalone helper above.
The approved canonical `deployment.package --service worker-preflight` path
instead uses `.runtime/access-probe-contract.json`, matching the deployed IaC.
Its validator permits only that probe contract under `.runtime`; model/runtime
approval artifacts and the standalone `.preflight` layout are rejected. Follow
`private-service-infra.md` for the approved `deploy-preflight` and `probe-source`
operations rather than mixing artifacts or settings from the two packaging paths.

### UI behavior regression gate

The independent application review found that advancement, investigation and
lifecycle submit handlers were registered inside `write()`. They were absent
on page load and accumulated after unrelated writes. Registration now occurs
once during script initialization. Run `node --test tests\review-ui.test.cjs`
from this subproject, in addition to the Python suite. The dependency-free Node
harness executes the real script and checks initial load/reload, one authenticated
POST per submission after repeated writes, and advancement idempotency keys.
The package uses `services/worker/preflight_function_app.py` as its root
`function_app.py`, on the existing worker Function app. It creates no additional
host or billable service. Its minimal dependency profile is
`requirements-preflight.txt`: Functions, identity, HTTP, schema validation, JWT
crypto and the existing package's Flask/PyYAML imports. No model, Search, Blob,
native SQL, MCP or test-runner dependency is needed. A fresh-process import test
blocks those SDKs and verifies the preflight entrypoint still loads.
Retain the result as ignored deployment evidence.
The parent must verify that the subsequently approved runtime workspace/run
matches the proven source contract before replacing this package with the full
runtime package. The full worker entrypoint also retains the restricted route;
include `--probe-contract` and the same explicit settings when retaining it.

### Bounded application jobs

1. The existing review API submits a bounded `analysis` job to the SQL queue.
2. POST `/api/work/analysis` enqueues immutable ingestion slices. Explicit calls
   to `/api/work/ingest`, `/api/work/coverage` and `/api/work/detector` capture
   evidence, prove snapshot coverage and enqueue enabled-policy candidates.
3. POST `/api/work/reasoning` reserves one SQL analysis revision, executes
   10/02 → actual 00 delegation to 04/03/05 → deterministic 06 → 07, validates
   citations/outputs and persists immutable final evidence and SQL receipt.
4. POST `/api/work/projection` sends only the durable workflow ID to
   `/internal/results/project` on the B1 host. The review identity independently
   reads the SQL receipt/Blob and registers the exact result. The worker cannot
   supply an "accepted" flag or directly mutate review approval tables.
5. Human acceptance and closure remain separate review CAS operations.
   POST `/api/lifecycle/postmortem` with only `{incident_id}` reads authoritative
   closed review state **and the exact matching persisted `action=close`
   receipt**, then enqueues an exact revision/hash/etag/closure-receipt-bound
   08 job. History is filtered by identity and hashes, never assumed to be
   chronological. The receipt is revalidated throughout execution; its ID and
   digest are retained in the lifecycle result provenance.
6. Explicit review hygiene-window jobs fan out to current eligible candidate
   revisions; `/api/work/hygiene` runs 09 without fabricated savings.
7. Lifecycle results are **new immutable revisions**, with provider receipts,
   source lineage and AI authorship. New revisions require fresh human review;
   publication approval is never copied. `/api/work/projection` projects them.

GET `/api/jobs/{job_id}` exposes bounded status, result receipt and next job IDs,
not raw token credentials or stack traces. All work endpoints authenticate app
principals. Bodies for `/api/work/{kind}` are exactly `{}`. There is no recurring
timer, background discarded task, automatic model polling or additional queue.
If a projection delivery fails, POST `/api/results/project` with `{workflow_id}`
and an explicit `Idempotency-Key` durably queues a retry of the **same** SQL final
receipt. Then explicitly call `/api/work/projection`. This route cannot rerun
models, substitute result content or reset approvals.

On a crash after final SQL receipt but before consumer completion, replay
returns that same receipt without model calls. Projection is idempotent even
after a lost HTTP response. A crash before a final receipt never synthesizes a
replacement outcome: the attempt is marked indeterminate on retry and must be
explicitly reconciled. Unprojected revision reservations block newer revisions.
An operator must not reset/delete these rows to "unstick" execution.

## SQL and Azure access handoff

Apply consumer migrations 001/002, review migrations, runtime budget schema,
then `consumer/runtime_sql/application.sql` through the canonical
`deployment.migrate` hash ledger under the separately authorized migration
identity. That bootstrap commits DDL and its source hash atomically and checks
the expected immutable triggers. Do **not** apply this file out of band or call
`ApplicationResults.migrate()` against live SQL: untracked existing schema is
deliberately rejected by the bootstrap. The helper now permits local SQLite
fixtures only and fails closed for SQL Server. Construction never performs DDL.
Existing queue scope backfill remains an explicit migration step.

Minimum application SQL table operations:

| Identity | Required application operations |
|---|---|
| Worker | SELECT/INSERT on `dbo.consumer_events`, `consumer_batches`, `consumer_batch_events`, `consumer_results`; SELECT/INSERT/UPDATE on `consumer_jobs`, `consumer_application_runs`; SELECT/INSERT/UPDATE on runtime month/workflow/call tables; SELECT/INSERT on runtime tool table |
| Worker | SELECT only on review tables needed by `current_revision`, `get_revision`, `publication_for_retrieval`; **no INSERT/UPDATE/DELETE on the `review` schema** |
| Tools | SELECT on `dbo.consumer_jobs`, `dbo.consumer_application_runs`; no review-table permissions |
| Review | Existing review repository rights and queue submission rights; additionally SELECT on `dbo.consumer_application_runs` for projection |

Runtime budget tables are `dbo.itops_runtime_month`, `itops_runtime_workflow`,
`itops_runtime_call`, `itops_runtime_tool`. SQL's default schema must be `dbo`
because the existing budget adapter uses unqualified table names. These runtime
identities require no DDL, `db_owner`, server administration or user creation.
The exact current worker review read allowlist is `review.incidents`,
`review.revisions`, `review.receipts`, `review.publications`,
`review.publisher_state` and `review.publisher_projections`. These cover
`current_revision`, `get_revision` and `publication_for_retrieval`, including
their nested helpers. Do not replace this with a schema-wide SELECT grant;
revalidate the list when those repository methods change. The existing review
queue adapter needs SELECT/INSERT on `dbo.consumer_jobs` and SELECT on
`dbo.consumer_results`; the projection mount adds only SELECT on
`dbo.consumer_application_runs`.

Azure requests, to be separately mapped to approved role manifests:

- Worker: table-scoped observation query, evidence Blob read/create,
  version-bound Foundry invocation, Search index data read, query embedding
  invocation; app roles `Tools.Invoke` and `Results.Project`.
- Tools: evidence Blob read only; no Log Analytics, ARM change, model, Search
  publication or producer-write grant.
- Review: evidence Blob read only for final-result projection; no model,
  embedding, Search-write or producer access.
- Publisher and migration identities retain their separate reviewed scopes.

Blob data operations needed by the worker are
`Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read` and
`.../write`; tools/review need only `.../read`. No application code deletes
evidence, lists storage keys or creates containers. Prefer narrowly scoped custom
data roles rather than granting evidence deletion. Function host/deployment
storage roles are distinct infrastructure requirements, not evidence grants.
Confirm effective table-level LAW denial and current provider-specific Foundry
data actions before applying exact role IDs; do not substitute workspace-wide
Reader, subscription Contributor or broad SQL writes to make startup succeed.

**Custom-table isolation is an unresolved deployment gate, not a legacy custom
role template.** Microsoft's
[table-access documentation](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/manage-table-access)
states that legacy `query/<TableName>/read` permissions do not grant individual
custom `_CL` tables. Do not treat
`query/EnergyObservations_CL/read` as proven isolation, and do not grant
`Tables.Custom/read`, which grants access to all custom tables. The deployment
owner must validate an exact table-conditioned/scoped supported role mechanism
and independently prove observation access plus denial against known-nonempty
audit data under the worker identity. Operator query success is not that proof.
Do not change producer workspace access-control mode, public-query settings,
retention or source ingestion to bypass this gate.

The source window is half-open. When constructing ignored runtime configuration
from an approved retained-data probe, select explicit upper bounds **after**
the final intended observation/ingestion timestamps; using the last row's exact
timestamp as an exclusive end would omit it. Keep the probe's run identity,
counts and tenant-bound settings in deployment evidence, not committed defaults.

## Build and host commands

The infrastructure-owned `deployment.package` + `deployment.bundle` flow,
referenced by `azure.yaml` and `private-service-infra.md`, is the canonical
multi-service release path. Do not replace it or stage competing release
artifacts concurrently. The standalone helper commands below support isolated
application validation and the explicit preflight profile; incorporate their
artifacts into the reviewed canonical bundle only through the deployment owner.

Run from `agentic-itops` using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts\build_service_bindings.py `
  --settings .azure\service-settings.json --output .azure\bindings.json

.\.venv\Scripts\python.exe scripts\package_application_service.py --host worker `
  --runtime-config .azure\runtime.json --bindings .azure\bindings.json `
  --registration-plan .azure\registration-plan.json `
  --registration-receipt .azure\registration-receipt.json --output .build\worker.zip

.\.venv\Scripts\python.exe scripts\package_application_service.py --host tools `
  --runtime-config .azure\runtime.json --bindings .azure\bindings.json `
  --output .build\tools.zip
```

The settings JSON consumed by the binding builder must contain actual
`ITOPS_TOOLS_ORIGIN`, `ITOPS_TOOLS_AUDIENCE`, `ITOPS_MODEL_DEPLOYMENT` and
`ITOPS_SEARCH_SNAPSHOT_ID`. It constructs implemented Search contracts, not
test URLs or native Search approval claims.

ZIPs are source packages for a **Linux** dependency build, not Windows `.venv`
uploads. Native SQL library loading, remote build and private deployment network
reachability must be verified on the actual hosts.

Integrated B1 startup (install existing `requirements-web.txt`):

```text
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 'agentic_itops.service_projection:create_review_service()'
```

For local Function host inspection, extract the appropriate package into a
project-owned build directory and run `func start` there. Real processing still
requires the host UAMI endpoint and private dependencies; local developer
authentication is deliberately not a production fallback.

## Validation and remaining live gates

### Valid live entry and proposed all-nine smoke envelope

Production has two distinct entries: an actual deterministic detector candidate
under an explicitly approved policy, or an explicitly approved
`operator_requested_investigation` over real, completely captured retained
observations. The latter is **not a detector incident**: it has no finding
membership, onset, recovery, calibrated policy or detected lifecycle. There is
still no fixture-injection endpoint. Disabled detector results remain disabled;
an operator request does not change them. Incomplete model results cannot be
accepted/closed merely to unlock 08 or 09.

Baseline-only data can support the approved operator-requested integration
batch, but cannot prove anomaly detection or E14 accuracy. The operator chooses
an actual domain/surface/entity/location in that retained source, supplies a
question, and the worker independently resolves matching committed observations.
No generated data, producer writes, audit rows or invented ARM resource enter
this path. A nonexistent target, incomplete capture, absent actor-bound approval
or expired evidence fails before inference.

For a separately approved one-candidate smoke, the source topology requires
at least **13 Luna response calls**: seven incident agents plus four coordinator
delegation continuations, then one postmortem and one hygiene final response.
Calling each declared function once instead requires **28 Luna responses and
19 function calls**: incident 23/16, postmortem 3/2, hygiene 2/1.
These are protocol counts, not latency or successful-output guarantees.

The user approved one future operator acceptance batch, not unattended model
operation: **at most $5 total**, three model workflows, 72 Luna requests,
48 function dispatches, ten query embeddings and a 120-second admission
deadline per model workflow. Actual calls remain deployment-owner actions.
The per-workflow request ceilings below divide that approved maximum:

| Limit | Each model workflow | Single approved operator acceptance batch |
|---|---:|---:|
| `api_calls` | 24 | At most 72 Luna requests |
| `tool_calls` | 16 | At most 48 function dispatches |
| `max_output_tokens` | 2,048 per request | At most 147,456 output tokens |
| `workflow_tokens` | 2,000,000 accounted input + output tokens | At most 6,000,000 accounted tokens |
| `deadline_seconds` | 120 | At most 360 seconds of model-workflow admission windows |

Set `model_context_tokens` only from independently verified deployment/context
facts, not a guessed small value to make admission succeed. If its conservative
reservation plus 2,048 exceeds the proposed workflow token cap, block and obtain
a revised approval; do not silently raise limits. Pricing references and the
monthly nanodollar cap remain separately explicit. These limits do not create
an Azure-wide billing cap or authorize retries beyond the three named workflows.

For query embeddings, use an explicit policy with at most
five API calls per embedding workflow, context reservation 8,192 and output
reservation one (actual embedding output usage is zero). Only the reasoning and
postmortem workflows declare Search: at most ten embedding requests and
81,920 reserved input tokens for this batch. Calling every Search site once
would use four embeddings. No model or embedding calls were made to derive
these bounds. Query and publication embeddings share the approved monthly
scope unless the parent explicitly allocates separate allowances; namespaced
workflow IDs separate execution accounting, not the monthly spending cap.

`tests/test_application_integration.py` exercises all-nine lifecycle sequencing,
exact closure authority, SQL crash/fence behavior, immutable projection replay,
six-operation contracts, real Search schema binding, source-owned retrieval,
app-only JWT rejection, MCP routes and deterministic source packaging. It uses
explicit offline SQL/HTTP/model doubles: none of this is presented as live
provider success.

Remaining acceptance is deployment-owned: exact permission manifests and Entra
assignments; private DNS/TLS and native SQL on Linux; applied schemas; actual
retention; fresh bounded observation evidence and audit denial; explicitly
approved detector policies and calibrated accuracy; actual nine-agent
registration/invocation with validated provider receipts; authenticated review,
separate publication, Search delivery, revocation and erasure. The foundation
resource endpoint alone does not satisfy any of these gates.

### Earlier application checkpoint and projection-role contract

Before the subsequently approved advancement/investigation extension, the shared
test run passed **1,100 tests in 156.39 seconds**. That is a historical checkpoint,
not the final extension validation; see the final section below. Endpoint portability is
complete, not a remaining blocker. No cloud/model calls, grants, production SQL
migrations, ADO comments or commits were performed by the application delegate.
Rebuild canonical release artifacts from the final shared source; older
standalone publisher ZIP hashes are superseded.

The required **new Entra approval delta** is exactly:

1. Add an enabled application role with value `Results.Project` and
   `allowedMemberTypes: ["Application"]` to the **existing services API
   application**, not the browser review application. Record its role GUID in
   the approved deployment manifest.
2. Assign that role to the **worker UAMI service principal only**, with the
   existing services API service principal as the resource. Resolve and record
   both actual object IDs; do not confuse the worker client ID with its
   principal/object ID.
3. Configure the review projection allowlist for that exact worker
   `(oid, client_id, Results.Project)` tuple. The projection JWT audience is
   the services application GUID; outbound acquisition remains
   `api://<services-appId>/.default`.

This adds one role definition and one assignment to the previously described
three-role design. The complete proposed service assignment map is seven:
review gets `Worker.Invoke` and `Publisher.Invoke`; worker gets `Tools.Invoke`
and `Results.Project`; bootstrap gets `Worker.Invoke`, `Tools.Invoke` and
`Publisher.Invoke`. Do not assign `Results.Project` to bootstrap, the browser
user, tools or publisher.

**Justification:** the worker has no review INSERT/UPDATE authority. It submits
only a workflow ID; the review-owned projection service independently resolves
the finalized SQL journal reference, verifies the immutable Blob bytes and
source/revision/hash, and registers a draft under review MI. This permission
cannot approve, close or publish a result. It avoids granting the inference
identity direct mutation of human-review state. Missing role/assignment remains
fail-closed; documentation does not authorize the grant.
The parent subsequently reported approval and Graph readback of the
`Results.Project` definition and app-only `idtyp` contract. Actual identity
assignments and activation status belong to the parent's deployment manifest.
The advancement/investigation extension adds no further role or grant.

The bounded parent acceptance path is:

1. Approve the exact deployment/identity/source-isolation manifests, including
   the delta above. Deploy only the canonical worker-preflight package first.
   Refresh the independent operator witness immediately before the private
   probe; it must be no older than five minutes. Existing historical counts
   are not worker-MI proof. Stop on observation mismatch, nonempty audit access,
   partial responses or ambiguous denial. This phase makes **zero model or
   embedding calls**.
2. Under the separately approved runtime deployment, verify private DNS/TLS,
   native SQL, ledgered migrations and exact grants; bind the actual registered
   nine-agent versions and deployed endpoints. Set top-level
   `embeddingBudgetScope` to the approved common scope and align both explicit
   embedding budget JSON policies. Never call application `migrate()` against
   production.
3. Process one explicit bounded analysis request with detector policies still
   disabled unless their exact hashes have been approved. Runtime source spans
   are at most two hours, ingestion slices at most fifteen minutes, and
   ingestion admission is bounded to ninety seconds. HTTP processing handles
   one durable job at a time; there is no timer or unattended polling.
   Baseline-only input proves ingestion/deployment, **not** all-nine reasoning.
4. For the approved operator batch, configure the explicit actor-bound approval
   below and use the investigation UI, not a detector-policy workaround.
   Remain within 72 Luna requests, 48 dispatches, ten query embeddings and the
   $5 combined model/embedding cap. Each model workflow has a 120-second admission
   deadline; do not weaken conservative context reservations to make it fit.
   Both postmortem and hygiene require the current exact human closure receipt.
   Review/publication,
   retrieval/revocation and E14 accuracy require their own observed acceptance
   evidence; all-nine offline coverage does not establish them.

### Exact server-held scope configuration and baseline-only acceptance

`ITOPS_REVIEW_SCOPES_JSON` is an object with exactly `sources` and
`entitlements` arrays. Its shape is:

```json
{
  "sources": [{
    "producer_key": "<ProducerScope(runtime.source.workspace_resource_id).key>",
    "run_id": "<exact runtime.source.run_id>",
    "domain": "power-grid",
    "surfaces": ["power-grid", "power-grid-inventory"]
  }],
  "entitlements": [{
    "oid": "<approved human review principal object UUID>",
    "producer_key": "<same producer_key>",
    "run_id": "<same exact run_id>"
  }]
}
```

This is a structural example for the legitimate power-grid subset, not
tenant-bound deployment data or approval of an operator. Obtain `producer_key`
through `consumer.contracts.ProducerScope(...).key`; it hashes the canonical
JSON representation of the case-folded workspace ARM ID, not raw ARM-ID bytes.
Do not substitute the workspace customer UUID. Preserve opaque run IDs exactly.
Entitlement `oid` is a human review principal object ID, not the browser app
client ID or worker identity. These entitlements supplement, not replace, the
review application's authenticated roles. Unentitled users receive no source
scope. Neither browser fields nor model outputs can expand this catalog.

`ITOPS_SEARCH_SCOPES_JSON` is a separate JSON **array**:

```json
[{
  "producer_key": "<same producer_key>",
  "run_id": "<same exact run_id>"
}]
```

Worker Search requires exactly that one configured producer/run pair. No
`domain`, `surfaces`, `oid` or wildcard fields belong in a Search scope entry.
Snapshot identity remains the separate `ITOPS_SEARCH_SNAPSHOT_ID` setting.

**Four-domain catalogs are supported:** repeat the producer/run in `sources`
with one real domain and its matching surfaces per entry. Duplicate
producer/run/domain triples are rejected. For a multi-domain pair, every
entitlement must additionally contain `"domain": "<approved-domain>"`; omission
is rejected rather than silently granting all four domains. Legacy single-domain
catalogs and three-field entitlement entries still work. Read lists, direct
revision access, work submission, lifecycle requests and result projection
enforce the domain; configured surface subsets cannot be expanded by browsers.
Do not use a fictional umbrella domain.

Runtime JSON retains exact fields `schema_version`, `source`,
`evidence_retained_until`, `detector_policies`,
`approved_detector_policy_hashes`, `budget`, and `gate_policy`. Its `source`
has exactly `workspace_resource_id`, `workspace_id`, `run_id`, `surfaces`,
`baseline_start`, `reference_time`, `ingestion_start`, and `ingestion_end`.
Use the independently verified workspace ARM ID/customer UUID and run, real
schema-supported surface names, explicit half-open retained windows and actual
retention. In the absence of detector approval, set both policy arrays to `[]`;
no threshold or policy hash should be invented. Complete budget/gate settings
are still required for the full runtime factory, but configuration is not model
invocation approval.

Legitimate baseline-only acceptance without model activation is limited to: current worker-MI
observation access and audit denial; private service/database/evidence
connectivity and least privilege; durable bounded ingestion, deduplication and
immutable evidence; and truthful empty/disabled detector outcomes. It does
**not** establish detected anomalies, incident reasoning, human close,
postmortem generation, publication/retrieval of a newly reviewed incident,
all-nine live invocation, or E14 detector accuracy. No new model batch is
approved by those checks. The separately approved operator investigation path
below permits a labeled integration exercise, not a detected-incident or
calibrated-accuracy claim.

## Approved explicit advancement and operator investigations

### New deployment settings

Add to the **review** settings:

- `ITOPS_WORKER_ORIGIN`: the actual private worker's HTTPS origin, without a
  path, query or fragment. Use its normal TLS hostname with private DNS.
- `ITOPS_WORKER_TOKEN_SCOPE=api://<services-appId>/.default`.
- `ITOPS_PUBLISHER_ORIGIN`: the actual private publisher HTTPS origin.
- `ITOPS_PUBLISHER_TOKEN_SCOPE=api://<services-appId>/.default`.

After explicit human knowledge approval or revocation, **Advance publication**
POSTs `/publications/<publication-id>/advance` with an empty body, normal
CSRF/origin protection and an idempotency key. The server requires
KnowledgeReviewer and the publication's source entitlement. It persists one
outbox ID in the existing receipt ledger before making the review-UAMI call to
`/api/publisher/run-once` with exactly `{"outbox_id":"<selected-UUID>"}`.
The publisher claims only that item, retains existing fencing/eligibility
checks, and skips global maintenance for this selected request. An unknown
response reuses the same selection; another successful click may select the
next item. A non-claimed item is not reported as a completed upload/removal.
The original service-only empty-body maintenance API remains available.

This extension requires no migration or new grant. Review reads the existing
outbox payload/status using its existing SELECT permission; it does not read
publisher job/attempt tables. An upload may consume an embedding request:
the parent must still enforce the acceptance batch's **ten total embeddings**
including publication, not treat query and publication allowances separately.

The production B1 factory injects `RemoteWorkAdvancer` using the **review UAMI**.
It acquires a service token with existing `Worker.Invoke`; it never forwards a
browser token or impersonates the human. The worker's app-only JWT validator and
caller allowlist remain mandatory. There is no new app role, SQL grant, table,
migration or trigger requirement for these features. The review receipt ledger
already provides SELECT/INSERT for immutable advance-intent receipts, and the
existing worker lease/journal permissions support exact-ID claims and batch
admission. Application SQL migration bytes are unchanged.

Add to the **worker** settings only when activating the approved batch:

```json
{
  "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": "{\"approval_id\":\"<stable-approved-batch-id>\",\"actor_oid\":\"<approved-human-object-UUID>\",\"expires_at_utc\":\"<explicit-UTC-expiry>\"}"
}
```

The inner JSON has exactly those three fields. The expiry must be future and
within the current UTC accounting month; keep it bounded to the approved
acceptance session. Missing approval disables operator inference. The queued
requester identity is populated server-side from the authenticated review
principal, not accepted from the browser. Approval is checked before inference
and again at workflow guards. Retaining a finalized receipt does not authorize
new inference: replay can only enqueue projection of its existing immutable
result.

With this approval enabled, the factory rejects enabled detector policies and
unrelated detector candidates cannot enter model work. Keep
`detector_policies=[]` and `approved_detector_policy_hashes=[]`. Chat budget
`api_calls` must be <=24, `tool_calls` <=16, and deadline <=120 seconds;
query embedding `api_calls` must be <=5 and its deadline no longer than chat.
The sum of chat and embedding **monthly_nanodollars must be <=5,000,000,000**.
The parent must allocate those two portions explicitly using real pricing and
context reservations; no amounts/prices are invented or increased by the
factory. Worker and publisher continue sharing the approved embedding scope.
Other Azure services and unrelated applications are not covered by this
application-level cap.

The investigation identity is deterministically bound to approval ID +
producer/run. Under the existing SQL insertion lock, the immutable application
journal admits at most one `reasoning`, one `postmortem` and one `hygiene` model
attempt for that identity, including failed/indeterminate attempts. Another
browser key, target or actor cannot create a fourth model workflow under that
batch. Do not rotate the approval ID to work around a consumed slot.

### Four domains under one producer/run

Provide four `sources` entries in `ITOPS_REVIEW_SCOPES_JSON`, with identical
producer/run and these domain/surface sets:

| Domain | Surfaces |
|---|---|
| `power-grid` | `power-grid`, `power-grid-inventory` |
| `upstream` | `upstream`, `upstream-inventory` |
| `midstream` | `midstream`, `midstream-inventory` |
| `downstream` | `downstream`, `downstream-portfolio`, `downstream-inventory` |

For each approved human/domain, add an entitlement with exactly `oid`,
`producer_key`, `run_id`, `domain`. One entitlement does not grant the other
domains. Single-domain legacy entries may continue omitting `domain` from their
entitlement. Search scope remains the single producer/run pair, independently
guarded by SQL publication eligibility; no native Search permission bypass is
introduced.
The worker additionally removes documents outside the exact case domain/surface
set before returning them to any agent, and rechecks that restriction on every
knowledge guard. Rejected document IDs are not exposed across review domains.
This is a post-filter over the bounded provider top-three response, not a claim
to retrieve every eligible document in that domain. Empty results do not prove
that no approved knowledge exists. Retained document fields/aliases and the
registered Search schemas remain unchanged.

### Explicit UI/API execution sequence

1. Submit `/analysis-jobs` from the review UI using one registered source and the
   exact configured retained window. The response is a durable root job ID.
2. Enter that ID in **Advance work**. Each click POSTs
   `/jobs/<root-job-id>/advance` with `{}`, CSRF/origin protection for browser
   sessions, and an idempotency key. The review server checks human role and
   source/domain/surfaces, follows at most 64 SQL-held descendants with a
   ten-second selection admission bound, and persists its selected-job intent
   before calling the private worker. No SQL transaction is held over HTTP.
3. The proxy POSTs `/api/jobs/<selected-job-id>/advance` with `{}`. The worker
   claims that **exact job ID**, never another queued job of the same kind.
   Existing fencing/lease checks remain authoritative. One click advances at
   most one job; the proxy HTTP timeout is 165 seconds. A retry of an unknown
   response reuses the same persisted selected-job intent, not the next child.
   SQL status, result and child IDs survive browser reloads. Completed status
   on an individual job does not imply its descendants completed.
4. Repeat explicit clicks until the root's traversal reports completion.
   Ingestion child receipts expose their coverage/detector jobs. Disabled
   detectors remain disabled; their completion does not start an investigation.
   Active leases report busy; expired leases may be reclaimed by a later
   explicit click. Failed/indeterminate model attempts are not silently retried.
5. Submit `/investigation-jobs` using the investigation form. The exact body is
   `{source, window, target, reason}`. `source` and `window` retain their existing
   schemas; `target` has exactly `surface`, `entity_id`, `location_id`; reason is
   nonempty and <=2,000 characters. The target must resolve to 1..256 actual
   observations in complete SQL-committed evidence for the configured source
   window. More than 256 fails; nothing is silently sampled. Then advance this
   new root explicitly: preparation → reasoning → projection.
6. Inspect the AI result labeled **OPERATOR-REQUESTED INVESTIGATION / not a
   detected incident**. The immutable `coverage.trigger` records request job ID,
   actual requester OID, SQL-created timestamp, approval ID, question and source
   Blob references. Observed lifecycle remains `unknown`; finding membership
   and detector transitions are empty. Administrative request/closure times
   are never represented as incident onset/recovery.
7. A human Reviewer must accept and administratively close the complete exact
   revision. An Analyst/Operator may then use
   `/incidents/<id>/lifecycle-jobs` with `{"kind":"postmortem"}`. Advance that root
   separately. Both preparation and inference recheck the current SQL
   closure/revision/hash/etag. The new AI draft revision requires fresh human
   review; accept/close it before separately requesting
   `{"kind":"hygiene"}` and advancing that root.
8. Stop after the three allowed model workflows. No publication is automatic;
   draft approval, Search delivery and revocation remain separate authorities.
   A model's incomplete result blocks human closure and subsequent stages.
   Report that honestly rather than inventing completeness.

No timer, background thread, recurring task, browser auto-poll, queue trigger
or unattended drain is added. Existing private `/api/work/{kind}` remains for
explicit app-role callers, including the new non-model `investigation` preparation
kind; the review UI exclusively uses exact-ID advancement.

### Validation

`tests/test_operator_investigation.py` uses offline HTTP/SQL/provider doubles to
exercise actual baseline-observation parsing, immutable evidence and journal,
all-nine runtime sequencing, two exact human closures, bounded batch admission,
four-domain projection/read isolation, CSRF/role/root-scope rejection,
unknown-response idempotency, explicit-only UI actions and service credentials.
These are not live provider results or E14 accuracy evidence. Run with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_operator_investigation.py `
  tests\test_application_integration.py tests\test_hosting.py tests\test_review.py `
  tests\test_consumer_storage.py tests\test_consumer_processing.py `
  tests\test_portfolio_runtime.py --basetemp=.build\operator-validation -q
```

Final extension validation: **1,135 tests passed in 133.77 seconds** in the
complete shared suite. Prior targeted checks passed 252 tests. No Azure/model
calls, role grants, production migrations, release package builds, ADO comments or commits were
performed by this implementation delegate. Production
`consumer/runtime_sql/application.sql` remains byte-identical, SHA256
`c1084394c27f784d816877ac7e1ce37314ea95ddaa104014948a0ea8016191cf`.
The parent must rebuild canonical packages from this final source, supply the
new settings and actual retained target identifiers, then execute the approved
bounded live batch. Offline tests do not prove private connectivity, live model
completion, delivery/receipts or detector accuracy.
