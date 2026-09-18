"""Offline structural checks. ARM/provider validation and private live proof remain required."""
import json
from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"


def test_foundation_luna_capacity_is_explicit_and_quota_sized():
    main = (INFRA / "main.bicep").read_text("utf-8")
    module = (INFRA / "modules" / "foundation.bicep").read_text("utf-8")
    assert "param lunaCapacity int = 300" in main
    assert "lunaCapacity: lunaCapacity" in main
    assert "output AZURE_AI_MODEL_CAPACITY int = lunaCapacity" in main
    assert "param lunaCapacity int" in module and "capacity: lunaCapacity" in module
    assert "capacity: 50" not in module


def test_review_cold_start_has_bounded_readiness_allowance():
    text = (INFRA / "modules" / "services.bicep").read_text("utf-8")
    review = text.split("module review ", 1)[1].split("module bootstrap ", 1)[0]
    assert "WEBSITES_CONTAINER_START_TIME_LIMIT: '600'" in review
    assert "--timeout=120" in review


def source(name):
    return (INFRA / name).read_text("utf-8")


def test_service_entrypoint_cannot_reprovision_foundation():
    text = source("services.bicep")
    assert "resourceGroups@2025-04-01' existing" in text
    assert "./modules/foundation.bicep" not in text
    assert "@allowed(['infrastructure', 'bootstrap', 'preflight', 'release', 'runtime'])" in text
    assert "param bootstrapAuthorized bool = false" in text
    assert "param embeddingBudgetScope string = ''" in text
    settings = json.loads((INFRA / "services.parameters.json").read_text())["parameters"]
    assert settings["phase"]["value"] == "infrastructure"
    assert settings["bootstrapAuthorized"]["value"] is False
    assert "administratorLogin" not in text


def test_network_topology_and_no_public_data_bypass():
    text = source("modules/services.bicep")
    assert "var functions = ['worker', 'tools', 'publisher']" in text
    assert "var principals = ['review', 'worker', 'tools', 'publisher', 'bootstrap']" in text
    assert "delegation: 'Microsoft.Web/serverFarms'" in text
    assert "delegation: 'Microsoft.App/environments'" in text
    assert text.count("name: 'private-endpoints'") == 1
    assert "groupId: 'sites'" in text
    for zone in ("database.windows.net", "blob.core.windows.net", "table.core.windows.net",
                 "vaultcore.azure.net", "azurewebsites.net"):
        assert f"'privatelink.{zone}'" in text
    assert "AzureServices" not in text
    assert "allowSharedKeyAccess: false" in text
    assert text.count("bypass: 'None'") == 2
    assert "publicNetworkAccess: 'Disabled'" in text
    assert "sku: 'standard'" in text and "enablePurgeProtection: true" in text
    assert "Microsoft.Network/virtualNetworkGateways" not in text
    assert "Microsoft.Compute/" not in text


def test_flex_is_identity_only_private_and_remote_build_compatible():
    text = source("modules/service-function.bicep")
    for expected in (
        "name: 'FC1'", "tier: 'FlexConsumption'", "instanceMemoryMB: 2048",
        "maximumInstanceCount: 40", "alwaysReady: []", "publicNetworkAccess: 'Disabled'",
        "AzureWebJobsStorage__credential: 'managedidentity'",
        "AzureWebJobsStorage__clientId: identityClientId",
        "AzureWebJobsStorage__blobServiceUri:", "AzureWebJobsStorage__tableServiceUri:",
        "userAssignedIdentityResourceId: identityId",
        "type: 'UserAssignedIdentity'", "requireAuthentication: true",
        "unauthenticatedClientAction: 'Return401'",
        "ENTRA_AUDIENCE: applicationClientId",
        "allowedAudiences: [applicationClientId]",
        "{ name: 'scm', allow: false }", "{ name: 'ftp', allow: false }",
    ):
        assert expected in text
    for forbidden in ("WEBSITE_RUN_FROM_PACKAGE", "AzureWebJobsStorage__queueServiceUri",
                      "SCM_DO_BUILD_DURING_DEPLOYMENT", "ENABLE_ORYX_BUILD", "listKeys(",
                      "ITOPS_ALLOW_EASYAUTH",
                      "WEBSITE_CONTENTAZUREFILECONNECTIONSTRING", "FUNCTIONS_WORKER_RUNTIME"):
        assert forbidden not in text
    assert "union(settings, {" in text  # Protected settings win over caller configuration.
    composition = source("modules/services.bicep")
    assert "ITOPS_EMBEDDING_BUDGET_SCOPE: embeddingBudgetScope" in composition
    for setting in ("ITOPS_TOOLS_AUDIENCE", "ITOPS_PROJECTION_AUDIENCE", "ITOPS_PUBLISHER_AUDIENCE"):
        assert f"{setting}: serviceApplicationClientId" in composition
    for setting in ("ITOPS_TOOLS_TOKEN_SCOPE", "ITOPS_PROJECTION_TOKEN_SCOPE"):
        assert f"{setting}: 'api://${{serviceApplicationClientId}}/.default'" in composition


