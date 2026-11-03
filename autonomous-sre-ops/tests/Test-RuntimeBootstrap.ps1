$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$Jobs = 'https://management.azure.com/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/jobs'
$Api = '?api-version=2025-07-01'
$script:StartedTemplates = @()
$script:ExecutionStatus = 'Succeeded'
$script:UnexpectedMount = $false
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $Repo 'scripts/Initialize-RuntimeSsh.ps1'), [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw 'The bootstrap script did not parse.' }
$definition = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Start-JobAndWait'
}, $true))
if ($definition.Count -ne 1) { throw 'The production job-start function was not uniquely identified.' }
. ([scriptblock]::Create($definition[0].Extent.Text))

function Invoke-AzJson {
    param([string[]]$Arguments)
    if (($Arguments -join ' ') -match '/executions/') {
        return [pscustomobject]@{ properties = [pscustomobject]@{ status = $script:ExecutionStatus } }
    }
    $resource = @'
{"properties":{"template":{"volumes":null,"initContainers":[],"containers":[{"name":"operations","image":"acrasre8a327010f9.azurecr.io/asre-operations@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","command":["python","-m","operations.worker"],"args":["queue"],"resources":{"cpu":0.25,"memory":"0.5Gi","ephemeralStorage":"1Gi"},"env":[{"name":"ASRE_EXECUTION_MODE","value":"Review","secretRef":null}],"volumeMounts":[]}]}}}
'@ | ConvertFrom-Json
    if ($script:UnexpectedMount) {
        $resource.properties.template.containers[0].volumeMounts = @(@{ volumeName = 'unexpected'; mountPath = '/unexpected' })
    }
    return $resource
}

function Invoke-ArmPost {
    param([string]$Uri, [object]$Body)
    if ($Uri -notmatch '/jobs/job-asre-(bootstrap|worker)-cus/start\?api-version=2025-07-01$') {
        throw 'Unexpected fixture job-start endpoint.'
    }
    $script:StartedTemplates += $Body
    return [pscustomobject]@{ name = 'fixture-execution' }
}

function Start-Sleep { throw 'A terminal fixture execution must not be polled again.' }
function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$known = 'fixture-only-public-host-trust'
$execution = Start-JobAndWait -Job job-asre-bootstrap-cus -Mode bootstrap -HostTrust $known
Assert-Test ($execution -eq 'fixture-execution') 'A real terminal success was not required.'
$body = $script:StartedTemplates[0]
Assert-Test (($body.Keys -join ',') -eq 'containers') 'GET-only template fields leaked into the start request.'
$container = $body.containers[0]
Assert-Test ($container.args[0] -eq 'bootstrap' -and $container.resources.Keys.Count -eq 2) 'Execution mode or resource projection differs from the fixed contract.'
Assert-Test ($container.Keys.Count -eq 6 -and -not $container.ContainsKey('volumeMounts')) 'The container projection admitted unsupported properties.'
Assert-Test ($container.env.Count -eq 2 -and $container.env[0].Keys.Count -eq 2) 'Environment variables include GET-only metadata.'
Assert-Test ($container.env[1].value -eq [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($known))) 'Authenticated host trust was not forwarded intact.'

$null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust ''
Assert-Test ($script:StartedTemplates[1].containers[0].env.Count -eq 1) 'Bootstrap trust mutated a subsequent operational job template.'
$script:UnexpectedMount = $true
$rejected = $false
try { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' }
catch { $rejected = $_.Exception.Message -match 'does not admit volumes' }
Assert-Test ($rejected -and $script:StartedTemplates.Count -eq 2) 'Unexpected mounted code was silently accepted or submitted.'
$script:UnexpectedMount = $false
$script:ExecutionStatus = 'Failed'
$rejected = $false
try { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' }
catch { $rejected = $_.Exception.Message -match 'real verify execution failed' }
Assert-Test $rejected 'An accepted but failed execution was reported as success.'
$install = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.ForEachStatementAst] -and
    $node.Extent.Text.Contains('Install-RuntimePublicKey.sh')
}, $true))
Assert-Test ($install.Count -eq 1) 'The production public-key installation loop was not uniquely identified.'
$public = 'ssh-ed25519 AQIDBA=='
$script:InstalledTargets = @()
function Invoke-AzJson {
    param([string[]]$Arguments)
    $index = [Array]::IndexOf($Arguments, '--parameters')
    Assert-Test ($index -ge 0 -and $Arguments.Count -eq $index + 2) 'Key installation did not send exactly one parameter.'
    Assert-Test ($Arguments[$index + 1] -eq 'public_key=AQIDBA==') 'Run Command received a space-bearing key or lost its Base64 padding.'
    $script:InstalledTargets += $Arguments[[Array]::IndexOf($Arguments, '--name') + 1]
    return [pscustomobject]@{ value = @([pscustomobject]@{ message = "ASRE_RUNTIME_SSH_INSTALLED`n" }) }
}
& ([scriptblock]::Create('param([string]$PSScriptRoot)' + "`n" + $install[0].Extent.Text)) (Join-Path $Repo 'scripts')
Assert-Test (($script:InstalledTargets -join ',') -eq 'vm-asre-linux-cus,vm-asre-patch-cus,vm-asre-backup-cus') 'The production loop did not verify all three fixed targets.'
Write-Output 'Runtime bootstrap projection fixtures passed; no Azure operation was executed.'
