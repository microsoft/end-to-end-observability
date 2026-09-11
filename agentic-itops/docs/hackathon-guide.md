# Hackathon guide — 14–15 September 2026

Two days. The goal is **one complete loop that runs**, not eight agents that
each half-work.

## Before Monday

Four things. The first blocks everything else.

- [ ] **Decide the tenant.** CDX or MCAPS. This has been open since
      2026-08-19 and every item below depends on it.
- [ ] **Provision a Foundry project** and deploy a `gpt-4o` model.
- [ ] **Seed the knowledge index.** 5–10 runbooks, 2–3 postmortems. An hour
      of work, and agent #4 is worthless without it. If you do only one
      thing before Monday, do this one — it is the only pre-work with value
      independent of the tenant decision.
- [ ] **Name who is building.** The ETT ask is still open. This plan assumes
      two or three people.

Nothing else is required. This repo runs offline, so the team can clone it
Monday morning and be productive while the tenant argument finishes.

## What already exists

Do not rebuild these on Monday:

| | Status |
|---|---|
| Signal Normalizer, 5 adapters | working, 10 eval cases |
| Change-Safety Gate + policy | working, 17 eval cases |
| Alert Hygiene Analyst | working |
| BM25 knowledge retrieval | working, hit@3 = 1.0 |
| Runbook + postmortem corpus | 8 + 3 documents |
| Sample alerts, two scenarios | cross-source + governance denial |
| Tool stubs, MCP stub, OpenAPI specs | working offline |
| Agent instructions, 9 agents | written |

What does **not** exist: any of it running against a real Foundry project.
That is Monday's work.

## Day 1 — Monday

**Morning: make the stubs real.**

1. Provision the agents from `agents/*/agent.yaml` into Foundry. Start with
   `04-runbook-memory` — it has one tool and will surface auth problems
   fastest.
2. Point `04` at Azure AI Search instead of local BM25. Set
   `AGENTIC_ITOPS_LIVE_SEARCH=1`. Re-run the retrieval evaluation and
   compare against the BM25 baseline of hit@3 = 1.0, MRR = 0.95. If hybrid
   search scores worse than BM25 on eleven documents, that is expected —
   vector search needs a corpus.
3. Deploy the normalizer and gate as Azure Functions. The OpenAPI specs in
   `tools/openapi/` are the contract.

**Afternoon: the investigator.**

4. Provision `03-rca-investigator` with Log Analytics and change-history
   tools. This is where the time will go — tool wiring and auth, not
   prompting.
5. Point it at Wilkin's refinery datasets. Alerts already fire there; no
   data engineering required.
6. Run the loop with `--foundry` and compare against the stub output.

**Success for Day 1:** a real alert from Wilkin's data produces a real
hypothesis from a real Foundry agent.

## Day 2 — Tuesday

**Morning: close the loop.**

7. Provision `00-ops-orchestrator` with the others as connected agents.
8. Wire the gate as a tool. Verify a DENY is reported as a refusal and not
   softened into a caveat — this is the most likely place for the
   governance story to quietly break.
9. Provision `07-incident-comms`.

**Afternoon: make it demonstrable.**

10. Run both scenarios end to end and screenshot the output.
11. Run the full evaluation suite against the live path.
12. Write down what broke. That list is worth more than the demo.

**Success for Day 2:** the two-scenario demo runs on Foundry, and the gate
denial still denies.

## Cut in this order if you run out of time

1. `07-incident-comms` — cheap, add later
2. `00-ops-orchestrator` — chain the agents manually; the loop still works
3. `03-rca-investigator` — demo #4 alone; "we have seen this before" is
   still a strong result

**Never cut the gate.** A demo without it is a demo of something we are not
selling.

## Explicitly out of scope

`02` triage, `05` planner, `06` gate rework, `08` postmortem, `09` hygiene
prompt half, `10` industry packs. The code skills behind `06` and `09`
already work; their prompt halves can wait.

Also out of scope: a production normalizer. The current one handles Azure
Monitor common alert schema properly and the others adequately. Do not
present it to a customer as complete.

## Demo narrative

Six minutes, in this order:

1. **"Four tools, one problem."** Run `normalize`. Six signals, three
   incidents. Point at the ServiceNow ticket joining the machine signals —
   that is the half everyone drops.
2. **"Have we seen this before?"** The runbook and the postmortem surface.
   This is the "only two people know how to debug that" fix.
3. **"What changed?"** The extension write, six minutes before the step
   change. Say that `PM-2026-06-03` lost 96 minutes for want of that
   question.
4. **"Here is the plan."** From the runbook, not invented.
5. **"And here is the gate saying no."** Switch to the SCADA incident. The
   runbook says restart, the agent proposes restart, policy refuses.
6. **"Nothing executed."** A human approves, then Azure SRE Agent acts.
   Foundry is the brain; SRE Agent is the hands.

Step 5 is the one that matters. Everyone demos success. Almost nobody demos
their own system refusing itself, and that is what a CISO is actually
listening for.

## Known limitations — say these out loud

- The normalizer is demo-grade for Datadog, Splunk, Prometheus and
  ServiceNow.
- `StubReasoner` is deterministic, not intelligent. It is for offline
  testing, not evaluation of model quality.
- Retrieval is measured over 11 documents. hit@3 = 1.0 on 11 documents
  means the plumbing works, not that retrieval is solved.
- No live prompt-injection defence beyond instructions and the structural
  separation of the gate.
- The audit record is returned, not persisted.

## Afterwards

The first thing to build after the hackathon is **`09` Alert Hygiene**, not
the next agent in the loop. It is the only artifact deliverable inside an
ADR — no live incident, no production access, no security gate — which makes
it the cheapest real thing to put in front of a customer.
