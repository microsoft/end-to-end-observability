# Foundry capabilities: verified documentation and proposed use

Research date: 2026-09-14. Synthesized by the parent from the independent
research agent's two returned reports. The research agent could return text but
could not create the requested artifact. It reported using its 15-call allowance
for the initial research and no additional research for the gateway follow-up.
No Azure resources, model endpoints or data uploads were used.

## Documented capabilities and their limits

| Capability | Official documentation evidence | Application design consequence |
|---|---|---|
| Agent / Response / Conversation separation | Runtime components, updated 2026-08-27: agents are reusable definitions; Responses execute; Conversations optionally retain interaction history | Store business state in SQL, not in a conversation or agent registration |
| Function results | Function-calling guide, updated 2026-07-16, demonstrates `FunctionCallOutput`, `call_id` and JSON output | Inspect response items and correlate tool results; validate nested payloads in application code |
| MCP approval continuation | MCP guide, updated 2026-09-04, demonstrates `McpApprovalResponse`, `approval_request_id`, and continuation using `previous_response_id` | A tool-access decision is separate from artifact review or knowledge publication; no approval permits synthetic operational execution |
| Tracing | Tracing overview, updated 2026-08-28: prompt/hosted tracing GA; workflow/external tracing preview; OpenTelemetry/Application Insights and child `invoke_agent` spans | Record every agent's contribution and tool evidence. Trace arrival is not the transaction commit or review-readiness signal |
| Cloud evaluation | Deployed-conversation evaluation, updated 2026-09-07: groundedness, coherence, task completion and agent name/version filters | Add separately approved quality scoring; matching a conversation where an agent participated does not isolate that agent's quality |
| Managed memory | Memory usage, updated 2026-08-07, labels Memory/Memory Store API preview; scoped record operations and TTL are documented | Optional future experiment, disabled for this rollout. Do not silently turn unreviewed conversations into approved learning |
| Search publication | Index-loading guide, updated 2026-07-15, documents keyed upload/merge/delete operations and publisher role requirements | A separate reviewed publisher is feasible; this does not resolve Foundry's Search reader-identity ambiguity or stale-context races |

Function parameter `strict=True` in an example does not establish the exact
strict final-response schema constructor or supported subset. Agent-version
fields in evaluation filters do not establish a Responses invocation field.
**Exact version-pinned invocation remains unverified.**

The function guide contains a ten-minute "Runs" expiry warning while also
documenting newer Responses patterns. Do not infer a universal continuation
TTL from it. Verify the chosen runtime's expiry and recovery behavior. Long
human review should occur on a completed durable artifact, not an indefinitely
held live tool call.

## Conditional A2A gateway alternative

An authenticated A2A facade over the four actual Foundry specialists is a
defensible proposal, not a documented turnkey configuration. The facade would
mediate workflow policy, reserve call budget, invoke the real named specialist,
validate its result and return structured provenance to the actual 00 prompt
orchestrator. It is a transport/controller layer, not replacement specialists.

External A2A integration and named agent execution are documented integration
surfaces. Trusted workflow correlation, replay protection, exact version pin,
Foundry-to-gateway and gateway-to-Foundry identities/audiences, and approval
pause/resume remain unverified end-to-end gates.

A mediated request cap is not a cap on hidden provider model turns. Global
accounting requires every counted request/retry/tool path to cross the trusted
boundary or an independently proven provider limit. Disable bypass routes.
Model-supplied workflow IDs or remaining budgets are not authorization.

P0 should compare proven direct controls against the mediated alternative,
preserving all nine real roles. If neither provides the required intermediate
validation, exact contribution evidence and bounded accounting, stop for an
architecture decision rather than silently returning to the three-agent loop.

## Knowledge and evaluation safeguards

Native Search can expose expired/revoked information to the model before final
application validation. Rejecting that answer does not erase its influence.
Use an authoritative registry plus an effective retrieval-time authorized
snapshot/filter; handle concurrent corpus changes and expiry. If that binding
cannot enforce the set, pause generation until removal/exclusion is verified.
Invalidate affected active work and do not reuse contaminated context.

Managed memory uses scoped records; a service identity serving multiple users
does not automatically imply the intended user isolation. Do not enable it
until scope/TTL/approval/deletion behavior has a separate design and approval.
The primary learning path remains reviewed synthetic Search publication.

Prove both participation and usefulness: application receipts show each actual
agent invoked; a case rubric scores what evidence or decision it contributed.
Conversation-level groundedness/task-completion scores supplement, but do not
replace, exact citations, role-specific evaluation and domain-owner judgment.
Tracing/evaluation availability is not a proof that a diagnosis is correct.

## Verified official sources

- [Runtime components](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/runtime-components)
- [Function calling](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/function-calling)
- [MCP tools](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/model-context-protocol)
- [Agent tracing](https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept)
- [Deployed-conversation evaluation](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/cloud-evaluation-deployed-conversations)
- [Memory usage](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/memory-usage)
- [Search index loading](https://learn.microsoft.com/en-us/azure/search/search-how-to-load-search-index)
- [A2A integration](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/agent-to-agent)

The first seven were verified by the independent researcher in this run. The
A2A source is reused from the earlier same-day platform review, not fetched
again in the gateway follow-up. Prior text-only/nonstreaming Foundry target
constraints and Search account/project identity uncertainty remain.
