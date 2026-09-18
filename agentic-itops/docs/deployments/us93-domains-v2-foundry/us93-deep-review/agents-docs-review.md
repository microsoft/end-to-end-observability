# Story 93: independent full-content review — agents, documentation, manifest emitter

## Scope, evidence, and limits

- Reviewed **all 25 assigned files in full**: nine manifest/instruction pairs, five documents, subproject README, and `scripts\emit_agent_manifests.py`. The companion coverage JSON enumerates each file.
- All source citations below are relative to `agentic-itops\`. Line numbers are from complete, numbered source reads.
- No runtime, tool implementation/specification, deployment script, data, evaluation, or test files were inspected. References to those components in this report are **documentation claims or manifest declarations**, not verification that they work.
- No repository changes, installs, model/Azure calls, test execution, work-item changes, or deployments were performed. The two requested session review artifacts are the only authored files.
- Story 93 being Active, revision 5, with empty description/acceptance criteria is supplied task context, not independently queried evidence. The acceptance gates proposed below are recommendations, not existing story criteria.
- Target requirements supplied by the parent: integrate the **new DomainsV2 producer in `iot-dv2-us92-0914-rg`**, use **`EnergyObservations_CL` only**, preserve **synthetic / OperationalUse prohibited**, permit **no real remediation**, and introduce an observation adapter/detector without audit/control-label inputs. The producer schema and implementation are outside this review scope; no column names or producer behavior are presumed.
- Folder definitions are treated as the desired nine-agent source of truth. Their instructions and manifests establish intent, not evidence of deployment, managed identities, connected-agent execution, output validation, or tool reachability.

## Executive conclusion

This is an **eleven-component portfolio containing nine prompt agents**, not nine independent copies of the existing incident demo. Components **01 Signal Normalizer and 06 Change-Safety Gate are code**; **09 Alert Hygiene is code plus a prompt agent**. The nine prompt definitions are 00, 02, 03, 04, 05, 07, 08, 09, and 10 (`README.md:63-80`).

The folders describe a stronger portfolio than the documented deploy path: the latter explicitly says it creates **five agents** (`docs\deploying-to-azure.md:75-77`), while the orchestrator connects only four specialists (`agents\00-ops-orchestrator\agent.yaml:12-18`). Neither establishes deployment or execution of all nine. Existing hackathon scope explicitly postpones several of the agents the user now requires (`docs\hackathon-guide.md:87-105`).

The new DomainsV2 feed is **observational telemetry, not an existing incident or alert-history contract**. Treating an observation row as an Azure Monitor alert, inferring engineering actions from a resolution, or routing a fictional plant fault to an actual producer resource would corrupt both analysis and governance. A deterministic observation-to-signal adapter/detector and immutable synthetic-use restrictions are prerequisites to the complete nine-agent deployment, not reasons to shrink the portfolio.

## Findings — blockers for the requested all-nine integration

### B1. The declared delegation graph does not cover the complete portfolio

**Evidence:** `agents\00-ops-orchestrator\agent.yaml:14-18` connects only `rca-investigator`, `runbook-memory`, `remediation-planner`, and `incident-comms`. Its sequence is memory → RCA → planner → gate → comms (`agents\00-ops-orchestrator\instructions.md:10-34`). There is no connected-agent entry or instruction for 02, 08, 09, or 10. Architecture places 02 in the Foundry layer, 08 in the learning loop, 09 out of band, and 10 across the stack (`docs\architecture.md:30-52`).

**Impact:** Deploying folders is not enough to make all nine functional. Adding every agent to one connected-agent list is also not enough: 08 and 09 have distinct lifecycle triggers, and 10 must influence context before decisions. The current narrative does not define these triggers or typed handoffs.

**Required plan:** Preserve all nine deliverables. Specify a deterministic host/lifecycle controller or expanded, validated delegation graph. Enrich with trusted industry context and triage before incident investigation; invoke 08 against an explicitly closed or simulated incident record; invoke 09 against a separate detector lifecycle/history dataset. Preserve memory as the first investigative call. Parent must determine how much of this the actual runtime already supports.

### B2. Existing input assumptions do not specify a DomainsV2 observation contract

**Evidence:** The orchestrator expects one normalized incident (`agents\00-ops-orchestrator\instructions.md:3-5`). Normalization is documented as heterogeneous existing alert/ticket payloads to canonical incidents (`docs\architecture.md:20-27`, `102-138`). RCA's prescribed queries assume symptom classes and operational telemetry, not an observation schema (`agents\03-rca-investigator\instructions.md:24-34`). The hackathon's integration assumption is “Alerts already fire there; no data engineering required” (`docs\hackathon-guide.md:58-66`).

**Impact:** None of these assigned contracts explains how `EnergyObservations_CL` measurements become evidence-backed events: timestamps, units, quality, sample cadence, entity identity, thresholds, grouping, recovery, deduplication, and provenance remain unspecified. The producer's resource group is a locator, not a valid faulted physical asset or default remediation target.

**Required plan:** Inspect the producer's actual contract in the separate scope, then specify a deterministic observation adapter plus detector before 01. Derive signals only from allowed observations and approved static configuration. No audit/control-label ingestion, label-driven “RCA,” invented ARM IDs, or false live alert claims. Document and test a versioned output envelope, evidence citations, detection time/window, stable incident identity, source provenance, and unsupported/malformed-row behavior.

### B3. The synthetic / prohibited-operational-use boundary is absent from these agent contracts

**Evidence:** README labels sample data fictional (`README.md:226-227`) but still describes a recommendation ready for Azure SRE Agent execution after an ALLOW (`README.md:142-143`). Architecture ends at human approval then SRE Agent execution (`docs\architecture.md:40-47`). Planner accepts a runbook action and a specific ARM target (`agents\05-remediation-planner\instructions.md:11-33`). Its output schema contains no synthetic-use or execution-prohibition field (`agents\05-remediation-planner\agent.yaml:19-49`). None of the nine schemas declares these provenance restrictions.

**Impact:** General “agents do not execute” prompts do not satisfy the stricter DomainsV2 requirement: even human handoff to real remediation is prohibited. A human approval must not clear this restriction. `annotate_incident` described as “always correct and never harmful” is a proposal fallback, not authority to write a real incident system (`agents\05-remediation-planner\instructions.md:14-16`).

**Required plan:** Keep the prohibition in trusted, immutable host/gate context throughout every agent handoff and artifact. Reject real execution/handoff regardless of the action verb, severity, runbook, industry response, or ordinary ALLOW. Use explicitly marked simulated outcomes; do not fabricate a resolution. Comms, postmortems, and hygiene recommendations must retain synthetic limitations. Parent to verify where the actual gate and host can enforce this independently of model output.

### B4. Required tool calls conflict with a strict observation-only evidence path unless explicitly adapted

**Evidence:** RCA must call `get_recent_changes` before telemetry and `get_resource_context` later (`agents\03-rca-investigator\instructions.md:11-16`, `40-46`); its tools also declare Azure Monitor MCP (`agents\03-rca-investigator\agent.yaml:9-27`). Triage and planner declare Resource Graph (`agents\02-triage-dedup\agent.yaml:9-13`; `agents\05-remediation-planner\agent.yaml:15-18`). Postmortem also declares change history (`agents\08-postmortem-drafter\agent.yaml:15-18`).

**Impact:** A “live Azure” switch alone could introduce unrelated production metadata or change/audit records into a scenario restricted to `EnergyObservations_CL`. Observation-derived faults cannot establish that a real deployment caused a process change. Static safety context must not be confused with producer control labels or diagnostic ground truth.

**Required plan:** Resolve exactly which non-telemetry inputs are approved. Bind telemetry queries to the approved workspace/table; unavailable change/topology tools must return explicit unavailable/unsupported evidence rather than seeded or unrelated live results. Either adapt the mandatory change-first method for this observation-only domain or make the first call a truthful unavailable response. Do not silently replace the nine tool portfolios with empty agents. Preserve declared capabilities while documenting their deployment-specific allowed/disabled status.

### B5. Alert Hygiene requires event lifecycle history that observations alone do not supply

**Evidence:** 09 interprets deterministic noise scores, self-resolve rates, and flap ratios over exported alert history (`agents\09-alert-hygiene\instructions.md:3-12`). It asks for total firings, projected reduction, and self-resolve rates (`:17-26`) while warning that history does not establish what an engineer did (`:51-57`). Architecture explicitly makes it out of band (`docs\architecture.md:50-52`).

**Impact:** Measurement counts are not firing counts, a value returning to normal is not proof of “resolved with no action,” and producer audit/control labels cannot fill the gap. The phrase “91% ... resolved with no action taken” in its example (`agents\09-alert-hygiene\instructions.md:19-20`) needs qualification under its own caveat.

**Required plan:** Deploy 09, but feed its code tool a versioned history of **detector-derived synthetic events** with explicitly defined firing/recovery/flapping semantics. If data is insufficient, return an honest insufficient-data analysis rather than manufacturing rates. Identify the supported observation period, minimum sample coverage, missing-data handling, and denominator for every percentage. No real alert-rule changes; report only simulated recommendations.

### B6. Postmortem write-back is promised but has no declared write tool or write identity

**Evidence:** 08 says it writes to the knowledge index (`agents\08-postmortem-drafter\agent.yaml:6-7`; `instructions.md:3-7`). Its only tools are `search_knowledge` and read-oriented `change_history`, and its identity roles are Monitoring Reader and Reader (`agent.yaml:9-18`, `39-43`). Governance says no agent has write permission on anything (`docs\governance.md:144-146`).

**Impact:** Drafting a postmortem is feasible as a read-only agent contract; persisting it is not established. It is invalid to count a drafted document as a closed institutional-memory loop. For synthetic incidents, unrestricted write-back would also contaminate operational knowledge.

**Required plan:** Keep 08 read-only and add an explicitly separate, approval-controlled publication service if write-back is required, or clearly declare a draft/export-only milestone with separate publishing acceptance. Use a segregated synthetic knowledge namespace, provenance, idempotent document IDs, review status, and retrieval filters. Parent must verify any host-side publication already present. No broad Search write role should be granted to the reasoning agent to paper over the gap.

### B7. Declared identity/tool settings do not establish usable live bindings

**Evidence:** Every agent declares the same generic UAMI role names; even tool-less comms has Monitoring Reader and Reader (`agents\07-incident-comms\agent.yaml:8`, `23-27`). OpenAPI tools declare `auth: managed_identity`, but no concrete identity resource ID, endpoint audience, or role scope appears in these manifests. Search bindings declare a fixed index, hybrid query type, and top three, without a connection/identity (`agents\04-runbook-memory\agent.yaml:9-14`). RCA uses `${AZURE_MONITOR_MCP_URL}` (`agents\03-rca-investigator\agent.yaml:22-27`). The deployment guide creates system-assigned account/project identities (`docs\deploying-to-azure.md:42-54`) and names operator prerequisites separately (`:30-31`).

**Impact:** Those are declaration-level intents, not evidence that Foundry assumes the specified UAMI, that the tool server authorizes it, that the server uses the correct downstream identity, or that Search/model access is granted. ARM Reader roles are not a complete specification of these data-plane contracts. Blanket roles for comms undermine the stated least-privilege rationale.

**Required plan:** Produce an identity/connection matrix distinguishing deployer, project/agent identity, tool service identity, Search identity, and any isolated publisher. Specify scope, token audience, downstream authority, network route, denied actions, and resource ownership for each. Parent should inspect deployer/runtime evidence before deciding whether the manifests need translation, validation, or actual implementation.

## Important findings — contract integrity and business semantics

### I1. Manifest schemas are skeletons, not sufficient enforcement of instruction contracts

All nine declare output schemas but no typed agent input schemas. No reviewed output schema closes unknown properties. Array elements are untyped, and nested required evidence is generally not encoded.

- **00:** `hypotheses` is any array; `proposed_action` is any string; gate output has only the effect enum, not mandatory reasons, audit ID, matched rules, target, policy version, or approval identity (`agents\00-ops-orchestrator\agent.yaml:40-49`). “Report the decision verbatim” (`instructions.md:29-31`) cannot be fully represented by the required scalar effect alone.
- **02:** severity enum is useful, but `severity_changed_because` is optional; protected-resource suppression rules and preserving the input fingerprint are prompt-only (`agents\02-triage-dedup\agent.yaml:14-37`; `instructions.md:25-34`).
- **03:** only `hypotheses` is required and its items are unspecified; hypothesis text, confidence enum, citations, next check, and timestamp format exist only in the example (`agents\03-rca-investigator\agent.yaml:28-38`; `instructions.md:75-90`).
- **04:** requires two arrays but not per-result document IDs, relevance, documented actions, preconditions, or verbatim prohibitions (`agents\04-runbook-memory\agent.yaml:15-26`; `instructions.md:48-73`).
- **05:** blast radius, preconditions, expected outcome, source runbook, rationale, and mitigation follow-up are optional despite instructions requiring them (`agents\05-remediation-planner\agent.yaml:19-49`; `instructions.md:22-43`). The action is not an enum; ARM ID is an unconstrained string.
- **07:** redaction explanations are optional (`agents\07-incident-comms\agent.yaml:9-22`) despite “List anything ... left out ... and why” (`instructions.md:38-46`). JSON syntax alone does not enforce audience, confidentiality, or truthful execution state.
- **08:** “what made it slow” and “lesson” are instruction-required sections but optional schema fields; action owners/statuses and timeline item structure are not defined (`agents\08-postmortem-drafter\agent.yaml:19-38`; `instructions.md:21-36`).
- **09:** percentage has no range; recommendation evidence and protected-rule details are untyped; `do_not_touch` is optional (`agents\09-alert-hygiene\agent.yaml:14-27`; `instructions.md:28-49`).
- **10:** only three fields are required; safety classes and prohibitions are optional arrays; output industry is unconstrained despite an input enum (`agents\10-industry-signal-pack\agent.yaml:15-42`).

**Recommendation:** Specify and validate versioned input/output contracts in the host, not just prompts or deployment metadata. Validate the nested semantics and immutable provenance, fail closed on malformed outputs, and carry a trusted gate record separately from the model's summary. Parent must confirm whether any existing runtime already performs these checks.

### I2. Re-running the emitter overwrites eight folder manifests, but not 00 or any instructions

**Evidence:** The emitter's `AGENT_SPECS` covers 02, 03, 04, 05, 07, 08, 09, and 10 (`scripts\emit_agent_manifests.py:67-256`), not 00. `main()` combines COMMON, the agent spec, and a forced `READER_IDENTITY`, then calls `path.write_text(...)` unconditionally (`:259-275`). Model and default settings are duplicated in code (`:19-29`); each tool definition and output schema also lives in the emitter. It writes only `agent.yaml`, not instructions.

**Impact:** Editing eight manifests alone creates changes that regeneration will discard. Editing 00's identity is not brought back into alignment by regeneration, despite the broad governance claim that manifests are generated for consistency (`docs\governance.md:148-151`). This is a dual-source maintenance contract, not necessarily current drift: this review did not run the emitter.

**Recommendation:** The parent plan must explicitly choose an authority model while honoring the user's folder-source-of-truth requirement: e.g., validate/export from folders instead of overwriting them, or declare generator ownership and revise that requirement with approval. Until reconciled, any future approved change to a generated field must account for both locations. Add a read-only drift test and exact expected-agent inventory; no generator execution during this review.

### I3. Triage's advertised job conflicts with its instructions; context conflicts need precedence

**Evidence:** Manifest description says it clusters normalized signals (`agents\02-triage-dedup\agent.yaml:6-7`), while instructions explicitly prohibit redoing deterministic deduplication (`instructions.md:3-6`). Safety criticality raises severity, dev lowers it (`:14-19`), and suppression is prohibited for safety/OT, security, or newly raised severity (`:30-34`). Energy pack says OT assets outrank any IT severity (`agents\10-industry-signal-pack\instructions.md:13-19`).

**Impact:** An OT development scenario presents conflicting heuristics unless precedence is explicit. A suppression recommendation must not silently erase evidence, rewrite fingerprint identity, or bypass safety reporting.

**Recommendation:** Define deterministic “safety/OT restrictions dominate environment downgrades” precedence using trusted context; preserve original severity and explicit explanation. Triage may recommend, not implement, real suppression. Test a dev-tagged safety asset and a security-class noisy signal.

### I4. Industry output is advisory context, not a substitute for deterministic policy or diagnosis

**Evidence:** 10 “adjusts ... what critical means, which controls apply, and what must never be automated” (`agents\10-industry-signal-pack\instructions.md:7-9`). Six vertical sections list regulatory frameworks and prohibitions (`:11-68`). The energy example says pressure plus flow decline **“is cavitation”** (`:20-22`), unlike RCA's evidence/confidence discipline (`agents\03-rca-investigator\instructions.md:48-73`).

**Impact:** A pattern can support a hypothesis but does not prove a unique physical diagnosis. Model-produced prohibitions must not weaken code policy, and static sector framework suggestions must not become unsupported claims about a customer's legal obligations. Unsupported-industry fallback conflicts with the declared closed input enum (`agents\10-industry-signal-pack\agent.yaml:16-25`; `instructions.md:72-75`).

**Recommendation:** Treat energy signatures as candidate explanations with required corroboration. Resolve generic/unknown industry input behavior. Use approved static vertical context, not audit/control labels. The agent may add cautious context but never grant operational authority or override the immutable synthetic prohibition.

### I5. Retrieval backend substitution does not automatically preserve structured grounding

**Evidence:** Memory depends on `documented_actions` extracted from runbook code blocks and verbatim “Do not” sections (`agents\04-runbook-memory\instructions.md:15-18`, `37-46`). Its manifest declares `azure_ai_search`, `vector_semantic_hybrid`, top three (`agent.yaml:9-14`). The live guide says the deployer substitutes `file_search` over uploaded markdown (`docs\deploying-to-azure.md:75-77`, `92-104`).

**Impact:** A generic retrieved chunk is not proven to contain the same structured action/prohibition fields. “One-line change” does not establish parity of retrieval filters, ranks, document IDs, citations, forbidden instructions, or empty-match handling.

**Recommendation:** Select the target backend explicitly and test semantic parity against the required structured contract. Use only approved synthetic/reference knowledge; prevent synthetic postmortems from being mistaken for production experience. Parent/data reviewer must confirm extraction and retrieval evidence.

### I6. “Every agent has the same untrusted-input clause” is false in these instructions

**Evidence:** Governance makes this universal claim (`docs\governance.md:113-120`). Explicit clauses occur in 00 (`instructions.md:56-59`), 03 (`instructions.md:70-71`), and 05 (`instructions.md:50-51`). The full instruction files for 02, 04, 07, 08, 09, and 10 do not carry the claimed clause.

**Impact:** This is a verified documentation/instruction coverage gap, not a demonstrated exploit. Retrieved runbooks, industry context, recommendation text, and previous agent outputs are also untrusted content. Structural protections, typed output validation, bounded tools, and trusted context are required independently of wording.

**Recommendation:** Add common source-content handling requirements in a future approved change and test propagation through memory, postmortem, hygiene, and comms. Do not characterize prompt instructions alone as enforcement.

### I7. A model-call chain needs lifecycle, fault, cost, and observability contracts

**Evidence:** 00 says connected agents need “no orchestration code” (`agents\00-ops-orchestrator\agent.yaml:12-13`), but only its instruction sequence defines order. Governance explicitly acknowledges no rate limiting, unpersisted audit, and unsolved policy distribution (`docs\governance.md:169-177`); model evaluation is “not yet built” (`docs\architecture.md:211-215`).

**Impact:** Deployment of nine resources does not establish retry/idempotency, cancellation, timeout, token/call budgets, handling of partial tool failures, or deterministic no-execution behavior after malformed model output.

**Recommendation:** Acceptance should require request/incident correlation IDs, bounded calls, explicit unavailable-evidence states, immutable gate/audit persistence by an agent-inaccessible host sink, and per-agent invocation records. All nine should have independent health/contract tests, not only the memory smoke test.

## Full agent matrix

### Common declaration details

Every folder declares model `gpt-4o`, `top_p: 0.95`, `instructions_file: instructions.md`, and a `user_assigned_managed_identity` with `Monitoring Reader` plus `Reader`. Temperature is 0.2 except comms 0.4 and postmortem 0.3 (each `agent.yaml:1-5`; identity sections cited below). These are requested configuration values, not confirmation the deployed model or API accepts every setting.

All OpenAPI references below are relative to the declaring agent directory and use `auth: managed_identity`. They point into `..\..\tools\openapi\`; those target specifications were **not** inspected here. All Search tools declare index `agentic-itops-knowledge`, hybrid vector/semantic querying, and `top_k: 3`, but do not explicitly declare authentication.

| Agent / role | Every declared tool and delegation | Required output; optional output | Instruction and lifecycle dependencies |
|---|---|---|---|
| **00 ops-orchestrator** — incident coordinator | OpenAPI `signal_normalizer` → `signal-normalizer.yaml`; OpenAPI `change_safety_gate` → `change-safety-gate.yaml`. Connected: `rca-investigator`, `runbook-memory`, `remediation-planner`, `incident-comms`. | Required `incident_fingerprint` string, `summary` string, `hypotheses` array, `proposed_action` string, `gate_decision` allow/deny/escalate. Optional `handoff` string. | Normalized, correlated incident input; memory first, RCA next, planner, actual gate, then comms; never execute or claim execution. Must not re-normalize away canonical identity. Evidence: `agents\00-ops-orchestrator\agent.yaml:12-49`; `instructions.md:10-34`, `38-60`. |
| **02 triage-dedup** — business severity/suppression advice | OpenAPI `resource_graph` → `resource-graph.yaml`. No connected agents. | Required `incident_fingerprint` string, `adjusted_severity` critical/high/medium/low/info, `suppress` boolean, `reasoning` string. Optional `severity_changed_because` string. | Already deduplicated input; needs trusted criticality/environment/blast-radius context. Never suppress safety/OT, security, or newly raised severity. Needs a host placement before investigation, not duplicate clustering. Evidence: `agents\02-triage-dedup\agent.yaml:9-42`; `instructions.md:3-44`. |
| **03 rca-investigator** — ranked explanations | OpenAPI `change_history` → `change-history.yaml`; OpenAPI `log_analytics` → `log-analytics.yaml`; OpenAPI `resource_graph` → `resource-graph.yaml`; MCP `azure_monitor`, label `azure-monitor`, URL `${AZURE_MONITOR_MCP_URL}`, managed identity, note pointing to a stub. | Required `hypotheses` array. Optional `timeline` array, `notable_absence` string. Instruction item contract: hypothesis, confidence high/medium/low/none, specific evidence, next_check; timeline at/observation. | `get_recent_changes` before `run_kql`, then `get_resource_context`; timestamps and causal ordering matter; no remediation. Observation-specific query mapping and unavailable-change behavior required. Evidence: `agents\03-rca-investigator\agent.yaml:9-43`; `instructions.md:9-46`, `48-93`. |
| **04 runbook-memory** — institutional knowledge | Azure AI Search `search_knowledge`. No connected agents. | Required `runbooks` and `postmortems` arrays. Optional `coverage_gap` string. Instructions require runbook doc_id/title/relevance/documented_actions/preconditions/prohibitions/excerpt and postmortem doc_id/title/what_happened/what_it_cost/lesson. | Query from symptom, service, resource type, title; top three; return no match truthfully; preserve prohibitions verbatim. Requires prepared corpus and structured retrieval parity. Evidence: `agents\04-runbook-memory\agent.yaml:9-31`; `instructions.md:10-46`, `48-73`. |
| **05 remediation-planner** — one proposed action | Azure AI Search `search_knowledge`; OpenAPI `resource_graph` → `resource-graph.yaml`. No connected agents. | Required `proposed_action`, `target_resource_id`, `rollback` strings; `classification` mitigation/fix. Optional `source_runbook`, `expected_outcome`, `follow_up`, `rationale` strings; `blast_radius` object; `preconditions` array. | Ground action in highest matching runbook; no action prescribed → `annotate_incident`; only allowed verbs; one action; mitigation requires follow-up; do not predict/appeal gate. Needs trustworthy target mapping and external action vocabulary. Evidence: `agents\05-remediation-planner\agent.yaml:9-54`; `instructions.md:9-53`, `57-69`. |
| **07 incident-comms** — drafts, never delivery | **No tools** (`tools: []`); no connected agents. | Required `engineer_update`, `executive_update` strings. Optional `customer_update` string, `withheld` array. | Needs full incident, evidence, actual gate record, and real/simulated execution state. Executive ≤3 sentences; customer only if impact confirmed; redact internal/security/personnel/vendor details; no restoration promises; JSON only. Evidence: `agents\07-incident-comms\agent.yaml:6-27`; `instructions.md:3-68`. |
| **08 postmortem-drafter** — learning draft | Azure AI Search `search_knowledge`; OpenAPI `change_history` → `change-history.yaml`. **No write-back tool.** | Required `summary` string; `timeline`, `contributing_factors`, `actions` arrays. Optional `what_made_it_slow`, `lesson` strings. Instructions require action owner/status and six sections. | Needs supported lifecycle/timeline, not invented resolution; search prior corpus first; recurrence claims require citations; gap-aware, blameless, impact/delay evidence. Separate reviewed publisher required for promised persistence. Evidence: `agents\08-postmortem-drafter\agent.yaml:6-43`; `instructions.md:3-7`, `21-53`. |
| **09 alert-hygiene** — code calculations plus prompt interpretation | OpenAPI `alert_hygiene` → `alert-hygiene.yaml`. No connected agents. | Required `executive_summary` string, `recommendations` array. Optional `estimated_volume_reduction_pct` number, `do_not_touch` array. | Out-of-band alert-history job. Code owns arithmetic, model owns explanation. Evidence for per-rule recommendations and denominators; never retune safety-critical rules; action history unknown. Needs observation-derived synthetic detector event history or insufficient-data output. Evidence: `agents\09-alert-hygiene\agent.yaml:6-32`; `instructions.md:3-57`. |
| **10 industry-signal-pack** — vertical context | Azure AI Search `search_knowledge`. No connected agents. Parameter `industry`: energy_utilities (default), manufacturing, financial_services, healthcare, retail_supply_chain, public_sector. | Required `industry`, `criticality_adjustment` strings, `control_frameworks` array. Optional `safety_critical_asset_classes`, `additional_prohibitions` arrays. | Must enrich trusted context before triage/planning while remaining advisory; cannot grant authority. Energy/OT, manufacturing traceability, financial records, clinical safety/PHI, retail peak freezes, public-sector records/CJIS all have different constraints. No unsupported compliance certainty. Evidence: `agents\10-industry-signal-pack\agent.yaml:9-47`; `instructions.md:7-75`. |

### Code components that must remain in the plan

| Component | Assigned-scope evidence | Required relationship to all-nine deployment |
|---|---|---|
| **01 Signal Normalizer** | `README.md:65-80`, `96-99`; `docs\architecture.md:102-138` | Deterministic canonicalization/correlation after the new observation detector has produced valid signals. It is not a tenth prompt agent. Parent owns executable verification of envelope, classification, adapters, timestamp policy, and correlation. |
| **06 Change-Safety Gate** | `README.md:90-94`; `docs\governance.md:73-89` | Deterministic, non-bypassable gate over trusted proposal/context. Synthetic restriction is stronger than ordinary human approval; no real execution path. Parent owns actual policy and enforcement review. |
| **09 scoring code** | `docs\architecture.md:62-70`; `agents\09-alert-hygiene\instructions.md:3-5` | Keep deterministic scoring separate from the deployed 09 prompt agent; test both and their data contract. Observations must first yield a truthful detector event history. |

## Documentation discrepancies and gaps

1. **Five deployed vs nine intended:** `docs\deploying-to-azure.md:75-77` describes five agents; `README.md:70-80` lists nine prompt agents. Do not use that deployment guide unchanged as Story 93's complete plan.
2. **Stale reduced scope:** `docs\hackathon-guide.md:87-105` cuts 07/00/03 and explicitly excludes 02/05/08/09's prompt half/10. This conflicts with the user requirement. It also loosely refers to “their prompt halves” after mentioning 06, though 06 is intentionally code.
3. **No live deployment vs previously deployed:** `docs\hackathon-guide.md:38-39` says none exists; `README.md:31-34` and `docs\architecture.md:191-209` describe defects found in an actual live deployment. These are incompatible status narratives unless clearly dated.
4. **Different deployment topologies:** hackathon requires AI Search and Azure Functions for normalizer/gate (`docs\hackathon-guide.md:45-54`); deployment guide intentionally avoids AI Search and keeps code skills local (`docs\deploying-to-azure.md:86-104`). Neither should silently stand in for the folder tool contracts.
5. **Opposite RCA order:** walkthrough puts telemetry before changes (`docs\walkthrough.md:97-104`); RCA requires changes before telemetry (`agents\03-rca-investigator\instructions.md:11-24`). Parent should verify actual runtime ordering rather than picking whichever prose is newer.
6. **Stdio is not a hosted remote MCP binding:** architecture/walkthrough describe a local stdio stub (`docs\architecture.md:155-157`; `docs\walkthrough.md:224-233`), while manifest expects a server URL and managed identity (`agents\03-rca-investigator\agent.yaml:22-27`). The remote transport, service hosting, authentication, network, and substitution are unspecified.
7. **“Just configuration” does not cover DomainsV2:** README says live switch contracts are identical (`README.md:208-216`), and architecture says adding an adapter means nothing else changes (`docs\architecture.md:217-221`). Observation detection, process-entity mapping, history derivation, and synthetic governance add semantic work beyond a source-system payload translation.
8. **Untrusted-input universality claim is inaccurate:** see I6. Governance's wider claims about code-only authorization and audit are runtime-verification questions, not facts established by these prompts.
9. **Generated-manifest universality claim is inaccurate:** see I2; 00 is not emitted. There is no generated-file warning visible in the eight emitted manifests.
10. **Postmortem write-back conflicts with reader-only posture:** see B6. Distinguish draft generation from publication in all architecture and status diagrams.
11. **Gate sample posture is ambiguous:** governance instructs read-only mode and mutating-action denial (`docs\governance.md:21-35`), while walkthrough demonstrates a mutating low-risk verb allowed and a pump restart escalated (`docs\walkthrough.md:113-119`, `164-166`). These may be different intended configurations, but the walkthrough does not label the difference. Parent must verify defaults; current request requires the stricter synthetic prohibition regardless.
12. **Test totals disagree:** README says 67 (`README.md:41`), walkthrough 60 (`docs\walkthrough.md:240`); both say 41 eval cases. Counts were not verified here and must not be repeated as current results.
13. **Cost, quota, API, model availability claims need fresh validation before deployment:** fixed `gpt-4o` model version/capacity and “effectively zero” idle cost/free vector store are assertions in `docs\deploying-to-azure.md:9-18`, `61-63`, `94-104`. No current pricing or platform check was performed. A deployable plan must validate target-region availability, supported API/SDK/tool capabilities, and resource costs without claiming the documentation proves them.
14. **Provisioning prerequisites are not complete resource-creation authority:** guide lists application/data-plane roles (`docs\deploying-to-azure.md:30-31`) before resource group/account/project/model creation (`:35-63`). The plan must separately identify provisioner control-plane permissions and role-assignment authority. No current tenant permissions were inspected.
15. **Resource tag not present in shown provisioning commands:** guide claims `project=agentic-itops` tagging (`docs\deploying-to-azure.md:145-146`); the commands at `:40-63` do not set it. This is a gap in the shown procedure, not proof the real resource lacks a tag.
16. **Prose-output troubleshooting is overly categorical:** deployment verification blames memory prose on vector-store binding/indexing (`docs\deploying-to-azure.md:130-132`), but troubleshooting separately blames instructions (`:154-155`), and architecture emphasizes probabilistic contract failures (`docs\architecture.md:197-215`). Diagnose from tool/run evidence; don't automatically recreate a store.
17. **Fictional corpus presented as “real”:** walkthrough calls the sample postmortem “the real version” (`docs\walkthrough.md:36-39`) and describes “a real capacity incident” (`:212-215`), while README explicitly calls the corpus fictional (`README.md:226-227`). The recreated demo narrative must visibly label synthetic incident history.
18. **Azure SRE Agent handoff is architectural intent, not an established integration:** README says “No — wrap it” (`README.md:54-59`) and nothing executes (`:142-143`); architecture says execution is deliberately absent (`docs\architecture.md:223-230`). Do not invent an existing executor or provision one for this synthetic-only task.
19. **Portal presence and one memory smoke test do not verify the portfolio:** `docs\deploying-to-azure.md:75-77`, `123-136` omit independent tests for 02/08/09/10 and all-nine delegation. An agent being chattable is not proof of approved tool/data access.

## Recreated full-nine deployment dependency graph — proposed, not executed

This is a complete-target graph with staged dependency gates, **not a smaller pilot**. Each stage contributes to the final nine-agent acceptance. The parent must merge this with executable deployer/runtime/data findings.

### Stage A — freeze deployment and evidence contracts

1. Record Story 93's intended all-nine acceptance criteria, the exact target Foundry project/model/resource locations, the producer-to-workspace relationship, allowed observation table, and immutable synthetic-use restriction.
2. Reconcile folder source of truth with the eight-manifest emitter before making any agent changes.
3. Define trust boundaries and versioned input/output contracts, including trusted policy/provenance separate from user/model text. Select Search vs another retrieval backend with an explicit compatibility plan.
4. Define how each declared OpenAPI/MCP/knowledge tool will be hosted or translated; never silently drop unsupported tools/parameters/identity/output schemas at deployment.

**Exit:** agreed full-nine resource inventory and tool/identity/contract matrix. No deployment claims based only on prose.

### Stage B — observation bridge and code safety foundation

1. Read-only verification of the actual `EnergyObservations_CL` schema and ingestion evidence by the parent/data owner; no audit/control labels.
2. Implement later, under approval, an observation detector/adapter with deterministic event identity, versioned thresholds, clear unit/time/quality semantics, provenance, and unsupported-data behavior.
3. Connect detector → **01 normalizer**, preserving simulated asset identity and separating it from Azure producer/workspace resources.
4. Establish **06 gate/host** enforcement of no real remediation, including no real incident annotations, suppressions, comms delivery, or SRE execution handoff.
5. Define detector event lifecycle/history → **09 deterministic scoring**. Observations are not themselves alert history.

**Exit:** offline contract and denial tests pass; permitted evidence cannot escape the target restriction.

### Stage C — shared services and all eight specialist agents

Provision the shared model/project/identity/tool/knowledge dependencies, then register and contract-test **04, 10, 02, 03, 05, 07, 08, and 09**. Independent resources may be provisioned in parallel, but activation remains dependency-gated.

- **04** depends on indexed approved knowledge and structured retrieval.
- **10** depends on approved static energy context and knowledge; its output is advisory and cannot weaken gate policy.
- **02** depends on normalized signals plus trusted context from configuration/10 and approved entity mapping.
- **03** depends on table-scoped query tools and explicit behavior for unavailable change/topology evidence.
- **05** depends on normalized incident, triage/context, RCA, retrieved actions/prohibitions, approved action vocabulary, and target policy.
- **07** has no tools and depends only on a typed incident/evidence/gate record plus proven simulation state.
- **08** depends on a supported incident lifecycle record, prior knowledge, and an explicit draft/publish boundary. It can draft a synthetic exercise postmortem without claiming an actual production resolution.
- **09** depends on deterministic detector event-history scoring or an explicit insufficient-data result.

**Exit:** every specialist exists with the declared intended capabilities accounted for; all eight independently pass expected-output, unsupported-evidence, and no-side-effect tests.

### Stage D — 00 orchestration plus all-nine lifecycle wiring

Provision/activate **00** after connected-agent names/versions and code tools are available.

Proposed incident flow:

```text
EnergyObservations_CL
  -> deterministic observation adapter/detector
  -> 01 normalize/correlate
  -> approved industry context / 10 advisory enrichment
  -> 02 severity and suppression recommendation
  -> 00 incident investigation:
       04 memory first
       -> 03 RCA (approved evidence / explicit absence)
       -> 05 one grounded proposal
       -> 06 actual deterministic gate + immutable simulation prohibition
       -> 07 JSON drafts (never send)
  -> evidence-backed simulated lifecycle record
       -> 08 postmortem draft
       -> optional separate approved synthetic publisher
       -> segregated knowledge retrievable by 04

