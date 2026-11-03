---
name: asre-linux-rca
description: Evidence-backed investigation of demo filesystem pressure and workload cgroup memory/OOM.
---

# Linux incident procedure

1. Confirm the exact target, alert time, signal family and active operational
   episode. Correlate source events; do not merge different resources or causes.
2. Read fresh Perf/Syslog data and fixed guest inspection. Compare disposable
   filesystem utilization, service state, workload configuration, memory events
   and journal timestamps. Do not infer OOM from high VM memory alone.
3. Create/obtain the owning ServiceNow case through the user's atomic integration.
   Include evidence references, confidence and explicitly missing evidence.
4. In Review, publish diagnosis and proposed infrastructure steps only. When the
   fixed scoped case tool is explicitly bound and permitted, create the owning
   case and its internal findings note. This exception never authorizes VM
   changes, repair, closure or arbitrary external writes. If no case tool is
   bound, report that gap rather than fabricating delivery.
5. In the separate accepted Autonomous sandbox plan, request only the fixed
   workload recovery sequence after case audit, episode ownership and budget
   checks. Stop growth and clean only exact owned manifest files. Never run
   arbitrary shell, delete an entire volume, change OS memory settings or reset
   the scenario controller.
6. Verify fresh service health, stable memory and the disposable filesystem
   after action time. Require a stable observation window; a successful command
   is not by itself recovery. Attribute watchdog/manual recovery accurately.
7. Append evidence and resolve only through the instance's actual lifecycle
   operation. On failed or ambiguous repair, retain accurate state and escalate.
