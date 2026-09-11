# Agentic ITOps — Foundry agents and skills

Reference implementation of the agent portfolio for the **Agentic ITOps**
solution: agents run IT operations, humans govern. Azure AI Foundry is the
framework; Azure SRE Agent is the Catalyst that opens the door and the
incident-response component inside it.

Everything here **runs offline**. No Azure subscription, no Foundry project,
no credentials. That is deliberate — the portfolio needs to be demonstrable
and testable before any tenant decision is made.

```bash
cd agentic-itops
pip install -r requirements.txt

python -m agentic_itops demo          # the full incident loop
python -m pytest tests -q             # 60 unit tests
python evaluations/run_evals.py       # 41 evaluation cases
```

---

## What this is not

It is not an observability platform, and adding one is out of scope. The
customer already has signals. This layer **normalizes them, reasons over
them, grounds that reasoning in the customer's own operational knowledge,
and hands execution to something accountable.**

| Layer | Who | Build here? |
|---|---|---|
| Collect telemetry — Azure Monitor, Datadog, Splunk, Prometheus | existing tooling | **No** |
| Normalize → reason → recommend | **these agents** | **Yes** |
| Execute the change | Azure SRE Agent, runbooks, automation | **No — wrap it** |
| Visualize | Workbooks, Grafana | **No** |

---

## The portfolio

**CODE** = deterministic, unit-tested, bound to Foundry as an OpenAPI tool.
**PROMPT** = a Foundry agent: instructions plus declarative tool bindings.

| # | Agent / skill | Host | Status |
|---|---|---|---|
| 0 | [Ops Orchestrator](agents/00-ops-orchestrator) | PROMPT | instructions + manifest |
| 1 | **Signal Normalizer** | **CODE** | implemented, tested |
| 2 | [Triage & Dedup](agents/02-triage-dedup) | PROMPT | instructions + manifest |
| 3 | [RCA Investigator](agents/03-rca-investigator) | PROMPT | instructions + manifest |
| 4 | [Runbook & Memory](agents/04-runbook-memory) | PROMPT | instructions + BM25 backend |
| 5 | [Remediation Planner](agents/05-remediation-planner) | PROMPT | instructions + manifest |
| 6 | **Change-Safety Gate** | **CODE** | implemented, tested |
| 7 | [Incident Comms](agents/07-incident-comms) | PROMPT | instructions + manifest |
| 8 | [Postmortem Drafter](agents/08-postmortem-drafter) | PROMPT | instructions + manifest |
| 9 | **Alert Hygiene Analyst** | **CODE** + PROMPT | implemented, tested |
| 10 | [Industry Signal Pack](agents/10-industry-signal-pack) | PROMPT | 6 verticals |

### The rule that decides code vs prompt

Code when it must be **deterministic, auditable, an authorization boundary,
or high-volume**. Prompt when it needs **judgement, synthesis, or handling
patterns nobody anticipated**.

Two consequences of that rule are not negotiable:

**The Change-Safety Gate is code.** It is an authorization boundary, and the
text it reasons over — alert descriptions, log lines, ticket bodies — is
attacker-influenceable. A prompt can be argued out of its instructions. If
the component deciding *"may this agent restart that production cluster"* is
a model, there is no defensible answer for an AI Workload Security Assessment.

**The Signal Normalizer is code.** Schema translation has exactly one correct
answer. If the same payload normalized differently on two runs the fingerprint
would change, deduplication would break, and every downstream agent would be
reasoning about a different incident.

---

## What the demo shows

```bash
python -m agentic_itops demo
```

Two scenarios ship in `data/alerts`:

**1. Four tools, one problem.** Azure Monitor, Datadog and Prometheus each
alert on the same CPU saturation on `vm-pump-ctrl-07`, and a control-room
operator opens a ServiceNow ticket about it. Four signals, four consoles,
**one incident** — including the human ticket, which is the half of the
picture most correlation layers drop.

