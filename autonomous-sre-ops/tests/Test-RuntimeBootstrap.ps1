$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$SshScript = Join-Path $Repo 'scripts\Initialize-RuntimeSsh.ps1'
$global:AssertionCount = 0

$null = Use-AsreTestContext
$AsreContext = Get-AsreContext
$AltContext = $null
$null = Use-AsreTestContext -Name alt
$AltContext = Get-AsreContext
$null = Use-AsreTestContext
$Roles = @('linux', 'patch', 'backup')
$Jobs = "https://management.azure.com/subscriptions/$($AsreContext.subscription_id)/resourceGroups/$($AsreContext.resource_groups.control)/providers/Microsoft.App/jobs"
$Api = '?api-version=2025-07-01'
$ContextValue = (@{ subscription_id = $AsreContext.subscription_id; name_suffix = $AsreContext.name_suffix } | ConvertTo-Json -Compress)
$Work = Join-Path ([IO.Path]::GetTempPath()) "asre-bootstrap-fixture-$([guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $Work

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

# ---- the production functions, extracted from the script by AST --------------------------------------------------
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($SshScript, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'The bootstrap script did not parse.' }
$definition = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Start-JobAndWait'
}, $true))
if ($definition.Count -ne 1) { throw 'The production job-start function was not uniquely identified.' }
. ([scriptblock]::Create($definition[0].Extent.Text))
$install = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.ForEachStatementAst] -and
    $node.Extent.Text.Contains('Install-RuntimePublicKey.sh')
}, $true))
Assert-Test ($install.Count -eq 1) 'The production public-key installation loop was not uniquely identified.'

function Reset-Job {
    $script:StartedTemplates = @()
    $script:StatusQueue = [System.Collections.Generic.Queue[string]]::new()
    $script:ExecutionStatus = 'Succeeded'
    $script:Sleeps = 0
    $script:UnexpectedMount = $false
    $script:InitContainer = $false
    $script:ContainerName = 'operations'
    $script:ExtraContainer = $false
    $script:TemplateEnv = @(
        @{ name = 'ASRE_EXECUTION_MODE'; value = 'Review'; secretRef = $null },
        @{ name = 'ASRE_CONTEXT'; value = $ContextValue; secretRef = $null }
    )
}

function Invoke-AzJson {
    param([string[]]$Arguments)
    if (($Arguments -join ' ') -match '/executions/') {
        $status = if ($script:StatusQueue.Count -gt 0) { $script:StatusQueue.Dequeue() } else { $script:ExecutionStatus }
        return [pscustomobject]@{ properties = [pscustomobject]@{ status = $status } }
    }
    $container = @{
        name = $script:ContainerName
        image = 'acrasre8a327010f9.azurecr.io/asre-operations@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        command = @('python', '-m', 'operations.worker')
        args = @('queue')
        resources = @{ cpu = 0.25; memory = '0.5Gi'; ephemeralStorage = '1Gi' }
        env = @($script:TemplateEnv)
        volumeMounts = @(if ($script:UnexpectedMount) { @{ volumeName = 'unexpected'; mountPath = '/unexpected' } })
    }
    $containers = @($container)
    if ($script:ExtraContainer) { $containers += @($container) }
    return (@{
        properties = @{
            template = @{
                volumes = $null
                initContainers = @(if ($script:InitContainer) { @{ name = 'sidecar'; image = 'x' } })
                containers = $containers
            }
        }
    } | ConvertTo-Json -Depth 12 | ConvertFrom-Json)
}

function Invoke-ArmPost {
    param([string]$Uri, [object]$Body)
    if ($Uri -notmatch '/jobs/job-asre-(bootstrap|worker)-cus/start\?api-version=2025-07-01$') {
        throw 'Unexpected fixture job-start endpoint.'
    }
    $script:StartedTemplates += $Body
    return [pscustomobject]@{ name = 'fixture-execution' }
}

