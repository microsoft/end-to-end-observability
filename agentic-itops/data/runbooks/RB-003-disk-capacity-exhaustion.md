# RB-003: Disk capacity exhaustion

id: RB-003
symptom_class: capacity
service: shared
severity: medium
owner: platform-ops@contoso.com
last_reviewed: 2026-06-11

## When this applies

Free space below 10% on any managed disk.

## Immediate mitigation

Clear rotated logs and temp artifacts first — this recovers space in
seconds and is reversible in the sense that nothing of value is lost:

    clear_cache

If free space is still below 10% after clearing, the disk is genuinely
undersized. Expand it; do not keep clearing.

## Root cause resolution

Most recurrences trace to log rotation that was configured for a
smaller disk and never revisited after a resize. Check the retention
policy before expanding.

## Do not

Do not delete application data to recover space. Expanding a disk is
cheap; a bad deletion is not recoverable.
