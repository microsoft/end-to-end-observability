#Requires -Version 7.5
# Offline tests for the Azure SRE Agent plane helper (scripts/AgentPlane.Common.ps1).
#
# Both Azure CLI and Invoke-WebRequest are shadowed for the whole run: the tests prove which hosts, paths, verbs, headers and
# bodies the helper is willing to send, that ARM writes stay confined to the agent resource, that the signed-in subscription
# is checked before any token is requested, that responses are reduced to a fixed vocabulary of errors (bodies and tokens are
# never echoed) and that tokens are cleared on failure. No Azure, terraform or network command is executed.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = Split-Path -Parent $PSScriptRoot
$SyntheticPath = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'
$HelperPath = Join-Path $Repo 'scripts\AgentPlane.Common.ps1'
$ArmToken = 'arm-token-sentinel-4f2a'
$SreToken = 'sre-token-sentinel-8b1c'
$LeakPattern = 'token-sentinel|Bearer|body-secret|transport-secret|Authorization(?!Failed)'
$script:AssertionCount = 0

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    $script:AssertionCount++
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Get-Failure {
    param([scriptblock]$Block)
    try { $null = & $Block } catch { return $_.Exception }
    return $null
}

function Assert-Fails {
    # The block must fail with a message matching the (case-sensitive) pattern and leaking no token or body.
    param([scriptblock]$Block, [string]$Pattern, [string]$Message, [string]$Category)
    $script:AssertionCount++
    $failure = Get-Failure $Block
    if ($null -eq $failure) { throw "ASSERTION FAILED: $Message (it did not fail)" }
    if ($failure.Message -cnotmatch $Pattern) { throw "ASSERTION FAILED: $Message (unexpected failure: $($failure.Message))" }
    if ($failure.Message -cmatch $LeakPattern) { throw "ASSERTION FAILED: $Message (the failure leaked a secret: $($failure.Message))" }
    if ($Category -and $failure.Data['AsrePlaneCategory'] -cne $Category) { throw "ASSERTION FAILED: $Message (category was '$($failure.Data['AsrePlaneCategory'])', expected '$Category')" }
}

function Assert-Rejected {
    # Rejected locally: it must fail and must not reach the network.
    param([scriptblock]$Block, [string]$Pattern, [string]$Message)
    Reset-Web
    Assert-Fails -Block $Block -Pattern $Pattern -Message $Message
    Assert-Test ($global:WebCalls.Count -eq 0) "$Message (a request was sent anyway)"
}

$global:AzCalls = [System.Collections.Generic.List[string]]::new()
$global:AzAccount = $null
$global:AzAccountExit = 0
$global:AzTokens = @{}
$global:WebCalls = [System.Collections.Generic.List[object]]::new()
$global:WebReplies = [System.Collections.Generic.Queue[object]]::new()

function global:az {
    $line = ($args -join ' ')
    $global:AzCalls.Add($line)
    $global:LASTEXITCODE = 0
    if ($line -cmatch '\Aaccount show ') { $global:LASTEXITCODE = $global:AzAccountExit; return $global:AzAccount }
    if ($line -cmatch '\Aaccount get-access-token ') {
        $resource = [string]$args[[System.Array]::IndexOf($args, '--resource') + 1]
        $entry = $global:AzTokens[$resource]
        $global:LASTEXITCODE = $entry.Exit
        return $entry.Out
    }
    $global:LASTEXITCODE = 9
    return 'unexpected az command'
}

function global:Invoke-WebRequest {
    param($Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec, $ErrorAction, [switch]$SkipHttpErrorCheck, $Body, $ContentType)
    $global:WebCalls.Add([pscustomobject]@{
            Uri = $Uri; Method = $Method; Headers = $Headers; MaximumRedirection = $MaximumRedirection; TimeoutSec = $TimeoutSec
            ErrorAction = $ErrorAction; SkipHttpErrorCheck = [bool]$SkipHttpErrorCheck; Body = $Body; ContentType = $ContentType
            HasBody = $PSBoundParameters.ContainsKey('Body')
        })
    $reply = if ($global:WebReplies.Count) { $global:WebReplies.Dequeue() } else { @{ StatusCode = 200; Content = '{}' } }
    if ($reply -is [scriptblock]) { return (& $reply) }
    if ($reply.ContainsKey('Throw')) { throw $reply['Throw'] }
    return [pscustomobject]$reply
}

function Reset-Web { $global:WebCalls.Clear(); $global:WebReplies.Clear() }
function Add-Reply { param([hashtable]$Reply) $global:WebReplies.Enqueue($Reply) }

