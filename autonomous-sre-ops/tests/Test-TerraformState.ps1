$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$StateScript = Join-Path $Repo 'scripts\Initialize-TerraformState.ps1'
$ExportScript = Join-Path $Repo 'scripts\Export-AsreContext.ps1'
$Sub = '00000001-000a-4000-a000-000000000001'
$Tenant = '00000002-000a-4000-9000-000000000002'
$OtherSub = '00000901-000a-4000-8000-000000000901'
$OtherTenant = '00000902-000a-4000-8000-000000000902'
$OperatorOid = '00000003-000a-4000-9000-000000000003'
$OperatorIp = '203.0.113.7'
$StaleIp = '198.51.100.9'
$RealSub = 'c0ffee00-1234-4abc-8def-0123456789ab'
$RealTenant = 'deadbeef-4321-4cba-9fed-ba9876543210'
$ResourceGroup = 'rg-asre-state-cus'
$Work = Join-Path ([IO.Path]::GetTempPath()) "asre-state-fixture-$([guid]::NewGuid().ToString('N'))"
$BackendPath = Join-Path $Work 'backend.hcl'
$null = New-Item -ItemType Directory -Path $Work
$global:AssertionCount = 0
$global:AllCalls = [System.Collections.Generic.List[string]]::new()
$global:TfCalls = [System.Collections.Generic.List[string]]::new()
$global:TfEnvironment = [System.Collections.Generic.List[hashtable]]::new()
$global:TfFail = $false
$global:TfOutput = ''
$SavedEnvironment = @{}
foreach ($name in 'ARM_USE_CLI', 'ARM_SUBSCRIPTION_ID', 'ARM_TENANT_ID', 'ARM_CLIENT_ID', 'ARM_CLIENT_SECRET', 'ARM_USE_OIDC', 'ARM_ACCESS_KEY', 'ARM_SAS_TOKEN', 'TF_WORKSPACE', 'ASRE_CONTEXT_FILE') {
    $SavedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}

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

function Get-DerivedSuffix {
    param([string]$SubscriptionId)
    $hash = [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes("${SubscriptionId}:asre-demo"))
    [Convert]::ToHexString($hash).ToLowerInvariant().Substring(0, 10)
}

$Suffix = Get-DerivedSuffix $Sub
Assert-Test ($Suffix -ceq '8a327010f9') 'The fixture subscription no longer derives the fixture suffix.'
$Account = "stasrestate$Suffix"
$AccountId = "/subscriptions/$Sub/resourceGroups/$ResourceGroup/providers/Microsoft.Storage/storageAccounts/$Account"

# ---- the stateful Azure CLI fixture -------------------------------------------------------------------------------------
function Reset-Fake {
    $global:Fake = @{
        Calls = [System.Collections.Generic.List[string]]::new()
        Mutations = [System.Collections.Generic.List[string]]::new()
        MutationCalls = [System.Collections.Generic.List[string]]::new()
        Sleeps = [System.Collections.Generic.List[int]]::new()
        PollBackendPresent = [System.Collections.Generic.List[bool]]::new()
        ExpectedSub = $Sub
        AccountSub = $Sub
        AccountTenant = $Tenant
        AccountFails = $false
        Oid = $OperatorOid
        OidBad = $false
        GroupExists = $false
        NameAvailable = $true
        Account = $null
        Service = $null
        Containers = [System.Collections.Generic.List[string]]::new()
        Roles = [System.Collections.Generic.List[string]]::new()
        PollFailures = 0
        PollCalls = 0
        FailVerb = ''
    }
}

function global:Start-Sleep {
    param([int]$Seconds)
    $global:Fake.Sleeps.Add($Seconds)
}

