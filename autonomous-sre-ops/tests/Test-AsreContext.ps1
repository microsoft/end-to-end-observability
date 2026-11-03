#Requires -Version 7.5
# Offline tests for the shared deployment-context loader (scripts/Context.Common.ps1).
#
# Covers: every shared mutant (the loader must reject every invalid one and accept every valid one; the JSON Schema
# must agree for every non-cross-field mutant), the strict reader (size cap, UTF-8, duplicate keys, trailing commas),
# synthetic-context refusal, the path precedence, the leaf/value helpers and Assert-AsreSubscription with a shadowed
# Azure CLI. No Azure, terraform or network command is executed; the real az is shadowed for the whole run.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = Split-Path -Parent $PSScriptRoot
$Fixtures = Join-Path $PSScriptRoot 'fixtures'
$SchemaPath = Join-Path $Repo 'schemas\asre-context.v1.schema.json'
$SyntheticPath = Join-Path $Fixtures 'context.synthetic.json'
$AltPath = Join-Path $Fixtures 'context.alt.json'
$MutantPath = Join-Path $Fixtures 'context.mutants.json'
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$script:AssertionCount = 0

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    $script:AssertionCount++
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Assert-Fails {
    param([scriptblock]$Block, [string]$Pattern, [string]$Message)
    $script:AssertionCount++
    $failure = $null
    try { $null = & $Block } catch { $failure = $_.Exception.Message }
    if ($null -eq $failure) { throw "ASSERTION FAILED: $Message (it did not fail)" }
    if ($failure -cnotmatch $Pattern) { throw "ASSERTION FAILED: $Message (unexpected failure: $failure)" }
}

function Get-DerivedSuffix {
    # Independent of the loader: the first ten hex characters of sha256("<subscription>:asre-demo").
    param([string]$SubscriptionId)
    $hash = [System.Security.Cryptography.SHA256]::HashData($Utf8.GetBytes("${SubscriptionId}:asre-demo"))
    [System.Convert]::ToHexString($hash).ToLowerInvariant().Substring(0, 10)
}

function Test-Schema {
    param([string]$Json)
    [bool](Test-Json -Json $Json -SchemaFile $SchemaPath -ErrorAction SilentlyContinue)
}

function Set-DottedValue {
    param([System.Collections.IDictionary]$Root, [string]$Path, $Value)
    $parts = $Path.Split('.')
    $node = $Root
    for ($i = 0; $i -lt $parts.Count - 1; $i++) {
        if (-not $node.Contains($parts[$i]) -or $node[$parts[$i]] -isnot [System.Collections.IDictionary]) { $node[$parts[$i]] = @{} }
        $node = $node[$parts[$i]]
    }
    $node[$parts[$parts.Count - 1]] = $Value
}

function Remove-DottedValue {
    param([System.Collections.IDictionary]$Root, [string]$Path)
    $parts = $Path.Split('.')
    $node = $Root
    for ($i = 0; $i -lt $parts.Count - 1; $i++) {
        if (-not $node.Contains($parts[$i]) -or $node[$parts[$i]] -isnot [System.Collections.IDictionary]) { throw "The mutant deletes a path that is not in the base: $Path" }
        $node = $node[$parts[$i]]
    }
    if (-not $node.Contains($parts[$parts.Count - 1])) { throw "The mutant deletes a path that is not in the base: $Path" }
    $node.Remove($parts[$parts.Count - 1])
}

function New-MutantJson {
    param([System.Collections.IDictionary]$Mutant)
    $document = [System.IO.File]::ReadAllText($SyntheticPath) | ConvertFrom-Json -AsHashtable
    if ($Mutant.ContainsKey('delete')) { foreach ($path in $Mutant['delete']) { Remove-DottedValue -Root $document -Path ([string]$path) } }
    if ($Mutant.ContainsKey('set')) { foreach ($path in @($Mutant['set'].Keys)) { Set-DottedValue -Root $document -Path ([string]$path) -Value $Mutant['set'][$path] } }
    ConvertTo-Json -InputObject $document -Depth 20 -Compress
}

function Write-TestFile {
    param([string]$Name, [byte[]]$Bytes)
    $path = Join-Path $Work $Name
    [System.IO.File]::WriteAllBytes($path, $Bytes)
    $path
}

function Write-TestText {
    param([string]$Name, [string]$Text)
    Write-TestFile -Name $Name -Bytes $Utf8.GetBytes($Text)
}

