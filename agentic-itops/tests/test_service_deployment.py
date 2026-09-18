from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from unittest.mock import Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest

from deployment.bootstrap import (BootstrapError, Journal, _sql_connection, deploy_function,
                                  _run_locked, initialize_search, initialize_secrets, load_manifest, private_dns, probe_source)
from deployment.control_plane import Operator, approval_plan, execute, upload_bootstrap
from deployment.bundle import bundle
from deployment.manifest import create as create_bootstrap_manifest
from deployment.migrate import MIGRATIONS, Migration, MigrationError, apply_grants, apply_migrations, split_batches, sql_identity_statement
from deployment.package import ROOT, SEARCH_SCHEMA_ASSET, add_artifacts, requirements, sources, stage, validate_function_zip, write_zip
from deployment.restore_admin import restore


@pytest.fixture
def workspace():
    path = ROOT / ".build" / "deployment-tests" / str(uuid4())
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def manifest():
    suffix = "gcjgvzpp44b4i"
    sql = f"sql-agentic-itops-us93-{suffix}.database.windows.net"
    vault = f"kv-itops-{suffix}.vault.azure.net"
    value = {
        "schema_version": 1, "deployment_id": str(uuid4()),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "bootstrap_client_id": str(uuid4()), "bootstrap_object_id": str(uuid4()), "bootstrap_site": f"app-bootstrap-{suffix}",
        "sql_server": sql, "sql_database": "itops", "vault_url": f"https://{vault}",
        "search_endpoint": f"https://srch-agentic-itops-us93-{suffix}.search.windows.net",
        "private_hosts": [sql, vault, f"stitops{suffix}.blob.core.windows.net",
                          *(f"sthost{i}{suffix}.{kind}.core.windows.net"
                            for i in range(3) for kind in ("blob", "table")),
                          *(f"func-{name}-{suffix}{scm}.azurewebsites.net"
                            for name in ("worker", "tools", "publisher") for scm in ("", ".scm"))],
        "operations": ["migrate"], "runtime_object_ids": {
            name: str(uuid4()) for name in ("review", "worker", "tools", "publisher")
        },
        "review_scopes": {"sources": [], "entitlements": []},
        "search_index": {}, "packages": [],
    }
    return value


def environment(value, raw):
    return {
        "ITOPS_BOOTSTRAP_AUTHORIZED": "true", "ITOPS_BOOTSTRAP_MANIFEST_SHA256": sha256(raw).hexdigest(),
        **{env: value[key] for key, env in {
            "bootstrap_client_id": "AZURE_CLIENT_ID", "bootstrap_site": "WEBSITE_SITE_NAME",
            "sql_server": "ITOPS_SQL_SERVER", "sql_database": "ITOPS_SQL_DATABASE",
            "vault_url": "ITOPS_KEY_VAULT_URL", "search_endpoint": "ITOPS_SEARCH_ENDPOINT",
        }.items()},
    }


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1;\nGO\nSELECT 2;\n", ["SELECT 1;", "SELECT 2;"]),
    ("SELECT 1;\ngo -- note\nSELECT 2;", ["SELECT 1;", "SELECT 2;"]),
    ("SELECT 'one\nGO\ntwo';\nGO\nSELECT 2;", ["SELECT 'one\nGO\ntwo';", "SELECT 2;"]),
    ("/* outer\n/* nested */\nGO\n*/\nSELECT 1;\nGO", ["/* outer\n/* nested */\nGO\n*/\nSELECT 1;"]),
    ("SELECT [one\nGO\ntwo];\nGO", ["SELECT [one\nGO\ntwo];"]),
    ("SELECT 'it''s';\n-- batch\nSELECT 2;", ["SELECT 'it''s';", "SELECT 2;"]),
])
def test_sql_batches(sql, expected):
    assert split_batches(sql) == expected


@pytest.mark.parametrize("sql", ["SELECT 1;\nGO 2\n", ":r other.sql\n", "!!command\n", "SELECT 'unterminated", "/* missing"])
def test_sql_batches_fail_closed(sql):
    with pytest.raises(MigrationError):
        split_batches(sql)


def test_all_original_sql_migrations_have_valid_batches():
    for migration in MIGRATIONS:
        assert split_batches((ROOT / migration.path).read_text("utf-8-sig"))
    assert "BEGIN TRANSACTION" in (ROOT / MIGRATIONS[3].path).read_text()


class Cursor:
    def __init__(self, *, applied=None, sentinel=None, fail=False):
        self.applied, self.sentinel, self.fail = applied, sentinel, fail
        self.commands = []
        self.result = None

    def execute(self, sql, *parameters):
        self.commands.append((sql, parameters))
        if "SELECT @r;" in sql:
            self.result = (0,)
        elif sql.startswith("SELECT source_sha256"):
            self.result = (self.applied,) if self.applied else None
        elif sql.startswith(("SELECT OBJECT_ID", "SELECT COL_LENGTH")):
            self.result = (self.sentinel,)
        elif sql == "SELECT @@TRANCOUNT, XACT_STATE()":
            self.result = (1, 1)
        elif sql == "CREATE TABLE example(id int);":
            if self.fail:
                raise RuntimeError("simulated DDL failure")
        elif sql.startswith("SELECT COUNT(*)"):
            self.result = (0,)
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []

    def nextset(self):
        return False

    def close(self):
        pass


def migration_fixture(workspace, **kwargs):
    payload = b"CREATE TABLE example(id int);\nGO\n"
    (workspace / "test.sql").write_bytes(payload)
    cursor = Cursor(**kwargs)
    connection = Mock(cursor=Mock(return_value=cursor))
    migration = Migration("test/001", "test.sql", "dbo.example")
    return connection, cursor, migration, sha256(payload).hexdigest()


def test_migration_ledger_commits_with_schema(workspace):
    connection, cursor, migration, digest = migration_fixture(workspace)
    assert apply_migrations(connection, workspace, [migration])[0]["sha256"] == digest
    commands = [item[0] for item in cursor.commands]
    assert commands.index("BEGIN TRANSACTION") < commands.index("CREATE TABLE example(id int);")
    assert commands[-2] == "COMMIT TRANSACTION"
    assert "sp_releaseapplock" in commands[-1]
    assert connection.autocommit is True


def test_migration_ledger_hash_prevents_reapplication(workspace):
    connection, cursor, migration, digest = migration_fixture(workspace)
    cursor.applied = digest
    assert apply_migrations(connection, workspace, [migration])[0]["status"] == "already-applied"
    assert not any(sql == "BEGIN TRANSACTION" for sql, _ in cursor.commands)
    cursor.applied = "0" * 64
    with pytest.raises(MigrationError, match="hash changed"):
        apply_migrations(connection, workspace, [migration])


def test_migration_does_not_adopt_untracked_schema(workspace):
    connection, cursor, migration, _ = migration_fixture(workspace, sentinel=123)
    with pytest.raises(MigrationError, match="untracked"):
        apply_migrations(connection, workspace, [migration])
    assert not any("INSERT INTO dbo.itops_deployment_migrations" in sql for sql, _ in cursor.commands)


