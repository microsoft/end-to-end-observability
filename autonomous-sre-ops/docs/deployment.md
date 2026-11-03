# Deployment runbook

This runbook deploys `autonomous-sre-ops/` into one Azure subscription in seven waves, 0 to 6. Each wave ends with a
gate: start the next wave only when the gate passes. Phase 1 deploys the foundation and compute, backup protection, the
runtime images and jobs, the managed agent, the alert rules and the observe-only handler. ServiceNow case management
and the operator dashboard are deferred (see [implementation.md](implementation.md)).

Run every command from the `autonomous-sre-ops` folder in PowerShell 7.5 or later. Text in angle brackets, such as
`<subscription-id>`, is a placeholder for your own value.

## How the deployment is controlled

- **Saved plans.** `scripts/Invoke-Terraform.ps1 -Action Plan` formats, validates and plans against the remote state,
  refuses any plan that deletes or replaces a resource, and writes the saved plan, a summary and a hash manifest of the
  exact source to a new artifact directory. `-Action Apply` re-verifies that directory (subscription and tenant,
  Terraform version, four-hour expiry, provider lock file, every file hash, no extra files, clean source) and applies
  exactly that plan. `-WhatIf` runs the verification and stops before the apply. A plan is never forced: when the state
  or the source changes, make a new one.
- **Review before apply.** Read the plan summary before every apply. A change that the wave does not list means stop.
- **One subscription.** Every script takes its target from the deployment context and refuses another subscription.
- **No secrets in the repository.** Keys, state, plans, the generated context and the rendered agent assets stay in
  git-ignored paths (see Local files).
- **Agent last, in Review mode.** The agent is created in Wave 5, because its billing starts at creation and continues
  until the agent is deleted. It runs in Review mode with access level Low and never repairs anything. The observe-only
  handler is published disabled in Wave 5 and enabled in Wave 6, after the alert rules.
- **Faults only on request.** No wave injects a fault. A controlled fault is optional, runs only on your explicit
  instruction and expires on its own.
- **Run scripts in the current session** with the call operator (`& .\scripts\<Script>.ps1`). A nested `pwsh -File`
  hangs on the first `az` call.

## Waves at a glance

| Wave | Goal | Terraform toggles set in the wave | Gate |
|---|---|---|---|
| 0 | Preflight checks, SSH keys, remote state | none | The delivery readiness check exits 0 |
| 1 | Foundation and compute | none (every toggle stays off) | A new plan shows no changes; three VMs run and report a heartbeat |
| 2 | Backup protection of the backup VM | `backup_protection_enabled` | The vault holds one protected item |
| 3 | Runtime image, locked by digest | none | The digest is recorded |
| 4 | Runtime jobs and the SSH path | `deploy_runtime`, `runtime_image_digest` | Worker and collector executions succeed; the runtime proof passes |
| 5 | Managed agent, connectors, Review profile, handler (disabled) | `deploy_managed_agent`, `operator_object_id` | Agent state, connectors, one evidence request, handler disabled, no plan changes |
| 6 | Alert rules one at a time, then the handler | `enabled_native_alert_rules` | Each rule verified; handler enabled and verified |

Waves 1, 4 and 5 take tens of minutes. The toggles live in `infra\environments\demo.tfvars`, which you create from
[`demo.tfvars.example`](../infra/environments/demo.tfvars.example) in Wave 1.

## Before you start

### Prerequisites

- PowerShell 7.5 or later, Git and the OpenSSH client (`ssh-keygen`).
- Terraform exactly 1.11.2 (`scripts/Invoke-Terraform.ps1` enforces it) and access to the Terraform registry to
  download the pinned providers.
- Azure CLI, signed in to the target tenant with rights to create resource groups, custom role definitions and role
  assignments in the subscription (for example Owner, or Contributor plus User Access Administrator).
- The public IPv4 address your workstation uses to reach Azure. Wave 0 allows only that address to reach the state
  storage account.
- A clean working tree in `autonomous-sre-ops/`. The plan script refuses uncommitted changes, and the image build
  accepts only a plan made from the committed source.
- Python 3.13 only to run the tests ([implementation.md](implementation.md), Local checks). Deployment needs no Python.

### Resource names

Every name is fixed except those that carry a suffix. `<suffix>` is the first 10 hexadecimal characters of the SHA-256
of `<subscription-id>:asre-demo`; after Wave 1 it is `$Ctx.name_suffix`. The region is Central US (`cus`).

| Resource | Name |
|---|---|
| Resource groups | `rg-asre-control-cus`, `rg-asre-linux-cus`, `rg-asre-patch-cus`, `rg-asre-backup-cus`; for state, `rg-asre-state-cus` |
| Virtual machines | `vm-asre-linux-cus` (10.42.0.10), `vm-asre-patch-cus` (10.42.0.11), `vm-asre-backup-cus` (10.42.0.12); administrator account `demoops`, runner account `asrerunner` |
| Network | `vnet-asre-cus`; egress address `pip-asre-egress-cus` |
| Monitoring | Workspace `law-asre-cus`; Application Insights `appi-asre-cus`; alert rules `asre-demo-<rule>` for `disk`, `workload-oom`, `patch-installation`, `heartbeat`, `noise-overlap`, `capacity-growth` and `backup-job` |
| Backup | Vault `rsv-asre-cus`; policy `asre-daily`; the backup service creates the resource group `AzureBackupRG_centralus_1` when protection starts |
| Runtime | Container Apps environment `cae-asre-operations-cus`; jobs `job-asre-worker-cus` (every minute), `job-asre-collector-cus` (every five minutes) and `job-asre-bootstrap-cus` (manual) |
| Identities | `id-asre-agent-cus`, `id-asre-runner-cus`, `id-asre-collector-cus`, `id-asre-pull-cus`, `id-asre-bootstrap-cus` |
| With suffix | Registry `acrasre<suffix>`; storage account `stasre<suffix>`; Key Vault `kv-asre-<suffix>`; state storage account `stasrestate<suffix>` |
| Agent | `asre-demo-cus` |

Every resource that accepts tags carries `project=autonomous-sre-ops`, `environment=demonstration`,
`managed-by=terraform` and `fault-state=disabled`.

