# Approved prerequisite actions and review corrections

This follow-up supersedes only the corresponding registration/quota/status
observations in the preserved initial preflight. It does not rewrite that
historical evidence or claim full deployment readiness.

## Specific user approval

The user selected:

- "Approve provider registration only": register `Microsoft.Search`,
  `Microsoft.Sql` and `Microsoft.App` in subscription
  `643febd3-3170-4110-80f9-fc8980b70a7a`, then perform read-only quota checks.
- "US data zone is acceptable; keep resources in Central US where supported":
  a processing-location constraint, not authorization to deploy a model.

This approval excludes billable resource creation, identities, role grants,
model deployment and inference. No such excluded operations occurred.

## Registration and quota results

Each approved `az provider register --wait` succeeded. Subsequent
`az provider show` calls returned `Registered` for all three namespaces.

| Check | Result | Meaning |
|---|---|---|
| `az quota list`, Search/Central US | Empty array | Does not establish effective headroom |
| `az quota list`, SQL/Central US | BadRequest | Unsupported response, not exhausted capacity |
| Search service-specific usages | Basic currentValue 0, limit 12, unit Count; nextLink null | One proposed Basic service fits the current service-count quota; allocation is not guaranteed |
| SQL service-specific usages | ServerQuota currentValue 0, limit 250, unit Count | One proposed logical server fits this counter; not a proof of chosen database SKU performance or allocation |
| Flex memory | Unresolved | Neither account counts nor SQL/Search quota resolve Functions memory headroom |

Read-only endpoints:

```text
GET https://management.azure.com/subscriptions/643febd3-3170-4110-80f9-fc8980b70a7a/providers/Microsoft.Search/locations/centralus/usages?api-version=2025-05-01
GET https://management.azure.com/subscriptions/643febd3-3170-4110-80f9-fc8980b70a7a/providers/Microsoft.Sql/locations/centralus/usages?api-version=2023-08-01
```

The Search endpoint was verified against
[Usages - List By Subscription](https://learn.microsoft.com/en-us/rest/api/searchmanagement/usages/list-by-subscription?view=rest-searchmanagement-2025-05-01)
before invocation. The effective Basic limit is **12**, not the public default
of 16 cited in the earlier report. No quota increase was requested.

## Independent review and correction round 1

Initial verdict: REVISE, high confidence. The reviewer found three reproducible
gaps in the narrow prerequisite checkpoint:

1. Synthetic markers in contributing source evidence did not reach the gate.
2. Completed model responses containing empty RCA/comms objects could become
   successful stages.
3. Blank, misspelled or predicate-free deny rules were silently ignored.

Corrections:

- Capture restrictive provenance before any reasoning, across raw/labels of
  every contributing envelope, including nested source structures. Aggregate
  prohibitions; never read permission/provenance assertions from a model.
  Malformed markers fail explicitly. This is not authenticated ingestion.
- Require typed RCA hypotheses/confidence/citations/next checks and typed,
  nonempty communications drafts. Empty hypotheses need an explicit
  insufficient-evidence explanation. Planner classification/rollback are also
  required. Citation strings passing type checks do not establish grounding.
- Reject unrecognized deny-rule keys, blank predicates and predicate-free
  rules; revalidate policy at evaluation. The previous test expecting malformed
  rules to be ignored was deliberately changed to require rejection.

Added 27 regression cases, including a nonprimary source prohibition changing
an otherwise ALLOW to DENY despite model assertions to the contrary, completed
empty Foundry RCA stopping after one call, malformed RCA blocking planning,
and malformed communications preventing successful return.

All **151 pytest cases** and **41 existing evaluation cases** pass. The original
valid offline demonstration is retained.

Independent follow-up verdict: **APPROVE, high confidence, limited to the three
corrections**. The reviewer closed all three findings, ran 30 targeted regression
cases successfully and confirmed the marker-free offline demonstration retains
its restart/escalation behavior. No significant issues were found within that
narrow correction scope.

Residual risk: this is an incomplete P0/correctness checkpoint, not deployment
or E01-E15 acceptance. Source-marker propagation does not prove authenticated
producer provenance. Static SDK construction does not prove server compatibility
or authenticated tools. Provider registration was outside this code recheck.