function global:az {
    $global:LASTEXITCODE = 0
    $fake = $global:Fake
    $all = @($args | ForEach-Object { [string]$_ })
    $text = $all -join ' '
    $fake.Calls.Add($text)
    $global:AllCalls.Add($text)
    $verb = [System.Collections.Generic.List[string]]::new()
    $options = @{}
    $index = 0
    while ($index -lt $all.Count -and -not $all[$index].StartsWith('--')) { $verb.Add($all[$index]); $index++ }
    while ($index -lt $all.Count) {
        $name = $all[$index]
        if (-not $name.StartsWith('--')) { throw "The fixture refused a stray argument: $name" }
        if ($index + 1 -lt $all.Count -and -not $all[$index + 1].StartsWith('--')) { $options[$name] = $all[$index + 1]; $index += 2 }
        else { $options[$name] = $null; $index++ }
    }
    if (-not $options.ContainsKey('--subscription') -or $options['--subscription'] -cne $fake.ExpectedSub) {
        throw "The fixture requires the explicit deployment subscription on every az call: $text"
    }
    if (-not $options.ContainsKey('--only-show-errors')) { throw "The fixture requires --only-show-errors: $text" }
    $verbText = $verb -join ' '
    if ($fake.FailVerb -ceq $verbText) { $global:LASTEXITCODE = 1; return 'ERROR: fixture failure' }
    $mutating = @('group create', 'storage account create', 'storage account update', 'storage account network-rule add',
        'storage account network-rule remove', 'storage account blob-service-properties update', 'storage container-rm create',
        'role assignment create')
    if ($mutating -ccontains $verbText) {
        if ($options['--output'] -cne 'none') { throw "The fixture requires --output none on a mutating call: $text" }
        $fake.Mutations.Add($verbText)
        $fake.MutationCalls.Add($text)
    }
    $accountName = "stasrestate$(Get-DerivedSuffix $fake.ExpectedSub)"
    $accountId = "/subscriptions/$($fake.ExpectedSub)/resourceGroups/rg-asre-state-cus/providers/Microsoft.Storage/storageAccounts/$accountName"
    $toBool = { param($value) $value -ceq 'true' }
    switch -CaseSensitive ($verbText) {
        'account show' {
            if ($fake.AccountFails) { $global:LASTEXITCODE = 1; return 'ERROR: Please run az login' }
            return (@{ id = $fake.AccountSub; tenantId = $fake.AccountTenant; state = 'Enabled' } | ConvertTo-Json -Compress)
        }
        'ad signed-in-user show' {
            if ($options['--query'] -cne 'id') { throw "The fixture expected --query id: $text" }
            if ($fake.OidBad) { return '"not-a-guid"' }
            return ('"' + $fake.Oid + '"')
        }
        'group exists' {
            if ($options['--name'] -cne 'rg-asre-state-cus') { throw "The fixture expected the state resource group: $text" }
            return $(if ($fake.GroupExists) { 'true' } else { 'false' })
        }
        'group create' {
            if ($options['--name'] -cne 'rg-asre-state-cus' -or $options['--location'] -cne 'centralus') { throw "The fixture expected the state group in centralus: $text" }
            $fake.GroupExists = $true
        }
        'storage account list' {
            if ($options['--resource-group'] -cne 'rg-asre-state-cus' -or $options['--query'] -cne "[?name=='$accountName'].name") { throw "The fixture refused the account listing: $text" }
            if ($null -ne $fake.Account) { return (ConvertTo-Json -InputObject @($accountName) -Compress) }
            return '[]'
        }
        'storage account check-name' {
            if ($options['--name'] -cne $accountName) { throw "The fixture expected the derived account name: $text" }
            return (@{ nameAvailable = $fake.NameAvailable } | ConvertTo-Json -Compress)
        }
        'storage account create' {
            if (-not $fake.GroupExists -or $null -ne $fake.Account) { throw "The fixture refused creating an account in this state: $text" }
            if ($options['--name'] -cne $accountName -or $options['--resource-group'] -cne 'rg-asre-state-cus' -or $options['--location'] -cne 'centralus') { throw "The fixture refused the account identity: $text" }
            $fake.Account = @{
                Id = $accountId
                PublicBlob = $(if ($options.ContainsKey('--allow-blob-public-access')) { & $toBool $options['--allow-blob-public-access'] } else { $true })
                SharedKey = $(if ($options.ContainsKey('--allow-shared-key-access')) { & $toBool $options['--allow-shared-key-access'] } else { $true })
                Tls = $(if ($options.ContainsKey('--min-tls-version')) { $options['--min-tls-version'] } else { 'TLS1_0' })
                Https = $(if ($options.ContainsKey('--https-only')) { & $toBool $options['--https-only'] } else { $true })
                Default = $(if ($options.ContainsKey('--default-action')) { $options['--default-action'] } else { 'Allow' })
                Bypass = $(if ($options.ContainsKey('--bypass')) { $options['--bypass'] } else { 'AzureServices' })
                Pna = 'Enabled'
                Ips = [System.Collections.Generic.List[string]]::new()
            }
            $fake.Service = @{ Versioning = $false; DeleteEnabled = $false; DeleteDays = $null; ContainerEnabled = $false; ContainerDays = $null }
        }
        'storage account show' {
            if ($options['--name'] -cne $accountName -or $options['--resource-group'] -cne 'rg-asre-state-cus') { throw "The fixture refused the account show: $text" }
            if ($null -eq $fake.Account) { $global:LASTEXITCODE = 1; return 'ERROR: ResourceNotFound' }
            $a = $fake.Account
            $rules = @($a.Ips | ForEach-Object { @{ ipAddressOrRange = $_; action = 'Allow' } })
            return (ConvertTo-Json -Compress -Depth 6 -InputObject @{
                    id = $a.Id; allowBlobPublicAccess = $a.PublicBlob; allowSharedKeyAccess = $a.SharedKey; minimumTlsVersion = $a.Tls
                    enableHttpsTrafficOnly = $a.Https; publicNetworkAccess = $a.Pna
                    networkRuleSet = @{ defaultAction = $a.Default; bypass = $a.Bypass; ipRules = $rules }
                })
        }
        'storage account update' {
            if ($null -eq $fake.Account) { throw "The fixture refused updating a missing account: $text" }
            $a = $fake.Account
            if ($options.ContainsKey('--allow-blob-public-access')) { $a.PublicBlob = & $toBool $options['--allow-blob-public-access'] }
            if ($options.ContainsKey('--allow-shared-key-access')) { $a.SharedKey = & $toBool $options['--allow-shared-key-access'] }
            if ($options.ContainsKey('--min-tls-version')) { $a.Tls = $options['--min-tls-version'] }
            if ($options.ContainsKey('--https-only')) { $a.Https = & $toBool $options['--https-only'] }
            if ($options.ContainsKey('--default-action')) { $a.Default = $options['--default-action'] }
            if ($options.ContainsKey('--bypass')) { $a.Bypass = $options['--bypass'] }
            if ($options.ContainsKey('--public-network-access')) { $a.Pna = $options['--public-network-access'] }
        }
        'storage account network-rule add' {
            if ($options['--account-name'] -cne $accountName -or $options['--resource-group'] -cne 'rg-asre-state-cus') { throw "The fixture refused the rule target: $text" }
            if (-not $fake.Account.Ips.Contains($options['--ip-address'])) { $fake.Account.Ips.Add($options['--ip-address']) }
        }
        'storage account network-rule remove' {
            if ($options['--account-name'] -cne $accountName -or $options['--resource-group'] -cne 'rg-asre-state-cus') { throw "The fixture refused the rule target: $text" }
            $null = $fake.Account.Ips.Remove($options['--ip-address'])
        }
        'storage account blob-service-properties show' {
            if ($options['--account-name'] -cne $accountName -or $options['--resource-group'] -cne 'rg-asre-state-cus') { throw "The fixture refused the blob service target: $text" }
            $s = $fake.Service
            return (ConvertTo-Json -Compress -Depth 6 -InputObject @{
                    isVersioningEnabled = $s.Versioning
                    deleteRetentionPolicy = @{ enabled = $s.DeleteEnabled; days = $s.DeleteDays }
                    containerDeleteRetentionPolicy = @{ enabled = $s.ContainerEnabled; days = $s.ContainerDays }
                })
        }
        'storage account blob-service-properties update' {
            $s = $fake.Service
            if ($options.ContainsKey('--enable-versioning')) { $s.Versioning = & $toBool $options['--enable-versioning'] }
            if ($options.ContainsKey('--enable-delete-retention')) { $s.DeleteEnabled = & $toBool $options['--enable-delete-retention'] }
            if ($options.ContainsKey('--delete-retention-days')) { $s.DeleteDays = [int]$options['--delete-retention-days'] }
            if ($options.ContainsKey('--enable-container-delete-retention')) { $s.ContainerEnabled = & $toBool $options['--enable-container-delete-retention'] }
            if ($options.ContainsKey('--container-delete-retention-days')) { $s.ContainerDays = [int]$options['--container-delete-retention-days'] }
        }
        'storage container-rm list' {
            if ($options['--storage-account'] -cne $accountName -or $options['--resource-group'] -cne 'rg-asre-state-cus' -or $options['--query'] -cne "[?name=='tfstate'].name") { throw "The fixture refused the container listing: $text" }
            return (ConvertTo-Json -InputObject @($fake.Containers | Where-Object { $_ -ceq 'tfstate' }) -Compress)
        }
        'storage container-rm create' {
            if ($options['--storage-account'] -cne $accountName -or $options['--name'] -cne 'tfstate' -or $options['--public-access'] -cne 'none') { throw "The fixture refused the container: $text" }
            $fake.Containers.Add($options['--name'])
        }
        'role assignment list' {
            $expectedQuery = "[?principalId=='$($fake.Oid)'&&roleDefinitionName=='Storage Blob Data Contributor'].id"
            if ($options['--scope'] -cne $accountId -or $options['--query'] -cne $expectedQuery -or -not $options.ContainsKey('--include-inherited')) { throw "The fixture refused the role listing: $text" }
            $found = @($fake.Roles | Where-Object { $_ -ceq "$($fake.Oid)|Storage Blob Data Contributor|User|$accountId" })
            return (ConvertTo-Json -InputObject @($found | ForEach-Object { "$accountId/providers/Microsoft.Authorization/roleAssignments/fixture" }) -Compress)
        }
        'role assignment create' {
            $fake.Roles.Add("$($options['--assignee-object-id'])|$($options['--role'])|$($options['--assignee-principal-type'])|$($options['--scope'])")
        }
        'storage blob list' {
            if ($options['--auth-mode'] -cne 'login' -or $options['--container-name'] -cne 'tfstate' -or $options['--account-name'] -cne $accountName -or $options['--num-results'] -cne '1') { throw "The fixture refused the data-plane probe: $text" }
            $fake.PollCalls++
            $fake.PollBackendPresent.Add([IO.File]::Exists($BackendPath))
            if ($fake.PollFailures -gt 0) { $fake.PollFailures--; $global:LASTEXITCODE = 1; return 'ERROR: AuthorizationPermissionMismatch' }
        }
        default { throw "The fixture refused: $verbText" }
    }
}

