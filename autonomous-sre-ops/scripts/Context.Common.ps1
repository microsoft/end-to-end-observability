# Loads and validates the asre-context/v1 deployment context for workstation scripts.
#
# The document is the Terraform output 'context' (saved as infra/environments/context.generated.json,
# which is git-ignored). It stores primitives only; every resource ID is derived here from names.
# operator_object_id, servicenow, identities.bootstrap and agent.endpoint may be omitted or null.
# The returned hashtable always has operator_object_id, servicenow and agent.endpoint (null when
# absent) because a missing hashtable key is a terminating error under StrictMode; identities.bootstrap
# exists only when the document supplies it, so test it with ContainsKey.
#
# A context that carries synthetic test values is refused unless -AllowSynthetic is given. Deployment
# and operations scripts must never pass -AllowSynthetic.

function ConvertFrom-AsreJsonElement {
    param($Element)
    switch -CaseSensitive ([string]$Element.ValueKind) {
        'Object' {
            $result = [hashtable]::new([System.StringComparer]::Ordinal)
            foreach ($property in $Element.EnumerateObject()) {
                if ($result.ContainsKey($property.Name)) { throw 'The deployment context has a duplicate key.' }
                $result[$property.Name] = ConvertFrom-AsreJsonElement -Element $property.Value
            }
            return $result
        }
        'Array' {
            $items = [System.Collections.Generic.List[object]]::new()
            foreach ($item in $Element.EnumerateArray()) { $items.Add((ConvertFrom-AsreJsonElement -Element $item)) }
            return , $items.ToArray()
        }
        'String' { return $Element.GetString() }
        'Number' { return $Element.GetRawText() -as [double] }
        'True' { return $true }
        'False' { return $false }
        default { return $null }
    }
}

function ConvertFrom-AsreStrictJson {
    # RFC 8259 only: no comments, no trailing commas, no duplicate keys, case-sensitive keys.
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Json)
    $options = [System.Text.Json.JsonDocumentOptions]::new()
    $options.MaxDepth = 16
    try {
        $document = [System.Text.Json.JsonDocument]::Parse($Json, $options)
        try { return ConvertFrom-AsreJsonElement -Element $document.RootElement }
        finally { $document.Dispose() }
    }
    catch {
        if ($_.Exception.Message -ceq 'The deployment context has a duplicate key.') { throw }
        throw 'The deployment context is not valid JSON.'
    }
}

function Assert-AsreContextKeys {
    param($Value, [string]$Path, [string[]]$Required, [string[]]$Optional = @())
    if ($Value -isnot [System.Collections.IDictionary]) { throw "Invalid deployment context at ${Path}: expected an object." }
    $keys = @($Value.Keys | ForEach-Object { [string]$_ })
    $missing = @($Required | Where-Object { $keys -cnotcontains $_ })
    if ($missing.Count) { throw "Invalid deployment context at ${Path}: missing $($missing -join ', ')." }
    $unknown = @($keys | Where-Object { ($Required -cnotcontains $_) -and ($Optional -cnotcontains $_) })
    if ($unknown.Count) { throw "Invalid deployment context at ${Path}: unknown $(($unknown | Sort-Object) -join ', ')." }
}

function Assert-AsreContextMatch {
    param($Value, [string]$Path, [string]$Pattern)
    if ($Value -isnot [string] -or -not [regex]::IsMatch($Value, "\A(?:$Pattern)\z")) {
        throw "Invalid deployment context at ${Path}: unexpected form."
    }
    return $Value
}

function Assert-AsreContextEquals {
    param($Value, [string]$Path, [string]$Expected)
    if ($Value -isnot [string] -or $Value -cne $Expected) { throw "Invalid deployment context at ${Path}: unexpected value." }
    return $Value
}

