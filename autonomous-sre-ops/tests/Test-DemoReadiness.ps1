$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$RealScript = Join-Path $Repo 'scripts\Test-DemoReadiness.ps1'
$FixtureSub = '00000001-000a-4000-a000-000000000001'
$FixtureTenant = '00000002-000a-4000-9000-000000000002'
$OtherSub = '00000901-000a-4000-8000-000000000901'
$OtherTenant = '00000902-000a-4000-8000-000000000902'
$Work = Join-Path ([IO.Path]::GetTempPath()) "asre-readiness-fixture-$([guid]::NewGuid().ToString('N'))"
$Fake = Join-Path $Work 'repo'
$FakeScript = Join-Path $Fake 'scripts\Test-DemoReadiness.ps1'
$FakeInfra = Join-Path $Fake 'infra'
$null = New-Item -ItemType Directory -Path $Work
$global:AssertionCount = 0
$global:AzCalls = [System.Collections.Generic.List[string]]::new()
$global:TfCalls = [System.Collections.Generic.List[string]]::new()
$FeatureRegister = 'az feature register --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress'
$ProviderRegister = 'az provider register --namespace Microsoft.Network'

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    $global:AssertionCount++
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Assert-Fails {
    param([scriptblock]$Block, [string]$Pattern, [string]$Message)
    $global:AssertionCount++
    $failure = $null
    try { & $Block } catch { $failure = $_.Exception.Message }
    if ($null -eq $failure) { throw "ASSERTION FAILED: $Message (the operation succeeded)." }
    if ($failure -notmatch $Pattern) { throw "ASSERTION FAILED: $Message (unexpected failure: $failure)" }
}

# ---- static pins on the real script --------------------------------------------------------------------------------
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($RealScript, [ref]$tokens, [ref]$errors)
Assert-Test ($errors.Count -eq 0) 'The readiness script did not parse.'
$assignment = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -ceq '$ProviderNamespaces'
}, $true))
Assert-Test ($assignment.Count -eq 1) 'The provider namespace list is not defined exactly once.'
$Namespaces = @($assignment[0].Right.FindAll({ param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] }, $true) | ForEach-Object { $_.Value })
Assert-Test ($Namespaces.Count -ge 10 -and $Namespaces.Count -eq @($Namespaces | Select-Object -Unique).Count) 'The provider namespace list is empty or has duplicates.'
Assert-Test (@($Namespaces | Where-Object { $_ -cnotmatch '\AMicrosoft\.[A-Za-z]+\z' }).Count -eq 0) 'The provider namespace list contains a non-namespace entry.'
Assert-Test (@($Namespaces | Where-Object { $_ -cin @('Microsoft.Azure.Monitor', 'Microsoft.Quota') }).Count -eq 0) 'The provider namespace list contains an extension publisher or an unused namespace.'

