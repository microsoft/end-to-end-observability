# -----------------------------------------------------------------------------
# Enterprise AMBA Hybrid Rollout - Variable Definitions
# All 34+ AMBA parameters plus enterprise-specific controls
# -----------------------------------------------------------------------------

# --- Core Topology ---

variable "enterprise_scale_company_prefix" {
  description = "The pseudo-root management group prefix for the enterprise (e.g., 'contoso')"
  type        = string
}

variable "platform_management_group" {
  description = "The management group for platform resources"
  type        = string
}

variable "identity_management_group" {
  description = "The management group for identity resources"
  type        = string
}

variable "management_management_group" {
  description = "The management group for management resources"
  type        = string
}

variable "connectivity_management_group" {
  description = "The management group for connectivity resources"
  type        = string
}

variable "landing_zone_management_group" {
  description = "The management group for landing zone resources"
  type        = string
}

# --- Initiative Enable/Disable Flags ---

variable "enable_amba_connectivity" {
  description = "Enable AMBA Connectivity initiative deployment"
  type        = string
  default     = "Yes"
  validation {
    condition     = contains(["Yes", "No"], var.enable_amba_connectivity)
    error_message = "Value must be 'Yes' or 'No'."
  }
}

variable "enable_amba_identity" {
  description = "Enable AMBA Identity initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_management" {
  description = "Enable AMBA Management initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_service_health" {
  description = "Enable AMBA Resource and Service Health initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_notification_assets" {
  description = "Enable AMBA Notification Assets initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_hybrid_vm" {
  description = "Enable AMBA HybridVM (Arc-enabled servers) initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_key_management" {
  description = "Enable AMBA Key Management initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_load_balancing" {
  description = "Enable AMBA Load Balancing initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_network_changes" {
  description = "Enable AMBA Network Changes initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_recovery_services" {
  description = "Enable AMBA Recovery Services initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_storage" {
  description = "Enable AMBA Storage initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_vm" {
  description = "Enable AMBA VM initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_vmss" {
  description = "Enable AMBA VMSS initiative deployment"
  type        = string
  default     = "Yes"
}

variable "enable_amba_web" {
  description = "Enable AMBA Web initiative deployment"
  type        = string
  default     = "Yes"
}

# --- Identity & RBAC ---

variable "bring_your_own_user_assigned_managed_identity" {
  description = "Use an existing User Assigned Managed Identity instead of creating one. 'Yes' or 'No'."
  type        = string
  default     = "No"
}

variable "bring_your_own_user_assigned_managed_identity_resource_id" {
  description = "Resource ID of existing UAMI (required if bring_your_own is 'Yes')"
  type        = string
  default     = ""
}

variable "user_assigned_managed_identity_name" {
  description = "Name for the UAMI created by AMBA (default: id-amba-prod-001)"
  type        = string
  default     = "id-amba-prod-001"
}

variable "management_subscription_id" {
  description = "Subscription ID for the management subscription (UAMI and monitoring resources)"
  type        = string
}

# --- Monitoring Configuration ---

variable "alz_monitor_resource_group_name" {
  description = "Name of the resource group for AMBA monitoring resources"
  type        = string
  default     = "rg-amba-monitoring-001"
}

variable "alz_monitor_resource_group_location" {
  description = "Location for the AMBA monitoring resource group"
  type        = string
  default     = "eastus"
}

variable "alz_monitor_resource_group_tags" {
  description = "Tags for the AMBA monitoring resource group"
  type        = map(string)
  default = {
    _deployed_by_amba = "True"
    environment       = "production"
  }
}

variable "alz_monitor_disable_tag_name" {
  description = "Tag name used to disable monitoring on specific resources"
  type        = string
  default     = "MonitorDisable"
}

variable "alz_monitor_disable_tag_values" {
  description = "Tag values that disable monitoring when present"
  type        = list(string)
  default     = ["true", "Test", "Dev", "Sandbox"]
}

variable "include_alz_alerts_only" {
  description = "When true, APR routes only AMBA-deployed alerts; when false, routes all alerts"
  type        = string
  default     = "true"
}

# --- Notification Configuration ---

variable "alz_monitor_action_group_email" {
  description = "Email addresses for the default AMBA action group"
  type        = list(string)
  default     = []
}

variable "alz_logic_app_resource_id" {
  description = "Resource ID of Logic App for alert routing (optional)"
  type        = string
  default     = ""
}

variable "alz_logic_app_callback_url" {
  description = "Callback URL of Logic App for alert routing (optional)"
  type        = string
  default     = ""
}

variable "bring_your_own_action_group" {
  description = "Use existing action groups instead of AMBA-created ones. Provide resource IDs."
  type        = list(string)
  default     = []
}

variable "bring_your_own_alert_processing_rule" {
  description = "Use existing alert processing rules instead of AMBA-created ones. Provide resource IDs."
  type        = list(string)
  default     = []
}

# --- Telemetry ---

variable "telemetry_opt_out" {
  description = "Opt out of AMBA telemetry collection"
  type        = string
  default     = "No"
}

# --- Enterprise Controls ---

variable "environment" {
  description = "Deployment environment ring: sandbox, pre-prod, or prod"
  type        = string
  validation {
    condition     = contains(["sandbox", "pre-prod", "prod"], var.environment)
    error_message = "Environment must be one of: sandbox, pre-prod, prod."
  }
}

variable "policy_assignment_exclusion_list" {
  description = "List of management group or subscription IDs to exclude from policy assignments"
  type        = list(string)
  default     = []
}

variable "deployment_location" {
  description = "Azure region for the deployment (locked after first deployment - choose carefully)"
  type        = string
  default     = "eastus"
}
