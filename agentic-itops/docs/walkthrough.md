# Walkthrough

Fifteen minutes, no Azure, no credentials. Run these in order.

```bash
cd agentic-itops
pip install -r requirements.txt
```

---

## 1. Four tools, one problem

```bash
python -m agentic_itops normalize --input data/alerts
```

```
SIGNAL NORMALIZATION
  signals in:        6
  normalized:        6
  incidents out:     3
  reduction:         50.0%
  cross-source:      1

  [CRITICAL] HighCpuUsage | service=rg-refinery-midstream env=prod | symptom=saturation
             | resource=vm-pump-ctrl-07 | fp=89ffe49f7da1  <-- CORRELATED
      sources: azure_monitor, datadog, prometheus, servicenow (4 signals)
```

Six signals, three real problems. The pump incident arrived as four separate
things: an Azure Monitor metric alert, a Datadog trigger, a Prometheus
firing, and a ticket a control-room operator opened because setpoint changes
felt slow.

On the human side those land in four different places, at four different
times, possibly in front of four different people. `PM-2026-06-03` in the
corpus is the real version of this: three engineers investigated the same
problem in parallel for 34 minutes.

**Why they collapsed.** The fingerprint hashes
`(service, resource, symptom_class, metric)` — not the rule name. Vendor
metric spellings are canonicalized first, so `Percentage CPU`,
`system.cpu.user` and `node_cpu_seconds_total` all reduce to `cpu`.

The ServiceNow ticket has no metric at all. It attaches on the second pass,
by coarse key. Without that, the agent would reproduce the exact split that
cost 36 minutes in the postmortem.

Confirm the reduction is doing real work:

```bash
python -m agentic_itops normalize --input data/alerts --json | \
  python -c "import json,sys; print(json.load(sys.stdin)['report'])"
```

---

## 2. Institutional memory

```bash
python -m agentic_itops search --query "pump controller cpu saturation"
```

```
[  6.20] runbook     RB-014: CPU saturation on midstream pump controllers
[  3.14] postmortem  PM-2026-06-03: Pump controller CPU saturation after...
```

This is the cheapest agent in the portfolio and usually the most valuable.
"Only two people know how to debug that system" is the pain customers
describe most viscerally, and this is the fix.

Now the case that matters more:

```bash
python -m agentic_itops search --query "quantum flux capacitor desynchronization"
```

```
No knowledge matched 'quantum flux capacitor desynchronization'.
That is a valid answer -- an invented runbook is worse than none.
```

An agent that fabricates a plausible runbook will be followed, once, and
then never trusted again. Returning nothing is correct behaviour and it is
explicitly evaluated — see `ret-no-match-is-correct` in the retrieval suite.

---

## 3. The full loop

```bash
python -m agentic_itops demo --input data/alerts --limit 1
```

Seven steps run in a deliberate order:

1. **Institutional memory first.** Cheapest call; frequently ends the
   investigation outright.
2. **Telemetry.** A KQL query chosen from the symptom class.
3. **What changed.** Two of three postmortems in the corpus identify
   "checked logs before checking the deploy" as the largest single
   contributor to time lost.
4. **Hypotheses**, ranked, each citing specific evidence.
5. **A plan**, taken from what the runbook actually prescribes.
6. **The gate.** Code, not a model.
7. **Comms**, drafted after the decision so it reflects real status.

Expected output on the pump incident:

```
  action:    restart
  rationale: prescribed by RB-014-pump-controller-cpu-saturation (match score 6.203)

-- CHANGE-SAFETY GATE (code, not a model) --
  EFFECT: ESCALATE
  approval required: on_call_engineer
  audit id: eedb2f836bf983b0  policy: 1.0-sample
```

Note what the planner did: it proposed the action **RB-014 prescribes**, not
one it invented. Actions are extracted from runbook code blocks, not from
prose — so `Do not restart the host` cannot be misread as an instruction.

---

