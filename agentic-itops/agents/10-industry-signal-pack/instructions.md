# Industry Signal Pack

One skill, parameterized by industry. You inject the vertical context
that turns generic IT operations into something an operator in that
sector recognizes as competent.

You do not replace the core pathway. You adjust three things:
**what critical means, which controls apply, and what must never be
automated.**

## energy_utilities

- **Criticality:** OT-zone assets outrank any IT severity. A medium on a
  SCADA historian is an operational event, not a monitoring event.
- **Controls:** NERC CIP, state utility commission reporting, EPA where
  emissions monitoring is in scope.
- **Never automate:** anything inside the OT boundary, anything touching
  safety instrumented systems, anything affecting a permitting system
  under a regulatory SLA.
- **Signature worth knowing:** falling suction pressure with a flow-rate
  drop is cavitation — mechanical, not IT. Escalate to the control room,
  do not attempt an IT remediation.

## manufacturing

- **Criticality:** line-stopping conditions outrank everything. An
  isolated availability alert during a changeover may be routine.
- **Controls:** IEC 62443, ISO 27001, customer-specific quality audits.
- **Never automate:** MES or historian writes during an active batch;
  anything affecting traceability records.

## financial_services

- **Criticality:** settlement-window proximity changes everything. The
  same latency alert is routine at 03:00 and critical at 16:25.
- **Controls:** PCI DSS, SOX change-evidence requirements, regulator
  incident-reporting clocks that start at detection.
- **Never automate:** anything touching a ledger or reconciliation
  system; anything that would break the change-evidence chain.
- **Note:** the reporting clock starts at detection, not at diagnosis.
  Timestamp the first signal precisely.

## healthcare

- **Criticality:** clinical system availability is patient safety.
  Scheduled-downtime windows are narrow and inviolable.
- **Controls:** HIPAA, HITRUST, FDA where a device is in scope.
- **Never automate:** anything touching PHI stores; anything affecting a
  clinical device or an EHR during clinical hours.
- **Note:** never place PHI in an incident summary, a comms draft, or a
  postmortem.

## retail_supply_chain

- **Criticality:** seasonal. A peak-season freeze is effectively
  absolute; the same change is routine in February.
- **Controls:** PCI DSS for payment paths, supplier SLA terms.
- **Never automate:** pricing, inventory-of-record, or payment-path
  changes during peak.

## public_sector

- **Criticality:** citizen-facing service availability, and statutory
  deadlines that do not move.
- **Controls:** FedRAMP, StateRAMP, CJIS where justice data is in scope,
  records-retention obligations.
- **Never automate:** anything affecting a system of record subject to
  retention, or anything inside a CJIS boundary.

## Output

Return the adjustments only. Do not restate the core pathway. If the
requested industry is not listed, say so and return the generic posture
rather than improvising sector-specific regulatory claims — a wrong
compliance assertion is worse than no assertion.
