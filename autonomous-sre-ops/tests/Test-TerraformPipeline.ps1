$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Offline checks of scripts/Invoke-Terraform.ps1. The script runs in this process against a throw-away copy of the
# repository layout; terraform, az and git are shadowed by functions, so no real tool, state, subscription or
# identity is reachable.
$Repo = Split-Path $PSScriptRoot -Parent
$TestDirectory = Join-Path ([IO.Path]::GetTempPath()) "asre-pipeline-test-$([guid]::NewGuid().ToString('N'))"
$FakeRepo = Join-Path $TestDirectory 'repo'
$Artifacts = Join-Path $TestDirectory 'artifacts'
$script:assertions = 0
$script:counter = 0

$Sub = '00000001-000a-4000-a000-000000000001'
$Tenant = '00000002-000a-4000-9000-000000000002'
$OtherSub = '00000009-000a-4000-a000-000000000009'
$OtherTenant = '00000008-000a-4000-9000-000000000008'
$FixtureCommit = '0123456789abcdef0123456789abcdef01234567'
$AdminKey = 'ssh-ed25519 Zm9yLXVuaXQtdGVzdHMtb25seQ=='
$RunnerKey = 'ssh-ed25519 ZGlzdGluY3QtdW5pdC10ZXN0LWZpeHR1cmU='
$AmbientNames = @('ARM_CLIENT_ID', 'ARM_CLIENT_SECRET', 'ARM_CLIENT_CERTIFICATE_PATH', 'ARM_USE_OIDC', 'ARM_OIDC_TOKEN',
    'ARM_OIDC_TOKEN_FILE_PATH', 'ARM_USE_MSI', 'ARM_MSI_ENDPOINT', 'ARM_ACCESS_KEY', 'ARM_SAS_TOKEN', 'ARM_USE_AZUREAD',
    'ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET', 'AZURE_TENANT_ID',
    'AZURE_FEDERATED_TOKEN_FILE', 'IDENTITY_ENDPOINT', 'IDENTITY_HEADER', 'MSI_ENDPOINT', 'MSI_SECRET',
    'TF_CLI_ARGS', 'TF_CLI_ARGS_plan', 'TF_WORKSPACE')
$ObservedNames = $AmbientNames + @('ARM_USE_CLI', 'ARM_SUBSCRIPTION_ID', 'ARM_TENANT_ID', 'TF_VAR_subscription_id',
    'TF_VAR_tenant_id', 'TF_IN_AUTOMATION', 'TF_PLUGIN_CACHE_DIR')

function Assert-True {
    param([bool]$Condition, [string]$Message)
    $script:assertions++
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Assert-Fails {
    param([scriptblock]$Block, [string]$Pattern, [string]$Message)
    $script:assertions++
    $Thrown = $null
    try { & $Block } catch { $Thrown = $_.Exception.Message }
    if ($null -eq $Thrown) { throw "ASSERTION FAILED (no error was raised): $Message" }
    if ($Thrown -notmatch $Pattern) { throw "ASSERTION FAILED (wrong error '$Thrown'): $Message" }
}

function Get-EnvSnapshot {
    $Snapshot = @{}
    foreach ($Entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) { $Snapshot[[string]$Entry.Key] = [string]$Entry.Value }
    return $Snapshot
}

function Restore-Env {
    param($Snapshot)
    foreach ($Key in @([Environment]::GetEnvironmentVariables().Keys | ForEach-Object { [string]$_ })) {
        if (-not $Snapshot.ContainsKey($Key)) { Remove-Item -LiteralPath "Env:\$Key" -Force -ErrorAction SilentlyContinue }
    }
    foreach ($Key in $Snapshot.Keys) { [Environment]::SetEnvironmentVariable($Key, $Snapshot[$Key], [EnvironmentVariableTarget]::Process) }
}

function global:terraform {
    $Arguments = @($args)
    $global:TfCalls.Add($Arguments)
    $Observed = @{}
    foreach ($Name in $global:TfObservedNames) { $Observed[$Name] = [Environment]::GetEnvironmentVariable($Name) }
    $global:TfEnv = $Observed
    $global:LASTEXITCODE = 0
    $Sub = if ($Arguments[0] -like '-chdir=*') { $Arguments[1] } else { $Arguments[0] }
    if ($Sub -ceq $global:TfFail) { $global:LASTEXITCODE = 3; return }
    switch ($Sub) {
        'version' { return $global:TfVersionJson }
        'plan' {
            $Out = @($Arguments | Where-Object { $_.StartsWith('-out=') })[0].Substring(5)
            [IO.File]::WriteAllText($Out, 'UNIT TEST FIXTURE ONLY; NOT A TERRAFORM PLAN')
        }
        'show' { return $global:PlanJson }
        'output' {
            $global:LASTEXITCODE = $global:OutputExit
            return $global:OutputJson
        }
    }
}

function global:az {
    $global:AzCalls.Add(@($args))
    $global:LASTEXITCODE = $global:AzExit
    if ($global:AzExit -ne 0) { return }
    if ($args[0] -eq 'account' -and $args[1] -eq 'show') {
        $Index = [array]::IndexOf($args, '--subscription')
        return (@{ id = $args[$Index + 1]; tenantId = $global:AzTenant; state = $global:AzState } | ConvertTo-Json -Compress)
    }
    throw 'The fixture permits only az account show.'
}

function global:git {
    $global:GitCalls.Add(@($args))
    $global:LASTEXITCODE = 0
    if ($args -contains 'rev-parse') { return $global:GitCommit }
    if ($args -contains 'status') { return $global:GitStatus }
}

function New-PlanJson {
    param([string]$Actions = '"create"', [string]$PlanSub = $Sub, [string]$PlanTenant = $Tenant, [switch]$NoVariables)
    $Variables = if ($NoVariables) { '' } else { "`"variables`":{`"subscription_id`":{`"value`":`"$PlanSub`"},`"tenant_id`":{`"value`":`"$PlanTenant`"}}," }
    return "{$Variables`"resource_changes`":[{`"address`":`"azurerm_resource_group.fixture`",`"type`":`"azurerm_resource_group`",`"change`":{`"actions`":[$Actions],`"after`":{`"name`":`"fixture-only`"}}}]}"
}

function Reset-Fixtures {
    $global:TfCalls = [Collections.Generic.List[object]]::new()
    $global:AzCalls = [Collections.Generic.List[object]]::new()
    $global:GitCalls = [Collections.Generic.List[object]]::new()
    $global:TfObservedNames = $ObservedNames
    $global:TfEnv = @{}
    $global:TfFail = ''
    $global:TfVersionJson = '{"terraform_version":"1.11.2"}'
    $global:PlanJson = New-PlanJson
    $global:OutputJson = '{"fixture_only":{"value":true}}'
    $global:OutputExit = 0
    $global:AzExit = 0
    $global:AzTenant = $Tenant
    $global:AzState = 'Enabled'
    $global:GitCommit = $FixtureCommit
    $global:GitStatus = ''
    $env:TF_VAR_admin_ssh_public_key = $AdminKey
    $env:TF_VAR_runner_ssh_public_key = $RunnerKey
}

function New-ArtifactPath {
    $script:counter++
    return Join-Path $Artifacts "a$($script:counter)"
}

function Write-FixtureFile {
    param([string]$Relative, [string]$Content)
    $Path = Join-Path $FakeRepo $Relative
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $Path -Parent)
    [IO.File]::WriteAllText($Path, $Content)
}