function Set-Compliant {
    $global:Fake.GroupExists = $true
    $global:Fake.Account = @{
        Id = $AccountId; PublicBlob = $false; SharedKey = $false; Tls = 'TLS1_2'; Https = $true; Default = 'Deny'; Bypass = 'None'; Pna = 'Enabled'
        Ips = [System.Collections.Generic.List[string]]::new()
    }
    $global:Fake.Account.Ips.Add($OperatorIp)
    $global:Fake.Service = @{ Versioning = $true; DeleteEnabled = $true; DeleteDays = 14; ContainerEnabled = $true; ContainerDays = 14 }
    $global:Fake.Containers.Add('tfstate')
    $global:Fake.Roles.Add("$OperatorOid|Storage Blob Data Contributor|User|$AccountId")
}

function Invoke-State {
    param([hashtable]$Extra = @{}, [switch]$WhatIf, [string]$Backend = $BackendPath, [string]$Ip = $OperatorIp)
    $fake = $global:Fake
    $fake.Calls.Clear(); $fake.Mutations.Clear(); $fake.MutationCalls.Clear(); $fake.Sleeps.Clear(); $fake.PollBackendPresent.Clear()
    $fake.PollCalls = 0
    $parameters = @{ SubscriptionId = $Sub; TenantId = $Tenant; OperatorIp = $Ip; BackendFile = $Backend }
    foreach ($key in $Extra.Keys) { $parameters[$key] = $Extra[$key] }
    if ($WhatIf) { $parameters.WhatIf = $true }
    $global:LASTEXITCODE = 0
    @(& $StateScript @parameters) | ForEach-Object { [string]$_ }
}

function Get-Mutations { $global:Fake.Mutations -join '|' }
function Test-Flag {
    param([string]$Call, [string]$Name, [string]$Value)
    [regex]::IsMatch($Call, "(?:\A| )$([regex]::Escape($Name)) $([regex]::Escape($Value))(?= |\z)")
}
function Get-BackendBytes { if ([IO.File]::Exists($BackendPath)) { , [IO.File]::ReadAllBytes($BackendPath) } else { $null } }
function Remove-Backend { if ([IO.File]::Exists($BackendPath)) { Remove-Item -LiteralPath $BackendPath -Force } }

$ExpectedBackend = (@(
        "resource_group_name  = `"$ResourceGroup`"",
        "storage_account_name = `"$Account`"",
        'container_name       = "tfstate"',
        'key                  = "autonomous-sre-ops/demo.tfstate"',
        'use_azuread_auth     = true',
        "subscription_id      = `"$Sub`"",
        "tenant_id            = `"$Tenant`""
    ) -join "`n") + "`n"

# ---- static pins ------------------------------------------------------------------------------------------------------
$StateText = [IO.File]::ReadAllText($StateScript)
Assert-Test ($StateText -notmatch '(?i)list-keys|--account-key|--sas-token|generate-sas|connection-string|az login|--auth-mode key|Invoke-Expression|storage account delete|group delete') 'The state script contains a credential-handling or destructive command.'
Assert-Test ($StateText -cnotmatch '\bterraform\b') 'The state script runs terraform.'
Assert-Test ($StateText -match 'SupportsShouldProcess' -and $StateText -match '\$PSCmdlet\.ShouldProcess') 'The state script does not gate writes behind ShouldProcess.'
Assert-Test ($StateText -match '\$PollAttempts = 24' -and $StateText -match '\$PollSeconds = 10') 'The data-plane wait is no longer bounded at 24 x 10 seconds.'
Assert-Test ($StateText -match "Join-Path \`$Repo 'infra\\environments\\backend\.hcl'") 'The default backend file is not infra\environments\backend.hcl.'
Assert-Test ([regex]::Matches($StateText, '&\s*az\b').Count -eq 3) 'The state script calls az outside the read, write and probe wrappers.'
$ExportText = [IO.File]::ReadAllText($ExportScript)
Assert-Test ($ExportText -match "Join-Path \`$Repo 'infra\\environments\\context\.generated\.json'") 'The default context file is not infra\environments\context.generated.json.'
Assert-Test ($ExportText -cnotmatch 'AllowSynthetic|Invoke-Expression|terraform[^\r\n]*\b(apply|destroy|import)\b|az login') 'The export script allows synthetic contexts or runs a mutating command.'

