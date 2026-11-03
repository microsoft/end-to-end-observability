# ASRE approved environment

Knowledge revision: ASRE-KNOWLEDGE-ENVIRONMENT-1.
This document describes approved topology, not current health.

The isolated demo is in subscription
`00000001-000a-4000-a000-000000000001`, Azure region `centralus`.
Only these VM resource IDs are in scope:

| Role | Resource ID | Private address |
|---|---|---|
| Linux incidents | `/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus` | `10.42.0.10` |
| Patch | `/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus` | `10.42.0.11` |
| Backup | `/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-backup-cus` | `10.42.0.12` |

The Linux incident filesystem is the disposable 4 GiB mount `/srv/asre-demo`.
Never propose root-filesystem cleanup. The workload has cgroup limits:
MemoryHigh 96 MiB, MemoryMax 128 MiB, MemorySwapMax 0 and CPUQuota 50%.
A workload OOM is not proof of host-wide memory exhaustion or a node failure.

The approved cost-optimized disk class is Standard_LRS. VM power state can
change between rehearsals. Never infer that a VM is running from this document.
The Log Analytics workspace is `law-asre-cus` in
`rg-asre-control-cus`; the Recovery Services vault is `rsv-asre-cus`.

Use `asre-review-evidence`, not arbitrary shell, Azure CLI or generic HTTP.
The tool implements authenticated private collection and immutable receipts.
Knowledge and prior incidents help select checks; only fresh receipts establish
current guest state. Missing telemetry is a gap, not evidence of health.
