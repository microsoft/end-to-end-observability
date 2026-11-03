---
name: asre-proactive-analysis
description: Real-baseline capacity forecasts and evidence-preserving alert recurrence analysis.
---

# Proactive and recurrence procedure

1. Require at least six distinct observations covering five minutes. Reject
   excessive gaps, duplicate/out-of-order times and resets. Report insufficient
   baseline rather than guessing a forecast.
2. For native capacity-growth, use the exact disposable-filesystem percentage
   series and returned forecast relative to its last sample. Do not infer byte
   capacity, host-wide pressure or a current outage. Require positive growth,
   no resets, all observations below 80 percent and crossing within fifteen minutes.
3. Only an actual configured native Monitor alert assigned to the fixed Review
   handler can authorize its case tool. Use profile capacity-growth, mode
   proactive and signal_family filesystem for a clearly marked risk incident.
   An unalerted scheduled review remains read-only and cannot submit a case.
4. Preserve the actual native investigation and alert IDs. Correlate any later
   critical alert accurately; do not invent cross-investigation deduplication.
5. Review never stops growth, acquires a repair budget, changes monitoring or
   claims prevention/recovery. Preserve the critical backstop.
6. For noise, distinguish raw notifications, correlated episodes, case count and
   action count. Auto-closed does not mean false positive. Preserve independent
   resource/failure detection and report ambiguous causes separately.
7. For a configured native recurrence alert, use profile alert-recurrence and
   signal_family alert-recurrence. Count actual alert IDs via the selected native
   tool, not repeated Perf values. Include gaps and the current disabled-merge
   limitation in case findings. Never tune/mute alerts or claim unmeasured savings.