try {
    # ---- parameter validation -----------------------------------------------------------------------------------------
    Reset-Fake
    $badParameters = @(
        @{ Name = 'an upper-case subscription'; Splat = @{ SubscriptionId = $Sub.ToUpperInvariant() }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'a malformed subscription'; Splat = @{ SubscriptionId = 'subscription' }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'a subscription with a trailing newline'; Splat = @{ SubscriptionId = "$Sub`n" }; Pattern = 'SubscriptionId must be a lowercase GUID' },
        @{ Name = 'a malformed tenant'; Splat = @{ TenantId = 'tenant' }; Pattern = 'TenantId must be a lowercase GUID' },
        @{ Name = 'a malformed operator object id'; Splat = @{ OperatorObjectId = 'operator' }; Pattern = 'OperatorObjectId must be a lowercase GUID' },
        @{ Name = 'a backend file that is not .hcl'; Splat = @{ BackendFile = (Join-Path $Work 'backend.txt') }; Pattern = 'BackendFile must be an \.hcl file' },
        @{ Name = 'a backend directory that does not exist'; Splat = @{ BackendFile = (Join-Path $Work 'missing-directory\backend.hcl') }; Pattern = 'BackendFile directory does not exist' })
    foreach ($case in $badParameters) {
        $parameters = @{ SubscriptionId = $Sub; TenantId = $Tenant; OperatorIp = $OperatorIp; BackendFile = $BackendPath }
        foreach ($key in $case.Splat.Keys) { $parameters[$key] = $case.Splat[$key] }
        $global:AllCalls.Clear()
        Assert-Fails { $null = & $StateScript @parameters } $case.Pattern "$($case.Name) was accepted."
        Assert-Test ($global:AllCalls.Count -eq 0) "$($case.Name) reached Azure."
    }
    # ---- operator address validation -----------------------------------------------------------------------------------
    $rejected = @('10.1.2.3', '127.0.0.1', '169.254.169.254', '172.16.0.1', '172.31.255.254', '192.168.0.1', '100.64.0.1', '100.127.255.255',
        '224.0.0.1', '239.255.255.255', '255.255.255.255', '0.0.0.0', '198.18.0.1', '198.19.255.255', '192.0.0.8', '203.0.113.7/32', '203.0.113.0/24',
        '203.0.113.07', '01.2.3.4', '1.2.3', '1.2.3.4.5', '256.1.1.1', '203.0.113.7 ', ' 203.0.113.7', "203.0.113.7`n", '::1', '2001:db8::1', 'abc', '')
    foreach ($address in $rejected) {
        Reset-Fake
        $global:AllCalls.Clear()
        Assert-Fails { $null = Invoke-State -Ip $address } 'OperatorIp' "The operator address '$address' was accepted."
        Assert-Test ($global:AllCalls.Count -eq 0) "The operator address '$address' reached Azure."
    }
    foreach ($address in '203.0.113.7', '8.8.8.8', '1.1.1.1', '172.15.255.255', '172.32.0.1', '100.63.255.255', '100.128.0.1', '198.17.255.255', '198.20.0.1', '192.0.1.1', '11.0.0.1', '223.255.255.255') {
        Reset-Fake
        $null = Invoke-State -Ip $address -WhatIf
        Assert-Test ($global:Fake.Mutations.Count -eq 0) "The public address '$address' was refused or caused a write."
    }

    # ---- first run on an empty subscription ------------------------------------------------------------------------------
    Reset-Fake
    Remove-Backend
    $output = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'group create|storage account create|storage account network-rule add|storage account blob-service-properties update|storage container-rm create|role assignment create') "The first run did not create the state resources in order: $(Get-Mutations)"
    $create = @($global:Fake.MutationCalls | Where-Object { $_ -match '^storage account create ' })[0]
    foreach ($flag in @(@('--sku', 'Standard_LRS'), @('--kind', 'StorageV2'), @('--location', 'centralus'), @('--resource-group', $ResourceGroup), @('--name', $Account),
            @('--allow-blob-public-access', 'false'), @('--allow-shared-key-access', 'false'), @('--min-tls-version', 'TLS1_2'),
            @('--https-only', 'true'), @('--default-action', 'Deny'), @('--bypass', 'None'))) {
        Assert-Test (Test-Flag $create $flag[0] $flag[1]) "The account was not created with $($flag[0]) $($flag[1])."
    }
    $a = $global:Fake.Account
    Assert-Test ($a.Id -ceq $AccountId -and -not $a.PublicBlob -and -not $a.SharedKey -and $a.Tls -ceq 'TLS1_2' -and $a.Https -and $a.Default -ceq 'Deny' -and $a.Bypass -ceq 'None') 'The created account is not private, Entra-only and TLS 1.2.'
    Assert-Test ($a.Ips.Count -eq 1 -and $a.Ips[0] -ceq $OperatorIp) 'The account does not allow exactly the operator address.'
    $s = $global:Fake.Service
    Assert-Test ($s.Versioning -and $s.DeleteEnabled -and $s.DeleteDays -eq 14 -and $s.ContainerEnabled -and $s.ContainerDays -eq 14) 'Versioning or 14-day soft delete is not enabled.'
    Assert-Test ($global:Fake.Containers.Count -eq 1 -and $global:Fake.Containers[0] -ceq 'tfstate') 'The tfstate container was not created.'
    Assert-Test ($global:Fake.Roles.Count -eq 1 -and $global:Fake.Roles[0] -ceq "$OperatorOid|Storage Blob Data Contributor|User|$AccountId") 'The operator did not receive Storage Blob Data Contributor on the account.'
    Assert-Test (@($global:Fake.Calls | Where-Object { $_ -cmatch '^ad signed-in-user show ' }).Count -eq 1) 'The signed-in operator was not resolved exactly once.'
    Assert-Test ($global:Fake.Calls[0] -ceq "account show --subscription $Sub --output json --only-show-errors") 'The first call was not the account verification.'
    Assert-Test ($global:Fake.PollCalls -eq 1 -and $global:Fake.Sleeps.Count -eq 0 -and $global:Fake.PollBackendPresent[0] -eq $false) 'The data-plane wait did not succeed on the first probe before the file was written.'
    $bytes = Get-BackendBytes
    Assert-Test ($null -ne $bytes -and -not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) 'backend.hcl was not written, or has a byte order mark.'
    $content = [IO.File]::ReadAllText($BackendPath)
    Assert-Test ($content -ceq $ExpectedBackend) 'backend.hcl content differs from the seven-line Entra-only backend.'
    Assert-Test ($content -notmatch "`r" -and $content.EndsWith("`n") -and -not $content.EndsWith("`n`n")) 'backend.hcl does not use LF line endings with one trailing newline.'
    Assert-Test (@(Get-ChildItem -LiteralPath $Work -Force | Where-Object { $_.Name -match '\.tmp-' }).Count -eq 0) 'A temporary backend file was left behind.'
    Assert-Test (($output -join "`n") -match 'Backend file written' -and ($output -join "`n") -match [regex]::Escape($OperatorOid)) 'The summary does not report the backend file and the operator object id.'
    Assert-Test (($output -join "`n") -notmatch '(?i)key1|key2|sas|connection') 'The summary mentions a credential.'
    Assert-Test ((@($global:Fake.Calls | Where-Object { $_ -match '^(storage account (create|update)|group create)' }).Count) -eq 2) 'Unexpected account or group writes on the first run.'

    # ---- idempotent second run ---------------------------------------------------------------------------------------------
    $output = Invoke-State
    Assert-Test ($global:Fake.Mutations.Count -eq 0) "The second run mutated Azure: $(Get-Mutations)"
    Assert-Test ([IO.File]::ReadAllText($BackendPath) -ceq $ExpectedBackend) 'The second run changed backend.hcl.'
    Assert-Test ($global:Fake.Roles.Count -eq 1 -and $global:Fake.Containers.Count -eq 1 -and $global:Fake.Account.Ips.Count -eq 1) 'The second run duplicated a role, container or rule.'
    $output = Invoke-State -Extra @{ OperatorObjectId = $OperatorOid }
    Assert-Test ($global:Fake.Mutations.Count -eq 0 -and @($global:Fake.Calls | Where-Object { $_ -match '^ad ' }).Count -eq 0) 'An explicit operator object id still queried the directory, or mutated Azure.'

    # ---- -WhatIf ------------------------------------------------------------------------------------------------------------
    Reset-Fake
    Remove-Backend
    $output = Invoke-State -WhatIf
    Assert-Test ($global:Fake.Mutations.Count -eq 0) "-WhatIf mutated Azure: $(Get-Mutations)"
    Assert-Test ($global:Fake.PollCalls -eq 0 -and $global:Fake.Sleeps.Count -eq 0) '-WhatIf polled the data plane.'
    Assert-Test (-not [IO.File]::Exists($BackendPath) -and $null -eq $global:Fake.Account) '-WhatIf wrote backend.hcl or created an account.'
    Assert-Test (($output -join "`n") -match 'What if: the backend file') '-WhatIf did not say what it would write.'
    Assert-Test (@($global:Fake.Calls | Where-Object { $_ -match '^storage account create|^group create|^role assignment create|^storage container-rm create' }).Count -eq 0) '-WhatIf issued a creating command.'
    # -WhatIf on a drifted account and an existing backend file leaves both alone.
    Reset-Fake
    Set-Compliant
    $global:Fake.Account.SharedKey = $true
    $global:Fake.Account.Default = 'Allow'
    $global:Fake.Account.Ips.Clear(); $global:Fake.Account.Ips.Add($StaleIp)
    $global:Fake.Service.Versioning = $false
    $global:Fake.Containers.Clear(); $global:Fake.Roles.Clear()
    [IO.File]::WriteAllText($BackendPath, "existing backend`n", [Text.UTF8Encoding]::new($false))
    $output = Invoke-State -WhatIf
    Assert-Test ($global:Fake.Mutations.Count -eq 0 -and [IO.File]::ReadAllText($BackendPath) -ceq "existing backend`n") '-WhatIf changed Azure or an existing backend.hcl.'
    Assert-Test ($global:Fake.Account.SharedKey -and $global:Fake.Account.Ips[0] -ceq $StaleIp) '-WhatIf corrected the account.'

    # ---- drift correction (never recreate or delete) -----------------------------------------------------------------------
    Reset-Fake
    Set-Compliant
    $a = $global:Fake.Account
    $a.PublicBlob = $true; $a.SharedKey = $true; $a.Tls = 'TLS1_0'; $a.Https = $false; $a.Default = 'Allow'; $a.Bypass = 'AzureServices'; $a.Pna = 'Disabled'
    $a.Ips.Clear(); $a.Ips.Add($StaleIp); $a.Ips.Add('198.51.100.10')
    $global:Fake.Service = @{ Versioning = $false; DeleteEnabled = $true; DeleteDays = 3; ContainerEnabled = $false; ContainerDays = $null }
    $global:Fake.Containers.Clear(); $global:Fake.Roles.Clear()
    $output = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'storage account update|storage account network-rule remove|storage account network-rule remove|storage account network-rule add|storage account blob-service-properties update|storage container-rm create|role assignment create') "Drift was not corrected in place: $(Get-Mutations)"
    $a = $global:Fake.Account
    Assert-Test (-not $a.PublicBlob -and -not $a.SharedKey -and $a.Tls -ceq 'TLS1_2' -and $a.Https -and $a.Default -ceq 'Deny' -and $a.Bypass -ceq 'None' -and $a.Pna -ceq 'Enabled') 'The account drift was not corrected.'
    Assert-Test ($a.Ips.Count -eq 1 -and $a.Ips[0] -ceq $OperatorIp) 'Stale network rules were not replaced by the operator address.'
    $s = $global:Fake.Service
    Assert-Test ($s.Versioning -and $s.DeleteEnabled -and $s.DeleteDays -eq 14 -and $s.ContainerEnabled -and $s.ContainerDays -eq 14) 'The blob service drift was not corrected.'
    Assert-Test (@($global:Fake.Calls | Where-Object { (($_ -split ' ')[0..2] -join ' ') -match '^(storage account create|group create)|\bdelete\b' }).Count -eq 0) 'Drift correction created or deleted a resource.'
    Assert-Test ([IO.File]::ReadAllText($BackendPath) -ceq $ExpectedBackend) 'backend.hcl was not rewritten after drift correction.'
    # The operator address is kept when it is already allowed.
    Reset-Fake
    Set-Compliant
    $global:Fake.Account.Ips.Add($StaleIp)
    $null = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'storage account network-rule remove' -and $global:Fake.Account.Ips.Count -eq 1 -and $global:Fake.Account.Ips[0] -ceq $OperatorIp) 'Only the stale rule should have been removed.'
    # Partial existing state: group and account exist, the container and role are missing.
    Reset-Fake
    Set-Compliant
    $global:Fake.Containers.Clear()
    $null = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'storage container-rm create') 'A missing container alone was not the only creation.'
    Reset-Fake
    Set-Compliant
    $global:Fake.Roles.Clear()
    $null = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'role assignment create') 'A missing role assignment alone was not the only creation.'
    Reset-Fake
    $global:Fake.GroupExists = $true
    $null = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'storage account create|storage account network-rule add|storage account blob-service-properties update|storage container-rm create|role assignment create') 'An existing resource group was recreated or the account was not created.'
    # A different role (not Storage Blob Data Contributor) does not count.
    Reset-Fake
    Set-Compliant
    $global:Fake.Roles.Clear(); $global:Fake.Roles.Add("$OperatorOid|Reader|User|$AccountId")
    $null = Invoke-State
    Assert-Test ((Get-Mutations) -ceq 'role assignment create') 'A different role satisfied the data-plane role.'

    # ---- refusals -----------------------------------------------------------------------------------------------------------
    Reset-Fake
    $global:Fake.NameAvailable = $false
    Remove-Backend
    Assert-Fails { $null = Invoke-State } 'not available to this subscription' 'An unavailable derived account name was accepted.'
    Assert-Test ($global:Fake.Mutations -ccontains 'group create' -and -not ($global:Fake.Mutations -ccontains 'storage account create') -and -not [IO.File]::Exists($BackendPath)) 'An unavailable account name still created the account or wrote the file.'
    Reset-Fake
    Set-Compliant
    $global:Fake.Account.Id = '/subscriptions/x/resourceGroups/rg-other/providers/Microsoft.Storage/storageAccounts/other'
    Remove-Backend
    Assert-Fails { $null = Invoke-State } 'not the expected resource' 'A different account behind the derived name was accepted.'
    Assert-Test ($global:Fake.Mutations.Count -eq 0 -and -not [IO.File]::Exists($BackendPath)) 'An unexpected account was modified or a file was written.'
    foreach ($case in @(@{ Sub = $OtherSub; Tenant = $Tenant }, @{ Sub = $Sub; Tenant = $OtherTenant })) {
        Reset-Fake
        $global:Fake.AccountSub = $case.Sub
        $global:Fake.AccountTenant = $case.Tenant
        Assert-Fails { $null = Invoke-State } 'not the enabled deployment subscription and tenant' 'A different Azure CLI account was accepted.'
        Assert-Test ($global:Fake.Calls.Count -eq 1 -and $global:Fake.Mutations.Count -eq 0) 'A different Azure CLI account reached further Azure calls.'
    }
    Reset-Fake
    $global:Fake.AccountFails = $true
    Assert-Fails { $null = Invoke-State } 'cannot read the deployment subscription' 'A missing Azure CLI login was accepted.'
    Reset-Fake
    $global:Fake.OidBad = $true
    Assert-Fails { $null = Invoke-State } 'pass -OperatorObjectId' 'An unreadable signed-in operator was accepted.'
    Assert-Test ($global:Fake.Mutations.Count -eq 0) 'An unreadable operator still mutated Azure.'
    Reset-Fake
    $global:Fake.FailVerb = 'storage account create'
    Remove-Backend
    Assert-Fails { $null = Invoke-State } 'Azure CLI failed: storage account create' 'A failing account creation was ignored.'
    Assert-Test ($global:Fake.PollCalls -eq 0 -and -not [IO.File]::Exists($BackendPath)) 'A failed creation still polled or wrote backend.hcl.'
    Reset-Fake
    $global:Fake.FailVerb = 'group exists'
    Assert-Fails { $null = Invoke-State } 'Azure CLI failed: group exists' 'A failing read was ignored.'

    # ---- bounded data-plane wait --------------------------------------------------------------------------------------------
    Reset-Fake
    Set-Compliant
    Remove-Backend
    $global:Fake.PollFailures = 3
    $null = Invoke-State
    Assert-Test ($global:Fake.PollCalls -eq 4 -and @($global:Fake.Sleeps).Count -eq 3 -and @($global:Fake.Sleeps | Where-Object { $_ -ne 10 }).Count -eq 0) 'The wait did not retry three times at 10 seconds.'
    Assert-Test (@($global:Fake.PollBackendPresent | Where-Object { $_ }).Count -eq 0) 'backend.hcl existed before the data plane became reachable.'
    Assert-Test ([IO.File]::ReadAllText($BackendPath) -ceq $ExpectedBackend) 'backend.hcl was not written after the wait.'
    Reset-Fake
    Set-Compliant
    [IO.File]::WriteAllText($BackendPath, "previous backend`n", [Text.UTF8Encoding]::new($false))
    $global:Fake.PollFailures = 1000
    Assert-Fails { $null = Invoke-State } 'not yet reachable with the operator identity' 'An unreachable state container was accepted.'
    Assert-Test ($global:Fake.PollCalls -eq 24 -and @($global:Fake.Sleeps).Count -eq 23) 'The wait was not bounded to 24 probes with 23 pauses.'
    Assert-Test ([IO.File]::ReadAllText($BackendPath) -ceq "previous backend`n" -and @(Get-ChildItem -LiteralPath $Work -Force | Where-Object { $_.Name -match '\.tmp-' }).Count -eq 0) 'An unreachable state container changed the previous backend.hcl or left a temporary file.'

    # ---- no credential or destructive command anywhere ---------------------------------------------------------------------
    Assert-Test ($global:AllCalls.Count -gt 50) 'The recorded Azure calls look incomplete.'
    Assert-Test (@($global:AllCalls | Where-Object { $_ -match '(?i)list-keys|account-key|--sas-token|generate-sas|\bsas\b|connection-string|--auth-mode key|\bkeys\b|^login\b|--expiry|--permissions' }).Count -eq 0) 'A key, SAS, connection string or login command was issued.'
    Assert-Test (@($global:AllCalls | Where-Object { (($_ -split ' ')[0..2] -join ' ') -match '\b(delete|purge)\b' }).Count -eq 0) 'A delete command was issued.'
    Assert-Test (@($global:AllCalls | Where-Object { $_ -cnotmatch '--subscription ' }).Count -eq 0) 'An Azure call was not scoped with --subscription.'

    # ---- Export-AsreContext --------------------------------------------------------------------------------------------------
    function New-RealGuid { param([int]$Number) ('{0:x8}-b2c3-4d5e-8f60-{0:x12}' -f (0xa1000000 + $Number)) }
    function New-RealContext {
        param([string]$SubscriptionId = $RealSub, [string]$TenantId = $RealTenant, [switch]$ServiceNow, [switch]$Endpoint, [int]$Pad = 0)
        $document = [IO.File]::ReadAllText((Join-Path $Repo 'tests\fixtures\context.synthetic.json')) | ConvertFrom-Json -AsHashtable
        $s = Get-DerivedSuffix $SubscriptionId
        $document.subscription_id = $SubscriptionId
        $document.tenant_id = $TenantId
        $document.name_suffix = $s
        $document.operator_object_id = New-RealGuid 1
        $document.registry = @{ name = "acrasre$s"; login_server = "acrasre$s.azurecr.io" }
        $document.key_vault = @{ name = "kv-asre-$s"; uri = "https://kv-asre-$s.vault.azure.net/" }
        $document.storage = @{ account_name = "stasre$s"; blob_endpoint = "https://stasre$s.blob.core.windows.net"; table_endpoint = "https://stasre$s.table.core.windows.net" }
        $document.log_analytics.customer_id = New-RealGuid 2
        $number = 10
        foreach ($identity in 'agent', 'runner', 'collector', 'pull', 'bootstrap') {
            $document.identities[$identity] = @{ client_id = New-RealGuid $number; principal_id = New-RealGuid ($number + 1) }
            $number += 2
        }
        if ($Endpoint) { $document.agent.endpoint = 'https://asre-demo-cus--abc123.zyx987.centralus.azuresre.ai' } else { $document.agent.Remove('endpoint') }
        if ($ServiceNow) {
            $document.servicenow = @{
                origin = 'https://contoso-dev.service-now.com'; api_base = '/api/x_1234567_asre/case_adapter/v1'
                client_id = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'; facade_client_id = 'b1b2c3d4e5f60718293a4b5c6d7e8f91'; assignment_group = 'c1b2c3d4e5f60718293a4b5c6d7e8f92'
                configuration_items = @{ linux = 'd1b2c3d4e5f60718293a4b5c6d7e8f93'; patch = 'e1b2c3d4e5f60718293a4b5c6d7e8f94'; backup = 'f1b2c3d4e5f60718293a4b5c6d7e8f95' }
            }
        }
        else { $document.Remove('servicenow') }
        if ($Pad -gt 0) { $document.pad = ('x' * $Pad) }
        ConvertTo-Json -InputObject $document -Depth 10 -Compress
    }
    function Write-Outputs {
        param([string]$Name, [string]$ContextJson)
        $path = Join-Path $Work $Name
        [IO.File]::WriteAllText($path, "{`"context`":{`"sensitive`":false,`"type`":`"object`",`"value`":$ContextJson}}", [Text.UTF8Encoding]::new($false))
        $path
    }
    function Invoke-Export {
        param([string]$OutputsFile, [string]$OutputPath, [string]$ExportSub = $RealSub, [string]$ExportTenant = $RealTenant)
        $global:Fake.Calls.Clear(); $global:TfCalls.Clear(); $global:TfEnvironment.Clear()
        $parameters = @{ SubscriptionId = $ExportSub; TenantId = $ExportTenant; OutputPath = $OutputPath }
        if ($OutputsFile) { $parameters.OutputsFile = $OutputsFile }
        @(& $ExportScript @parameters) | ForEach-Object { [string]$_ }
    }
    function global:terraform {
        $global:LASTEXITCODE = 0
        $global:TfCalls.Add(($args -join ' '))
        $global:TfEnvironment.Add(@{
                UseCli = $env:ARM_USE_CLI; Sub = $env:ARM_SUBSCRIPTION_ID; Tenant = $env:ARM_TENANT_ID; Secret = $env:ARM_CLIENT_SECRET
                ClientId = $env:ARM_CLIENT_ID; Oidc = $env:ARM_USE_OIDC; AccessKey = $env:ARM_ACCESS_KEY; Sas = $env:ARM_SAS_TOKEN; Workspace = $env:TF_WORKSPACE
            })
        if ($global:TfFail) { $global:LASTEXITCODE = 1; return 'Error: fixture' }
        return $global:TfOutput
    }

    $ExportPath = Join-Path $Work 'context.generated.json'
    $RealJson = New-RealContext
    Reset-Fake
    $global:Fake.ExpectedSub = $RealSub; $global:Fake.AccountSub = $RealSub; $global:Fake.AccountTenant = $RealTenant
    # The context-building helper itself yields a context the loader accepts as a real (non-synthetic) one.
    . (Join-Path $Repo 'scripts\Context.Common.ps1')
    $loaded = ConvertTo-AsreContext -Json $RealJson
    Assert-Test ($loaded.subscription_id -ceq $RealSub -and -not (Test-AsreSyntheticContext -Context $loaded)) 'The fixture context is not accepted as a real context.'

    # From a deployment-outputs file: no Azure and no terraform call.
    $outputsFile = Write-Outputs 'outputs.json' $RealJson
    $output = Invoke-Export -OutputsFile $outputsFile -OutputPath $ExportPath
    Assert-Test ($global:Fake.Calls.Count -eq 0 -and $global:TfCalls.Count -eq 0) 'Exporting from an outputs file reached Azure or terraform.'
    $written = [IO.File]::ReadAllBytes($ExportPath)
    Assert-Test (-not ($written.Length -ge 3 -and $written[0] -eq 0xEF -and $written[1] -eq 0xBB -and $written[2] -eq 0xBF)) 'The context file has a byte order mark.'
    Assert-Test ([IO.File]::ReadAllText($ExportPath) -ceq ($RealJson + "`n")) 'The context file is not the exact context.value document plus one trailing newline.'
    $reloaded = Get-AsreContext -Path $ExportPath
    Assert-Test ($reloaded.subscription_id -ceq $RealSub -and $reloaded.tenant_id -ceq $RealTenant) 'The saved context cannot be loaded by the real loader.'
    Assert-Test (($output -join "`n") -match 'Deployment context saved' -and ($output -join "`n") -match 'ServiceNow: not configured' -and ($output -join "`n") -match 'not yet deployed') 'The export summary is wrong for a context without an endpoint or ServiceNow.'
    Assert-Test (($output -join "`n") -notmatch '(?i)customer|client_id|principal|secret|token|key=') 'The export summary printed an identifier or secret.'
    Assert-Test (@(Get-ChildItem -LiteralPath $Work -Force | Where-Object { $_.Name -match '\.tmp-' }).Count -eq 0) 'The export left a temporary file.'
    # Replacement, with an endpoint and ServiceNow.
    $withAll = New-RealContext -Endpoint -ServiceNow
    $output = Invoke-Export -OutputsFile (Write-Outputs 'outputs-all.json' $withAll) -OutputPath $ExportPath
    Assert-Test ([IO.File]::ReadAllText($ExportPath) -ceq ($withAll + "`n") -and ($output -join "`n") -match 'ServiceNow: configured' -and ($output -join "`n") -match 'endpoint present') 'An existing context file was not replaced by the new context.'

    # Refusals leave an existing file untouched.
    $keep = [IO.File]::ReadAllText($ExportPath)
    $syntheticJson = [IO.File]::ReadAllText((Join-Path $Repo 'tests\fixtures\context.synthetic.json')) | ConvertFrom-Json -AsHashtable | ConvertTo-Json -Depth 10 -Compress
    $invalidJson = (New-RealContext) -replace '"location":"centralus"', '"location":"eastus"'
    $unknownKey = (New-RealContext) -replace '^\{', '{"extra":1,'
    foreach ($case in @(
            @{ Name = 'a synthetic context'; Json = $syntheticJson; Sub = '00000001-000a-4000-a000-000000000001'; Tenant = '00000002-000a-4000-9000-000000000002'; Pattern = 'synthetic test values' },
            @{ Name = 'a context of another subscription'; Json = (New-RealContext -SubscriptionId 'f00dcafe-1111-4222-8333-444455556666'); Sub = $RealSub; Tenant = $RealTenant; Pattern = 'does not belong to the requested subscription and tenant' },
            @{ Name = 'a context of another tenant'; Json = (New-RealContext -TenantId 'f00dcafe-9999-4222-8333-444455556666'); Sub = $RealSub; Tenant = $RealTenant; Pattern = 'does not belong to the requested subscription and tenant' },
            @{ Name = 'a context with another location'; Json = $invalidJson; Sub = $RealSub; Tenant = $RealTenant; Pattern = 'Invalid deployment context at \$\.location' },
            @{ Name = 'a context with an unknown key'; Json = $unknownKey; Sub = $RealSub; Tenant = $RealTenant; Pattern = 'Invalid deployment context at \$: unknown extra' },
            @{ Name = 'an oversized context'; Json = (New-RealContext -Pad 70000); Sub = $RealSub; Tenant = $RealTenant; Pattern = 'too large' })) {
        $file = Write-Outputs 'refused.json' $case.Json
        Assert-Fails { $null = Invoke-Export -OutputsFile $file -OutputPath $ExportPath -ExportSub $case.Sub -ExportTenant $case.Tenant } $case.Pattern "$($case.Name) was exported."
        Assert-Test ([IO.File]::ReadAllText($ExportPath) -ceq $keep) "$($case.Name) changed the existing context file."
    }
    Assert-Test (@(Get-ChildItem -LiteralPath $Work -Force | Where-Object { $_.Name -match '\.tmp-' }).Count -eq 0) 'A refused export left a temporary file.'
    # An invalid document never creates the file either.
    $freshPath = Join-Path $Work 'fresh-context.json'
    Assert-Fails { $null = Invoke-Export -OutputsFile (Write-Outputs 'refused.json' $invalidJson) -OutputPath $freshPath } 'Invalid deployment context' 'An invalid context was exported to a new file.'
    Assert-Test (-not [IO.File]::Exists($freshPath)) 'An invalid context created a file.'
    # Malformed outputs files and paths.
    $badOutputs = @(
        @{ Name = 'a missing outputs file'; File = (Join-Path $Work 'no-such-outputs.json'); Pattern = 'outputs file was not found' },
        @{ Name = 'outputs without a context'; Text = '{"other":{"value":{}}}'; Pattern = 'no context.value document' },
        @{ Name = 'outputs with a scalar value'; Text = '{"context":{"value":"x"}}'; Pattern = 'no context.value document' },
        @{ Name = 'outputs without a value'; Text = '{"context":{"sensitive":false}}'; Pattern = 'no context.value document' },
        @{ Name = 'outputs that are not an object'; Text = '[1,2]'; Pattern = 'no context.value document' },
        @{ Name = 'outputs that are not JSON'; Text = 'not json'; Pattern = 'no context.value document' })
    foreach ($case in $badOutputs) {
        $file = if ($case.ContainsKey('File')) { $case.File } else {
            $path = Join-Path $Work 'bad-outputs.json'
            [IO.File]::WriteAllText($path, $case.Text, [Text.UTF8Encoding]::new($false))
            $path
        }
        Assert-Fails { $null = Invoke-Export -OutputsFile $file -OutputPath $ExportPath } $case.Pattern "$($case.Name) was accepted."
        Assert-Test ([IO.File]::ReadAllText($ExportPath) -ceq $keep) "$($case.Name) changed the existing context file."
    }
    $invalidBytes = Join-Path $Work 'invalid-utf8.json'
    [IO.File]::WriteAllBytes($invalidBytes, [byte[]](0x7B, 0xFF, 0xFE, 0x7D))
    Assert-Fails { $null = Invoke-Export -OutputsFile $invalidBytes -OutputPath $ExportPath } '.' 'An outputs file that is not UTF-8 was accepted.'
    Assert-Fails { $null = Invoke-Export -OutputsFile $outputsFile -OutputPath (Join-Path $Work 'missing-directory\context.json') } 'OutputPath directory does not exist' 'A missing output directory was accepted.'
    foreach ($case in @(@{ Sub = $RealSub.ToUpperInvariant(); Tenant = $RealTenant; Pattern = 'SubscriptionId must be a lowercase GUID' }, @{ Sub = $RealSub; Tenant = 'tenant'; Pattern = 'TenantId must be a lowercase GUID' })) {
        Assert-Fails { $null = Invoke-Export -OutputsFile $outputsFile -OutputPath $ExportPath -ExportSub $case.Sub -ExportTenant $case.Tenant } $case.Pattern 'An invalid export parameter was accepted.'
    }

    # From terraform: the signed-in account is verified first; ambient credentials are removed; the output is read in infra.
    [Environment]::SetEnvironmentVariable('ARM_CLIENT_SECRET', 'must-not-reach-terraform', 'Process')
    [Environment]::SetEnvironmentVariable('ARM_CLIENT_ID', 'client', 'Process')
    [Environment]::SetEnvironmentVariable('ARM_USE_OIDC', 'true', 'Process')
    [Environment]::SetEnvironmentVariable('ARM_ACCESS_KEY', 'key', 'Process')
    [Environment]::SetEnvironmentVariable('ARM_SAS_TOKEN', 'sas', 'Process')
    [Environment]::SetEnvironmentVariable('TF_WORKSPACE', 'other', 'Process')
    $global:TfOutput = New-RealContext -Endpoint
    $freshPath = Join-Path $Work 'from-terraform.json'
    $output = Invoke-Export -OutputPath $freshPath
    Assert-Test ($global:Fake.Calls.Count -eq 1 -and $global:Fake.Calls[0] -ceq "account show --subscription $RealSub --output json --only-show-errors") 'The terraform export did not verify the Azure CLI account first, and only.'
    Assert-Test ($global:TfCalls.Count -eq 1 -and $global:TfCalls[0] -ceq "-chdir=$(Join-Path $Repo 'infra') output -json context") "The terraform export ran an unexpected command: $($global:TfCalls -join '; ')"
    $seen = $global:TfEnvironment[0]
    Assert-Test ($seen.UseCli -ceq 'true' -and $seen.Sub -ceq $RealSub -and $seen.Tenant -ceq $RealTenant) 'terraform was not told to use Azure CLI authentication for the subscription and tenant.'
    Assert-Test ($null -eq $seen.Secret -and $null -eq $seen.ClientId -and $null -eq $seen.Oidc -and $null -eq $seen.AccessKey -and $null -eq $seen.Sas -and $null -eq $seen.Workspace) 'Ambient credentials or a workspace leaked into the terraform process.'
    Assert-Test ([IO.File]::ReadAllText($freshPath) -ceq ($global:TfOutput + "`n")) 'The terraform output was not saved verbatim.'
    # A multi-line terraform output (pretty-printed) is joined and saved.
    $pretty = (New-RealContext) | ConvertFrom-Json -AsHashtable | ConvertTo-Json -Depth 10
    $global:TfOutput = $pretty -split "`r?`n"
    $prettyPath = Join-Path $Work 'pretty.json'
    $null = Invoke-Export -OutputPath $prettyPath
    Assert-Test ((Get-AsreContext -Path $prettyPath).subscription_id -ceq $RealSub) 'A multi-line terraform output was not saved as a loadable context.'
    # Failures.
    $global:TfOutput = New-RealContext
    $global:TfFail = $true
    $failPath = Join-Path $Work 'terraform-failed.json'
    Assert-Fails { $null = Invoke-Export -OutputPath $failPath } 'Terraform could not read the context output' 'A failing terraform output was accepted.'
    Assert-Test (-not [IO.File]::Exists($failPath)) 'A failing terraform output created a file.'
    $global:TfFail = $false
    $global:Fake.AccountSub = $OtherSub
    Assert-Fails { $null = Invoke-Export -OutputPath $failPath } 'not the enabled deployment subscription and tenant' 'A different Azure CLI account was accepted for the terraform export.'
    Assert-Test ($global:TfCalls.Count -eq 0 -and -not [IO.File]::Exists($failPath)) 'terraform ran under a different Azure CLI account.'
    $global:Fake.AccountSub = $RealSub
    $global:TfOutput = $syntheticJson
    $global:Fake.ExpectedSub = $Sub; $global:Fake.AccountSub = $Sub; $global:Fake.AccountTenant = $Tenant
    Assert-Fails { $null = Invoke-Export -OutputPath $failPath -ExportSub $Sub -ExportTenant $Tenant } 'synthetic test values' 'A synthetic terraform output was exported.'
    Assert-Test (-not [IO.File]::Exists($failPath)) 'A synthetic terraform output created a file.'

    Write-Output "Terraform state and context export fixtures passed ($($global:AssertionCount) assertions); no Azure or terraform command was executed."
}
finally {
    foreach ($name in $SavedEnvironment.Keys) {
        if ($null -eq $SavedEnvironment[$name]) { Remove-Item -LiteralPath "Env:\$name" -Force -ErrorAction SilentlyContinue }
        else { [Environment]::SetEnvironmentVariable($name, $SavedEnvironment[$name], 'Process') }
    }
    Remove-Item -LiteralPath 'Function:\az', 'Function:\terraform', 'Function:\Start-Sleep' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
