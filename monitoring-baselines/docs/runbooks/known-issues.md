# Known Issues - Monitoring Baselines

Documented issues and resolutions for the AMA / diagnostic settings / private endpoint policy initiative.

## 1. AMA fails to install where legacy MMA/OMS agent is present

**Symptom:** AMA extension deployment fails or both agents emit duplicate data.

**Cause:** The legacy Microsoft Monitoring Agent (MMA/OMS) and the Azure Monitor Agent can coexist temporarily but cause duplicate ingestion and occasional extension conflicts.

**Resolution:**
1. Confirm AMA is installed and healthy and that data flows to the central workspace.
2. Remove the legacy agent (`MicrosoftMonitoringAgent` / `OmsAgentForLinux` extension).
3. Re-run the AMA remediation task to confirm compliance.

> A dedicated MMA-to-AMA migration policy is tracked as a follow-up and is intentionally out of scope for this initiative.

## 2. Diagnostic settings policy reports non-compliant despite existing settings

**Symptom:** A resource already has diagnostic settings but is still flagged non-compliant.

**Cause:** Built-in diagnostic settings policies match a specific setting name and destination. Pre-existing settings with a different name or destination do not satisfy the policy.

**Resolution:**
- Diagnostic settings are additive — the policy creates its own named setting alongside any existing ones; it does not overwrite.
- If duplicate ingestion is a concern, consolidate to the policy-created setting and remove the redundant one.
- Confirm the `log_analytics_workspace_id` in the tfvars matches the intended central workspace.

## 3. Remediation fails with authorization error

**Symptom:** Remediation task status is `Failed` with an authorization/forbidden error.

**Cause:** The remediation User Assigned Managed Identity lacks the required role at the assignment scope.

**Resolution:** Confirm the identity has, at the Landing Zones MG scope:
- Monitoring Contributor
- Log Analytics Contributor
- Virtual Machine Contributor (for AMA extension installs)

These are created by `terraform/main.tf`. If the UAMI resource ID changed, re-apply Terraform so the role assignments target the correct principal.

## 4. Built-in policy definition not found at plan time

**Symptom:** `terraform plan` fails resolving a `data "azurerm_policy_definition"` by display name.

**Cause:** Microsoft renamed or deprecated a built-in policy definition.

**Resolution:** Update the affected display name in the `locals` block of `terraform/main.tf`. Find the current name with:
```bash
az policy definition list --query "[?contains(displayName, 'Azure Monitor Agent')].{name:displayName, id:name}" -o table
```

## 5. Private endpoint Deny blocks a legitimate resource

**Symptom:** After escalating `private_endpoint_effect` to `Deny`, a legitimate deployment is blocked.

**Cause:** The resource genuinely cannot use private link, or its private endpoint configuration is not yet complete.

**Resolution:**
- Add the subscription/resource to `policy_assignment_exclusion_list` (temporary, tracked).
- Or revert the effect to `Audit` while the network team completes private endpoint configuration.
- Document the exception per `runbooks/override-lifecycle.md`.

## 6. VM scale set instances not remediated

**Symptom:** AMA is installed on the VMSS model but existing instances remain non-compliant.

**Cause:** Uniform-orchestration scale sets require an instance upgrade to pick up the extension; Flexible-orchestration instances are treated as individual VMs.

**Resolution:** For Uniform VMSS, set the upgrade policy to Automatic or manually upgrade instances after remediation.