Detector event history
  -> 09 scoring code
  -> 09 prompt explanation
  -> synthetic-only hygiene report (never change real alert rules)
```

The proposed 10/02 placement is a required integration decision, not current orchestrator behavior. If 00 must own these invocations, revise its “memory before anything else” instruction to distinguish pre-investigation enrichment from investigation. 08/09 do not need to be indiscriminate connected tools called on every incident; they do need explicit, testable lifecycle entry points.

**Exit:** traces show each of all nine agents invoked through its intended route, gate results unaltered, and no real side effects.

### Stage E — full-portfolio verification and release evidence

Require full-nine invocation evidence, exact inventory/version linkage, bound-tool inventories, identity scopes, synthetic provenance, typed outputs, durable host audit, and table-restricted evidence. Existing offline tests may be reused, but they do not prove live model quality, auth, tool selection, or real row shapes (`README.md:15-29`; `docs\architecture.md:184-215`).

No deployment/model calls were made by this reviewer. Any future live checks require parent/user authorization consistent with the current task.

## Proposed tests and acceptance evidence

These are proposed tests, not tests run or confirmed present.

### Portfolio/source-of-truth tests

- Exact nine-agent set: `ops-orchestrator`, `triage-dedup`, `rca-investigator`, `runbook-memory`, `remediation-planner`, `incident-comms`, `postmortem-drafter`, `alert-hygiene`, `industry-signal-pack`.
- Each manifest resolves its instructions and intended tool definitions; deployer preserves or explicitly maps every model/parameter/schema/identity/tool field.
- Connected-agent references resolve to intended deployed versions; no undocumented five-agent filter.
- Read-only emitter drift test covers the eight generated manifests; explicit separate treatment of 00. Confirm future regeneration cannot undo accepted folder definitions unnoticed.

### Observation and safety contract tests

- Only approved `EnergyObservations_CL` evidence accepted; reject other-table, audit, and control-label inputs.
- Schema variations: missing/invalid timestamps, absent units, out-of-order and duplicate events, null/non-numeric values, stale data, inconsistent entity identifiers, timezone boundaries, gaps.
- Detection semantics: normal → threshold crossing → persistence → recovery, no recovery when data is merely missing, reproducible event/fingerprint identity, distinct recurrence episodes, deterministic per-rule version.
- Asset mapping: physical/synthetic entity does not become the actual producer's ARM remediation target; unknown ARM mapping remains explicit.
- Immutable synthetic/OperationalUse prohibition persists through all nine outputs and host state; malicious or malformed model attempts cannot remove it.
- No allowed/denied/escalated gate response, human approval field, or `annotate_incident` fallback can initiate a real mutation, delivery, annotation, rule suppression, or SRE handoff.

### Agent-specific tests

| Agent | Minimum targeted cases |
|---|---|
| **00** | All intended specialists/lifecycle routes accounted for; memory-first investigative sequence; accurate correlated-source count; complete actual gate record attached; refusal not softened; malformed planner/gate/comms output fails closed; retries bounded. |
| **02** | Preserve fingerprint; no duplicate clustering; unchanged severity explicitly returned; explained severity changes; safety/OT in dev, security noise, recently raised severity cannot be suppressed; suppression remains advisory. |
| **03** | Approved changes-first/unavailable path; time/asset-scoped observation query; no audit/control labels; zero rows and unsupported symptoms produce honest absence; causality rejects changes after onset; ranked cited hypotheses; no asserted cavitation from ambiguous pattern; no action selection. |
| **04** | Strong/partial/weak/no matches; three-result contract; prescribed code action vs prose prohibition; explicit `documented_actions` and verbatim “Do not”; corpus/tenant/synthetic provenance filters; source-content instructions ignored; Search/file-search parity if both supported. |
| **05** | Documented action only; no prescribed action → non-executing annotation proposal; absent/invalid target fails safely; one action, bounded blast radius, preconditions/rollback/observable outcome; mitigation requires follow-up; forbidden verbs not proposed; cannot predict/appeal gate; never claim operational authority. |
| **07** | JSON-only shape; engineer accuracy; executive ≤3 sentences and no internals; unconfirmed customer impact → no customer-facing claim; reviewable redactions; no restoration promise; no execution claim without genuine evidence, and explicitly simulated outcome for this target. |
| **08** | Honest unresolved/partially recorded incident; no invented duration/impact/owner; all six sections and typed actions; recurrence claims cite actual approved records; draft not counted as published; separate publisher approval/idempotency/segregation if implemented. |
| **09** | No history → insufficient data; observation samples not counted as alert firings; correct denominators and sample windows; score explanation matches deterministic tool; no inferred engineer actions; protected assets never retuned; zero denominators and rates bounded; no real rule changes. |
| **10** | Energy default plus all six declared vertical inputs; unsupported-industry behavior specified; approved static context only; safety prohibitions cannot be removed; frameworks are conditional/advisory; no PHI in summaries; mechanical pattern is a hypothesis with escalation, not a proven diagnosis. |

### Cross-cutting deployment checks

- Resolve tool identity versus downstream Azure identity and prove least-privileged table/resource scoping, including tool-less 07.
- Unsupported model/tool/API settings are detected before agent creation; no success return after silently discarded schema or tools.
- Prove MCP transport/auth reachability rather than treating stdio stub output as remote integration evidence.
- Bound run/tool counts, timeouts, retries, and token budgets; attach immutable incident/run IDs.
- Gate audit persisted by trusted host, not model-accessible storage; policy version attached; missing/malformed proposal produces a meaningful denial, not a stringified null.
- Capture all-nine smoke/contract/behavior results individually and together; do not substitute portal screenshots or a single memory reply.

## Critical integration questions for the parent

1. Does the actual deployer enumerate all nine folders, or explicitly deploy only five? Which manifest fields/tools/identities/output schemas does it ignore or substitute?
2. Does the runtime invoke 00 as an actual connected-agent coordinator, or run a separate Python chain? Where are 02/08/09/10 invoked today, if anywhere?
3. What is the exact actual DomainsV2 observation schema, workspace, detector threshold/config contract, and safe entity mapping? Are observations sufficient for valid onset/recovery history without labels?
4. Does “EnergyObservations_CL only” allow any approved static metadata/knowledge, and how must existing `change_history`, Resource Graph, and Azure Monitor MCP tools respond without introducing audit/control-label evidence?
5. Where can immutable synthetic / OperationalUse-prohibited context be enforced across proposal, gate, comms, postmortem, publication, and downstream handoff? Is any current “ALLOW” treated as ready for real execution?
6. Can current knowledge tooling return structured `documented_actions` and prohibitions under each backend? Is there an actual reviewed write-back path for 08, or only a draft?
7. Are detector-derived synthetic event history and insufficient-data states supported by 09's code tool and API schema?
8. Is there host-side output/schema validation, trusted gate-record retention, durable audit, bounded tool calling, and per-agent identity enforcement beyond these declarations?

**Review disposition:** Complete assigned-source review. The folders provide a credible intended nine-agent portfolio, but the current documentation is not an adequate complete deployment plan for the new observation-only synthetic target. The proposed graph preserves every agent while making the missing contract, lifecycle, provenance, and verification work explicit.