function Get-BackendText {
    param([string]$BackendSub = $Sub, [string]$BackendTenant = $Tenant, [string]$Extra = '')
    return "resource_group_name  = `"rg-fixture-state`"`nstorage_account_name = `"stfixturestate`"`ncontainer_name       = `"tfstate`"`nkey                  = `"fixture.tfstate`"`nuse_azuread_auth     = true`nsubscription_id      = `"$BackendSub`"`ntenant_id            = `"$BackendTenant`"`n$Extra"
}

function Invoke-Wrapper {
    param([string]$Action, [string]$Directory, [hashtable]$Extra = @{}, [string]$SubscriptionId = $Sub, [string]$TenantId = $Tenant)
    & $Wrapper -Action $Action -ArtifactDirectory $Directory -SubscriptionId $SubscriptionId -TenantId $TenantId @Extra
}

function Get-Calls {
    param([string]$Subcommand)
    return , @($global:TfCalls | Where-Object { ($_.Count -gt 1 -and $_[1] -ceq $Subcommand) -or $_[0] -ceq $Subcommand })
}

function Edit-Manifest {
    param([string]$Directory, [scriptblock]$Edit)
    $Path = Join-Path $Directory 'manifest.json'
    $Manifest = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -DateKind String
    & $Edit $Manifest $Directory
    $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Set-ArtifactFile {
    # Rewrites an artifact file and re-pins its hash, as an attacker who controls the directory could.
    param([string]$Directory, [string]$Relative, [string]$Text)
    $Path = Join-Path $Directory $Relative
    [IO.File]::WriteAllText($Path, $Text)
    $Hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    Edit-Manifest $Directory { param($Manifest) $Manifest.files.PSObject.Properties[$Relative].Value = $Hash }
}

function Copy-Artifact {
    $Target = New-ArtifactPath
    Copy-Item -LiteralPath $PlannedArtifact -Destination $Target -Recurse
    return $Target
}

function Assert-NoTerraformMutation {
    param([string]$Message)
    Assert-True (@($global:TfCalls | Where-Object { $_ -contains 'init' -or $_ -contains 'apply' }).Count -eq 0) $Message
}

$EnvSnapshot = Get-EnvSnapshot
try {
    # --- fixture repository ---------------------------------------------------------------------------------------
    $null = New-Item -ItemType Directory -Path $FakeRepo, $Artifacts
    foreach ($Name in @('Invoke-Terraform.ps1', 'Context.Common.ps1')) {
        $Target = Join-Path $FakeRepo "scripts\$Name"
        $null = New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent)
        Copy-Item -LiteralPath (Join-Path $Repo "scripts\$Name") -Destination $Target
    }
    $Wrapper = Join-Path $FakeRepo 'scripts\Invoke-Terraform.ps1'
    $Fixture = [ordered]@{
        'infra/main.tf'                                   = '# fixture'
        'infra/.terraform.lock.hcl'                       = '# lock fixture'
        'infra/environments/demo.tfvars'                  = 'location = "centralus"'
        'infra/environments/backend.hcl'                  = (Get-BackendText)
        'infra/environments/keys/runner_key.json'         = 'MUST NEVER BE COPIED'
        'infra/environments/context.generated.json'       = 'MUST NEVER BE COPIED'
        'infra/.terraform/providers/provider.json'        = 'MUST NEVER BE COPIED'
        'infra/old.tfstate.json'                          = 'MUST NEVER BE COPIED'
        'infra/saved.tfplan'                              = 'MUST NEVER BE COPIED'
        'workloads/linux-demo-service/guest_control.py'   = '# fixture'
        'workloads/__pycache__/cached.py'                 = 'MUST NEVER BE COPIED'
        'operations/Dockerfile'                           = 'FROM scratch'
        'operations/.dockerignore'                        = '*'
        'operations/requirements.txt'                     = ''
        'operations/app.py'                               = '# fixture'
        'contracts/case.schema.json'                      = '{}'
        'agent/readme.md'                                 = '# fixture'
        'dashboard/server.py'                             = '# fixture'
        'dashboard/context.py'                            = '# fixture'
        'dashboard/index.html'                            = '<html></html>'
        'dashboard/evidence.html'                         = '<html></html>'
        'dashboard/Dockerfile'                            = 'FROM scratch'
        'dashboard/.dockerignore'                         = '*'
        'dashboard/requirements.txt'                      = ''
    }
    foreach ($Entry in $Fixture.GetEnumerator()) { Write-FixtureFile $Entry.Key $Entry.Value }
    $Cache = Join-Path $TestDirectory 'plugin-cache'
    $env:TF_PLUGIN_CACHE_DIR = $Cache

    Reset-Fixtures
    Assert-True ((Get-Command terraform).CommandType -eq 'Function' -and (Get-Command az).CommandType -eq 'Function' -and
        (Get-Command git).CommandType -eq 'Function') 'The real terraform, az or git is reachable; the fixtures must shadow all three.'

    # --- source pins ----------------------------------------------------------------------------------------------
    $Source = Get-Content -LiteralPath (Join-Path $Repo 'scripts\Invoke-Terraform.ps1') -Raw
    foreach ($Forbidden in @('##vso', 'idToken', 'servicePrincipalId', 'BUILD_SOURCEVERSION', 'TEMPLATE_SOURCE_COMMIT', 'template_commit',
            '-auto-approve', 'Invoke-Expression', 'destroy')) {
        Assert-True (-not $Source.Contains($Forbidden)) "The wrapper still carries '$Forbidden'."
    }
    Assert-True ($Source -notmatch '\$env:ARM_USE_OIDC\s*=' -and $Source -notmatch '\$env:ARM_CLIENT_ID\s*=') 'The wrapper must never set a service-principal identity.'
    foreach ($Required in @('ARM_USE_CLI', '-lockfile=readonly', '-lock-timeout=60s', '-lock=false', 'Assert-AsreSubscription', 'SupportsShouldProcess')) {
        Assert-True $Source.Contains($Required) "The wrapper lost '$Required'."
    }

    # --- parameter validation -------------------------------------------------------------------------------------
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) -SubscriptionId $Sub.ToUpperInvariant() } 'SubscriptionId must be a lowercase GUID' 'An uppercase subscription id was accepted.'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) -TenantId 'not-a-guid' } 'TenantId must be a lowercase GUID' 'A malformed tenant id was accepted.'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) -TenantId "$Tenant`n" } 'TenantId must be a lowercase GUID' 'A tenant id with a trailing newline was accepted.'
    Assert-True ($global:TfCalls.Count -eq 0 -and $global:AzCalls.Count -eq 0) 'Parameter validation reached a tool.'

    # --- plan: happy path, hygiene, manifest ----------------------------------------------------------------------
    Reset-Fixtures
    foreach ($Name in $AmbientNames) { [Environment]::SetEnvironmentVariable($Name, 'fixture-only', [EnvironmentVariableTarget]::Process) }
    $PlannedArtifact = New-ArtifactPath
    Invoke-Wrapper -Action Plan -Directory $PlannedArtifact
    foreach ($Name in $AmbientNames) {
        Assert-True ($null -eq $global:TfEnv[$Name]) "Ambient credential variable $Name reached terraform."
    }
    Assert-True ($global:TfEnv['ARM_USE_CLI'] -ceq 'true' -and $global:TfEnv['ARM_SUBSCRIPTION_ID'] -ceq $Sub -and
        $global:TfEnv['ARM_TENANT_ID'] -ceq $Tenant -and $global:TfEnv['TF_VAR_subscription_id'] -ceq $Sub -and
        $global:TfEnv['TF_VAR_tenant_id'] -ceq $Tenant -and $global:TfEnv['TF_IN_AUTOMATION'] -ceq 'true') 'The operator Azure CLI identity was not exported to terraform.'
    Assert-True ($global:TfEnv['TF_PLUGIN_CACHE_DIR'] -ceq $Cache -and (Test-Path -LiteralPath $Cache -PathType Container)) 'An operator provider cache directory was not honoured.'
    Assert-True ($global:AzCalls.Count -eq 1 -and $global:AzCalls[0][0] -ceq 'account' -and $global:AzCalls[0][1] -ceq 'show' -and
        $global:AzCalls[0] -contains $Sub) 'Plan must make exactly one subscription-pinned az account show call.'
    Assert-True ($global:GitCalls.Count -eq 2 -and $global:GitCalls[0][0] -ceq '-C' -and $global:GitCalls[0][1] -ceq $FakeRepo -and
        $global:GitCalls[1] -contains '--porcelain') 'git must be asked about the source tree only.'
    Assert-True ($global:GitCalls[1][-1] -ceq '.') 'The dirty check must be scoped to the subproject.'

    $SubCommands = @($global:TfCalls | ForEach-Object { if ($_[0] -like '-chdir=*') { $_[1] } else { $_[0] } })
    Assert-True (($SubCommands -join ',') -ceq 'version,fmt,init,validate,state,plan,show') "Unexpected terraform sequence: $($SubCommands -join ',')."
    Assert-True ($SubCommands -notcontains 'apply') 'Plan called apply.'
    $Infra = Join-Path $FakeRepo 'infra'
    $Init = $global:TfCalls[2]
    Assert-True ($Init[0] -ceq "-chdir=$Infra" -and $Init -contains '-input=false' -and $Init -contains '-lockfile=readonly' -and
        $Init -contains '-backend-config=environments/backend.hcl') 'Plan init did not use the locked providers and the remote backend file.'
    $PlanCall = $global:TfCalls[5]
    Assert-True ($PlanCall -contains '-input=false' -and $PlanCall -contains '-lock=false' -and
        $PlanCall -contains '-var-file=environments/demo.tfvars' -and $PlanCall -contains "-out=$(Join-Path $PlannedArtifact 'demo.tfplan')") 'Terraform plan arguments were not preserved.'

    $ManifestPath = Join-Path $PlannedArtifact 'manifest.json'
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json -DateKind String
    Assert-True ((@($Manifest.PSObject.Properties.Name) -join ',') -ceq 'subscription_id,tenant_id,terraform,source_commit,source_dirty,created_at,files') 'The manifest does not have the exact frozen shape.'
    Assert-True ($Manifest.subscription_id -ceq $Sub -and $Manifest.tenant_id -ceq $Tenant -and $Manifest.terraform -ceq '1.11.2' -and
        $Manifest.source_commit -ceq $FixtureCommit -and $Manifest.source_dirty -eq $false) 'Manifest identity fields are wrong.'
    $Created = [DateTimeOffset]::MinValue
    Assert-True ([DateTimeOffset]::TryParseExact([string]$Manifest.created_at, 'o', [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::None, [ref]$Created) -and ([DateTimeOffset]::UtcNow - $Created).TotalMinutes -lt 5) 'created_at is not a fresh round-trip timestamp.'
    $Listed = @($Manifest.files.PSObject.Properties.Name)
    foreach ($Required in @('infra/.terraform.lock.hcl', 'infra/environments/backend.hcl', 'infra/environments/demo.tfvars', 'infra/main.tf',
            'operations/Dockerfile', 'operations/.dockerignore', 'operations/requirements.txt', 'operations/app.py',
            'dashboard/server.py', 'dashboard/context.py', 'dashboard/index.html', 'dashboard/evidence.html', 'dashboard/Dockerfile',
            'dashboard/.dockerignore', 'dashboard/requirements.txt', 'workloads/linux-demo-service/guest_control.py',
            'contracts/case.schema.json', 'agent/readme.md', 'scripts/Invoke-Terraform.ps1', 'demo.tfplan', 'plan-summary.json')) {
        Assert-True ($Listed -contains $Required) "The manifest does not pin $Required."
    }
    foreach ($Name in $Listed) {
        Assert-True ($Name -notmatch 'keys/|context\.generated|\.terraform/|tfstate|saved\.tfplan|__pycache__|MUST') "A forbidden file reached the artifact: $Name"
        $Item = Join-Path $PlannedArtifact $Name
        Assert-True ($Manifest.files.PSObject.Properties[$Name].Value -ceq (Get-FileHash -LiteralPath $Item -Algorithm SHA256).Hash) "The manifest hash of $Name is wrong."
    }
    foreach ($Present in (Get-ChildItem -LiteralPath $PlannedArtifact -Recurse -File -Force)) {
        $Relative = [IO.Path]::GetRelativePath($PlannedArtifact, $Present.FullName).Replace('\', '/')
        Assert-True ($Relative -ceq 'manifest.json' -or $Listed -contains $Relative) "An artifact file is not hash pinned: $Relative"
    }
    Assert-True ((Get-Content -LiteralPath (Join-Path $PlannedArtifact 'infra\.terraform.lock.hcl') -Raw) -ceq '# lock fixture') 'The hidden provider lock file was not copied.'
    $Summary = Get-Content -LiteralPath (Join-Path $PlannedArtifact 'plan-summary.json') -Raw | ConvertFrom-Json
    Assert-True ($Summary.resources.Count -eq 1 -and $Summary.resources[0].address -ceq 'azurerm_resource_group.fixture' -and
        -not $Summary.destructive -and $Summary.subscription_id -ceq $Sub) 'Plan did not publish its restricted change summary.'

    # --- plan: the default provider cache location ---------------------------------------------------------------
    Reset-Fixtures
    Remove-Item Env:\TF_PLUGIN_CACHE_DIR
    $DataRoot = [Environment]::GetFolderPath('LocalApplicationData')
    $DefaultRoot = Join-Path $DataRoot 'asre-terraform'
    $Existed = Test-Path -LiteralPath $DefaultRoot
    try {
        Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath)
        Assert-True ($global:TfEnv['TF_PLUGIN_CACHE_DIR'] -ceq [IO.Path]::Combine($DataRoot, 'asre-terraform', 'plugin-cache') -and
            (Test-Path -LiteralPath $global:TfEnv['TF_PLUGIN_CACHE_DIR'] -PathType Container)) 'The default provider cache is not under the local application data folder.'
    }
    finally {
        if (-not $Existed -and (Test-Path -LiteralPath $DefaultRoot)) { Remove-Item -LiteralPath $DefaultRoot -Recurse -Force }
        $env:TF_PLUGIN_CACHE_DIR = $Cache
    }

    # --- plan: -WhatIf is ignored (a plan never changes Azure) ----------------------------------------------------
    Reset-Fixtures
    $WhatIfDirectory = New-ArtifactPath
    Invoke-Wrapper -Action Plan -Directory $WhatIfDirectory -Extra @{ WhatIf = $true }
    Assert-True ((Test-Path -LiteralPath (Join-Path $WhatIfDirectory 'manifest.json')) -and (Get-Calls 'plan').Count -eq 1) 'Plan must run completely under -WhatIf.'

    # --- plan: refusals -------------------------------------------------------------------------------------------
    Reset-Fixtures
    $Existing = New-ArtifactPath
    $null = New-Item -ItemType Directory -Path $Existing
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory $Existing } 'new, empty artifact directory' 'An existing artifact directory was reused.'

    Reset-Fixtures
    $global:AzTenant = $OtherTenant
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'not the enabled deployment subscription and tenant' 'A signed-in account of another tenant was accepted.'
    $global:AzTenant = $Tenant
    $global:AzState = 'Disabled'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'not the enabled deployment subscription and tenant' 'A disabled subscription was accepted.'
    $global:AzState = 'Enabled'
    $global:AzExit = 1
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'cannot read the deployment subscription' 'A missing az login was accepted.'
    Assert-True ($global:TfCalls.Count -eq 0) 'Terraform ran although the Azure CLI identity was refused.'

    Reset-Fixtures
    $global:TfVersionJson = '{"terraform_version":"1.11.1"}'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'Terraform 1\.11\.2 is required' 'A wrong terraform version was accepted.'
    $global:TfVersionJson = 'not json'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'Terraform 1\.11\.2 is required' 'An unreadable terraform version was accepted.'

    Reset-Fixtures
    $env:TF_VAR_admin_ssh_public_key = ''
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'TF_VAR_admin_ssh_public_key' 'A missing administrator key was accepted.'
    $env:TF_VAR_admin_ssh_public_key = "$AdminKey`nssh-rsa AAAA"
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'TF_VAR_admin_ssh_public_key' 'A multi-line administrator key was accepted.'
    $env:TF_VAR_admin_ssh_public_key = $AdminKey
    $env:TF_VAR_runner_ssh_public_key = $AdminKey
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'separate, nonadministrator public key' 'The runner key equal to the admin key was accepted.'

    Reset-Fixtures
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) -Extra @{ VarFile = '../main.tf' } } 'existing file under infra/environments' 'A var file outside infra/environments was accepted.'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) -Extra @{ VarFile = 'environments/absent.tfvars' } } 'existing file under infra/environments' 'A missing var file was accepted.'
    Assert-True ((Get-Calls 'plan').Count -eq 0) 'A refused var file reached terraform plan.'

    # backend.hcl guard
    $BackendPath = Join-Path $FakeRepo 'infra\environments\backend.hcl'
    $GoodBackend = Get-BackendText
    try {
        Reset-Fixtures
        Remove-Item -LiteralPath $BackendPath
        Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'Initialize-TerraformState\.ps1' 'A missing backend file was accepted.'
        Assert-True ((Get-Calls 'plan').Count -eq 0 -and (Get-Calls 'init').Count -eq 0) 'A missing backend file reached terraform init.'
        foreach ($Case in @(
                @{ Text = (Get-BackendText -BackendSub $OtherSub); Pattern = 'requested subscription_id'; Why = 'another subscription' },
                @{ Text = (Get-BackendText -BackendTenant $OtherTenant); Pattern = 'requested tenant_id'; Why = 'another tenant' },
                @{ Text = ($GoodBackend -replace 'use_azuread_auth\s*=\s*true', 'use_azuread_auth = false'); Pattern = 'Entra authentication'; Why = 'shared-key authentication' },
                @{ Text = ($GoodBackend -replace '(?m)^use_azuread_auth\s*=\s*true\r?\n', ''); Pattern = 'Entra authentication'; Why = 'no authentication setting' },
                @{ Text = (Get-BackendText -Extra 'access_key = "fixture-only"'); Pattern = 'must not carry credentials'; Why = 'an access key' },
                @{ Text = (Get-BackendText -Extra 'sas_token = "fixture-only"'); Pattern = 'must not carry credentials'; Why = 'a SAS token' },
                @{ Text = (Get-BackendText -Extra 'client_secret = "fixture-only"'); Pattern = 'must not carry credentials'; Why = 'a client secret' },
                @{ Text = (Get-BackendText -Extra 'use_msi = true'); Pattern = 'must not carry credentials'; Why = 'a managed-identity switch' },
                @{ Text = ($GoodBackend + "subscription_id = `"$Sub`"`n"); Pattern = 'requested subscription_id'; Why = 'a duplicated subscription key' })) {
            Reset-Fixtures
            [IO.File]::WriteAllText($BackendPath, $Case.Text)
            Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } $Case.Pattern "A backend file with $($Case.Why) was accepted."
            Assert-True ((Get-Calls 'init').Count -eq 0) "A backend file with $($Case.Why) reached terraform init."
        }
    }
    finally { [IO.File]::WriteAllText($BackendPath, $GoodBackend) }

    # git state
    Reset-Fixtures
    $global:GitStatus = ' M infra/main.tf'
    $DirtyDirectory = New-ArtifactPath
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory $DirtyDirectory } 'uncommitted changes' 'A dirty source tree was planned.'
    Assert-True (-not (Test-Path -LiteralPath $DirtyDirectory) -and (Get-Calls 'plan').Count -eq 0) 'A refused dirty tree created an artifact or planned.'
    $AllowedDirectory = New-ArtifactPath
    Invoke-Wrapper -Action Plan -Directory $AllowedDirectory -Extra @{ AllowDirty = $true }
    $AllowedManifest = Get-Content -LiteralPath (Join-Path $AllowedDirectory 'manifest.json') -Raw | ConvertFrom-Json -DateKind String
    Assert-True ($AllowedManifest.source_dirty -eq $true) '-AllowDirty did not record source_dirty.'
    Reset-Fixtures
    $global:GitCommit = 'main'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'Unable to identify the source commit' 'A non-sha commit was accepted.'
    $global:GitCommit = $FixtureCommit.ToUpperInvariant()
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'Unable to identify the source commit' 'An upper-case commit was accepted.'

    # terraform failures and plan content
    Reset-Fixtures
    $global:TfFail = 'fmt'
    $FmtDirectory = New-ArtifactPath
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory $FmtDirectory } 'Terraform fmt failed' 'A formatting failure was ignored.'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $FmtDirectory 'manifest.json'))) 'A failed plan left a manifest.'
    $global:TfFail = 'plan'
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'Terraform plan failed' 'A plan failure was ignored.'

    foreach ($Actions in @('"delete"', '"delete","create"', '"create","delete"')) {
        Reset-Fixtures
        $global:PlanJson = New-PlanJson -Actions $Actions
        $Destructive = New-ArtifactPath
        Assert-Fails { Invoke-Wrapper -Action Plan -Directory $Destructive } 'rejects every deletion or replacement.*azurerm_resource_group\.fixture' "A plan with actions [$Actions] was accepted."
        Assert-True (-not (Test-Path -LiteralPath (Join-Path $Destructive 'manifest.json'))) 'A rejected destructive plan published an applicable manifest.'
    }
    Reset-Fixtures
    $global:PlanJson = New-PlanJson -PlanSub $OtherSub
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'not planned for the requested subscription_id' 'A plan for another subscription was accepted.'
    $global:PlanJson = New-PlanJson -PlanTenant $OtherTenant
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'not planned for the requested tenant_id' 'A plan for another tenant was accepted.'
    $global:PlanJson = New-PlanJson -NoVariables
    Assert-Fails { Invoke-Wrapper -Action Plan -Directory (New-ArtifactPath) } 'not planned for the requested subscription_id' 'A plan without variables was accepted.'
    $global:PlanJson = '{"variables":{"subscription_id":{"value":"' + $Sub + '"},"tenant_id":{"value":"' + $Tenant + '"}}}'
    $EmptyPlan = New-ArtifactPath
    Invoke-Wrapper -Action Plan -Directory $EmptyPlan
    $EmptySummary = Get-Content -LiteralPath (Join-Path $EmptyPlan 'plan-summary.json') -Raw | ConvertFrom-Json
    Assert-True (@($EmptySummary.resources).Count -eq 0) 'A plan without changes produced a phantom resource.'

    # --- apply: happy path and -WhatIf ---------------------------------------------------------------------------
    Reset-Fixtures
    $WhatIfApply = Copy-Artifact
    Invoke-Wrapper -Action Apply -Directory $WhatIfApply -Extra @{ WhatIf = $true }
    Assert-True ((Get-Calls 'apply').Count -eq 0 -and (Get-Calls 'output').Count -eq 0 -and (Get-Calls 'plan').Count -eq 0) '-WhatIf reached apply, output or a replan.'
    Assert-True ((Get-Calls 'init').Count -eq 1 -and -not (Test-Path -LiteralPath (Join-Path $WhatIfApply 'deployment-outputs.json'))) '-WhatIf must verify and initialise but publish nothing.'

    Reset-Fixtures
    foreach ($Name in $AmbientNames) { [Environment]::SetEnvironmentVariable($Name, 'fixture-only', [EnvironmentVariableTarget]::Process) }
    $ApplyDirectory = Copy-Artifact
    Invoke-Wrapper -Action Apply -Directory $ApplyDirectory -Extra @{ Confirm = $false }
    foreach ($Name in $AmbientNames) { Assert-True ($null -eq $global:TfEnv[$Name]) "Ambient credential variable $Name reached apply." }
    $ApplySubCommands = @($global:TfCalls | ForEach-Object { if ($_[0] -like '-chdir=*') { $_[1] } else { $_[0] } })
    Assert-True (($ApplySubCommands -join ',') -ceq 'version,init,apply,output') "Unexpected apply sequence: $($ApplySubCommands -join ',')."
    $ArtifactInfra = Join-Path $ApplyDirectory 'infra'
    $ApplyCall = (Get-Calls 'apply')[0]
    Assert-True ($ApplyCall[0] -ceq "-chdir=$ArtifactInfra" -and $ApplyCall -contains '-lock-timeout=60s' -and $ApplyCall -notcontains '-lock=false' -and
        $ApplyCall -contains '-input=false' -and $ApplyCall[-1] -ceq (Join-Path $ApplyDirectory 'demo.tfplan')) 'Apply must acquire the lease and apply only the saved plan inside the artifact.'
    $ApplyInit = (Get-Calls 'init')[0]
    Assert-True ($ApplyInit[0] -ceq "-chdir=$ArtifactInfra" -and $ApplyInit -contains '-lockfile=readonly' -and $ApplyInit -contains '-backend-config=environments/backend.hcl') 'Apply must init the artifact copy with the same backend file.'
    $Outputs = [IO.File]::ReadAllBytes((Join-Path $ApplyDirectory 'deployment-outputs.json'))
    Assert-True ($Outputs.Length -gt 2 -and -not ($Outputs[0] -eq 0xEF -and $Outputs[1] -eq 0xBB) -and
        [Text.Encoding]::UTF8.GetString($Outputs).Contains('fixture_only')) 'deployment-outputs.json was not written as UTF-8 without BOM.'
    Assert-True ($global:AzCalls.Count -eq 1) 'Apply made an unexpected Azure call.'

    # a second apply of the same directory tolerates its own outputs and a local .terraform folder
    Reset-Fixtures
    $null = New-Item -ItemType Directory -Force -Path (Join-Path $ApplyDirectory 'infra\.terraform\providers')
    [IO.File]::WriteAllText((Join-Path $ApplyDirectory 'infra\.terraform\providers\x'), 'local init state')
    Invoke-Wrapper -Action Apply -Directory $ApplyDirectory -Extra @{ Confirm = $false }
    Assert-True ((Get-Calls 'apply').Count -eq 1) 'The initialised artifact could not be applied again.'

    # output collection failure
    Reset-Fixtures
    $global:OutputExit = 1
    $NoOutputs = Copy-Artifact
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $NoOutputs -Extra @{ Confirm = $false } } 'output collection failed' 'A failed output collection was ignored.'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $NoOutputs 'deployment-outputs.json'))) 'Failed output collection published a partial outputs file.'

    # --- apply: refusals ------------------------------------------------------------------------------------------
    Reset-Fixtures
    $Missing = New-ArtifactPath
    $null = New-Item -ItemType Directory -Path $Missing
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Missing } 'no manifest\.json' 'An artifact without a manifest was accepted.'
    $Garbage = Copy-Artifact
    [IO.File]::WriteAllText((Join-Path $Garbage 'manifest.json'), '{ not json')
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Garbage } 'manifest is unreadable' 'An unreadable manifest was accepted.'

    $Clean = Copy-Artifact
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Clean -SubscriptionId $OtherSub } 'does not match the requested subscription, tenant and Terraform version' 'A plan for another subscription was applied.'
    $global:AzTenant = $OtherTenant
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Clean -TenantId $OtherTenant } 'does not match the requested subscription, tenant and Terraform version' 'A plan for another tenant was applied.'
    $global:AzTenant = $Tenant
    $global:TfVersionJson = '{"terraform_version":"1.11.1"}'
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Clean } 'Terraform 1\.11\.2 is required' 'Apply ran under another terraform version.'
    $global:TfVersionJson = '{"terraform_version":"1.11.2"}'
    $global:AzTenant = $OtherTenant
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Clean } 'not the enabled deployment subscription and tenant' 'Apply ran under another signed-in tenant.'
    $global:AzTenant = $Tenant
    Assert-NoTerraformMutation 'A mismatched subscription, tenant, version or account reached init or apply.'

    $Manifests = @(
        @{ Why = 'an expired plan'; Pattern = 'has expired'; Edit = { param($m) $m.created_at = [DateTimeOffset]::UtcNow.AddHours(-5).ToString('o') } },
        @{ Why = 'a plan from the future'; Pattern = 'has expired'; Edit = { param($m) $m.created_at = [DateTimeOffset]::UtcNow.AddMinutes(10).ToString('o') } },
        @{ Why = 'a malformed timestamp'; Pattern = 'no valid creation time'; Edit = { param($m) $m.created_at = '2026-01-01' } },
        @{ Why = 'a missing timestamp'; Pattern = 'no valid creation time'; Edit = { param($m) $m.PSObject.Properties.Remove('created_at') } },
        @{ Why = 'a non-sha source commit'; Pattern = 'no valid source commit'; Edit = { param($m) $m.source_commit = 'main' } },
        @{ Why = 'a missing lock-file pin'; Pattern = 'missing its pinned provider lock file'; Edit = { param($m) $m.files.PSObject.Properties.Remove('infra/.terraform.lock.hcl') } },
        @{ Why = 'a path outside the bundle'; Pattern = 'out-of-bundle path'; Edit = { param($m) $m.files | Add-Member -NotePropertyName '../escape.txt' -NotePropertyValue ('A' * 64) } },
        @{ Why = 'a non-boolean source_dirty'; Pattern = 'does not record the source state'; Edit = { param($m) $m.source_dirty = 'false' } },
        @{ Why = 'a dirty source tree'; Pattern = 'uncommitted source tree'; Edit = { param($m) $m.source_dirty = $true } },
        @{ Why = 'a removed backend pin'; Pattern = 'unlisted file infra/environments/backend\.hcl'; Edit = { param($m) $m.files.PSObject.Properties.Remove('infra/environments/backend.hcl') } },
        @{ Why = 'a removed backend file'; Pattern = 'no state backend configuration'; Edit = { param($m, $dir)
                $m.files.PSObject.Properties.Remove('infra/environments/backend.hcl')
                Remove-Item -LiteralPath (Join-Path $dir 'infra\environments\backend.hcl') } })
    foreach ($Case in $Manifests) {
        Reset-Fixtures
        $Mutated = Copy-Artifact
        Edit-Manifest $Mutated $Case.Edit
        Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Mutated -Extra @{ Confirm = $false } } $Case.Pattern "Apply accepted $($Case.Why)."
        Assert-NoTerraformMutation "Apply reached init or apply with $($Case.Why)."
    }
    # a recent plan inside the four hour window is accepted
    Reset-Fixtures
    $Recent = Copy-Artifact
    Edit-Manifest $Recent { param($m) $m.created_at = [DateTimeOffset]::UtcNow.AddHours(-3).AddMinutes(-50).ToString('o') }
    Invoke-Wrapper -Action Apply -Directory $Recent -Extra @{ Confirm = $false }
    Assert-True ((Get-Calls 'apply').Count -eq 1) 'A plan inside the four hour window was refused.'
    # -AllowDirty lets a recorded dirty plan through
    Reset-Fixtures
    $DirtyApply = Copy-Artifact
    Edit-Manifest $DirtyApply { param($m) $m.source_dirty = $true }
    Invoke-Wrapper -Action Apply -Directory $DirtyApply -Extra @{ Confirm = $false; AllowDirty = $true }
    Assert-True ((Get-Calls 'apply').Count -eq 1) '-AllowDirty did not admit a recorded dirty plan.'

    # file tampering
    Reset-Fixtures
    $Tampered = Copy-Artifact
    Add-Content -LiteralPath (Join-Path $Tampered 'infra\main.tf') -Value '# modified fixture'
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Tampered } 'integrity check failed: infra/main\.tf' 'A tampered source file was applied.'
    $TamperedPlan = Copy-Artifact
    [IO.File]::WriteAllText((Join-Path $TamperedPlan 'demo.tfplan'), 'a different plan')
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $TamperedPlan } 'integrity check failed: demo\.tfplan' 'A swapped saved plan was applied.'
    $Deleted = Copy-Artifact
    Remove-Item -LiteralPath (Join-Path $Deleted 'operations\app.py')
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Deleted } 'integrity check failed: operations/app\.py' 'A deleted source file was applied.'
    $Extra = Copy-Artifact
    [IO.File]::WriteAllText((Join-Path $Extra 'infra\injected_override.tf'), 'resource "terraform_data" "x" {}')
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Extra } 'unlisted file infra/injected_override\.tf' 'An unlisted Terraform file was applied.'
    $ExtraTop = Copy-Artifact
    [IO.File]::WriteAllText((Join-Path $ExtraTop 'terraform.tfvars'), 'x = 1')
    Assert-Fails { Invoke-Wrapper -Action Apply -Directory $ExtraTop } 'unlisted file terraform\.tfvars' 'An unlisted variable file was applied.'
    Assert-NoTerraformMutation 'A tampered artifact reached init or apply.'

    # the backend file of the artifact is re-checked after its hash is re-pinned
    foreach ($Case in @(
            @{ Text = (Get-BackendText -BackendSub $OtherSub); Pattern = 'requested subscription_id'; Why = 'another subscription' },
            @{ Text = ($GoodBackend -replace 'use_azuread_auth\s*=\s*true', 'use_azuread_auth = false'); Pattern = 'Entra authentication'; Why = 'shared-key authentication' },
            @{ Text = (Get-BackendText -Extra 'access_key = "fixture-only"'); Pattern = 'must not carry credentials'; Why = 'an access key' })) {
        Reset-Fixtures
        $Repinned = Copy-Artifact
        Set-ArtifactFile $Repinned 'infra/environments/backend.hcl' $Case.Text
        Assert-Fails { Invoke-Wrapper -Action Apply -Directory $Repinned -Extra @{ Confirm = $false } } $Case.Pattern "A re-pinned backend with $($Case.Why) was applied."
        Assert-NoTerraformMutation "A re-pinned backend with $($Case.Why) reached init or apply."
    }

    Write-Output "PowerShell pipeline behavior checks passed ($script:assertions assertions) using fixture-only terraform, az and git commands."
}
finally {
    foreach ($Name in @('terraform', 'az', 'git')) { Remove-Item "Function:\$Name" -ErrorAction SilentlyContinue }
    Restore-Env $EnvSnapshot
    if (Test-Path -LiteralPath $TestDirectory) { Remove-Item -LiteralPath $TestDirectory -Recurse -Force }
}
