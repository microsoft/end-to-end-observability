$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$BuildScript = Join-Path $Repo 'scripts\Build-RuntimeImages.ps1'

$FixtureSub = '00000001-000a-4000-a000-000000000001'
$FixtureTenant = '00000002-000a-4000-9000-000000000002'
$FixtureRegistry = 'acrasre8a327010f9'
$FixtureLogin = 'acrasre8a327010f9.azurecr.io'
$AltSub = '00000901-000a-4000-8000-000000000901'
$AltTenant = '00000902-000a-4000-8000-000000000902'
$AltRegistry = 'acrasree26b179818'
$AltLogin = 'acrasree26b179818.azurecr.io'
$FixtureCommit = '0123456789abcdef0123456789abcdef01234567'
$OtherCommit = 'fedcba9876543210fedcba9876543210fedcba98'
$Work = Join-Path ([IO.Path]::GetTempPath()) "asre-image-fixture-$([guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $Work
$global:AssertionCount = 0
$global:AzCalls = [System.Collections.Generic.List[string]]::new()
$global:GitCalls = [System.Collections.Generic.List[string]]::new()
$global:FixtureRegistries = [System.Collections.Generic.HashSet[string]]::new()
$global:FixtureSleeps = [System.Collections.Generic.List[double]]::new()
# The poll bound of Build-RuntimeImages.ps1: 270 polls, 10 seconds apart (45 minutes), then it gives up.
$PollBound = 270
$OriginalEnv = @{}
foreach ($Name in 'ASRE_RUNTIME_IMAGE_KIND', 'BUILD_SOURCEVERSION', 'TEMPLATE_SOURCE_COMMIT', 'ASRE_CONTEXT_FILE') {
    $OriginalEnv[$Name] = [Environment]::GetEnvironmentVariable($Name)
}

function global:az {
    $global:LASTEXITCODE = 0
    $global:AzCalls.Add(($args -join ' '))
    $subscriptionIndex = [Array]::IndexOf($args, '--subscription')
    if ($subscriptionIndex -lt 0 -or $args[$subscriptionIndex + 1] -cne $global:FixtureExpectedSubscription) {
        throw 'The fixture requires the explicit deployment subscription on every az call.'
    }
    if ($args[0] -ceq 'account' -and $args[1] -ceq 'show') {
        if ($global:FixtureAccountFails) { $global:LASTEXITCODE = 1; return }
        return (@{ id = $global:FixtureAccountSubscription; tenantId = $global:FixtureAccountTenant; state = 'Enabled' } | ConvertTo-Json -Compress)
    }
    if ($args[0] -cne 'acr') { throw 'The fixture permits account and ACR commands only.' }
    $global:AcrCalls++
    if ($args[1] -ceq 'build') {
        [void]$global:FixtureRegistries.Add($args[[Array]::IndexOf($args, '--registry') + 1])
        $global:FixtureBuildArguments = @($args)
        $global:FixtureBuildCalls++
        if ($args -contains '--no-wait') {
            # Queue only: the Run JSON carries the run id, nothing is streamed.
            if ($global:FixtureQueueFails) { $global:LASTEXITCODE = 1; return }
            $global:FixtureImageBuilds++
            $global:FixtureImageExists = $true
            if ($null -eq $global:FixtureRunId) { return '{"status":"Queued"}' }
            return (@{ runId = $global:FixtureRunId; status = 'Queued' } | ConvertTo-Json -Compress)
        }
        # Without --no-wait the real CLI streams the log and its exit code is the stream's: a log display that crashes
        # (UnicodeEncodeError on redirected Windows stdout) fails here although the run itself succeeded.
        $global:FixtureStreamingBuilds++
        $global:FixtureImageBuilds++
        $global:FixtureImageExists = $true
        if ($global:FixtureLogsFail) {
            Write-Error "UnicodeEncodeError: 'charmap' codec can't encode characters in position 4-7" -ErrorAction Continue
            $global:LASTEXITCODE = 1
        }
        return
    }
    if ($args[1] -ceq 'task') {
        $registryIndex = [Array]::IndexOf($args, '--registry')
        $runIndex = [Array]::IndexOf($args, '--run-id')
        if ($registryIndex -lt 0 -or $runIndex -lt 0) { throw 'The fixture requires --registry and --run-id on every acr task call.' }
        [void]$global:FixtureRegistries.Add($args[$registryIndex + 1])
        if ($args[$runIndex + 1] -cne $global:FixtureRunId) { throw 'The fixture run id is not the run that was queued.' }
        switch ($args[2]) {
            'show-run' {
                $global:FixtureShowRunCalls++
                $sequence = @($global:FixtureRunStatuses)
                $status = $sequence[[Math]::Min($global:FixtureShowRunCalls, $sequence.Count) - 1]
                return (@{ runId = $global:FixtureRunId; status = $status } | ConvertTo-Json -Compress)
            }
            'logs' {
                $global:FixtureLogsCalls++
                if ($global:FixtureLogsFail) {
                    Write-Error "UnicodeEncodeError: 'charmap' codec can't encode characters in position 4-7" -ErrorAction Continue
                    $global:LASTEXITCODE = 1
                    return
                }
                return "fixture build log of run $($global:FixtureRunId)"
            }
            default { throw 'Unexpected fixture task operation.' }
        }
    }
    if ($args[1] -cne 'repository') { throw 'Unexpected fixture Azure operation.' }
    [void]$global:FixtureRegistries.Add($args[[Array]::IndexOf($args, '--name') + 1])
    switch ($args[2]) {
        'list' { if ($global:FixtureImageExists) { return (ConvertTo-Json -InputObject @($global:FixtureRepository) -Compress) } else { return '[]' } }
        'show-tags' { return (@{ name = $global:FixtureHeadCommit } | ConvertTo-Json) }
        'update' {
            if (($args -join ' ') -notmatch '--write-enabled false --delete-enabled false') {
                throw 'The image fixture was not locked.'
            }
            $reference = $args[[Array]::IndexOf($args, '--image') + 1]
            if ($reference -ceq "$($global:FixtureRepository):$global:FixtureHeadCommit") {
                $global:FixtureTagLocked = $true
            }
            elseif ($reference -ceq "$($global:FixtureRepository)@$global:FixtureImageDigest") {
                if (-not $global:FixtureIgnoreManifestLock) { $global:FixtureManifestLocked = $true }
            }
            else { throw 'Unexpected fixture lock reference.' }
            return '{}'
        }
        'show' {
            $reference = $args[[Array]::IndexOf($args, '--image') + 1]
            if ($reference -ceq "$($global:FixtureRepository):$global:FixtureHeadCommit") {
                $locked = $global:FixtureTagLocked
            }
            elseif ($reference -ceq "$($global:FixtureRepository)@$global:FixtureImageDigest") {
                $locked = $global:FixtureManifestLocked
            }
            else { throw 'Unexpected fixture image reference.' }
            return (@{
                digest = $global:FixtureImageDigest
                changeableAttributes = @{ writeEnabled = -not $locked; deleteEnabled = -not $locked }
            } | ConvertTo-Json)
        }
        default { throw 'Unexpected fixture repository operation.' }
    }
}