def test_migration_failure_rolls_back_and_does_not_ledger(workspace):
    connection, cursor, migration, _ = migration_fixture(workspace, fail=True)
    with pytest.raises(RuntimeError):
        apply_migrations(connection, workspace, [migration])
    assert any(sql == "IF @@TRANCOUNT>0 ROLLBACK TRANSACTION" for sql, _ in cursor.commands)
    assert not any("INSERT INTO dbo.itops_deployment_migrations" in sql for sql, _ in cursor.commands)


def test_sql_sid_is_object_uuid_in_sql_byte_order():
    sql = sql_identity_statement("itops_worker", "00112233-4455-6677-8899-aabbccddeeff")
    assert "SID=0x33221100554477668899aabbccddeeff, TYPE=E" in sql
    assert "FROM EXTERNAL PROVIDER" not in sql
    with pytest.raises(MigrationError):
        sql_identity_statement("db_owner", str(uuid4()))


def test_actual_sql_profiles_have_no_approval_authority_for_worker_or_publisher():
    grants = json.loads((ROOT / "deployment" / "sql-grants.json").read_text())
    assert set(grants) == {"review", "worker", "tools", "publisher"}
    assert all(permissions == ["SELECT"] for obj, permissions in grants["worker"].items()
               if obj.startswith("review."))
    assert all(permissions == ["SELECT"] for permissions in grants["tools"].values())
    for table in ("incidents", "revisions", "publications"):
        assert "INSERT" not in grants["publisher"][f"review.{table}"]
    assert "review.receipts" not in grants["publisher"]
    assert all("DELETE" not in permissions for profile in grants.values() for permissions in profile.values())
    cursor = Cursor()
    apply_grants(Mock(cursor=Mock(return_value=cursor)), {key: str(uuid4()) for key in grants}, grants)
    assert any("Runtime authority triggers" in sql for sql, _ in cursor.commands)
    assert any("consumer_application_runs_immutable" in sql and "<>9" in sql for sql, _ in cursor.commands)
    assert any("GRANT SELECT,INSERT ON OBJECT::[dbo].[consumer_jobs] TO [itops_review]" in sql
               for sql, _ in cursor.commands)


def test_sql_boundary_preserves_monotonic_revocation_and_output_compatibility():
    text = (ROOT / "deployment" / "sql" / "001_runtime_boundaries.sql").read_text()
    assert len(split_batches(text)) == 5
    assert "USER_NAME() = N'itops_publisher'" in text
    assert "IF UPDATE(eligible_until)" in text
    assert "Publisher cannot approve or reactivate a publication" in text
    assert "Only review authority can enqueue a publication upload" in text
    assert "status COLLATE Latin1_General_100_BIN2 <> 'pending'" in text
    assert "NOT IN ('leased','failed','processed','cancelled')" in text
    assert "DATALENGTH(kind)<>LEN(kind)" in text
    assert "DATALENGTH(status)<>DATALENGTH('pending')" in text
    assert "DATALENGTH(i.review_state)<>DATALENGTH('needs_evidence')" in text
    assert "IF UPDATE(outbox_id) OR UPDATE(kind) OR UPDATE(payload_json)" in text
    assert "ON review.publisher_jobs" not in text  # Existing fenced UPDATE uses OUTPUT without INTO.


def test_bootstrap_package_contains_exact_migrations_and_grants():
    files = sources(ROOT, "bootstrap")
    assert "deployment/sql-grants.json" in files
    for migration in MIGRATIONS:
        assert migration.path in files
    assert b"azure-keyvault-secrets==4.10.0" in files["requirements.txt"]
    assert b"azure-ai-projects" not in files["requirements.txt"]
    assert not any(path.startswith("agentic_itops/") and path.endswith(".py") for path in files)


@pytest.mark.parametrize("objects", [{"dbo.x": ["DELETE"]}, {"dbo.x;DROP TABLE x": ["SELECT"]},
                                     {"*": ["SELECT"]}, {"dbo.x": ["CONTROL"]}])
def test_sql_grants_reject_broad_or_injected_permissions(objects):
    identities = {name: str(uuid4()) for name in ("review", "worker", "tools", "publisher")}
    grants = {name: objects for name in identities}
    with pytest.raises(MigrationError):
        apply_grants(Mock(), identities, grants)


@pytest.mark.parametrize("query", ["sys.database_role_members", "FROM sys.schemas", "FROM sys.objects"])
def test_existing_runtime_authority_aborts_grants(query):
    class ExistingAuthority(Cursor):
        def execute(self, sql, *parameters):
            super().execute(sql, *parameters)
            if query in sql:
                self.result = (1,)
            return self
    cursor = ExistingAuthority()
    identities = {name: str(uuid4()) for name in ("review", "worker", "tools", "publisher")}
    with pytest.raises(MigrationError, match="role membership|owns"):
        apply_grants(Mock(cursor=Mock(return_value=cursor)), identities,
                     {name: {"dbo.consumer_jobs": ["SELECT"]} for name in identities})
    assert cursor.commands[-1][0] == "IF @@TRANCOUNT>0 ROLLBACK TRANSACTION"


def test_database_control_permission_aborts_grants():
    cursor = Cursor()
    cursor.fetchall = lambda: [("itops_worker", 0, "G", "CONTROL", None, None, 0)]
    identities = {name: str(uuid4()) for name in ("review", "worker", "tools", "publisher")}
    with pytest.raises(MigrationError, match="unexpected explicit"):
        apply_grants(Mock(cursor=Mock(return_value=cursor)), identities,
                     {name: {"dbo.consumer_jobs": ["SELECT"]} for name in identities})


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "8.8.8.8", "::1", "100.64.0.1"])
def test_private_dns_rejects_non_vnet_addresses(ip):
    resolver = lambda *_args, **_kwargs: [(socket.AF_INET, 1, 6, "", (ip, 443))]
    with pytest.raises(BootstrapError):
        private_dns("owned.blob.core.windows.net", resolver=resolver)


def test_private_dns_accepts_rfc1918():
    resolver = lambda *_args, **_kwargs: [(socket.AF_INET, 1, 6, "", ("10.93.2.4", 443))]
    assert private_dns("owned.blob.core.windows.net", resolver=resolver) == ["10.93.2.4"]


def test_bootstrap_requires_exact_authorization_and_owned_hosts(workspace):
    value = manifest()
    path = workspace / "manifest.json"
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    env = environment(value, raw)
    assert load_manifest(path, env) == value
    for key, replacement in [("ITOPS_BOOTSTRAP_AUTHORIZED", "false"),
                             ("ITOPS_BOOTSTRAP_MANIFEST_SHA256", "0" * 64),
                             ("AZURE_CLIENT_ID", str(uuid4()))]:
        with pytest.raises(BootstrapError):
            load_manifest(path, {**env, key: replacement})
    value["private_hosts"].append("other.database.windows.net")
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    with pytest.raises(BootstrapError, match="all and only"):
        load_manifest(path, environment(value, raw))


def test_bootstrap_expired_approval_fails(workspace):
    value = manifest()
    value["expires_at"] = "2020-01-01T00:00:00Z"
    raw = json.dumps(value).encode()
    path = workspace / "manifest.json"
    path.write_bytes(raw)
    with pytest.raises(BootstrapError, match="expired"):
        load_manifest(path, environment(value, raw))