try {
    Assert-Test ($null -eq (Get-Command -Name Get-AsreContext -CommandType Alias -ErrorAction SilentlyContinue)) 'A test alias shadows the real loader; run this test in a fresh process.'
    . (Join-Path $Repo 'scripts\Context.Common.ps1')
    . $HelperPath
    $ctx = Get-AsreContext -Path $SyntheticPath -AllowSynthetic
    $Sub = $ctx.subscription_id
    $Tenant = $ctx.tenant_id
    $Agent = $ctx.agent.resource_id
    $AgentEndpoint = $ctx.agent.endpoint
    $AgentGet = "$Agent`?api-version=2026-01-01"

    # Record every clear so tests can prove tokens are wiped on failure; the real function still does the work.
    $OriginalClear = ${function:Clear-AsreAgentPlane}
    $script:Cleared = [System.Collections.Generic.List[object]]::new()
    function Clear-AsreAgentPlane { param($Plane) $script:Cleared.Add($Plane); & $OriginalClear -Plane $Plane }

    function Reset-Az {
        $global:AzCalls.Clear()
        $global:AzAccountExit = 0
        $global:AzAccount = (@{ id = $Sub; tenantId = $Tenant; state = 'Enabled' } | ConvertTo-Json -Compress)
        $global:AzTokens = @{
            'https://management.azure.com' = @{ Exit = 0; Out = "  $ArmToken  " }
            'https://azuresre.dev'         = @{ Exit = 0; Out = $SreToken }
        }
        $script:Cleared.Clear()
        Reset-Web
    }
    function New-Plane {
        param([switch]$ReadOnly, $SreEndpoint = $AgentEndpoint)
        @{ agent = $Agent; readOnly = [bool]$ReadOnly; armHeaders = @{ Authorization = "Bearer $ArmToken" }
            sreHeaders = @{ Authorization = "Bearer $SreToken" }; sreEndpoint = $SreEndpoint; context = $ctx }
    }
    function Invoke-Plane {
        param($Plane, [string]$Service = 'Arm', [string]$Method = 'Get', [string]$Path, $Body, [string]$IfMatch, [switch]$AllowNotFound)
        if ($null -eq $Plane) { $Plane = New-Plane }
        $splat = @{ Plane = $Plane; Service = $Service; Method = $Method; Path = $Path }
        if ($PSBoundParameters.ContainsKey('Body')) { $splat['Body'] = $Body }
        if ($PSBoundParameters.ContainsKey('IfMatch')) { $splat['IfMatch'] = $IfMatch }
        if ($AllowNotFound) { $splat['AllowNotFound'] = $true }
        Invoke-AsreAgentPlaneRequest @splat
    }

    # ---- static pins on the helper source ----------------------------------------------------------------------------------
    $source = [System.IO.File]::ReadAllText($HelperPath)
    Assert-Test ($source -cmatch 'MaximumRedirection = 0' -and $source -cmatch 'TimeoutSec = 60' -and $source -cmatch 'SkipHttpErrorCheck = \$true') 'The helper must refuse redirects, bound the time and handle status codes itself.'
    Assert-Test ($source -cnotmatch '(?i)SkipCertificateCheck|AllowUnencryptedAuthentication|-Proxy\b|SkipHeaderValidation|Invoke-RestMethod|Write-Host|Out-File|Set-Content|Add-Content') 'The helper must not weaken TLS, send through a proxy, write files or print.'
    Assert-Test (@([regex]::Matches($source, 'get-access-token')).Count -eq 2 -and $source -cmatch '--resource https://management\.azure\.com' -and $source -cmatch '--resource https://azuresre\.dev') 'Only the ARM and SRE audiences may be requested.'
    Assert-Test ($source -cnotmatch '(?i)az (?:login|account set|logout)|client[_-]?secret|\.token\b' -and $source -cnotmatch '\$env:') 'The helper must not log in, switch accounts or read environment credentials.'

    # ---- Get-AsreMember / Get-AsreResponseValue ---------------------------------------------------------------------------
    $nested = @{ a = @{ b = @{ c = 'deep' }; n = $null }; s = 'text' }
    Assert-Test ((Get-AsreMember -Value $nested -Path @('a', 'b', 'c')) -ceq 'deep') 'A nested member was not found.'
    Assert-Test ($null -eq (Get-AsreMember -Value $nested -Path @('a', 'x', 'c')) -and $null -eq (Get-AsreMember -Value $nested -Path @('s', 'x')) -and $null -eq (Get-AsreMember -Value $nested -Path @('a', 'n', 'x'))) 'A missing step must yield null.'
    Assert-Test ($null -eq (Get-AsreMember -Value $null -Path @('a')) -and (Get-AsreMember -Value $nested -Path @()) -is [hashtable]) 'A null root yields null and an empty path yields the value.'
    Assert-Test ((Get-AsreResponseValue -Response @{ A = 1 } -Name 'A') -eq 1 -and $null -eq (Get-AsreResponseValue -Response @{ A = 1 } -Name 'B')) 'A dictionary response value was misread.'
    Assert-Test ((Get-AsreResponseValue -Response ([pscustomobject]@{ A = 2 }) -Name 'A') -eq 2 -and $null -eq (Get-AsreResponseValue -Response ([pscustomobject]@{ A = 2 }) -Name 'B')) 'An object response value was misread.'

    # ---- Assert-AsreExact --------------------------------------------------------------------------------------------------
    Assert-Test ($null -eq (Assert-AsreExact -Expected @{ a = 1; b = @(1, 2); c = @{ d = 'x' } } -Actual @{ c = @{ d = 'x' }; b = @(1, 2); a = 1 })) 'Equal documents, in another key order, were refused.'
    Assert-Test ($null -eq (Assert-AsreExact -Expected $null -Actual $null) -and $null -eq (Assert-AsreExact -Expected 'x' -Actual 'x') -and $null -eq (Assert-AsreExact -Expected @{} -Actual @{})) 'Equal scalars, nulls or empty objects were refused.'
    Assert-Test ($null -eq (Assert-AsreExact -Expected @(@{ k = 1 }, @{ k = 2 }) -Actual @(@{ k = 1 }, @{ k = 2 }))) 'Equal arrays of objects were refused.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = 1 } -Actual @{ a = 1; b = 2 } } 'Strict readback changed value' 'An unexpected extra field was accepted.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = 1; b = 2 } -Actual @{ a = 1; c = 2 } } 'Strict readback omitted value\.b' 'A missing field was accepted.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = 1 } -Actual @{ a = '1' } } 'Strict readback changed value\.a' 'A type change was accepted.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = 'Abc' } -Actual @{ a = 'abc' } } 'Strict readback changed value\.a' 'A case change was accepted.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = $null } -Actual @{ a = '' } } 'Strict readback changed value\.a' 'A null became an empty string.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = $true } -Actual @{ a = 1 } } 'Strict readback changed value\.a' 'A boolean became a number.'
    Assert-Fails { Assert-AsreExact -Expected @{ l = @(1, 2) } -Actual @{ l = @(1, 2, 3) } } 'Strict readback changed value\.l' 'A longer array was accepted.'
    Assert-Fails { Assert-AsreExact -Expected @{ l = @(1, 2) } -Actual @{ l = @(1, 3) } } 'Strict readback changed value\.l\[1\]' 'A changed array element was accepted.'
    Assert-Fails { Assert-AsreExact -Expected @{ l = @(1, 2) } -Actual @{ l = @{ x = 1 } } } 'Strict readback changed value\.l' 'An array became an object.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = @{ b = @{ c = 1 } } } -Actual @{ a = @{ b = @{ c = 2 } } } } 'Strict readback changed value\.a\.b\.c' 'The failing path was not reported.'
    Assert-Fails { Assert-AsreExact -Expected @{ a = 1 } -Actual @{ a = 2 } -Path 'resource' } 'Strict readback changed resource\.a' 'A custom path prefix was ignored.'

    # ---- Get-AsrePlaneErrorCode / New-AsrePlaneFailure ---------------------------------------------------------------------
    Assert-Test ((Get-AsrePlaneErrorCode -Response @{ Content = '{"error":{"code":"Conflict","message":"body-secret"}}' }) -ceq 'Conflict') 'The nested error code was not read.'
    Assert-Test ((Get-AsrePlaneErrorCode -Response ([pscustomobject]@{ Content = '{"code":"Not.Found-1_x"}' })) -ceq 'Not.Found-1_x') 'The top-level error code was not read.'
    foreach ($content in @('{"error":{"code":"bad code!"}}', '{"error":{"code":""}}', '{"error":{"code":5}}', "{`"error`":{`"code`":`"$('x' * 81)`"}}", '{"error":"x"}', 'not json', '', $null, '[1]', ('{"error":{"code":"A"},"pad":"' + ('x' * 17000) + '"}'))) {
        Assert-Test ($null -eq (Get-AsrePlaneErrorCode -Response @{ Content = $content })) "An unusable error code was returned for content '$("$content".Substring(0, [Math]::Min(30, "$content".Length)))'."
    }
    Assert-Test ($null -eq (Get-AsrePlaneErrorCode -Response @{ Content = [byte[]](1, 2) }) -and $null -eq (Get-AsrePlaneErrorCode -Response @{ Other = 1 })) 'A non-text body or a response without content produced an error code.'
    Assert-Test ((Get-AsrePlaneErrorCode -Response @{ Content = "{`"error`":{`"code`":`"$('x' * 80)`"}}" }).Length -eq 80) 'An 80-character error code was refused.'
    $failure = New-AsrePlaneFailure -Message 'm' -Category 'HttpFailure' -Service 'Arm' -Method 'Get' -Status 404 -Code 'NotFound'
    Assert-Test ($failure -is [System.InvalidOperationException] -and $failure.Message -ceq 'm' -and $failure.Data['AsrePlaneCategory'] -ceq 'HttpFailure' -and $failure.Data['AsrePlaneService'] -ceq 'Arm' -and $failure.Data['AsrePlaneMethod'] -ceq 'Get' -and $failure.Data['AsrePlaneStatus'] -eq 404 -and $failure.Data['AsrePlaneErrorCode'] -ceq 'NotFound' -and $null -eq $failure.InnerException) 'The failure object does not carry its category data.'
    $bare = New-AsrePlaneFailure -Message 'm' -Category 'TransportFailure' -Service 'Sre' -Method 'Put' -Status $null -Code $null
    Assert-Test (-not $bare.Data.Contains('AsrePlaneStatus') -and -not $bare.Data.Contains('AsrePlaneErrorCode')) 'A missing status or code must not be recorded.'

    # ---- Assert-AsreAgentContext -------------------------------------------------------------------------------------------
    Assert-Test ($null -eq (Assert-AsreAgentContext -Context $ctx)) 'A context returned by the loader was refused.'
    $alt = Get-AsreContext -Path (Join-Path $PSScriptRoot 'fixtures\context.alt.json') -AllowSynthetic
    Assert-Test ($null -eq (Assert-AsreAgentContext -Context $alt)) 'The alternative context was refused.'
    $needsLoader = 'needs a context returned by Get-AsreContext'
    Assert-Fails { Assert-AsreAgentContext -Context $null } $needsLoader 'A null context was accepted.'
    Assert-Fails { Assert-AsreAgentContext -Context @{} } $needsLoader 'An empty context was accepted.'
    Assert-Fails { Assert-AsreAgentContext -Context @{ subscription_id = $Sub; resource_groups = @{ control = 'rg-asre-control-cus' }; agent = @{ name = 'asre-demo-cus' } } } $needsLoader 'A context without a derived agent resource id was accepted.'
    foreach ($tamper in @(
            @{ Name = 'another subscription'; Id = $Agent.Replace($Sub, '00000009-000a-4000-a000-000000000009') },
            @{ Name = 'another group'; Id = $Agent.Replace('rg-asre-control-cus', 'rg-asre-linux-cus') },
            @{ Name = 'another agent'; Id = $Agent.Replace('asre-demo-cus', 'other') },
            @{ Name = 'another provider'; Id = $Agent.Replace('Microsoft.App/agents', 'Microsoft.App/jobs') },
            @{ Name = 'a different case'; Id = $Agent.ToLowerInvariant() },
            @{ Name = 'a trailing slash'; Id = "$Agent/" })) {
        $copy = @{ subscription_id = $Sub; resource_groups = @{ control = $ctx.resource_groups.control }; agent = @{ name = $ctx.agent.name; resource_id = $tamper.Id } }
        $label = $tamper.Name
        Assert-Fails { Assert-AsreAgentContext -Context $copy } $needsLoader "An agent resource id with $label was accepted."
    }

    # ---- Clear-AsreAgentPlane ----------------------------------------------------------------------------------------------
    $clearMe = New-Plane
    Clear-AsreAgentPlane -Plane $clearMe
    Assert-Test ($clearMe.armHeaders.Count -eq 0 -and $clearMe.sreHeaders.Count -eq 0 -and $clearMe.agent -ceq $Agent) 'Clearing must empty both header tables and nothing else.'
    foreach ($odd in @($null, 'text', 5, @{}, @{ armHeaders = 'x'; sreHeaders = $null })) {
        Clear-AsreAgentPlane -Plane $odd
        $script:AssertionCount++
    }
    $script:Cleared.Clear()

    # ---- request validation that never reaches the network -----------------------------------------------------------------
    foreach ($key in 'agent', 'readOnly', 'armHeaders', 'sreHeaders', 'sreEndpoint', 'context') {
        $broken = New-Plane
        $broken.Remove($key)
        $shownKey = $key
        Assert-Rejected { Invoke-Plane -Plane $broken -Path "/subscriptions/$Sub/x" } '\AThe agent plane is not valid\.\z' "A plane without '$shownKey' was accepted."
    }
    Assert-Rejected { Invoke-Plane -Plane @{} -Path '/api/x' -Service Sre } 'The agent plane is not valid' 'An empty plane was accepted.'
    Assert-Rejected { Invoke-AsreAgentPlaneRequest -Plane (New-Plane) -Service arm -Path "/subscriptions/$Sub/x" } 'does not belong to the set' 'A lowercase service was accepted.'
    Assert-Rejected { Invoke-AsreAgentPlaneRequest -Plane (New-Plane) -Service Arm -Method get -Path "/subscriptions/$Sub/x" } 'does not belong to the set' 'A lowercase method was accepted.'
    Assert-Rejected { Invoke-AsreAgentPlaneRequest -Plane (New-Plane) -Service Arm -Method Head -Path "/subscriptions/$Sub/x" } 'does not belong to the set' 'An unlisted method was accepted.'

    $readOnly = New-Plane -ReadOnly
    foreach ($method in 'Put', 'Patch', 'Post', 'Delete') {
        $shownMethod = $method
        Assert-Rejected { Invoke-Plane -Plane $readOnly -Method $method -Path "$Agent/x" -Body @{ a = 1 } } 'read-only agent plane cannot send non-GET' "A read-only plane sent $shownMethod to ARM."
        Assert-Rejected { Invoke-Plane -Plane $readOnly -Service Sre -Method $method -Path '/api/v1/x' } 'read-only agent plane cannot send non-GET' "A read-only plane sent $shownMethod to the SRE endpoint."
    }
    Reset-Web
    $null = Invoke-Plane -Plane $readOnly -Path $AgentGet
    $null = Invoke-Plane -Plane $readOnly -Service Sre -Path '/api/v1/x'
    Assert-Test ($global:WebCalls.Count -eq 2) 'A read-only plane must still allow GET.'

    Assert-Rejected { Invoke-Plane -Method Get -Path $AgentGet -Body @{ a = 1 } } 'A Get request cannot carry a body' 'A GET with a body was accepted.'
    Assert-Rejected { Invoke-Plane -Method Delete -Path "$Agent/x" -Body @{ a = 1 } } 'A Delete request cannot carry a body' 'A DELETE with a body was accepted.'
    Assert-Rejected { Invoke-Plane -Method Put -Path "$Agent/x" -Body 'text' } 'must be an object or an array' 'A string body was accepted.'
    Assert-Rejected { Invoke-Plane -Method Put -Path "$Agent/x" -Body 5 } 'must be an object or an array' 'A numeric body was accepted.'
    foreach ($etag in @(' leading', "tab`there", "new`nline", ('e' * 257), ('caf' + [char]0x00E9), "ends`n")) {
        $shownEtag = $etag
        Assert-Rejected { Invoke-Plane -Method Put -Path "$Agent/x" -Body @{ a = 1 } -IfMatch $etag } 'not a plain ETag' "An unsafe If-Match value was accepted: '$shownEtag'."
    }

    # ---- path rules --------------------------------------------------------------------------------------------------------
    foreach ($path in @('relative/path', "/subscriptions/$Sub/a b", "/subscriptions/$Sub/..", "/subscriptions/$Sub/x/../y", "/subscriptions/$Sub//x",
            "/subscriptions/$Sub/x\y", "/subscriptions/$Sub/%2e%2e/x", "/subscriptions/$Sub/x#frag", "/subscriptions/$Sub/x@y", "/subscriptions/$Sub/x?bad query",
            "/subscriptions/$Sub/x?a=<b>", "/subscriptions/$Sub/x?a=..", "/subscriptions/$Sub/x?a=b#c", "/subscriptions/$Sub/x`n", '/', "/subscriptions/$Sub/x?a=`"b`"", "/subscriptions/$Sub/x?a=b;c",
            "/subscriptions/$Sub/x?a=b|c", "https://management.azure.com/subscriptions/$Sub/x", "/subscriptions/$Sub/caf$([char]0x00E9)")) {
        $shownPath = $path -replace "`n", '\n'
        Assert-Rejected { Invoke-Plane -Path $path } 'rejected the request path' "The ARM path '$shownPath' was accepted."
    }
    foreach ($path in @('/api/x\y', '/api/../x', '/api//x', '/api/x y', '/api/x?a=<b>', '/api/x%2e%2e')) {
        $shownPath = $path
        Assert-Rejected { Invoke-Plane -Service Sre -Path $path } 'rejected the request path' "The SRE path '$shownPath' was accepted."
    }
    Assert-Rejected { Invoke-Plane -Path "/subscriptions/$Sub/./x" } 'rejected the request URI' 'A dot segment that the URI parser would normalize was accepted.'
    Assert-Rejected { Invoke-Plane -Service Sre -Path '/api/./x' } 'rejected the request URI' 'A dot segment on the SRE endpoint was accepted.'

    # ---- ARM: confinement to the deployment subscription -------------------------------------------------------------------
    foreach ($path in @('/subscriptions/00000009-000a-4000-a000-000000000009/resourceGroups/x', '/providers/Microsoft.App/agents/x', "/tenants/$Tenant", "/subscriptions/$Sub",
            "/subscriptions/${Sub}x/resourceGroups/x", "/subscriptionsx/$Sub/resourceGroups/x", "/api/subscriptions/$Sub/x", "/subscriptions/00000001-000a-4000-a000-00000000000/x")) {
        $shownPath = $path
        Assert-Rejected { Invoke-Plane -Path $path } 'ARM requests must stay inside the deployment subscription' "ARM path '$shownPath' escaped the subscription."
    }

    # ---- ARM: the request that is sent ------------------------------------------------------------------------------------
    Reset-Web
    Add-Reply @{ StatusCode = 200; Content = '{"id":"x","date":"2026-01-01T00:00:00Z","list":[1,2]}'; Headers = @{ ETag = @('"e1"') } }
    $result = Invoke-Plane -Path $AgentGet
    $call = $global:WebCalls[0]
    Assert-Test ($global:WebCalls.Count -eq 1 -and $call.Uri -ceq "https://management.azure.com$AgentGet" -and $call.Method -ceq 'Get') "The ARM request went to an unexpected URI: $($call.Uri)."
    Assert-Test ($call.Headers.Count -eq 1 -and $call.Headers['Authorization'] -ceq "Bearer $ArmToken") 'The ARM request did not carry exactly the ARM bearer token.'
    Assert-Test ($call.MaximumRedirection -eq 0 -and $call.TimeoutSec -eq 60 -and $call.ErrorAction -ceq 'Stop' -and $call.SkipHttpErrorCheck -and -not $call.HasBody) 'The ARM request did not refuse redirects, bound the time or handle status codes itself.'
    Assert-Test ($result.status -eq 200 -and $result.data.id -ceq 'x' -and $result.data.date -is [string] -and $result.data.date -ceq '2026-01-01T00:00:00Z' -and @($result.data.list).Count -eq 2 -and $result.etag -ceq '"e1"') 'The ARM response was not parsed into status, data and ETag.'
    foreach ($path in @("/subscriptions/$Sub/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus?api-version=2024-07-01",
            "/subscriptions/$Sub/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=atScope()", "/subscriptions/$Sub/x?a='b',c*d+e%20f&g=1")) {
        Reset-Web
        $null = Invoke-Plane -Path $path
        $shownPath = $path
        Assert-Test ($global:WebCalls.Count -eq 1 -and $global:WebCalls[0].Uri -ceq "https://management.azure.com$path") "A valid ARM read of $shownPath was refused or rewritten."
    }
    Reset-Web
    $plane = New-Plane
    $null = Invoke-Plane -Plane $plane -Method Patch -Path $Agent -Body @{ properties = @{ x = 1 } } -IfMatch '"abc"'
    $call = $global:WebCalls[0]
    Assert-Test ($call.Method -ceq 'Patch' -and $call.Headers['If-Match'] -ceq '"abc"' -and $call.Headers['Authorization'] -ceq "Bearer $ArmToken" -and $call.ContentType -ceq 'application/json') 'PATCH on the agent resource did not send If-Match, the token and a JSON content type.'
    Assert-Test ((($call.Body | ConvertFrom-Json -AsHashtable).properties.x) -eq 1 -and -not $plane.armHeaders.Contains('If-Match') -and $plane.armHeaders.Count -eq 1) 'The body was not serialized or If-Match leaked into the stored plane headers.'
    Reset-Web
    $null = Invoke-Plane -Method Post -Path "$Agent/actions" -Body @(@{ a = 1 }, @{ a = 2 }) -IfMatch 'W/"x y"'
    Assert-Test (@($global:WebCalls[0].Body | ConvertFrom-Json -AsHashtable).Count -eq 2 -and $global:WebCalls[0].Headers['If-Match'] -ceq 'W/"x y"') 'An array body or a weak ETag was refused.'
    Reset-Web
    $null = Invoke-Plane -Method Post -Path "$Agent/actions" -Body @{}
    Assert-Test ($global:WebCalls[0].HasBody -and ($global:WebCalls[0].Body -replace '\s', '') -ceq '{}') 'An empty object body was not sent.'
    Reset-Web
    $null = Invoke-Plane -Method Put -Path "$Agent/x" -Body @{ a = 1 } -IfMatch ('e' * 256)
    Assert-Test ($global:WebCalls[0].Headers['If-Match'].Length -eq 256) 'A 256-character If-Match value was refused.'
    Reset-Web
    $null = Invoke-Plane -Path "/subscriptions/$($Sub.ToUpperInvariant())/resourceGroups/x"
    Assert-Test ($global:WebCalls.Count -eq 1) 'The subscription prefix must match case-insensitively, as Azure resource ids do.'

    # ---- ARM: writes only to the agent resource ------------------------------------------------------------------------------
    $elsewhere = @("/subscriptions/$Sub/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus",
        "/subscriptions/$Sub/resourceGroups/rg-asre-control-cus", "/subscriptions/$Sub/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/other-agent",
        "${Agent}x", "$($Agent -replace '/asre-demo-cus\z', '')", "/subscriptions/$Sub/resourceGroups/rg-asre-linux-cus/providers/Microsoft.App/agents/asre-demo-cus",
        "/subscriptions/$Sub/providers/Microsoft.Authorization/roleAssignments/00000000-0000-4000-8000-000000000001")
    foreach ($path in $elsewhere) {
        foreach ($method in 'Put', 'Patch', 'Post', 'Delete') {
            $shownPath = $path; $shownMethod = $method
            $splat = @{ Method = $method; Path = $path }
            if ($method -cne 'Delete') { $splat.Body = @{ a = 1 } }
            Assert-Rejected { Invoke-Plane @splat } 'ARM writes must target the agent resource' "$shownMethod of $shownPath was allowed."
        }
    }
    foreach ($method in 'Put', 'Post', 'Delete') {
        foreach ($path in $Agent, $AgentGet) {
            $shownMethod = $method
            $splat = @{ Method = $method; Path = $path }
            if ($method -cne 'Delete') { $splat.Body = @{ a = 1 } }
            Assert-Rejected { Invoke-Plane @splat } 'Only PATCH may target the agent resource itself' "$shownMethod on the agent resource itself was allowed."
        }
    }
    foreach ($path in $Agent, $AgentGet) {
        Reset-Web
        $null = Invoke-Plane -Method Patch -Path $path -Body @{ properties = @{} }
        Assert-Test ($global:WebCalls.Count -eq 1 -and $global:WebCalls[0].Method -ceq 'Patch') 'PATCH on the agent resource was refused.'
    }
    foreach ($method in 'Put', 'Patch', 'Post', 'Delete') {
        Reset-Web
        $splat = @{ Method = $method; Path = "$Agent/triggers/t1?api-version=2026-01-01" }
        if ($method -cne 'Delete') { $splat.Body = @{ a = 1 } }
        $null = Invoke-Plane @splat
        Assert-Test ($global:WebCalls.Count -eq 1 -and $global:WebCalls[0].Method -ceq $method -and $global:WebCalls[0].Uri -ceq "https://management.azure.com$Agent/triggers/t1?api-version=2026-01-01") "$method on a child of the agent resource was refused."
    }

    # ---- SRE data plane ------------------------------------------------------------------------------------------------------
    Reset-Web
    Add-Reply @{ StatusCode = 200; Content = '{"items":[]}'; Headers = @{ ETag = '"s1"' } }
    $result = Invoke-Plane -Service Sre -Path '/api/v1/threads?top=5'
    $call = $global:WebCalls[0]
    Assert-Test ($call.Uri -ceq "$AgentEndpoint/api/v1/threads?top=5" -and $call.Method -ceq 'Get' -and $call.MaximumRedirection -eq 0 -and $call.TimeoutSec -eq 60) "The SRE request went to an unexpected URI: $($call.Uri)."
    Assert-Test ($call.Headers.Count -eq 1 -and $call.Headers['Authorization'] -ceq "Bearer $SreToken" -and $call.Headers['Authorization'] -cnotmatch 'arm-token') 'The SRE request must carry the SRE token and never the ARM token.'
    Assert-Test ($result.status -eq 200 -and @($result.data.items).Count -eq 0 -and $result.etag -ceq '"s1"') 'The SRE response was not parsed.'
    Reset-Web
    $null = Invoke-Plane -Service Sre -Method Post -Path '/api/v1/threads' -Body @{ title = 't' }
    Assert-Test ($global:WebCalls[0].Method -ceq 'Post' -and $global:WebCalls[0].Uri -ceq "$AgentEndpoint/api/v1/threads" -and $global:WebCalls[0].ContentType -ceq 'application/json') 'A non-read-only plane could not write to the SRE endpoint.'
    foreach ($path in @('/apis/x', '/API/x', '/api', "/subscriptions/$Sub/x", '/x/api/y', '/v1/api/x')) {
        $shownPath = $path
        Assert-Rejected { Invoke-Plane -Service Sre -Path $path } 'SRE requests must use the /api/ routes' "The SRE path '$shownPath' was accepted."
    }
    foreach ($endpoint in @($null, '', 5, 'http://x.azuresre.ai', 'https://evil.example.com', 'https://x.azuresre.ai.evil.com', 'https://x.azuresre.ai:8443', 'https://x.azuresre.ai/', 'https://X.azuresre.ai',
            'https://azuresre.ai', 'https://-x.azuresre.ai', 'https://user@x.azuresre.ai', 'https://x.azuresre.ai/api', 'https://x.azuresre.ai?a=b', 'ftp://x.azuresre.ai', 'https://x.azuresre.dev')) {
        $badPlane = New-Plane -SreEndpoint $endpoint
        $shownEndpoint = "$endpoint"
        Assert-Rejected { Invoke-Plane -Plane $badPlane -Service Sre -Path '/api/v1/x' } 'The agent plane has no SRE endpoint' "The SRE endpoint '$shownEndpoint' was accepted."
    }
    $wild = New-Plane -SreEndpoint 'https://a-b.c1.centralus.azuresre.ai'
    Reset-Web
    $null = Invoke-Plane -Plane $wild -Service Sre -Path '/api/v1/x'
    Assert-Test ($global:WebCalls[0].Uri -ceq 'https://a-b.c1.centralus.azuresre.ai/api/v1/x') 'A well-formed azuresre.ai endpoint was refused.'

    # ---- cleared and partial headers ---------------------------------------------------------------------------------------
    $wipedPlane = New-Plane
    Clear-AsreAgentPlane -Plane $wipedPlane
    Assert-Rejected { Invoke-Plane -Plane $wipedPlane -Path $AgentGet } 'The agent plane has been cleared' 'A cleared plane still sent an ARM request.'
    Assert-Rejected { Invoke-Plane -Plane $wipedPlane -Service Sre -Path '/api/v1/x' } 'The agent plane has been cleared' 'A cleared plane still sent an SRE request.'
    $armOnlyCleared = New-Plane
    $armOnlyCleared.armHeaders.Clear()
    Assert-Rejected { Invoke-Plane -Plane $armOnlyCleared -Path $AgentGet } 'The agent plane has been cleared' 'A plane without an ARM token sent an ARM request.'
    Reset-Web
    $null = Invoke-Plane -Plane $armOnlyCleared -Service Sre -Path '/api/v1/x'
    Assert-Test ($global:WebCalls.Count -eq 1) 'Clearing one audience must not disable the other.'
    $noAuth = New-Plane
    $noAuth.armHeaders = @{ Accept = 'application/json' }
    Assert-Rejected { Invoke-Plane -Plane $noAuth -Path $AgentGet } 'The agent plane has been cleared' 'A plane whose header table has no Authorization sent a request.'
    $notATable = New-Plane
    $notATable.sreHeaders = $null
    Assert-Rejected { Invoke-Plane -Plane $notATable -Service Sre -Path '/api/v1/x' } 'The agent plane has been cleared' 'A plane without a header table sent a request.'

    # ---- response handling ---------------------------------------------------------------------------------------------------
    $arm = "/subscriptions/$Sub/resourceGroups/rg-asre-control-cus"
    foreach ($case in @(
            @{ Name = 'a string ETag'; Reply = @{ StatusCode = 200; Content = '{}'; Headers = @{ ETag = '"abc"' } }; Etag = '"abc"' },
            @{ Name = 'an array ETag'; Reply = @{ StatusCode = 200; Content = '{}'; Headers = @{ ETag = @('"first"', '"second"') } }; Etag = '"first"' },
            @{ Name = 'no ETag'; Reply = @{ StatusCode = 200; Content = '{}'; Headers = @{ Other = 'x' } }; Etag = $null },
            @{ Name = 'no headers'; Reply = @{ StatusCode = 200; Content = '{}' }; Etag = $null },
            @{ Name = 'a null header table'; Reply = @{ StatusCode = 200; Content = '{}'; Headers = $null }; Etag = $null },
            @{ Name = 'a header table that is not a dictionary'; Reply = @{ StatusCode = 200; Content = '{}'; Headers = 'text' }; Etag = $null })) {
        Reset-Web; Add-Reply $case.Reply
        $r = Invoke-Plane -Path $arm
        $label = $case.Name
        Assert-Test ($r.status -eq 200 -and (($null -eq $case.Etag -and $null -eq $r.etag) -or $r.etag -ceq $case.Etag)) "A response with $label gave the wrong ETag."
    }
    foreach ($case in @(
            @{ Name = 'created'; Reply = @{ StatusCode = 201; Content = '{"a":1}' }; Status = 201; Data = $true },
            @{ Name = 'accepted without content'; Reply = @{ StatusCode = 202; Content = $null }; Status = 202; Data = $false },
            @{ Name = 'no content'; Reply = @{ StatusCode = 204; Content = '' }; Status = 204; Data = $false },
            @{ Name = 'whitespace only'; Reply = @{ StatusCode = 200; Content = " `n " }; Status = 200; Data = $false },
            @{ Name = 'an empty byte array'; Reply = @{ StatusCode = 200; Content = [byte[]]@() }; Status = 200; Data = $false },
            @{ Name = 'the upper edge of success'; Reply = @{ StatusCode = 299; Content = '{"a":1}' }; Status = 299; Data = $true },
            @{ Name = 'a response without a Content property'; Reply = @{ StatusCode = 200 }; Status = 200; Data = $false })) {
        Reset-Web; Add-Reply $case.Reply
        $r = Invoke-Plane -Path $arm
        $label = $case.Name
        Assert-Test ($r.status -eq $case.Status -and (($null -ne $r.data) -eq $case.Data)) "A $label response was handled wrongly."
    }
    Reset-Web; Add-Reply @{ StatusCode = 200; Content = [byte[]](123, 125) }
    Assert-Fails { Invoke-Plane -Path $arm } '\AThe agent plane response was not JSON text; details are suppressed\.\z' 'A byte response was parsed.' 'ParseFailure'
    Reset-Web; Add-Reply @{ StatusCode = 200; Content = 5 }
    Assert-Fails { Invoke-Plane -Path $arm } 'was not JSON text' 'A numeric content value was parsed.' 'ParseFailure'
    Reset-Web; Add-Reply @{ StatusCode = 200; Content = '<html>body-secret</html>' }
    Assert-Fails { Invoke-Plane -Path $arm } '\AThe agent plane response was not valid JSON; details are suppressed\.\z' 'An HTML response was parsed as JSON.' 'ParseFailure'
    Reset-Web; Add-Reply @{ StatusCode = 200; Content = '{"a":' }
    Assert-Fails { Invoke-Plane -Path $arm } 'was not valid JSON' 'A truncated JSON response was parsed.' 'ParseFailure'
    Reset-Web; $global:WebReplies.Enqueue({ @{ StatusCode = 200; Content = '{"x":1}'; Headers = @{ ETag = '"d"' } } })
    $r = Invoke-Plane -Path $arm
    Assert-Test ($r.data.x -eq 1 -and $r.etag -ceq '"d"') 'A dictionary-shaped response object was not read.'

    # Not found.
    Reset-Web; Add-Reply @{ StatusCode = 404; Content = '{"error":{"code":"ResourceNotFound","message":"body-secret"}}'; Headers = @{ ETag = '"ignored"' } }
    $r = Invoke-Plane -Path $arm -AllowNotFound
    Assert-Test ($r.status -eq 404 -and $null -eq $r.data -and $null -eq $r.etag -and $r.Keys.Count -eq 3) 'A tolerated 404 must return status 404 with no data and no ETag.'
    Reset-Web; Add-Reply @{ StatusCode = 404; Content = '{"error":{"code":"ResourceNotFound","message":"body-secret"}}' }
    Assert-Fails { Invoke-Plane -Path $arm } '\AThe agent plane returned HTTP 404 \(ResourceNotFound\); response details are suppressed\. Re-read before retrying\.\z' 'A 404 was tolerated without -AllowNotFound.' 'HttpFailure'
    Reset-Web; Add-Reply @{ StatusCode = 404; Content = '{"error":{"code":"ResourceNotFound","message":"body-secret"}}' }
    $failure = Get-Failure { Invoke-Plane -Path $arm }
    Assert-Test ($failure.Data['AsrePlaneStatus'] -eq 404 -and $failure.Data['AsrePlaneErrorCode'] -ceq 'ResourceNotFound' -and $failure.Data['AsrePlaneService'] -ceq 'Arm' -and $failure.Data['AsrePlaneMethod'] -ceq 'Get') 'The failure does not carry its status, code, service and method.'

    # Other statuses, with secrets in the body that must never surface.
    foreach ($status in 100, 199, 300, 301, 302, 307, 400, 401, 403, 409, 412, 429, 500, 503, 599) {
        Reset-Web; Add-Reply @{ StatusCode = $status; Content = '{"error":{"code":"Denied","message":"body-secret Bearer transport-secret"}}'; Headers = @{ Location = 'https://evil.example/body-secret' } }
        $shownStatus = $status
        Assert-Fails { Invoke-Plane -Path $arm -AllowNotFound } "\AThe agent plane returned HTTP $status \(Denied\); response details are suppressed\. Re-read before retrying\.\z" "Status $shownStatus was not reported with the fixed message." 'HttpFailure'
        Assert-Test ($global:WebCalls.Count -eq 1) "Status $shownStatus was followed or retried."
    }
    Reset-Web; Add-Reply @{ StatusCode = 500; Content = '<html>body-secret</html>' }
    Assert-Fails { Invoke-Plane -Path $arm } '\AThe agent plane returned HTTP 500; response details are suppressed\. Re-read before retrying\.\z' 'A non-JSON error body changed the message.' 'HttpFailure'
    Reset-Web; Add-Reply @{ StatusCode = 400; Content = '{"error":{"code":"bad code body-secret","message":"x"}}' }
    Assert-Fails { Invoke-Plane -Path $arm } '\AThe agent plane returned HTTP 400; response details' 'An unsafe error code was echoed.' 'HttpFailure'
    foreach ($status in $null, '200', 200.5, 99, 600, 0, -1) {
        Reset-Web; Add-Reply @{ StatusCode = $status; Content = '{}' }
        $shownStatus = "$status"
        Assert-Fails { Invoke-Plane -Path $arm } '\AThe agent plane returned an invalid HTTP status; details are suppressed\.\z' "The status '$shownStatus' was accepted." 'InvalidStatus'
    }
    Reset-Web; Add-Reply @{ Content = '{}' }
    Assert-Fails { Invoke-Plane -Path $arm } 'invalid HTTP status' 'A response without a status was accepted.' 'InvalidStatus'

    # Transport errors.
    foreach ($service in 'Arm', 'Sre') {
        Reset-Web; Add-Reply @{ Throw = 'transport-secret Bearer arm-token-sentinel-4f2a at https://evil.example' }
        $path = if ($service -ceq 'Arm') { $arm } else { '/api/v1/x' }
        $failure = Get-Failure { Invoke-Plane -Service $service -Path $path }
        Assert-Test ($failure.Message -ceq 'The agent plane request failed in transport; response and authentication details are suppressed. Re-read before retrying.' -and $failure.Message -cnotmatch $LeakPattern -and $null -eq $failure.InnerException -and
            $failure.Data['AsrePlaneCategory'] -ceq 'TransportFailure' -and $failure.Data['AsrePlaneService'] -ceq $service -and -not $failure.Data.Contains('AsrePlaneStatus')) "A $service transport failure leaked detail or lost its category."
    }

    # ---- Get-AsreAgentPlane --------------------------------------------------------------------------------------------------
    $readback = (@{ properties = @{ agentEndpoint = $AgentEndpoint } } | ConvertTo-Json -Depth 4 -Compress)
    Reset-Az
    Add-Reply @{ StatusCode = 200; Content = $readback }
    $planes = @(Get-AsreAgentPlane -Context $ctx)
    Assert-Test ($planes.Count -eq 1) 'Get-AsreAgentPlane must return only the plane.'
    $plane = $planes[0]
    Assert-Test ($global:AzCalls.Count -eq 3) "Unexpected Azure CLI call count: $($global:AzCalls -join ' | ')."
    Assert-Test ($global:AzCalls[0] -ceq "account show --subscription $Sub --output json --only-show-errors") 'The signed-in subscription was not checked first.'
    Assert-Test ($global:AzCalls[1] -ceq "account get-access-token --subscription $Sub --resource https://management.azure.com --query accessToken --output tsv --only-show-errors") "The ARM token request is wrong: $($global:AzCalls[1])"
    Assert-Test ($global:AzCalls[2] -ceq "account get-access-token --subscription $Sub --resource https://azuresre.dev --query accessToken --output tsv --only-show-errors") "The SRE token request is wrong: $($global:AzCalls[2])"
    Assert-Test ($global:WebCalls.Count -eq 1 -and $global:WebCalls[0].Uri -ceq "https://management.azure.com$Agent`?api-version=2026-01-01" -and $global:WebCalls[0].Method -ceq 'Get' -and $global:WebCalls[0].Headers['Authorization'] -ceq "Bearer $ArmToken") 'The endpoint read-back was not a plain ARM GET of the agent resource with the ARM token.'
    Assert-Test ($plane.agent -ceq $Agent -and $plane.readOnly -eq $false -and $plane.sreEndpoint -ceq $AgentEndpoint -and [object]::ReferenceEquals($plane.context, $ctx)) 'The plane has the wrong agent, endpoint or context.'
    Assert-Test ($plane.armHeaders.Authorization -ceq "Bearer $ArmToken" -and $plane.sreHeaders.Authorization -ceq "Bearer $SreToken" -and $plane.armHeaders.Count -eq 1 -and $plane.sreHeaders.Count -eq 1) 'The plane tokens were not trimmed and stored per audience.'
    Assert-Test ($script:Cleared.Count -eq 0) 'A successful plane was cleared.'
    Reset-Web
    $null = Invoke-AsreAgentPlaneRequest -Plane $plane -Service Sre -Path '/api/v1/x'
    Assert-Test ($global:WebCalls[0].Uri -ceq "$AgentEndpoint/api/v1/x") 'The plane did not route SRE requests to the resolved endpoint.'
    Clear-AsreAgentPlane -Plane $plane
    Assert-Test ($plane.armHeaders.Count -eq 0 -and $plane.sreHeaders.Count -eq 0) 'The plane tokens survived Clear-AsreAgentPlane.'

    Reset-Az; Add-Reply @{ StatusCode = 200; Content = $readback }
    $readOnlyPlane = Get-AsreAgentPlane -Context $ctx -ReadOnly
    Assert-Test ($readOnlyPlane.readOnly -eq $true) '-ReadOnly was not recorded in the plane.'
    Assert-Rejected { Invoke-AsreAgentPlaneRequest -Plane $readOnlyPlane -Service Arm -Method Patch -Path $Agent -Body @{ a = 1 } } 'read-only agent plane' 'A plane created with -ReadOnly allowed a write.'

    # A context without a declared endpoint takes the endpoint the control plane reports.
    $noEndpoint = [hashtable]::new($ctx)
    $noEndpoint.agent = @{ name = $ctx.agent.name; endpoint = $null; resource_id = $Agent }
    Reset-Az; Add-Reply @{ StatusCode = 200; Content = (@{ properties = @{ agentEndpoint = 'https://other-agent.abc.centralus.azuresre.ai' } } | ConvertTo-Json -Depth 4 -Compress) }
    $discovered = Get-AsreAgentPlane -Context $noEndpoint
    Assert-Test ($discovered.sreEndpoint -ceq 'https://other-agent.abc.centralus.azuresre.ai') 'An undeclared endpoint was not taken from ARM.'

    # Refusals before any token is requested.
    Reset-Az
    $brokenContext = [hashtable]::new($ctx)
    $brokenContext.agent = @{ name = $ctx.agent.name; endpoint = $null; resource_id = $Agent.Replace($Sub, '00000009-000a-4000-a000-000000000009') }
    Assert-Fails { Get-AsreAgentPlane -Context $brokenContext } $needsLoader 'A context with a foreign agent resource id was accepted.'
    Assert-Test ($global:AzCalls.Count -eq 0 -and $global:WebCalls.Count -eq 0) 'A malformed context reached Azure.'
    Reset-Az
    $global:AzAccount = (@{ id = '00000009-000a-4000-a000-000000000009'; tenantId = $Tenant; state = 'Enabled' } | ConvertTo-Json -Compress)
    Assert-Fails { Get-AsreAgentPlane -Context $ctx } 'not the enabled deployment subscription and tenant' 'A different signed-in subscription was accepted.'
    Assert-Test ($global:AzCalls.Count -eq 1 -and $global:WebCalls.Count -eq 0) 'A token was requested before the subscription was verified.'
    Reset-Az; $global:AzAccountExit = 1
    Assert-Fails { Get-AsreAgentPlane -Context $ctx } 'cannot read the deployment subscription' 'A failed subscription read was accepted.'
    Assert-Test ($global:AzCalls.Count -eq 1) 'A token was requested after the subscription read failed.'

    # Authentication failures.
    foreach ($case in @(
            @{ Resource = 'https://management.azure.com'; Reply = @{ Exit = 1; Out = 'ERROR transport-secret' }; Message = 'ARM authentication failed\.'; Calls = 2 },
            @{ Resource = 'https://management.azure.com'; Reply = @{ Exit = 0; Out = '  ' }; Message = 'ARM authentication failed\.'; Calls = 2 },
            @{ Resource = 'https://management.azure.com'; Reply = @{ Exit = 0; Out = $null }; Message = 'ARM authentication failed\.'; Calls = 2 },
            @{ Resource = 'https://azuresre.dev'; Reply = @{ Exit = 1; Out = 'ERROR transport-secret' }; Message = 'SRE authentication failed\.'; Calls = 3 },
            @{ Resource = 'https://azuresre.dev'; Reply = @{ Exit = 0; Out = "`n" }; Message = 'SRE authentication failed\.'; Calls = 3 })) {
        Reset-Az
        $global:AzTokens[$case.Resource] = $case.Reply
        $label = "$($case.Resource) / $($case.Message)"
        Assert-Fails { Get-AsreAgentPlane -Context $ctx } "\A$($case.Message)\z" "A token failure was accepted ($label)."
        Assert-Test ($global:AzCalls.Count -eq $case.Calls -and $global:WebCalls.Count -eq 0) "A token failure continued after the failing call ($label)."
    }

    # Read-back failures: the tokens already held must be wiped before the failure escapes.
    $declaredMismatch = (@{ properties = @{ agentEndpoint = 'https://impostor.abc.centralus.azuresre.ai' } } | ConvertTo-Json -Depth 4 -Compress)
    foreach ($case in @(
            @{ Name = 'a forbidden read'; Reply = @{ StatusCode = 403; Content = '{"error":{"code":"AuthorizationFailed","message":"body-secret"}}' }; Pattern = 'HTTP 403 \(AuthorizationFailed\)' },
            @{ Name = 'a missing agent'; Reply = @{ StatusCode = 404; Content = '{}' }; Pattern = 'HTTP 404' },
            @{ Name = 'a transport failure'; Reply = @{ Throw = 'transport-secret Bearer x' }; Pattern = 'failed in transport' },
            @{ Name = 'an invalid body'; Reply = @{ StatusCode = 200; Content = 'body-secret' }; Pattern = 'not valid JSON' },
            @{ Name = 'a body without an endpoint'; Reply = @{ StatusCode = 200; Content = '{"properties":{}}' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'an empty body'; Reply = @{ StatusCode = 200; Content = '' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'a numeric endpoint'; Reply = @{ StatusCode = 200; Content = '{"properties":{"agentEndpoint":5}}' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'an http endpoint'; Reply = @{ StatusCode = 200; Content = '{"properties":{"agentEndpoint":"http://a.azuresre.ai"}}' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'a foreign endpoint'; Reply = @{ StatusCode = 200; Content = '{"properties":{"agentEndpoint":"https://evil.example.com"}}' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'an endpoint with a path'; Reply = @{ StatusCode = 200; Content = '{"properties":{"agentEndpoint":"https://a.azuresre.ai/x"}}' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'an endpoint with a port'; Reply = @{ StatusCode = 200; Content = '{"properties":{"agentEndpoint":"https://a.azuresre.ai:443"}}' }; Pattern = 'no valid SRE endpoint yet' },
            @{ Name = 'an endpoint that contradicts the context'; Reply = @{ StatusCode = 200; Content = $declaredMismatch }; Pattern = 'agent endpoint differs from the deployment context' })) {
        Reset-Az; Add-Reply $case.Reply
        $label = $case.Name
        Assert-Fails { Get-AsreAgentPlane -Context $ctx } $case.Pattern "Get-AsreAgentPlane accepted $label."
        Assert-Test ($script:Cleared.Count -eq 1 -and $script:Cleared[0].armHeaders.Count -eq 0 -and $script:Cleared[0].sreHeaders.Count -eq 0) "The tokens were not cleared after $label."
    }

    # No token or body ever reached a verbose, warning or output stream while a plane was built and used.
    Reset-Az; Add-Reply @{ StatusCode = 200; Content = $readback }
    $streams = & { Get-AsreAgentPlane -Context $ctx -Verbose -WarningAction Continue } 6>&1 5>&1 4>&1 3>&1 2>&1
    $text = (@($streams | Where-Object { $_ -isnot [System.Collections.IDictionary] } | ForEach-Object { "$_" }) -join "`n")
    Assert-Test ($text -cnotmatch 'token-sentinel|Bearer') 'A token appeared in a stream while the plane was built.'
}
finally {
    Remove-Item -LiteralPath 'Function:\az' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath 'Function:\Invoke-WebRequest' -Force -ErrorAction SilentlyContinue
}

Write-Output "Agent plane helper fixtures passed ($script:AssertionCount assertions); no Azure, terraform or network command was executed."
