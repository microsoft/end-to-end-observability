# ASRE ServiceNow Review pilot

Knowledge revision: ASRE-KNOWLEDGE-SERVICENOW-REVIEW-1.

The connected native incident platform is ServiceNow. It is the sole incoming
platform for this pilot; do not enable a second Azure Monitor incident scanner.
The development instance is `https://example-instance.service-now.com`.
The incoming assignment group is ASRE Demo Operations, native sys_id
`0000000000000000000000000000007a`.

The initial Review pilot is deliberately limited to:

| Field | Required value |
|---|---|
| Incident | INC0900001 |
| Incident sys_id | `0000000000000000000000000000010a` |
| Linux CI sys_id | `0000000000000000000000000000014a` |
| Title | `[ASRE Demo] Synthetic readiness test (linux); no real Azure alert` |
| Evidence target | `vm-asre-linux-cus` in the exact approved Linux resource group/subscription |

This is a synthetic readiness case, not a real Azure alert or outage.
GetServiceNowIncident uses the native sys_id as `incidentSystemId`, not the
display number or the scoped adapter's episode UUID.

Read the incident and verify sys_id, group and CI before collecting evidence.
PostServiceNowDiscussionEntry is the only exposed native case write, and only
internal work notes on this exact case are admitted by the pre-tool policy.
Use `customerFacing=false`. Do not change priority, assignment or state.
The work note must start with `ASRE REVIEW | INC0900001 | ` and end with
`Review only. No repair or recovery is claimed.`

Include the investigation/request correlation, UTC evidence times, immutable
evidence request UUIDs, knowledge sources, findings and gaps. Ask for relevant
engineer context without treating a work note as repair approval.
On an uncertain write result, retain the original correlation and report the
outcome as unknown. Do not blindly retry a journal append.
HTTP 200 or a returned incident ID is transport acknowledgement, not proof of
journal persistence. Dedicated-writer acceptance needs exactly one matching
internal work-note journal on the expected case, with exact correlated content
and canonical author `copilot.integration`. Without independent journal
confirmation, report the write as unconfirmed. A verified facade journal does
not prove that a separate native discussion write persisted.

When a native read returns 200 but omits `work_notes`, report journal access as
unavailable, not empty. An incident-wide wildcard read grant does not establish
access through a more specific work-note ACL. Do not request ITIL/admin or
broader OAuth access for the runtime writer to hide a field-access failure.
Preserve the original note intent and durable attempt record while an operator
repairs and verifies the narrowly scoped policy.

The bounded 0.1.3 field-read increment was independently verified on
INC0900002: restricted Node and production Python native reads now include
`work_notes`, with 19 installed increment records, nine ACL-role lists and
13 owned script hashes matched to the CI package. This is isolated API
acceptance, not proof of native SRE tool output, a new persisted journal,
automatic feedback, rebind or autonomous operation. The original failed
append and diagnostic attempt records remain immutable. Any new post-policy
write acceptance needs a distinct intent ledger and fresh canonical absence
reconciliation; never reset or replay those prior attempts.

The permanent SDK deployment-only publisher is not a runtime credential.
Never ask for it, expose it to an agent, or use it to bypass runtime case
permissions. Operator installation and restricted runtime acceptance are
separate gates.

The distinct native journal `0000000000000000000000000000008a` on isolated
INC0900002 was independently attributed to `copilot.integration` exactly once.
Do not replay its correlation or reset any previous attempted flags.
The subsequent 0.1.4 assignment-group negative actually changed the fixture;
preserve that failed result, not an accepted-denial label. The 0.1.5 native
declarative-abort rule passed six fresh prohibited-field checks after an
operator-only restoration. The case remained at revision `4`, and complete
customer/work-note journal inventories stayed unchanged. This proves a bounded
API field boundary, not a new positive write under that rule or actual SRE identity.

Native JSON and operator XML may omit `cmdb_ci` even on HTTP 200. Never fill it
from the expected configuration. Read the pinned typed facade's actual native
CI/context, bracket it with identical canonical native snapshots at the same
case/revision, and match state, group and description. Missing, conflicting or
ownership-rejected context stops acceptance. Complete field/revision and both
journal inventories must remain unchanged for a prohibited-write denial.
An interrupted request without retained HTTP evidence is not an accepted
negative; reconcile its actual state and preserve its ledger before any
separately recorded post-fix exercise.

GetServiceNowIncident does not return journal entries. Do not claim to have
read a new work note from that tool alone. Native discussion delivery, durable
feedback acknowledgement and automatic continuation require separate live
acceptance; an explicitly supplied note remains untrusted context.

Native Review notes are not the scoped adapter's idempotent case/action-audit
receipts. The scoped adapter remains at
`/api/x_0000000_asre/case_adapter/v1`, with separate episode/write UUIDs.
Do not set CaseProof.audited, authorize repairs or resolve an incident based
on a native discussion write or this pilot's success.
