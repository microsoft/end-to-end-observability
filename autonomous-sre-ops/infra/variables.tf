variable "subscription_id" {
  description = "Only the confirmed disposable demonstration subscription is accepted."
  type        = string
  default     = "00000001-000a-4000-a000-000000000001"
  validation {
    condition     = var.subscription_id == "00000001-000a-4000-a000-000000000001"
    error_message = "This deployment is restricted to the confirmed demo subscription."
  }
}

variable "tenant_id" {
  type    = string
  default = "00000002-000a-4000-9000-000000000002"
  validation {
    condition     = var.tenant_id == "00000002-000a-4000-9000-000000000002"
    error_message = "The tenant must match the verified demo subscription."
  }
}

variable "location" {
  type    = string
  default = "centralus"
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

variable "agent_administrator_object_id" {
  description = "Verified tenant object ID of the deployment owner, scoped to this agent."
  type        = string
  default     = null
  validation {
    condition     = !var.deploy_managed_agent || can(regex("^[0-9a-fA-F-]{36}$", var.agent_administrator_object_id))
    error_message = "Agent deployment requires the administrator's verified object ID."
  }
}

variable "backup_protection_enabled" {
  description = "Protect only the dedicated backup target. Protection cannot be silently destroyed."
  type        = bool
  default     = true
}

variable "deploy_runtime" {
  description = "Enable only after the exact immutable operations image has been built and verified through ADO."
  type        = bool
  default     = false
}

variable "runtime_image_digest" {
  description = "Actual ACR digest from the successful exact-source runtime image build, never a mutable latest tag."
  type        = string
  default     = ""
  validation {
    condition     = !var.deploy_runtime || can(regex("^sha256:[0-9a-f]{64}$", var.runtime_image_digest))
    error_message = "Runtime deployment requires an actual published image digest."
  }
}

variable "enable_servicenow_feedback" {
  description = "Opt in through protected image/RBAC delivery; automatic feedback readiness still requires live acceptance."
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_servicenow_feedback || (var.deploy_runtime && var.deploy_managed_agent)
    error_message = "ServiceNow feedback requires the deployed private runtime and managed SRE Agent."
  }

}

variable "enabled_native_alert_rules" {
  description = "Explicitly preserve existing native intake rules; new environments remain disabled by default."
  type        = set(string)
  default     = []
  validation {
    condition     = length(setsubtract(var.enabled_native_alert_rules, toset(["disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job"]))) == 0
    error_message = "Only the five fixed native demo rules can be enabled through this setting."
  }
}

variable "adopt_existing_native_alerts" {
  description = "Adopt the three already-created native rules into this existing lab state; never enable for a fresh lab."
  type        = bool
  default     = false
}

variable "dashboard" {
  description = "Staged dashboard-only activation. Supply only public metadata and a versioned Key Vault reference, never a credential value."
  type = object({
    phase              = optional(string, "disabled")
    image_digest       = optional(string, "")
    entra_client_id    = optional(string, "")
    secret_version_uri = optional(string, "")
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
    condition = !contains(["private", "public"], var.dashboard.phase) || (
      can(regex("^sha256:[0-9a-f]{64}$", var.dashboard.image_digest)) &&
      can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.dashboard.entra_client_id)) &&
      var.dashboard.entra_client_id != "00000000-0000-0000-0000-000000000000" &&
      can(regex("^https://kv-asre-dash-demo-4d79\\.vault\\.azure\\.net/secrets/entra-client-secret/[0-9a-f]{32}$", var.dashboard.secret_version_uri))
    )
    error_message = "Private/public dashboard deployment requires a published image digest, nonzero Entra client UUID and exact dashboard-vault secret version URI."
  }
}