def test_packages_are_byte_deterministic_and_linux_safe(workspace):
    files = sources(ROOT, "review")
    one, two = workspace / "one.zip", workspace / "two.zip"
    assert write_zip(one, files)["sha256"] == write_zip(two, files)["sha256"]
    with ZipFile(one) as archive:
        assert "agentic_itops/hosting.py" in archive.namelist()
        assert "agentic_itops/service_projection.py" in archive.namelist()
        for migration in MIGRATIONS:
            if migration.path.startswith("agentic_itops/"):
                assert migration.path in archive.namelist()
        assert "deployment/sql/001_runtime_boundaries.sql" not in archive.namelist()
        assert "requirements.txt" in archive.namelist()
        assert all("\\" not in name and not name.startswith(".") for name in archive.namelist())
        assert all(part not in name for name in archive.namelist() for part in
                   (".azure", ".venv", "__pycache__", "local.settings", ".build", "tests/"))
        assert b"pytest" not in archive.read("requirements.txt")
        assert b"gunicorn" in archive.read("requirements.txt")


@pytest.mark.parametrize("name", ["../secret", ".azure/token", "a\\b", "/root/file", "C:/Windows/file"])
def test_zip_member_traversal_rejected(workspace, name):
    with pytest.raises(ValueError):
        write_zip(workspace / "bad.zip", {name: b"x"})


def test_requirement_profile_does_not_accept_private_urls(workspace):
    (workspace / "requirements.txt").write_text("https://private.example/package.whl\n")
    with pytest.raises(ValueError):
        requirements(workspace, "requirements.txt")


def test_staging_rejects_extra_capture(workspace):
    (workspace / "token.json").write_text("{}")
    with pytest.raises(ValueError, match="stale"):
        stage(workspace, {"requirements.txt": b""})


def test_runtime_artifacts_require_named_explicit_inputs(workspace):
    artifact = workspace / "config.json"
    artifact.write_text("{}")
    files = {}
    add_artifacts(files, [f"runtime.json={artifact}"])
    assert files == {".runtime/runtime.json": b"{}"}
    with pytest.raises(ValueError):
        add_artifacts({}, [f"token.json={artifact}"])


def test_onedeploy_is_private_bearer_remote_build_and_receipt_bound(workspace, monkeypatch):
    monkeypatch.setattr("deployment.bootstrap.private_dns", lambda _host: ["10.93.2.4"])
    payload = b"reviewed zip"
    (workspace / "worker.zip").write_bytes(payload)
    host = "func-worker-gcjgvzpp44b4i.scm.azurewebsites.net"
    package = {"service": "worker", "file": "worker.zip", "sha256": sha256(payload).hexdigest(), "scm_host": host}
    submit = Mock(status_code=202, headers={"Location": f"https://{host}/api/deployments/receipt123"})
    status = Mock(status_code=200, json=Mock(return_value={"id": "receipt123", "status": 4}))
    client = Mock(post=Mock(return_value=submit), get=Mock(return_value=status))
    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    journal = Journal(workspace / "journal.json")
    result = deploy_function(package, workspace, credential, client, journal, wait=lambda _: None)
    assert result["state"] == "complete"
    assert "/api/publish?RemoteBuild=true" in client.post.call_args.args[0]
    assert "Bearer " in client.post.call_args.kwargs["headers"]["Authorization"]
    assert client.get.call_args.args[0].endswith("/receipt123")
    deploy_function(package, workspace, credential, client, journal)
    assert client.post.call_count == 1


def test_onedeploy_does_not_repeat_ambiguous_submission(workspace, monkeypatch):
    monkeypatch.setattr("deployment.bootstrap.private_dns", lambda _host: ["10.93.2.4"])
    (workspace / "worker.zip").write_bytes(b"x")
    package = {"service": "worker", "file": "worker.zip", "sha256": sha256(b"x").hexdigest(), "scm_host": "owned"}
    journal = Journal(workspace / "journal.json")
    journal.save("deploy-worker", {"state": "intent", "sha256": package["sha256"]})
    client = Mock()
    with pytest.raises(BootstrapError, match="uncertain"):
        deploy_function(package, workspace, Mock(), client, journal)
    client.post.assert_not_called()


def test_operator_dry_run_plan_binds_zip_and_original_admin(workspace):
    value = manifest()
    raw = json.dumps(value).encode()
    path, package = workspace / "manifest.json", workspace / "bootstrap.zip"
    path.write_bytes(raw)
    write_zip(package, {"bootstrap-manifest.json": raw})
    original = str(uuid4())
    plan = approval_plan(path, package, str(uuid4()), "owned-rg", value["bootstrap_object_id"], original)
    assert plan["restore_sql_admin_object_id"] == original
    assert plan["package_sha256"] == sha256(package.read_bytes()).hexdigest()
    assert "temporary-sql-admin" in plan["operations"]


