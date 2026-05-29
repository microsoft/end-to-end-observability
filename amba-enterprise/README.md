# Enterprise AMBA Hybrid Rollout

This directory contains the enterprise implementation of Azure Monitor Baseline Alerts (AMBA) using the hybrid deployment model: Terraform orchestrates policy deployment and parameters, while Azure Policy + remediation deploy and enforce alert rules.

## Structure

```
amba-enterprise/
├── terraform/                    # Terraform wrapper calling AVM modules
│   ├── main.tf                   # Root module orchestrating AMBA deployment
│   ├── variables.tf              # Input variable definitions
│   ├── outputs.tf                # Output values
│   ├── providers.tf              # Provider configuration
│   ├── versions.tf               # Version constraints
│   └── environments/             # Per-ring parameter files
│       ├── sandbox.tfvars
│       ├── pre-prod.tfvars
│       └── prod.tfvars
├── scripts/                      # Operational scripts
│   ├── preflight-inventory.ps1   # Brownfield subscription inventory
│   ├── validate-parameters.ps1   # Parameter governance validation
│   ├── validate-naming.ps1       # Subscription naming compliance
│   └── deployment-budget.ps1     # MG deployment count checker
├── pipelines/                    # GitHub Actions workflows
│   ├── amba-deploy.yml           # Deployment pipeline
│   ├── amba-validate.yml         # Pre-deployment validation
│   └── amba-drift-detection.yml  # Drift detection schedule
├── docs/                         # Operational documentation
│   ├── coverage-matrix.md        # Full 17-initiative mapping
│   ├── onboarding-brownfield.md  # Brownfield procedures
│   ├── onboarding-greenfield.md  # Greenfield procedures
│   └── runbooks/                 # Operational runbooks
│       ├── known-issues.md
│       ├── cleanup.md
│       ├── release-upgrade.md
│       ├── maintenance-windows.md
│       └── override-lifecycle.md
└── visualizations/               # Dashboard templates
    ├── grafana-compliance.json
    └── workbook-alert-health.json
```

## Quick Start

1. Configure your environment variables in `terraform/environments/<ring>.tfvars`
2. Run validation: `./scripts/validate-parameters.ps1`
3. Deploy: `terraform -chdir=terraform init && terraform -chdir=terraform apply -var-file=environments/<ring>.tfvars`

## Prerequisites

- Terraform >= 1.9.0
- Azure CLI authenticated with sufficient permissions
- Management group hierarchy already established (ALZ reference architecture)
- VM Insights DCR deployed on target VMs (for log-based alerts)
- Subscription names must use only alphanumeric characters, underscores, and hyphens
