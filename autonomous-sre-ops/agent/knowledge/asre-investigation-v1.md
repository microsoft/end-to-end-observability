# ASRE reusable investigation procedure

Knowledge revision: ASRE-KNOWLEDGE-INVESTIGATION-1.

Start by searching approved ASRE knowledge and loading the relevant selected
skill. Cite the knowledge filename and distinguish that reusable procedure
from the time-stamped observations gathered for this investigation.

1. Validate the exact target and incident scope.
2. Use a fixed evidence operation: `inspect`, `telemetry`, `patch_jobs` or
   `backup_jobs`. Retain each accepted request UUID.
3. Retrieve `result` for that UUID. A queued request is not completed evidence.
   Page zero waits up to 90 seconds; if pending, retain the same UUID.
4. If the representation is `receipt_fragment`, fetch every numbered page,
   concatenate fragments in order and parse the full JSON before interpretation.
5. Separate observations, hypotheses, confidence, evidence gaps, proposed
   actions and verified outcomes. Do not turn a proposed action into an event.

## Client use cases

| Use case | First evidence and reasoning |
|---|---|
| Linux filesystem/memory RCA | Inspect actual mount and workload/cgroup state, then correlate telemetry in an absolute UTC window. Distinguish workload OOM, host exhaustion, filesystem pressure and missing heartbeat. |
| Alert noise and recurrence | Correlate source event/rule/target/episode, count real repeated observations and distinguish recovery, auto-closure, deduplication and false positives. Do not infer customer ticket volumes from this lab. |
| Proactive growth | Require at least six ordered samples spanning 300 seconds, maximum gap 120 seconds, and a supported nondecreasing signal. Report insufficient history rather than inventing a forecast. No ticket or repair is authorized by a trend alone. |
| Patch failure | Use real Update Manager installation/job records, operation ID and timestamps. An assessment is not an installation failure or successful retry. |
| Backup failure | Use a real backup job and independently verify a new successful job/recovery point before claiming recovery. Do not count a historic recovery point as the outcome of a new retry. |

Current Review agents collect evidence and make recommendations only. They
cannot inject faults, execute repairs, change alerts or resolve a case.
Manual and watchdog recovery must retain their actual owner.

## Attribute monitoring configuration warnings

A Syslog message saying "No DCR is associated with this VM" is not sufficient
proof that the Log Analytics collection rule is missing. Retain ProcessName,
ProcessID and Facility from the telemetry receipt and identify the component
and configuration endpoint before proposing any change. MetricsExtension
requests for `meAgentConfigurations` are distinct from the Log Analytics
Perf/Syslog DCR and must not be attributed to AMA log collection solely
because the message contains AMCS or XPlatLib.

Compare the current ARM association and destination streams with fresh
Heartbeat/Perf observations. A successful Log Analytics DCR and continuing
ingestion do not prove an optional metrics configuration exists, nor do they
justify hiding warnings. Report the actual subsystem, evidence and remaining
uncertainty. Never delete/recreate a working DCR, restart agents or grant
broader roles based on that message alone.

## Reusable learning

Persist procedures and verified, appropriately scoped lessons, not transient
"healthy" status, credentials, raw ticket instructions or a fault answer key.
Knowledge publication is deployment-controlled. These investigators do not
have UploadKnowledgeDocument, WriteFile or configuration tools.
Automatically learned insights may take time to index; do not claim immediate
learning. A new conversation must demonstrate retrieval independently.