function global:git {
    $global:LASTEXITCODE = 0
    $global:GitCalls.Add(($args -join ' '))
    if ($args.Count -ne 4 -or $args[0] -cne '-C' -or $args[2] -cne 'rev-parse' -or $args[3] -cne 'HEAD') {
        throw 'The fixture permits git rev-parse HEAD only.'
    }
    if ($global:FixtureGitFails) { $global:LASTEXITCODE = 128; return }
    return $global:FixtureHeadCommit
}

function global:Start-Sleep {
    # No wall-clock waiting: the polling of the ACR run only records the requested pauses.
    param([double]$Seconds = 0)
    $global:FixtureSleeps.Add($Seconds)
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

function Reset-Fixture {
    param([ValidateSet('synthetic', 'alt')][string]$Name = 'synthetic')
    $global:FixtureImageExists = $false
    $global:FixtureTagLocked = $false
    $global:FixtureManifestLocked = $false
    $global:FixtureIgnoreManifestLock = $false
    $global:FixtureImageBuilds = 0
    $global:AcrCalls = 0
    $global:FixtureAccountFails = $false
    $global:FixtureGitFails = $false
    $global:FixtureBuildArguments = @()
    $global:FixtureBuildCalls = 0
    $global:FixtureStreamingBuilds = 0
    $global:FixtureQueueFails = $false
    $global:FixtureLogsFail = $false
    $global:FixtureShowRunCalls = 0
    $global:FixtureLogsCalls = 0
    $global:FixtureRunId = 'ch1'
    $global:FixtureRunStatuses = @('Queued', 'Started', 'Running', 'Succeeded')
    $global:FixtureSleeps.Clear()
    $global:FixtureImageDigest = 'sha256:' + ('a' * 64)
    $global:FixtureHeadCommit = $FixtureCommit
    $global:FixtureRepository = 'asre-operations'
    $global:AzCalls.Clear()
    $global:GitCalls.Clear()
    $global:FixtureRegistries.Clear()
    $identity = if ($Name -ceq 'alt') { @($AltSub, $AltTenant) } else { @($FixtureSub, $FixtureTenant) }
    $global:FixtureExpectedSubscription = $identity[0]
    $global:FixtureAccountSubscription = $identity[0]
    $global:FixtureAccountTenant = $identity[1]
    $null = Use-AsreTestContext -Name $Name
}

function Get-BuildInputs {
    param([string]$Kind)
    $buildContext = Join-Path $Repo $Kind
    $extensions = if ($Kind -ceq 'dashboard') { @('.py', '.html') } else { @('.py', '.json') }
    @(Get-ChildItem -LiteralPath $buildContext -Recurse -File -Force | Where-Object {
        $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
        ($_.Extension -in $extensions -or $_.Name -in @('requirements.txt', 'Dockerfile', '.dockerignore'))
    })
}

function Save-Manifest {
    param($Plan)
    $Plan.Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $Plan.Directory 'manifest.json') -Encoding utf8
}

