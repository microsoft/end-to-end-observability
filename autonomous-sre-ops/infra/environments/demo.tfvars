subscription_id      = "00000001-000a-4000-a000-000000000001"
tenant_id            = "00000002-000a-4000-9000-000000000002"
location             = "centralus"
deploy_managed_agent = true
agent_model = {
  name     = "Automatic"
  provider = "MicrosoftFoundry"
}
backup_protection_enabled    = true
deploy_runtime               = true
runtime_image_digest         = "sha256:000000000000000000000000000000000000000000000000000000000000009a"
enable_servicenow_feedback   = true
enabled_native_alert_rules   = ["disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job"]
adopt_existing_native_alerts = true

dashboard = {
  phase = "bootstrap"
}