function Start-Sleep {
    param([int]$Seconds)
    $script:Sleeps++
    if ($script:Sleeps -gt 5) { throw 'The fixture execution was polled more often than expected.' }
}

try {
    # ---- the start request is a strict projection that carries every template variable ---------------------------
    Reset-Job
    $known = 'fixture-only-public-host-trust'
    $execution = Start-JobAndWait -Job job-asre-bootstrap-cus -Mode bootstrap -HostTrust $known
    Assert-Test ($execution -ceq 'fixture-execution') 'A real terminal success was not required.'
    Assert-Test ($script:Sleeps -eq 0) 'A terminal fixture execution was polled again.'
    $body = $script:StartedTemplates[0]
    Assert-Test (($body.Keys -join ',') -ceq 'containers') 'GET-only template fields leaked into the start request.'
    $container = $body.containers[0]
    Assert-Test ($container.args[0] -ceq 'bootstrap' -and $container.resources.Keys.Count -eq 2) 'Execution mode or resource projection differs from the fixed contract.'
    Assert-Test ($container.Keys.Count -eq 6 -and -not $container.ContainsKey('volumeMounts')) 'The container projection admitted unsupported properties.'
    Assert-Test ($container.env.Count -eq 3 -and $container.env[0].Keys.Count -eq 2) 'Environment variables include GET-only metadata or lost one.'
    Assert-Test (($container.env | ForEach-Object { $_.name }) -join ',' -ceq 'ASRE_EXECUTION_MODE,ASRE_CONTEXT,ASRE_SSH_KNOWN_HOSTS_B64') 'The template variables were reordered or dropped.'
    $carriedContext = @($container.env | Where-Object { $_.name -ceq 'ASRE_CONTEXT' })
    Assert-Test ($carriedContext.Count -eq 1 -and $carriedContext[0].value -ceq $ContextValue) 'The patched job body dropped or altered ASRE_CONTEXT.'
    Assert-Test ($container.env[2].value -ceq [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($known))) 'Authenticated host trust was not forwarded intact.'
    Assert-Test ($container.command.Count -eq 3 -and $container.image -match '@sha256:[0-9a-f]{64}$') 'The digest-pinned image or command was not carried.'

    $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust ''
    $second = $script:StartedTemplates[1].containers[0]
    Assert-Test ($second.env.Count -eq 2 -and $second.args[0] -ceq 'verify') 'Bootstrap trust mutated a subsequent operational job template.'

    # A secret reference is carried as a reference, never resolved or dropped.
    Reset-Job
    $script:TemplateEnv += @{ name = 'ASRE_FIXTURE_SECRET'; value = $null; secretRef = 'fixture-secret-ref' }
    $script:TemplateEnv += @{ name = 'ASRE_FIXTURE_EMPTY_REF'; value = 'plain'; secretRef = '' }
    $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust ''
    $carried = @($script:StartedTemplates[0].containers[0].env)
    $secret = @($carried | Where-Object { $_.name -ceq 'ASRE_FIXTURE_SECRET' })
    $plain = @($carried | Where-Object { $_.name -ceq 'ASRE_FIXTURE_EMPTY_REF' })
    Assert-Test ($carried.Count -eq 4 -and $secret.Count -eq 1 -and $secret[0].Keys.Count -eq 2 -and $secret[0].secretRef -ceq 'fixture-secret-ref' -and -not $secret[0].ContainsKey('value')) 'A secret-referenced variable was not carried as a reference.'
    Assert-Test ($plain.Count -eq 1 -and $plain[0].value -ceq 'plain' -and -not $plain[0].ContainsKey('secretRef')) 'A plain variable with an empty reference was mishandled.'

    # ---- the deployment context carried by the template is mandatory and must be the requested one --------------
    $cases = @(
        @{ Name = 'a template without ASRE_CONTEXT'; Pattern = 'does not carry the deployment context'; Env = @(@{ name = 'ASRE_EXECUTION_MODE'; value = 'Review'; secretRef = $null }) },
        @{ Name = 'a template with an empty ASRE_CONTEXT'; Pattern = 'does not carry the deployment context'; Env = @(@{ name = 'ASRE_CONTEXT'; value = ' '; secretRef = $null }) },
        @{ Name = 'a template with a secret-referenced ASRE_CONTEXT'; Pattern = 'does not carry the deployment context'; Env = @(@{ name = 'ASRE_CONTEXT'; value = $null; secretRef = 'ctx' }) },
        @{ Name = 'a template with a duplicated ASRE_CONTEXT'; Pattern = 'does not carry the deployment context'; Env = @(@{ name = 'ASRE_CONTEXT'; value = $ContextValue; secretRef = $null }, @{ name = 'ASRE_CONTEXT'; value = $ContextValue; secretRef = $null }) },
        @{ Name = 'a template with a lower-case context name'; Pattern = 'does not carry the deployment context'; Env = @(@{ name = 'asre_context'; value = $ContextValue; secretRef = $null }) },
        @{ Name = 'a template with unreadable context JSON'; Pattern = 'deployment context is unreadable'; Env = @(@{ name = 'ASRE_CONTEXT'; value = '{not json'; secretRef = $null }) },
        @{ Name = 'a template whose context is another subscription'; Pattern = 'belongs to another subscription'; Env = @(@{ name = 'ASRE_CONTEXT'; value = (@{ subscription_id = $AltContext.subscription_id } | ConvertTo-Json -Compress); secretRef = $null }) },
        @{ Name = 'a template whose context has a different-case subscription'; Pattern = 'belongs to another subscription'; Env = @(@{ name = 'ASRE_CONTEXT'; value = (@{ subscription_id = $AsreContext.subscription_id.ToUpperInvariant() } | ConvertTo-Json -Compress); secretRef = $null }) },
        @{ Name = 'a template whose context has no subscription'; Pattern = 'belongs to another subscription'; Env = @(@{ name = 'ASRE_CONTEXT'; value = '{"name_suffix":"x"}'; secretRef = $null }) },
        @{ Name = 'a template whose context is not an object'; Pattern = 'belongs to another subscription'; Env = @(@{ name = 'ASRE_CONTEXT'; value = '[1]'; secretRef = $null }) }
    )
    foreach ($case in $cases) {
        Reset-Job
        $script:TemplateEnv = @($case.Env)
        Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' } $case.Pattern "$($case.Name) was accepted."
        Assert-Test ($script:StartedTemplates.Count -eq 0) "$($case.Name) still submitted a job."
    }
    Reset-Job
    Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' -Context $AltContext } 'belongs to another subscription' 'A job of the first deployment was started for the second one.'
    Assert-Test ($script:StartedTemplates.Count -eq 0) 'A job of another deployment was submitted.'

    # ---- unexpected template shapes -------------------------------------------------------------------------------
    Reset-Job
    $script:UnexpectedMount = $true
    Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' } 'does not admit volumes' 'Unexpected mounted code was silently accepted.'
    $script:UnexpectedMount = $false
    $script:InitContainer = $true
    Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' } 'does not admit volumes' 'An initialization sidecar was accepted.'
    $script:InitContainer = $false
    $script:ExtraContainer = $true
    Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' } 'Unexpected runtime job template' 'A second container was accepted.'
    $script:ExtraContainer = $false
    $script:ContainerName = 'other'
    Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' } 'Unexpected runtime job template' 'An unexpected container name was accepted.'
    Assert-Test ($script:StartedTemplates.Count -eq 0) 'An unexpected template shape was still submitted.'

    # ---- execution outcomes ---------------------------------------------------------------------------------------
    foreach ($terminal in 'Failed', 'Stopped', 'Degraded') {
        Reset-Job
        $script:ExecutionStatus = $terminal
        Assert-Fails { $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust '' } 'real verify execution failed' "An execution that ended $terminal was reported as success."
    }
    Reset-Job
    foreach ($state in 'Running', 'Running') { $script:StatusQueue.Enqueue($state) }
    $script:ExecutionStatus = 'Succeeded'
    $null = Start-JobAndWait -Job job-asre-worker-cus -Mode verify -HostTrust ''
    Assert-Test ($script:Sleeps -eq 2) 'A running execution was not polled until it succeeded.'

    # ---- the production public-key installation loop -------------------------------------------------------------
    $public = 'ssh-ed25519 AQIDBA=='
    $script:Installed = @()
    $script:InstallMarker = "ASRE_RUNTIME_SSH_INSTALLED`n"
    function Invoke-AzJson {
        param([string[]]$Arguments)
        $index = [Array]::IndexOf($Arguments, '--parameters')
        Assert-Test ($index -ge 0 -and $Arguments.Count -eq $index + 2 -and $Arguments[$index + 1] -ceq 'public_key=AQIDBA==') 'Run Command received a space-bearing key, extra parameters or lost its Base64 padding.'
        Assert-Test (($Arguments[0..2] -join ' ') -ceq 'vm run-command invoke' -and $Arguments[[Array]::IndexOf($Arguments, '--command-id') + 1] -ceq 'RunShellScript') 'Key installation did not use Run Command.'
        Assert-Test ($Arguments[[Array]::IndexOf($Arguments, '--scripts') + 1] -ceq ('@' + (Join-Path (Join-Path $Repo 'scripts') 'Install-RuntimePublicKey.sh'))) 'Key installation did not run the committed script.'
        $script:Installed += ($Arguments[[Array]::IndexOf($Arguments, '--resource-group') + 1] + '/' + $Arguments[[Array]::IndexOf($Arguments, '--name') + 1])
        return [pscustomobject]@{ value = @([pscustomobject]@{ message = $script:InstallMarker }) }
    }
    $loop = [scriptblock]::Create('param([string]$PSScriptRoot)' + "`n" + $install[0].Extent.Text)
    & $loop (Join-Path $Repo 'scripts')
    $expected = @($Roles | ForEach-Object { "$($AsreContext.targets[$_].resource_group)/$($AsreContext.targets[$_].name)" })
    Assert-Test (($script:Installed -join ',') -ceq ($expected -join ',')) 'The production loop did not verify all three context targets in order.'
    $script:Installed = @()
    $script:InstallMarker = "installed`n"
    Assert-Fails { & $loop (Join-Path $Repo 'scripts') } 'was not verified on linux' 'A missing installation marker was accepted.'
    Assert-Test ($script:Installed.Count -eq 1) 'The installation loop continued after the first unverified target.'

    # ---- the whole script, run in-process against a read-only az fixture -----------------------------------------
    $global:AzCalls = [System.Collections.Generic.List[string]]::new()
    $global:FixturePower = @{}
    $global:FixtureAccountSubscription = $AsreContext.subscription_id
    $global:FixtureAccountTenant = $AsreContext.tenant_id
    function global:az {
        $global:LASTEXITCODE = 0
        $global:AzCalls.Add(($args -join ' '))
        $subscriptionIndex = [Array]::IndexOf($args, '--subscription')
        if ($subscriptionIndex -lt 0 -or $args[$subscriptionIndex + 1] -cne $global:FixtureExpectedSubscription) {
            throw 'The fixture requires the explicit deployment subscription on every az call.'
        }
        if ($args[0] -ceq 'account' -and $args[1] -ceq 'show') {
            return (@{ id = $global:FixtureAccountSubscription; tenantId = $global:FixtureAccountTenant; state = 'Enabled' } | ConvertTo-Json -Compress)
        }
        if ($args[0] -ceq 'vm' -and $args[1] -ceq 'get-instance-view') {
            $id = $args[[Array]::IndexOf($args, '--ids') + 1]
            return (@{ id = $id; instanceView = @{ statuses = @(@{ code = $global:FixturePower[$id] }) } } | ConvertTo-Json -Depth 6 -Compress)
        }
        throw "The read-only fixture refused: $($args[0]) $($args[1])"
    }
    $global:FixtureExpectedSubscription = $AsreContext.subscription_id
    function New-Outputs {
        param([hashtable]$RuntimeJobs = @{ bootstrap = 'job-asre-bootstrap-cus'; worker = 'job-asre-worker-cus' }, [switch]$OmitJobs)
        $directory = Join-Path $Work ([guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Directory -Path $directory
        $targets = @{}
        foreach ($role in $Roles) { $targets[$role] = @{ resource_id = $AsreContext.targets[$role].resource_id } }
        $document = @{ targets = @{ value = $targets } }
        if (-not $OmitJobs) { $document.runtime_jobs = @{ value = $RuntimeJobs } }
        $path = Join-Path $directory 'deployment-outputs.json'
        $document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding utf8
        return $path
    }

    $outputs = New-Outputs -RuntimeJobs @{}
    $lines = @(& $SshScript -OutputsFile $outputs)
    Assert-Test ($global:AzCalls.Count -eq 0 -and ($lines -join ' ') -match 'deliberately disabled') 'A disabled runtime reached Azure.'
    Assert-Fails { & $SshScript -OutputsFile (New-Outputs -OmitJobs) } 'do not describe the runtime jobs' 'Outputs without runtime jobs were accepted.'
    Assert-Fails { & $SshScript -OutputsFile (New-Outputs -RuntimeJobs @{ bootstrap = 'job-asre-bootstrap-cus'; worker = 'job-other' }) } 'fixed runtime job allowlist' 'An unexpected worker job was accepted.'
    Assert-Fails { & $SshScript -OutputsFile (New-Outputs -RuntimeJobs @{ bootstrap = 'job-asre-bootstrap-CUS'; worker = 'job-asre-worker-cus' }) } 'fixed runtime job allowlist' 'A differently cased bootstrap job was accepted.'
    Assert-Test ($global:AzCalls.Count -eq 0) 'A refused outputs file reached Azure.'

    $outputs = New-Outputs
    $verification = Join-Path (Split-Path $outputs -Parent) 'runtime-ssh-verification.json'
    $null = & $SshScript -OutputsFile $outputs -WhatIf
    Assert-Test ($global:AzCalls.Count -eq 1 -and $global:AzCalls[0] -match '^account show --subscription ') 'WhatIf did more than verify the Azure CLI account.'
    Assert-Test (-not (Test-Path -LiteralPath $verification)) 'WhatIf wrote a verification result.'

    $global:AzCalls.Clear()
    foreach ($role in $Roles) { $global:FixturePower[$AsreContext.targets[$role].resource_id] = 'PowerState/deallocated' }
    $null = & $SshScript -OutputsFile $outputs -ResumeTargets -WhatIf
    $mutating = @($global:AzCalls | Where-Object { $_ -notmatch '^(account show|vm get-instance-view) ' })
    Assert-Test ($mutating.Count -eq 0 -and @($global:AzCalls | Where-Object { $_ -match '^vm get-instance-view ' }).Count -eq 3) 'WhatIf with -ResumeTargets started a VM or ran a job.'
    Assert-Test (-not (Test-Path -LiteralPath $verification)) 'WhatIf with -ResumeTargets wrote a verification result.'

    $global:AzCalls.Clear()
    $global:FixtureAccountSubscription = $AltContext.subscription_id
    Assert-Fails { & $SshScript -OutputsFile $outputs -ResumeTargets } 'not the enabled deployment subscription and tenant' 'A different Azure CLI subscription was accepted.'
    Assert-Test ($global:AzCalls.Count -eq 1) 'A mismatched Azure CLI account reached a VM or job operation.'
    $global:FixtureAccountSubscription = $AsreContext.subscription_id

    Write-Output "Runtime bootstrap projection fixtures passed ($($global:AssertionCount) assertions); no Azure operation was executed."
}
finally {
    Clear-AsreTestContext
    Remove-Item -LiteralPath 'Function:\az' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