function New-Plan {
    # A validated-plan directory holding a byte-identical snapshot of both image sources and the manifest proving them.
    param([string]$Subscription = $FixtureSub, [string]$Tenant = $FixtureTenant)
    $directory = Join-Path $Work "plan-$([guid]::NewGuid().ToString('N'))"
    $null = New-Item -ItemType Directory -Path $directory
    $files = [ordered]@{}
    foreach ($kind in 'operations', 'dashboard') {
        $buildContext = Join-Path $Repo $kind
        foreach ($file in (Get-BuildInputs -Kind $kind)) {
            $relative = [IO.Path]::GetRelativePath($buildContext, $file.FullName)
            $destination = Join-Path (Join-Path $directory $kind) $relative
            $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force
            Copy-Item -LiteralPath $file.FullName -Destination $destination
            $files["$kind/" + $relative.Replace('\', '/')] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        }
    }
    $plan = @{
        Directory = $directory
        Manifest = [ordered]@{
            source_commit = $FixtureCommit
            subscription_id = $Subscription
            tenant_id = $Tenant
            source_dirty = $false
            files = $files
        }
    }
    Save-Manifest -Plan $plan
    return $plan
}

function Invoke-Build {
    param([Parameter(Mandatory)]$Plan, [Parameter(Mandatory)][string]$Output, [string]$Subscription = $global:FixtureExpectedSubscription,
        [string]$Tenant = $global:FixtureAccountTenant)
    $global:LastOutput = @(& $BuildScript -SubscriptionId $Subscription -TenantId $Tenant -ValidatedPlanDirectory $Plan.Directory -OutputDirectory $Output)
}

function Set-TestEnv {
    # A null value removes the variable; assigning an empty string would leave it set (and different from unset).
    param([string]$Name, $Value)
    if ($null -eq $Value) { Remove-Item -LiteralPath "Env:\$Name" -ErrorAction SilentlyContinue } else { Set-Item -LiteralPath "Env:\$Name" -Value $Value }
}

function Get-Provenance { param([string]$Output) Get-Content -LiteralPath (Join-Path $Output 'image-provenance.json') -Raw | ConvertFrom-Json }

try {
    foreach ($Name in 'BUILD_SOURCEVERSION', 'TEMPLATE_SOURCE_COMMIT') { Set-TestEnv -Name $Name -Value $null }
    Set-TestEnv -Name 'ASRE_RUNTIME_IMAGE_KIND' -Value $null
    # The old pipeline variables must have no influence: set them to values that would break a build that read them.
    $env:BUILD_SOURCEVERSION = $OtherCommit
    $env:TEMPLATE_SOURCE_COMMIT = $OtherCommit

    # ---- operations image: build, lock and provenance --------------------------------------------------------------
    Reset-Fixture
    $plan = New-Plan
    $buildOutput = Join-Path $Work 'build'
    Invoke-Build -Plan $plan -Output $buildOutput
    Assert-Test ($global:FixtureImageBuilds -eq 1 -and $global:FixtureTagLocked -and $global:FixtureManifestLocked) 'The actual build and both independent locks were not selected.'
    Assert-Test ($global:AzCalls[0] -match '^account show --subscription 00000001-000a-4000-a000-000000000001 ') 'The subscription was not verified before any registry operation.'
    Assert-Test ($global:GitCalls.Count -eq 1 -and $global:GitCalls[0] -ceq "-C $Repo rev-parse HEAD") 'The source commit was not read from the working tree HEAD.'
    Assert-Test ($global:FixtureRegistries.Count -eq 1 -and $global:FixtureRegistries.Contains($FixtureRegistry)) 'The registry was not the one named by the deployment context.'
    $arguments = $global:FixtureBuildArguments
    Assert-Test ($arguments -contains "SOURCE_COMMIT=$FixtureCommit" -and $arguments -contains "asre-operations:$FixtureCommit" -and
        $arguments -contains 'linux/amd64' -and $arguments[-1] -ceq (Join-Path $Repo 'operations')) 'The build did not use the exact commit tag, the amd64 platform and the operations build context.'
    $proof = Get-Provenance -Output $buildOutput
    Assert-Test ($proof.digest -ceq $global:FixtureImageDigest -and $proof.source_commit -ceq $FixtureCommit) 'Provenance did not bind the actual digest to source.'
    Assert-Test ($proof.write_enabled -eq $false -and $proof.delete_enabled -eq $false -and
        $proof.tag_write_enabled -eq $false -and $proof.tag_delete_enabled -eq $false) 'Provenance did not record both verified lock surfaces.'
    Assert-Test ($proof.registry -ceq $FixtureLogin -and $proof.repository -ceq 'asre-operations' -and
        $proof.image -ceq "$FixtureLogin/asre-operations@$($global:FixtureImageDigest)") 'Provenance did not name the context registry and the digest-pinned image.'
    Assert-Test (-not $proof.PSObject.Properties['template_commit'] -and -not $proof.PSObject.Properties['subscription_id']) 'Provenance carries a field the new model dropped.'
    Assert-Test ((Get-Content -LiteralPath (Join-Path $buildOutput 'image-provenance.json') -Raw) -notmatch '(?i)password|secret|token|sas') 'Provenance carries credential-shaped text.'

    # ---- reuse and mutable-image refusals --------------------------------------------------------------------------
    Invoke-Build -Plan $plan -Output (Join-Path $Work 'reuse')
    Assert-Test ($global:FixtureImageBuilds -eq 1) 'An existing locked image was rebuilt.'
    Assert-Test (($global:LastOutput -join ' ') -match 'Reusing the existing locked exact-source image') 'The reuse was not reported.'
    Assert-Test ((Get-Provenance -Output (Join-Path $Work 'reuse')).digest -ceq $global:FixtureImageDigest) 'The reused image provenance lost its digest.'

    $global:FixtureTagLocked = $false
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'unlocked') } 'unlocked source-revision image' 'An existing mutable image was accepted.'
    Assert-Test (-not (Test-Path -LiteralPath (Join-Path $Work 'unlocked'))) 'A mutable image published provenance.'

    $global:FixtureTagLocked = $true
    $global:FixtureManifestLocked = $false
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'mutable-digest') } 'image digest remains mutable' 'A locked tag with a mutable digest was accepted.'
    Assert-Test (-not (Test-Path -LiteralPath (Join-Path $Work 'mutable-digest'))) 'A mutable digest published provenance.'

    Reset-Fixture
    $global:FixtureIgnoreManifestLock = $true
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'failed-manifest-lock') } 'image digest remains mutable' 'An unverified manifest lock was accepted.'
    Assert-Test (-not (Test-Path -LiteralPath (Join-Path $Work 'failed-manifest-lock'))) 'An unverified manifest lock published success-shaped provenance.'

    # ---- the ACR run status decides: queued without streaming, polled until final, the log shown afterwards ---------
    Reset-Fixture
    $plan = New-Plan
    $queuedOutput = Join-Path $Work 'run-sequence'
    Invoke-Build -Plan $plan -Output $queuedOutput
    $arguments = $global:FixtureBuildArguments
    Assert-Test ($global:FixtureBuildCalls -eq 1 -and $global:FixtureStreamingBuilds -eq 0 -and $arguments -contains '--no-wait') 'The build was not queued with --no-wait.'
    Assert-Test ($arguments[[Array]::IndexOf($arguments, '--timeout') + 1] -ceq '1800' -and
        $arguments[[Array]::IndexOf($arguments, '--output') + 1] -ceq 'json' -and $arguments[-1] -ceq (Join-Path $Repo 'operations')) 'The queued build lost its timeout, JSON output or build context.'
    $calls = @($global:AzCalls)
    $polls = @(0..($calls.Count - 1) | Where-Object { $calls[$_] -match '^acr task show-run ' })
    $logDisplays = @(0..($calls.Count - 1) | Where-Object { $calls[$_] -match '^acr task logs ' })
    $locks = @(0..($calls.Count - 1) | Where-Object { $calls[$_] -match '^acr repository update ' })
    Assert-Test ($polls.Count -eq 4 -and $global:FixtureShowRunCalls -eq 4) 'The run was not polled until its final status (Queued, Started, Running, Succeeded).'
    Assert-Test ($calls[$polls[0]] -ceq "acr task show-run --registry $FixtureRegistry --run-id ch1 --subscription $FixtureSub --only-show-errors --output json") 'The run poll did not name the registry, the queued run id and the subscription.'
    Assert-Test ($global:FixtureSleeps.Count -eq 3 -and @($global:FixtureSleeps | Where-Object { $_ -ne 10 }).Count -eq 0) 'The polls were not 10 seconds apart.'
    Assert-Test ($logDisplays.Count -eq 1 -and $logDisplays[0] -gt $polls[-1] -and $locks.Count -ge 2 -and $logDisplays[0] -lt $locks[0]) 'The build log was not displayed once, after the final status.'
    Assert-Test ($calls[$logDisplays[0]] -ceq "acr task logs --registry $FixtureRegistry --run-id ch1 --subscription $FixtureSub --only-show-errors") 'The log display did not name the queued run.'
    Assert-Test (($global:LastOutput -join ' ') -match 'ended with status Succeeded' -and ($global:LastOutput -join ' ') -match [regex]::Escape("az acr task logs --registry $FixtureRegistry --run-id ch1")) 'The status and the log re-read command were not reported.'
    Assert-Test ($global:FixtureTagLocked -and $global:FixtureManifestLocked -and (Get-Provenance -Output $queuedOutput).digest -ceq $global:FixtureImageDigest) 'A succeeded run was not locked and given provenance.'

    # Any final status other than Succeeded fails the build: nothing is locked and no provenance is written.
    foreach ($final in 'Failed', 'Canceled', 'Timeout', 'Error') {
        Reset-Fixture
        $global:FixtureRunStatuses = @('Queued', 'Running', $final)
        $finalOutput = Join-Path $Work "run-$final"
        Assert-Fails { Invoke-Build -Plan $plan -Output $finalOutput } "ACR source build failed; no image provenance will be published\. ACR run ch1 ended with status $final" "A $final run was accepted."
        Assert-Test (-not $global:FixtureTagLocked -and -not $global:FixtureManifestLocked -and
            @($global:AzCalls | Where-Object { $_ -match '^acr repository update ' }).Count -eq 0) "A $final run was locked."
        Assert-Test (-not (Test-Path -LiteralPath $finalOutput)) "A $final run published provenance."
        Assert-Test ($global:FixtureShowRunCalls -eq 3 -and $global:FixtureLogsCalls -eq 1) "A $final run was not read once and then shown."
    }

    # A log display that crashes (UnicodeEncodeError on redirected Windows stdout) cannot change the result of a succeeded run.
    Reset-Fixture
    $global:FixtureLogsFail = $true
    $crashOutput = Join-Path $Work 'logs-crash'
    $streams = @(& { Invoke-Build -Plan $plan -Output $crashOutput } 2>&1)
    Assert-Test ($global:FixtureLogsCalls -eq 1 -and $global:FixtureStreamingBuilds -eq 0) 'The crashing log display was not exercised, or the build streamed.'
    Assert-Test ($global:FixtureTagLocked -and $global:FixtureManifestLocked -and (Get-Provenance -Output $crashOutput).digest -ceq $global:FixtureImageDigest) 'A crashed log display stopped a succeeded run before the locks or provenance.'
    Assert-Test (@($streams | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }).Count -eq 0 -and
        ($global:LastOutput -join ' ') -notmatch 'UnicodeEncodeError') 'The stderr of the crashed log display was not discarded.'

    # A run that never reaches a final status ends at the bound, with its id, and locks nothing.
    Reset-Fixture
    $global:FixtureRunStatuses = @('Queued', 'Running')
    $boundOutput = Join-Path $Work 'run-bound'
    Assert-Fails { Invoke-Build -Plan $plan -Output $boundOutput } 'ACR run ch1 did not reach a final status within 45 minutes.*may still be running and may leave an unlocked' 'A run without a final status was accepted.'
    Assert-Test ($global:FixtureShowRunCalls -eq $PollBound -and $global:FixtureSleeps.Count -eq ($PollBound - 1)) 'The polling did not stop at the poll bound.'
    Assert-Test (-not $global:FixtureTagLocked -and -not $global:FixtureManifestLocked -and $global:FixtureLogsCalls -eq 0 -and
        @($global:AzCalls | Where-Object { $_ -match '^acr repository update ' }).Count -eq 0) 'An unfinished run was locked or its log was shown as if it had ended.'
    Assert-Test (-not (Test-Path -LiteralPath $boundOutput)) 'An unfinished run published provenance.'

    # A build that cannot be queued is not polled.
    Reset-Fixture
    $global:FixtureQueueFails = $true
    $queueFailOutput = Join-Path $Work 'queue-fails'
    Assert-Fails { Invoke-Build -Plan $plan -Output $queueFailOutput } 'ACR source build could not be queued; no image provenance will be published' 'A build that could not be queued was accepted.'
    Assert-Test ($global:FixtureBuildCalls -eq 1 -and @($global:AzCalls | Where-Object { $_ -match '^acr task ' }).Count -eq 0 -and
        -not $global:FixtureTagLocked -and -not $global:FixtureManifestLocked) 'A build that could not be queued was polled or locked.'
    Assert-Test (-not (Test-Path -LiteralPath $queueFailOutput)) 'A build that could not be queued published provenance.'

    # A missing or malformed run id never becomes a command argument.
    foreach ($malformed in $null, '', 'ch1 && echo', '../ch1', ('a' * 33), "ch1`n") {
        Reset-Fixture
        $global:FixtureRunId = $malformed
        $malformedOutput = Join-Path $Work "bad-run-id-$([guid]::NewGuid().ToString('N'))"
        Assert-Fails { Invoke-Build -Plan $plan -Output $malformedOutput } 'queued without a usable run id' "A malformed run id was accepted: '$malformed'."
        Assert-Test (@($global:AzCalls | Where-Object { $_ -match '^acr task ' }).Count -eq 0 -and
            -not $global:FixtureTagLocked -and -not (Test-Path -LiteralPath $malformedOutput)) "A malformed run id reached an acr task call: '$malformed'."
    }

    # ---- source proof: nested inputs, tampering and manifest identity ----------------------------------------------
    $nested = 'operations/contracts/servicenow-action-audit.schema.json'
    $freshPlan = New-Plan
    Assert-Test ($freshPlan.Manifest.files.Contains($nested)) 'The fixture did not preserve the nested contract input.'
    foreach ($required in 'operations/context.py', 'dashboard/context.py', 'dashboard/evidence.html', 'dashboard/index.html') {
        Assert-Test ($freshPlan.Manifest.files.Contains($required)) "The fixture did not pin $required."
    }

    Reset-Fixture
    $missing = New-Plan
    $missing.Manifest.files.Remove($nested)
    Save-Manifest -Plan $missing
    Assert-Fails { Invoke-Build -Plan $missing -Output (Join-Path $Work 'missing-contract') } 'Build input is missing from' 'A missing nested contract was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'A missing nested contract reached ACR.'

    $missing = New-Plan
    $missing.Manifest.files.Remove('operations/context.py')
    Save-Manifest -Plan $missing
    Assert-Fails { Invoke-Build -Plan $missing -Output (Join-Path $Work 'missing-context-py') } 'Build input is missing from' 'An operations input missing from the proof was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'An unproven operations input reached ACR.'

    $tampered = New-Plan
    '{}' | Set-Content -LiteralPath (Join-Path $tampered.Directory 'operations\contracts\servicenow-action-audit.schema.json') -Encoding utf8
    Assert-Fails { Invoke-Build -Plan $tampered -Output (Join-Path $Work 'tampered-contract') } 'validated plan source was modified' 'A tampered nested contract snapshot was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'A tampered nested contract reached ACR.'

    $tampered = New-Plan
    $tampered.Manifest.files['operations/cloud.py'] = '0' * 64
    Save-Manifest -Plan $tampered
    Assert-Fails { Invoke-Build -Plan $tampered -Output (Join-Path $Work 'tampered') } 'validated plan source was modified' 'A tampered source hash was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'Tampered source reached ACR.'

    $tampered = New-Plan
    $tampered.Manifest.files['../escape.py'] = '0' * 64
    Save-Manifest -Plan $tampered
    Assert-Fails { Invoke-Build -Plan $tampered -Output (Join-Path $Work 'escape') } 'validated plan source was modified' 'A manifest entry escaping the plan directory was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'A path-escaping manifest entry reached ACR.'

    $edited = New-Plan
    $edited.Manifest.source_dirty = $true
    Save-Manifest -Plan $edited
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'dirty') } 'uncommitted source tree' 'An image was built from a dirty-tree plan.'
    $edited.Manifest.source_dirty = 'false'
    Save-Manifest -Plan $edited
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'dirty-string') } 'uncommitted source tree' 'A non-boolean source_dirty was accepted.'
    $edited.Manifest.Remove('source_dirty')
    Save-Manifest -Plan $edited
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'dirty-missing') } 'source proof is incomplete' 'A manifest without source_dirty was accepted.'
    foreach ($field in 'source_commit', 'subscription_id', 'tenant_id', 'files') {
        $edited = New-Plan
        $edited.Manifest.Remove($field)
        Save-Manifest -Plan $edited
        Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work "no-$field") } 'source proof is incomplete' "A manifest without $field was accepted."
    }
    $edited = New-Plan -Subscription $AltSub
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'other-subscription') } 'does not match this build, subscription or tenant' 'A manifest of another subscription was accepted.'
    $edited = New-Plan -Tenant $AltTenant
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'other-tenant') } 'does not match this build, subscription or tenant' 'A manifest of another tenant was accepted.'
    $edited = New-Plan
    $edited.Manifest.source_commit = $OtherCommit
    Save-Manifest -Plan $edited
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'other-commit') } 'does not match this build, subscription or tenant' 'A manifest of another commit was accepted.'
    $edited = New-Plan
    Remove-Item -LiteralPath (Join-Path $edited.Directory 'manifest.json')
    Assert-Fails { Invoke-Build -Plan $edited -Output (Join-Path $Work 'no-manifest') } 'has no manifest.json' 'A plan directory without a manifest was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'A refused source proof reached ACR.'

    # ---- identity: context, Azure CLI account, commit and parameters -----------------------------------------------
    Reset-Fixture
    $plan = New-Plan
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'other-ctx-sub') -Subscription $AltSub -Tenant $AltTenant } 'does not belong to the requested subscription and tenant' 'A context of another subscription was accepted.'
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'other-ctx-tenant') -Subscription $FixtureSub -Tenant $AltTenant } 'does not belong to the requested subscription and tenant' 'A context of another tenant was accepted.'
    Assert-Test ($global:AzCalls.Count -eq 0) 'A context mismatch reached the Azure CLI.'

    $global:FixtureAccountFails = $true
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'no-login') } 'Azure CLI cannot read the deployment subscription' 'A missing Azure CLI login was accepted.'
    $global:FixtureAccountFails = $false
    $global:FixtureAccountSubscription = $AltSub
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'wrong-account') } 'not the enabled deployment subscription and tenant' 'A different Azure CLI subscription was accepted.'
    $global:FixtureAccountSubscription = $FixtureSub
    $global:FixtureAccountTenant = $AltTenant
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'wrong-tenant') -Tenant $FixtureTenant } 'not the enabled deployment subscription and tenant' 'A different Azure CLI tenant was accepted.'
    $global:FixtureAccountTenant = $FixtureTenant
    Assert-Test ($global:AcrCalls -eq 0) 'An unverified Azure CLI account reached ACR.'

    Assert-Fails { & $BuildScript -SubscriptionId $FixtureSub.ToUpperInvariant() -TenantId $FixtureTenant -ValidatedPlanDirectory $plan.Directory -OutputDirectory (Join-Path $Work 'upper') } 'SubscriptionId must be a lowercase GUID' 'An uppercase subscription id was accepted.'
    Assert-Fails { & $BuildScript -SubscriptionId $FixtureSub -TenantId 'not-a-guid' -ValidatedPlanDirectory $plan.Directory -OutputDirectory (Join-Path $Work 'bad-tenant') } 'TenantId must be a lowercase GUID' 'A malformed tenant id was accepted.'
    Assert-Fails { & $BuildScript -SubscriptionId "$FixtureSub`n" -TenantId $FixtureTenant -ValidatedPlanDirectory $plan.Directory -OutputDirectory (Join-Path $Work 'newline') } 'SubscriptionId must be a lowercase GUID' 'A subscription id with a trailing newline was accepted.'

    $global:FixtureGitFails = $true
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'no-git') } 'exact Git source commit' 'A failed git revision lookup was accepted.'
    $global:FixtureGitFails = $false
    $global:FixtureHeadCommit = 'not-a-commit'
    Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'bad-commit') } 'exact Git source commit' 'A malformed commit was accepted.'
    Assert-Test ($global:AcrCalls -eq 0) 'A refused commit reached ACR.'

    # An existing output directory is never reused or overwritten.
    Reset-Fixture
    $plan = New-Plan
    $occupied = Join-Path $Work 'occupied'
    Invoke-Build -Plan $plan -Output $occupied
    $before = (Get-FileHash -LiteralPath (Join-Path $occupied 'image-provenance.json') -Algorithm SHA256).Hash
    Assert-Fails { Invoke-Build -Plan $plan -Output $occupied } 'already exists' 'An existing output directory was reused.'
    Assert-Test ($before -ceq (Get-FileHash -LiteralPath (Join-Path $occupied 'image-provenance.json') -Algorithm SHA256).Hash) 'An existing provenance file was overwritten.'

    # ---- dashboard image ------------------------------------------------------------------------------------------
    $env:ASRE_RUNTIME_IMAGE_KIND = 'dashboard'
    Reset-Fixture
    $global:FixtureRepository = 'asre-dashboard'
    $plan = New-Plan
    Invoke-Build -Plan $plan -Output (Join-Path $Work 'dashboard-build')
    $dashboardProof = Get-Provenance -Output (Join-Path $Work 'dashboard-build')
    Assert-Test ($global:FixtureImageBuilds -eq 1 -and $dashboardProof.repository -ceq 'asre-dashboard' -and
        $dashboardProof.image -ceq "$FixtureLogin/asre-dashboard@$($global:FixtureImageDigest)") 'Dashboard publication selected the wrong repository or an unpinned image.'
    Assert-Test ($global:FixtureBuildArguments -contains "asre-dashboard:$FixtureCommit" -and $global:FixtureBuildArguments[-1] -ceq (Join-Path $Repo 'dashboard')) 'The dashboard build did not use the dashboard context.'
    foreach ($file in (Get-BuildInputs -Kind 'dashboard')) {
        $relative = 'dashboard/' + [IO.Path]::GetRelativePath((Join-Path $Repo 'dashboard'), $file.FullName).Replace('\', '/')
        Assert-Test ($plan.Manifest.files.Contains($relative)) "A dashboard build input is not proven: $relative"
    }
    foreach ($dropped in 'dashboard/evidence.html', 'dashboard/context.py', 'dashboard/index.html', 'dashboard/requirements.txt') {
        $partial = New-Plan
        $partial.Manifest.files.Remove($dropped)
        Save-Manifest -Plan $partial
        $before = $global:AcrCalls
        Assert-Fails { Invoke-Build -Plan $partial -Output (Join-Path $Work "missing-$([IO.Path]::GetFileName($dropped))") } 'Build input is missing from' "An unproven dashboard input ($dropped) was accepted."
        Assert-Test ($global:AcrCalls -eq $before) "Unvalidated dashboard input $dropped reached ACR."
    }

    foreach ($kind in '../operations', 'Operations', 'Dashboard', 'operations ') {
        $env:ASRE_RUNTIME_IMAGE_KIND = $kind
        $before = $global:AzCalls.Count
        Assert-Fails { Invoke-Build -Plan $plan -Output (Join-Path $Work 'invalid-image-kind') } 'must be operations or dashboard' "An arbitrary image kind '$kind' was accepted."
        Assert-Test ($global:AzCalls.Count -eq $before) 'An arbitrary image context reached Azure.'
    }
    Set-TestEnv -Name 'ASRE_RUNTIME_IMAGE_KIND' -Value $null

    # ---- a second deployment context: nothing of the first one may leak into the build -----------------------------
    Reset-Fixture -Name alt
    $plan = New-Plan -Subscription $AltSub -Tenant $AltTenant
    Invoke-Build -Plan $plan -Output (Join-Path $Work 'alt-build')
    $altProof = Get-Provenance -Output (Join-Path $Work 'alt-build')
    Assert-Test ($altProof.registry -ceq $AltLogin -and $altProof.image -ceq "$AltLogin/asre-operations@$($global:FixtureImageDigest)") 'The second context did not select its own registry.'
    Assert-Test ($global:FixtureRegistries.Count -eq 1 -and $global:FixtureRegistries.Contains($AltRegistry)) 'The second context used another registry.'
    Assert-Test (-not (($global:AzCalls -join "`n") -match [regex]::Escape($FixtureRegistry)) -and -not (($global:AzCalls -join "`n") -match [regex]::Escape($FixtureSub))) 'The first context leaked into the second build.'
    Assert-Fails { Invoke-Build -Plan (New-Plan) -Output (Join-Path $Work 'alt-with-first-plan') -Subscription $FixtureSub -Tenant $FixtureTenant } 'does not belong to the requested subscription and tenant' 'The second context built for the first subscription.'

    Write-Output "Image delivery fixtures passed ($($global:AssertionCount) assertions); no actual image or Azure resource was created."
}
finally {
    Clear-AsreTestContext
    foreach ($Name in $OriginalEnv.Keys) { Set-TestEnv -Name $Name -Value $OriginalEnv[$Name] }
    Remove-Item -LiteralPath 'Function:\az', 'Function:\git', 'Function:\Start-Sleep' -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