@pytest.mark.parametrize("failure", [
    "upload", "bootstrap", "prepare", "always-off", "prepared-bootstrap", "prepared-running", "prepared-armed",
])
def test_operator_elevates_only_after_upload_and_restores_on_failure(workspace, failure):
    value = manifest()
    original, bootstrap_oid = str(uuid4()), str(uuid4())
    props = {"sid": original, "login": "operator", "tenantId": str(uuid4()),
             "administratorType": "ActiveDirectory", "azureADOnlyAuthentication": True}
    plan = {"bootstrap_site_id": "/owned/site", "sql_admin_id": "/owned/sql/admin",
            "temporary_sql_admin_object_id": bootstrap_oid, "restore_sql_admin_object_id": original,
            "manifest_sha256": "0" * 64, "package_sha256": sha256(b"zip").hexdigest()}
    site = {"properties": {"virtualNetworkSubnetId": "/owned/subnets/web-integration", "state": "Stopped"},
            "identity": {"userAssignedIdentities": {
        "/owned/id": {"principalId": bootstrap_oid, "clientId": value["bootstrap_client_id"]}
    }}}
    operator = Mock()
    def request(method, url, **kwargs):
        if "/config/appsettings/list" in url:
            return Mock(json=Mock(return_value={"properties": {
                "WEBSITES_ENABLE_APP_SERVICE_STORAGE": "true",
                "ITOPS_BOOTSTRAP_AUTHORIZED": "true" if failure == "prepared-armed" else "false",
                **{setting: value[key] for key, setting in {
                    "bootstrap_client_id": "AZURE_CLIENT_ID", "sql_server": "ITOPS_SQL_SERVER",
                    "sql_database": "ITOPS_SQL_DATABASE", "vault_url": "ITOPS_KEY_VAULT_URL",
                    "search_endpoint": "ITOPS_SEARCH_ENDPOINT",
                }.items()},
            }}))
        if "/api/zipdeploy" in url:
            upload = Journal(journal.path).data["upload"]
            assert kwargs["headers"]["SCM-DEPLOYMENT-ID"] == upload["deployment_id"]
            assert upload["state"] == "intent"
            if failure == "upload":
                raise BootstrapError("simulated upload failure")
            return Mock(headers={"Location": "/api/deployments/latest?deployer=" + upload["deployer"]
                                 + "&time=2026-09-15_20-35-00Z"})
        if method == "GET":
            if "/api/deployments/" in url:
                upload = Journal(journal.path).data["upload"]
                assert url == upload["status_url"] and "/latest" not in url
                return Mock(status_code=200, json=Mock(return_value={
                    "status": 4, "id": upload["deployment_id"], "deployer": upload["deployer"],
                }))
            if "/api/vfs/" in url:
                attempt = operator.settings.call_args.args[1]["ITOPS_BOOTSTRAP_ATTEMPT_ID"]
                return Mock(status_code=200, json=Mock(return_value={
                    "manifest": {"sha256": plan["manifest_sha256"]}, "attempt": {"id": attempt},
                    "bootstrap": {"state": "failed"},
                }))
            if "/config/web" in url:
                return Mock(json=Mock(return_value={"properties": {
                    "alwaysOn": failure != "always-off",
                    "ipSecurityRestrictionsDefaultAction": "Deny", "ipSecurityRestrictions": [],
                    "scmIpSecurityRestrictionsUseMain": False,
                }}))
            if "/basicPublishingCredentialsPolicies/" in url:
                return Mock(json=Mock(return_value={"properties": {"allow": False}}))
            return Mock(json=Mock(return_value=site if "/owned/site" in url else {"properties": props}))
        return Mock()
    operator.request.side_effect = request
    package = workspace / "bootstrap.zip"
    package.write_bytes(b"zip")
    journal = Journal(workspace / "operator.json")
    if failure.startswith("prepared-"):
        execute(plan, value, package, operator, journal, prepare_only=True)
        operator.request.reset_mock()
        if failure == "prepared-running":
            site["properties"]["state"] = "Running"
    if failure == "prepare":
        for _ in range(2):
            assert execute(plan, value, package, operator, Journal(journal.path), prepare_only=True) == {
                "state": "prepared", "bootstrap_stopped": True, "data_operations_executed": False,
            }
            journal = Journal(journal.path)
        assert sum("/api/zipdeploy" in call.args[1] for call in operator.request.call_args_list) == 1
        assert sum("/start?" in call.args[1] for call in operator.request.call_args_list) == 1
    else:
        with pytest.raises(BootstrapError, match="upload failure|bootstrap failed|Always On|stopped and disarmed"):
            execute(plan, value, package, operator, journal)
    if failure == "prepared-bootstrap":
        assert sum("/start?" in call.args[1] for call in operator.request.call_args_list) == 1
        assert not any("/api/zipdeploy" in call.args[1] for call in operator.request.call_args_list)
    if failure in ("upload", "prepare", "always-off", "prepared-running", "prepared-armed"):
        operator.set_admin.assert_not_called()
    else:
        assert operator.set_admin.call_args_list[0].args[1]["sid"] == bootstrap_oid
        assert operator.set_admin.call_args_list[-1].args[1]["sid"] == original
        assert journal.data["admin-window"]["state"] == "restored"
    if failure == "always-off":
        operator.settings.assert_not_called()
        assert not any(call.args[0] == "POST" and "/api/zipdeploy" in call.args[1]
                       for call in operator.request.call_args_list)
    else:
        assert "ITOPS_REVIEW_ENTRA_CLIENT_SECRET" in operator.settings.call_args.kwargs["remove"]


