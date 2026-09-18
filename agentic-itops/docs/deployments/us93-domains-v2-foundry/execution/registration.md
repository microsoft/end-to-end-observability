# All-nine version registration

Status: **all nine prompt agents registered and independently read back in Azure**
on September 15. Each has exact version `1` in `agentic-itops-us93-project`.
Registration is not workflow execution; live acceptance remains pending.

The user approved plan SHA256
`224f3a37cbef8b2e48154fdd95b8bb09e39cc663b87a369468353a810f2b7401`,
compiled from actual service bindings, existing `gpt-5-6-luna` and
`model_defaults` sampling. The durable journal and receipt remain in ignored
`.build\registration\execution-01.sqlite` and `receipt-01.json`.
All-nine receipt status is `registered_and_read_back`, with
`execution_allowed: false` and `live_acceptance: false`. No inference calls,
model capacity changes, operational actions or publication occurred.
Additional evaluation-suite generation was explicitly deferred by the user;
continue with deployment and the already approved bounded integration batch.

`agentic_itops.registration` and `scripts/register_portfolio.py` consume the
complete trusted-dispatch compiler artifact. They refuse missing agents or
bindings, native tools without mediation, mixed model deployments and an
endpoint different from the approved plan. No tool handler is fabricated merely
to make the portfolio binding-complete.

## Model and source contract

Every source model reference must be explicitly mapped to `gpt-5-6-luna` in the
deployment binding file. The nine source folders remain authoritative for
instructions, tool declarations and output schemas. The old five-agent
`deploy_to_foundry.py` harness is not this registration path.

The CLI requires an explicit sampling choice. `source` retains the manifests'
temperature/top-p values. `model_defaults` omits those two request fields and
records their original values in the plan. For the approved Luna target, review
`model_defaults` rather than silently carrying GPT-4o sampling settings into a
reasoning model. No reasoning effort is invented; the output-token reservation
and limit remain runtime responsibilities. SDK serialization is not proof of
server acceptance or model quality.

The plan digest covers the project, portfolio/source digest, all nine complete
wire definitions, model deployment and sampling choice. Both the plan and its
digest must be reviewed before applying. Changing sources or bindings requires
a new review, not editing an old journal.

## Offline preparation

Run from `agentic-itops` with `requirements-consumer.txt` installed; that profile
includes the pinned Foundry SDK and the compiler/runtime JSON-schema dependency.
The binding path below must be populated from real reviewed service contracts;
it is not shipped as an invented, deployment-ready configuration.

```powershell
& .\.venv\Scripts\python.exe .\scripts\register_portfolio.py `
  --bindings .\.build\services\bindings.json `
  --project-endpoint "https://ai-agentic-itops-us93-gcjgvzpp44b4i.services.ai.azure.com/api/projects/agentic-itops-us93-project" `
  --model-deployment gpt-5-6-luna `
  --sampling model_defaults `
  --output .\.build\registration\plan.json
```

Preparation never creates credentials, registers a version, uploads knowledge or
calls a model. All output/journal paths must be inside ignored `.build`, and
receipt files are exclusive-create: existing results are never overwritten.

## Apply gate and recovery

Application requires `--apply`, the exact `--approve-plan-sha256` from the
reviewed plan, a persistent `--journal` path and one explicit credential choice:
`--credential azure_cli --tenant-id <approved-tenant-UUID>` or
`--credential managed_identity --client-id <approved-UAMI-client-UUID>`.
These flags do not replace the human resource/permission approval required by
the service deployment plan. There is no developer-credential fallback.
Azure CLI token acquisition allows 60 seconds for the existing Windows CLI;
provider create retries remain disabled.

For each agent, the SQLite registration journal durably records an attempt ID
before the network write. The create request carries plan, definition and attempt
metadata. The SDK client and each operation disable automatic retries.
Registration succeeds only after a separate exact-version read-back matches
name, version, ownership metadata and the complete wire definition.
Draft/creating/failed/deleted versions cannot pass verification. A missing
optional prompt-version status can pass definition read-back; this does not
claim hosted readiness or successful inference.

An interrupted or timed-out creation remains `attempting`. Resume with the same
plan and journal: bounded version enumeration must find exactly one owned match.
Zero matches is still ambiguous, not permission to issue another create; multiple
matches also stop. A crash before the network call is indistinguishable from an
unacknowledged call and deliberately requires operator reconciliation.
No automatic version deletion, rollback or uncertain-write retry is implemented.

`created` versions resume read-back without creating again; `verified` versions
are read back again on a resumed run. A journal cannot be reused for a different
endpoint, source revision or model settings. Do not discard it or create another
journal to work around an unresolved outcome. At-most-once create admission is
per journal, not a global server idempotency guarantee.

The final receipt contains all nine exact version strings plus the matching
project, plan and portfolio digests. Use
`bind_registered_runtime_agents(portfolio, plan, receipt)` to recompile/check the
plan, validate receipt scope and bind all nine exact versions with registration
provenance. Keep these artifacts under trusted deployment control: a digest is
not a signature and this helper does not authenticate user-uploaded receipts.
Accepting an arbitrary version map is not provenance verification.

## Remaining gates

Application code activation, native model/schema inference compatibility and
all-nine version-pinned execution still require the approved live service stage.
Registration does not create application hosts, invoke agents, write Search
knowledge, activate schedules or complete Story 93.
