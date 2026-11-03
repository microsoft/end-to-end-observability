# Autonomous SRE Ops

A standalone lab that runs an **Azure SRE Agent** in **Review mode** against a small,
self-contained Azure environment (private Linux VMs, Azure Update Manager, Azure Backup,
Azure Monitor alert rules and a Log Analytics workspace). A private runtime collects
evidence for the agent.

> **Status: under construction.** This subproject is a standalone lab. It is **not** a
> phase of the client-onboarding pipeline described in
> [`.github/copilot-instructions.md`](../.github/copilot-instructions.md).

## Layout

| Path | Purpose |
| --- | --- |
| `infra/` | Terraform for the lab environment |
| `operations/` | Python runtime worker and evidence collectors |
| `agent/` | Agent instructions, skills, evidence tool, hooks, workflow manifests and knowledge |
| `scripts/` | PowerShell deployment, publishing and scenario scripts |
| `contracts/` | JSON schemas |
| `dashboard/` | Optional evidence dashboard |
| `workloads/` | Workloads that run on the lab VMs |
| `tests/` | Offline Python and PowerShell tests |
| `config/` | Sanitized sample runtime configuration |
