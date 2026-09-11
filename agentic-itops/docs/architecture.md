# Architecture

## The constraint that drives everything

We are not rebuilding observability. The customer already has signals, and
they have usually spent years and real money on the tools producing them.
Arriving with a proposal to replace that is both commercially wrong and
technically unnecessary.

What they do not have is anything that **reasons across** those signals,
grounds the reasoning in their own operational knowledge, and can act within
policy. That gap is the product.

This has a sharp practical consequence: the moment someone proposes an
ingestion agent or a dashboard agent, the answer is no. Those belong to
layers we explicitly do not build.

## Layers

```
  Signals (existing — not built here)
  Azure Monitor · Datadog · Splunk · Prometheus · ServiceNow
                    |
                    v
  [1] Signal Normalizer                                    CODE
      heterogeneous payloads -> canonical incident envelope
      cross-source correlation by fingerprint
                    |
                    v
  Azure AI Foundry
      [0] Ops Orchestrator          — connected agents      PROMPT
      [2] Triage & Dedup                                    PROMPT
      [3] RCA Investigator          — heavy tool use        PROMPT
      [4] Runbook & Memory          — AI Search             PROMPT
      [5] Remediation Planner                               PROMPT
      [7] Incident Comms                                    PROMPT
      [8] Postmortem Drafter        — writes back to [4]    PROMPT
                    |
                    v
  [6] Change-Safety Gate                                   CODE
      allow / deny / escalate + audit record
                    |
                    v
  Human approval
                    |
                    v
  Azure SRE Agent — executes
```

`[9] Alert Hygiene Analyst` runs out of band, over alert history, with no
live incident. `[10] Industry Signal Pack` parameterizes the whole stack by
vertical.

## Prompt or code?

**Code** when the work must be deterministic, auditable, an authorization
boundary, or high-volume. **Prompt** when it needs judgement, synthesis, or
the ability to handle something nobody anticipated.

Applied:

| Skill | Host | Why |
|---|---|---|
| Signal Normalizer | CODE | schema translation has one correct answer |
| Change-Safety Gate | CODE | authorization boundary over untrusted text |
| Hygiene scoring | CODE | arithmetic over a corpus |
| RCA Investigator | PROMPT | hypothesis generation over partial evidence |
| Runbook & Memory | PROMPT | relevance judgement, reading intent not keywords |
| Remediation Planner | PROMPT | synthesis into a reviewable plan |
| Incident Comms | PROMPT | audience adaptation |

### Why the normalizer cannot be a prompt

The fingerprint is a hash of `(service, resource, symptom_class, metric)`.
If normalization is non-deterministic the fingerprint is non-deterministic,
and if the fingerprint is non-deterministic then deduplication silently
fails — the same problem produces two incidents on Tuesday and one on
Wednesday. Everything downstream inherits that instability.

There is a second reason. Normalization runs on every signal, and in a real
estate that is tens of thousands a day. Paying model latency and cost for a
mapping is indefensible.

### Why the gate cannot be a prompt

This is the load-bearing decision in the whole design.

The gate decides whether an agent may change production. The text it would
reason over — alert descriptions, log lines, ticket bodies, runbook content
— is **attacker-influenceable**. Anyone who can write a log line can write
text that a model might read as an instruction.

A prompt can be argued out of its instructions. A policy engine cannot. When
an AI Workload Security Assessment asks *"what stops the agent from doing X?"*
the answer must be a file a reviewer can read, not a paragraph of
instructions and a hope.

So: policy is data (`policies/default.yaml`), evaluation is code
(`safety_gate.py`), the model never sees the policy, and the planner cannot
appeal. The planner proposes. The gate disposes.

## The canonical incident envelope

One shape, from every source. The fields that matter most:

- **`fingerprint`** — `sha256(service | resource | symptom_class | metric)`.
  Deliberately excludes source, timestamp, severity and rule name. Two tools
  observing the same problem must collapse to one fingerprint; including the
  rule name would defeat that, because no two vendors name a rule the same.
- **`symptom_class`** — nine buckets. Deliberately small: a large taxonomy
  looks thorough and then nobody maps to it consistently.
- **`blast_radius`** — feeds the gate's approval tier directly.
- **`normalization.warnings`** — what the adapter had to guess. An
  untagged environment is a warning, because guessing `prod` wrong in either
  direction has real consequences at the gate.
- **`raw`** — always retained. Agents occasionally need a field we did not
  model; auditors always need the original.

### Cross-source correlation, and the human ticket problem

Correlation runs in two passes.

**Pass 1 — exact fingerprint.** Metric spellings are canonicalized first, so
`Percentage CPU`, `system.cpu.user` and `node_cpu_seconds_total` all reduce
to `cpu` and collapse together.

**Pass 2 — metric-free attachment.** A signal carrying no metric at all —
in practice almost always a human-opened ticket — attaches to an existing
incident sharing its coarse key `(service, resource, symptom_class)`.

Pass 2 exists because of a specific observed failure. In `PM-2026-06-03`
three tools alerted and a control-room operator opened a ticket, and the
machine view and the human view were joined by hand, 36 minutes in. Without
pass 2 the agent reproduces exactly that split.

Pass 2 never merges two metric-bearing signals. CPU saturation and memory
saturation on one host share a coarse key but are different problems, and
collapsing them would be a worse failure than leaving them apart.

## Tool binding: MCP or OpenAPI?

Both are supported by Foundry. The choice is not arbitrary:

- **OpenAPI** for skills we own — normalizer, gate, hygiene. Our endpoints,
  our contract, and the specs are generated from
  `scripts/emit_openapi.py` so the descriptions stay honest. Those
  `description` fields are what the model actually reads when deciding
  whether to call a tool, so they are written for that reader.
- **MCP** for read-only access to systems we do not own — Azure Monitor, a
  customer's ITSM, a vendor platform. This is where the ecosystem is
  converging, and a customer already running an MCP server for their
  observability stack can point us at it instead of us writing another
  adapter.

`tools/mcp/azure_monitor_mcp_stub.py` implements the MCP shape over stdio
against the same fixtures, so agents can be wired and tested before any
Azure resource exists. It exposes nothing that mutates.

## Offline-first

Every tool has two implementations behind one signature: a stub that answers
from `data/fixtures`, and a live path behind an environment variable.

This is not only developer convenience. It means the portfolio can be
demonstrated, evaluated and reviewed **before the tenant decision is made** —
which matters when that decision has been open for weeks and is blocking
everything else.

## Adding a source

One file in `agentic_itops/adapters/`, implementing `matches()` and
`normalize()`, plus `registry.register(...)`. Nothing else changes. That is
the payoff of the envelope: the RCA Investigator has no idea Datadog exists.

## What is deliberately absent

- **No execution path.** Nothing in this repo mutates an Azure resource.
  Execution belongs to Azure SRE Agent, after a human.
- **Destructive verbs are not in the allowed action list.** `delete`,
  `purge`, `rotate_secret`, `modify_rbac`, `disable_backup` are excluded by
  design, not oversight.
- **No ingestion or visualization.** See the first section.