### Local files

None of these is committed.

| Path | Made by | Holds |
|---|---|---|
| `infra\environments\backend.hcl` | Wave 0, `Initialize-TerraformState.ps1` | The remote state settings (Entra ID authentication, no key) |
| `infra\environments\keys\` | Wave 0 | The administrator and runner SSH key pairs |
| `infra\environments\demo.tfvars` | You, in Wave 1; later waves edit it with `Set-TfVar` | The toggles in the table above |
| `infra\environments\context.generated.json` | `Export-AsreContext.ps1` | The deployment context |
| `out\deploy\` | The scripts, one folder per step | Saved plans, plan summaries, manifests, Terraform outputs, image provenance, rendered agent assets |

Saved plans can contain sensitive values. Keep `out\` on your workstation, do not attach it to a ticket and delete it when
the deployment is finished.

### Shell setup

Run this block in every new PowerShell session, after you replace the two placeholders with lowercase GUIDs. After
Wave 1 also run `$Ctx = Get-AsreContext`.

```powershell
Set-Location <repository-root>\autonomous-sre-ops
$SubscriptionId = '<subscription-id>'
$TenantId = '<tenant-id>'
$Artifacts = Join-Path $PWD 'out\deploy'
. .\scripts\Context.Common.ps1

function Assert-AzureContext {
    $json = az account show --output json
    if ($LASTEXITCODE -ne 0) { throw "az is not signed in. Run: az login --tenant $TenantId" }
    $account = $json | ConvertFrom-Json
    if ($account.id -cne $SubscriptionId -or $account.tenantId -cne $TenantId) {
        throw "az is signed in to a different subscription or tenant. Run: az account set --subscription $SubscriptionId"
    }
    "Signed in to subscription $($account.name) ($($account.id))."
}

function Show-PlanSummary {
    param([Parameter(Mandatory)][string]$ArtifactDirectory)
    $summary = Get-Content -LiteralPath (Join-Path $ArtifactDirectory 'plan-summary.json') -Raw | ConvertFrom-Json
    $changes = @($summary.resources | Where-Object { @($_.actions | Where-Object { $_ -notin 'no-op', 'read' }).Count -gt 0 })
    '{0} resource change(s)' -f $changes.Count
    foreach ($change in $changes) { '{0,-8} {1}' -f ($change.actions -join ','), $change.address }
}

function Set-TfVar {
    # Replaces the value of one top-level assignment in demo.tfvars; the value is an HCL literal.
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Value)
    $path = Join-Path $PWD 'infra\environments\demo.tfvars'
    $text = [IO.File]::ReadAllText($path)
    $pattern = '^(' + [regex]::Escape($Name) + '[ \t]*=[ \t]*)[^\r\n]*'
    $regex = [regex]::new($pattern, [Text.RegularExpressions.RegexOptions]::Multiline)
    if (-not $regex.IsMatch($text)) { throw "demo.tfvars has no top-level assignment for $Name." }
    $evaluator = [Text.RegularExpressions.MatchEvaluator] { param($match) $match.Groups[1].Value + $Value }
    [IO.File]::WriteAllText($path, $regex.Replace($text, $evaluator, 1), [Text.UTF8Encoding]::new($false))
}

