variable "subscription_id" {
  description = "Target subscription ID (lowercase GUID). Supplied by scripts/Invoke-Terraform.ps1 through TF_VAR_subscription_id; it also seeds the globally unique resource-name suffix."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.subscription_id))
    error_message = "subscription_id must be a lowercase GUID."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID (lowercase GUID) that owns the target subscription. Supplied by scripts/Invoke-Terraform.ps1 through TF_VAR_tenant_id."
  type        = string
  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.tenant_id))
    error_message = "tenant_id must be a lowercase GUID."
  }
}

variable "location" {
  description = "Azure region. Only centralus is supported."
  type        = string
  default     = "centralus"
  validation {
    condition     = var.location == "centralus"
    error_message = "Only the approved Central US region is supported."
  }
}

variable "admin_ssh_public_key" {
  description = "An externally generated public key. Never pass a private key to Terraform."
  type        = string
  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?$", trimspace(var.admin_ssh_public_key)))
    error_message = "Supply a valid OpenSSH public key."
  }
}

variable "runner_ssh_public_key" {
  description = "A separate public key for the forced-command, nonadministrator SSH account."
  type        = string
  validation {
    condition = (
      can(regex("^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?$", trimspace(var.runner_ssh_public_key))) &&
      trimspace(var.runner_ssh_public_key) != trimspace(var.admin_ssh_public_key)
    )
    error_message = "Supply a valid runner public key different from the deployment administrator key."
  }
}

variable "deploy_managed_agent" {
  description = "Enable only after service admission and model availability have been verified."
  type        = bool
  default     = false
}

variable "agent_model" {
  description = "Exact model/provider returned by the service, not a guessed model name."
  type = object({
    name     = string
    provider = string
  })
  default = null
  validation {
    condition     = !var.deploy_managed_agent || var.agent_model != null
    error_message = "Agent deployment requires an explicitly verified model and provider."
  }
}

variable "operator_object_id" {
  description = "Entra object ID (lowercase GUID) of the operator running Terraform. Receives the SRE Agent Administrator role on the managed agent and, when the dashboard is enabled, may set the dashboard sign-in credential. Required when deploy_managed_agent is true."
  type        = string
  default     = null
  validation {
    condition     = var.operator_object_id == null ? true : can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.operator_object_id))
    error_message = "operator_object_id must be a lowercase GUID when set."
  }
  validation {
    condition     = !var.deploy_managed_agent || var.operator_object_id != null
    error_message = "Agent deployment requires operator_object_id (the operator's Entra object ID)."
  }
}

variable "backup_protection_enabled" {
  description = "Protect only the dedicated backup target. Apply once with false (the default), then with true; protection cannot be silently destroyed."
  type        = bool
  default     = false
}

variable "deploy_runtime" {
  description = "Enable only after the runtime image has been built and its digest recorded by scripts/Build-RuntimeImages.ps1."
  type        = bool
  default     = false
}

variable "runtime_image_digest" {
  description = "Registry digest of the runtime image, taken from the image-provenance.json written by scripts/Build-RuntimeImages.ps1; never a mutable tag."
  type        = string
  default     = ""
  validation {
    condition     = !var.deploy_runtime || can(regex("^sha256:[0-9a-f]{64}$", var.runtime_image_digest))
    error_message = "Runtime deployment requires an actual published image digest."
  }
}

variable "enable_servicenow_feedback" {
  description = "Enable the private ServiceNow feedback job. Requires the deployed runtime and managed agent; readiness still needs a live acceptance check."
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_servicenow_feedback || (var.deploy_runtime && var.deploy_managed_agent)
    error_message = "ServiceNow feedback requires the deployed private runtime and managed SRE Agent."
  }

}

variable "enabled_native_alert_rules" {
  description = "Native alert rules to enable. All five fixed demo rules are created disabled unless named here."
  type        = set(string)
  default     = []
  validation {
    condition     = length(setsubtract(var.enabled_native_alert_rules, toset(["disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job"]))) == 0
    error_message = "Only the five fixed native demo rules can be enabled through this setting."
  }
}

variable "servicenow" {
  description = "Optional ServiceNow identifiers recorded in the emitted context document. Identifiers only; no credential belongs here. Null until the ServiceNow integration is enabled."
  type = object({
    origin           = string
    api_base         = string
    client_id        = string
    facade_client_id = string
    assignment_group = string
    configuration_items = object({
      linux  = string
      patch  = string
      backup = string
    })
  })
  default = null
  validation {
    condition = var.servicenow == null ? true : (
      can(regex("^https://[a-z0-9-]+\\.service-now\\.com$", var.servicenow.origin)) &&
      can(regex("^/api/x_[0-9a-z]+_asre/case_adapter/v1$", var.servicenow.api_base)) &&
      can(regex("^[0-9a-f]{32}$", var.servicenow.client_id)) &&
      can(regex("^[0-9a-f]{32}$", var.servicenow.facade_client_id)) &&
      can(regex("^[0-9a-f]{32}$", var.servicenow.assignment_group)) &&
      can(regex("^[0-9a-f]{32}$", var.servicenow.configuration_items.linux)) &&
      can(regex("^[0-9a-f]{32}$", var.servicenow.configuration_items.patch)) &&
      can(regex("^[0-9a-f]{32}$", var.servicenow.configuration_items.backup))
    )
    error_message = "servicenow must carry an https://<instance>.service-now.com origin, an /api/x_<scope>_asre/case_adapter/v1 api_base and 32-character lowercase hex identifiers."
  }
}

variable "dashboard" {
  description = "Staged dashboard-only activation. Supply only public metadata and a versioned Key Vault reference, never a credential value. Any phase other than disabled also requires viewer_group_id and operator_object_id."
  type = object({
    phase              = optional(string, "disabled")
    image_digest       = optional(string, "")
    entra_client_id    = optional(string, "")
    secret_version_uri = optional(string, "")
    viewer_group_id    = optional(string, "")
  })
  default = {}
  validation {
    condition     = contains(["disabled", "bootstrap", "private", "public"], var.dashboard.phase)
    error_message = "Dashboard phase must be disabled, bootstrap, private or public."
  }
  validation {
    condition     = var.dashboard.phase == "disabled" || (var.deploy_managed_agent && var.deploy_runtime)
    error_message = "The dashboard requires the existing managed SRE agent and registry/runtime foundation."
  }
  validation {
    condition     = var.dashboard.viewer_group_id == "" ? var.dashboard.phase == "disabled" : can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.dashboard.viewer_group_id))
    error_message = "viewer_group_id must be a lowercase GUID and is required when the dashboard phase is not disabled."
  }
  validation {
    condition     = var.dashboard.phase == "disabled" || var.operator_object_id != null
    error_message = "A dashboard phase other than disabled requires operator_object_id."
  }
  validation {
    condition = !contains(["private", "public"], var.dashboard.phase) || (
      can(regex("^sha256:[0-9a-f]{64}$", var.dashboard.image_digest)) &&
      can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.dashboard.entra_client_id)) &&
      !can(regex("^0{8}-0{4}-0{4}-0{4}-0{12}$", var.dashboard.entra_client_id)) &&
      can(regex("^https://kv-asre-dash-${substr(sha256("${var.subscription_id}:asre-demo"), 0, 10)}\\.vault\\.azure\\.net/secrets/entra-client-secret/[0-9a-f]{32}$", var.dashboard.secret_version_uri))
    )
    error_message = "Private/public dashboard deployment requires a published image digest, nonzero Entra client UUID and exact dashboard-vault secret version URI."
  }
}
