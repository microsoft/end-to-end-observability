[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
# The builder reads the deployment context with Get-AsreContext; every in-process build below runs under the committed
# synthetic fixture (the render is then the identity) unless a check selects another context.
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
$builder = Join-Path $root 'scripts/Build-CaseTool.ps1'
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('asre-case-package-' + [guid]::NewGuid())
[void][IO.Directory]::CreateDirectory($temporary)
$count = 0
function Assert-Package([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:count++
}
function Get-CanonicalContextJson([string]$Path) {
    # Independent of the builder: sorted-key minified JSON of the stored context document, computed by Python.
    $text = & $python -I -B -c 'import json, sys; sys.stdout.write(json.dumps(json.load(open(sys.argv[1], encoding="utf-8")), sort_keys=True, separators=(",", ":"), ensure_ascii=False))' $Path
    if ($LASTEXITCODE -ne 0) { throw 'The canonical context document could not be computed.' }
    return [string]$text
}
function New-RealisticDocument {
    # A valid, non-synthetic context (the real loader accepts it): the alternative fixture with values that match no
    # sentinel shape and a complete ServiceNow section.
    $document = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixtures\context.alt.json') -Raw | ConvertFrom-Json -AsHashtable
    $subscription = '5b7c1e84-2f3a-4d96-8c05-a1e3f7b92d60'
    $suffix = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes("${subscription}:asre-demo"))).ToLowerInvariant().Substring(0, 10)
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
function Write-ContextFile([string]$Path, $Document) {
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Document -Depth 10), [Text.UTF8Encoding]::new($false))
}
function Get-BuildFailure([scriptblock]$Build) {
    # The exception message of a refused build, or $null when the build succeeded.
    try { & $Build | Out-Null } catch { return [string]$_.Exception.Message }
    return $null
}
$syntheticFixture = Join-Path $root 'tests/fixtures/context.synthetic.json'
$altFixture = Join-Path $root 'tests/fixtures/context.alt.json'
try {
    [void](Use-AsreTestContext -Name synthetic)
    $source = & $builder -PassThru
    $again = & $builder -PassThru
    Assert-Package ($source -is [string] -and $source -ceq $again) 'Bundle generation must be deterministic and return only code.'
    Assert-Package ((& $builder -PassThru -HealthOnly:$false) -ceq $source) 'Explicit false must retain byte-identical configured output.'
    Assert-Package (@((Get-Command $builder).Parameters.Keys | Where-Object { $_ -notin @(
        'OutputPath','PassThru','HealthOnly','Verbose','Debug','ErrorAction','WarningAction',
        'InformationAction','ProgressAction','ErrorVariable','WarningVariable','InformationVariable',
        'OutVariable','OutBuffer','PipelineVariable'
    ) }).Count -eq 0) 'Only the optional HealthOnly builder parameter may be added.'
    $generated = Join-Path $temporary 'case_tool.py'
    & $builder -OutputPath $generated
    Assert-Package ([IO.File]::ReadAllText($generated) -ceq $source) 'PassThru and file artifacts must agree.'
    $denied = $false
    try { & $builder -OutputPath $generated } catch { $denied = $true }
    Assert-Package $denied 'An existing generated artifact must never be overwritten.'
    $denied = $false
    try { & $builder -OutputPath (Join-Path $root 'operations/do-not-create.py') } catch { $denied = $true }
    Assert-Package $denied 'Generated artifacts must not overwrite repository source.'
    $declaration = Get-Content -LiteralPath (Join-Path $root 'agent/tools/servicenow_case.json') -Raw | ConvertFrom-Json
    $expected = @('action', 'investigation_id', 'alert_id', 'evidence_json', 'findings_note', 'recurrence_id', 'profile')
    Assert-Package ((@($declaration.properties.parameters.name) -join ',') -ceq ($expected -join ',')) 'Declaration parameters changed.'
    Assert-Package ($declaration.name -ceq 'asre-servicenow-case' -and $declaration.callable -ceq 'main') 'Fixed native tool binding changed.'
    $hashes = [ordered]@{}
    foreach ($path in @('operations/agent_case.py', 'operations/servicenow.py', 'operations/patch_provider.py', 'operations/context.py')) {
        $hashes[('_asre_case/' + [IO.Path]::GetFileName($path))] = (Get-FileHash -LiteralPath (Join-Path $root $path) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    # The bundled context is the stored context document, sorted-key and minified (no trailing newline).
    $contextBytes = [Text.UTF8Encoding]::new($false).GetBytes((Get-CanonicalContextJson $syntheticFixture))
    $hashes['_asre_case/context.json'] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($contextBytes)).ToLowerInvariant()
    foreach ($path in Get-ChildItem -LiteralPath (Join-Path $root 'operations/contracts') -Filter 'servicenow-*.schema.json' -File) {
        $hashes[('_asre_case/contracts/' + $path.Name)] = (Get-FileHash -LiteralPath $path.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $workflowRaw = Get-Content -LiteralPath (Join-Path $root 'agent/workflows/azure-monitor-review.json') -Raw
    $workflow = $workflowRaw | ConvertFrom-Json -DateKind String
    $policyBytes = [Text.UTF8Encoding]::new($false).GetBytes(($workflow.commissioning | ConvertTo-Json -Compress))
    $hashes['_asre_case/commissioning.json'] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($policyBytes)).ToLowerInvariant()
    $runtimeRaw = Get-Content -LiteralPath (Join-Path $root 'config/native-incident-runtime.json') -Raw
    $runtimeConfig = $runtimeRaw | ConvertFrom-Json -DateKind String
    $runtimeBytes = [Text.UTF8Encoding]::new($false).GetBytes(($runtimeConfig | ConvertTo-Json -Depth 8 -Compress))
    $hashes['_asre_case/runtime-configuration.json'] = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($runtimeBytes)).ToLowerInvariant()
    # Execute only copied generated source from a fresh directory under -I.
    # No repository source directory is on sys.path; networking is forbidden.
    $probe = @'
import base64, hashlib, importlib.util, inspect, io, json, pathlib, sys, zipfile
def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Network is forbidden in offline package verification.")
sys.addaudithook(deny_network)
path, root, manifest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), json.loads(sys.argv[3])
assert pathlib.Path.cwd() == path.parent
# This interpreter's installed third-party dependencies live under .venv; they
# are allowed. Repository source/module directories must not be importable.
assert all(not pathlib.Path(p).is_relative_to(root) or pathlib.Path(p).is_relative_to(root / ".venv") for p in sys.path)
spec = importlib.util.spec_from_file_location("fresh_case_tool", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert list(inspect.signature(module.main).parameters) == ["action","investigation_id","alert_id","evidence_json","findings_note","recurrence_id","profile"]
with zipfile.ZipFile(io.BytesIO(base64.b64decode(module.__CASE_TOOL_BUNDLE__))) as package:
    assert set(package.namelist()) == set(manifest) | {"_asre_case/__init__.py"}
    for name, expected in manifest.items():
        assert hashlib.sha256(package.read(name)).hexdigest() == expected, name
runtime = module._runtime()
assert "operations" not in sys.modules
assert runtime.CaseClient.__module__ == "_asre_case.servicenow"
runtime.Contracts()
assert runtime.commissioning_policy()["version"] == "1.0"
assert runtime.commissioning_policy() == json.loads(sys.argv[4])["commissioning"]
assert runtime._configuration_frozen and runtime._frozen_configuration == json.loads(sys.argv[5])
from azure.identity import DefaultAzureCredential
import jsonschema, rfc3339_validator
assert module.main("create","unit-thread","unit-alert")["status"] == "blocked"
assert not hasattr(module, "install_runtime")
# Default health must really use the fixed secret/OAuth/client path despite
# absent native-positive DTO/config. Only HTTP and MI wire responses are fake.
from unittest.mock import patch
facade = sys.modules[runtime.CaseClient.__module__]
calls = []
class Credential:
    def get_token(self, scope):
        assert scope == "https://vault.azure.net/.default"
        return type("Token", (), {"token":"unit-mi-token"})()
    def close(self): pass
class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())
        self.status = 200
class Opener:
    def open(self, request, timeout):
        calls.append(request.full_url)
        if request.full_url == runtime.SECRET_URL:
            return Response({"value":"unit-only-facade-secret"})
        if request.full_url == facade.ORIGIN + "/oauth_token.do":
            return Response({"access_token":"unit-facade-token","token_type":"Bearer","expires_in":1800,"scope":facade.SCOPE})
        assert request.full_url == facade.ORIGIN + facade.BASE + "/health"
        return Response({"result":{"version":"0.1.0","instance":"example-instance","scope":"x_0000000_asre",
            "execution_mode":"Review","azure_mutations_exposed":False,"human_approval_enabled":False,
            "trusted_verifier_bound":False,"feedback":"fixture","agent_continuation":"fixture"}})
with patch.object(runtime,"managed_identity",return_value=Credential()), patch.object(runtime,"build_opener",return_value=Opener()), patch.object(facade,"build_opener",return_value=Opener()):
    health = module.main("health")
assert health["status"] == "healthy" and health["scope"] == "facade_auth_only" and health["case_writes_ready"] is False
assert calls == [runtime.SECRET_URL, facade.ORIGIN + "/oauth_token.do", facade.ORIGIN + facade.BASE + "/health"]
print("Fresh isolated runtime/package/dependency verification PASS")
'@
    Push-Location $temporary
    try {
        & $python -I -B -c $probe $generated $root ($hashes | ConvertTo-Json -Compress) $workflowRaw $runtimeRaw
        if ($LASTEXITCODE -ne 0) { throw 'Fresh isolated package verification failed.' }
    } finally { Pop-Location }
    $count++
    $currentObservationProbe = @'
import copy, hashlib, importlib.util, json, pathlib, sys
from datetime import timedelta
from unittest.mock import patch
network_attempts = []
def deny_network(event, args):
    if event.startswith("socket."):
        network_attempts.append(event)
        raise AssertionError("Current observation package verification is offline only.")
sys.addaudithook(deny_network)
root, source = pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[1])
assert all(not pathlib.Path(p).is_relative_to(root) or pathlib.Path(p).is_relative_to(root / ".venv") for p in sys.path)
spec = importlib.util.spec_from_file_location("current_observation_package", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = module._runtime()
assert runtime.CaseTool.__module__ == "_asre_case.agent_case" and "operations" not in sys.modules
provider = runtime.patch
config = copy.deepcopy(runtime._frozen_configuration)
lower = runtime.stamp(config["not_before"])
now = lower + timedelta(minutes=5)
def stamp(seconds): return (lower + timedelta(seconds=seconds)).isoformat()
investigation = "44444444-4444-4444-8444-444444444444"
alert = runtime.WORKSPACE + "/providers/Microsoft.AlertsManagement/alerts/55555555-5555-4555-8555-555555555555"
installation = "11111111-1111-4111-8111-111111111111"
summary = {name: 0 for name in provider.COUNTS}
summary.update(installationActivityId=installation, status="Failed", failedPatchCount=1,
               startTime=stamp(60), lastModifiedTime=stamp(90), maintenanceWindowExceeded=False,
               error={"code": "Unit", "message": "Benign original archive."})
archived = provider.snapshot(provider.encode({"patchStatus": {"lastPatchInstallationSummary": summary}}),
                             lower + timedelta(seconds=120))
sample = provider.sample(archived, provider.digest_for(provider.encode(archived)))
# Positive UNIT Reader/Client protocol fixtures, not a native DTO injection or
# a change to the generated entrypoint's protected decoder/authentication.
class Reader:
    def __init__(self):
        self.summary, self.samples = copy.deepcopy(summary), [copy.deepcopy(sample)]
        self.investigation_id = investigation
    def investigation(self, identity):
        return {"id": self.investigation_id, "platform": "AzMonitor", "created_at": stamp(181),
                "alert_ids": [alert], "resource_id": runtime.PATCH_RESOURCE}
    def alert(self, identity):
        return {"id": alert, "rule_id": runtime.PATCH_PROFILE.rule_id, "fired_at": stamp(180),
                "state": "Fired", "severity": "Sev1", "window_start": stamp(-480),
                "window_end": stamp(120), "installation_id": installation}
    def patch_current(self, samples):
        return provider.observation(provider.encode({"patchStatus": {"lastPatchInstallationSummary": self.summary}}), now)
    def signal(self, identity):
        return {"alert_id": alert, "rule_id": runtime.PATCH_PROFILE.rule_id, "resource_id": runtime.PATCH_RESOURCE,
                "query": runtime.PATCH_PROFILE.query, "samples": copy.deepcopy(self.samples),
                "current_provider_observation": self.patch_current(self.samples)}
class Store:
    def __init__(self): self.data, self.creations = {}, 0
    def read(self, name): return self.data[name]
    def create(self, name, raw):
        assert name not in self.data
        self.data[name] = raw
        self.creations += 1
class Client:
    reference = {"record_type": "incident", "number": "INC0099999", "sys_id": "a"*32}
    def __init__(self): self.posts, self.writes, self.unknown = [], {}, set()
    def reconcile(self, episode, write):
        return copy.deepcopy(self.writes.get(write, {
            "outcome": "unknown" if write in self.unknown else "not_applied",
            "episode_id": episode, "write_request_id": write,
            **({"message": "Offline unknown write."} if write in self.unknown
               else {"error_code": "write_not_found"})}))
    def read_case(self, episode, resource, reference):
        facade = sys.modules[runtime.CaseClient.__module__]
        return facade.OwnedCase(episode, resource.lower(), copy.deepcopy(reference), "1")
    def submit(self, name, episode, payload, before_submit):
        assert before_submit() > 0
        self.posts.append(payload["write_request_id"])
        receipt = {"outcome": "applied", "episode_id": episode,
                   "write_request_id": payload["write_request_id"], "case_reference": copy.deepcopy(self.reference)}
        if name == "note": receipt["journal_sys_id"] = "b"*32
        self.writes[payload["write_request_id"]] = receipt
        return copy.deepcopy(receipt)
    def create_case(self, payload, *, before_submit):
        return self.submit("create", payload["evidence"]["episode_id"], payload, before_submit)
    def append_note(self, case, payload, *, before_submit):
        return self.submit("note", case.episode_id, payload, before_submit)
evidence = {"contract_version": "1.0", "source_event_ids": [alert], "resource_id": runtime.PATCH_RESOURCE,
            "mode": "reactive", "signal_family": "patch-installation", "detected_at": stamp(180),
            "summary": "Offline unit patch evidence.",
            "evidence": [{"source": "AzureMonitor", "observed_at": stamp(180), "reference": alert, "finding": "Unit alert."},
                         {"source": "UpdateManager", "observed_at": stamp(90),
                          "reference": provider.COMPUTE_URL, "finding": "Unit archived failure."}],
            "diagnosis": {"finding": "Unit evidence.", "confidence": 0.8, "gaps": ["Offline only"]},
            "actions": [], "verification": {"status": "not_started", "recovery_owner": "none"}}
fake = "offline-current-credential-fixture"
messages = ("https://fixture.invalid/packages?access_token=" + fake, "Authorization: Bearer " + fake)
blocked, failed = 0, []
for action in ("read", "create", "note"):
    for index, message in enumerate(messages):
        reader, store, client = Reader(), Store(), Client()
        def tool():
            return runtime.CaseTool(reader, copy.deepcopy(config), store, client, lambda: now, profile="patch-installation")
        if action != "read":
            owner = tool()
            binding = owner.authority(investigation, alert, writing=True)
            plan = owner.freeze(provider.episode(installation), binding, json.dumps(evidence), "Offline unit findings.", "")
            if action == "note":
                client.unknown.add(plan["payloads"]["note"]["write_request_id"])
                assert tool().run("create", investigation, alert)["status"] == "pending"
                client.unknown.clear()
        original, posts = dict(store.data), list(client.posts)
        reader.summary["error"] = {"code": "ChangedCurrent", "message": message}
        provider.validate_summary(reader.summary)  # Shape alone intentionally accepts the strings.
        try:
            tool().run(action, investigation, alert)
        except runtime.CaseToolError as error:
            assert str(error) == "Runtime content exceeds its bound or contains suspected credentials."
            assert fake not in str(error)
            blocked += 1
        else:
            failed.append((action, index))
            continue
        assert store.data == original and client.posts == posts and fake.encode() not in b"".join(store.data.values())
        if action != "read":
            result = tool().run("reconcile", investigation, alert)
            assert "current_provider_observation" not in result and fake not in json.dumps(result)
            assert result[action]["write_request_id"] == plan["payloads"][action]["write_request_id"]
        reader.summary["error"] = {"code": "ChangedCurrent", "message": "Benign current update."}
        result = tool().run(action, investigation, alert)
        if action == "read":
            assert result["status"] == "native_evidence" and not store.data and not client.posts
        else:
            assert result["status"] == "persisted" and store.data == original and store.creations == 1
            assert result[action]["write_request_id"] == plan["payloads"][action]["write_request_id"]
assert blocked == 6 and not failed, ("Sensitive current observation was not blocked", failed)
assert runtime.PATCH_DISPLAY_LIMIT == 3072
assert (runtime.PATCH_ERROR_CODE_LIMIT, runtime.PATCH_ERROR_MESSAGE_LIMIT,
        runtime.PATCH_DETAIL_MESSAGE_LIMIT, runtime.PATCH_DETAIL_LIMIT) == (48, 192, 160, 3)
display_probes = 0
def display_error(error):
    global reader, store, client, display_probes
    reader, store, client = Reader(), Store(), Client()
    value = copy.deepcopy(archived)
    if error is None: del value["summary"]["error"]
    else: value["summary"]["error"] = copy.deepcopy(error)
    reader.samples = [provider.sample(value, provider.digest_for(provider.encode(value)))]
    saved = copy.deepcopy(reader.samples)
    result = tool().run("read", investigation, alert)
    shown = result["signal"]["samples"][0]
    fields = ("resource_id", "installation_id", "observed_at", "status", "failed_patch_count", "digest")
    assert set(shown) == set(fields) | {"provider_error"}
    assert {key: shown[key] for key in fields} == {key: saved[0][key] for key in fields}
    assert result["status"] == "native_evidence" and result["binding"] == tool().authority(investigation, alert, writing=False)
    assert result["signal"]["reference"] == provider.COMPUTE_URL and result["signal"]["sample_count"] == 1
    assert result["current_provider_observation"] == {
        "installation_id": installation, "status": "Failed", "failed_patch_count": 1,
        "provider_time": summary["lastModifiedTime"], "collection_time": now.isoformat(),
        "relationship": "current summary identifies the archived installation",
    }
    assert json.loads(runtime.wire(result)) == result and len(runtime.wire(result)) <= 3072
    assert reader.samples == saved and not store.data and not client.posts
    display_probes += 1
    return result
recorded_error = {
    "code": "1",
    "message": ("2 error/s reported. The latest 2 error/s are shared in detail. To view all errors, "
                "review this log file on the machine: /var/log/azure/Microsoft.CPlat.Core.LinuxPatchExtension/13.core.log"),
    "details": [
        {"code": "OPERATION_FAILED",
         "message": " Operation status was marked as failed because: [X] a failure occurred during the operation "},
        {"code": "PACKAGE_MANAGER_FAILURE",
         "message": "Customer environment error: Investigate and resolve unexpected return code (100) from package manager on command: sudo DEBIAN..."},
    ],
}
recorded_display = display_error(recorded_error)
projected = recorded_display["signal"]["samples"][0]["provider_error"]
assert projected == {"code": "1", "message": recorded_error["message"], "detail_count": 2,
                     "details": [{"code": item["code"], "message": item["message"].strip()}
                                 for item in recorded_error["details"]], "truncated": False}
assert projected["details"][1]["code"] == "PACKAGE_MANAGER_FAILURE" and "(100)" in projected["details"][1]["message"]
assert len(runtime.wire(recorded_display)) <= 2600
absent = display_error(None)
expected = copy.deepcopy(recorded_display)
expected["signal"]["samples"][0].update(provider_error=None, digest=reader.samples[0]["digest"])
assert absent == expected and absent["signal"]["samples"][0]["provider_error"] is None
byte_sizes = {"recorded": len(runtime.wire(recorded_display)), "no_error": len(runtime.wire(absent))}
base = copy.deepcopy(recorded_display)
del base["signal"]["samples"][0]["provider_error"]
byte_sizes["without_provider_error"] = len(runtime.wire(base))
missing_details = display_error({"code": "Unit", "message": "Root"})
assert missing_details["signal"]["samples"][0]["provider_error"] == {
    "code": "Unit", "message": "Root", "detail_count": 0, "details": [], "truncated": False,
}
for field, cap in (("code", 48), ("message", 192), ("detail_code", 48), ("detail_message", 160)):
    for ch, cost in (("a", 1), ("é", 6), ("🚀", 12), ('"', 2), ("\\", 2)):
        for extra in (0, 1):
            error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
            target, key = (error["details"][0], field[7:]) if field.startswith("detail_") else (error, field)
            target[key] = ch * (cap // cost + extra)
            projected = display_error(error)["signal"]["samples"][0]["provider_error"]
            actual = projected["details"][0][key] if field.startswith("detail_") else projected[key]
            assert actual == ch * (cap // cost) and len(json.dumps(actual, ensure_ascii=True)) - 2 <= cap
            assert projected["truncated"] == bool(extra)
    for text, clean in (
        (' \tquoted "message"\nC:\\logs\x00fail ', 'quoted "message" C:\\logs fail'),
        (" a\u2003b\r\n c ", "a b c"),
    ):
        error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
        target, key = (error["details"][0], field[7:]) if field.startswith("detail_") else (error, field)
        target[key] = text
        projected = display_error(error)["signal"]["samples"][0]["provider_error"]
        actual = projected["details"][0][key] if field.startswith("detail_") else projected[key]
        assert actual == clean and projected["truncated"] is False
    error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
    target, key = (error["details"][0], field[7:]) if field.startswith("detail_") else (error, field)
    target[key] = "valid prefix " + "\ud800"
    try: display_error(error)
    except runtime.CaseToolError: pass
    else: raise AssertionError("Lone surrogate was admitted.")
    assert not store.data and not client.posts
    display_probes += 1
for text, clean in (("a"*190 + "🚀", "a"*190), ("a"*191 + " " + "🚀", "a"*191 + " ")):
    projected = display_error({"code": "Unit", "message": text})["signal"]["samples"][0]["provider_error"]
    assert projected["message"] == clean and projected["truncated"] is True
for count in (0, 3, 5):
    details = [{"code": str(index), "message": f"Detail {index}"} for index in range(count)]
    error = {"code": "Unit", "message": "Root", "details": details}
    projected = display_error(error)["signal"]["samples"][0]["provider_error"]
    assert projected == {**error, "details": details[:3], "detail_count": count, "truncated": count > 3}
inner = {"exceptiontype": "UnitException", "errordetail": "Offline detail"}
for location, key, value in (
    ("root", "target", ""), ("root", "innererror", inner),
    ("detail", "target", ""), ("detail", "innererror", inner), ("detail", "details", []),
):
    error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
    target = error if location == "root" else error["details"][0]
    target[key] = value
    saved = copy.deepcopy(error)
    projected = display_error(error)["signal"]["samples"][0]["provider_error"]
    assert projected == {"code": "Unit", "message": "Root", "detail_count": 1,
                         "details": [{"code": "Detail", "message": "Body"}], "truncated": True}
    assert error == saved
mixed = '🚀é"\\'*100
adversarial = {"code": mixed, "message": mixed,
               "details": [{"code": mixed, "message": mixed} for _ in range(4)]}
at_cap = {"code": "🚀"*4, "message": "é"*32,
          "details": [{"code": "é"*8, "message": '"\\'*40} for _ in range(3)]}
for name, error in (("adversarial", adversarial), ("at_cap", at_cap)):
    result = display_error(error)
    assert len(provider.encode(reader.samples[0]["snapshot"]["summary"], provider.SUMMARY_LIMIT)) <= provider.SUMMARY_LIMIT
    projected = result["signal"]["samples"][0]["provider_error"]
    assert projected["detail_count"] == len(error["details"]) and len(projected["details"]) == 3
    assert projected["truncated"] == (name == "adversarial")
    fields = [(projected["code"], 48), (projected["message"], 192)]
    fields += [(detail[key], cap) for detail in projected["details"] for key, cap in (("code", 48), ("message", 160))]
    for text, cap in fields:
        size = len(json.dumps(text, ensure_ascii=True)) - 2
        assert size <= cap
        if name == "at_cap": assert size == cap
    byte_sizes[name] = len(runtime.wire(result))
    assert byte_sizes[name] <= 2900
reader, store, client = Reader(), Store(), Client()
reader.summary.update(installationActivityId="22222222-2222-4222-8222-222222222222",
                      status="Succeeded", failedPatchCount=0, startTime=stamp(-1200), lastModifiedTime=stamp(-600))
result = tool().run("read", investigation, alert)
assert result["current_provider_observation"] == {
    "installation_id": reader.summary["installationActivityId"], "status": "Succeeded", "failed_patch_count": 0,
    "provider_time": stamp(-600), "collection_time": now.isoformat(),
    "relationship": "current summary identifies another installation",
}
assert result["signal"]["samples"][0]["installation_id"] == installation
assert result["signal"]["samples"][0]["observed_at"] == summary["lastModifiedTime"]
assert not store.data and not client.posts
display_probes += 1
reader, store, client = Reader(), Store(), Client()
oversized = tool().run("read", investigation, alert)
reader.investigation_id = "é"*300
oversized["binding"]["investigation_id"] = reader.investigation_id
assert len(json.dumps(oversized, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) < 3072
assert len(runtime.wire(oversized)) > 3072
with patch.object(runtime, "_patch_native_display", wraps=runtime._patch_native_display) as display:
    try: tool().run("read", reader.investigation_id, alert)
    except runtime.CaseToolError: pass
    else: raise AssertionError("Escaped canonical display overflow was accepted.")
    display.assert_called_once()
assert not store.data and not client.posts
display_probes += 1
for kind in ("hidden-sensitive", "hidden-count", "hidden-time", "hidden-installation", "current-sensitive",
             "hidden-detail-fourth", "hidden-detail-tail"):
    reader, store, client = Reader(), Store(), Client()
    hidden = copy.deepcopy(sample)
    if kind == "hidden-sensitive":
        value = copy.deepcopy(archived)
        value["summary"]["error"]["message"] = "benign prefix "*20 + messages[0]
        hidden = provider.sample(value, provider.digest_for(provider.encode(value)))
    elif kind == "hidden-count": hidden["failed_patch_count"] = True
    elif kind == "hidden-time": hidden["observed_at"] = stamp(240)
    elif kind == "hidden-installation": hidden["installation_id"] = "22222222-2222-4222-8222-222222222222"
    elif kind.startswith("hidden-detail"):
        value = copy.deepcopy(archived)
        details = [{"code": "Shown", "message": "Benign"} for _ in range(3)] if kind == "hidden-detail-fourth" else []
        details.append({"code": "Hidden", "message": "x"*200 + messages[0]})
        value["summary"]["error"]["details"] = details
        hidden = provider.sample(value, provider.digest_for(provider.encode(value)))
    else: reader.summary["error"]["details"] = [{"code": "Hidden", "message": "benign prefix "*20 + messages[1]}]
    reader.samples = [hidden] if kind.startswith("hidden-detail") else [copy.deepcopy(sample), hidden]
    with patch.object(runtime, "_patch_native_display", wraps=runtime._patch_native_display) as display:
        try: tool().run("read", investigation, alert)
        except runtime.CaseToolError: pass
        else: raise AssertionError("Non-displayed invalid/sensitive evidence was hidden by projection.")
        display.assert_not_called()
    assert not store.data and not client.posts
    display_probes += 1
reader, store, client = Reader(), Store(), Client()
first, second = copy.deepcopy(archived), copy.deepcopy(archived)
first["summary"]["error"] = copy.deepcopy(recorded_error)
first["summary"]["error"]["message"] = "complete archived error "*100
second["summary"].update(lastModifiedTime=stamp(105), status="CompletedWithWarnings", failedPatchCount=2)
reader.samples = [provider.sample(value, provider.digest_for(provider.encode(value))) for value in (first, second)]
reader.summary = copy.deepcopy(first["summary"])
saved, current = copy.deepcopy(reader.samples), copy.deepcopy(reader.summary)
saved_bytes = provider.encode(saved)
owner = tool()
binding = owner.authority(investigation, alert, writing=False)
saved_binding = copy.deepcopy(binding)
expected_evidence = {**copy.deepcopy(evidence), "episode_id": provider.episode(installation)}
payloads = owner.payloads(provider.episode(installation), binding, expected_evidence, "Offline unit findings.")
fingerprints = {key: hashlib.sha256(runtime.wire(value)).hexdigest() for key, value in payloads.items()}
result = owner.native_context(binding)
assert result["binding"] == saved_binding and result["signal"]["sample_count"] == 2
assert len(result["signal"]["samples"]) == 1 and result["signal"]["samples"][0]["provider_error"]["truncated"]
assert not store.data and not client.posts
result["binding"]["investigation_id"] = "changed-display"
result["signal"]["samples"][0]["status"] = "changed-display"
result["signal"]["samples"][0]["provider_error"]["code"] = "changed-display"
result["signal"]["samples"][0]["provider_error"]["details"][0]["message"] = "changed-display"
result["current_provider_observation"]["status"] = "changed-display"
assert binding == saved_binding and reader.samples == saved and reader.summary == current
assert provider.encode(reader.samples) == saved_bytes
assert owner._patch_observation["summary"] == current
plan = owner.freeze(provider.episode(installation), binding, json.dumps(evidence), "Offline unit findings.", "")
assert plan["proof"]["signal"]["samples"] == saved and store.creations == 1 and not client.posts
assert plan["payloads"] == payloads and plan["fingerprints"] == fingerprints
assert provider.encode(plan["proof"]["signal"]["samples"]) == saved_bytes
for row in plan["proof"]["signal"]["samples"]:
    assert "provider_error" not in row and provider.archived(provider.encode(row["snapshot"]), row["digest"]) == row["snapshot"]
    assert provider.encode(row) == provider.encode(provider.sample(row["snapshot"], row["digest"]))
    assert row["error_details"] == provider.encode(row["snapshot"]["summary"]["error"]).decode("ascii")[:1600]
assert len(plan["proof"]["signal"]["samples"][0]["error_details"]) == 1600
assert plan["proof"]["signal"]["samples"][0]["snapshot"]["summary"]["error"]["message"] == first["summary"]["error"]["message"]
display_probes += 1
print("PATCH_DISPLAY_PROBE " + json.dumps({
    "display_probes": display_probes, "canonical_byte_limit": 3072,
    "error_code_limit": 48, "error_message_limit": 192, "detail_message_limit": 160, "detail_limit": 3,
    "canonical_display_bytes": byte_sizes,
    "read_side_writes": 0, "complete_frozen_samples": len(plan["proof"]["signal"]["samples"]),
    "network_attempts": len(network_attempts),
}, sort_keys=True))
assert not network_attempts
print("CURRENT_OBSERVATION_CONTENT_PROBE " + json.dumps({
    "sensitive_probes": blocked, "benign_read_probes": 2, "benign_resume_probes": 4,
    "network_attempts": len(network_attempts), "frozen_proof_and_write_ids_preserved": True,
}, sort_keys=True))
'@
    Push-Location $temporary
    try {
        & $python -I -B -c $currentObservationProbe $generated $root
        if ($LASTEXITCODE -ne 0) { throw 'Generated current observation content-guard verification failed.' }
    } finally { Pop-Location }
    $count++
    $retryProbe = @'
import contextlib, importlib.util, io, json, pathlib, sys
from unittest.mock import patch
network = []
def deny(event, args):
    if event.startswith("socket."):
        network.append(event)
        raise AssertionError("Offline package retry probe.")
sys.addaudithook(deny)
spec = importlib.util.spec_from_file_location("retry_package", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = module._runtime()
assert "operations" not in sys.modules
class Clock:
    def __init__(self): self.tick, self.sleeps = 0.0, []
    def __call__(self): return self.tick
    def sleep(self, value): self.sleeps.append(value); self.tick += value
class Credential:
    def __init__(self): self.closed, self.calls = 0, 0
    def get_token(self, scope):
        self.calls += 1
        return type("Token", (), {"token": "offline-unit-token"})()
    def close(self): self.closed += 1
class Opener:
    def __init__(self): self.calls = []
    def open(self, request, timeout):
        self.calls.append(timeout)
        raise TimeoutError("offline-private-canary")
checks = 0
for profile, action, enhanced in (("patch-installation", "read", True),
                                  ("patch-installation", "create", True),
                                  ("patch-installation", "note", False), ("disk", "read", False)):
    clock, credential, opener = Clock(), Credential(), Opener()
    context = runtime._PatchReadContext(action, clock, clock.sleep)
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(runtime, "managed_identity", return_value=credential), \
            patch.object(runtime, "_patch_context", return_value=context) as factory, \
            patch.object(runtime, "build_opener", return_value=opener), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = module.main(action, "44444444-4444-4444-8444-444444444444",
                             runtime.WORKSPACE + "/providers/Microsoft.AlertsManagement/alerts/11111111-1111-4111-8111-111111111111",
                             profile=profile)
    assert result["status"] == "blocked" and credential.closed == 1
    assert factory.call_count == int(enhanced)
    assert "offline-private-canary" not in json.dumps(result) + stdout.getvalue() + stderr.getvalue()
    if enhanced:
        assert (result["stage"], result["reason"], result["attempts"], result["retryable"]) == (
            "sre-thread", "timeout", 2, action == "read")
        assert opener.calls == [30, 30] and clock.sleeps == [2]
    else:
        assert set(result) == {"status", "message"} and opener.calls == [20] and not clock.sleeps
    checks += 1
clock = Clock()
context = runtime._PatchReadContext("read", clock, clock.sleep)
attempts = []
def attempt(timeout):
    attempts.append(timeout)
    if len(attempts) == 1: raise TimeoutError()
    return {"ok": True}
assert context.request("log-query", attempt) == {"ok": True}
assert attempts == [45, 45] and clock.sleeps == [2] and context.deadline == 150
clock.tick = 150
try: context.request("compute", attempt)
except runtime.CaseDiagnostic as failure:
    assert (failure.stage, failure.reason, failure.attempts, failure.retryable) == ("compute", "budget", 0, False)
else: raise AssertionError("Renewed deadline.")
checks += 2
context = runtime._PatchReadContext("create", Clock(), lambda seconds: None)
class Client:
    def reconcile(self):
        assert not context.active
        raise TimeoutError()
client = runtime._PrewriteClient(Client(), context)
for _ in range(2):
    try: client.reconcile()
    except TimeoutError: pass
    else: raise AssertionError("Facade fixture did not fail.")
    assert not context.active
checks += 1
diagnostic = runtime.CaseDiagnostic("secret", "timeout", True, 2)
def secret(): raise diagnostic
facade = sys.modules[runtime.CaseClient.__module__]
try: facade.CaseClient(facade.ClientCredentials(secret).token).health()
except runtime.CaseDiagnostic as failure: assert failure is diagnostic
else: raise AssertionError("Secret diagnostic was swallowed.")
assert issubclass(runtime.CaseDiagnostic, runtime.CaseToolError)
assert not issubclass(runtime.CaseDiagnostic, runtime.patch.ProviderError)
assert runtime._read_failure(runtime.patch.ProviderError("offline-private-canary"))[1:3] == ("invalid", False)
timeout = type("ServiceRequestTimeoutError", (Exception,), {})("auth canary")
assert runtime._token_reason(timeout) == ("timeout", None, True)
assert runtime._token_reason(RuntimeError("ServiceRequestTimeoutError")) == ("auth", None, True)
checks += 3
import errno
error = PermissionError(errno.EACCES, "offline-private-canary")
assert runtime.patch.classify_failure(error) is None
assert runtime._token_reason(error) == ("auth", None, True)
assert runtime._read_failure(error) == (None, "invalid", False, None, True)
checks += 3
for header, expected_calls, retryable in (("3", 2, False), ("31", 1, False), (None, 2, True)):
    clock = Clock()
    context = runtime._PatchReadContext("read", clock, clock.sleep)
    class HttpResponseError(Exception):
        status_code = 429
        response = type("Response", (), {"headers": {} if header is None else {"rEtRy-AfTeR": header}})()
    calls = []
    def throttled(timeout):
        calls.append(timeout)
        handle = runtime._MeteredCredential(Credential(), context)
        with patch.object(handle.legacy, "get_token", side_effect=HttpResponseError("offline-private-canary")):
            handle.get_token("https://management.azure.com/.default")
    try: context.request("compute", throttled)
    except runtime.CaseDiagnostic as failure:
        assert (failure.stage, failure.reason, failure.attempts, failure.retryable) == (
            "token", "throttled", expected_calls, retryable)
        assert set(failure.result()) == {"status", "message", "stage", "reason", "attempts", "retryable"}
    else: raise AssertionError("Expected bounded throttling.")
    assert len(calls) == expected_calls
    assert clock.sleeps == ([3] if header == "3" else [2] if header is None else [])
    checks += 1
import ssl
for outer_tls in (False, True):
    status = HttpResponseError("offline-private-canary")
    status.status_code = 503
    status.response = type("Response", (), {"headers": {"Retry-After": "3"}})()
    tls = ssl.SSLError("offline-private-canary")
    outer, inner = (tls, status) if outer_tls else (status, tls)
    outer.__cause__ = inner
    assert runtime.patch.classify_failure(outer) == ("tls", False, None, True)
    assert runtime._token_reason(outer) == ("tls", None, True)
    checks += 1
guard = PermissionError(errno.EACCES, "offline-private-canary")
guard.__cause__ = status
assert runtime.patch.classify_failure(guard) is None
assert runtime._token_reason(guard) == ("auth", None, True)
assert runtime._read_failure(guard) == (None, "invalid", False, None, True)
checks += 1
assert not network
print("PATCH_READ_RETRY_PROBE " + json.dumps({"checks": checks, "network_attempts": len(network),
      "main_production_path": True, "secret_diagnostic_preserved": True}, sort_keys=True))
'@
    Push-Location $temporary
    try {
        & $python -I -B -c $retryProbe $generated
        if ($LASTEXITCODE -ne 0) { throw 'Generated patch retry/budget/latch verification failed.' }
    } finally { Pop-Location }
    $count++
    $activeJson = $runtimeRaw
    $activeSource = & $builder -PassThru
    Assert-Package ($activeSource -ceq $source) 'Default disabled publication must already include the complete trusted configuration without activation-time repackaging.'
    $activeBundle = Join-Path $temporary 'active_case_tool.py'
    & $builder -OutputPath $activeBundle
    $activeProbe = @'
import importlib.util, json, sys
from unittest.mock import patch
def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}: raise AssertionError("offline only")
sys.addaudithook(deny_network)
spec = importlib.util.spec_from_file_location("fresh_active_tool", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = module._runtime()
config = json.loads(sys.argv[2])
assert runtime._frozen_configuration == config
assert not hasattr(module, "install_runtime")
runtime.freeze_runtime_configuration(config)  # identical package binding is idempotent
class Credential:
    def close(self): pass
investigation = "44444444-4444-4444-8444-444444444444"
raw = {"id":investigation,"source":"Conversation","incidentSource":None,"incidentDetails":None,"startMessageIsHuman":True}
with patch.object(runtime,"managed_identity",return_value=Credential()), patch.object(runtime,"_azure_json",return_value=raw) as fetch:
    result = module.main("create", investigation, "unit-alert")
assert result["status"] == "blocked"  # self-bound fixed reader rejects manual provenance
assert fetch.call_args.args[1:] == ("https://azuresre.dev/.default", runtime.SRE_ORIGIN + "/api/v1/threads/" + investigation)
try:
    runtime.freeze_runtime_configuration({**config, "not_before":"2026-10-19T22:00:01Z"})
except RuntimeError: pass
else: raise AssertionError("moved active boundary accepted")
assert runtime._frozen_configuration == config
print("Frozen active commissioning and fresh self-bound fail-closed native reader PASS")
'@
    Push-Location $temporary
    try {
        & $python -I -B -c $activeProbe $activeBundle $activeJson
        if ($LASTEXITCODE -ne 0) { throw 'Frozen active package verification failed.' }
    } finally { Pop-Location }
    $count++
    Assert-Package (-not (Get-Command $builder).Parameters.ContainsKey('RuntimeConfigurationPath')) 'No arbitrary runtime configuration override may be exposed.'
    $healthSource = & $builder -HealthOnly -PassThru
    Assert-Package ($healthSource -is [string] -and $healthSource -ceq (& $builder -HealthOnly -PassThru)) 'Health-only generation must be deterministic.'
    $healthDirectory = Join-Path $temporary 'isolated-health'
    [void][IO.Directory]::CreateDirectory($healthDirectory)
    $healthBundle = Join-Path $healthDirectory 'health_case_tool.py'
    & $builder -HealthOnly -OutputPath $healthBundle
    Assert-Package ([IO.File]::ReadAllText($healthBundle) -ceq $healthSource) 'Health-only PassThru and file output must agree.'
    $denied = $false
    try { & $builder -HealthOnly -OutputPath $healthBundle } catch { $denied = $true }
    Assert-Package $denied 'Health-only must not overwrite an existing generated artifact.'
    $denied = $false
    try { & $builder -HealthOnly -OutputPath (Join-Path $root 'operations/do-not-create.py') } catch { $denied = $true }
    Assert-Package $denied 'Health-only must retain repository output protections.'
    $healthProbe = @'
import base64, hashlib, importlib.util, io, json, pathlib, sys, zipfile
from contextlib import ExitStack
from unittest.mock import patch
from urllib.parse import parse_qs

network_attempts = []
def deny_network(event, args):
    if event.startswith("socket."):
        network_attempts.append(event)
        raise AssertionError("Network is forbidden in isolated health verification.")
sys.addaudithook(deny_network)
path, configured_path, root = map(pathlib.Path, sys.argv[1:4])
config = json.loads(sys.argv[4])
assert sys.flags.isolated and sys.dont_write_bytecode
assert pathlib.Path.cwd() == path.parent
assert all(not pathlib.Path(p).is_relative_to(root) or pathlib.Path(p).is_relative_to(root / ".venv") for p in sys.path)
def load(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
module = load("isolated_health_tool", path)
configured = load("configured_comparison_only", configured_path)
def archive(tool):
    return zipfile.ZipFile(io.BytesIO(base64.b64decode(tool.__CASE_TOOL_BUNDLE__, validate=True)))
with archive(module) as health, archive(configured) as default:
    assert health.namelist() == default.namelist()
    assert len(set(health.namelist())) == len(health.namelist())
    for name in health.namelist():
        assert health.read(name) == (b"null" if name.endswith("/runtime-configuration.json") else default.read(name)), name
    member_hashes = {name: hashlib.sha256(health.read(name)).hexdigest() for name in health.namelist()}
# Invoke the generated entrypoint before retrieving its cached bundled receiver.
assert module.main("unknown")["status"] == "blocked"
runtime = module._runtime()
assert runtime._configuration_frozen and runtime._frozen_configuration is None
assert pathlib.Path(runtime.__file__).is_relative_to(pathlib.Path(module._case_directory.name))
assert "operations" not in sys.modules and "operations.agent_case" not in sys.modules
assert runtime.CaseClient.__module__ == "_asre_case.servicenow"
facade = sys.modules[runtime.CaseClient.__module__]
investigation = "44444444-4444-4444-8444-444444444444"
alert = runtime.WORKSPACE + "/providers/Microsoft.AlertsManagement/alerts/55555555-5555-4555-8555-555555555555"
recurrence = "66666666-6666-4666-8666-666666666666"
evidence = json.dumps({"investigation_id": investigation, "alert_id": alert, "request_id": recurrence})
blocked = {"status": "blocked", "message": "Fixed case operation unavailable or rejected; preserve original investigation/write IDs and use reconciliation. Protected commissioning, native provenance and access must be verified."}
blocked_calls = 0
early_access = {}
with ExitStack() as stack:
    guards = {name: stack.enter_context(patch.object(runtime, name, side_effect=AssertionError("Forbidden early access: " + name)))
              for name in ("managed_identity", "exact_secret", "_azure_json", "build_opener",
                           "AzureReader", "BlobIntents", "ClientCredentials", "CaseClient", "CaseTool")}
    def rejected(*args):
        global blocked_calls
        assert module.main(*args) == blocked, args
        blocked_calls += 1
    for action in ("read", "create", "note", "reconcile"):
        for arguments in (
            (investigation, alert),
            (investigation, alert, evidence, "Observed disk saturation", ""),
            (investigation, alert, "", "", recurrence),
            (investigation, alert, evidence, "Recurrence evidence supplied", recurrence),
        ):
            rejected(action, *arguments)
    for action in ("unknown", "HEALTH", "", None, 1):
        rejected(action, investigation, alert, evidence, "supplied finding", recurrence)
    for index in range(5):
        for value in ("nonempty", " ", "\t\n", None, 1, {}, []):
            arguments = [""] * 5
            arguments[index] = value
            rejected("health", *arguments)
    for name, guard in guards.items():
        guard.assert_not_called()
        early_access[name] = guard.call_count

# A valid nonnull configuration passes validation but cannot rebind this receiver.
runtime.validate_commissioning(config, runtime.commissioning_policy())
try:
    runtime.freeze_runtime_configuration(config)
except runtime.CaseToolError:
    pass
else:
    raise AssertionError("Frozen health-only configuration was rebound.")
runtime.freeze_runtime_configuration(None)  # identical binding remains idempotent
assert runtime._configuration_frozen and runtime._frozen_configuration is None

http_calls, credentials = [], []
failure_stage = None
class Credential:
    def __init__(self):
        self.scopes, self.closed = [], 0
    def get_token(self, scope):
        self.scopes.append(scope)
        assert scope == "https://vault.azure.net/.default"
        if failure_stage == "token":
            raise RuntimeError("private-token-fixture")
        return type("Token", (), {"token": "unit-mi-token"})()
    def close(self):
        self.closed += 1
def credential_factory():
    credential = Credential()
    credentials.append(credential)
    return credential
class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())
        self.status = 200
class Opener:
    def open(self, request, timeout):
        http_calls.append((request.get_method(), request.full_url, timeout))
        if request.full_url == runtime.SECRET_URL:
            assert request.get_method() == "GET" and timeout == 20
            assert request.get_header("Authorization") == "Bearer unit-mi-token"
            if failure_stage == "secret":
                raise TimeoutError("private-secret-fixture")
            return Response({"value": "unit-only-facade-secret"})
        if request.full_url == facade.ORIGIN + "/oauth_token.do":
            assert request.get_method() == "POST" and timeout == 45
            body = parse_qs(request.data.decode())
            assert body["client_id"] == [runtime.FACADE_CLIENT]
            assert body["client_secret"] == ["unit-only-facade-secret"]
            assert body["grant_type"] == ["client_credentials"]
            if failure_stage == "oauth":
                raise TimeoutError("private-oauth-fixture")
            return Response({"access_token": "unit-facade-token", "token_type": "Bearer", "expires_in": 1800, "scope": facade.SCOPE})
        assert request.full_url == facade.ORIGIN + facade.BASE + "/health"
        assert request.get_method() == "GET" and timeout == 45
        assert request.get_header("Authorization") == "Bearer unit-facade-token"
        if failure_stage == "health":
            raise TimeoutError("private-health-fixture")
        return Response({"result": {
            "version": "0.1.0", "instance": "example-instance", "scope": "x_0000000_asre",
            "execution_mode": "Review", "azure_mutations_exposed": False, "human_approval_enabled": False,
            "trusted_verifier_bound": False, "feedback": "fixture", "agent_continuation": "fixture",
        }})
expected_calls = [("GET", runtime.SECRET_URL, 20),
                  ("POST", facade.ORIGIN + "/oauth_token.do", 45),
                  ("GET", facade.ORIGIN + facade.BASE + "/health", 45)]
health_access = {}
with ExitStack() as stack:
    # Constructors for Reader/CaseClient are harmless existing wiring. Every
    # authoritative/read/write method, intent constructor and CaseTool is guarded
    # with a counter so swallowed exceptions cannot make a forbidden call pass.
    guards = {}
    for cls in (runtime.AzureReader, runtime.CaseClient):
        for name, value in vars(cls).items():
            if not name.startswith("_") and callable(value) and name != "health":
                key = cls.__name__ + "." + name
                guards[key] = stack.enter_context(patch.object(cls, name, side_effect=AssertionError(key)))
    for name in ("_azure_json", "BlobIntents", "CaseTool"):
        guards[name] = stack.enter_context(patch.object(runtime, name, side_effect=AssertionError(name)))
    identity = stack.enter_context(patch.object(runtime, "managed_identity", side_effect=credential_factory))
    secret = stack.enter_context(patch.object(runtime, "exact_secret", wraps=runtime.exact_secret))
    stack.enter_context(patch.object(runtime, "build_opener", return_value=Opener()))
    stack.enter_context(patch.object(facade, "build_opener", return_value=Opener()))
    # Repeated entrypoint calls must stay health-only after the attempted rebind.
    for _ in range(2):
        before = len(http_calls)
        assert module.main("health") == {"status": "healthy", "mode": "Review", "origin": facade.ORIGIN,
                                         "scope": "facade_auth_only", "case_writes_ready": False}
        assert http_calls[before:] == expected_calls
        assert credentials[-1].closed == 1
        assert credentials[-1].scopes == ["https://vault.azure.net/.default"]
        assert module.main("create", investigation, alert, evidence, "", recurrence) == blocked
        assert runtime._configuration_frozen and runtime._frozen_configuration is None
    failure_probes = 0
    for failure_stage, expected_length in (("token", 0), ("secret", 1), ("oauth", 2), ("health", 3)):
        before = len(http_calls)
        result = module.main("health")
        assert result == blocked and "private-" not in json.dumps(result)
        assert http_calls[before:] == expected_calls[:expected_length]
        assert credentials[-1].closed == 1
        assert credentials[-1].scopes == ["https://vault.azure.net/.default"]
        failure_probes += 1
    assert identity.call_count == secret.call_count == len(credentials) == 6
    for name, guard in guards.items():
        guard.assert_not_called()
        health_access[name] = guard.call_count
assert not network_attempts
print("HEALTH_ONLY_PROBE " + json.dumps({
    "blocked_argument_probes": blocked_calls, "valid_health_probes": 2, "redacted_failure_probes": failure_probes,
    "credential_instances_closed_once": len(credentials), "mock_http_calls": len(http_calls),
    "early_access_calls": early_access, "health_forbidden_access_calls": health_access,
    "network_attempts": len(network_attempts), "frozen_configuration": None,
    "member_sha256": member_hashes,
}, sort_keys=True))
'@
    Push-Location $healthDirectory
    try {
        & $python -I -B -c $healthProbe $healthBundle $generated $root $runtimeRaw
        if ($LASTEXITCODE -ne 0) { throw 'Fresh isolated health-only package verification failed.' }
    } finally { Pop-Location }
    $count++
    $dateTimeProbe = @'
import importlib.abc, importlib.util, json, pathlib, sys
network_attempts = []
def deny_network(event, args):
    if event.startswith("socket."):
        network_attempts.append(event)
        raise AssertionError("Network is forbidden in date-time package verification.")
sys.addaudithook(deny_network)
assert "jsonschema" not in sys.modules and "rfc3339_validator" not in sys.modules
class MissingOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "rfc3339_validator" or fullname.startswith("rfc3339_validator."):
            raise ModuleNotFoundError("Optional RFC3339 dependency deliberately unavailable.", name=fullname)
blocker = MissingOptional()
sys.meta_path.insert(0, blocker)
import jsonschema
assert "date-time" not in jsonschema.FormatChecker.checkers
original_checkers = dict(jsonschema.FormatChecker.checkers)
mode = sys.argv[2]
assert mode in {"absent", "late"}
if mode == "late":
    sys.meta_path.remove(blocker)
    import rfc3339_validator
    assert dict(jsonschema.FormatChecker.checkers) == original_checkers
source = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("isolated_datetime_package", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = module._runtime()
facade = sys.modules[runtime.CaseClient.__module__]
embedded = pathlib.Path(facade.__file__).read_text(encoding="utf-8")
for notice in ("rfc3339-validator", "0.1.4", "Permission is hereby granted, free of charge", "THE SOFTWARE IS PROVIDED"):
    assert notice in embedded, notice
contracts = runtime.Contracts()
assert len(contracts.validators) == 6
def forbidden_token():
    raise AssertionError("Contract initialization must not acquire credentials.")
runtime.CaseClient(forbidden_token)
accepted = ("2024-02-29T23:59:59Z", "2026-10-20t03:08:55z", "2026-10-20T03:08:55.123456789+05:30",
            "2000-02-29T00:00:00-04:00", "2026-10-20T03:08:55+00:00")
rejected = ("2023-02-29T00:00:00Z", "1900-02-29T00:00:00Z", "2026-10-20T24:00:00Z",
            "2026-10-20T03:08:60Z", "2026-10-20T03:08:55", "2026-10-20T03:08:55+24:00",
            "2026-10-20T03:08:55Z\n", "2026-10-20T03:08:55Zjunk", " 2026-10-20T03:08:55Z",
            "2026-10-20T03:08:55Z ", "2026-10-20 03:08:55Z", "\u0662\u0660\u0662\u0666-10-20T03:08:55Z")
checks = 0
for validator in contracts.validators.values():
    date_time = validator.evolve(schema={"type": "string", "format": "date-time"})
    for value in accepted:
        date_time.validate(value)
        checks += 1
    for value in rejected:
        try:
            date_time.validate(value)
        except jsonschema.ValidationError:
            checks += 1
        else:
            raise AssertionError("Invalid date-time was accepted.")
assert dict(jsonschema.FormatChecker.checkers) == original_checkers
assert not network_attempts
print("DATETIME_PACKAGE_PROBE " + json.dumps({"mode": mode, "checks": checks,
      "configuration_is_null": runtime._frozen_configuration is None, "network_attempts": len(network_attempts),
      "global_registry_unchanged": True}, sort_keys=True))
'@
    Push-Location $temporary
    try {
        foreach ($package in @($generated, $healthBundle)) {
            foreach ($mode in @('absent', 'late')) {
                & $python -I -B -c $dateTimeProbe $package $mode
                if ($LASTEXITCODE -ne 0) { throw 'Isolated package date-time verification failed.' }
                $count++
            }
        }
    } finally { Pop-Location }
    # Only temporary fixture files are modified; real source/config is untouched. The fixture holds exactly what the
    # builder needs: itself, the context and asset-render helpers with their map and map-source context, the three
    # bundled modules plus the context module, and every asset root of the map.
    $fixture = Join-Path $temporary 'source-fixture'
    $paths = @('scripts/Build-CaseTool.ps1','scripts/Context.Common.ps1','scripts/AgentAssets.Common.ps1','scripts/AgentAssets.map.json',
               'tests/fixtures/context.synthetic.json',
               'operations/agent_case.py','operations/servicenow.py','operations/patch_provider.py','operations/context.py',
               'config/native-incident-runtime.json')
    foreach ($assetRoot in @('agent', 'contracts', 'operations/contracts')) {
        $assetBase = Join-Path $root $assetRoot
        $paths += @(Get-ChildItem -LiteralPath $assetBase -Recurse -File | Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
            ForEach-Object { [IO.Path]::GetRelativePath($root, $_.FullName).Replace('\', '/') })
    }
    foreach ($relative in $paths) {
        $destination = Join-Path $fixture $relative
        [void][IO.Directory]::CreateDirectory((Split-Path -Parent $destination))
        [IO.File]::WriteAllBytes($destination, [IO.File]::ReadAllBytes((Join-Path $root $relative)))
    }
    $fixtureBuilder = Join-Path $fixture 'scripts/Build-CaseTool.ps1'
    Assert-Package ((& $fixtureBuilder -PassThru) -ceq $source) 'The same exact source/config artifact must generate identical publication code.'
    Assert-Package ((& $fixtureBuilder -HealthOnly -PassThru) -ceq $healthSource) 'Health-only must be independent of present valid protected configuration.'
    $fixtureConfig = Join-Path $fixture 'config/native-incident-runtime.json'
    $finiteConfig = $runtimeRaw | ConvertFrom-Json -AsHashtable -DateKind String
    $finiteConfig['finite_commissioning'] = @{ version = '1.0' }
    $finiteConfig['expires_at'] = [DateTimeOffset]::Parse($finiteConfig['not_before']).AddSeconds(600).ToString('yyyy-MM-ddTHH:mm:ssZ')
    $finiteJson = $finiteConfig | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($fixtureConfig, $finiteJson)
    $finiteBundle = Join-Path $temporary 'finite_case_tool.py'
    & $fixtureBuilder -OutputPath $finiteBundle
    $finiteProbe = @'
import copy, importlib.util, io, json, pathlib, sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
def deny_network(event, args):
    if event.startswith("socket."):
        raise AssertionError("Finite package verification is offline only.")
sys.addaudithook(deny_network)
spec = importlib.util.spec_from_file_location("finite_package_fixture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
runtime = module._runtime()
facade = sys.modules[runtime.CaseClient.__module__]
config = json.loads(sys.argv[2])
assert runtime._configuration_frozen and runtime._frozen_configuration == config
runtime.freeze_runtime_configuration(copy.deepcopy(config))
try:
    runtime.freeze_runtime_configuration({**config, "historical_alert_ids": ["unit-different-history"]})
except runtime.CaseToolError: pass
else: raise AssertionError("Finite configuration was rebound.")
lower = runtime.stamp(config["not_before"])
now = runtime.stamp(config["expires_at"]) - timedelta(seconds=0.125)
def stamp(seconds): return (lower + timedelta(seconds=seconds)).isoformat()
investigation = "44444444-4444-4444-8444-444444444444"
alert = runtime.WORKSPACE + "/providers/Microsoft.AlertsManagement/alerts/55555555-5555-4555-8555-555555555555"
request = "66666666-6666-4666-8666-666666666666"
reference = {"record_type": "incident", "number": "INC0099999", "sys_id": "a"*32}
raw = {"id": investigation, "source": "unit-unverified", "startMessageIsHuman": False,
       "incidentSource": {"incidentType": "AzMonitor"}, "status": {"incidentStatus": {"incidentId": "unit"}},
       "incidentDetails": {"unit": "not-reviewed"}, "createdTimestamp": stamp(181)}
class Clock(datetime):
    @classmethod
    def now(cls, tz=None): return now
class Credential:
    def close(self): pass
    def get_token(self, scope):
        assert scope == "https://storage.azure.com/.default"
        return type("Token", (), {"token": "unit-storage-token"})()
# Actual generated main retains its bound decoder: plausible unit-only native
# data cannot authorize a reservation, intent, credential fetch or business POST.
with patch.object(runtime, "datetime", Clock), patch.object(runtime, "managed_identity", return_value=Credential()), \
     patch.object(runtime, "_azure_json", return_value=raw), patch.object(runtime.BlobIntents, "create") as put, \
     patch.object(runtime.BlobIntents, "read") as read, patch.object(runtime, "exact_secret") as secret:
    assert module.main("create", investigation, alert, "{}", "unit findings")["status"] == "blocked"
    put.assert_not_called()
    read.assert_not_called()
    secret.assert_not_called()
# Positive Reader protocol fixture is UNIT evidence, not a native DTO decoder
# or a change/injection into the production entrypoint.
class Reader:
    def investigation(self, identity):
        return {"id": investigation, "platform": "AzMonitor", "created_at": stamp(181),
                "alert_ids": [alert], "resource_id": runtime.RESOURCE}
    def alert(self, identity):
        return {"id": alert, "rule_id": runtime.RULE, "fired_at": stamp(180), "state": "Fired", "severity": "Sev1",
                "window_start": stamp(-480), "window_end": stamp(120)}
    def disk(self, identity):
        return {"alert_id": alert, "rule_id": runtime.RULE, "resource_id": runtime.RESOURCE, "query": runtime.QUERY,
                "samples": [{"resource_id": runtime.RESOURCE, "observed_at": stamp(90), "object_name": "Logical Disk",
                             "counter_name": "% Used Space", "instance_name": "/srv/asre-demo", "value": 82}]}
    def receipt(self, identity):
        return {"request_id": request, "resource_id": runtime.RESOURCE, "mode": "Review", "command": "inspect",
                "status": "completed", "observed_at": stamp(270), "fingerprint": "c"*64, "data": {"complete": True}}
evidence = {"contract_version": "1.0", "source_event_ids": [alert], "resource_id": runtime.RESOURCE,
            "mode": "reactive", "signal_family": "filesystem", "detected_at": stamp(180), "summary": "Offline unit fixture.",
            "evidence": [{"source": "AzureMonitor", "observed_at": stamp(180), "reference": alert, "finding": "Unit alert."},
                         {"source": "GuestInspection", "observed_at": stamp(270),
                          "reference": runtime.RECEIPT_PREFIX + request + ".json", "finding": "Unit receipt."}],
            "diagnosis": {"finding": "Unit disk evidence.", "confidence": 0.8, "gaps": ["Offline only"]},
            "actions": [], "verification": {"status": "not_started", "recovery_owner": "none"}}
data, storage, posts, receipts = {}, [], [], {}
class BlobHttp:
    def open(self, request, timeout):
        from urllib.error import HTTPError
        assert timeout == 20
        name = request.full_url.removeprefix(runtime.STORAGE + "/evidence/")
        runtime.checked_path(name)
        if request.get_method() == "PUT":
            assert request.get_header("If-none-match") == "*"
            assert request.get_header("X-ms-blob-type") == "BlockBlob"
            storage.append(("create", name))
            if name in data: raise HTTPError(request.full_url, 412, "Unit conflict", {}, io.BytesIO())
            data[name] = request.data
            raw, status = b"", 201
        else:
            storage.append(("read", name))
            if name not in data: raise HTTPError(request.full_url, 404, "Unit missing", {}, io.BytesIO())
            raw, status = data[name], 200
        response = io.BytesIO(raw)
        response.status = status
        return response
class Response(io.BytesIO):
    def __init__(self, result, status=200):
        super().__init__(json.dumps({"result": result}).encode())
        self.status = status
episode = runtime.episode_for(investigation)
frozen_evidence = {**evidence, "episode_id": episode}
class Opener:
    def open(self, req, timeout):
        path = req.full_url.removeprefix(facade.ORIGIN)
        if req.get_method() == "POST":
            payload = json.loads(req.data)
            name = "note" if path.endswith("/notes") else "create"
            plan = json.loads(data[runtime.intent_path(episode)])
            assert payload == plan["payloads"][name]
            assert len([p for p in data if "/finite/" in p]) == 1
            assert 0 < timeout <= 0.125
            posts.append((name, req.data, timeout))
            result = {"outcome": "applied", "episode_id": episode,
                      "write_request_id": payload["write_request_id"], "case_reference": reference}
            if name == "note": result["journal_sys_id"] = "b"*32
            receipts[payload["write_request_id"]] = result
            return Response(result)
        if "/writes/" in path:
            write = path.rsplit("/", 1)[1]
            return Response(receipts[write]) if write in receipts else Response(
                {"error": {"code": "write_not_found", "message": "Unit missing.", "write_request_id": None}}, 404)
        return Response({"episode_id": episode, "case_reference": reference, "resource_id": runtime.RESOURCE,
                         "mode": "reactive", "signal_family": "filesystem", "state": {"value": "1", "label": "New"},
                         "assignment_group": {"sys_id": facade.GROUP, "label": "Fixture"},
                         "ci": {"sys_id": facade.CIS["linux"], "label": "Fixture"},
                         "priority": {"value": "4", "label": "Low"}, "short_description": "Unit fixture.",
                         "description": "Offline only", "revision": "1", "updated_at": "2026-10-18 22:04:30",
                         "investigation_reference": investigation, "evidence": frozen_evidence,
                         "instance_url": facade.ORIGIN + "/incident.do?sys_id=" + reference["sys_id"]})
with patch.object(facade, "build_opener", return_value=Opener()):
    def tool():
        return runtime.CaseTool(Reader(), copy.deepcopy(config), runtime.BlobIntents(Credential(), BlobHttp()),
                                runtime.CaseClient(lambda: "unit-token"), lambda: now, monotonic=lambda: 0)
    assert tool().run("create", investigation, alert, json.dumps(evidence), "Offline unit findings.")["status"] == "persisted"
    original = dict(data)
    assert tool().run("create", investigation, alert)["status"] == "persisted"
    assert data == original and len(posts) == 2
    now = runtime.stamp(config["expires_at"])
    assert tool().run("reconcile", investigation, alert)["status"] == "persisted"
    try: tool().run("create", investigation, alert)
    except runtime.CaseToolError: pass
    else: raise AssertionError("Expired reconstructed finite tool submitted a write.")
    assert len(posts) == 2
creates = [op for op in storage if op[0] == "create"]
assert len(creates) == 2 and "/finite/" in creates[0][1] and creates[1][1] == runtime.intent_path(episode)
assert posts[0][2] == posts[1][2] == 0.125
print("FINITE_PACKAGE_PROBE " + json.dumps({"reservation_creates": 1, "intent_creates": 1,
      "business_posts": len(posts), "timeouts": [p[2] for p in posts], "unbound_main_blocked": True,
      "native_readiness_proved": False}, sort_keys=True))
'@
    Push-Location $temporary
    try {
        & $python -I -B -c $finiteProbe $finiteBundle $finiteJson
        if ($LASTEXITCODE -ne 0) { throw 'Finite isolated package verification failed.' }
    } finally { Pop-Location }
    $count++
    foreach ($change in @(
        @{ finite_commissioning = $null }, @{ finite_commissioning = $true },
        @{ finite_commissioning = $false }, @{ finite_commissioning = @{} },
        @{ finite_commissioning = @{ version = 1 } },
        @{ finite_commissioning = @{ version = '2.0' } },
        @{ finite_commissioning = @{ version = '1.0'; episodes = 1 } },
        @{ expires_at = $null }, @{ expires_at = $finiteConfig['not_before'] },
        @{ expires_at = [DateTimeOffset]::Parse($finiteConfig['not_before']).AddSeconds(601).ToString('yyyy-MM-ddTHH:mm:ssZ') },
        @{ expires_at = $finiteConfig['expires_at'].Replace('Z', '+01:00') },
        @{ not_before = $finiteConfig['not_before'].Replace('Z', '-00:00') },
        @{ expires_at = ($finiteConfig['expires_at'] + "`n") }
    )) {
        $invalidFinite = $finiteJson | ConvertFrom-Json -AsHashtable -DateKind String
        foreach ($key in $change.Keys) { $invalidFinite[$key] = $change[$key] }
        [IO.File]::WriteAllText($fixtureConfig, ($invalidFinite | ConvertTo-Json -Depth 8 -Compress))
        $denied = $false
        try { & $fixtureBuilder -PassThru | Out-Null } catch { $denied = $true }
        Assert-Package $denied 'Malformed finite configuration must fail in the real protected builder.'
        Assert-Package ((& $fixtureBuilder -HealthOnly -PassThru) -ceq $healthSource) 'Finite malformed configuration must not change health-only/null packaging.'
    }
    $missingExpiry = $finiteJson | ConvertFrom-Json -AsHashtable -DateKind String
    $missingExpiry.Remove('expires_at')
    [IO.File]::WriteAllText($fixtureConfig, ($missingExpiry | ConvertTo-Json -Depth 8 -Compress))
    $denied = $false
    try { & $fixtureBuilder -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'Finite configuration requires explicit expiry.'
    $badConfig = $runtimeRaw | ConvertFrom-Json -AsHashtable -DateKind String
    $badConfig['expires_at'] = $badConfig['not_before']
    [IO.File]::WriteAllText($fixtureConfig, ($badConfig | ConvertTo-Json -Depth 8 -Compress))
    $denied = $false
    try { & $fixtureBuilder -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'An optional expiry at or before the persistent boundary must fail.'
    [IO.File]::WriteAllText($fixtureConfig, ($runtimeRaw.Trim().TrimEnd('}') + ',"version":"1.0"}'))
    $denied = $false
    try { & $fixtureBuilder -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'Duplicate active configuration keys must fail.'
    [IO.File]::WriteAllText($fixtureConfig, '{malformed')
    Assert-Package ((& $fixtureBuilder -HealthOnly -PassThru) -ceq $healthSource) 'Malformed protected configuration must not affect health-only output.'
    foreach ($explicitFalse in @($false, $true)) {
        $denied = $false
        try {
            if ($explicitFalse) { & $fixtureBuilder -HealthOnly:$false -PassThru | Out-Null }
            else { & $fixtureBuilder -PassThru | Out-Null }
        } catch { $denied = $true }
        Assert-Package $denied 'Default and explicit false must reject malformed protected configuration.'
    }
    [IO.File]::Delete($fixtureConfig)
    $denied = $false
    try { & $fixtureBuilder -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'Missing fixed source-bound configuration must fail, never publish null.'
    $denied = $false
    try { & $fixtureBuilder -HealthOnly:$false -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'Explicit false must reject missing protected configuration.'
    Assert-Package ((& $fixtureBuilder -HealthOnly -PassThru) -ceq $healthSource) 'Missing protected configuration must not affect health-only output.'
    $fixtureDeclaration = Join-Path $fixture 'agent/tools/servicenow_case.json'
    $declarationRaw = [IO.File]::ReadAllText($fixtureDeclaration)
    [IO.File]::WriteAllText($fixtureDeclaration, $declarationRaw.Replace('asre-servicenow-case', 'unreviewed-case'))
    $denied = $false
    try { & $fixtureBuilder -HealthOnly -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'Health-only must retain fixed declaration validation.'
    [IO.File]::WriteAllText($fixtureDeclaration, $declarationRaw)
    $fixtureWorkflow = Join-Path $fixture 'agent/workflows/azure-monitor-review.json'
    $badWorkflow = $workflowRaw | ConvertFrom-Json -AsHashtable -DateKind String
    $badWorkflow['commissioning']['version'] = 'invalid'
    [IO.File]::WriteAllText($fixtureWorkflow, ($badWorkflow | ConvertTo-Json -Depth 32))
    $denied = $false
    try { & $fixtureBuilder -HealthOnly -PassThru | Out-Null } catch { $denied = $true }
    Assert-Package $denied 'Health-only must retain shared commissioning policy validation.'

    # ---- The bundle follows the deployment context --------------------------------------------------------------
    $bindingMessage = 'Trusted runtime binding differs from the deployment context.'
    $contextMessage = 'The case-tool bundle needs a deployment context with its ServiceNow section and agent endpoint.'
    $contextProbe = @'
import base64, importlib.util, io, json, pathlib, sys, zipfile
def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Network is forbidden in offline package verification.")
sys.addaudithook(deny_network)
path, expected = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2])
spec = importlib.util.spec_from_file_location("context_case_tool", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with zipfile.ZipFile(io.BytesIO(base64.b64decode(module.__CASE_TOOL_BUNDLE__))) as package:
    stored = package.read("_asre_case/context.json").decode("utf-8")
    config = json.loads(package.read("_asre_case/runtime-configuration.json").decode("utf-8"))
    contracts = package.read("_asre_case/contracts/servicenow-case.schema.json").decode("utf-8")
assert stored == expected["context_json"]
assert config["facade_origin"] == expected["origin"]
assert config["facade_client_id"] == expected["facade_client_id"]
assert config["resource_id"] == expected["resource_id"]
assert config["rule_id"].lower().startswith(expected["control_group_id"].lower() + "/providers/microsoft.insights/scheduledqueryrules/")
assert config["reader"]["sre_origin"] == expected["sre_origin"]
assert config["reader"]["log_analytics_workspace_id"] == expected["customer_id"]
assert config["reader"]["workspace_resource_id"] == expected["workspace_resource_id"]
for resource_id in expected["target_resource_ids"]:
    assert resource_id in contracts, resource_id
for stale in expected["stale"]:
    assert stale not in contracts and stale not in stored, stale
    assert stale not in config["resource_id"] and stale not in config["rule_id"], stale
    assert stale not in config["reader"]["workspace_resource_id"] and stale not in config["facade_origin"], stale
    assert stale not in config["reader"]["sre_origin"], stale
runtime = module._runtime()
facade = sys.modules[runtime.CaseClient.__module__]
assert facade.ORIGIN == expected["origin"] and facade.CLIENT_ID == expected["client_id"]
assert runtime.FACADE_CLIENT == expected["facade_client_id"] and runtime.SRE_ORIGIN == expected["sre_origin"]
assert runtime.RESOURCE == expected["resource_id"] and runtime.WORKSPACE.lower() == expected["workspace_resource_id"].lower()
assert runtime.LOG_WORKSPACE_ID == expected["customer_id"]
assert runtime._configuration_frozen and runtime._frozen_configuration == config
'@
    function Get-ContextExpectation([string]$DocumentPath, [string[]]$Stale) {
        # Every expected value is derived from the stored document itself, never from the builder or the loader.
        $document = Get-Content -LiteralPath $DocumentPath -Raw | ConvertFrom-Json -AsHashtable
        $base = '/subscriptions/' + $document['subscription_id'] + '/resourceGroups/'
        $vm = { param($role) $base + $document['targets'][$role]['resource_group'] + '/providers/Microsoft.Compute/virtualMachines/' + $document['targets'][$role]['name'] }
        return [ordered]@{
            context_json = Get-CanonicalContextJson $DocumentPath
            origin = $document['servicenow']['origin']
            client_id = $document['servicenow']['client_id']
            facade_client_id = $document['servicenow']['facade_client_id']
            sre_origin = $document['agent']['endpoint']
            customer_id = $document['log_analytics']['customer_id']
            control_group_id = $base + $document['resource_groups']['control']
            workspace_resource_id = $base + $document['resource_groups']['control'] + '/providers/Microsoft.OperationalInsights/workspaces/' + $document['log_analytics']['name']
            resource_id = (& $vm 'linux')
            target_resource_ids = @((& $vm 'linux'), (& $vm 'patch'), (& $vm 'backup'))
            stale = $Stale
        }
    }
    function Assert-ContextBundle([string]$Bundle, [string]$DocumentPath, [string[]]$Stale, [string]$Label) {
        $expectation = Get-ContextExpectation $DocumentPath $Stale
        Push-Location $temporary
        try {
            & $python -I -B -c $contextProbe $Bundle ($expectation | ConvertTo-Json -Compress -Depth 6)
            if ($LASTEXITCODE -ne 0) { throw "Context-bound package verification failed ($Label)." }
        } finally { Pop-Location }
        Assert-Package $true $Label
    }
    $syntheticSubscription = '00000001-000a-4000-a000-000000000001'
    $altBundle = Join-Path $temporary 'alt_case_tool.py'
    [void](Use-AsreTestContext -Name alt)
    & $builder -OutputPath $altBundle
    Assert-Package ([IO.File]::ReadAllText($altBundle) -cne $source) 'A different deployment context must produce a different bundle.'
    Assert-ContextBundle $altBundle $altFixture @($syntheticSubscription, 'example-instance') 'The alternative context must bind origin, client, endpoint, workspace and resource.'
    $altHealth = & $builder -HealthOnly -PassThru
    Assert-Package ($altHealth -is [string] -and $altHealth -cne $healthSource -and $altHealth -cne $source) 'Health-only output must also follow the deployment context.'
    Assert-Package ((& $builder -PassThru) -ceq [IO.File]::ReadAllText($altBundle)) 'A context-bound bundle must be deterministic.'
    [void](Use-AsreTestContext -Name synthetic)
    Assert-Package ((& $builder -PassThru) -ceq $source) 'Returning to the synthetic context must restore the synthetic bundle.'

    # A deployment context that the real loader accepts (non-synthetic), through the real loader and no test alias.
    $realisticFile = Join-Path $temporary 'context.realistic.json'
    Write-ContextFile $realisticFile (New-RealisticDocument)
    $savedContextFile = $env:ASRE_CONTEXT_FILE
    Clear-AsreTestContext
    try {
        $env:ASRE_CONTEXT_FILE = $realisticFile
        $realisticBundle = Join-Path $temporary 'realistic_case_tool.py'
        & $builder -OutputPath $realisticBundle
        Assert-ContextBundle $realisticBundle $realisticFile @($syntheticSubscription, 'example-instance', 'alt-instance', '00000901-000a-4000-8000-000000000901') 'A real (non-synthetic) context must bind origin, client, endpoint, workspace and resource.'
        # The real loader refuses synthetic contexts and unreadable files: nothing is published.
        foreach ($refused in @($syntheticFixture, $altFixture, (Join-Path $temporary 'absent-context.json'))) {
            $env:ASRE_CONTEXT_FILE = $refused
            $refusedOutput = Join-Path $temporary ('refused-' + [guid]::NewGuid().ToString('N') + '.py')
            Assert-Package ($null -ne (Get-BuildFailure { & $builder -OutputPath $refusedOutput })) 'The real loader must refuse a synthetic or absent context.'
            Assert-Package (-not (Test-Path -LiteralPath $refusedOutput)) 'A refused context must leave no artifact.'
            Assert-Package ($null -ne (Get-BuildFailure { & $builder -PassThru })) 'A refused context must not return code.'
            Assert-Package ($null -ne (Get-BuildFailure { & $builder -HealthOnly -PassThru })) 'A refused context must not return health-only code.'
        }
    } finally {
        if ($null -eq $savedContextFile) { Remove-Item -LiteralPath Env:ASRE_CONTEXT_FILE -ErrorAction SilentlyContinue } else { $env:ASRE_CONTEXT_FILE = $savedContextFile }
        [void](Use-AsreTestContext -Name synthetic)
    }

    # A context without its ServiceNow section or its agent endpoint is refused with the fixed message, before any file.
    foreach ($missing in @('servicenow', 'agent endpoint')) {
        $incomplete = Get-Content -LiteralPath $altFixture -Raw | ConvertFrom-Json -AsHashtable
        if ($missing -ceq 'servicenow') { $incomplete.Remove('servicenow') } else { $incomplete['agent'].Remove('endpoint') }
        $incompleteFile = Join-Path $temporary ('context.without-' + $missing.Replace(' ', '-') + '.json')
        Write-ContextFile $incompleteFile $incomplete
        [void](Use-AsreTestContext -Path $incompleteFile)
        $incompleteOutput = Join-Path $temporary ('incomplete-' + [guid]::NewGuid().ToString('N') + '.py')
        Assert-Package ((Get-BuildFailure { & $builder -OutputPath $incompleteOutput }) -ceq $contextMessage) "A context without its $missing must be refused with the fixed message."
        Assert-Package (-not (Test-Path -LiteralPath $incompleteOutput)) "A context without its $missing must leave no artifact."
        Assert-Package ((Get-BuildFailure { & $builder -PassThru }) -ceq $contextMessage) "A context without its $missing must not return code."
        Assert-Package ((Get-BuildFailure { & $builder -HealthOnly -PassThru }) -ceq $contextMessage) "A context without its $missing must not return health-only code."
    }
    [void](Use-AsreTestContext -Name synthetic)

    # Every trusted-runtime binding is tied to the deployment context itself: a self-consistent but different value is denied.
    $fixtureWorkflow = Join-Path $fixture 'agent/workflows/azure-monitor-review.json'
    function Set-FixtureDocuments([scriptblock]$Mutate) {
        $mutableConfig = $runtimeRaw | ConvertFrom-Json -AsHashtable -DateKind String
        $mutableWorkflow = $workflowRaw | ConvertFrom-Json -AsHashtable -DateKind String
        & $Mutate $mutableConfig $mutableWorkflow
        [IO.File]::WriteAllText($fixtureConfig, ($mutableConfig | ConvertTo-Json -Depth 8 -Compress))
        [IO.File]::WriteAllText($fixtureWorkflow, ($mutableWorkflow | ConvertTo-Json -Depth 32))
    }
    Set-FixtureDocuments { param($c, $w) }
    Assert-Package ($null -eq (Get-BuildFailure { & $fixtureBuilder -PassThru })) 'The unmutated fixture must build (control for the binding denials).'
    $bindingCases = @(
        @{ Name = 'facade origin'; Mutate = { param($c, $w) $c['facade_origin'] = 'https://other-instance.service-now.com' } },
        @{ Name = 'facade client id'; Mutate = { param($c, $w) $c['facade_client_id'] = '0000000000000000000000000000ffff' } },
        @{ Name = 'SRE origin'; Mutate = { param($c, $w) $c['reader']['sre_origin'] = 'https://other-agent.centralus.azuresre.ai' } },
        @{ Name = 'workspace customer id'; Mutate = { param($c, $w) $c['reader']['log_analytics_workspace_id'] = '00000063-000a-4000-8000-000000000063' } },
        @{ Name = 'configuration resource only'; Mutate = { param($c, $w) $c['resource_id'] = $c['resource_id'].Replace('vm-asre-linux-cus', 'vm-other') } },
        @{ Name = 'workflow scope resource only'; Mutate = { param($c, $w) $w['scope']['resource_id'] = $w['scope']['resource_id'].Replace('vm-asre-linux-cus', 'vm-other') } },
        @{ Name = 'resource agreed by configuration and workflow'; Mutate = { param($c, $w) $id = $c['resource_id'].Replace('vm-asre-linux-cus', 'vm-other'); $c['resource_id'] = $id; $w['scope']['resource_id'] = $id } },
        @{ Name = 'workspace agreed by configuration and workflow'; Mutate = { param($c, $w) $id = $c['reader']['workspace_resource_id'].Replace('law-asre-cus', 'law-other'); $c['reader']['workspace_resource_id'] = $id; $w['scope']['workspace_resource_id'] = $id } },
        @{ Name = 'rule outside the control group'; Mutate = { param($c, $w) $id = $c['rule_id'].Replace('rg-asre-control-cus', 'rg-other'); $c['rule_id'] = $id; $w['scope']['rule_id'] = $id } },
        @{ Name = 'rule without a name'; Mutate = { param($c, $w) $id = $w['scope']['rule_id'].Substring(0, $w['scope']['rule_id'].LastIndexOf('/') + 1); $c['rule_id'] = $id; $w['scope']['rule_id'] = $id } },
        @{ Name = 'rule of another subscription'; Mutate = { param($c, $w) $id = $c['rule_id'].Replace($syntheticSubscription, '00000002-000a-4000-a000-000000000002'); $c['rule_id'] = $id; $w['scope']['rule_id'] = $id } }
    )
    foreach ($case in $bindingCases) {
        Set-FixtureDocuments $case.Mutate
        $bindingOutput = Join-Path $temporary ('binding-' + [guid]::NewGuid().ToString('N') + '.py')
        Assert-Package ((Get-BuildFailure { & $fixtureBuilder -OutputPath $bindingOutput }) -ceq $bindingMessage) "A different $($case.Name) must be denied by the context binding."
        Assert-Package (-not (Test-Path -LiteralPath $bindingOutput)) "A different $($case.Name) must leave no artifact."
    }

    # The private render never outlives the build, whether it succeeds, is refused or fails.
    $privateTemp = Join-Path $temporary 'private-temp'
    [void][IO.Directory]::CreateDirectory($privateTemp)
    $savedTmp = $env:TMP
    $savedTemp = $env:TEMP
    try {
        $env:TMP = $privateTemp
        $env:TEMP = $privateTemp
        Assert-Package ([IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetTempPath()) -ieq $privateTemp) 'The temporary location must be redirected for the cleanup proof.'
        Assert-Package ($null -eq (Get-BuildFailure { & $builder -PassThru })) 'A successful build must not leave a private render behind.'
        Assert-Package ($null -eq (Get-BuildFailure { & $builder -HealthOnly -PassThru })) 'A successful health-only build must not leave a private render behind.'
        Assert-Package ((Get-BuildFailure { & $fixtureBuilder -PassThru }) -ceq $bindingMessage) 'A denied build must fail while the cleanup proof is active.'
        [void](Use-AsreTestContext -Path $incompleteFile)
        Assert-Package ((Get-BuildFailure { & $builder -PassThru }) -ceq $contextMessage) 'A refused context must fail while the cleanup proof is active.'
        [void](Use-AsreTestContext -Name synthetic)
        Assert-Package (@(Get-ChildItem -LiteralPath $privateTemp -Force).Count -eq 0) 'No private render may remain after a successful, denied or refused build.'
    } finally {
        $env:TMP = $savedTmp
        $env:TEMP = $savedTemp
        [void](Use-AsreTestContext -Name synthetic)
    }
    Write-Output "$count case-tool package checks passed (offline only)."
} finally {
    Clear-AsreTestContext
    [IO.Directory]::Delete($temporary, $true)
}