function Import-SshKeyEnvironment {
    $keys = Join-Path $PWD 'infra\environments\keys'
    $pairs = @(@('TF_VAR_admin_ssh_public_key', 'asre-admin.pub'), @('TF_VAR_runner_ssh_public_key', 'asre-runner.pub'))
    foreach ($pair in $pairs) {
        $file = Join-Path $keys $pair[1]
        if (Test-Path -LiteralPath $file) { Set-Item -Path ('Env:' + $pair[0]) -Value (Get-Content -LiteralPath $file -Raw).Trim() }
    }
}
```

The verification commands in this runbook use three Azure CLI extensions. Install them once; the deployment scripts need
none.

```powershell
foreach ($name in 'containerapp', 'scheduled-query', 'log-analytics') {
    az extension add --name $name --upgrade --yes --only-show-errors
}
```

## Wave 0: preflight, SSH keys and state

Creates the resource group `rg-asre-state-cus` and the storage account `stasrestate<suffix>`: shared-key access off,
public network access enabled with the firewall default action Deny and one rule for each operator address you pass
(normally just yours; see step 6), blob versioning and soft delete on, and a private `tfstate` container. It gives you
Storage Blob Data Contributor on the
account and writes `infra\environments\backend.hcl`. Nothing else changes. After its writes the script reads the account
back; if the tenant forces public network access off, it stops with an error and writes no backend file (see the note
after the gate, for a tenant with a storage public-network Modify policy).

1. Sign in, and confirm that the session targets the right subscription.

   ```powershell
   az login --tenant $TenantId
   az account set --subscription $SubscriptionId
   Assert-AzureContext
   ```

2. Run the source and preflight checks. Each prints one JSON object and exits 1 if a check fails; neither changes
   anything. The preflight checks the resource providers, the `AllowBringYourOwnPublicIpAddress` feature of
   `Microsoft.Network`, the pinned VM image, the vCPU quota for three `Standard_D2s_v3` VMs in Central US, and reports
   the Defender plans for information. The source check downloads the pinned providers.

   ```powershell
   & .\scripts\Test-DemoReadiness.ps1 -Phase Source
   & .\scripts\Test-DemoReadiness.ps1 -Phase Preflight -SubscriptionId $SubscriptionId -TenantId $TenantId
   ```

   Register what the preflight reports as missing, wait until it shows as registered, and run the preflight again.
   For the feature:

   ```powershell
   az feature register --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress --subscription $SubscriptionId
   az provider register --namespace Microsoft.Network --subscription $SubscriptionId
   ```

   For any other unregistered provider, run `az provider register --namespace <provider> --subscription $SubscriptionId`.

3. Confirm that the managed agent resource type is offered in Central US. The list must include `Central US`.

   ```powershell
   $provider = az provider show --namespace Microsoft.App --subscription $SubscriptionId --output json | ConvertFrom-Json
   ($provider.resourceTypes | Where-Object { $_.resourceType -eq 'agents' }).locations
   ```

4. List the agents that already exist in the subscription. Leave them alone; Wave 6 checks that none of them reacts
   to this environment's alerts.

   ```powershell
   az resource list --subscription $SubscriptionId --resource-type Microsoft.App/agents --output table
   ```

5. Create the two SSH key pairs. The administrator key is the credential of the VM administrative account `demoops`;
   protect it with a passphrase and keep the private key. The runner key has no passphrase; it is bound to a forced
   command on the account `asrerunner`, and the runtime later installs its own key next to it. The two keys must
   differ. `Import-SshKeyEnvironment` hands the public keys to Terraform; run it in every new session before a plan.

   ```powershell
   New-Item -ItemType Directory -Force -Path infra\environments\keys | Out-Null
   ssh-keygen -q -t ed25519 -C asre-admin -f infra\environments\keys\asre-admin
   ssh-keygen -q -t ed25519 -N '' -C asre-runner -f infra\environments\keys\asre-runner
   Import-SshKeyEnvironment
   ```

6. Record the public address that Azure sees for your traffic and your Entra object id, then create the state backend.
   `-OperatorIp` takes one to eight distinct addresses: the firewall gets one rule for each and every other rule is
   removed. The first run is a rehearsal. Both calls pass the object id. If an address changes later, run the script
   again with the current set. In a tenant with a storage public-network Modify policy, read the note after the gate
   first and add `-ApplyPolicyExclusionTag` to both calls.

   ```powershell
   $OperatorIp = @('<operator-ip>')
   $OperatorObjectId = (az ad signed-in-user show --query id --output tsv).Trim().ToLowerInvariant()
   & .\scripts\Initialize-TerraformState.ps1 -SubscriptionId $SubscriptionId -TenantId $TenantId -OperatorIp $OperatorIp -OperatorObjectId $OperatorObjectId -WhatIf
   & .\scripts\Initialize-TerraformState.ps1 -SubscriptionId $SubscriptionId -TenantId $TenantId -OperatorIp $OperatorIp -OperatorObjectId $OperatorObjectId
   ```

   **Which address.** The firewall matches the address that Azure sees, which is not always the one a "what is my IP"
   service reports. Behind a VPN, a proxy or the Global Secure Access client the two differ: the client tunnels Azure
   traffic, so Azure sees its egress addresses. The sign is a run that ends with "not yet reachable with the operator
   identity" after the Azure CLI reported `AuthorizationFailure` on every attempt (a missing role reports
   `AuthorizationPermissionMismatch` instead). The first run has already created the resource group and the account,
   so your sign-in has activity to read. List the addresses that Azure recorded for you in the Activity Log, review
   them (the firewall takes IPv4 only), and run both calls again with all of them. Add your own address as well if you
   sometimes work outside the tunnel. The script's data-plane wait proves that they work.

   ```powershell
   $me = (az account show --subscription $SubscriptionId --query user.name --output tsv).Trim()
   $Seen = az monitor activity-log list --subscription $SubscriptionId --offset 7d --max-events 1000 --query "[].{caller:caller,ip:httpRequest.clientIpAddress}" --output json | ConvertFrom-Json | Where-Object { $_.caller -ieq $me -and $_.ip } | Select-Object -ExpandProperty ip -Unique
   $Seen
   $OperatorIp = @('<operator-ip>', '<seen-ip-1>', '<seen-ip-2>')
   ```

   Egress addresses can change over time; run the script again with the current set (it removes the rules that are no
   longer listed).

**Gate.** The delivery check adds `backend.hcl` and access to the state to the preflight. It must exit 0.

```powershell
& .\scripts\Test-DemoReadiness.ps1 -Phase Delivery -SubscriptionId $SubscriptionId -TenantId $TenantId
```

**Note: a tenant with a storage public-network Modify policy.** Some tenants assign a policy that rewrites
`publicNetworkAccess` to `Disabled` whenever a storage account is created or updated, unless the account (or its resource
group) carries the tag `SecurityControl=Ignore`. The SFI network-security policy `StorageAccount_PublicNetwork_Modify`
(effect Modify) is one. The state account is reached through its firewall from the operator addresses, so it cannot work
with public network access off. `-WhatIf` writes nothing and cannot show this policy behaviour; only the read-back of the
real run can. The script reads the account back after its last account write (every run) and fails closed: it names the
observed value, writes no backend file and never falls back to local Terraform state.

- **Use the switch from the first run.** In such a tenant run both calls of step 6 with `-ApplyPolicyExclusionTag`. A
  switchless re-run on an untagged account can be rewritten back to `Disabled` and break a working account (the policy
  acts on the next write, for example after your address changed). The read-back reports that, and a re-run with the
  switch repairs it. The switch is never on by default and nothing enables it automatically.
- **What the tag does.** With the switch, the script sets `SecurityControl=Ignore` on the state storage account only:
  in the create call for a new account, or by a merge that keeps the other tags on an existing one. It prints one
  warning per run. It then requires public network access to read back as `Enabled` and the tag to read back as `Ignore`.
- **What the tag does not do.** It does not touch any other resource, change any policy, grant any access or open the
  firewall (the default action stays Deny with only the operator addresses). Removing the tag changes nothing until the next
  write to the account, when the policy rewrites the account again.
- **Never tag the resource group.** The policy honours a resource group tag for every account in the group. The script
  never tags it; do not do it by hand.
- **It is a self-service bypass.** Any principal with tag or storage account write rights can add or remove the tag, so
  the only controls are RBAC and the Activity Log. Obtain the policy owner's sign-off before you use the switch. An
  expiring policy exemption, granted by the policy owner, is the governed alternative; this repository does not
  automate it.
- **Compensating controls.** The data plane accepts Entra ID only (`backend.hcl` has no key), shared-key access is off,
  the firewall default action is Deny with only the operator addresses, blob versioning and soft delete are on, and RBAC
  limits who can read or write the state.
- **Residual risk.** Each operator address is a /32 on a public network path: anyone who reaches Azure from the same
  egress address passes the firewall, though they still need Entra ID and a role on the account. The egress addresses
  of a shared service such as the Global Secure Access client are probably used by other organizations' traffic as well,
  so list only the addresses you need.
- **Teardown.** Deleting the state resource group deletes the account and its tag.

## Wave 1: foundation and compute

Creates the four lab resource groups, the network (virtual network, subnets, security rules, NAT gateway with its egress
address, private DNS zones and private endpoints), the three VMs with the Azure Monitor Agent, the Log Analytics
workspace, Application Insights and the data collection rule, the operations storage account, the Key Vault, the
registry, the managed identities and their role assignments, the Recovery Services vault and its policy, and the seven
alert rules, all disabled. It creates no agent, no runtime job and no backup protection.

1. Create the variable file from the example. Every toggle is off in it. Confirm that git sees no uncommitted change.

   ```powershell
   Copy-Item infra\environments\demo.tfvars.example infra\environments\demo.tfvars
   git status --short -- .
   ```

   The status command prints nothing: `demo.tfvars`, `out\` and the generated files are git-ignored.

2. Plan, then read the summary. Every line must be a `create`, and none may concern the agent, a Container Apps job or
   backup protection.

   ```powershell
   & .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\01-foundation-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   Show-PlanSummary -ArtifactDirectory "$Artifacts\01-foundation-plan"
   ```

3. Rehearse the apply, then apply. The real apply asks for confirmation; answer `Y` only after you have read the
   summary.

   ```powershell
   & .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\01-foundation-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId -WhatIf
   & .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\01-foundation-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   ```

4. Export the deployment context from the apply outputs, and load it. Later scripts read it from
   `infra\environments\context.generated.json`.

   ```powershell
   & .\scripts\Export-AsreContext.ps1 -SubscriptionId $SubscriptionId -TenantId $TenantId -OutputsFile "$Artifacts\01-foundation-plan\deployment-outputs.json"
   $Ctx = Get-AsreContext
   ```

**Gate.** All five must hold.

- A new plan shows no changes. If it shows any, find the cause before you continue and do not apply it.

  ```powershell
  & .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\01-foundation-check" -SubscriptionId $SubscriptionId -TenantId $TenantId
  Show-PlanSummary -ArtifactDirectory "$Artifacts\01-foundation-check"
  ```

- The three VMs run.

  ```powershell
  az vm list --subscription $SubscriptionId --show-details --output json | ConvertFrom-Json | Where-Object { $_.name -like 'vm-asre-*' } | Select-Object name, powerState
  ```

- Each VM has the `AzureMonitorLinuxAgent` extension in the state `Succeeded`.

  ```powershell
  foreach ($target in $Ctx.targets.Values) {
      az vm extension list --resource-group $target.resource_group --vm-name $target.name --subscription $SubscriptionId --output json | ConvertFrom-Json | ForEach-Object { '{0} {1}: {2}' -f $target.name, $_.name, $_.provisioningState }
  }
  ```

- All three VMs report a heartbeat to the workspace. The Azure Monitor Agent takes a few minutes; repeat the query until
  it lists three computers.

  ```powershell
  az monitor log-analytics query --workspace $Ctx.log_analytics.customer_id --subscription $SubscriptionId --analytics-query "Heartbeat | where TimeGenerated > ago(15m) | summarize LastSeen=max(TimeGenerated) by Computer" --output table
  ```

- Each guest passes the baseline recheck, a read-only script that runs inside the VM through Run Command. It checks
  cloud-init, the workload service, the guest policy (faults disabled), the incident disk of the Linux VM, and DNS
  resolution and TLS to the private endpoints of the storage account and the Key Vault. Run Command can return success
  while the script fails, so the check looks for the `ASRE_BASELINE_VERIFIED` marker in the output. Each run can take a
  few minutes.

  ```powershell
  foreach ($role in 'linux', 'patch', 'backup') {
      $target = $Ctx.targets[$role]
      $run = az vm run-command invoke --subscription $SubscriptionId --resource-group $target.resource_group --name $target.name `
          --command-id RunShellScript --scripts '@scripts\Test-GuestBaseline.sh' `
          --parameters $Ctx.storage.account_name $Ctx.key_vault.name --output json | ConvertFrom-Json
      $text = ($run.value | ForEach-Object message) -join "`n"
      '{0}: {1}' -f $target.name, $(if ($text -match '(?m)^ASRE_BASELINE_VERIFIED ') { 'verified' } else { 'NOT verified' })
  }
  ```

  If a guest is not verified, read the script output (`$text`) and repair the cause with a new plan; do not continue.

## Wave 2: backup protection

Protects the backup VM with the policy `asre-daily` (daily at 02:00 UTC, seven days of retention). The change is
one-way: a plan that turns protection off deletes the protected item, and the plan script refuses it. Never use
`-target`.

```powershell
Set-TfVar -Name 'backup_protection_enabled' -Value 'true'
& .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\02-backup-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
Show-PlanSummary -ArtifactDirectory "$Artifacts\02-backup-plan"
```

The summary must show exactly one change: the creation of `azurerm_backup_protected_vm.demo[0]`. Anything else means
drift: stop and find out why.

```powershell
& .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\02-backup-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
```

**Gate.** The vault lists one protected item, the backup VM. The state `IRPending` is normal until the first scheduled
backup completes. Do not start an on-demand backup.

```powershell
az backup item list --vault-name $Ctx.recovery_vault.name --resource-group $Ctx.recovery_vault.resource_group --subscription $SubscriptionId --backup-management-type AzureIaasVM --workload-type VM --output json | ConvertFrom-Json | ForEach-Object { '{0}: {1}' -f $_.properties.friendlyName, $_.properties.protectionState }
```

## Wave 3: runtime image

Builds the operations image in the registry with ACR Tasks, locks the tag and the manifest, and records the digest. The
build accepts only a plan directory whose manifest proves the exact source: the same subscription and tenant, the same
commit as `HEAD`, a clean tree and unchanged build inputs. The plan of Wave 2 qualifies. Do not commit between that plan
and this build; if you do, make a new plan first. The image tag is the commit id: running the build again for the same
commit reuses the locked image instead of building another, and the output directory must not exist yet.

```powershell
& .\scripts\Build-RuntimeImages.ps1 -SubscriptionId $SubscriptionId -TenantId $TenantId -ValidatedPlanDirectory "$Artifacts\02-backup-plan" -OutputDirectory "$Artifacts\03-image"
$Digest = (Get-Content -LiteralPath "$Artifacts\03-image\image-provenance.json" -Raw | ConvertFrom-Json).digest
$Digest
```

**Gate.** `$Digest` is `sha256:` followed by 64 hexadecimal characters.

## Wave 4: runtime

Creates the Container Apps environment on the runner subnet, the worker, collector and bootstrap jobs that pull the image
by digest, the bootstrap identity and its custom role, the request queue and the matching role assignments. Then it
bootstraps and verifies the private SSH path to the three VMs.

1. Set the toggles, plan and read the summary. The plan must create runtime resources only; it must not change the VMs,
   the vault or the network.

   ```powershell
   Set-TfVar -Name 'deploy_runtime' -Value 'true'
   Set-TfVar -Name 'runtime_image_digest' -Value ('"{0}"' -f $Digest)
   & .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\04-runtime-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   Show-PlanSummary -ArtifactDirectory "$Artifacts\04-runtime-plan"
   ```

2. Apply, then export the context again so that it reflects the runtime. If a job creation fails because the registry
   pull role has not propagated, make a new plan and apply again.

   ```powershell
   & .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\04-runtime-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   & .\scripts\Export-AsreContext.ps1 -SubscriptionId $SubscriptionId -TenantId $TenantId -OutputsFile "$Artifacts\04-runtime-plan\deployment-outputs.json"
   $Ctx = Get-AsreContext
   ```

3. Bootstrap the SSH path: the script retrieves the host keys, runs the bootstrap and verification jobs and installs the
   restricted runner public key on the three VMs. It writes `runtime-ssh-verification.json` next to the outputs file. If
   the VMs are deallocated, add `-ResumeTargets`, which starts them.

   ```powershell
   & .\scripts\Initialize-RuntimeSsh.ps1 -OutputsFile "$Artifacts\04-runtime-plan\deployment-outputs.json" -WhatIf
   & .\scripts\Initialize-RuntimeSsh.ps1 -OutputsFile "$Artifacts\04-runtime-plan\deployment-outputs.json"
   ```

**Gate.** The bootstrap and verification jobs ended `Succeeded` (the script reports them), and the worker and the
collector have recent executions that ended `Succeeded`. The worker runs every minute and the collector every five
minutes, so wait for the first executions. The operations storage is private, so you cannot list its blobs from the
workstation.

```powershell
foreach ($job in 'job-asre-worker-cus', 'job-asre-collector-cus') {
    az containerapp job execution list --name $job --resource-group $Ctx.resource_groups.control --subscription $SubscriptionId --output json | ConvertFrom-Json | Sort-Object { $_.properties.startTime } -Descending | Select-Object -First 3 | ForEach-Object { '{0} {1}: {2}' -f $job, $_.name, $_.properties.status }
}
```

## Wave 5: managed agent

The agent bills continuously from the moment it exists until it is deleted. Stopping it does not stop the charge. The
README Cost section has the estimate; confirm the current price per Azure Agent Unit (AAU) for Central US on the
[Azure SRE Agent pricing page](https://azure.microsoft.com/pricing/details/sre-agent/) before you continue, and
continue only when you accept the cost. The agent's model (`agent_model` in `demo.tfvars`) sets the AAU rate per token
of the on-demand work.

1. Set the toggles, plan and read the summary. It must create exactly two resources: `azapi_resource.agent[0]` and
   `azurerm_role_assignment.agent_administrator[0]` (the `SRE Agent Administrator` role for you on the agent).

   ```powershell
   Set-TfVar -Name 'deploy_managed_agent' -Value 'true'
   Set-TfVar -Name 'operator_object_id' -Value ('"{0}"' -f $OperatorObjectId)
   & .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\05-agent-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   Show-PlanSummary -ArtifactDirectory "$Artifacts\05-agent-plan"
   ```

2. Rehearse the apply, then apply. Export the context again: it now carries the agent endpoint.

   ```powershell
   & .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\05-agent-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId -WhatIf
   & .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\05-agent-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   & .\scripts\Export-AsreContext.ps1 -SubscriptionId $SubscriptionId -TenantId $TenantId -OutputsFile "$Artifacts\05-agent-plan\deployment-outputs.json"
   $Ctx = Get-AsreContext
   ```

3. Bind Azure Monitor as the agent's incident platform. `-Mode Validate` is an offline check of the declaration. `Bind`
   needs the agent name as a typed confirmation, waits until the agent is ready (add `-ReadinessTimeoutSeconds 900` for
   a slow start) and changes only the agent's incident-management configuration.

   ```powershell
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Validate
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Bind -ConfirmAgentName $Ctx.agent.name -WhatIf
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Bind -ConfirmAgentName $Ctx.agent.name
   ```

   Open the agent in the SRE Agent portal and confirm that Azure Monitor is the connected incident platform and that no
   incident filter is enabled yet.

4. Render the agent assets for this deployment, and verify the result. The rendering replaces the synthetic values of the
   source with the values of your context; publication accepts only a rendered copy. The output folder must not exist
   yet: to render again, use a new folder name.

   ```powershell
   & .\scripts\Render-AgentAssets.ps1 -OutputRoot "$Artifacts\05-agent-assets"
   & .\scripts\Render-AgentAssets.ps1 -VerifyRoot "$Artifacts\05-agent-assets"
   ```

5. Refresh the runtime proof, then publish the common profile. The publisher accepts a proof that is at most 15 minutes
   old, so run the two commands close together; if the rehearsal and the publication are further apart, refresh the proof
   again. The common profile publishes the evidence tool, four skills, the operating-boundaries prompt and the
   evidence-only investigator. It also creates the two native read-only telemetry connectors of the agent,
   `asre-operations-logs` and `monitor-client`.

   ```powershell
   & .\scripts\Initialize-RuntimeSsh.ps1 -OutputsFile "$Artifacts\05-agent-plan\deployment-outputs.json"
   & .\scripts\Initialize-ReviewAgent.ps1 -OutputsFile "$Artifacts\05-agent-plan\deployment-outputs.json" `
       -RuntimeProofFile "$Artifacts\05-agent-plan\runtime-ssh-verification.json" `
       -AssetRoot "$Artifacts\05-agent-assets" -Profile Common -WhatIf
   & .\scripts\Initialize-ReviewAgent.ps1 -OutputsFile "$Artifacts\05-agent-plan\deployment-outputs.json" `
       -RuntimeProofFile "$Artifacts\05-agent-plan\runtime-ssh-verification.json" `
       -AssetRoot "$Artifacts\05-agent-assets" -Profile Common
   ```

   Pass `-Profile Common` explicitly, as above. The `Native` profile (not the default) adds the ServiceNow-backed agents,
   a response plan and a proactive task, and it needs the `servicenow` section of the context and a pilot case, which
   Phase 1 defers.

6. Verify the connectors, then publish the observe handler with its filter disabled, and read the result back.

   ```powershell
   & .\scripts\Initialize-OperationsConnector.ps1 -VerifyOnly
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode PublishDisabled -WhatIf
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode PublishDisabled
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Verify -ExpectedState Disabled
   ```

7. Cap the agent's on-demand consumption. In the SRE Agent portal (`https://sre.azure.com`) open the agent, then Build +
   setup, Monitor, Agent consumption, and use Change AAU allocation to set the Monthly AAU limit (the portal allows 500
   to 1,000,000 units; an increase applies at once, a decrease from the next month). The limit bounds active work only;
   the always-on charge continues. At the limit the agent is unavailable for chat and actions until the next month. The
   [pricing and billing](https://learn.microsoft.com/azure/sre-agent/pricing-billing) page describes the rules.

**Gate.** All five must hold.

- The agent is `Succeeded`, in Review mode with access level Low, and its endpoint ends in `.azuresre.ai`.

  ```powershell
  $Agent = az resource show --ids $Ctx.agent.resource_id --api-version 2026-01-01 --subscription $SubscriptionId --output json | ConvertFrom-Json
  $Agent.properties.provisioningState
  $Agent.properties.actionConfiguration.mode
  $Agent.properties.actionConfiguration.accessLevel
  $Agent.properties.agentEndpoint
  ```

- The agent identity holds Reader and Monitoring Reader on the four lab resource groups and Log Analytics Reader on the
  workspace, and nothing at subscription scope.

  ```powershell
  az role assignment list --assignee $Ctx.identities.agent.principal_id --all --subscription $SubscriptionId --output json | ConvertFrom-Json | ForEach-Object { '{0}  {1}' -f $_.roleDefinitionName, $_.scope }
  ```

- One on-demand investigation returns evidence. In the SRE Agent portal chat, ask the agent to use the
  `asre-review-evidence` tool with the action `inspect` on the Linux VM, then to read the result. Print the resource id
  to paste into the chat.

  ```powershell
  $Ctx.targets.linux.resource_id
  ```

  The reply must carry a request UUID, and the result must show a receipt with the status `completed` and the mode
  `Review`. The tool accepts only the three lab VM resource ids.

- The handler and its filter read back as published and disabled (the `Verify` command above reports it).

- A new plan shows no changes. Never apply a plan that modifies the agent's incident-management binding; record what it
  shows and stop.

  ```powershell
  & .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\05-agent-check" -SubscriptionId $SubscriptionId -TenantId $TenantId
  Show-PlanSummary -ArtifactDirectory "$Artifacts\05-agent-check"
  ```

## Wave 6: alert rules, then the handler

The seven alert rules exist and are disabled. Terraform can enable five of them: `disk`, `patch-installation`,
`noise-overlap`, `capacity-growth` and `backup-job`. `workload-oom` and `heartbeat` stay disabled in Phase 1. Each
enabled rule that matches the handler's filter starts one automated investigation and uses agent units, which the
monthly limit of Wave 5 bounds.

1. Check the other agents that you listed in Wave 0. Open each in the SRE Agent portal and confirm that none has an
   incident filter or response plan that matches alerts whose names start with `asre-demo-`. If one does, change it
   before you enable any rule, or two agents will respond to the same alert.

2. Enable one rule at a time, starting with `disk`. Each rule needs its own plan; the summary must show one in-place
   update of `azurerm_monitor_scheduled_query_rules_alert_v2.guest["<rule>"]` and nothing else.

   ```powershell
   Set-TfVar -Name 'enabled_native_alert_rules' -Value '["disk"]'
   & .\scripts\Invoke-Terraform.ps1 -Action Plan -ArtifactDirectory "$Artifacts\06-alert-disk-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   Show-PlanSummary -ArtifactDirectory "$Artifacts\06-alert-disk-plan"
   & .\scripts\Invoke-Terraform.ps1 -Action Apply -ArtifactDirectory "$Artifacts\06-alert-disk-plan" -SubscriptionId $SubscriptionId -TenantId $TenantId
   ```

   Confirm the state of the rules.

   ```powershell
   az monitor scheduled-query list --resource-group $Ctx.resource_groups.control --subscription $SubscriptionId --output json | ConvertFrom-Json | Sort-Object name | ForEach-Object { '{0}: enabled={1}' -f $_.name, $_.enabled }
   ```

   To enable another rule, add it to the list (for example `'["disk", "patch-installation"]'`) and repeat with a new plan
   folder such as `06-alert-patch-plan`.

3. Enable the handler last, after at least one rule is verified. `Enable` needs the agent name as a typed confirmation
   and refuses while another incident filter on the agent is enabled. If its outcome is uncertain, it disables the
   filter again and verifies that.

   ```powershell
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Enable -ConfirmAgentName $Ctx.agent.name -WhatIf
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Enable -ConfirmAgentName $Ctx.agent.name
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Verify -ExpectedState Enabled
   ```

**Gate.** Every enabled rule reads back `enabled=True`, the handler verifies as `Enabled`, and you know the pause
command before the first alert fires: `Publish-ObserveWorkflow.ps1 -Mode Pause`.

The gate proves configuration, not routing. The first `asre-demo-disk` alert, from the controlled fault below or a real
one, is the only proof that an alert reaches the agent: it must start an investigation thread in the agent portal. If
the alert fires and no thread appears, follow the matching row in Troubleshooting before you enable another rule.

### Optional: a controlled fault

Run this only when you have explicitly decided to produce a real alert. The `Disk` fault fills the dedicated incident
disk of the Linux VM with bounded disposable files. An independent guest watchdog restores the disk when the lifetime
ends (120 to 900 seconds), and `Reset` restores it earlier. Use the same scenario id for every command, and keep the
evidence directory absolute. The `disk` rule must be enabled, and the handler enabled if you want an investigation.

```powershell
$ScenarioId = [guid]::NewGuid().ToString()
$Evidence = Join-Path $Artifacts "fault-$ScenarioId"
& .\scripts\Invoke-LinuxIncident.ps1 -Action Trigger -Fault Disk -LifetimeSeconds 900 -ScenarioId $ScenarioId -EvidenceDirectory $Evidence -WhatIf
& .\scripts\Invoke-LinuxIncident.ps1 -Action Trigger -Fault Disk -LifetimeSeconds 900 -ScenarioId $ScenarioId -EvidenceDirectory $Evidence
& .\scripts\Invoke-LinuxIncident.ps1 -Action Status -ScenarioId $ScenarioId -EvidenceDirectory $Evidence
& .\scripts\Invoke-LinuxIncident.ps1 -Action Reset -ScenarioId $ScenarioId -EvidenceDirectory $Evidence
```

The patch and backup drivers follow the same pattern; see Fault controls in [implementation.md](implementation.md) for
their bounds. The agent never repairs anything: it reports what it observed and the next steps for a human.

## After the deployment

Run these checks when Wave 6 is complete, and again the next morning.

- Inventory by tag.

  ```powershell
  az resource list --subscription $SubscriptionId --tag project=autonomous-sre-ops --output json | ConvertFrom-Json | Group-Object type | Sort-Object Name | Select-Object Count, Name
  ```

- VM power state and size. The platform may deallocate lab VMs overnight. If it does, run
  `Initialize-RuntimeSsh.ps1 -ResumeTargets` with the outputs file of the last apply, or start each VM with `az vm start`,
  wait for it to settle and verify the runtime again.

  ```powershell
  az vm list --subscription $SubscriptionId --show-details --output json | ConvertFrom-Json | Where-Object { $_.name -like 'vm-asre-*' } | Select-Object name, powerState, @{ Name = 'size'; Expression = { $_.hardwareProfile.vmSize } }
  ```

- A final plan with no changes: make a new plan folder (`07-final-check`) as in the Wave 1 gate.
- The agent state and role assignments, as in the Wave 5 gate.
- The jobs, as in the Wave 4 gate: recent executions `Succeeded`.
- Cost. After about 24 hours open Cost Management in the portal and group the cost of the subscription by the tag
  `project`; compare with the README Cost section.

## Day-2 levers

| Need | Action |
|---|---|
| Stop the agent from reacting to alerts | `Publish-ObserveWorkflow.ps1 -Mode Pause` disables the filter. It never creates a filter and refuses a filter it does not own |
| Stop one alert rule | Remove it from `enabled_native_alert_rules` with `Set-TfVar`, then plan and apply |
| Stop the always-on agent charge | Delete the agent (see Teardown). Stopping it is not enough |
| Bring the VMs back after a deallocation | `Initialize-RuntimeSsh.ps1 -ResumeTargets` (see the checks above) |
| Change the runtime image | Run Wave 3 again from a new plan of the committed source, set `runtime_image_digest`, then plan and apply |

## Teardown

Teardown is manual by design: the plan script refuses every deletion, and the Recovery Services vault and the Key Vault
protect themselves. The commands below are not exercised by the tests; read each one before you run it, and use the
portal to confirm what exists.

1. Stop activity. Pause the handler, then plan and apply with `Set-TfVar -Name 'enabled_native_alert_rules' -Value '[]'`.

   ```powershell
   & .\scripts\Publish-ObserveWorkflow.ps1 -Mode Pause
   ```

2. Delete the agent. This is the only action that stops its charge. Afterwards set `deploy_managed_agent` to `false`
   and make a plan: the refresh should drop the agent and your role assignment from the state. If the plan script still
   reports a deletion, stop and review the plan by hand before you use `terraform state rm`.

   ```powershell
   az resource delete --ids $Ctx.agent.resource_id --api-version 2026-01-01 --subscription $SubscriptionId
   Set-TfVar -Name 'deploy_managed_agent' -Value 'false'
   ```

3. Remove the protected backup item and its data. The vault has soft delete on; disable it and delete any soft-deleted
   item. The container name comes from the item (`properties.containerName`).

   ```powershell
   az backup vault backup-properties set --name $Ctx.recovery_vault.name --resource-group $Ctx.recovery_vault.resource_group --subscription $SubscriptionId --soft-delete-feature-state Disable
   az backup item list --vault-name $Ctx.recovery_vault.name --resource-group $Ctx.recovery_vault.resource_group --subscription $SubscriptionId --backup-management-type AzureIaasVM --workload-type VM --output json | ConvertFrom-Json | ForEach-Object { az backup protection disable --vault-name $Ctx.recovery_vault.name --resource-group $Ctx.recovery_vault.resource_group --subscription $SubscriptionId --container-name $_.properties.containerName --item-name $_.name --backup-management-type AzureIaasVM --workload-type VM --delete-backup-data true --yes }
   ```

4. Delete the custom role definition `ASRE credential bootstrap` before you delete its resource group. Role definitions
   can outlive the group and then block a redeployment with a name conflict.

   ```powershell
   $ControlScope = "/subscriptions/$SubscriptionId/resourceGroups/$($Ctx.resource_groups.control)"
   az role definition list --custom-role-only true --scope $ControlScope --subscription $SubscriptionId --output json | ConvertFrom-Json | Where-Object { $_.roleName -like 'ASRE*' } | Select-Object roleName, name
   ```

   Delete the one that is listed with `az role definition delete --name <name> --scope $ControlScope --subscription $SubscriptionId`.

5. Delete the resource groups in dependency order: the three VM groups first, then the control group, then
   `AzureBackupRG_centralus_1` once the vault holds no items, and the state group `rg-asre-state-cus` last. Delete a
   resource group only after you have confirmed that it holds nothing you need.

A deleted Key Vault stays reserved (purge protection) and a deleted workspace stays recoverable for a period. Both
block a redeployment that reuses the same names; see Troubleshooting.

## Troubleshooting

| Symptom | Cause and action |
|---|---|
| The preflight reports `AllowBringYourOwnPublicIpAddress` as not registered | Run the two registration commands in Wave 0 and repeat the preflight |
| A plan or apply fails with 403 on the state account, or `Initialize-TerraformState.ps1` ends with "not yet reachable with the operator identity" and `AuthorizationFailure` | Azure sees your traffic from an address that is not in the state firewall. Either your address changed, or you are behind a VPN, a proxy or the Global Secure Access client, whose egress addresses differ from the one a "what is my IP" service reports (Wave 0, step 6 shows how to read them). Run `Initialize-TerraformState.ps1` again with every current address in `-OperatorIp` (and `-ApplyPolicyExclusionTag` in a tenant with the Wave 0 policy note) |
| `Initialize-TerraformState.ps1` stops after its writes: public network access is not `Enabled`, or the tag `SecurityControl` is not `Ignore` | A tenant policy forces public network access off (see the Wave 0 note). No backend file was written. Without the switch: run again with `-ApplyPolicyExclusionTag` (after the policy owner's sign-off). With the switch and the tag present: another policy parameter, a network perimeter or propagation applies; wait and run again, then ask the policy owner. Local Terraform state is not a workaround. With the switch and the tag missing: the tag was not applied or was removed; check your tag permissions and the Activity Log |
| "The source tree has uncommitted changes" | Commit or stash the changes in `autonomous-sre-ops/`. `-AllowDirty` exists for experiments; a plan made with it cannot feed `Build-RuntimeImages.ps1` |
| "The saved plan has expired" | The four-hour limit passed. Make a new plan and apply that; never replan inside an apply |
| "The baseline rejects every deletion or replacement" | Do not work around it. Find why the plan wants to delete (drift or a renamed resource) and fix the cause. An intended deletion is manual (see Teardown) |
| A role assignment, or a job creation after one, fails | The assignment had not propagated. Make a new plan and apply again |
| An apply stops partway | Terraform records what exists. Make a new plan, review it and apply |
| `Build-RuntimeImages.ps1` reports "An unlocked source-revision image already exists" | The tag for this commit exists but is not locked, so this script did not finish it. Delete it with `az acr repository delete --name <registry> --image asre-operations:<commit> --subscription $SubscriptionId --yes` and run the script again. If the delete is refused, first run `az acr repository update --name <registry> --image asre-operations:<commit> --subscription $SubscriptionId --delete-enabled true` |
| `Build-RuntimeImages.ps1` reports "The actual ACR source build failed" | Read the `az acr build` output above the message. If it says that ACR Tasks is not permitted for the subscription or registry, stop: do not substitute a local build. Otherwise fix the cause and run the script again |
| A script hangs on its first `az` call | It ran in a nested `pwsh -File`. Run it in the session with `&` |
| `terraform` run by hand fails with "Too many command line arguments" | In PowerShell, quote each flag whole, for example `"-var-file=demo.tfvars"` |
| An `az` command fails with "was unexpected" after `--query` | `az` is a batch file (`az.cmd`), so cmd.exe splits an argument that has no space but contains an ampersand, a pipe, parentheses, angle brackets or a caret, for example `"[?starts_with(name,'x')].name"`. Put a space in the expression (after a comma) or avoid those characters: use `--output json`, then `ConvertFrom-Json` and filter in PowerShell |
| The VMs are deallocated in the morning | The platform may deallocate lab VMs overnight. Start them as described in the checks above |
| `Publish-ObserveWorkflow.ps1 -Mode Bind` times out | The agent is not ready. Run it again with `-ReadinessTimeoutSeconds 900` |
| Agent calls return 401 or 403 right after Wave 5 | The `SRE Agent Administrator` assignment has not propagated. Wait a few minutes and run the step again |
| `Publish-ObserveWorkflow.ps1 -Mode Enable` refuses | Another incident filter on the agent is enabled. Do not enable two filters; settle the other one with its owner |
| An alert fired and the agent started no investigation | Check, in order: the handler verifies as `Enabled`; the alert has severity Sev1 or Sev2 and a title that starts with `asre-demo-`; no other agent's filter claims the alert (Wave 6, step 1); the filter's `targetResource` (the workspace id from the context) equals the target resource id on the alert, letter for letter. The alert may carry the workspace id in lower case. A difference in case alone is a publisher defect, because the publisher writes the context id as it is: report it and do not edit the filter by hand. The filter fields are in [native-review-workflows.md](native-review-workflows.md) |
| A script rejects the subscription or tenant id | Both must be lowercase GUIDs |
| A second deployment in the same tenant fails on a custom role name | Custom role names are unique per tenant; see [implementation.md](implementation.md) |
| A redeployment fails on the Key Vault name | The vault is soft-deleted and purge protection blocks a purge. Recover it with `az keyvault recover --name kv-asre-<suffix> --location centralus --subscription $SubscriptionId` (not exercised) |
| A redeployment fails on the workspace name | The deleted workspace is in its recovery period. Delete it permanently first with `az monitor log-analytics workspace delete --workspace-name law-asre-cus --resource-group rg-asre-control-cus --subscription $SubscriptionId --force true --yes` (not exercised) |

## Deferred

ServiceNow case management and its production handler, the operator dashboard, a CI/CD pipeline and autonomous repair
are not deployed by this runbook. [implementation.md](implementation.md) lists what each needs.

## See also

- [README.md](../README.md): the overview, scope, cost and limitations.
- [implementation.md](implementation.md): the source layout, the context contract, local checks and fault controls.
- [runtime-networking.md](runtime-networking.md): how the agent reaches the private runtime and what to prove after
  deployment.
- [native-review-workflows.md](native-review-workflows.md): the handler, its evidence tool, the filter and publication.
- [decisions.md](decisions.md): the decisions that bind the source.
