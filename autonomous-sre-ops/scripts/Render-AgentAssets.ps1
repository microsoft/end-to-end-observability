#Requires -Version 7.5
<#
.SYNOPSIS
    Renders the committed agent assets for the deployment context, or verifies a rendered tree.
.DESCRIPTION
    Render: writes a NEW directory (default out/agent-assets/<UTC stamp>-<8 hex of the context hash>) with every
    asset rendered for the context plus render-receipt.json. Verify: re-renders in memory and requires the tree
    to match byte for byte. The context comes from -ContextPath, else $env:ASRE_CONTEXT_FILE, else
    infra\environments\context.generated.json (a context that carries synthetic values is refused).
    Prints the root, the file count and the receipt SHA-256 (never a context value); exits 1 on any failure.
.EXAMPLE
    ./scripts/Render-AgentAssets.ps1
    ./scripts/Render-AgentAssets.ps1 -VerifyRoot ./out/agent-assets/<folder>
#>
[CmdletBinding(DefaultParameterSetName = 'Render')]
param(
    [Parameter(ParameterSetName = 'Render')]
    [Parameter(ParameterSetName = 'Verify')]
    [string]$ContextPath,

    [Parameter(ParameterSetName = 'Render')]
    [string]$OutputRoot,

    [Parameter(Mandatory, ParameterSetName = 'Verify')]
    [string]$VerifyRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')
. (Join-Path $PSScriptRoot 'AgentAssets.Common.ps1')

try {
    $context = if ([string]::IsNullOrWhiteSpace($ContextPath)) { Get-AsreContext } else { Get-AsreContext -Path $ContextPath }
    if ($PSCmdlet.ParameterSetName -ceq 'Verify') {
        $result = Assert-AsreRenderedAssets -Context $context -Root $VerifyRoot
        Write-Output 'Rendered assets verified.'
    }
    else {
        $arguments = @{ Context = $context }
        if (-not [string]::IsNullOrWhiteSpace($OutputRoot)) { $arguments['OutputRoot'] = $OutputRoot }
        $result = Export-AsreAgentAssets @arguments
        Write-Output 'Agent assets rendered.'
    }
    Write-Output "Root: $($result.Root)"
    Write-Output "Files: $($result.FileCount)"
    Write-Output "Receipt SHA-256: $($result.ReceiptSha256)"
}
catch {
    [Console]::Error.WriteLine("Render-AgentAssets failed: $($_.Exception.Message)")
    exit 1
}