@pytest.mark.parametrize("authorized", [False, True])
def test_bootstrap_health_host_survives_disarmed_or_invalid_authorization(monkeypatch, capsys, authorized):
    import deployment.bootstrap as bootstrap
    monkeypatch.setenv("ITOPS_BOOTSTRAP_AUTHORIZED", "true" if authorized else "false")
    monkeypatch.setattr(bootstrap, "ThreadingHTTPServer", Mock())
    monkeypatch.setattr(bootstrap.threading, "Thread", Mock())
    run = Mock(side_effect=BootstrapError("test authorization gate"))
    load = Mock(side_effect=BootstrapError("test authorization gate"))
    monkeypatch.setattr(bootstrap, "run", run)
    monkeypatch.setattr(bootstrap, "load_manifest", load)
    monkeypatch.setattr(bootstrap.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        bootstrap.main()
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == ("failed" if authorized else "disarmed")
    if authorized:
        run.assert_called_once()
    else:
        run.assert_not_called()
        load.assert_not_called()


@pytest.mark.parametrize("absolute_location", [True, False])
def test_kudulite_actual_latest_location_uses_prejournaled_exact_receipt(workspace, absolute_location):
    import httpx
    scm = "https://app-bootstrap-gcjgvzpp44b4i.scm.azurewebsites.net"
    package = workspace / "bootstrap.zip"
    package.write_bytes(b"approved bootstrap bytes")
    digest = sha256(package.read_bytes()).hexdigest()
    journal = Journal(workspace / "upload.json")
    gets = []
    def transport(request):
        persisted = Journal(journal.path).data["upload"]
        if request.method == "POST":
            assert persisted["state"] == "intent"
            assert request.headers["SCM-DEPLOYMENT-ID"] == persisted["deployment_id"]
            assert request.url.params["deployer"] == persisted["deployer"]
            assert request.content == package.read_bytes()
            location = "/api/deployments/latest?deployer=" + persisted["deployer"] + "&time=2026-09-15_20-35-00Z"
            return httpx.Response(202, headers={"Location": (scm if absolute_location else "") + location})
        if request.url.path == "/api/deployments":
            return httpx.Response(200, json=[])
        assert str(request.url) == persisted["status_url"]
        assert request.url.path.endswith("/" + persisted["deployment_id"])
        assert not request.url.query and "/latest" not in request.url.path
        gets.append(request.url.path)
        if len(gets) < 3:
            return httpx.Response(202 if len(gets) == 1 else 404)
        return httpx.Response(200, json={
            "id": persisted["deployment_id"], "deployer": persisted["deployer"],
            "status": 4, "complete": True, "active": True,
        })
    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = upload_bootstrap(package, digest, scm, Operator(credential, client), journal,
                                  max_polls=3, wait=lambda _: None)
    assert result["state"] == "complete"
    assert len(gets) == 3


@pytest.mark.parametrize("wrong_field", ["id", "deployer", "missing-id"])
def test_bootstrap_upload_rejects_uncorrelated_success(workspace, wrong_field):
    scm = "https://app-bootstrap-gcjgvzpp44b4i.scm.azurewebsites.net"
    path = workspace / "bootstrap.zip"
    path.write_bytes(b"x")
    journal = Journal(workspace / "upload.json")
    operator = Mock()
    def request(method, url, **kwargs):
        if method == "POST":
            return Mock(headers={"Location": "/api/deployments/latest?deployer=other&time=2026-09-15_20-35-00Z"})
        record = journal.data["upload"]
        result = {"status": 4, "id": record["deployment_id"], "deployer": record["deployer"]}
        if wrong_field == "missing-id":
            result.pop("id")
        else:
            result[wrong_field] = "unrelated"
        return Mock(status_code=200, json=Mock(return_value=result))
    operator.request.side_effect = request
    for _ in range(2):
        with pytest.raises(BootstrapError, match="request correlation"):
            upload_bootstrap(path, sha256(b"x").hexdigest(), scm, operator, journal, max_polls=1)
    assert journal.data["upload"]["state"] != "complete"
    assert sum(call.args[0] == "POST" for call in operator.request.call_args_list) == 1


@pytest.mark.parametrize("receipt_exists", [True, False])
def test_bootstrap_timeout_recovery_polls_without_reposting(workspace, receipt_exists):
    import httpx
    scm = "https://app-bootstrap-gcjgvzpp44b4i.scm.azurewebsites.net"
    path = workspace / "bootstrap.zip"
    path.write_bytes(b"x")
    journal = Journal(workspace / "upload.json")
    calls = []
    def transport(request):
        calls.append(request.method)
        persisted = Journal(journal.path).data["upload"]
        if request.method == "POST":
            assert persisted["state"] == "intent"
            assert request.headers["SCM-DEPLOYMENT-ID"] == persisted["deployment_id"]
            raise httpx.ReadTimeout("simulated lost accepted response", request=request)
        if request.url.path == "/api/deployments":
            return httpx.Response(200, json=[])
        assert str(request.url) == persisted["status_url"]
        if not receipt_exists:
            return httpx.Response(404)
        return httpx.Response(200, json={
            "id": persisted["deployment_id"], "deployer": persisted["deployer"], "status": 4,
        })
    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        operator = Operator(credential, client)
        with pytest.raises(httpx.ReadTimeout):
            upload_bootstrap(path, sha256(b"x").hexdigest(), scm, operator, journal)
        saved_id = Journal(journal.path).data["upload"]["deployment_id"]
        recovered = Journal(journal.path)
        if receipt_exists:
            assert upload_bootstrap(path, sha256(b"x").hexdigest(), scm, operator, recovered,
                                    max_polls=1, wait=lambda _: None)["state"] == "complete"
        else:
            with pytest.raises(BootstrapError, match="never blindly resubmit"):
                upload_bootstrap(path, sha256(b"x").hexdigest(), scm, operator, recovered,
                                 max_polls=2, wait=lambda _: None)
        assert recovered.data["upload"]["deployment_id"] == saved_id
    assert calls.count("POST") == 1


@pytest.mark.parametrize("lose_response", [False, True])
def test_bootstrap_reconciles_server_assigned_id_without_reupload(workspace, lose_response):
    import httpx
    scm = "https://app-bootstrap-gcjgvzpp44b4i.scm.azurewebsites.net"
    path = workspace / "bootstrap.zip"
    path.write_bytes(b"approved")
    digest = sha256(path.read_bytes()).hexdigest()
    journal = Journal(workspace / "upload.json")
    server_id = str(uuid4())
    posts, exact_reads = [], []

    def transport(request):
        persisted = Journal(journal.path).data["upload"]
        if request.method == "POST":
            posts.append(request)
            assert persisted["state"] == "intent"
            if lose_response:
                raise httpx.ReadTimeout("accepted but response lost", request=request)
            return httpx.Response(202, headers={"Location": "/api/deployments/latest?deployer=ignored"})
        if request.url.path == "/api/deployments":
            return httpx.Response(200, json=[
                {"id": str(uuid4()), "deployer": "another-request", "status": 4},
                {"id": "temp-71ab1e73", "deployer": persisted["deployer"], "status": 0, "complete": False},
                {"id": server_id, "deployer": persisted["deployer"], "status": 4},
            ])
        if request.url.path.endswith("/" + persisted["deployment_id"]):
            return httpx.Response(404)
        assert request.url.path == "/api/deployments/" + server_id
        assert persisted["provider_deployment_id"] == server_id
        exact_reads.append(request)
        return httpx.Response(200, json={"id": server_id, "deployer": persisted["deployer"], "status": 4})

    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        operator = Operator(credential, client)
        if lose_response:
            with pytest.raises(httpx.ReadTimeout):
                upload_bootstrap(path, digest, scm, operator, journal)
        for _ in range(2):
            result = upload_bootstrap(path, digest, scm, operator, Journal(journal.path),
                                      max_polls=2, wait=lambda _: None)
            assert result["state"] == "complete"
            assert result["provider_deployment_id"] == server_id
    assert len(posts) == 1 and len(exact_reads) == 2


@pytest.mark.parametrize("case", ["unrelated", "duplicate", "invalid-id", "receipt-mismatch",
                                "temporary-only", "temporary-success"])
def test_bootstrap_reconciliation_never_accepts_ambiguous_or_unrelated_success(workspace, case):
    import httpx
    scm = "https://app-bootstrap-gcjgvzpp44b4i.scm.azurewebsites.net"
    path = workspace / "bootstrap.zip"
    path.write_bytes(b"approved")
    journal = Journal(workspace / "upload.json")
    server_id = str(uuid4())
    posts = []

    def transport(request):
        persisted = Journal(journal.path).data["upload"]
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(202)
        if request.url.path == "/api/deployments":
            row = {"id": server_id, "deployer": persisted["deployer"], "status": 4}
            if case == "unrelated":
                row["deployer"] = "someone-else"
            if case == "invalid-id":
                row["id"] = "../latest"
            if case.startswith("temporary-"):
                row.update(id="temp-71ab1e73", status=0 if case == "temporary-only" else 4,
                           complete=case == "temporary-success")
            return httpx.Response(200, json=[row, dict(row)] if case == "duplicate" else [row])
        if request.url.path.endswith("/" + persisted["deployment_id"]):
            return httpx.Response(404)
        return httpx.Response(200, json={"id": server_id, "deployer": "someone-else", "status": 4})

    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(BootstrapError):
            upload_bootstrap(path, sha256(path.read_bytes()).hexdigest(), scm,
                             Operator(credential, client), journal, max_polls=2, wait=lambda _: None)
    assert journal.data["upload"]["state"] != "complete" and len(posts) == 1


def test_bootstrap_upload_rejects_changed_approved_bytes_without_network(workspace):
    path = workspace / "changed.zip"
    path.write_bytes(b"changed")
    operator = Mock()
    with pytest.raises(BootstrapError, match="changed after approval"):
        upload_bootstrap(path, sha256(b"approved").hexdigest(),
                         "https://app-bootstrap-gcjgvzpp44b4i.scm.azurewebsites.net",
                         operator, Journal(workspace / "upload.json"))
    operator.request.assert_not_called()


def test_operator_never_repeats_ambiguous_upload(workspace):
    journal = Journal(workspace / "operator.json")
    journal.save("upload", {"state": "intent"})
    operator = Mock()
    with pytest.raises(BootstrapError, match="uncertain"):
        execute({"bootstrap_site_id": "unused", "sql_admin_id": "unused"}, manifest(),
                workspace / "unused.zip", operator, journal)
    operator.request.assert_not_called()


def test_recovery_disarms_even_when_administrator_restoration_fails(workspace):
    original, bootstrap_oid = str(uuid4()), str(uuid4())
    journal = Journal(workspace / "recovery.json")
    journal.save("admin-window", {
        "state": "intent", "resource_id": "/approved/admin", "original": {"sid": original},
        "approval": {"restore_sql_admin_object_id": original, "temporary_sql_admin_object_id": bootstrap_oid,
                     "sql_admin_id": "/approved/admin", "bootstrap_site_id": "/approved/site"},
    })
    operator = Mock()
    operator.request.return_value.json.return_value = {"properties": {"sid": bootstrap_oid}}
    operator.set_admin.side_effect = BootstrapError("simulated administrator failure")
    with pytest.raises(BootstrapError, match="administrator failure"):
        restore(operator, journal, original)
    assert journal.data["admin-window"]["state"] == "intent"
    assert operator.settings.call_args.args[1] == {"ITOPS_BOOTSTRAP_AUTHORIZED": "false"}
    assert "ITOPS_REVIEW_ENTRA_CLIENT_SECRET" in operator.settings.call_args.kwargs["remove"]


def test_deploy_only_approval_does_not_elevate_sql(workspace):
    value = manifest()
    value["operations"] = ["deploy"]
    path, package = workspace / "manifest.json", workspace / "bootstrap.zip"
    path.write_bytes(json.dumps(value).encode())
    write_zip(package, {"bootstrap-manifest.json": path.read_bytes()})
    plan = approval_plan(path, package, str(uuid4()), "owned-rg", value["bootstrap_object_id"], str(uuid4()))
    assert "temporary-sql-admin" not in plan["operations"]
    assert "restore-sql-admin" not in plan["operations"]


@pytest.mark.parametrize("sid_kind", ["client", "object", "unrelated"])
def test_sql_bootstrap_verifies_native_application_client_sid_before_creating_users(monkeypatch, sid_kind):
    from uuid import UUID
    value = manifest()
    actual = (UUID(value[f"bootstrap_{sid_kind}_id"]).bytes_le
              if sid_kind in {"client", "object"} else uuid4().bytes_le)
    cursor = Mock(fetchone=Mock(return_value=(actual,)))
    connection = Mock(cursor=Mock(return_value=cursor))
    monkeypatch.setattr("ctypes.CDLL", Mock())
    monkeypatch.setattr("mssql_python.get_native_provider_info", lambda: {"driver_path": "verified-test-driver"})
    monkeypatch.setattr("mssql_python.pooling", Mock())
    monkeypatch.setattr("mssql_python.connect", Mock(return_value=connection))
    if sid_kind == "client":
        assert _sql_connection(value) is connection
    else:
        with pytest.raises(BootstrapError, match="approved application client SID"):
            _sql_connection(value)
        connection.close.assert_called_once()
    cursor.execute.assert_called_once_with("SELECT SUSER_SID()")


@pytest.mark.parametrize("mutation", ["none", "missing", "duplicate", "object-ids", "zero"])
def test_grant_manifest_requires_separate_application_client_ids(workspace, mutation):
    value = manifest()
    value["operations"] = ["grants"]
    clients = {service: str(uuid4()) for service in value["runtime_object_ids"]}
    value["runtime_client_ids"] = clients
    if mutation == "missing":
        del value["runtime_client_ids"]
    elif mutation == "duplicate":
        clients["worker"] = clients["review"]
    elif mutation == "object-ids":
        value["runtime_client_ids"] = value["runtime_object_ids"]
    elif mutation == "zero":
        clients["worker"] = "00000000-0000-0000-0000-000000000000"
    raw = json.dumps(value).encode()
    path = workspace / "grant-manifest.json"
    path.write_bytes(raw)
    if mutation == "none":
        assert load_manifest(path, environment(value, raw))["runtime_client_ids"] == clients
    else:
        with pytest.raises(BootstrapError):
            load_manifest(path, environment(value, raw))


@pytest.mark.parametrize("mismatch", [None, "principalId", "clientId"])
def test_grant_targets_are_verified_against_actual_uami_pairs(mismatch):
    from deployment.control_plane import verify_runtime_identities
    value = manifest()
    value["runtime_client_ids"] = {service: str(uuid4()) for service in value["runtime_object_ids"]}
    plan = {"bootstrap_site_id": "/owned/providers/Microsoft.Web/sites/" + value["bootstrap_site"]}
    operator = Mock()
    def request(method, url):
        assert method == "GET"
        service = url.split("/id-", 1)[1].split("-", 1)[0]
        properties = {"principalId": value["runtime_object_ids"][service],
                      "clientId": value["runtime_client_ids"][service]}
        if mismatch:
            properties[mismatch] = str(uuid4())
        return Mock(json=Mock(return_value={"properties": properties}))
    operator.request.side_effect = request
    if mismatch:
        with pytest.raises(BootstrapError, match="object/client pair"):
            verify_runtime_identities(plan, value, operator)
    else:
        verify_runtime_identities(plan, value, operator)
        assert operator.request.call_count == 4


def test_sql_failure_diagnostics_do_not_copy_provider_payloads():
    from deployment.migrate import sql_failure_metadata
    error = RuntimeError("must-not-be-logged")
    error.driver_error = "SQLState=42000 NativeError=102"
    error.ddbc_error = "Incorrect syntax near 'source_column'. token=secret-value"
    result = sql_failure_metadata(error)
    assert result["categories"] == ["syntax"]
    assert result["sql_states"] == ["42000"] and result["native_codes"] == ["102"]
    assert result["source_token_sha256"] == [sha256(b"source_column").hexdigest()]
    assert all(value not in json.dumps(result) for value in ("source_column", "secret-value", "must-not-be-logged"))


def test_application_cross_column_check_is_a_table_constraint():
    text = (ROOT / "agentic_itops" / "consumer" / "runtime_sql" / "application.sql").read_text()
    assert "projected bit NOT NULL," in text
    assert "CONSTRAINT CK_consumer_application_result CHECK (projected=0 OR result_json IS NOT NULL)" in text
    assert "projected bit NOT NULL CHECK" not in text


def test_search_existing_schema_accepts_defaults_but_rejects_changed_contract():
    value = manifest()
    value["search_index"] = {"name": "agentic-itops-knowledge", "fields": [{"name": "id", "key": True}]}
    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    client = Mock()
    client.get.return_value = Mock(status_code=200, json=Mock(return_value={
        "name": "agentic-itops-knowledge", "@odata.etag": "example",
        "fields": [{"name": "id", "key": True, "searchable": False}],
    }))
    assert initialize_search(value, credential, client)["state"] == "complete"
    client.put.assert_not_called()
    client.get.return_value.json.return_value["fields"][0]["key"] = False
    with pytest.raises(BootstrapError, match="differs"):
        initialize_search(value, credential, client)
    client.put.assert_not_called()


def test_vault_initialization_is_idempotent_without_secret_output(monkeypatch):
    from azure.core.exceptions import ResourceNotFoundError
    stored = {}
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    def get_secret(name):
        if name not in stored:
            raise ResourceNotFoundError()
        return Mock(value=stored[name])
    client.get_secret.side_effect = get_secret
    client.set_secret.side_effect = lambda name, value: stored.update({name: value})
    monkeypatch.setattr("azure.keyvault.secrets.SecretClient", Mock(return_value=client))
    monkeypatch.setenv("ITOPS_REVIEW_ENTRA_CLIENT_SECRET", "fictional-credential-for-unit-test")
    first = initialize_secrets(manifest(), Mock())
    second = initialize_secrets(manifest(), Mock())
    assert first == second
    assert client.set_secret.call_count == 3
    assert len(stored["review-session-key"]) >= 32
    assert all(value not in json.dumps(first) for value in stored.values())


@pytest.mark.parametrize("service", ["worker", "tools"])
def test_release_bundle_requires_named_runtime_artifacts(workspace, service):
    files = sources(ROOT, service)
    path = workspace / f"{service}.zip"
    write_zip(path, files)
    with pytest.raises(ValueError, match="runtime artifacts"):
        validate_function_zip(path.read_bytes(), service)
    files.update({f".runtime/{name}": b"{}" for name in
                  ("runtime.json", "bindings.json", "registration-plan.json", "registration-receipt.json")})
    write_zip(path, files)
    validate_function_zip(path.read_bytes(), service)


def test_runtime_artifact_rejects_nested_credentials(workspace):
    path = workspace / "credential.json"
    path.write_text(json.dumps({"provider": {"client_secret": "fictional"}}))
    with pytest.raises(ValueError, match="credentials"):
        add_artifacts({}, [f"runtime.json={path}"])


@pytest.mark.parametrize("matching_hostnames", [True, False])
def test_manifest_requires_actual_hostname_readback(workspace, matching_hostnames):
    value = manifest()
    suffix = value["bootstrap_site"].removeprefix("app-bootstrap-")
    hostnames = {
        name: f"{'app' if name in ('review', 'bootstrap') else 'func'}-{name}-{suffix}.azurewebsites.net"
        for name in ("review", "bootstrap", "worker", "tools", "publisher")
    }
    if not matching_hostnames:
        hostnames["review"] = f"app-review-{suffix}-generated.centralus-01.azurewebsites.net"
    outputs = {key: {"value": item} for key, item in {
        "SERVICE_BOOTSTRAP_NAME": value["bootstrap_site"], "ITOPS_SERVICE_HOSTNAMES": hostnames,
        "AZURE_KEY_VAULT_NAME": f"kv-itops-{suffix}", "ITOPS_PRIVATE_HOSTNAMES": value["private_hosts"],
        "ITOPS_SERVICE_IDENTITIES": [
            {"service": name, "clientId": str(uuid4()), "principalId": str(uuid4())}
            for name in ("review", "worker", "tools", "publisher", "bootstrap")
        ],
    }.items()}
    args = (outputs, "agentic-itops-us93", value["review_scopes"], {}, workspace,
            value["expires_at"], ["secrets"])
    if matching_hostnames:
        assert create_bootstrap_manifest(*args)["bootstrap_site"] == value["bootstrap_site"]
    else:
        with pytest.raises(ValueError, match="actual Azure hostnames"):
            create_bootstrap_manifest(*args)


@pytest.mark.parametrize("matching_schema", [True, False])
def test_bootstrap_search_manifest_must_match_packaged_source(workspace, matching_schema):
    schema = json.loads((ROOT / SEARCH_SCHEMA_ASSET).read_bytes())
    for service in ("worker", "publisher"):
        assert json.loads(sources(ROOT, service)[SEARCH_SCHEMA_ASSET]) == schema
    value = manifest()
    value["operations"] = ["search"]
    value["search_index"] = schema if matching_schema else {"name": "agentic-itops-knowledge", "fields": []}
    path = workspace / "manifest.json"
    path.write_text(json.dumps(value))
    if matching_schema:
        assert bundle(ROOT, path, workspace, workspace / "bootstrap.zip")["operations"] == ["search"]
    else:
        with pytest.raises(ValueError, match="publisher's source schema"):
            bundle(ROOT, path, workspace, workspace / "bootstrap.zip")


def probe_contract():
    from agentic_itops.access_probe import query_contract
    now = datetime.now(timezone.utc)
    return query_contract(str(uuid4()), str(uuid4()), (now - timedelta(days=2)).isoformat(), now.isoformat())


def test_preflight_package_is_separate_and_does_not_relax_runtime_artifacts(workspace):
    contract = probe_contract()
    files = sources(ROOT, "worker-preflight")
    artifact = workspace / "probe-contract.json"
    artifact.write_text(json.dumps(contract))
    add_artifacts(files, [f"access-probe-contract.json={artifact}"])
    assert ".runtime/access-probe-contract.json" in files
    assert not any(name.startswith(".preflight/") for name in files)
    for dependency in (b"openai", b"mssql", b"azure-ai", b"azure-search", b"azure-storage", b"mcp"):
        assert dependency not in files["requirements.txt"].lower()
    path = workspace / "worker-preflight.zip"
    write_zip(path, files)
    validate_function_zip(path.read_bytes(), "worker-preflight")
    with pytest.raises(ValueError, match="runtime artifacts"):
        validate_function_zip(path.read_bytes(), "worker")
    files["function_app.py"] = (ROOT / "services" / "worker" / "function_app.py").read_bytes()
    write_zip(path, files)
    with pytest.raises(ValueError, match="only the reviewed"):
        validate_function_zip(path.read_bytes(), "worker-preflight")


@pytest.mark.parametrize("extra", [
    ".runtime/runtime.json", ".runtime/registration-receipt.json", ".preflight/access-probe-contract.json",
])
def test_preflight_rejects_model_artifacts_and_standalone_helper_layout(workspace, extra):
    files = sources(ROOT, "worker-preflight")
    files[".runtime/access-probe-contract.json"] = json.dumps(probe_contract()).encode()
    files[extra] = b"{}"
    path = workspace / "invalid-preflight.zip"
    write_zip(path, files)
    with pytest.raises(ValueError, match="must not contain"):
        validate_function_zip(path.read_bytes(), "worker-preflight")


def test_preflight_source_package_indexes_only_one_route_in_isolated_process(workspace):
    files = sources(ROOT, "worker-preflight")
    files[".runtime/access-probe-contract.json"] = json.dumps(probe_contract()).encode()
    stage(workspace, files)
    command = """
import importlib.abc, pathlib, sys
blocked = ('mssql_python', 'openai', 'azure.ai', 'azure.search', 'azure.storage', 'mcp')
class NoRuntimeSdk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ImportError('preflight must not load runtime SDK: ' + fullname)
sys.meta_path.insert(0, NoRuntimeSdk())
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
import function_app, agentic_itops.service_preflight
assert pathlib.Path(agentic_itops.service_preflight.__file__).is_relative_to(root)
assert len(function_app.app.get_functions()) == 1
print('one restricted route indexed')
"""
    result = subprocess.run([sys.executable, "-I", "-c", command, str(workspace)],
                            cwd=workspace, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "one restricted route indexed"


def test_preflight_deployment_manifest_cannot_mix_privileged_work(workspace):
    value = manifest()
    value["operations"] = ["deploy-preflight"]
    value["packages"] = [{
        "service": "worker-preflight", "file": "packages/worker-preflight.zip", "sha256": "a" * 64,
        "scm_host": "func-worker-gcjgvzpp44b4i.scm.azurewebsites.net",
    }]
    path = workspace / "manifest.json"
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    assert load_manifest(path, environment(value, raw)) == value
    for operations in (["deploy-preflight", "migrate"], ["deploy"]):
        value["operations"] = operations
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        with pytest.raises(BootstrapError):
            load_manifest(path, environment(value, raw))


@pytest.mark.parametrize("mutation", [
    "bundle", "flat", "changed-query", "wrong-contract", "extra-envelope", "extra-witness", "expired",
])
def test_controller_reads_the_count_collector_artifact_without_weakening_witness_checks(tmp_path, mutation):
    from agentic_itops.access_probe import query_contract
    from agentic_itops.consumer.contracts import ContractError
    from deployment.control_plane import load_source_witness_artifact

    contract = query_contract(str(uuid4()), str(uuid4()), "2026-09-14T00:00:00Z", "2026-09-16T00:00:00Z")
    value = manifest()
    value["source_probe"] = {"contract_sha256": contract["sha256"]}
    witness = {
        "contract_sha256": contract["sha256"], "observed_at": datetime.now(timezone.utc).isoformat(),
        "observation_rows": 42, "audit_rows": 3, "witness_kind": "operator_count_only", "inference_input": False,
    }
    artifact = {"contract": contract, "witness": witness}
    if mutation == "flat":
        artifact = witness
    elif mutation == "changed-query":
        contract["queries"]["audit"] += "\n| take 0"
    elif mutation == "wrong-contract":
        value["source_probe"]["contract_sha256"] = "b" * 64
    elif mutation == "extra-envelope":
        artifact["labels"] = ["forbidden"]
    elif mutation == "extra-witness":
        witness["labels"] = ["forbidden"]
    elif mutation == "expired":
        witness["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    path = tmp_path / "operator-witness.json"
    path.write_text(json.dumps(artifact))
    if mutation in {"bundle", "flat"}:
        assert load_source_witness_artifact(value, path) == witness
    else:
        with pytest.raises((BootstrapError, ContractError)):
            load_source_witness_artifact(value, path)


def test_completed_source_proof_cannot_be_relabelled_as_a_fresh_attempt(workspace, monkeypatch):
    value = manifest()
    value["operations"] = ["probe-source"]
    value["source_probe"] = {
        "contract_sha256": "a" * 64, "worker_client_id": str(uuid4()), "token_scope": f"api://{uuid4()}/.default",
    }
    raw = json.dumps(value).encode()
    (workspace / "bootstrap-manifest.json").write_bytes(raw)
    for key, data in environment(value, raw).items():
        monkeypatch.setenv(key, data)
    monkeypatch.setenv("ITOPS_BOOTSTRAP_ATTEMPT_ID", str(uuid4()))
    state = workspace / "state"
    journal = Journal(state / f"{value['deployment_id']}.json")
    journal.save("manifest", {"sha256": sha256(raw).hexdigest()})
    journal.save("probe-source", {"state": "complete"})
    journal.save("bootstrap", {"state": "complete"})
    dns = Mock(side_effect=AssertionError("replay must not query the network"))
    monkeypatch.setattr("deployment.bootstrap.private_dns", dns)
    for _ in range(2):
        with pytest.raises(BootstrapError, match="new approval/deployment ID"):
            _run_locked(workspace, state)
    dns.assert_not_called()


@pytest.mark.parametrize("mutation", ["none", "expired", "labels", "wrong-worker", "audit-readable"])
def test_private_source_probe_is_count_only_worker_identity_bound(workspace, monkeypatch, mutation):
    value = manifest()
    value["operations"] = ["probe-source"]
    value["source_probe"] = {
        "contract_sha256": "a" * 64, "worker_client_id": str(uuid4()), "token_scope": f"api://{uuid4()}/.default",
    }
    raw = json.dumps(value).encode()
    path = workspace / "manifest.json"
    path.write_bytes(raw)
    assert load_manifest(path, environment(value, raw)) == value
    witness = {
        "contract_sha256": "a" * 64, "observed_at": datetime.now(timezone.utc).isoformat(),
        "observation_rows": 42, "audit_rows": 3, "witness_kind": "operator_count_only", "inference_input": False,
    }
    result = {
        "contract_sha256": "a" * 64, "verified_at": datetime.now(timezone.utc).isoformat(),
        "observation_rows": 42, "audit_access": "denied", "denial_evidence": "explicit_insufficient_access",
        "independent_audit_rows": 3, "inference_input": False, "execution_allowed": False,
        "status": "verified", "worker_client_id": value["source_probe"]["worker_client_id"],
        "caller_oid": value["bootstrap_object_id"], "probe_kind": "worker_managed_identity_count_only",
    }
    if mutation == "expired":
        witness["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    elif mutation == "labels":
        witness["labels"] = ["forbidden"]
    elif mutation == "wrong-worker":
        result["worker_client_id"] = str(uuid4())
    elif mutation == "audit-readable":
        result["audit_access"] = "allowed"
    monkeypatch.setattr("deployment.bootstrap.private_dns", lambda _host: ["10.93.2.4"])
    client = Mock(post=Mock(return_value=Mock(status_code=200, json=Mock(return_value=result))))
    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    env = {"ITOPS_SOURCE_PROBE_WITNESS_JSON": json.dumps(witness)}
    if mutation == "none":
        assert probe_source(value, credential, client, environment=env)["state"] == "complete"
        assert client.post.call_args.args[0].endswith("/api/preflight/source-access")
        assert client.post.call_args.kwargs["json"] == {"witness": witness}
        credential.get_token.assert_called_once_with(value["source_probe"]["token_scope"])
    else:
        with pytest.raises(BootstrapError):
            probe_source(value, credential, client, environment=env)
        if mutation in ("expired", "labels"):
            client.post.assert_not_called()


@pytest.mark.parametrize("matching_receipt", [True, False])
def test_flex_latest_fallback_is_bound_to_unique_submission(workspace, monkeypatch, matching_receipt):
    monkeypatch.setattr("deployment.bootstrap.private_dns", lambda _host: ["10.93.2.4"])
    path = workspace / "worker.zip"
    path.write_bytes(b"x")
    host = "func-worker-gcjgvzpp44b4i.scm.azurewebsites.net"
    package = {"service": "worker", "file": "worker.zip", "sha256": sha256(b"x").hexdigest(), "scm_host": host}
    submit = Mock(status_code=202, headers={"Location": f"https://{host}/api/deployments/receipt123"})
    lookup = Mock(status_code=404)
    latest = Mock(status_code=200, json=Mock(return_value={
        "id": "receipt123" if matching_receipt else "different", "status": 4,
    }))
    client = Mock(post=Mock(return_value=submit), get=Mock(side_effect=[lookup, latest]))
    credential = Mock(get_token=Mock(return_value=Mock(token="test-token")))
    if matching_receipt:
        assert deploy_function(package, workspace, credential, client, Journal(workspace / "j.json"),
                               max_polls=1, wait=lambda _: None)["state"] == "complete"
    else:
        with pytest.raises(BootstrapError, match="polling budget"):
            deploy_function(package, workspace, credential, client, Journal(workspace / "j.json"),
                            max_polls=1, wait=lambda _: None)
