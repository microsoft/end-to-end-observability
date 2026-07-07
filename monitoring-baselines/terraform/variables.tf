# -----------------------------------------------------------------------------
# Monitoring Baselines - Variable Definitions
# Enforces AMA deployment, diagnostic settings, and private endpoint posture
# via a custom Azure Policy initiative built from Azure built-in definitions.
#
# NOTE: This component is SEPARATE from amba-enterprise (alerting). It governs
# telemetry collection and network posture, not alert rules.
# -----------------------------------------------------------------------------

# --- Core Topology ---

variable "enterprise_scale_company_prefix" {
  description = "The pseudo-root management group prefix for the enterprise (e.g., 'contoso')"
  type        = string
}

variable "landing_zone_management_group" {
  description = "The management group ID where the monitoring baselines initiative is assigned (workloads live here)"
  type        = string
}

variable "management_subscription_id" {
  description = "Subscription ID hosting the remediation identity and Terraform state"
  type        = string
}

variable "deployment_location" {
  description = "Azure region for policy deployments and remediation (locked after first deployment)"
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Deployment environment ring: sandbox, pre-prod, or prod"
  type        = string
  validation {
    condition     = contains(["sandbox", "pre-prod", "prod"], var.environment)
    error_message = "Environment must be one of: sandbox, pre-prod, prod."
  }
}

# --- Policy Category Enable/Disable Flags ---

variable "enable_ama_policies" {
  description = "Enable Azure Monitor Agent (AMA) DeployIfNotExists policies for VMs, VMSS, and Arc machines"
  type        = string
  default     = "Yes"
  validation {
    condition     = contains(["Yes", "No"], var.enable_ama_policies)
    error_message = "Value must be 'Yes' or 'No'."
  }
}

variable "enable_diagnostic_settings_policies" {
  description = "Enable diagnostic settings DeployIfNotExists policies routing resource logs to Log Analytics"
  type        = string
  default     = "Yes"
  validation {
    condition     = contains(["Yes", "No"], var.enable_diagnostic_settings_policies)
    error_message = "Value must be 'Yes' or 'No'."
  }
}

variable "enable_private_endpoint_policies" {
  description = "Enable private endpoint posture policies for monitoring-adjacent resources"
  type        = string
  default     = "Yes"
  validation {
    condition     = contains(["Yes", "No"], var.enable_private_endpoint_policies)
    error_message = "Value must be 'Yes' or 'No'."
  }
}

# --- Policy Effects ---

variable "private_endpoint_effect" {
  description = "Effect for private endpoint policies: 'Audit' (flag non-compliance) or 'Deny' (block creation)"
  type        = string
  default     = "Audit"
  validation {
    condition     = contains(["Audit", "Deny", "Disabled"], var.private_endpoint_effect)
    error_message = "Value must be 'Audit', 'Deny', or 'Disabled'."
  }
}

# --- Diagnostic Settings Destination ---

variable "log_analytics_workspace_id" {
  description = "Resource ID of the central Log Analytics workspace used as the diagnostic settings destination"
  type        = string
}

# --- Remediation Identity ---

variable "user_assigned_managed_identity_resource_id" {
  description = "Resource ID of the User Assigned Managed Identity used for DeployIfNotExists remediation (reuses the AMBA UAMI)"
  type        = string
}

# --- Governance ---

variable "policy_assignment_exclusion_list" {
  description = "List of management group or subscription IDs to exclude from the monitoring baselines assignment"
  type        = list(string)
  default     = []
}

variable "resource_tags" {
  description = "Tags applied to resources created by this component"
  type        = map(string)
  default = {
    _deployed_by_monitoring_baselines = "True"
    managed_by                        = "terraform"
  }
}
