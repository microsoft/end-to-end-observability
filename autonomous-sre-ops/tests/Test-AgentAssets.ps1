#Requires -Version 7.5
# Offline tests for the agent-asset render step (scripts/AgentAssets.Common.ps1 and Render-AgentAssets.ps1).
# Nothing here uses the network or the Azure CLI; fixture trees live under the temp folder.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
. (Join-Path $repo 'scripts\AgentAssets.Common.ps1')
$script:assertions = 0

function Assert-Assets {
    param([bool]$Condition, [string]$Message)
    $script:assertions++
    if (-not $Condition) { throw $Message }
}
function Assert-Refused {
    param([scriptblock]$Action, [string]$Pattern, [string]$Message)
    $text = $null
    try { & $Action | Out-Null } catch { $text = $_.Exception.Message }
    Assert-Assets ($null -ne $text) "$Message (it was accepted)"
    Assert-Assets ($text -match $Pattern) "$Message (unexpected error: $text)"
}
function Get-Sha([byte[]]$Bytes) { [System.Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant() }
function Get-FileSha([string]$Path) { Get-Sha ([System.IO.File]::ReadAllBytes($Path)) }
function Write-Text([string]$Path, [string]$Text) {
    [void][System.IO.Directory]::CreateDirectory((Split-Path -Parent $Path))
    [System.IO.File]::WriteAllText($Path, $Text, [System.Text.UTF8Encoding]::new($false))
}
function Get-TreeFile([string]$Root) {
    $files = [ordered]@{}
    foreach ($item in (Get-ChildItem -LiteralPath $Root -Recurse -File | Sort-Object FullName)) {
        $files[[System.IO.Path]::GetRelativePath($Root, $item.FullName).Replace('\', '/')] = $item.FullName
    }
    return $files
}
function Read-Document([string]$Path) { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable }
function ConvertTo-TestContext([System.Collections.IDictionary]$Document) {
    ConvertTo-AsreContext -Json (ConvertTo-Json -InputObject $Document -Depth 10)
}
function New-RealisticDocument {
    # A valid context whose values match no sentinel shape (the real loader accepts it).
    $document = Read-Document (Join-Path $PSScriptRoot 'fixtures\context.alt.json')
    $subscription = '5b7c1e84-2f3a-4d96-8c05-a1e3f7b92d60'
    $suffix = [System.Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes("${subscription}:asre-demo"))).ToLowerInvariant().Substring(0, 10)
    $document['subscription_id'] = $subscription
    $document['tenant_id'] = '9d2e6a41-7c58-4b03-b6f9-3e8a15c07d24'
    $document['operator_object_id'] = '1f6b8c32-5a47-4e09-8d13-c92b0e7a4f58'
    $document['name_suffix'] = $suffix
    $document['log_analytics']['customer_id'] = '4a9d7e13-6b20-4c85-9f41-d27e0b5a8c63'
    $document['registry'] = @{ name = "acrasre$suffix"; login_server = "acrasre$suffix.azurecr.io" }
    $document['key_vault'] = @{ name = "kv-asre-$suffix"; uri = "https://kv-asre-$suffix.vault.azure.net/" }
    $document['storage'] = @{
        account_name = "stasre$suffix"
        blob_endpoint = "https://stasre$suffix.blob.core.windows.net"
        table_endpoint = "https://stasre$suffix.table.core.windows.net"
    }
    $guids = @('7e1a4c92-3b58-4d06-a2f7-6c80d1e95b34', '2c93f0a7-8d14-4e6b-b05a-91e7c3d28f46', 'd4b61e08-9a27-4f35-8c19-0a7e52b3d6f1',
               'f08a3d65-1c94-47b2-9e60-5d2b7a1c84e9', '63e7b2d0-a815-4c93-b7f4-18d90e5a3c27', 'a95c1f48-6e03-4d7a-8b21-c4f60d9e7a15',
               '0b3d8e72-f594-4a16-9c8d-72e1a60b5f39', 'e6a07c13-4d82-4b59-a3f0-9b15c8d2e764')
    $index = 0
    foreach ($key in @('agent', 'runner', 'collector', 'pull', 'bootstrap')) {
        $document['identities'][$key] = @{ client_id = $guids[$index % 8]; principal_id = $guids[($index + 3) % 8] }
        $index++
    }
    $document['agent']['endpoint'] = 'https://asre-demo-cus--c4e81b27.7f3a95d0.centralus.azuresre.ai'
    $document['servicenow'] = @{
        origin = 'https://tenant-fixture.service-now.com'
        api_base = '/api/x_4417_asre/case_adapter/v1'
        client_id = 'c1a2b3d4e5f60718293a4b5c6d7e8f90'
        facade_client_id = 'b7a6c5d4e3f2019887766554433221ff'
        assignment_group = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'
        configuration_items = @{
            linux = 'd1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6'
            patch = 'e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7'
            backup = 'f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8'
        }
    }
    return $document
}

$synPath = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'
$altPath = Join-Path $PSScriptRoot 'fixtures\context.alt.json'
$syn = Get-AsreContext -Path $synPath -AllowSynthetic
$alt = Get-AsreContext -Path $altPath -AllowSynthetic
$phase1Document = Read-Document $altPath
$phase1Document.Remove('servicenow')
$phase1Document.Remove('operator_object_id')
$phase1Document['agent'].Remove('endpoint')
$phase1 = ConvertTo-TestContext $phase1Document
$realDocument = New-RealisticDocument
$real = ConvertTo-TestContext $realDocument
Assert-Assets (-not (Test-AsreSyntheticContext -Context $real)) 'The realistic test context must not look synthetic.'
Assert-Assets (Test-AsreSyntheticContext -Context $alt) 'The alternative fixture is a sentinel-shaped context; the real loader refuses it.'

$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('asre-assets-test-' + [guid]::NewGuid())
[void][System.IO.Directory]::CreateDirectory($temporary)
$script:counter = 0
function New-WorkPath([string]$Name) { $script:counter++; Join-Path $temporary ("$Name-$script:counter") }

# ---------------------------------------------------------------- fixture source tree (small, offline)
$fixtureFile = [ordered]@{
    'agent/instructions/review.md'                 = (@(
            'Subscription 00000001-000a-4000-a000-000000000001.',
            'Workspace /subscriptions/00000001-000a-4000-a000-000000000001/resourcegroups/rg-asre-control-cus/providers/microsoft.operationalinsights/workspaces/law-asre-cus/providers/Microsoft.AlertsManagement/alerts/x',
            'Storage stasre8a327010f9 suffix 8a327010f9 vault https://kv-asre-8a327010f9.vault.azure.net/',
            'Linux /subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus done',
            'Nothing to replace: centralus 10.42.0.10', '') -join "`n")
    'agent/tools/tool.py'                          = "SUBSCRIPTION = `"00000001-000a-4000-a000-000000000001`"`nTENANT = '00000002-000a-4000-9000-000000000002'`n"
    'agent/knowledge/index.json'                   = "{`"entries`": [`"environment.md`"]}`n"
    'agent/hooks/review-case-policy.py'            = "CASE_ID = `"0000000000000000000000000000010a`"`nORIGIN = `"https://example-instance.service-now.com`"`nGROUP = '0000000000000000000000000000007a'`n"
    'agent/workflows/native-review.json'           = "{`"assignment_group_id`": `"0000000000000000000000000000007a`", `"case_id`": `"0000000000000000000000000000010a`"}`n"
    'contracts/case.schema.json'                   = (@(
            '{"enum": [',
            '  "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus",',
            '  "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus",',
            '  "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-backup-cus"',
            ']}', '') -join "`n")
    'config/native-incident-runtime.json'          = "{`"facade_origin`": `"https://example-instance.service-now.com`", `"facade_client_id`": `"0000000000000000000000000000005a`"}`n"
}
$fixtureFile['operations/contracts/case.schema.json'] = $fixtureFile['contracts/case.schema.json']

function New-FixtureSource {
    # Writes a fresh fixture source tree (real map + synthetic context + small assets). $Extra overrides or adds files;
    # a $null value deletes one. $Map replaces the map text.
    param([hashtable]$Extra = @{}, [string]$Map)
    $path = New-WorkPath 'source'
    [void][System.IO.Directory]::CreateDirectory((Join-Path $path 'scripts'))
    if ($Map) { Write-Text (Join-Path $path 'scripts/AgentAssets.map.json') $Map }
    else { Copy-Item -LiteralPath (Join-Path $repo 'scripts\AgentAssets.map.json') -Destination (Join-Path $path 'scripts/AgentAssets.map.json') }
    [void][System.IO.Directory]::CreateDirectory((Join-Path $path 'tests/fixtures'))
    Copy-Item -LiteralPath $synPath -Destination (Join-Path $path 'tests/fixtures/context.synthetic.json')
    $all = [ordered]@{}
    foreach ($key in $fixtureFile.Keys) { $all[$key] = $fixtureFile[$key] }
    foreach ($key in $Extra.Keys) { $all[$key] = $Extra[$key] }
    foreach ($key in $all.Keys) {
        if ($null -eq $all[$key]) { continue }
        if ($all[$key] -is [byte[]]) {
            [void][System.IO.Directory]::CreateDirectory((Split-Path -Parent (Join-Path $path $key)))
            [System.IO.File]::WriteAllBytes((Join-Path $path $key), $all[$key])
        }
        else { Write-Text (Join-Path $path $key) $all[$key] }
    }
    return $path
}

try {
    # ------------------------------------------------------------ context document and hash
    $synDocument = ConvertTo-AsreContextDocument -Context $syn
    Assert-Assets ($synDocument -cmatch '\A\{"agent":\{"endpoint":"https://asre-demo-cus--0a1b2c3d\.4e5f6a7b\.centralus\.azuresre\.ai","name":"asre-demo-cus"\},"identities":') 'The stored document must use sorted keys, be minified and keep optional parts when present.'
    Assert-Assets ($synDocument -cnotmatch '[\r\n\t]|": |, "') 'The stored document must be minified.'
    $phase1Text = ConvertTo-AsreContextDocument -Context $phase1
    Assert-Assets ($phase1Text -cnotmatch '"servicenow"|"operator_object_id"|"endpoint"') 'Null optional parts must be omitted from the stored document.'
    foreach ($context in @($syn, $alt, $phase1, $real)) {
        $text = ConvertTo-AsreContextDocument -Context $context
        $again = ConvertTo-AsreContext -Json $text
        $left = Get-AsreContextLeaf -Context $context
        $right = Get-AsreContextLeaf -Context $again
        Assert-Assets ($left.Count -eq $right.Count -and @($left.Keys | Where-Object { $left[$_] -cne $right[$_] }).Count -eq 0) 'The stored document must load back to the same context.'
    }
    Assert-Assets ((Get-AsreContextSha256 -Context $syn) -cne (Get-AsreContextSha256 -Context $alt)) 'Different contexts must hash differently.'
    Assert-Assets ((Get-AsreContextSha256 -Context $alt) -ceq (Get-AsreContextSha256 -Context (Get-AsreContext -Path $altPath -AllowSynthetic))) 'The context hash must be stable.'
    Assert-Assets ((Get-AsreContextSha256 -Context $syn) -ceq (Get-Sha ([System.Text.UTF8Encoding]::new($false).GetBytes($synDocument)))) 'The context hash must cover the canonical stored document.'

    # ------------------------------------------------------------ the real tree
    $mapDocument = Read-Document (Join-Path $repo 'scripts\AgentAssets.map.json')
    $synLeaf = Get-AsreContextLeaf -Context $syn
    $altLeaf = Get-AsreContextLeaf -Context $alt
    $ruleValue = @($mapDocument['rules'] | ForEach-Object { $synLeaf[$_] })
    $sourceFile = [ordered]@{}
    foreach ($rootRelative in $mapDocument['roots']) {
        $rootPath = Join-Path $repo $rootRelative
        if (Test-Path -LiteralPath $rootPath -PathType Leaf) { $sourceFile[$rootRelative] = $rootPath; continue }
        foreach ($item in (Get-ChildItem -LiteralPath $rootPath -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } | Sort-Object FullName)) {
            $sourceFile[$rootRelative + '/' + [System.IO.Path]::GetRelativePath($rootPath, $item.FullName).Replace('\', '/')] = $item.FullName
        }
    }
    Assert-Assets ($sourceFile.Count -gt 20) 'The real asset roots must contain the agent assets.'
    Assert-Assets (@($mapDocument['rules']).Count -eq 46 -and @($mapDocument['sentinel_patterns']).Count -eq 4) 'The frozen map shape changed.'

    # synthetic render is the identity
    $synOut = Export-AsreAgentAssets -Context $syn -OutputRoot (New-WorkPath 'syn')
    $synTree = Get-TreeFile $synOut.Root
    Assert-Assets ($synOut.FileCount -eq $sourceFile.Count -and $synTree.Count -eq $sourceFile.Count + 1 -and $synTree.Contains('render-receipt.json')) 'A render holds the assets plus the receipt only.'
    foreach ($relative in $sourceFile.Keys) {
        Assert-Assets ($synTree.Contains($relative) -and (Get-FileSha $synTree[$relative]) -ceq (Get-FileSha $sourceFile[$relative])) "The synthetic render must be byte-identical for $relative."
    }
    $receiptBytes = [System.IO.File]::ReadAllBytes($synTree['render-receipt.json'])
    Assert-Assets ($receiptBytes[0] -ne 0xEF -and -not ($receiptBytes -contains 13)) 'The receipt must be UTF-8 without a byte order mark and use LF.'
    $receipt = ConvertFrom-AsreStrictJson -Json ([System.Text.UTF8Encoding]::new($false, $true).GetString($receiptBytes))
    Assert-Assets (($receipt.Keys | Sort-Object) -join ',' -ceq 'context_sha256,files,inert,map_sha256,rendered_at,schema,source_commit,source_dirty,sources') 'The receipt layout changed.'
    Assert-Assets ($receipt['schema'] -ceq 'asre-agent-assets-receipt/v1' -and $receipt['source_dirty'] -is [bool] -and $receipt['source_commit'] -cmatch '\A(?:[0-9a-f]{40}|unknown)\z') 'The receipt identity fields are wrong.'
    Assert-Assets ($receipt['rendered_at'] -cmatch '\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\z') 'The receipt time must be UTC.'
    Assert-Assets ($receipt['map_sha256'] -ceq (Get-FileSha (Join-Path $repo 'scripts\AgentAssets.map.json'))) 'The receipt must pin the map bytes.'
    Assert-Assets ($receipt['context_sha256'] -ceq (Get-AsreContextSha256 -Context $syn)) 'The receipt must pin the canonical context.'
    Assert-Assets (($receipt['inert'] -join '|') -ceq ((@($mapDocument['inert']) | Sort-Object { $_ } -CaseSensitive) -join '|')) 'The receipt must list the inert files.'
    Assert-Assets ($receipt['sources'].Count -eq $sourceFile.Count -and $receipt['files'].Count -eq $sourceFile.Count) 'The receipt must hash every source and output.'
    foreach ($relative in $sourceFile.Keys) {
        Assert-Assets ($receipt['sources'][$relative] -ceq (Get-FileSha $sourceFile[$relative]) -and $receipt['files'][$relative] -ceq $receipt['sources'][$relative]) "The synthetic receipt hashes must match for $relative."
    }
    $verified = Assert-AsreRenderedAssets -Context $syn -Root $synOut.Root
    Assert-Assets ($verified.FileCount -eq $synOut.FileCount -and $verified.ReceiptSha256 -ceq $synOut.ReceiptSha256 -and $synOut.ReceiptSha256 -ceq (Get-Sha $receiptBytes)) 'Verification must report the same receipt hash.'

    # alternative render differs exactly where the rules apply
    $altOut = Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'alt')
    $altTree = Get-TreeFile $altOut.Root
    $utf8 = [System.Text.UTF8Encoding]::new($false, $true)
    $differing = 0
    foreach ($relative in $sourceFile.Keys) {
        $before = $utf8.GetString([System.IO.File]::ReadAllBytes($sourceFile[$relative])).Split("`n")
        $after = $utf8.GetString([System.IO.File]::ReadAllBytes($altTree[$relative])).Split("`n")
        Assert-Assets ($before.Count -eq $after.Count) "A render must not add or remove lines in $relative."
        for ($line = 0; $line -lt $before.Count; $line++) {
            $hit = @($ruleValue | Where-Object { $before[$line].IndexOf($_, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
            if ($hit) { Assert-Assets ($before[$line] -cne $after[$line]) "A line with a rule value must change in $relative."; $differing++ }
            else { Assert-Assets ($before[$line] -ceq $after[$line]) "A line without a rule value must be untouched in $relative." }
        }
        if ($relative -cnotin @($mapDocument['inert'])) {
            $lower = $utf8.GetString([System.IO.File]::ReadAllBytes($altTree[$relative])).ToLowerInvariant()
            foreach ($entry in $synLeaf.GetEnumerator()) {
                if ($altLeaf[$entry.Key] -cne $entry.Value) {
                    Assert-Assets (-not $lower.Contains($entry.Value.ToLowerInvariant())) "No synthetic-only value may survive in $relative (rule $($entry.Key))."
                }
            }
        }
    }
    Assert-Assets ($differing -gt 20) 'The alternative render must change many lines.'
    foreach ($relative in @($altTree.Keys | Where-Object { $_ -cmatch '\Aoperations/contracts/' })) {
        $twin = $relative -creplace '\Aoperations/', ''
        Assert-Assets ((Get-FileSha $altTree[$relative]) -ceq (Get-FileSha $altTree[$twin])) "operations/$twin must stay byte-identical to $twin."
    }
    $caseSchema = [System.IO.File]::ReadAllText($altTree['contracts/servicenow-case.schema.json'])
    foreach ($role in @('linux', 'patch', 'backup')) {
        Assert-Assets ($caseSchema.Contains($alt['targets'][$role]['resource_id'])) "The case contract must list the $role target of the context."
    }
    Assert-Assets (-not $caseSchema.Contains($syn['subscription_id'])) 'The case contract must not keep the synthetic subscription.'
    $altCaseText = [System.IO.File]::ReadAllText($altTree['agent/knowledge/asre-environment-v1.md'])
    Assert-Assets ($altCaseText.Contains($alt['subscription_id']) -and $altCaseText.Contains($alt['targets']['linux']['resource_id'])) 'Knowledge text must carry the context ids.'
    $altEvidence = [System.IO.File]::ReadAllText($altTree['agent/tools/review_evidence.py'])
    Assert-Assets ($altEvidence.Contains($alt['storage']['account_name']) -and $altEvidence.Contains($alt['subscription_id'])) 'The evidence tool must carry the context storage account and subscription.'
    $null = Assert-AsreRenderedAssets -Context $alt -Root $altOut.Root

    # Phase-1 context: no servicenow, no agent endpoint, no operator id
    $phase1Out = Export-AsreAgentAssets -Context $phase1 -OutputRoot (New-WorkPath 'phase1')
    $phase1Tree = Get-TreeFile $phase1Out.Root
    Assert-Assets ($phase1Out.FileCount -eq $sourceFile.Count) 'The Phase-1 context must render every asset (non-inert files need no ServiceNow, endpoint or operator id).'
    foreach ($relative in $sourceFile.Keys) {
        if ($relative -cin @($mapDocument['inert'])) { continue }
        $lower = [System.IO.File]::ReadAllText($phase1Tree[$relative]).ToLowerInvariant()
        foreach ($entry in $synLeaf.GetEnumerator()) {
            if (-not $altLeaf.ContainsKey($entry.Key) -or $altLeaf[$entry.Key] -cne $entry.Value) {
                Assert-Assets (-not $lower.Contains($entry.Value.ToLowerInvariant())) "No synthetic-only value may survive the Phase-1 render in $relative."
            }
        }
    }
    foreach ($relative in @('agent/workflows/native-review.json', 'agent/hooks/review-case-policy.py')) {
        $inertText = [System.IO.File]::ReadAllText($phase1Tree[$relative])
        Assert-Assets ($inertText.Contains('0000000000000000000000000000010a')) "$relative is inert and keeps its pilot sentinel."
    }
    $null = Assert-AsreRenderedAssets -Context $phase1 -Root $phase1Out.Root

    # a realistic (non-sentinel) context renders cleanly and leaves no sentinel anywhere outside inert files
    $realOut = Export-AsreAgentAssets -Context $real -OutputRoot (New-WorkPath 'real')
    $realTree = Get-TreeFile $realOut.Root
    $sentinel = @($mapDocument['sentinel_patterns'] | ForEach-Object { [regex]::new($_) })
    foreach ($relative in $sourceFile.Keys) {
        if ($relative -cin @($mapDocument['inert'])) { continue }
        $text = [System.IO.File]::ReadAllText($realTree[$relative])
        foreach ($expression in $sentinel) { Assert-Assets (-not $expression.IsMatch($text)) "A real render must leave no sentinel in $relative." }
    }
    Assert-Assets ([System.IO.File]::ReadAllText($realTree['agent/knowledge/asre-environment-v1.md']).Contains($real['subscription_id'])) 'The real render must carry the real subscription.'
    $null = Assert-AsreRenderedAssets -Context $real -Root $realOut.Root
    # an unrendered copy of the repository assets is refused for a non-synthetic context
    $unrendered = New-WorkPath 'unrendered'
    foreach ($relative in $sourceFile.Keys) {
        $target = Join-Path $unrendered $relative
        [void][System.IO.Directory]::CreateDirectory((Split-Path -Parent $target))
        Copy-Item -LiteralPath $sourceFile[$relative] -Destination $target
    }
    Assert-Refused { Assert-AsreRenderedAssets -Context $real -Root $unrendered } 'receipt|differs' 'An unrendered tree must be refused for a real context.'
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $synOut.Root } 'differs|receipt' 'A synthetic render must be refused for another context.'
    Assert-Refused { Assert-AsreRenderedAssets -Context $real -Root (New-WorkPath 'absent') } 'does not exist' 'A missing root must be refused.'

    # the loader alias does not change the render
    Use-AsreTestContext -Name alt | Out-Null
    try {
        $viaAlias = Export-AsreAgentAssets -Context (Get-AsreContext) -OutputRoot (New-WorkPath 'alias')
        Assert-Assets ($viaAlias.ReceiptSha256 -ne $synOut.ReceiptSha256 -and (Get-TreeFile $viaAlias.Root)['agent/tools/review_evidence.py'] -and
            (Get-FileSha (Get-TreeFile $viaAlias.Root)['agent/tools/review_evidence.py']) -ceq (Get-FileSha $altTree['agent/tools/review_evidence.py'])) 'The test alias must serve the alternative context.'
    }
    finally { Clear-AsreTestContext }

    # ------------------------------------------------------------ fixture trees: failures and guards
    $source = New-FixtureSource
    $out = Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'fixture-alt') -SourceRoot $source
    Assert-Assets ($out.FileCount -eq 8) 'The fixture tree must render its eight files.'
    $text = [System.IO.File]::ReadAllText((Join-Path $out.Root 'agent/instructions/review.md'))
    Assert-Assets ($text.Contains("Subscription $($alt['subscription_id']).")) 'An exact-case GUID must become the exact target value.'
    Assert-Assets ($text.Contains($alt['log_analytics']['resource_id'].ToLowerInvariant() + '/providers/Microsoft.AlertsManagement/alerts/x')) 'An all-lowercase match must become the lower-cased target value.'
    Assert-Assets ($text.Contains("Storage $($alt['storage']['account_name']) suffix $($alt['name_suffix']) vault $($alt['key_vault']['uri'])")) 'Longest rules must win over the shorter suffix rule.'
    Assert-Assets ($text.Contains("Linux $($alt['targets']['linux']['resource_id']) done") -and $text.Contains('centralus 10.42.0.10')) 'Targets must render and unrelated text must stay.'
    Assert-Assets ((Get-FileSha (Join-Path $out.Root 'contracts/case.schema.json')) -ceq (Get-FileSha (Join-Path $out.Root 'operations/contracts/case.schema.json'))) 'Equal sources must render to equal outputs.'
    $inertText = [System.IO.File]::ReadAllText((Join-Path $out.Root 'agent/hooks/review-case-policy.py'))
    Assert-Assets ($inertText.Contains('0000000000000000000000000000010a') -and $inertText.Contains($alt['servicenow']['origin']) -and $inertText.Contains($alt['servicenow']['assignment_group'])) 'Inert files are rendered where the context has a value and keep the pilot sentinel.'
    $same = Export-AsreAgentAssets -Context $syn -OutputRoot (New-WorkPath 'fixture-syn') -SourceRoot $source
    foreach ($relative in @('agent/instructions/review.md', 'agent/hooks/review-case-policy.py', 'config/native-incident-runtime.json')) {
        Assert-Assets ((Get-FileSha (Join-Path $same.Root $relative)) -ceq (Get-FileSha (Join-Path $source $relative))) "The synthetic render of the fixture tree must be the identity for $relative."
    }

    # letter-case rules
    foreach ($case in @(
            @('upper-case GUID', 'Subscription 00000001-000A-4000-A000-000000000001.'),
            @('mixed-case path', 'See /Subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus here.'),
            @('upper-case suffix', 'Storage STASRE8A327010F9.'))) {
        $bad = New-FixtureSource -Extra @{ 'agent/instructions/case.md' = ($case[1] + "`n") }
        Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'bad-case') -SourceRoot $bad } 'unsupported letter case' "A $($case[0]) must be refused."
        Assert-Refused { Export-AsreAgentAssets -Context $syn -OutputRoot (New-WorkPath 'bad-case-syn') -SourceRoot $bad } 'unsupported letter case' "A $($case[0]) must also be refused for the synthetic context."
    }

    # null targets
    $needsServiceNow = New-FixtureSource -Extra @{ 'agent/instructions/sn.md' = "Group 0000000000000000000000000000007a`n" }
    Assert-Refused { Export-AsreAgentAssets -Context $phase1 -OutputRoot (New-WorkPath 'null-target') -SourceRoot $needsServiceNow } 'servicenow\.assignment_group.*does not provide' 'A non-inert file that needs a null rule must be refused.'
    $needsEndpoint = New-FixtureSource -Extra @{ 'agent/instructions/ep.md' = "Endpoint https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai`n" }
    Assert-Refused { Export-AsreAgentAssets -Context $phase1 -OutputRoot (New-WorkPath 'null-endpoint') -SourceRoot $needsEndpoint } 'agent\.endpoint.*does not provide' 'A non-inert file that needs the agent endpoint must be refused when it is null.'
    $needsOperator = New-FixtureSource -Extra @{ 'agent/instructions/op.md' = "Operator 00000003-000a-4000-9000-000000000003`n" }
    Assert-Refused { Export-AsreAgentAssets -Context $phase1 -OutputRoot (New-WorkPath 'null-operator') -SourceRoot $needsOperator } 'operator_object_id.*does not provide' 'A non-inert file that needs the operator id must be refused when it is null.'
    $null = Export-AsreAgentAssets -Context $phase1 -OutputRoot (New-WorkPath 'null-inert') -SourceRoot $source
    $nullInert = Export-AsreAgentAssets -Context $phase1 -OutputRoot (New-WorkPath 'null-inert-check') -SourceRoot $source
    $kept = [System.IO.File]::ReadAllText((Join-Path $nullInert.Root 'agent/workflows/native-review.json'))
    Assert-Assets ($kept.Contains('0000000000000000000000000000007a')) 'An inert file keeps a value whose target is null.'
    $null = Export-AsreAgentAssets -Context $syn -OutputRoot (New-WorkPath 'null-syn') -SourceRoot $needsServiceNow

    # sentinel guard (non-synthetic contexts only); a sentinel the target itself contains is not a leftover
    foreach ($case in @(
            @('GUID shape', 'Object 00000057-000a-4000-a000-000000000057'),
            @('ServiceNow id shape', 'Case 0000000000000000000000000000abcd'),
            @('endpoint shape', 'Host x.0a1b2c3d.4e5f6a7b.y'),
            @('instance host', 'Instance https://alt-instance.service-now.com'))) {
        $leaky = New-FixtureSource -Extra @{ 'agent/instructions/leak.md' = ($case[1] + "`n") }
        Assert-Refused { Export-AsreAgentAssets -Context $real -OutputRoot (New-WorkPath 'sentinel') -SourceRoot $leaky } 'sentinel' "A leftover $($case[0]) must be refused."
        $null = Export-AsreAgentAssets -Context $syn -OutputRoot (New-WorkPath 'sentinel-syn') -SourceRoot $leaky
    }
    $ownValue = New-FixtureSource -Extra @{ 'agent/instructions/own.md' = "Alt subscription $($alt['subscription_id']) is the target itself`n" }
    $null = Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'own') -SourceRoot $ownValue
    Assert-Refused { Export-AsreAgentAssets -Context $real -OutputRoot (New-WorkPath 'own-real') -SourceRoot $ownValue } 'sentinel' 'A sentinel-shaped value the real context does not own must be refused.'
    $inertLeak = New-FixtureSource -Extra @{ 'agent/instructions/incident-review.md' = "Case 0000000000000000000000000000abcd`n" }
    $null = Export-AsreAgentAssets -Context $real -OutputRoot (New-WorkPath 'inert-leak') -SourceRoot $inertLeak

    # a synthetic-only value that is not sentinel-shaped (a rule removed from the map)
    $trimmed = $mapDocument.Clone()
    $trimmed['rules'] = @($mapDocument['rules'] | Where-Object { $_ -cne 'name_suffix' })
    $shortMap = New-FixtureSource -Map (ConvertTo-Json -InputObject $trimmed -Depth 5) -Extra @{ 'agent/instructions/suffix.md' = "bare 8a327010f9`n" }
    Assert-Refused { Export-AsreAgentAssets -Context $real -OutputRoot (New-WorkPath 'needle') -SourceRoot $shortMap } 'synthetic value of name_suffix' 'A synthetic-only value that the map does not cover must be refused.'

    # file kinds and map shape
    $encoded = [System.Text.UTF8Encoding]::new($false)
    $badFile = [ordered]@{
        'an unsupported extension' = @{ 'agent/tools/blob.bin' = $encoded.GetBytes('x') }
        'no extension'             = @{ 'agent/tools/Makefile' = 'x' }
        'a byte order mark'        = @{ 'agent/instructions/bom.md' = [byte[]](0xEF, 0xBB, 0xBF, 0x61) }
        'invalid UTF-8'            = @{ 'agent/instructions/bad.md' = [byte[]](0x61, 0xFF, 0xFE) }
        'a NUL byte'               = @{ 'agent/instructions/nul.md' = [byte[]](0x61, 0x00, 0x62) }
        'an odd file name'         = @{ 'agent/instructions/with space.md' = 'x' }
    }
    foreach ($name in $badFile.Keys) {
        $broken = New-FixtureSource -Extra $badFile[$name]
        $target = New-WorkPath 'broken'
        Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot $target -SourceRoot $broken } 'asset|path' "A tree with $name must be refused."
        Assert-Assets (-not (Test-Path -LiteralPath $target)) "A refused render must leave nothing behind ($name)."
    }
    $missingRoot = New-FixtureSource
    Remove-Item -LiteralPath (Join-Path $missingRoot 'contracts') -Recurse -Force
    Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'missing-root') -SourceRoot $missingRoot } 'root contracts is missing' 'A missing asset root must be refused.'
    $withCache = New-FixtureSource -Extra @{ 'agent/tools/__pycache__/tool.cpython-312.pyc' = [byte[]](1, 2, 3) }
    $cacheOut = Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'cache') -SourceRoot $withCache
    Assert-Assets ($cacheOut.FileCount -eq 8 -and -not (Test-Path -LiteralPath (Join-Path $cacheOut.Root 'agent/tools/__pycache__'))) 'Interpreter caches are skipped, never rendered.'
    foreach ($case in @(
            @('an unknown key', { param($m) $m['surprise'] = 1; $m }),
            @('a wrong format', { param($m) $m['format'] = 'other'; $m }),
            @('a rule without a source value', { param($m) $m['rules'] = @($m['rules']) + @('identities.nobody.client_id'); $m }),
            @('a repeated rule', { param($m) $m['rules'] = @($m['rules']) + @('tenant_id'); $m }),
            @('a parent path root', { param($m) $m['roots'] = @($m['roots']) + @('../outside'); $m }),
            @('an invalid sentinel pattern', { param($m) $m['sentinel_patterns'] = @('(unclosed'); $m }))) {
        $copy = Read-Document (Join-Path $repo 'scripts\AgentAssets.map.json')
        $mapText = ConvertTo-Json -InputObject (& $case[1] $copy) -Depth 5
        $badMap = New-FixtureSource -Map $mapText
        Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'bad-map') -SourceRoot $badMap } 'map|rule|path' "A map with $($case[0]) must be refused."
    }

    # new files under agent/ are rendered like any other
    $newKind = New-FixtureSource -Extra @{
        'agent/knowledge/azure-monitor-observe.md'   = "Observe subscription 00000001-000a-4000-a000-000000000001`n"
        'agent/tools/azure-monitor-observe.json'     = "{`"workspace`": `"00000062-000a-4000-8000-000000000062`"}`n"
    }
    $newOut = Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'new-kind') -SourceRoot $newKind
    Assert-Assets ([System.IO.File]::ReadAllText((Join-Path $newOut.Root 'agent/knowledge/azure-monitor-observe.md')).Contains($alt['subscription_id']) -and
        [System.IO.File]::ReadAllText((Join-Path $newOut.Root 'agent/tools/azure-monitor-observe.json')).Contains($alt['log_analytics']['customer_id'])) 'New asset files must be rendered like any other.'

    # ------------------------------------------------------------ output protections
    $guard = New-FixtureSource
    $before = (Get-TreeFile $guard).Keys.Count
    Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot $out.Root -SourceRoot $source } 'already exists' 'An existing output directory must be refused.'
    foreach ($inside in @('agent', 'agent/new', 'contracts', 'scripts', 'tests/fixtures', 'operations/contracts/x', 'newfolder')) {
        Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot (Join-Path $guard $inside) -SourceRoot $guard } 'tracked source tree' "Output inside $inside must be refused."
    }
    Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot $guard -SourceRoot $guard } 'tracked source tree|already exists' 'Output equal to the source root must be refused.'
    Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot (Split-Path -Parent $guard) -SourceRoot $guard } 'already exists|around' 'Output around the source root must be refused.'
    Assert-Assets ((Get-TreeFile $guard).Keys.Count -eq $before) 'A refused render must not write into the source tree.'
    $ignored = Export-AsreAgentAssets -Context $alt -OutputRoot (Join-Path $guard 'out/custom') -SourceRoot $guard
    Assert-Assets (Test-Path -LiteralPath (Join-Path $ignored.Root 'render-receipt.json')) 'The git-ignored out folder is an allowed output.'
    $defaultOut = Export-AsreAgentAssets -Context $alt -SourceRoot $guard
    Assert-Assets ($defaultOut.Root -match '[\\/]out[\\/]agent-assets[\\/]\d{8}T\d{6}Z-[0-9a-f]{8}\z' -and
        (Split-Path -Leaf $defaultOut.Root).EndsWith((Get-AsreContextSha256 -Context $alt).Substring(0, 8))) 'The default output must be out/agent-assets/<UTC stamp>-<8 hex of the context hash>.'
    Assert-Refused { Export-AsreAgentAssets -Context $alt -OutputRoot '' -SourceRoot (Join-Path $guard 'missing') } 'map was not found|empty' 'A missing source root must be refused.'

    # ------------------------------------------------------------ tamper tests
    function New-Rendered([string]$Name = 'tamper') {
        (Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath $Name) -SourceRoot $source).Root
    }
    $good = New-Rendered
    $passed = Assert-AsreRenderedAssets -Context $alt -Root $good -SourceRoot $source
    Assert-Assets ($passed.FileCount -eq 8) 'An untouched render must verify.'

    $flip = New-Rendered
    $bytes = [System.IO.File]::ReadAllBytes((Join-Path $flip 'agent/tools/tool.py'))
    $bytes[0] = [byte]($bytes[0] -bxor 1)
    [System.IO.File]::WriteAllBytes((Join-Path $flip 'agent/tools/tool.py'), $bytes)
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $flip -SourceRoot $source } 'differs from a fresh render' 'A flipped byte must be detected.'

    $added = New-Rendered
    Write-Text (Join-Path $added 'agent/instructions/extra.md') "extra`n"
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $added -SourceRoot $source } 'unexpected file' 'An added file must be detected.'
    $addedTop = New-Rendered
    Write-Text (Join-Path $addedTop 'notes.txt') "extra`n"
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $addedTop -SourceRoot $source } 'unexpected file' 'An added top-level file must be detected.'

    $deleted = New-Rendered
    Remove-Item -LiteralPath (Join-Path $deleted 'agent/tools/tool.py')
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $deleted -SourceRoot $source } 'missing' 'A deleted file must be detected.'

    $noReceipt = New-Rendered
    Remove-Item -LiteralPath (Join-Path $noReceipt 'render-receipt.json')
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $noReceipt -SourceRoot $source } 'no render receipt' 'A missing receipt must be detected.'

    Assert-Refused { Assert-AsreRenderedAssets -Context $real -Root $good -SourceRoot $source } 'differs from a fresh render' 'A render must be refused for another context.'
    Assert-Refused { Assert-AsreRenderedAssets -Context $syn -Root $good -SourceRoot $source } 'differs from a fresh render' 'An alternative render must be refused for the synthetic context.'

    # a different map with identical output bytes: only the receipt can tell
    $inertMap = $mapDocument.Clone()
    $inertMap['inert'] = @($mapDocument['inert'] | Where-Object { $_ -cne 'config/native-incident-runtime.json' })
    $otherMap = New-FixtureSource -Map (ConvertTo-Json -InputObject $inertMap -Depth 5)
    $sameBytes = Export-AsreAgentAssets -Context $alt -OutputRoot (New-WorkPath 'other-map') -SourceRoot $otherMap
    foreach ($relative in (Get-TreeFile $good).Keys) {
        if ($relative -ceq 'render-receipt.json') { continue }
        Assert-Assets ((Get-FileSha (Join-Path $good $relative)) -ceq (Get-FileSha (Join-Path $sameBytes.Root $relative))) 'The two maps must render identical bytes for this check to isolate the receipt.'
    }
    Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $good -SourceRoot $otherMap } 'different asset map' 'A receipt made with another map must be refused.'

    $receiptFile = Join-Path $good 'render-receipt.json'
    $receiptText = [System.IO.File]::ReadAllText($receiptFile)
    foreach ($case in @(
            @('a changed context hash', { param($t, $d) $t.Replace($d['context_sha256'], ('0' * 64)) }, 'different deployment context'),
            @('a changed map hash', { param($t, $d) $t.Replace($d['map_sha256'], ('0' * 64)) }, 'different asset map'),
            @('a changed file hash', { param($t, $d) $t.Replace($d['files']['agent/tools/tool.py'], ('0' * 64)) }, 'files do not match'),
            @('a changed source hash', { param($t, $d) $t.Replace($d['sources']['agent/tools/tool.py'], ('0' * 64)) }, 'sources do not match'),
            @('a changed schema', { param($t, $d) $t.Replace('receipt/v1', 'receipt/v2') }, 'unexpected shape'),
            @('a changed inert list', { param($t, $d) $t.Replace('"agent/workflows/native-review.json",', '') }, 'inert'),
            @('an extra field', { param($t, $d) $t.Replace('"schema":', '"extra": 1, "schema":') }, 'unexpected shape'),
            @('a duplicate key', { param($t, $d) $t.Replace('"schema":', '"schema": "x", "schema":') }, 'not valid JSON'),
            @('a bad commit', { param($t, $d) $t.Replace($d['source_commit'], 'HEAD') }, 'unexpected shape'))) {
        $copy = New-Rendered 'receipt'
        $document = ConvertFrom-AsreStrictJson -Json $receiptText
        $changed = & $case[1] $receiptText $document
        Assert-Assets ($changed -cne $receiptText) "The receipt mutation must change the receipt ($($case[0]))."
        Write-Text (Join-Path $copy 'render-receipt.json') $changed
        Assert-Refused { Assert-AsreRenderedAssets -Context $alt -Root $copy -SourceRoot $source } $case[2] "A receipt with $($case[0]) must be refused."
    }
    # ------------------------------------------------------------ the script
    $pwsh = (Get-Process -Id $PID).Path
    $script = Join-Path $repo 'scripts\Render-AgentAssets.ps1'
    $realFile = Join-Path $temporary 'real-context.json'
    Write-Text $realFile (ConvertTo-Json -InputObject $realDocument -Depth 10)
    Assert-Assets ((Get-AsreContext -Path $realFile).subscription_id -ceq $real['subscription_id']) 'The real loader must accept the realistic test context.'
    $parameters = (Get-Command $script).ParameterSets
    Assert-Assets (@($parameters | ForEach-Object { $_.Name } | Sort-Object) -join ',' -ceq 'Render,Verify') 'The script has a Render and a Verify parameter set.'
    $cliOut = Join-Path $temporary 'cli-out'
    $log = @(& $pwsh -NoProfile -ExecutionPolicy Bypass -File $script -ContextPath $realFile -OutputRoot $cliOut 2>&1 | ForEach-Object { "$_" })
    Assert-Assets ($LASTEXITCODE -eq 0) 'The render script must succeed for a real context.'
    Assert-Assets (@($log | Where-Object { $_ -match "\AFiles: $($sourceFile.Count)\z" }).Count -eq 1 -and @($log | Where-Object { $_ -match '\AReceipt SHA-256: [0-9a-f]{64}\z' }).Count -eq 1 -and @($log | Where-Object { $_ -match '\ARoot: ' }).Count -eq 1) 'The script prints the root, the file count and the receipt hash.'
    $joined = $log -join "`n"
    foreach ($value in @($real['subscription_id'], $real['tenant_id'], $real['name_suffix'], $real['servicenow']['origin'], $real['agent']['endpoint'])) {
        Assert-Assets (-not $joined.Contains($value)) 'The script output must not echo a context value.'
    }
    $log = @(& $pwsh -NoProfile -ExecutionPolicy Bypass -File $script -ContextPath $realFile -VerifyRoot $cliOut 2>&1 | ForEach-Object { "$_" })
    Assert-Assets ($LASTEXITCODE -eq 0 -and ($log -join "`n").Contains('Rendered assets verified.')) 'The script must verify its own render.'
    $null = & $pwsh -NoProfile -ExecutionPolicy Bypass -File $script -ContextPath $realFile -OutputRoot $cliOut 2>&1
    Assert-Assets ($LASTEXITCODE -eq 1) 'The script must refuse an existing output directory with exit code 1.'
    $null = & $pwsh -NoProfile -ExecutionPolicy Bypass -File $script -ContextPath $synPath -OutputRoot (Join-Path $temporary 'cli-synthetic') 2>&1
    Assert-Assets ($LASTEXITCODE -eq 1 -and -not (Test-Path -LiteralPath (Join-Path $temporary 'cli-synthetic'))) 'The script must refuse a synthetic context.'
    $null = & $pwsh -NoProfile -ExecutionPolicy Bypass -File $script -ContextPath $realFile -OutputRoot (Join-Path $repo 'agent/rendered-by-mistake') 2>&1
    Assert-Assets ($LASTEXITCODE -eq 1 -and -not (Test-Path -LiteralPath (Join-Path $repo 'agent/rendered-by-mistake'))) 'The script must refuse a tracked output folder.'
    $cliFile = Join-Path $cliOut 'agent/tools/review_evidence.py'
    [System.IO.File]::AppendAllText($cliFile, "# tampered`n")
    $null = & $pwsh -NoProfile -ExecutionPolicy Bypass -File $script -ContextPath $realFile -VerifyRoot $cliOut 2>&1
    Assert-Assets ($LASTEXITCODE -eq 1) 'The script must reject a tampered tree with exit code 1.'

    # ------------------------------------------------------------ hygiene
    foreach ($name in @('AgentAssets.Common.ps1', 'Render-AgentAssets.ps1')) {
        $code = [System.IO.File]::ReadAllText((Join-Path $repo "scripts\$name"))
        Assert-Assets ($code -cnotmatch 'Invoke-Expression|Invoke-WebRequest|Invoke-RestMethod|Invoke-AsreAgentPlane|(?m)^\s*&?\s*az\s|\baz account\b|##vso|servicePrincipalId|idToken|BUILD_SOURCEVERSION') "$name must stay offline and free of pipeline identity."
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo "scripts\$name"), [ref]$null, [ref]$errors)
        Assert-Assets ($errors.Count -eq 0) "$name must parse."
    }
}
finally {
    Clear-AsreTestContext
    Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Output "Agent asset render tests passed: $script:assertions assertions."