# A context that is not synthetic: a real-looking subscription, derived names and non-sentinel identifiers.
$RealSub = 'c0ffee00-1234-4abc-8def-0123456789ab'
$RealTenant = 'deadbeef-4321-4cba-9fed-ba9876543210'
function New-RealGuid { param([int]$Number) ('{0:x8}-b2c3-4d5e-8f60-{0:x12}' -f (0xa1000000 + $Number)) }
function New-RealContextJson {
    param([string]$SubscriptionId = $RealSub, [string]$TenantId = $RealTenant, [switch]$ServiceNow, [switch]$Endpoint)
    $document = [System.IO.File]::ReadAllText($SyntheticPath) | ConvertFrom-Json -AsHashtable
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
    ConvertTo-Json -InputObject $document -Depth 20 -Compress
}

$FixedMessage = '\A(?:Invalid deployment context at |The deployment context )'
$AmbientPresent = Test-Path -LiteralPath 'Env:\ASRE_CONTEXT_FILE'
$AmbientValue = if ($AmbientPresent) { $env:ASRE_CONTEXT_FILE } else { $null }
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ('asre-context-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Work

$global:AzCalls = [System.Collections.Generic.List[string]]::new()
$global:AzExit = 0
$global:AzReply = $null
function global:az {
    $global:AzCalls.Add(($args -join ' '))
    $global:LASTEXITCODE = $global:AzExit
    return $global:AzReply
}

try {
    Assert-Test ($null -eq (Get-Command -Name Get-AsreContext -CommandType Alias -ErrorAction SilentlyContinue)) 'A test alias shadows the real loader; run this test in a fresh process.'
    . (Join-Path $Repo 'scripts\Context.Common.ps1')
    Assert-Test ((Get-Command -Name Get-AsreContext).CommandType -eq 'Function') 'The real loader is not a function.'

    # ---- fixtures and derived values -------------------------------------------------------------------------------------
    Assert-Test ((Get-DerivedSuffix '00000001-000a-4000-a000-000000000001') -ceq '8a327010f9') 'The independent suffix derivation disagrees with the synthetic fixture.'
    Assert-Test ((Get-DerivedSuffix '00000901-000a-4000-8000-000000000901') -ceq 'e26b179818') 'The independent suffix derivation disagrees with the alt fixture.'
    foreach ($case in @(@{ Path = $SyntheticPath; Sub = '00000001-000a-4000-a000-000000000001'; Tenant = '00000002-000a-4000-9000-000000000002' },
            @{ Path = $AltPath; Sub = '00000901-000a-4000-8000-000000000901'; Tenant = '00000902-000a-4000-8000-000000000902' })) {
        $context = Get-AsreContext -Path $case.Path -AllowSynthetic
        $s = Get-DerivedSuffix $case.Sub
        Assert-Test ($context.subscription_id -ceq $case.Sub -and $context.tenant_id -ceq $case.Tenant -and $context.name_suffix -ceq $s) "Fixture $($case.Path) did not load with its own identifiers."
        Assert-Test ($context.schema -ceq 'asre-context/v1' -and $context.location -ceq 'centralus') 'The loaded context lost its schema or location.'
        foreach ($role in 'control', 'linux', 'patch', 'backup') {
            Assert-Test ($context.resource_groups[$role] -ceq "rg-asre-$role-cus" -and $context.resource_group_ids[$role] -ceq "/subscriptions/$($case.Sub)/resourceGroups/rg-asre-$role-cus") "Resource group $role is not derived from the subscription."
        }
        Assert-Test ($context.registry.name -ceq "acrasre$s" -and $context.registry.login_server -ceq "acrasre$s.azurecr.io") 'The registry name is not derived from the suffix.'
        Assert-Test ($context.key_vault.name -ceq "kv-asre-$s" -and $context.key_vault.uri -ceq "https://kv-asre-$s.vault.azure.net/") 'The key vault is not derived from the suffix.'
        Assert-Test ($context.storage.account_name -ceq "stasre$s" -and $context.storage.table_endpoint -ceq "https://stasre$s.table.core.windows.net") 'The storage account is not derived from the suffix.'
        Assert-Test ($context.agent.resource_id -ceq "/subscriptions/$($case.Sub)/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus") 'The agent resource id is not derived.'
        Assert-Test ($context.targets.linux.resource_id -ceq "/subscriptions/$($case.Sub)/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus" -and $context.targets.patch.address -ceq '10.42.0.11') 'A target resource id or address is wrong.'
        Assert-Test ($context.identities.runner.resource_id -ceq "/subscriptions/$($case.Sub)/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-runner-cus") 'An identity resource id is not derived.'
        Assert-Test ($context.log_analytics.resource_id -ceq "/subscriptions/$($case.Sub)/resourceGroups/rg-asre-control-cus/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus") 'The workspace resource id is not derived.'
        Assert-Test ($context.recovery_vault.resource_id -ceq "/subscriptions/$($case.Sub)/resourceGroups/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus") 'The recovery vault resource id is not derived.'
        Assert-Test (Test-AsreSyntheticContext -Context $context) 'A synthetic fixture was not recognized as synthetic.'
        Assert-Fails { Get-AsreContext -Path $case.Path } 'carries synthetic test values' 'The default loader accepted a synthetic fixture.'
        Assert-Test (Test-Schema ([System.IO.File]::ReadAllText($case.Path))) 'The JSON Schema rejected a committed fixture.'
    }

    # ---- a real-looking context passes the default loader -----------------------------------------------------------------
    foreach ($variant in @(@{}, @{ ServiceNow = $true }, @{ Endpoint = $true }, @{ ServiceNow = $true; Endpoint = $true })) {
        $json = New-RealContextJson @variant
        $real = Get-AsreContext -Path (Write-TestText 'real.json' $json)
        Assert-Test ($real.subscription_id -ceq $RealSub -and -not (Test-AsreSyntheticContext -Context $real)) 'A non-synthetic context was refused or flagged as synthetic.'
        Assert-Test ($real.agent.resource_id -ceq "/subscriptions/$RealSub/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus") 'A real context did not derive its agent resource id from its subscription.'
        Assert-Test ($real.name_suffix -ceq (Get-DerivedSuffix $RealSub)) 'A real context carries the wrong suffix.'
        Assert-Test ((($null -ne $real.agent.endpoint) -eq [bool]$variant.ContainsKey('Endpoint')) -and (($null -ne $real.servicenow) -eq [bool]$variant.ContainsKey('ServiceNow'))) 'An optional agent endpoint or ServiceNow block was not honored as given.'
        Assert-Test (Test-Schema $json) 'The JSON Schema rejected a real-looking context.'
    }
    # One synthetic leaf in an otherwise real context is enough to refuse it by default, but -AllowSynthetic admits it.
    $partial = New-RealContextJson -ServiceNow | ConvertFrom-Json -AsHashtable
    $partial.servicenow.origin = 'https://example-instance.service-now.com'
    $partialPath = Write-TestText 'partial.json' (ConvertTo-Json -InputObject $partial -Depth 20 -Compress)
    Assert-Fails { Get-AsreContext -Path $partialPath } 'carries synthetic test values' 'A context with one synthetic leaf was accepted by default.'
    Assert-Test ((Get-AsreContext -Path $partialPath -AllowSynthetic).servicenow.origin -ceq 'https://example-instance.service-now.com') '-AllowSynthetic did not admit a partly synthetic context.'
    # The synthetic agent endpoint alone (no synthetic GUID anywhere) must also be recognized.
    $endpointOnly = New-RealContextJson -Endpoint | ConvertFrom-Json -AsHashtable
    $endpointOnly.agent.endpoint = 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai'
    $endpointOnlyPath = Write-TestText 'endpoint-only.json' (ConvertTo-Json -InputObject $endpointOnly -Depth 20 -Compress)
    Assert-Fails { Get-AsreContext -Path $endpointOnlyPath } 'carries synthetic test values' 'A real context with only the synthetic agent endpoint was accepted by default.'
    $endpointOnly.agent.endpoint = 'https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai'
    Assert-Fails { Get-AsreContext -Path (Write-TestText 'endpoint-alt.json' (ConvertTo-Json -InputObject $endpointOnly -Depth 20 -Compress)) } 'carries synthetic test values' 'A real context with only the alternative synthetic agent endpoint was accepted by default.'

    # ---- shared mutants ----------------------------------------------------------------------------------------------------
    $mutants = [System.IO.File]::ReadAllText($MutantPath) | ConvertFrom-Json -AsHashtable
    Assert-Test ($mutants.format -ceq 'asre-context-mutants/v1' -and $mutants.base -ceq 'context.synthetic.json') 'The mutant file has an unexpected format or base.'
    Assert-Test (@($mutants.valid).Count -ge 8 -and @($mutants.invalid).Count -ge 40) 'The mutant file lost mutants.'
    $names = @(@($mutants.valid) + @($mutants.invalid) | ForEach-Object { [string]$_.name })
    Assert-Test (@($names | Sort-Object -Unique).Count -eq $names.Count -and -not @($names | Where-Object { [string]::IsNullOrWhiteSpace($_) })) 'Mutant names must be unique and non-empty.'
    Assert-Test (@($mutants.invalid | Where-Object { $_.ContainsKey('cross_field') -and $_.cross_field }).Count -ge 5) 'The mutant file lost its cross-field mutants.'

    $index = 0
    foreach ($mutant in $mutants.valid) {
        $index++
        $json = New-MutantJson $mutant
        $path = Write-TestText "valid-$index.json" $json
        $loaded = $null
        try { $loaded = Get-AsreContext -Path $path -AllowSynthetic } catch { throw "ASSERTION FAILED: The loader rejected valid mutant $($mutant.name): $($_.Exception.Message)" }
        $script:AssertionCount++
        Assert-Test (Test-Schema $json) "The JSON Schema rejected valid mutant $($mutant.name)."
        # Omitted and null optional values are normalized so scripts never trip over a missing key under StrictMode.
        Assert-Test ($loaded.ContainsKey('operator_object_id') -and $loaded.ContainsKey('servicenow') -and $loaded.agent.ContainsKey('endpoint')) "Valid mutant $($mutant.name) lost a normalized optional key."
        switch -CaseSensitive ([string]$mutant.name) {
            { $_ -in 'agent-endpoint-null', 'agent-endpoint-absent' } { Assert-Test ($null -eq $loaded.agent.endpoint) 'An absent agent endpoint must load as null.' }
            { $_ -in 'servicenow-absent', 'servicenow-null' } { Assert-Test ($null -eq $loaded.servicenow) 'An absent ServiceNow block must load as null.' }
            { $_ -in 'operator-absent', 'operator-null' } { Assert-Test ($null -eq $loaded.operator_object_id) 'An absent operator must load as null.' }
            { $_ -in 'bootstrap-identity-absent', 'bootstrap-identity-null' } { Assert-Test (-not $loaded.identities.ContainsKey('bootstrap')) 'An absent bootstrap identity must not appear in the context.' }
        }
    }

    $index = 0
    foreach ($mutant in $mutants.invalid) {
        $index++
        $json = New-MutantJson $mutant
        $failure = $null
        try { $null = ConvertTo-AsreContext -Json $json } catch { $failure = $_.Exception.Message }
        Assert-Test ($null -ne $failure) "The loader accepted invalid mutant $($mutant.name)."
        Assert-Test ($failure -cmatch $FixedMessage) "Invalid mutant $($mutant.name) failed with an unexpected message: $failure"
        if ($mutant.ContainsKey('set')) {
            # The path before the first colon names the field; what follows must not repeat the offending value.
            $detail = $failure.Substring($failure.IndexOf(': ') + 2)
            foreach ($value in $mutant['set'].Values) {
                if ($value -is [string] -and $value.Length -ge 6) { Assert-Test (-not $detail.Contains($value)) "The rejection of $($mutant.name) echoed a value from the document." }
            }
        }
        $path = Write-TestText "invalid-$index.json" $json
        Assert-Fails { Get-AsreContext -Path $path -AllowSynthetic } $FixedMessage "The file loader accepted invalid mutant $($mutant.name)."
        $schemaAccepts = Test-Schema $json
        if ($mutant.ContainsKey('cross_field') -and $mutant.cross_field) {
            Assert-Test $schemaAccepts "The JSON Schema rejected cross-field mutant $($mutant.name), which only the loaders may reject."
        }
        else {
            Assert-Test (-not $schemaAccepts) "The JSON Schema accepted invalid mutant $($mutant.name)."
        }
    }

    # ---- strict reader -----------------------------------------------------------------------------------------------------
    $parsed = ConvertFrom-AsreStrictJson -Json '{"a":[1,2,{"b":null}],"c":true,"d":1.5,"e":"x","f":false}'
    Assert-Test ($parsed -is [hashtable] -and @($parsed.a).Count -eq 3 -and $null -eq $parsed.a[2].b -and $parsed.c -eq $true -and $parsed.f -eq $false -and $parsed.d -eq 1.5 -and $parsed.e -ceq 'x') 'The strict reader changed a value.'
    Assert-Test ((ConvertFrom-AsreStrictJson -Json '{"a":1,"A":2}').Count -eq 2) 'Keys that differ only in case must stay distinct.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json '{"a":1,"a":2}' } 'duplicate key' 'A duplicate key was accepted.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json '{"a":{"b":1,"c":2,"b":3}}' } 'duplicate key' 'A nested duplicate key was accepted.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json '{"a":1,}' } 'not valid JSON' 'A trailing comma was accepted.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json '{/* c */"a":1}' } 'not valid JSON' 'A comment was accepted.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json '{"a":1} {"b":2}' } 'not valid JSON' 'Trailing content was accepted.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json (('[' * 20) + (']' * 20)) } 'not valid JSON' 'A document deeper than the limit was accepted.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json "{'a':1}" } 'not valid JSON' 'Single quotes were accepted.'
    foreach ($text in @('[]', '"text"', 'null', '1', 'true')) {
        $shown = $text
        Assert-Fails { ConvertTo-AsreContext -Json $text } 'expected an object' "A top-level $shown document was accepted as a context."
    }
    Assert-Fails { ConvertTo-AsreContext -Json ' ' } 'not valid JSON' 'A blank document was accepted.'
    Assert-Fails { ConvertTo-AsreContext -Json '' } 'not valid JSON' 'An empty document did not fail with the fixed message.'
    Assert-Fails { ConvertFrom-AsreStrictJson -Json '' } 'not valid JSON' 'An empty strict document did not fail with the fixed message.'
    Assert-Fails { ConvertTo-AsreContext -Json '{"schema":' } 'not valid JSON' 'A truncated document was accepted.'
    $duplicated = '{"schema":"asre-context/v1",' + ([System.IO.File]::ReadAllText($SyntheticPath).TrimStart().Substring(1))
    Assert-Fails { ConvertTo-AsreContext -Json $duplicated } 'duplicate key' 'A duplicate top-level schema key was accepted.'
    Assert-Fails { Get-AsreContext -Path (Write-TestText 'duplicate.json' $duplicated) -AllowSynthetic } 'duplicate key' 'A duplicate key in a file was accepted.'

    # ---- size cap and encoding ---------------------------------------------------------------------------------------------
    $compact = ConvertTo-Json -InputObject ([System.IO.File]::ReadAllText($SyntheticPath) | ConvertFrom-Json -AsHashtable) -Depth 20 -Compress
    $exact = $compact + (' ' * (65536 - $Utf8.GetByteCount($compact)))
    Assert-Test ($Utf8.GetByteCount($exact) -eq 65536) 'The size-cap fixture is not exactly 64 KB.'
    Assert-Test ((Get-AsreContext -Path (Write-TestText 'exact.json' $exact) -AllowSynthetic).name_suffix -ceq '8a327010f9') 'A document of exactly 64 KB was refused.'
    Assert-Test ((ConvertTo-AsreContext -Json $exact).name_suffix -ceq '8a327010f9') 'The text loader refused a document of exactly 64 KB.'
    Assert-Fails { Get-AsreContext -Path (Write-TestText 'over.json' ($exact + ' ')) -AllowSynthetic } 'too large' 'A document of 64 KB plus one byte was accepted.'
    Assert-Fails { ConvertTo-AsreContext -Json ($exact + ' ') } 'too large' 'The text loader accepted a document over 64 KB.'
    Assert-Fails { Get-AsreContext -Path (Write-TestText 'huge.json' (' ' * 300000 + $compact)) -AllowSynthetic } 'too large' 'A 300 KB file was accepted.'
    # The limit is in bytes, not characters: a multibyte payload that is under 64 K characters but over 64 KB bytes is refused.
    $multibyte = ('{"note":"' + ([string][char]0x00E9 * 33000) + '"}')
    Assert-Test ($multibyte.Length -lt 65536 -and $Utf8.GetByteCount($multibyte) -gt 65536) 'The multibyte fixture does not straddle the limit.'
    Assert-Fails { ConvertTo-AsreContext -Json $multibyte } 'too large' 'The size limit counted characters instead of bytes.'

    Assert-Fails { Get-AsreContext -Path (Write-TestFile 'latin1.json' ([byte[]](0x7B, 0x22, 0xFF, 0x22, 0x7D))) -AllowSynthetic } 'not UTF-8' 'A non-UTF-8 byte was accepted.'
    Assert-Fails { Get-AsreContext -Path (Write-TestFile 'truncated.json' ([byte[]](0x7B, 0x22, 0xC3, 0x28, 0x22, 0x7D))) -AllowSynthetic } 'not UTF-8' 'An invalid UTF-8 sequence was accepted.'
    $utf16 = [System.Text.Encoding]::Unicode.GetPreamble() + [System.Text.Encoding]::Unicode.GetBytes([System.IO.File]::ReadAllText($SyntheticPath))
    Assert-Fails { Get-AsreContext -Path (Write-TestFile 'utf16.json' $utf16) -AllowSynthetic } 'not UTF-8|not valid JSON' 'A UTF-16 file was accepted.'
    Assert-Fails { Get-AsreContext -Path (Write-TestFile 'empty.json' ([byte[]]@())) -AllowSynthetic } 'not valid JSON' 'An empty file was accepted.'
    Assert-Fails { Get-AsreContext -Path (Join-Path $Work 'missing.json') -AllowSynthetic } 'was not found: .*missing\.json\. Save the Terraform output' 'A missing file did not name the full path and the remedy.'
    Assert-Fails { Get-AsreContext -Path $Work -AllowSynthetic } 'was not found' 'A directory was accepted as a context file.'

    # ---- path precedence ---------------------------------------------------------------------------------------------------
    try {
        $env:ASRE_CONTEXT_FILE = $AltPath
        Assert-Test ((Get-AsreContext -AllowSynthetic).subscription_id -ceq '00000901-000a-4000-8000-000000000901') 'ASRE_CONTEXT_FILE was not honored.'
        Assert-Test ((Get-AsreContext -Path $SyntheticPath -AllowSynthetic).subscription_id -ceq '00000001-000a-4000-a000-000000000001') '-Path did not beat ASRE_CONTEXT_FILE.'
        Assert-Test ((Get-AsreContext -Path '  ' -AllowSynthetic).subscription_id -ceq '00000901-000a-4000-8000-000000000901') 'A blank -Path did not fall back to ASRE_CONTEXT_FILE.'
        $env:ASRE_CONTEXT_FILE = Join-Path $Work 'absent-from-env.json'
        Assert-Fails { Get-AsreContext -AllowSynthetic } 'absent-from-env\.json' 'A missing ASRE_CONTEXT_FILE target did not fail by name.'
        $env:ASRE_CONTEXT_FILE = '   '
        $defaultFile = Join-Path $Repo 'infra\environments\context.generated.json'
        if (-not (Test-Path -LiteralPath $defaultFile)) {
            Assert-Fails { Get-AsreContext -AllowSynthetic } 'infra\\environments\\context\.generated\.json' 'A blank ASRE_CONTEXT_FILE did not fall back to the default location.'
        }
        Remove-Item -LiteralPath 'Env:\ASRE_CONTEXT_FILE' -Force
        if (-not (Test-Path -LiteralPath $defaultFile)) {
            Assert-Fails { Get-AsreContext -AllowSynthetic } 'infra\\environments\\context\.generated\.json\. Save the Terraform output' 'The default location is not infra\environments\context.generated.json.'
        }
    }
    finally {
        if ($AmbientPresent) { $env:ASRE_CONTEXT_FILE = $AmbientValue } else { Remove-Item -LiteralPath 'Env:\ASRE_CONTEXT_FILE' -Force -ErrorAction SilentlyContinue }
    }

    # ---- leaf and value helpers --------------------------------------------------------------------------------------------
    $sample = @{ b = @{ z = 'last'; c = 'y'; n = $null; i = 5; f = @{ g = 'deep' } }; a = 'first'; flag = $true; list = @('x') }
    $leaves = Get-AsreContextLeaf -Context $sample
    Assert-Test ($leaves -is [System.Collections.Generic.SortedDictionary[string, string]]) 'The leaf map is not a sorted dictionary.'
    Assert-Test ((@($leaves.Keys) -join ',') -ceq 'a,b.c,b.f.g,b.z') "Only string leaves, sorted by path, were expected: $(@($leaves.Keys) -join ',')."
    Assert-Test ((@($leaves.Values) -join ',') -ceq 'first,y,deep,last') 'The leaf values do not follow the path order.'
    Assert-Test ((@((Get-AsreContextLeaf -Context $sample -Prefix 'ctx').Keys) -join ',') -ceq 'ctx.a,ctx.b.c,ctx.b.f.g,ctx.b.z') 'The prefix was not applied to every leaf.'
    Assert-Test ((Get-AsreContextLeaf -Context @{}).Count -eq 0) 'An empty context must have no leaves.'
    $loadedSynthetic = Get-AsreContext -Path $SyntheticPath -AllowSynthetic
    $allLeaves = Get-AsreContextLeaf -Context $loadedSynthetic
    Assert-Test ($allLeaves['identities.runner.client_id'] -ceq '00000071-000a-4000-8000-000000000071' -and $allLeaves.ContainsKey('targets.backup.resource_id') -and $allLeaves.ContainsKey('resource_group_ids.control')) 'The leaf map of a loaded context is incomplete.'

    Assert-Test ((Get-AsreContextValue -Context $loadedSynthetic -Path 'registry.login_server') -ceq 'acrasre8a327010f9.azurecr.io') 'A dotted value was not returned.'
    Assert-Test ((Get-AsreContextValue -Context $loadedSynthetic -Path 'subscription_id') -ceq '00000001-000a-4000-a000-000000000001') 'A top-level value was not returned.'
    Assert-Fails { Get-AsreContextValue -Context $loadedSynthetic -Path 'registry.nothing' } 'has no value at registry\.nothing' 'A missing leaf was returned.'
    Assert-Fails { Get-AsreContextValue -Context $loadedSynthetic -Path 'registry' } 'has no string value at registry' 'An object was returned as a string.'
    Assert-Fails { Get-AsreContextValue -Context $loadedSynthetic -Path 'subscription_id.deeper' } 'has no value at subscription_id\.deeper' 'A path through a string leaf was followed.'
    Assert-Fails { Get-AsreContextValue -Context (Get-AsreContext -Path (Write-TestText 'null-endpoint.json' (New-MutantJson @{ set = @{ 'agent.endpoint' = $null } })) -AllowSynthetic) -Path 'agent.endpoint' } 'has no string value at agent\.endpoint' 'A null leaf was returned as a string.'
    Assert-Fails { Get-AsreContextValue -Context $loadedSynthetic -Path 'registry..name' } 'has no value at registry\.\.name' 'An empty path segment was followed.'

    # ---- synthetic detection -----------------------------------------------------------------------------------------------
    foreach ($sentinel in @('00000123-000a-4000-a000-000000000123', '0000000000000000000000000000007a',
            'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai', 'https://asre-demo-cus--9a8b7c6d.5e4f3a2b.centralus.azuresre.ai', 'Host x.0a1b2c3d.4e5f6a7b.y',
            'https://example-instance.service-now.com', 'https://alt-instance.service-now.com')) {
        Assert-Test (Test-AsreSyntheticContext -Context @{ leaf = $sentinel }) "Sentinel $sentinel was not recognized."
        Assert-Test (Test-AsreSyntheticContext -Context @{ outer = @{ inner = $sentinel } }) "A nested sentinel $sentinel was not recognized."
    }
    foreach ($ordinary in @('c0ffee00-1234-4abc-8def-0123456789ab', 'https://contoso-dev.service-now.com', 'https://example-instance.service-now.com/path',
            'x00000123-000a-4000-a000-000000000123', '00000ABC-000a-4000-a000-000000000123', '0000000000000000000000000000007a0',
            'https://asre-demo-cus--abc123.zyx987.centralus.azuresre.ai', 'acrasre1234567890', '')) {
        Assert-Test (-not (Test-AsreSyntheticContext -Context @{ leaf = $ordinary })) "Ordinary value '$ordinary' was flagged as synthetic."
    }
    Assert-Test (-not (Test-AsreSyntheticContext -Context @{ number = 5; flag = $true; none = $null; list = @('0000000000000000000000000000007a') })) 'Non-string values and lists must be ignored.'

    # ---- Assert-AsreSubscription -------------------------------------------------------------------------------------------
    $sub = '00000001-000a-4000-a000-000000000001'
    $tenant = '00000002-000a-4000-9000-000000000002'
    $ctx = @{ subscription_id = $sub; tenant_id = $tenant }
    function Set-Az { param([int]$Exit = 0, $Reply) $global:AzExit = $Exit; $global:AzReply = $Reply; $global:AzCalls.Clear() }
    function New-Account { param([string]$Id = $sub, [string]$TenantId = $tenant, [string]$State = 'Enabled') (@{ id = $Id; tenantId = $TenantId; state = $State; name = 'any' } | ConvertTo-Json -Compress) }

    Set-Az -Reply (New-Account)
    Assert-Test ($null -eq (Assert-AsreSubscription -Context $ctx)) 'A matching enabled account was refused.'
    Assert-Test ($global:AzCalls.Count -eq 1 -and $global:AzCalls[0] -ceq "account show --subscription $sub --output json --only-show-errors") "The account check used an unexpected command: $($global:AzCalls -join '; ')"
    Set-Az -Reply (@{ id = $sub; tenantId = $tenant; state = 'Enabled'; user = @{ name = 'u'; type = 'user' } } | ConvertTo-Json -Depth 4)
    Assert-Test ($null -eq (Assert-AsreSubscription -Context $ctx)) 'Multi-line account JSON was refused.'
    Set-Az -Reply @((New-Account) -split '(?<=,)')
    Assert-Test ($null -eq (Assert-AsreSubscription -Context $ctx -ErrorAction Stop)) 'Account JSON split into several output lines was refused.'

    Set-Az -Exit 1 -Reply 'ERROR: confidential-detail-1234'
    $failure = $null
    try { Assert-AsreSubscription -Context $ctx } catch { $failure = $_.Exception.Message }
    Assert-Test ($failure -cmatch 'cannot read the deployment subscription; sign in' -and $failure -cnotmatch 'confidential|00000001') 'A failed account read was not reported with a fixed message.'
    Set-Az -Reply 'this is not json'
    Assert-Fails { Assert-AsreSubscription -Context $ctx } 'returned an unreadable account' 'Unreadable account output was accepted.'
    foreach ($reply in @('null', '[]', '[{"id":"x"}]', '"text"', '5', '{}')) {
        Set-Az -Reply $reply
        $shown = $reply
        Assert-Fails { Assert-AsreSubscription -Context $ctx } 'not the enabled deployment subscription and tenant' "Account output $shown was accepted."
    }
    Set-Az -Reply $null
    Assert-Fails { Assert-AsreSubscription -Context $ctx } 'unreadable account|not the enabled deployment subscription' 'Empty account output was accepted.'
    foreach ($case in @(
            @{ Name = 'another subscription'; Reply = (New-Account -Id '00000009-000a-4000-a000-000000000009') },
            @{ Name = 'another tenant'; Reply = (New-Account -TenantId '00000009-000a-4000-9000-000000000009') },
            @{ Name = 'a disabled subscription'; Reply = (New-Account -State 'Disabled') },
            @{ Name = 'a warned subscription'; Reply = (New-Account -State 'Warned') },
            @{ Name = 'a lowercase state'; Reply = (New-Account -State 'enabled') },
            @{ Name = 'an uppercase subscription id'; Reply = (New-Account -Id $sub.ToUpperInvariant()) },
            @{ Name = 'an uppercase tenant id'; Reply = (New-Account -TenantId $tenant.ToUpperInvariant()) },
            @{ Name = 'a subscription id with a trailing space'; Reply = (New-Account -Id "$sub ") },
            @{ Name = 'a missing id'; Reply = '{"tenantId":"' + $tenant + '","state":"Enabled"}' },
            @{ Name = 'a missing tenant'; Reply = '{"id":"' + $sub + '","state":"Enabled"}' },
            @{ Name = 'a missing state'; Reply = '{"id":"' + $sub + '","tenantId":"' + $tenant + '"}' },
            @{ Name = 'a numeric id'; Reply = '{"id":1,"tenantId":"' + $tenant + '","state":"Enabled"}' })) {
        Set-Az -Reply $case.Reply
        $label = $case.Name
        Assert-Fails { Assert-AsreSubscription -Context $ctx } 'not the enabled deployment subscription and tenant' "Account output with $label was accepted."
    }
    # Every check is read-only and scoped to the context subscription; the CLI default subscription is never trusted or changed.
    Set-Az -Reply (New-Account -Id '00000009-000a-4000-a000-000000000009')
    try { Assert-AsreSubscription -Context $ctx } catch { $null = $_ }
    Assert-Test ($global:AzCalls.Count -eq 1 -and $global:AzCalls[0] -cmatch '\Aaccount show --subscription ' -and -not @($global:AzCalls | Where-Object { $_ -cmatch 'account set|login|logout' })) 'The account check issued a state-changing Azure CLI command.'

    Assert-Test ($AmbientPresent -eq (Test-Path -LiteralPath 'Env:\ASRE_CONTEXT_FILE') -and (-not $AmbientPresent -or $env:ASRE_CONTEXT_FILE -ceq $AmbientValue)) 'The test altered ASRE_CONTEXT_FILE.'
}
finally {
    Remove-Item -LiteralPath 'Function:\global:az' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Function:\az' -Force -ErrorAction SilentlyContinue
    if ($AmbientPresent) { $env:ASRE_CONTEXT_FILE = $AmbientValue } else { Remove-Item -LiteralPath 'Env:\ASRE_CONTEXT_FILE' -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "Deployment context loader fixtures passed ($script:AssertionCount assertions); no Azure or terraform command was executed."