def test_bootstrap_is_isolated_and_stopped_unless_explicitly_authorized():
    text = source("modules/services.bicep")
    bootstrap = text.split("module bootstrap ", 1)[1].split("var settings ", 1)[0]
    assert "serverFarmResourceId: webPlan.outputs.resourceId" in bootstrap
    assert "userAssignedResourceIds: [identities[4].outputs.resourceId]" in bootstrap
    assert "enabled: bootstrapActive" in bootstrap
    assert "(phase == 'bootstrap' || phase == 'preflight' || phase == 'release') && bootstrapAuthorized" in text
    assert "phase == 'runtime' || phase == 'release' || (phase == 'preflight' && service == 'worker')" in text
    assert "ITOPS_PREFLIGHT_BOOTSTRAP_OID: identities[4].outputs.principalId" in text
    assert "ITOPS_ACCESS_PROBE_CONTRACT_PATH: '.runtime/access-probe-contract.json'" in text
    assert "ipSecurityRestrictionsDefaultAction: 'Deny'" in bootstrap
    assert "scmIpSecurityRestrictionsUseMain: false" in bootstrap
    assert "scmIpSecurityRestrictionsDefaultAction: 'Allow'" in bootstrap
    assert "basicPublishingCredentialsPolicies: noBasicAuth" in bootstrap
    review = text.split("module review ", 1)[1].split("module bootstrap ", 1)[0]
    assert "identities[4]" not in review
    assert "enabled: phase == 'runtime'" in review
    assert "SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'" in review
    assert "agentic_itops.service_projection:create_review_service()" in review
    assert "--workers=1 --threads=4" in review
    assert "ITOPS_WORKER_ORIGIN: 'https://${serviceNames[0]}.azurewebsites.net'" in review
    assert "ITOPS_WORKER_TOKEN_SCOPE: 'api://${serviceApplicationClientId}/.default'" in review
    assert "ITOPS_PUBLISHER_ORIGIN: 'https://${serviceNames[2]}.azurewebsites.net'" in review
    assert "ITOPS_PUBLISHER_TOKEN_SCOPE: 'api://${serviceApplicationClientId}/.default'" in review


def test_review_easyauth_supplies_the_signed_id_token():
    review = source("modules/services.bicep").split("module review ", 1)[1].split("module bootstrap ", 1)[0]
    assert "ITOPS_ALLOW_EASYAUTH: 'true'" in review
    assert "login: { tokenStore: { enabled: true } }" in review
    assert "requireAuthentication: true" in review
    assert "loginParameters: ['scope=openid profile email']" in review


def test_review_frontend_explicitly_allows_only_review_and_worker_clients():
    review = source("modules/services.bicep").split("module review ", 1)[1].split("module bootstrap ", 1)[0]
    assert re.search(
        r"allowedApplications:\s*\[\s*reviewApplicationClientId\s+identities\[1\]\.outputs\.clientId\s*\]",
        review)
    assert review.count("allowedApplications:") == 1
    assert "allowedAudiences: [\n              reviewApplicationClientId\n              serviceApplicationClientId" in review
    assert "roles: ['Results.Project']" in review
    assert "requireAuthentication: true" in review
    assert "excludedPaths" not in review and "AllowAnonymous" not in review


def test_release_enables_deployment_with_only_bootstrap_application_callers():
    text = source("modules/services.bicep")
    assert "var invocationRoles = ['Worker.Invoke', 'Tools.Invoke', 'Publisher.Invoke']" in text
    assert "callerObjectIds: phase == 'release' || (phase == 'preflight' && service == 'worker')" in text
    assert "? [identities[4].outputs.principalId]" in text
    override = text.split("} : {}, phase == 'release' || (phase == 'preflight' && service == 'worker') ? {", 1)[1]
    override = override.split("} : {})", 1)[0]
    assert "ITOPS_SERVICE_CALLERS_JSON: string([{" in override
    assert "oid: identities[4].outputs.principalId" in override
    assert "client_id: identities[4].outputs.clientId" in override
    assert "roles: [invocationRoles[i]]" in override


