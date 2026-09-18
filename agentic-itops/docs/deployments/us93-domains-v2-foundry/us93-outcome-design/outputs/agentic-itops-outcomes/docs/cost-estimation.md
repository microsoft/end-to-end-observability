# Cost model and approval requirements

## Estimate Status and Assumptions

There is no verified dollar quote. This document supplies the costed topology and calculation inputs required for approval, not invented monthly totals. The subscription/region/SKU/model choices must be checked before provisioning. No price lookup, resource creation or billable inference was performed for this review.

Initial scenario: a manual, bounded full-portfolio pilot at concurrency one; at most one two-hour producer run on an active demo day; all nine agents available, but invoked only at relevant incident/lifecycle transitions. The existing producer is already provisioned and its baseline bill is excluded from the new-consumer total. Additional producer ingestion caused by a new run remains separately billable and separately authorized.

## Service-by-Service Cost Breakdown

| Service | Proposed pricing input, not approved purchase | Monthly calculation |
|---|---|---|
| Foundry chat model | Exact supported deployment/SKU after compatibility and lifecycle review; preserve manifest intent unless a change is approved | Input tokens / 1M x input rate + output tokens / 1M x output rate, including nested/retried calls |
| Embedding model | Exact model/version and vector dimensions matched to Search | Indexing/query embedding tokens / billing unit x rate |
| Azure AI Search | Basic, one replica/partition as a sandbox sizing candidate; confirm required semantic/vector features, capacity and availability needs | Search units x hourly rate x provisioned hours + semantic/premium charges |
| App Service review API/UI | Linux B1 candidate, adjust only from load/network requirements | Plan hourly rate x provisioned hours; one app does not imply nine plans |
| Azure Functions | Flex Consumption candidate for worker, tools and publisher; host/MCP compatibility gates apply | Executions and GB-seconds, plus any always-ready allocation and required host storage |
| Azure SQL Database | Basic 5-DTU candidate for small pilot metadata, subject to capacity/load/network validation | Database hourly rate x provisioned hours + applicable backup/storage charges |
| Blob/host storage | Standard GPv2, initially LRS candidate for pilot; availability/DR decision can change it | GB-month, write/read/list transactions and transfer |
| Monitoring | Application Insights/Log Analytics consumer telemetry | Ingestion, retention and query charges where applicable |
| Networking | Selected public authenticated or private topology | Private endpoints, DNS, egress and compatible host/network tier costs |

Every candidate needs a regional quote in a named currency and timestamp. For a continuously provisioned illustrative month, use 730 hours as a budgeting convention, not measured uptime. Monthly total is the sum of these line items; annual planning total is 12 x the approved monthly scenario, adjusted for variable usage. Until rates and usage are approved, both totals are **unpriced and deployment-blocking**.

Basic tiers here are sizing candidates, not availability recommendations. A higher Search replica count or a different SQL/Functions network-compatible tier changes the quote. A dedicated MCP host, if required, is an additional line item rather than a hidden assumption that remote MCP is free.

If the P0 control spike selects an A2A gateway, add its compatible hosting,
telemetry and downstream invocation usage to the quote. Model-backed cloud
evaluation also has separate model/compute charges and approval; it is not
included in the first operational batch or treated as free tracing.

## Usage and Spend Controls

At maximum producer sampling, 90 rows/minute x 120 minutes is 10,800 observations per run. This is not 10,800 agent invocations. Detect/aggregate first, cache versioned industry context, and reason only on admitted incident transitions. Size evidence token budgets using measured serialization/tokenization and retain explicit omission/missingness records.

The first approved live acceptance batch is proposed at 20 minutes, concurrency one and at most 60 Responses including nested calls/retries. A full quality matrix requires additional explicitly priced batches. Bound tool calls, input/output tokens and workflow retries independently; budget alerts alone do not enforce a hard cap.

## Reserved Versus Pay-As-You-Go

Use pay-as-you-go where available for an uncertain pilot. Do not make reservation or provisioned-throughput commitments before observed utilization and regional offers are reviewed. Any longer-term discount comparison must use the selected eligible service/SKU, term and cancellation conditions; no percentage saving is assumed.

## ROI Framing and Optimization

Measure manual minutes per reviewed incident, accepted-result rate and reviewer corrections before calculating savings. A transparent future formula is eligible incident volume x measured minutes saved / 60 x agreed labor rate, minus incremental platform and maintenance cost. Neither that labor rate nor measured savings currently exists.

Optimize by limiting reasoning to material changes, reducing redundant context without losing contradictory evidence, expiring evidence deliberately and using a derived Search projection rather than duplicating all observations in the index. Disable schedules when idle. Deleting approved owned resources can remove standing charges, but stopping the worker alone cannot; destructive teardown always requires explicit approval.