$InfraText = @(Get-ChildItem -LiteralPath (Join-Path $Repo 'infra') -Filter '*.tf' -File | Sort-Object Name | ForEach-Object { [IO.File]::ReadAllText($_.FullName) }) -join "`n"
$TokenNamespaces = @([regex]::Matches($InfraText, '(?i)Microsoft\.([A-Za-z]+)(?=/)') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
Assert-Test ($TokenNamespaces.Count -gt 0) 'No Microsoft.<Namespace>/ token was found in infra/*.tf.'
$Missing = @($TokenNamespaces | Where-Object { $token = "Microsoft.$_"; -not (@($Namespaces | Where-Object { $_ -ieq $token }).Count) })
Assert-Test ($Missing.Count -eq 0) "infra/*.tf touches namespaces the readiness check does not register-check: $($Missing -join ', ')"

$NamespaceMap = [ordered]@{
    'network_interface|virtual_network|subnet|network_security|public_ip|nat_gateway|private_endpoint|private_dns|route_table' = 'Microsoft.Network'
    'linux_virtual_machine|windows_virtual_machine|managed_disk|virtual_machine_|availability_set' = 'Microsoft.Compute'
    'recovery_services|backup_' = 'Microsoft.RecoveryServices'
    'role_|policy_' = 'Microsoft.Authorization'
    'storage_' = 'Microsoft.Storage'
    'key_vault' = 'Microsoft.KeyVault'
    'resource_group|resource_provider_registration' = 'Microsoft.Resources'
    'user_assigned_identity' = 'Microsoft.ManagedIdentity'
    'log_analytics' = 'Microsoft.OperationalInsights'
    'application_insights|monitor_' = 'Microsoft.Insights'
    'container_registry' = 'Microsoft.ContainerRegistry'
    'container_app' = 'Microsoft.App'
}
$Unmapped = [System.Collections.Generic.List[string]]::new()
foreach ($match in [regex]::Matches($InfraText, '(?m)^\s*(?:resource|data)\s+"azurerm_(?<type>[a-z0-9_]+)"')) {
    $type = $match.Groups['type'].Value
    $namespace = $null
    foreach ($pattern in $NamespaceMap.Keys) {
        if ([regex]::IsMatch($type, "\A(?:$pattern)")) { $namespace = $NamespaceMap[$pattern]; break }
    }
    if ($null -eq $namespace) { $Unmapped.Add("azurerm_$type"); continue }
    if (-not (@($Namespaces | Where-Object { $_ -ceq $namespace }).Count)) { $Unmapped.Add("azurerm_$type -> $namespace (not registered by the check)") }
}
Assert-Test ($Unmapped.Count -eq 0) "azurerm resource types without a checked provider namespace: $($Unmapped -join ', ')"

$ScriptText = [IO.File]::ReadAllText($RealScript)
$AllowedReads = @('provider show', 'feature show', 'vm image', 'vm list-usage', 'security pricing', 'storage blob')
$Reads = @([regex]::Matches($ScriptText, "Invoke-AzRead -Arguments @\('(?<a>[a-z-]+)', '(?<b>[a-z-]+)'") | ForEach-Object { "$($_.Groups['a'].Value) $($_.Groups['b'].Value)" })
Assert-Test ($Reads.Count -ge 6 -and @($Reads | Where-Object { $_ -cnotin $AllowedReads }).Count -eq 0) 'The readiness script issues an Azure command outside its read-only allow-list.'
Assert-Test ([regex]::Matches($ScriptText, '&\s*az\b').Count -eq 1) 'The readiness script calls az outside its single read-only wrapper.'
Assert-Test ($ScriptText -match "'--subscription', \`$SubscriptionId|--subscription \`$SubscriptionId") 'The readiness script does not pass an explicit subscription.'
Assert-Test ($ScriptText -notmatch '(?i)-auto-approve|terraform[^\r\n]*\b(apply|destroy|import|taint)\b|Invoke-Expression|--account-key|--sas-token|--connection-string') 'The readiness script contains a mutating or credential-handling command.'
Assert-Test ($ScriptText -match 'if \(\$Failed\.Count\) \{ exit 1 \}') 'The readiness script does not exit 1 on a failed check.'

# ---- fixtures ---------------------------------------------------------------------------------------------------------
function Write-Text {
    param([string]$Relative, [string]$Text)
    $path = Join-Path $Fake $Relative
    $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($path)) -Force
    [IO.File]::WriteAllText($path, $Text, [Text.UTF8Encoding]::new($false))
}

function Reset-Repo {
    if (Test-Path -LiteralPath $Fake) { Remove-Item -LiteralPath $Fake -Recurse -Force }
    $null = New-Item -ItemType Directory -Path (Join-Path $Fake 'scripts') -Force
    Copy-Item -LiteralPath $RealScript -Destination $FakeScript
    Copy-Item -LiteralPath (Join-Path $Repo 'scripts\Context.Common.ps1') -Destination (Join-Path $Fake 'scripts\Context.Common.ps1')
    $null = New-Item -ItemType Directory -Path $FakeInfra -Force
    Copy-Item -LiteralPath (Join-Path $Repo 'infra\compute.tf') -Destination (Join-Path $FakeInfra 'compute.tf')
    foreach ($file in 'infra/versions.tf', 'infra/backend.tf', 'infra/.terraform.lock.hcl', 'infra/environments/backend.hcl.example',
        'infra/environments/demo.tfvars.example', 'schemas/asre-context.v1.schema.json',
        'workloads/linux-demo-service/guest_control.py', 'contracts/servicenow-case.schema.json') {
        Write-Text $file "# fixture`n"
    }
    Write-Text 'agent/response-plans/plans.json' '{"deployed":false,"plans":[{"name":"a","enabled":false},{"name":"b","enabled":false}]}'
    Write-Text 'agent/scheduled-tasks/tasks.json' '{"deployed":false,"tasks":[{"name":"t","enabled":false}]}'
}