def test_publisher_uses_its_own_factory_settings_and_release_caller_gate():
    text = source("modules/services.bicep")
    override = text.split("}, service == 'publisher' ? {", 1)[1].split("} : {}, phase", 1)[0]
    assert "ITOPS_PUBLISHER_ENABLED: phase == 'runtime' || phase == 'release' ? 'true' : 'false'" in override
    assert "ITOPS_PUBLISHER_CALLER_OIDS_JSON: string(phase == 'release'" in override
    assert "? [identities[4].outputs.principalId]" in override
    assert ": serviceCallerObjectIds)" in override
    factory = (ROOT / "services" / "publisher" / "function_app.py").read_text()
    assert '_required(environment, "ITOPS_PUBLISHER_ENABLED")' in factory
    assert '_required(environment, "ITOPS_PUBLISHER_CALLER_OIDS_JSON")' in factory


def test_rbac_scopes_are_resources_not_subscription_and_no_owner_fallback():
    text = source("modules/services.bicep")
    assert "scope: evidenceContainer" in text
    assert "scope: project" in text
    assert "scope: ai" in text
    assert "scope: search" in text
    assert "scope: insights" in text
    assert "Microsoft.Web/sites/publish/Action" in text
    assert "Microsoft.Web/sites/*" not in text
    assert "containers/blobs/delete" not in text
    assert "Directory Readers" not in text
    for privileged in ("8e3af657-a8ff-443c-a75c-2fe8c4bcb635", "b24988ac-6180-42a0-ab88-20f7382dd24c"):
        assert privileged not in text
    assert "if (bootstrapAuthorized)" in text


def test_shared_flex_subnet_integrations_are_serialized():
    text = source("modules/services.bicep")
    assert "@batchSize(1)\nmodule functionApps " in text


def test_azd_wiring_uses_exact_staged_packages_and_guards_private_deploy():
    config = yaml.safe_load((ROOT / "azure.yaml").read_text())
    assert config["infra"]["module"] == "services"
    assert set(config["services"]) == {"review", "worker", "tools", "publisher"}
    for name, service in config["services"].items():
        assert service["project"] == rf".\.build\azd\{name}"
        assert service["language"] == "python"
        if name != "review":
            assert service["host"] == "function"
            assert "deployment.control_plane" in service["hooks"]["predeploy"]["run"]


def test_avm_pins_are_explicit():
    text = "\n".join(p.read_text() for p in (INFRA / "modules").glob("service*.bicep"))
    modules = re.findall(r"'br/public:([^']+)'", text)
    assert modules
    assert all(re.fullmatch(r"avm/res/[a-z0-9/-]+:\d+\.\d+\.\d+", module) for module in modules)


def test_compiled_service_template_has_no_foundation_or_credential_outputs():
    path = ROOT / ".build" / "services.arm.json"
    if not path.exists():
        pytest.skip("run az bicep build --file infra\\services.bicep --outfile .build\\services.arm.json first")
    sources = [INFRA / "services.bicep", *(INFRA / "modules").glob("service*.bicep")]
    assert path.stat().st_mtime_ns >= max(item.stat().st_mtime_ns for item in sources), (
        "compiled service template is stale; rebuild before claiming infrastructure validation"
    )
    template = json.loads(path.read_text("utf-8-sig"))
    outputs = template["outputs"]
    assert set(outputs) == {
        "AZURE_RESOURCE_GROUP", "SERVICE_REVIEW_NAME", "SERVICE_WORKER_NAME", "SERVICE_TOOLS_NAME",
        "SERVICE_PUBLISHER_NAME", "SERVICE_BOOTSTRAP_NAME", "AZURE_KEY_VAULT_NAME",
        "ITOPS_SERVICE_IDENTITIES", "ITOPS_REVIEW_URL", "ITOPS_PRIVATE_HOSTNAMES",
        "ITOPS_SERVICE_HOSTNAMES",
    }
    forbidden = {"Microsoft.Sql/servers", "Microsoft.CognitiveServices/accounts",
                 "Microsoft.CognitiveServices/accounts/projects", "Microsoft.Search/searchServices",
                 "Microsoft.Insights/components", "Microsoft.OperationalInsights/workspaces"}
    def resources(value):
        if isinstance(value, dict):
            if (isinstance(value.get("type"), str) and value["type"] in forbidden
                    and "apiVersion" in value and not value.get("existing")):
                pytest.fail(f"service template attempts to write foundation resource {value['type']}")
            for child in value.values():
                resources(child)
        elif isinstance(value, list):
            for child in value:
                resources(child)
    resources(template)