function ConvertTo-AsreContext {
    # Validates context JSON text and returns the context with every derived resource ID added.
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Json)

    $guid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    $sysid = '[0-9a-f]{32}'
    $groupNames = @('control', 'linux', 'patch', 'backup')
    $roles = @('linux', 'patch', 'backup')
    $identityKeys = @('agent', 'runner', 'collector', 'pull')
    $addresses = @{ linux = '10.42.0.10'; patch = '10.42.0.11'; backup = '10.42.0.12' }

    if ([System.Text.Encoding]::UTF8.GetByteCount($Json) -gt 65536) { throw 'The deployment context is too large.' }
    $root = ConvertFrom-AsreStrictJson -Json $Json
    Assert-AsreContextKeys -Value $root -Path '$' -Optional @('operator_object_id', 'servicenow') -Required @(
        'schema', 'subscription_id', 'tenant_id', 'location', 'name_suffix', 'resource_groups', 'log_analytics',
        'registry', 'key_vault', 'storage', 'recovery_vault', 'identities', 'targets', 'agent')
    $null = Assert-AsreContextEquals -Value $root['schema'] -Path '$.schema' -Expected 'asre-context/v1'
    $sub = Assert-AsreContextMatch -Value $root['subscription_id'] -Path '$.subscription_id' -Pattern $guid
    $tenant = Assert-AsreContextMatch -Value $root['tenant_id'] -Path '$.tenant_id' -Pattern $guid
    $null = Assert-AsreContextEquals -Value $root['location'] -Path '$.location' -Expected 'centralus'
    $suffix = Assert-AsreContextMatch -Value $root['name_suffix'] -Path '$.name_suffix' -Pattern '[0-9a-f]{10}'
    $hash = [System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes("${sub}:asre-demo"))
    $derivedSuffix = [System.Convert]::ToHexString($hash).ToLowerInvariant().Substring(0, 10)
    if ($suffix -cne $derivedSuffix) { throw 'Invalid deployment context at $.name_suffix: not derived from the subscription.' }
    $operator = $null
    if ($root.ContainsKey('operator_object_id') -and $null -ne $root['operator_object_id']) {
        $operator = Assert-AsreContextMatch -Value $root['operator_object_id'] -Path '$.operator_object_id' -Pattern $guid
    }

    $groups = $root['resource_groups']
    Assert-AsreContextKeys -Value $groups -Path '$.resource_groups' -Required $groupNames
    foreach ($group in $groupNames) {
        $null = Assert-AsreContextEquals -Value $groups[$group] -Path ('$.resource_groups.' + $group) -Expected "rg-asre-$group-cus"
    }
    $base = "/subscriptions/$sub/resourceGroups"
    $control = "$base/$($groups['control'])"
    $groupIds = @{}
    foreach ($group in $groupNames) { $groupIds[$group] = "$base/$($groups[$group])" }

    $law = $root['log_analytics']
    Assert-AsreContextKeys -Value $law -Path '$.log_analytics' -Required @('name', 'customer_id')
    $null = Assert-AsreContextEquals -Value $law['name'] -Path '$.log_analytics.name' -Expected 'law-asre-cus'
    $logAnalytics = @{
        name        = $law['name']
        customer_id = Assert-AsreContextMatch -Value $law['customer_id'] -Path '$.log_analytics.customer_id' -Pattern $guid
        resource_id = "$control/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
    }

    $registryRoot = $root['registry']
    Assert-AsreContextKeys -Value $registryRoot -Path '$.registry' -Required @('name', 'login_server')
    $registry = @{
        name         = Assert-AsreContextEquals -Value $registryRoot['name'] -Path '$.registry.name' -Expected "acrasre$suffix"
        login_server = Assert-AsreContextEquals -Value $registryRoot['login_server'] -Path '$.registry.login_server' -Expected "acrasre$suffix.azurecr.io"
    }

    $vaultRoot = $root['key_vault']
    Assert-AsreContextKeys -Value $vaultRoot -Path '$.key_vault' -Required @('name', 'uri')
    $keyVault = @{
        name = Assert-AsreContextEquals -Value $vaultRoot['name'] -Path '$.key_vault.name' -Expected "kv-asre-$suffix"
        uri  = Assert-AsreContextEquals -Value $vaultRoot['uri'] -Path '$.key_vault.uri' -Expected "https://kv-asre-$suffix.vault.azure.net/"
    }

    $storageRoot = $root['storage']
    Assert-AsreContextKeys -Value $storageRoot -Path '$.storage' -Required @('account_name', 'blob_endpoint', 'table_endpoint')
    $storage = @{
        account_name   = Assert-AsreContextEquals -Value $storageRoot['account_name'] -Path '$.storage.account_name' -Expected "stasre$suffix"
        blob_endpoint  = Assert-AsreContextEquals -Value $storageRoot['blob_endpoint'] -Path '$.storage.blob_endpoint' -Expected "https://stasre$suffix.blob.core.windows.net"
        table_endpoint = Assert-AsreContextEquals -Value $storageRoot['table_endpoint'] -Path '$.storage.table_endpoint' -Expected "https://stasre$suffix.table.core.windows.net"
    }

    $vaultGroup = $root['recovery_vault']
    Assert-AsreContextKeys -Value $vaultGroup -Path '$.recovery_vault' -Required @('name', 'resource_group')
    $null = Assert-AsreContextEquals -Value $vaultGroup['name'] -Path '$.recovery_vault.name' -Expected 'rsv-asre-cus'
    $null = Assert-AsreContextEquals -Value $vaultGroup['resource_group'] -Path '$.recovery_vault.resource_group' -Expected $groups['backup']
    $recoveryVault = @{
        name           = $vaultGroup['name']
        resource_group = $vaultGroup['resource_group']
        resource_id    = "$base/$($vaultGroup['resource_group'])/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"
    }

    $identityRoot = $root['identities']
    Assert-AsreContextKeys -Value $identityRoot -Path '$.identities' -Required $identityKeys -Optional @('bootstrap')
    $identities = @{}
    foreach ($key in ($identityKeys + @('bootstrap'))) {
        if (-not $identityRoot.ContainsKey($key)) { continue }
        if ($key -ceq 'bootstrap' -and $null -eq $identityRoot[$key]) { continue }
        $item = $identityRoot[$key]
        Assert-AsreContextKeys -Value $item -Path ('$.identities.' + $key) -Required @('client_id', 'principal_id')
        $identities[$key] = @{
            name         = "id-asre-$key-cus"
            resource_id  = "$control/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-$key-cus"
            client_id    = Assert-AsreContextMatch -Value $item['client_id'] -Path ('$.identities.' + $key + '.client_id') -Pattern $guid
            principal_id = Assert-AsreContextMatch -Value $item['principal_id'] -Path ('$.identities.' + $key + '.principal_id') -Pattern $guid
        }
    }

    $targetRoot = $root['targets']
    Assert-AsreContextKeys -Value $targetRoot -Path '$.targets' -Required $roles
    $targets = @{}
    foreach ($role in $roles) {
        $item = $targetRoot[$role]
        Assert-AsreContextKeys -Value $item -Path ('$.targets.' + $role) -Required @('name', 'resource_group', 'address')
        $null = Assert-AsreContextEquals -Value $item['name'] -Path ('$.targets.' + $role + '.name') -Expected "vm-asre-$role-cus"
        $null = Assert-AsreContextEquals -Value $item['resource_group'] -Path ('$.targets.' + $role + '.resource_group') -Expected "rg-asre-$role-cus"
        $null = Assert-AsreContextEquals -Value $item['address'] -Path ('$.targets.' + $role + '.address') -Expected $addresses[$role]
        $targets[$role] = @{
            name           = $item['name']
            resource_group = $item['resource_group']
            address        = $item['address']
            resource_id    = "$base/$($item['resource_group'])/providers/Microsoft.Compute/virtualMachines/$($item['name'])"
        }
    }

    $agentRoot = $root['agent']
    Assert-AsreContextKeys -Value $agentRoot -Path '$.agent' -Required @('name') -Optional @('endpoint')
    $null = Assert-AsreContextEquals -Value $agentRoot['name'] -Path '$.agent.name' -Expected 'asre-demo-cus'
    $endpoint = $null
    if ($agentRoot.ContainsKey('endpoint') -and $null -ne $agentRoot['endpoint']) {
        $endpoint = Assert-AsreContextMatch -Value $agentRoot['endpoint'] -Path '$.agent.endpoint' `
            -Pattern 'https://asre-demo-cus--[a-z0-9]+\.[a-z0-9]+\.centralus\.azuresre\.ai'
    }
    $agent = @{
        name        = $agentRoot['name']
        endpoint    = $endpoint
        resource_id = "$control/providers/Microsoft.App/agents/asre-demo-cus"
    }

    $servicenow = $null
    if ($root.ContainsKey('servicenow') -and $null -ne $root['servicenow']) {
        $raw = $root['servicenow']
        Assert-AsreContextKeys -Value $raw -Path '$.servicenow' -Required @(
            'origin', 'api_base', 'client_id', 'facade_client_id', 'assignment_group', 'configuration_items')
        $items = $raw['configuration_items']
        Assert-AsreContextKeys -Value $items -Path '$.servicenow.configuration_items' -Required $roles
        $configurationItems = @{}
        foreach ($role in $roles) {
            $configurationItems[$role] = Assert-AsreContextMatch -Value $items[$role] -Path ('$.servicenow.configuration_items.' + $role) -Pattern $sysid
        }
        $servicenow = @{
            origin              = Assert-AsreContextMatch -Value $raw['origin'] -Path '$.servicenow.origin' -Pattern 'https://[a-z0-9-]+\.service-now\.com'
            api_base            = Assert-AsreContextMatch -Value $raw['api_base'] -Path '$.servicenow.api_base' -Pattern '/api/x_[0-9a-z]+_asre/case_adapter/v1'
            client_id           = Assert-AsreContextMatch -Value $raw['client_id'] -Path '$.servicenow.client_id' -Pattern $sysid
            facade_client_id    = Assert-AsreContextMatch -Value $raw['facade_client_id'] -Path '$.servicenow.facade_client_id' -Pattern $sysid
            assignment_group    = Assert-AsreContextMatch -Value $raw['assignment_group'] -Path '$.servicenow.assignment_group' -Pattern $sysid
            configuration_items = $configurationItems
        }
    }

    return @{
        schema             = 'asre-context/v1'
        subscription_id    = $sub
        tenant_id          = $tenant
        location           = 'centralus'
        name_suffix        = $suffix
        operator_object_id = $operator
        resource_groups    = @{ control = $groups['control']; linux = $groups['linux']; patch = $groups['patch']; backup = $groups['backup'] }
        resource_group_ids = $groupIds
        log_analytics      = $logAnalytics
        registry           = $registry
        key_vault          = $keyVault
        storage            = $storage
        recovery_vault     = $recoveryVault
        identities         = $identities
        targets            = $targets
        agent              = $agent
        servicenow         = $servicenow
    }
}

function Get-AsreContext {
    # Path precedence: -Path, then $env:ASRE_CONTEXT_FILE, then infra/environments/context.generated.json.
    [CmdletBinding()]
    param([string]$Path, [switch]$AllowSynthetic)
    if ([string]::IsNullOrWhiteSpace($Path)) { $Path = $env:ASRE_CONTEXT_FILE }
    if ([string]::IsNullOrWhiteSpace($Path)) {
        $Path = Join-Path (Split-Path $PSScriptRoot -Parent) 'infra\environments\context.generated.json'
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not [System.IO.File]::Exists($full)) {
        throw "The deployment context file was not found: $full. Save the Terraform output 'context' there after apply."
    }
    $buffer = [byte[]]::new(65537)
    $count = 0
    try {
        $stream = [System.IO.File]::Open($full, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        try {
            while ($count -lt $buffer.Length) {
                $read = $stream.Read($buffer, $count, $buffer.Length - $count)
                if ($read -le 0) { break }
                $count += $read
            }
        }
        finally { $stream.Dispose() }
    }
    catch { throw "The deployment context file cannot be read: $full" }
    if ($count -gt 65536) { throw 'The deployment context is too large.' }
    try { $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($buffer, 0, $count) }
    catch { throw 'The deployment context file is not UTF-8.' }
    $context = ConvertTo-AsreContext -Json $text
    if (-not $AllowSynthetic -and (Test-AsreSyntheticContext -Context $context)) {
        throw 'The deployment context carries synthetic test values; use the Terraform output of the real deployment.'
    }
    return $context
}

function Get-AsreContextLeaf {
    # Flattens every string value to a dotted path (for example identities.runner.client_id), sorted by path.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context, [string]$Prefix = '')
    $leaves = [System.Collections.Generic.SortedDictionary[string, string]]::new([System.StringComparer]::Ordinal)
    foreach ($key in $Context.Keys) {
        $path = if ($Prefix) { "$Prefix.$key" } else { [string]$key }
        $value = $Context[$key]
        if ($value -is [System.Collections.IDictionary]) {
            foreach ($entry in (Get-AsreContextLeaf -Context $value -Prefix $path).GetEnumerator()) { $leaves[$entry.Key] = $entry.Value }
        }
        elseif ($value -is [string]) { $leaves[$path] = $value }
    }
    return , $leaves
}

function Get-AsreContextValue {
    # Returns the string at a dotted path of the context, or throws when the path is not a string leaf.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context, [Parameter(Mandatory)][string]$Path)
    $current = $Context
    foreach ($name in $Path.Split('.')) {
        if ($current -isnot [System.Collections.IDictionary] -or -not $current.Contains($name)) {
            throw "The deployment context has no value at $Path."
        }
        $current = $current[$name]
    }
    if ($current -isnot [string]) { throw "The deployment context has no string value at $Path." }
    return $current
}

function Test-AsreSyntheticContext {
    # True when any value is one of the committed synthetic sentinels (test GUIDs, endpoint, ServiceNow ids).
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context)
    $patterns = @(
        '\A00000[0-9]{3}-000a-',
        '\A0{24}[0-9a-f]{8}\z',
        '[.-](?:0a1b2c3d\.4e5f6a7b|9a8b7c6d\.5e4f3a2b)\.',
        '\Ahttps://(?:example|alt)-instance\.service-now\.com\z')
    foreach ($value in (Get-AsreContextLeaf -Context $Context).Values) {
        foreach ($pattern in $patterns) { if ([regex]::IsMatch($value, $pattern)) { return $true } }
    }
    return $false
}

function Assert-AsreSubscription {
    # The signed-in Azure CLI account must be the context subscription and tenant; never trusts the default subscription.
    [CmdletBinding()]
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context)
    $raw = & az account show --subscription $Context.subscription_id --output json --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI cannot read the deployment subscription; sign in and check access.' }
    try { $account = (@($raw) -join "`n") | ConvertFrom-Json -AsHashtable -DateKind String }
    catch { throw 'Azure CLI returned an unreadable account.' }
    if ($account -isnot [System.Collections.IDictionary] -or
        -not $account.Contains('id') -or $account['id'] -cne $Context.subscription_id -or
        -not $account.Contains('tenantId') -or $account['tenantId'] -cne $Context.tenant_id -or
        -not $account.Contains('state') -or $account['state'] -cne 'Enabled') {
        throw 'The Azure CLI account is not the enabled deployment subscription and tenant of the context.'
    }
}