function Reset-Azure {
    $global:FixtureSubscription = $FixtureSub
    $global:AccountSubscription = $FixtureSub
    $global:AccountTenant = $FixtureTenant
    $global:AccountFails = $false
    $global:ProviderStates = @{}
    $global:ProviderFails = @()
    $global:FeatureState = 'Registered'
    $global:FeatureFails = $false
    $global:ImageFails = $false
    $global:UsageFails = $false
    $global:CoresLimit = 100; $global:CoresUsed = 10
    $global:FamilyLimit = 100; $global:FamilyUsed = 10
    $global:UsageOmitFamily = $false
    $global:PricingFails = $false
    $global:PricingTier = 'Free'
    $global:BlobFails = $false
    $global:TfExit = @{ init = 0; fmt = 0; validate = 0 }
    $global:ValidateJson = '{"valid":true,"error_count":0}'
    $global:AzCalls.Clear()
    $global:TfCalls.Clear()
}

function global:terraform {
    $global:LASTEXITCODE = 0
    $global:TfCalls.Add(($args -join ' '))
    if ($args[0] -cne "-chdir=$global:FakeInfraPath") { throw 'The fixture permits terraform only against the fake infra directory.' }
    $subcommand = [string]$args[1]
    if (-not $global:TfExit.ContainsKey($subcommand)) { throw "The fixture refused terraform $subcommand." }
    $global:LASTEXITCODE = [int]$global:TfExit[$subcommand]
    if ($subcommand -ceq 'validate') { return $global:ValidateJson }
    if ($global:LASTEXITCODE -ne 0) { return "terraform $subcommand fixture failure" }
    return "terraform $subcommand fixture ok"
}

function global:az {
    $global:LASTEXITCODE = 0
    $global:AzCalls.Add(($args -join ' '))
    $subscriptionIndex = [Array]::IndexOf($args, '--subscription')
    if ($subscriptionIndex -lt 0 -or $args[$subscriptionIndex + 1] -cne $global:FixtureSubscription) {
        throw 'The fixture requires the explicit deployment subscription on every az call.'
    }
    if ($args -contains '--only-show-errors' -eq $false) { throw 'The fixture requires --only-show-errors.' }
    $verb = ($args[0..2] -join ' ')
    switch -Wildcard ($verb) {
        'account show*' {
            if ($global:AccountFails) { $global:LASTEXITCODE = 1; return 'ERROR: Please run az login' }
            return (@{ id = $global:AccountSubscription; tenantId = $global:AccountTenant; state = 'Enabled' } | ConvertTo-Json -Compress)
        }
        'provider show*' {
            $namespace = $args[[Array]::IndexOf($args, '--namespace') + 1]
            if ($global:ProviderFails -contains $namespace) { $global:LASTEXITCODE = 1; return 'ERROR: provider read failed' }
            if ($global:ProviderStates.ContainsKey($namespace)) { return $global:ProviderStates[$namespace] }
            return 'Registered'
        }
        'feature show*' {
            if ($global:FeatureFails) { $global:LASTEXITCODE = 1; return 'ERROR: feature not found' }
            return $global:FeatureState
        }
        'vm image show' {
            if ($global:ImageFails) { $global:LASTEXITCODE = 1; return 'ERROR: image not found' }
            return '{"id":"fixture"}'
        }
        'vm list-usage*' {
            if ($global:UsageFails) { $global:LASTEXITCODE = 1; return 'ERROR: usage read failed' }
            $entries = @(@{ name = @{ value = 'cores' }; limit = $global:CoresLimit; currentValue = $global:CoresUsed })
            if (-not $global:UsageOmitFamily) { $entries += @{ name = @{ value = 'standardDSv3Family' }; limit = $global:FamilyLimit; currentValue = $global:FamilyUsed } }
            $entries += @{ name = @{ value = 'availabilitySets' }; limit = 2500; currentValue = 0 }
            return (ConvertTo-Json -InputObject $entries -Depth 5 -Compress)
        }
        'security pricing show' {
            if ($global:PricingFails) { $global:LASTEXITCODE = 1; return 'ERROR: pricing read failed' }
            return $global:PricingTier
        }
        'storage blob list' {
            if ($global:BlobFails) { $global:LASTEXITCODE = 1; return 'ERROR: not authorized' }
            return '[]'
        }
        default { throw "The read-only fixture refused: $verb" }
    }
}
$global:FakeInfraPath = $FakeInfra

function Invoke-Readiness {
    param([string]$Phase = 'Source', [string]$SubscriptionId, [string]$TenantId)
    $global:AzCalls.Clear()
    $global:TfCalls.Clear()
    $global:LASTEXITCODE = 0
    $parameters = @{ Phase = $Phase }
    if ($PSBoundParameters.ContainsKey('SubscriptionId')) { $parameters.SubscriptionId = $SubscriptionId }
    if ($PSBoundParameters.ContainsKey('TenantId')) { $parameters.TenantId = $TenantId }
    $raw = @(& $FakeScript @parameters)
    $code = $global:LASTEXITCODE
    $text = $raw -join "`n"
    [pscustomobject]@{ Code = $code; Text = $text; Json = ($text | ConvertFrom-Json) }
}

