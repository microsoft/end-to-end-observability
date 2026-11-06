# Authenticated access to the Azure SRE Agent control plane (ARM) and data plane for the Phase-1 scripts.
#
# Get-AsreAgentPlane signs in with the operator's Azure CLI session, resolves the agent endpoint from ARM
# and returns a plane object. Invoke-AsreAgentPlaneRequest is the only way scripts should call either plane:
# it allow-lists the host, refuses redirects and unusual paths, confines ARM writes to the agent resource
# and returns either a safe result or a fixed error (response bodies and tokens are never echoed).
# Scripts must call Clear-AsreAgentPlane in a finally block.

if (-not (Get-Command Assert-AsreSubscription -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'Context.Common.ps1')
}

function Get-AsreMember {
    # Walks nested dictionaries; returns $null when any step is missing.
    param($Value, [string[]]$Path)
    $current = $Value
    foreach ($name in $Path) {
        if ($current -isnot [System.Collections.IDictionary] -or -not $current.Contains($name)) { return $null }
        $current = $current[$name]
    }
    return $current
}

function Get-AsreResponseValue {
    param($Response, [string]$Name)
    if ($Response -is [System.Collections.IDictionary]) {
        if ($Response.Contains($Name)) { return $Response[$Name] }
        return $null
    }
    $property = $Response.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

function Assert-AsreExact {
    # Recursive strict comparison that also rejects unexpected fields, array length changes and type changes.
    param($Expected, $Actual, [string]$Path = 'value')
    $e = ConvertTo-Json -InputObject $Expected -Depth 40 | ConvertFrom-Json -AsHashtable -NoEnumerate -DateKind String
    $a = ConvertTo-Json -InputObject $Actual -Depth 40 | ConvertFrom-Json -AsHashtable -NoEnumerate -DateKind String
    if ($e -is [Collections.IDictionary]) {
        if ($a -isnot [Collections.IDictionary] -or $e.Count -ne $a.Count) { throw "Strict readback changed $Path" }
        foreach ($key in $e.Keys) {
            if (@($a.Keys) -cnotcontains $key) { throw "Strict readback omitted $Path.$key" }
            Assert-AsreExact -Expected $e[$key] -Actual $a[$key] -Path "$Path.$key"
        }
    }
    elseif ($e -is [array]) {
        if ($a -isnot [array] -or $e.Count -ne $a.Count) { throw "Strict readback changed $Path" }
        for ($i = 0; $i -lt $e.Count; $i++) { Assert-AsreExact -Expected $e[$i] -Actual $a[$i] -Path "$Path[$i]" }
    }
    elseif (($null -eq $e) -ne ($null -eq $a) -or
        ($null -ne $e -and ($e.GetType() -ne $a.GetType() -or $e -cne $a))) {
        throw "Strict readback changed $Path"
    }
}

function Get-AsrePlaneErrorCode {
    # Returns only a short error code token from an ARM or SRE error body, never the message.
    param($Response)
    $content = Get-AsreResponseValue -Response $Response -Name 'Content'
    if ($content -isnot [string] -or $content.Length -gt 16384) { return $null }
    try { $body = ConvertFrom-Json -InputObject $content -AsHashtable -NoEnumerate -Depth 8 -ErrorAction Stop }
    catch { return $null }
    $code = Get-AsreMember -Value $body -Path @('error', 'code')
    if ($code -isnot [string]) { $code = Get-AsreMember -Value $body -Path @('code') }
    if ($code -is [string] -and [regex]::IsMatch($code, '\A[A-Za-z0-9_.-]{1,80}\z')) { return $code }
    return $null
}

function New-AsrePlaneFailure {
    param([string]$Message, [string]$Category, [string]$Service, [string]$Method, $Status, $Code)
    $failure = [System.InvalidOperationException]::new($Message)
    $failure.Data['AsrePlaneCategory'] = $Category
    $failure.Data['AsrePlaneService'] = $Service
    $failure.Data['AsrePlaneMethod'] = $Method
    if ($null -ne $Status) { $failure.Data['AsrePlaneStatus'] = $Status }
    if ($null -ne $Code) { $failure.Data['AsrePlaneErrorCode'] = $Code }
    return $failure
}

function Invoke-AsreAgentPlaneRequest {
    # Returns @{ status; data; etag }. A 404 with -AllowNotFound returns status 404 and data $null.
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$Plane,
        [Parameter(Mandatory)][ValidateSet('Arm', 'Sre', IgnoreCase = $false)][string]$Service,
        [ValidateSet('Get', 'Put', 'Patch', 'Post', 'Delete', IgnoreCase = $false)][string]$Method = 'Get',
        [Parameter(Mandatory)][string]$Path,
        $Body,
        [string]$IfMatch,
        [switch]$AllowNotFound
    )
    foreach ($key in 'agent', 'readOnly', 'armHeaders', 'sreHeaders', 'sreEndpoint', 'context') {
        if (-not $Plane.Contains($key)) { throw 'The agent plane is not valid.' }
    }
    if ($Method -cne 'Get' -and $Plane['readOnly']) { throw 'A read-only agent plane cannot send non-GET requests.' }
    if ($Method -cin @('Get', 'Delete') -and $null -ne $Body) { throw "A $Method request cannot carry a body." }
    if ($null -ne $Body -and $Body -isnot [System.Collections.IDictionary] -and $Body -isnot [System.Array]) {
        throw 'The agent plane body must be an object or an array.'
    }
    if ($IfMatch -and -not [regex]::IsMatch($IfMatch, '\A[\x21-\x7E][\x20-\x7E]{0,255}\z')) { throw 'The If-Match value is not a plain ETag.' }

    $queryIndex = $Path.IndexOf('?')
    $pathOnly = if ($queryIndex -ge 0) { $Path.Substring(0, $queryIndex) } else { $Path }
    $query = if ($queryIndex -ge 0) { $Path.Substring($queryIndex + 1) } else { $null }
    if (-not [regex]::IsMatch($pathOnly, '\A/[-A-Za-z0-9._~:()/]+\z') -or $Path.Contains('..') -or $Path.Contains('//') -or
        ($null -ne $query -and -not [regex]::IsMatch($query, '\A[-A-Za-z0-9._~:()/=&%$'',*+]+\z'))) {
        throw 'The agent plane rejected the request path.'
    }

    if ($Service -ceq 'Arm') {
        $subscription = [string](Get-AsreMember -Value $Plane['context'] -Path @('subscription_id'))
        if (-not $pathOnly.StartsWith("/subscriptions/$subscription/", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw 'ARM requests must stay inside the deployment subscription.'
        }
        if ($Method -cne 'Get') {
            $agent = [string]$Plane['agent']
            $isAgent = $pathOnly.Equals($agent, [System.StringComparison]::OrdinalIgnoreCase)
            if (-not ($isAgent -or $pathOnly.StartsWith("$agent/", [System.StringComparison]::OrdinalIgnoreCase))) {
                throw 'ARM writes must target the agent resource.'
            }
            if ($isAgent -and $Method -cne 'Patch') { throw 'Only PATCH may target the agent resource itself; Terraform owns its lifecycle.' }
        }
        $base = 'https://management.azure.com'
        $hostName = 'management.azure.com'
        $source = $Plane['armHeaders']
    }
    else {
        if (-not $pathOnly.StartsWith('/api/', [System.StringComparison]::Ordinal)) { throw 'SRE requests must use the /api/ routes.' }
        $endpoint = $Plane['sreEndpoint']
        if ($endpoint -isnot [string] -or -not [regex]::IsMatch($endpoint, '\Ahttps://[a-z0-9][a-z0-9.-]*\.azuresre\.ai\z')) {
            throw 'The agent plane has no SRE endpoint.'
        }
        $base = $endpoint
        $hostName = $endpoint.Substring('https://'.Length)
        $source = $Plane['sreHeaders']
    }
    if ($source -isnot [System.Collections.IDictionary] -or -not $source.Contains('Authorization')) {
        throw 'The agent plane has been cleared.'
    }
    $headers = @{}
    foreach ($name in $source.Keys) { $headers[$name] = $source[$name] }
    if ($IfMatch) { $headers['If-Match'] = $IfMatch }

    $uri = $base + $Path
    $parsed = $null
    if (-not [System.Uri]::TryCreate($uri, [System.UriKind]::Absolute, [ref]$parsed) -or $parsed.Scheme -cne 'https' -or
        $parsed.Host -cne $hostName -or $parsed.Port -ne 443 -or $parsed.UserInfo -or $parsed.Fragment -or
        $parsed.AbsolutePath -cne $pathOnly) {
        throw 'The agent plane rejected the request URI.'
    }

    $request = @{ Uri = $uri; Method = $Method; Headers = $headers; MaximumRedirection = 0
        TimeoutSec = 60; ErrorAction = 'Stop'; SkipHttpErrorCheck = $true }
    if ($null -ne $Body) {
        $request['Body'] = ConvertTo-Json -InputObject $Body -Depth 40
        $request['ContentType'] = 'application/json'
    }
    Write-Verbose "$Service $Method $pathOnly"

    try { $response = Invoke-WebRequest @request }
    catch {
        throw (New-AsrePlaneFailure -Message 'The agent plane request failed in transport; response and authentication details are suppressed. Re-read before retrying.' `
                -Category 'TransportFailure' -Service $Service -Method $Method -Status $null -Code $null)
    }
    $status = Get-AsreResponseValue -Response $response -Name 'StatusCode'
    if ($status -isnot [int] -or $status -lt 100 -or $status -gt 599) {
        throw (New-AsrePlaneFailure -Message 'The agent plane returned an invalid HTTP status; details are suppressed.' `
                -Category 'InvalidStatus' -Service $Service -Method $Method -Status $null -Code $null)
    }
    if ($status -eq 404 -and $AllowNotFound) { return @{ status = 404; data = $null; etag = $null } }
    if ($status -lt 200 -or $status -ge 300) {
        $code = Get-AsrePlaneErrorCode -Response $response
        $detail = if ($code) { " ($code)" } else { '' }
        throw (New-AsrePlaneFailure -Message "The agent plane returned HTTP $status$detail; response details are suppressed. Re-read before retrying." `
                -Category 'HttpFailure' -Service $Service -Method $Method -Status $status -Code $code)
    }

    $data = $null
    $content = Get-AsreResponseValue -Response $response -Name 'Content'
    if ($content -is [byte[]] -and $content.Length -eq 0) { $content = $null }
    if ($null -ne $content -and $content -isnot [string]) {
        throw (New-AsrePlaneFailure -Message 'The agent plane response was not JSON text; details are suppressed.' `
                -Category 'ParseFailure' -Service $Service -Method $Method -Status $status -Code $null)
    }
    if ($content -is [string] -and -not [string]::IsNullOrWhiteSpace($content)) {
        try { $data = $content | ConvertFrom-Json -AsHashtable -DateKind String }
        catch {
            throw (New-AsrePlaneFailure -Message 'The agent plane response was not valid JSON; details are suppressed.' `
                    -Category 'ParseFailure' -Service $Service -Method $Method -Status $status -Code $null)
        }
    }
    $etag = $null
    $responseHeaders = Get-AsreResponseValue -Response $response -Name 'Headers'
    if ($responseHeaders -is [System.Collections.IDictionary]) {
        try { $raw = $responseHeaders['ETag'] } catch { $raw = $null }
        if ($null -ne $raw) { $etag = [string]@($raw)[0] }
    }
    return @{ status = $status; data = $data; etag = $etag }
}

function Assert-AsreAgentContext {
    param($Context)
    $subscription = Get-AsreMember -Value $Context -Path @('subscription_id')
    $group = Get-AsreMember -Value $Context -Path @('resource_groups', 'control')
    $name = Get-AsreMember -Value $Context -Path @('agent', 'name')
    $id = Get-AsreMember -Value $Context -Path @('agent', 'resource_id')
    if ($subscription -isnot [string] -or $group -isnot [string] -or $name -isnot [string] -or $id -isnot [string] -or
        $id -cne "/subscriptions/$subscription/resourceGroups/$group/providers/Microsoft.App/agents/$name") {
        throw 'The agent plane needs a context returned by Get-AsreContext.'
    }
}

function Get-AsreAgentPlane {
    # Returns @{ agent; readOnly; armHeaders; sreHeaders; sreEndpoint; context }. Tokens live only in the header tables.
    [CmdletBinding()]
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context, [switch]$ReadOnly)
    Assert-AsreAgentContext -Context $Context
    Assert-AsreSubscription -Context $Context
    $subscription = $Context['subscription_id']
    $armToken = & az account get-access-token --subscription $subscription --resource https://management.azure.com --query accessToken --output tsv --only-show-errors
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace("$armToken")) { throw 'ARM authentication failed.' }
    $sreToken = & az account get-access-token --subscription $subscription --resource https://azuresre.dev --query accessToken --output tsv --only-show-errors
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace("$sreToken")) { throw 'SRE authentication failed.' }
    $plane = @{
        agent       = $Context['agent']['resource_id']
        readOnly    = [bool]$ReadOnly
        armHeaders  = @{ Authorization = "Bearer $("$armToken".Trim())" }
        sreHeaders  = @{ Authorization = "Bearer $("$sreToken".Trim())" }
        sreEndpoint = $null
        context     = $Context
    }
    $armToken = $null
    $sreToken = $null
    try {
        $readback = Invoke-AsreAgentPlaneRequest -Plane $plane -Service Arm -Method Get -Path "$($plane['agent'])?api-version=2026-01-01"
        $endpoint = Get-AsreMember -Value $readback['data'] -Path @('properties', 'agentEndpoint')
        if ($endpoint -isnot [string] -or -not [regex]::IsMatch($endpoint, '\Ahttps://[a-z0-9][a-z0-9.-]*\.azuresre\.ai\z')) {
            throw 'The agent has no valid SRE endpoint yet.'
        }
        $declared = Get-AsreMember -Value $Context -Path @('agent', 'endpoint')
        if ($declared -is [string] -and $declared -cne $endpoint) { throw 'The agent endpoint differs from the deployment context.' }
        $plane['sreEndpoint'] = $endpoint
    }
    catch {
        Clear-AsreAgentPlane -Plane $plane
        throw
    }
    return $plane
}

function Clear-AsreAgentPlane {
    param($Plane)
    if ($Plane -isnot [System.Collections.IDictionary]) { return }
    foreach ($name in 'armHeaders', 'sreHeaders') {
        if ($Plane.Contains($name) -and $Plane[$name] -is [System.Collections.IDictionary]) { $Plane[$name].Clear() }
    }
}