```
INCIDENT  89ffe49f7da1  [CRITICAL]
  4 signal(s) from azure_monitor, datadog, prometheus, servicenow   <-- CORRELATED
  [ 6.20] runbook:    RB-014: CPU saturation on midstream pump controllers
  [ 3.14] postmortem: PM-2026-06-03: ...polling extension upgrade
  1. [  high] Recent change 'extensions/write' at 13:16 altered behaviour
             evidence: landed 6 minutes before CPU stepped from ~44% to ~78%
  action: restart   (prescribed by RB-014)
  EFFECT: ESCALATE -> on_call_engineer
```

**2. The gate says no.** A Sev0 on the SCADA historian. RB-021 documents the
fix as a restart, the planner proposes exactly that — and the gate refuses,
because OT assets are outside agent authority regardless of severity:

```
  action: restart   (prescribed by RB-021)
  EFFECT: DENY
    - OT boundary: operational technology assets are out of scope for agent action
```

A demo that only shows success proves nothing about governance. This one
shows the runbook, the agent and the policy disagreeing, and the policy
winning.

**Nothing executes.** Even an ALLOW only marks the action ready to hand to
Azure SRE Agent. Foundry is the brain; SRE Agent is the hands.

---

## CLI

```bash
python -m agentic_itops normalize --input data/alerts      # correlate signals
python -m agentic_itops search    --query "pump cavitation"
python -m agentic_itops gate      --action restart --resource <arm-id> --env prod
python -m agentic_itops hygiene   --input data/alert-history   # the ADR artifact
python -m agentic_itops demo      --limit 2
```

`hygiene` is the one to reach for first in a real engagement. It runs against
exported alert history with **no live incident and no production access**, so
it can be delivered inside an Architecture Design Review before any security
gate has been cleared:

```
ALERT HYGIENE ANALYSIS
  total firings:     397        rules flagged:     5
  potential volume reduction: 87.7%
```

---

## Layout

```
agentic-itops/
├── agentic_itops/          # the Python package
│   ├── envelope.py         # canonical incident envelope + fingerprinting
│   ├── classify.py         # symptom / severity / environment mapping
│   ├── normalizer.py       # skill #1 — normalize + correlate
│   ├── safety_gate.py      # skill #6 — the authorization boundary
│   ├── hygiene.py          # skill #9 — noise scoring
│   ├── orchestrator.py     # agent #0 + the offline demo harness
│   ├── adapters/           # one file per source system
│   ├── policies/           # change-safety policy as data
│   └── tools/              # stubbed tool implementations
├── agents/                 # prompt-hosted agent definitions
├── tools/openapi/          # generated OpenAPI specs for Foundry binding
├── tools/mcp/              # MCP stub server
├── data/                   # alerts, runbooks, postmortems, fixtures
├── evaluations/            # datasets + runner
├── tests/                  # pytest
└── docs/
```

## Documentation

| Document | What it covers |
|---|---|
| [architecture.md](docs/architecture.md) | design decisions, prompt-vs-code, MCP vs OpenAPI |
| [walkthrough.md](docs/walkthrough.md) | run it end to end, with expected output |
| [deploying-to-azure.md](docs/deploying-to-azure.md) | live Foundry deployment, and the API gotchas |
| [hackathon-guide.md](docs/hackathon-guide.md) | the two-day build plan and pre-work |
| [governance.md](docs/governance.md) | AIWSA mapping, the gate, the untrusted-input posture |
| [evaluations/README.md](evaluations/README.md) | what is measured and why |

---

## Going live

Everything runs stubbed by default. Two environment variables switch it:

```bash
AGENTIC_ITOPS_LIVE_AZURE=1     # Log Analytics, Resource Graph, Activity Log
AGENTIC_ITOPS_LIVE_SEARCH=1    # Azure AI Search instead of local BM25
```

See [`.env.example`](.env.example). The tool contracts are identical either
way, so going live is configuration and not a rewrite.

---

## Status

Sample implementation for the Agentic ITOps solution accelerator. The code
skills are real and tested; the prompt-hosted agents ship as instruction sets
and manifests, ready to provision into a Foundry project.

Sample data is synthetic. `vm-pump-ctrl-07`, `contoso.com` and the runbook
corpus are fictional.