function Get-Check {
    param($Result, [string]$Name)
    , @($Result.Json.checks | Where-Object { $_.name -ceq $Name })
}

# One comma-joined string, so that a comparison can never match a single element of a longer list.
function Get-FailedNames { param($Result) (@($Result.Json.checks | Where-Object { -not $_.passed } | ForEach-Object { $_.name }) -join ',') }

function Invoke-Preflight { Invoke-Readiness -Phase Preflight -SubscriptionId $FixtureSub -TenantId $FixtureTenant }
function Invoke-Delivery { Invoke-Readiness -Phase Delivery -SubscriptionId $FixtureSub -TenantId $FixtureTenant }

function Write-Backend {
    param([string]$Subscription = $FixtureSub, [string]$Tenant = $FixtureTenant, [string]$Account = 'stasrestate0123456789',
        [string]$Container = 'tfstate', [string]$Entra = 'true', [string]$NewLine = "`n", [string[]]$Extra = @(), [switch]$OmitEntra)
    $lines = @(
        'resource_group_name  = "rg-asre-state-cus"',
        "storage_account_name = `"$Account`"",
        "container_name       = `"$Container`"",
        'key                  = "autonomous-sre-ops/demo.tfstate"',
        "use_azuread_auth     = $Entra",
        "subscription_id      = `"$Subscription`"",
        "tenant_id            = `"$Tenant`""
    ) + $Extra
    if ($OmitEntra) { $lines = @($lines | Where-Object { $_ -notmatch '^use_azuread_auth' }) }
    Write-Text 'infra/environments/backend.hcl' (($lines -join $NewLine) + $NewLine)
}