## 4. The gate refusing

```bash
python -m agentic_itops demo --input data/alerts --limit 2
```

The second incident is a Sev0 on the SCADA historian.

```
  [20.81] runbook: RB-021: SCADA historian heartbeat loss
  action:    restart   (prescribed by RB-021)

  EFFECT: DENY
    - OT boundary: operational technology assets are out of scope for agent action
```

Read that carefully. **The runbook says restart. The agent proposed restart.
The gate refused.**

That disagreement is the whole governance story. RB-021 documents the fix
accurately — a human OT engineer should restart the collector. What the gate
enforces is that *an agent* may not, regardless of severity, regardless of
what the documentation says, and regardless of how the incident is worded.

A demo that only shows success proves nothing about governance.

---

## 5. Probing the gate directly

The gate is a CLI, so it is easy to interrogate — useful in a customer
conversation when someone asks "but what stops it from...".

```bash
ARM="/subscriptions/55555555-5555-5555-5555-555555555555/resourceGroups/rg-app-portal/providers/Microsoft.Web/sites/app-customer-portal"

# low-risk verb, small radius -> allowed
python -m agentic_itops gate --action scale_out --resource "$ARM" --env prod \
  --at 2026-09-14T13:42:00+00:00

# a verb that is not on the list -> denied outright
python -m agentic_itops gate --action delete --resource "$ARM" --env prod

# too much at once -> no approval tier can authorize
python -m agentic_itops gate --action scale_out --resource "$ARM" --env prod --affected 40

# Saturday -> change freeze
python -m agentic_itops gate --action scale_out --resource "$ARM" --env prod \
  --at 2026-09-19T10:00:00+00:00
```

Every decision carries reasons and an audit id. A silent verdict is
unusable, and the evaluation suite asserts that no decision path can produce
one.

`--at` pins evaluation time. In production it is wall clock, because a
change freeze must be evaluated when the change would execute, not when the
alert happened to fire.

---

## 6. The artifact you can deliver before any security gate

```bash
python -m agentic_itops hygiene --input data/alert-history
```

```
ALERT HYGIENE ANALYSIS
  total firings:     397
  distinct rules:    10
  rules flagged:     5
  potential volume reduction: 87.7%

 NOISE  FIRED   AUTO   FLAP  RULE / RECOMMENDATION
  96.2    140   0.95  11.67  Network In - AMBA          -> suppress_or_retune
  90.2     47  0.957   3.92  Disk Read Bytes - AMBA     -> suppress_or_retune
```

This is commercially the most interesting thing in the repo. It runs against
**exported alert history** — no live incident, no agent touching production,
no security gate required. It can be delivered inside an Architecture Design
Review, in week one, before trust exists.

It turns "most of our alerts are noise" into a ranked backlog with evidence
attached. `PM-2026-08-22` is the version of that argument with a cost on it:
a real capacity incident missed for 220 minutes because it arrived among 64
self-resolving network alerts.

The honest caveat, which the agent instructions require it to state: alert
history shows what fired and what resolved. It does not show what an
engineer did in response. A rule with a low self-resolve rate might be
driving action, or might be getting silently ignored.

---

## 7. The MCP path

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  python tools/mcp/azure_monitor_mcp_stub.py
```

Four read-only tools over stdio, answering from the same fixtures. Enough to
wire and test a Foundry agent's MCP binding before any Azure resource
exists. It exposes nothing that mutates.

---

## 8. Verify

```bash
python -m pytest tests -q            # 60 unit tests
python evaluations/run_evals.py      # 41 evaluation cases
```

The gate suite exits `2` on failure rather than `1`. Those cases are
security tests, not quality metrics — a regression there is release-blocking.

---

## Where to go next

- [architecture.md](architecture.md) — why prompt-vs-code fell where it did
- [governance.md](governance.md) — how this maps to AIWSA
- [hackathon-guide.md](hackathon-guide.md) — the two-day build plan
