# Override Lifecycle - Monitoring Baselines

How to exclude resources from monitoring baseline enforcement, and how those exclusions are governed and audited.

## When an override is appropriate

- An appliance or third-party VM that does not support the Azure Monitor Agent extension.
- A resource that genuinely cannot use private link (and the effect is `Deny`).
- A subscription undergoing migration where enforcement would cause noise.

Overrides are exceptions, not defaults. Each must be time-bound, justified, and tracked.

## Override mechanisms

### 1. Assignment exclusion (per subscription / MG)

Exclude a scope from the entire Monitoring Baselines assignment.

- Add the management group or subscription ID to `policy_assignment_exclusion_list` in the environment tfvars.
- This maps to the assignment `not_scopes`.
- Requires operations + governance approval.
- Maximum duration: **180 days** (renewable).

```hcl
policy_assignment_exclusion_list = [
  "/subscriptions/00000000-0000-0000-0000-000000000000"
]
```

### 2. AMA exclusion (per resource)

For individual VMs/appliances that cannot run the agent, exclude via the built-in policy's exclusion tag (where supported) or place the resource in an excluded resource group/subscription.

- Document the device type and reason (e.g., vendor appliance, unsupported OS).
- Maximum duration: **90 days** (renewable) — re-evaluate when the vendor adds support.

### 3. Private endpoint exception

When `private_endpoint_effect = "Deny"` blocks a legitimate resource:

- Temporary: add the scope to `policy_assignment_exclusion_list`.
- Preferred: complete the private endpoint + private DNS configuration so the resource becomes compliant.
- Track until the underlying network configuration is finished.

## Approval workflow

1. Request — requester documents resource, category, justification, and desired duration.
2. Approve — operations approves for technical validity; governance approves for policy.
3. Apply — update tfvars / tags and deploy.
4. Record — add to the exclusion registry below.

## Exclusion registry

Maintain a table of active exclusions (in your tracking system or this file):

| Scope / Resource | Category | Reason | Ticket | Owner | Expiry |
|---|---|---|---|---|---|
| _example: /subscriptions/…_ | Private Endpoints | Legacy app, PE in progress | OPS-1234 | network-team | 2026-09-01 |

## Quarterly audit

Each quarter, review all exclusions:

```bash
# List subscriptions excluded from the assignment
az policy assignment show \
  --name "mon-baselines-prod" \
  --scope "/providers/Microsoft.Management/managementGroups/<landing-zone-mg>" \
  --query "notScopes"

# Find resources still non-compliant (candidates that should have been remediated, not excluded)
az policy state list \
  --management-group "<landing-zone-mg>" \
  --filter "policySetDefinitionName eq 'Monitoring-Baselines-prod' and complianceState eq 'NonCompliant'"
```

- Remove expired exclusions.
- Renew only with fresh justification.
- Escalate long-standing exclusions to architecture review.