try {
    # ---- Source phase ------------------------------------------------------------------------------------------------
    Reset-Repo
    Reset-Azure
    $result = Invoke-Readiness
    Assert-Test ($result.Code -eq 0 -and $result.Json.passed -eq $true -and $result.Json.phase -ceq 'Source') 'A clean source tree did not pass.'
    Assert-Test (($result.Json.PSObject.Properties.Name -join ',') -ceq 'phase,checked_at,passed,demo_ready,requires_saved_plan,faults_enabled,checks,warnings') 'The readiness result shape changed.'
    Assert-Test ($result.Json.demo_ready -eq $false -and $result.Json.requires_saved_plan -eq $true -and $result.Json.faults_enabled -eq $false) 'The readiness result claims the demo is ready, or lost its safety flags.'
    Assert-Test ($global:AzCalls.Count -eq 0) 'The Source phase reached Azure.'
    Assert-Test (($global:TfCalls -join '|') -ceq ("-chdir=$FakeInfra init -backend=false -input=false -lockfile=readonly|-chdir=$FakeInfra fmt -check -recursive|-chdir=$FakeInfra validate -json")) "The Source phase terraform sequence differs: $($global:TfCalls -join '|')"
    foreach ($name in 'terraform-init', 'terraform-format', 'terraform-schema', 'response-plans', 'scheduled-tasks', 'infra/backend.tf', 'infra/.terraform.lock.hcl',
        'infra/environments/backend.hcl.example', 'infra/environments/demo.tfvars.example', 'schemas/asre-context.v1.schema.json') {
        Assert-Test ((Get-Check $result $name).Count -eq 1) "The Source phase has no check named $name."
    }
    Assert-Test ((Get-Check $result 'network-feature').Count -eq 0 -and (Get-Check $result 'azure-context').Count -eq 0) 'The Source phase ran an Azure check.'

    foreach ($case in @(
            @{ Step = 'init'; Name = 'terraform-init' }, @{ Step = 'fmt'; Name = 'terraform-format' }, @{ Step = 'validate'; Name = 'terraform-schema' })) {
        Reset-Azure
        $global:TfExit[$case.Step] = 3
        $result = Invoke-Readiness
        Assert-Test ($result.Code -eq 1 -and $result.Json.passed -eq $false -and (Get-FailedNames $result) -ceq $case.Name) "A failing terraform $($case.Step) was not reported as $($case.Name)."
    }
    Reset-Azure
    $global:ValidateJson = '{"valid":false,"error_count":2}'
    $result = Invoke-Readiness
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'terraform-schema') 'An invalid schema validation result was accepted.'
    Reset-Azure
    Remove-Item -LiteralPath (Join-Path $Fake 'infra/environments/backend.hcl.example')
    Remove-Item -LiteralPath (Join-Path $Fake 'schemas/asre-context.v1.schema.json')
    $result = Invoke-Readiness
    Assert-Test ($result.Code -eq 1 -and ((Get-FailedNames $result) -join ',') -ceq 'infra/environments/backend.hcl.example,schemas/asre-context.v1.schema.json') 'Missing required files were not reported.'

    # The product configuration manifests are fail-closed.
    foreach ($case in @(
            @{ Name = 'an enabled plan'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":false,"plans":[{"name":"a","enabled":false},{"name":"b","enabled":true}]}' },
            @{ Name = 'a plan without enabled'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":false,"plans":[{"name":"a"}]}' },
            @{ Name = 'a plan with a string enabled'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":false,"plans":[{"name":"a","enabled":"false"}]}' },
            @{ Name = 'a plan with null enabled'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":false,"plans":[{"name":"a","enabled":null}]}' },
            @{ Name = 'a deployed plan manifest'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":true,"plans":[]}' },
            @{ Name = 'a plan manifest without deployed'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"plans":[]}' },
            @{ Name = 'a plan manifest with a string deployed'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":"false","plans":[]}' },
            @{ Name = 'a plan manifest without its list'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{"deployed":false}' },
            @{ Name = 'an unreadable plan manifest'; File = 'agent/response-plans/plans.json'; Check = 'response-plans'; Text = '{not json' },
            @{ Name = 'an enabled task'; File = 'agent/scheduled-tasks/tasks.json'; Check = 'scheduled-tasks'; Text = '{"deployed":false,"tasks":[{"name":"t","enabled":true}]}' },
            @{ Name = 'a task without enabled'; File = 'agent/scheduled-tasks/tasks.json'; Check = 'scheduled-tasks'; Text = '{"deployed":false,"tasks":[{"name":"t"}]}' },
            @{ Name = 'a deployed task manifest'; File = 'agent/scheduled-tasks/tasks.json'; Check = 'scheduled-tasks'; Text = '{"deployed":true,"tasks":[]}' },
            @{ Name = 'a task manifest without deployed'; File = 'agent/scheduled-tasks/tasks.json'; Check = 'scheduled-tasks'; Text = '{"tasks":[]}' })) {
        Reset-Repo
        Reset-Azure
        Write-Text $case.File $case.Text
        $result = Invoke-Readiness
        Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq $case.Check) "$($case.Name) was not refused."
    }
    foreach ($file in 'agent/response-plans/plans.json', 'agent/scheduled-tasks/tasks.json') {
        Reset-Repo
        Reset-Azure
        Remove-Item -LiteralPath (Join-Path $Fake $file)
        $result = Invoke-Readiness
        Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -cmatch '\A[^,]+\z') "A missing $file was not refused."
    }

    # ---- parameters ---------------------------------------------------------------------------------------------------
    Reset-Repo
    Reset-Azure
    $bad = @(
        @{ Name = 'Preflight without identity'; Splat = @{ Phase = 'Preflight' }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'Delivery without identity'; Splat = @{ Phase = 'Delivery' }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'Preflight without a tenant'; Splat = @{ Phase = 'Preflight'; SubscriptionId = $FixtureSub }; Pattern = 'TenantId must be a lowercase GUID' },
        @{ Name = 'an upper-case subscription'; Splat = @{ Phase = 'Preflight'; SubscriptionId = $FixtureSub.ToUpperInvariant(); TenantId = $FixtureTenant }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'a subscription with a trailing newline'; Splat = @{ Phase = 'Preflight'; SubscriptionId = "$FixtureSub`n"; TenantId = $FixtureTenant }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'a malformed tenant'; Splat = @{ Phase = 'Preflight'; SubscriptionId = $FixtureSub; TenantId = 'tenant' }; Pattern = 'TenantId must be a lowercase GUID' },
        @{ Name = 'Source with only a malformed subscription'; Splat = @{ Phase = 'Source'; SubscriptionId = 'x' }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'Source with only a subscription'; Splat = @{ Phase = 'Source'; SubscriptionId = $FixtureSub }; Pattern = 'TenantId must be a lowercase GUID' },
        @{ Name = 'an unknown phase'; Splat = @{ Phase = 'Apply' }; Pattern = 'Phase' })
    foreach ($case in $bad) {
        $global:AzCalls.Clear(); $global:TfCalls.Clear()
        $splat = $case.Splat
        Assert-Fails { $null = & $FakeScript @splat } $case.Pattern "$($case.Name) was accepted."
        Assert-Test ($global:AzCalls.Count -eq 0 -and $global:TfCalls.Count -eq 0) "$($case.Name) reached Azure or terraform."
    }

    # ---- Preflight ----------------------------------------------------------------------------------------------------
    Reset-Azure
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 0 -and $result.Json.passed -eq $true -and $result.Json.demo_ready -eq $false) 'A fully registered subscription did not pass Preflight.'
    Assert-Test ($global:AzCalls[0] -ceq "account show --subscription $FixtureSub --output json --only-show-errors") 'Preflight did not verify the Azure CLI account first.'
    foreach ($namespace in $Namespaces) {
        Assert-Test ((Get-Check $result "provider:$namespace").Count -eq 1 -and (Get-Check $result "provider:$namespace")[0].passed) "The provider $namespace was not checked."
        Assert-Test ($global:AzCalls -ccontains "provider show --namespace $namespace --query registrationState --output tsv --subscription $FixtureSub --only-show-errors") "The provider $namespace was not read with the exact read-only command."
    }
    Assert-Test (@($global:AzCalls | Where-Object { $_ -match '^provider show ' }).Count -eq $Namespaces.Count) 'An unexpected provider was read.'
    $allowedPrefix = '^(account show|provider show|feature show|vm image show|vm list-usage|security pricing show) '
    Assert-Test (@($global:AzCalls | Where-Object { $_ -cnotmatch $allowedPrefix }).Count -eq 0) "Preflight issued an unexpected Azure command: $(@($global:AzCalls | Where-Object { $_ -cnotmatch $allowedPrefix }) -join '; ')"
    Assert-Test (@($global:AzCalls | Where-Object { $_ -match '(?i)\b(register|create|delete|update|start|stop|assign|set)\b' }).Count -eq 0) 'Preflight issued a mutating Azure verb.'
    $RealCompute = [IO.File]::ReadAllText((Join-Path $Repo 'infra\compute.tf'))
    $urn = (@('publisher', 'offer', 'sku', 'version') | ForEach-Object { [regex]::Match($RealCompute, "source_image_reference\s*\{[^}]*?\b$_\s*=\s*`"([^`"]+)`"").Groups[1].Value }) -join ':'
    Assert-Test ($urn -cmatch '\A[^:]+:[^:]+:[^:]+:[0-9]+(\.[0-9]+)+\z') "The real infra/compute.tf does not pin an exact image: $urn"
    Assert-Test ($global:AzCalls -ccontains "vm image show --location centralus --urn $urn --output json --subscription $FixtureSub --only-show-errors") 'The pinned image of infra/compute.tf was not the one read.'
    Assert-Test ((Get-Check $result 'vm-image')[0].passed -and (Get-Check $result 'vm-quota')[0].passed -and (Get-Check $result 'network-feature')[0].passed) 'The image, quota or network feature check did not pass.'
    Assert-Test ($global:AzCalls -ccontains "vm list-usage --location centralus --output json --subscription $FixtureSub --only-show-errors") 'The regional usage was not read.'
    Assert-Test (@($result.Json.warnings | Where-Object { $_ -match '^Informational: Defender plan (VirtualMachines|StorageAccounts) tier is Free\.$' }).Count -eq 2) 'The Defender tiers were not reported as information.'
    Assert-Test (@($global:AzCalls | Where-Object { $_ -match '^storage ' }).Count -eq 0) 'Preflight read the state storage.'

    # A different Azure CLI context never reaches the read-only checks.
    Reset-Azure
    $global:AccountFails = $true
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'azure-context' -and $global:AzCalls.Count -eq 1) 'A missing Azure CLI login did not stop Preflight at azure-context.'
    Reset-Azure
    $global:AccountSubscription = $OtherSub
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'azure-context' -and $global:AzCalls.Count -eq 1) 'An Azure CLI session on another subscription was not refused.'
    Reset-Azure
    $global:AccountTenant = $OtherTenant
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'azure-context' -and $global:AzCalls.Count -eq 1) 'An Azure CLI session on another tenant was not refused.'
    Reset-Azure
    $result = Invoke-Readiness -Phase Preflight -SubscriptionId $OtherSub -TenantId $OtherTenant
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'azure-context') 'A requested subscription that is not the signed-in one was accepted.'

    # Resource providers.
    foreach ($state in 'NotRegistered', 'Registering', 'Unregistering', 'registered', '') {
        Reset-Azure
        $global:ProviderStates['Microsoft.Compute'] = $state
        $result = Invoke-Preflight
        Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'provider:Microsoft.Compute') "The provider state '$state' was not refused."
    }
    Reset-Azure
    $global:ProviderFails = @('Microsoft.Storage')
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'provider:Microsoft.Storage') 'An unreadable provider was accepted.'
    Reset-Azure
    $global:ProviderStates['Microsoft.App'] = 'NotRegistered'
    $global:ProviderStates['Microsoft.Network'] = 'NotRegistered'
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and ((Get-FailedNames $result) -join ',') -ceq 'provider:Microsoft.App,provider:Microsoft.Network') 'Several unregistered providers were not all reported.'

    # The Standard public IP feature (read-only; the script never registers anything).
    Reset-Azure
    $result = Invoke-Preflight
    $featureCall = "feature show --namespace Microsoft.Network --name AllowBringYourOwnPublicIpAddress --query properties.state --output tsv --subscription $FixtureSub --only-show-errors"
    Assert-Test (@($global:AzCalls | Where-Object { $_ -ceq $featureCall }).Count -eq 1) 'The Microsoft.Network public IP feature was not read exactly once with the read-only command.'
    $feature = Get-Check $result 'network-feature'
    Assert-Test ($feature.Count -eq 1 -and $feature[0].passed -eq $true -and $result.Code -eq 0) 'A registered public IP feature did not pass.'
    foreach ($state in 'NotRegistered', 'Pending', 'Unregistered', 'Registering', 'registered') {
        Reset-Azure
        $global:FeatureState = $state
        $result = Invoke-Preflight
        $feature = Get-Check $result 'network-feature'
        Assert-Test ($result.Code -eq 1 -and $feature.Count -eq 1 -and $feature[0].passed -eq $false -and (Get-FailedNames $result) -ceq 'network-feature') "The feature state '$state' was not refused."
        Assert-Test ($feature[0].detail.Contains($FeatureRegister) -and $feature[0].detail.Contains($ProviderRegister) -and
            $feature[0].detail.IndexOf($FeatureRegister) -lt $feature[0].detail.IndexOf($ProviderRegister)) "The refusal for '$state' does not tell the operator both registration commands in order."
        Assert-Test (@($global:AzCalls | Where-Object { $_ -match '\bregister\b' }).Count -eq 0) 'The script ran a registration instead of telling the operator.'
    }
    foreach ($unreadable in 'exit', 'empty') {
        Reset-Azure
        if ($unreadable -ceq 'exit') { $global:FeatureFails = $true } else { $global:FeatureState = '' }
        $result = Invoke-Preflight
        Assert-Test ($result.Code -eq 0 -and $result.Json.passed -eq $true -and (Get-Check $result 'network-feature').Count -eq 0) 'An unreadable feature failed Preflight.'
        Assert-Test (@($result.Json.warnings | Where-Object { $_ -match '^Informational: .*Microsoft\.Network.*AllowBringYourOwnPublicIpAddress.*could not be read' }).Count -eq 1) 'An unreadable feature was not reported as an informational warning.'
    }

    # The pinned image and the quota.
    Reset-Azure
    $global:ImageFails = $true
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'vm-image') 'A missing pinned image was accepted.'
    Reset-Repo
    Reset-Azure
    Write-Text 'infra/compute.tf' ($RealCompute -replace '(?m)^(\s*version\s*=\s*)"[0-9.]+"', '${1}"latest"')
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'vm-image' -and @($global:AzCalls | Where-Object { $_ -match '^vm image' }).Count -eq 0) 'An unpinned image version was accepted.'
    Write-Text 'infra/compute.tf' ($RealCompute -replace 'source_image_reference', 'source_image_id_reference')
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'vm-image') 'A missing image reference block was accepted.'
    Write-Text 'infra/compute.tf' ($RealCompute -replace '"Standard_D2s_v3"', '"Standard_B2s"')
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'vm-quota' -and @($global:AzCalls | Where-Object { $_ -match '^vm list-usage' }).Count -eq 0) 'A VM size outside the quota table was accepted.'
    Reset-Repo

    foreach ($case in @(
            @{ Name = 'six free cores and family vCPUs'; Cores = @(100, 94); Family = @(100, 94); Pass = $true },
            @{ Name = 'five free cores'; Cores = @(100, 95); Family = @(100, 0); Pass = $false },
            @{ Name = 'five free family vCPUs'; Cores = @(100, 0); Family = @(100, 95); Pass = $false },
            @{ Name = 'a fully used family'; Cores = @(100, 0); Family = @(10, 10); Pass = $false },
            @{ Name = 'a missing family counter'; Cores = @(100, 0); Family = @(100, 0); Omit = $true; Pass = $false })) {
        Reset-Azure
        $global:CoresLimit, $global:CoresUsed = $case.Cores
        $global:FamilyLimit, $global:FamilyUsed = $case.Family
        $global:UsageOmitFamily = [bool]($case.ContainsKey('Omit') -and $case.Omit)
        $result = Invoke-Preflight
        $quota = Get-Check $result 'vm-quota'
        Assert-Test ($quota.Count -eq 1 -and $quota[0].passed -eq $case.Pass -and $result.Code -eq $(if ($case.Pass) { 0 } else { 1 })) "Quota with $($case.Name) was judged wrongly."
    }
    Reset-Azure
    $global:UsageFails = $true
    $result = Invoke-Preflight
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'vm-quota') 'An unreadable quota was accepted.'

    # Defender tiers are information only.
    foreach ($case in @(@{ Tier = 'Standard'; Fails = $false }, @{ Tier = 'Free'; Fails = $true })) {
        Reset-Azure
        $global:PricingTier = $case.Tier
        $global:PricingFails = $case.Fails
        $result = Invoke-Preflight
        Assert-Test ($result.Json.passed -eq $true -and @($result.Json.checks | Where-Object { $_.name -match 'defender|pricing' }).Count -eq 0) 'A Defender plan became a check.'
        Assert-Test (@($result.Json.warnings | Where-Object { $_ -match '^Informational: Defender plan ' }).Count -eq 2) 'The Defender plans were not both reported as information.'
    }

    # ---- Delivery -----------------------------------------------------------------------------------------------------
    Reset-Repo
    Reset-Azure
    $result = Invoke-Delivery
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'state-backend-file' -and @($global:AzCalls | Where-Object { $_ -match '^storage ' }).Count -eq 0) 'A missing backend.hcl was not refused before any storage call.'
    foreach ($case in @(
            @{ Name = 'another subscription'; Args = @{ Subscription = $OtherSub } },
            @{ Name = 'another tenant'; Args = @{ Tenant = $OtherTenant } },
            @{ Name = 'shared-key authentication'; Args = @{ Entra = 'false' } },
            @{ Name = 'a missing Entra setting'; Args = @{ OmitEntra = $true } },
            @{ Name = 'an invalid storage account'; Args = @{ Account = 'StateAccount' } },
            @{ Name = 'an invalid container'; Args = @{ Container = '../tfstate' } })) {
        Reset-Repo
        Reset-Azure
        $arguments = $case.Args
        Write-Backend @arguments
        $result = Invoke-Delivery
        Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'state-backend-file' -and @($global:AzCalls | Where-Object { $_ -match '^storage ' }).Count -eq 0) "A backend.hcl with $($case.Name) was accepted."
    }
    foreach ($newLine in "`n", "`r`n") {
        Reset-Repo
        Reset-Azure
        Write-Backend -NewLine $newLine
        $result = Invoke-Delivery
        Assert-Test ($result.Code -eq 0 -and $result.Json.passed -eq $true -and $result.Json.demo_ready -eq $false) 'A valid Delivery run did not pass.'
        Assert-Test ((Get-Check $result 'state-backend-file')[0].passed -and (Get-Check $result 'private-state-access')[0].passed) 'The state checks did not pass.'
        $blobCalls = @($global:AzCalls | Where-Object { $_ -match '^storage ' })
        Assert-Test ($blobCalls.Count -eq 1 -and $blobCalls[0] -ceq "storage blob list --account-name stasrestate0123456789 --container-name tfstate --auth-mode login --num-results 1 --query [].name --output json --subscription $FixtureSub --only-show-errors") 'The state access check was not the Entra-authenticated, key-free blob list.'
        Assert-Test (@($global:AzCalls | Where-Object { $_ -match '(?i)--account-key|--sas-token|--connection-string|list-keys|generate-sas' }).Count -eq 0) 'The state access check used a credential.'
    }
    Reset-Repo
    Reset-Azure
    Write-Backend
    $global:BlobFails = $true
    $result = Invoke-Delivery
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'private-state-access') 'A failed state read was accepted.'
    Reset-Azure
    $global:ProviderStates['Microsoft.Compute'] = 'NotRegistered'
    $result = Invoke-Delivery
    Assert-Test ($result.Code -eq 1 -and (Get-FailedNames $result) -ceq 'provider:Microsoft.Compute') 'Delivery does not include every Preflight check.'

    Write-Output "Demo readiness fixtures passed ($($global:AssertionCount) assertions); no Azure or terraform command was executed."
}
finally {
    Remove-Item -LiteralPath 'Function:\az', 'Function:\terraform' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
