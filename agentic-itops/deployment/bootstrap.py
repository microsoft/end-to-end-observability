"""Bounded, manifest-authorized VNet bootstrap. No administrative HTTP endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import threading
import time
from urllib.parse import urlparse
from uuid import UUID

from .migrate import MigrationError, apply_grants, apply_migrations, sql_failure_metadata


ROOT = Path(__file__).resolve().parents[1]
STATE_DIRECTORY = Path("/home/data/itops-bootstrap")
OPERATIONS = {"migrate", "grants", "secrets", "review-scopes", "search", "deploy", "deploy-preflight", "probe-source"}


class BootstrapError(RuntimeError):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def private_dns(host: str, *, port: int = 443, resolver=socket.getaddrinfo) -> list[str]:
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise BootstrapError("invalid private hostname")
    addresses = sorted({entry[4][0] for entry in resolver(host, port, type=socket.SOCK_STREAM)})
    allowed = tuple(ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    if not addresses or any(not any(ipaddress.ip_address(ip) in subnet for subnet in allowed) for ip in addresses):
        raise BootstrapError(f"private DNS required: {host}")
    return addresses


def private_tls(host: str) -> list[str]:
    addresses = private_dns(host)
    with socket.create_connection((host, 443), timeout=15) as connection:
        with ssl.create_default_context().wrap_socket(connection, server_hostname=host):
            pass
    return addresses


def load_manifest(path: Path, environment: dict | os._Environ) -> dict:
    raw = path.read_bytes()
    expected = environment.get("ITOPS_BOOTSTRAP_MANIFEST_SHA256", "")
    if (not re.fullmatch("[a-f0-9]{64}", expected) or sha256(raw).hexdigest() != expected
            or environment.get("ITOPS_BOOTSTRAP_AUTHORIZED") != "true"):
        raise BootstrapError("bootstrap authorization/hash gate not satisfied")
    value = json.loads(raw)
    required = {"schema_version", "deployment_id", "expires_at", "bootstrap_client_id", "bootstrap_object_id", "bootstrap_site",
                "sql_server", "sql_database", "vault_url", "search_endpoint", "private_hosts", "operations",
                "runtime_object_ids", "review_scopes", "search_index", "packages"}
    if isinstance(value, dict) and value.get("operations") == ["probe-source"]:
        required.add("source_probe")
    if isinstance(value, dict) and "grants" in value.get("operations", []):
        required.add("runtime_client_ids")
    if isinstance(value, dict) and "review-scopes" in value.get("operations", []):
        required.add("previous_review_scopes_sha256")
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != 1:
        raise BootstrapError("invalid bootstrap manifest fields/version")
    if not UUID(value["deployment_id"]).int:
        raise BootstrapError("invalid deployment ID")
    if not UUID(value["bootstrap_object_id"]).int:
        raise BootstrapError("bootstrap object ID must be explicit")
    expiry = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise BootstrapError("bootstrap approval expired")
    if not value["operations"] or len(set(value["operations"])) != len(value["operations"]) or not set(value["operations"]) <= OPERATIONS:
        raise BootstrapError("explicit unique allowed operations required")
    if "review-scopes" in value["operations"] and (
            "secrets" in value["operations"]
            or not isinstance(value["previous_review_scopes_sha256"], str)
            or not re.fullmatch("[a-f0-9]{64}", value["previous_review_scopes_sha256"])):
        raise BootstrapError("scope-only activation requires a prior catalog hash and cannot rotate credentials")
    for key, env in {
        "bootstrap_client_id": "AZURE_CLIENT_ID", "bootstrap_site": "WEBSITE_SITE_NAME",
        "sql_server": "ITOPS_SQL_SERVER", "sql_database": "ITOPS_SQL_DATABASE",
        "vault_url": "ITOPS_KEY_VAULT_URL", "search_endpoint": "ITOPS_SEARCH_ENDPOINT",
    }.items():
        if value[key] != environment.get(env):
            raise BootstrapError(f"manifest target mismatch: {key}")
    site = re.fullmatch(r"app-bootstrap-([a-z0-9]{13})", value["bootstrap_site"])
    if not site:
        raise BootstrapError("not an owned bootstrap site")
    suffix = site[1]
    if "probe-source" in value["operations"]:
        probe = value.get("source_probe", {})
        if (value["operations"] != ["probe-source"] or value["packages"]
                or set(probe) != {"contract_sha256", "worker_client_id", "token_scope"}
                or not re.fullmatch("[a-f0-9]{64}", probe["contract_sha256"])
                or not UUID(probe["worker_client_id"]).int
                or not re.fullmatch(r"api://[0-9a-fA-F-]{36}/\.default", probe["token_scope"])
                or not UUID(probe["token_scope"].split("/")[2]).int):
            raise BootstrapError("source probe requires isolated exact contract/worker identity/token scope")
    expected_private = {
        value["sql_server"], urlparse(value["vault_url"]).hostname,
        f"stitops{suffix}.blob.core.windows.net",
        *(f"sthost{i}{suffix}.{kind}.core.windows.net" for i in range(3) for kind in ("blob", "table")),
        *(f"func-{service}-{suffix}{scm}.azurewebsites.net"
          for service in ("worker", "tools", "publisher") for scm in ("", ".scm")),
    }
    if not isinstance(value["private_hosts"], list) or set(value["private_hosts"]) != expected_private:
        raise BootstrapError("all and only owned private hostnames must be proven")
    if set(value["runtime_object_ids"]) != {"review", "worker", "tools", "publisher"}:
        raise BootstrapError("four runtime object IDs required")
    if len(set(value["runtime_object_ids"].values())) != 4:
        raise BootstrapError("runtime identities must be distinct")
    for oid in value["runtime_object_ids"].values():
        if not UUID(oid).int:
            raise BootstrapError("placeholder runtime object ID")
    if "grants" in value["operations"]:
        clients = value["runtime_client_ids"]
        if (not isinstance(clients, dict) or set(clients) != set(value["runtime_object_ids"])
                or len(set(clients.values())) != 4
                or any(not UUID(client_id).int for client_id in clients.values())
                or set(clients.values()) & set(value["runtime_object_ids"].values())):
            raise BootstrapError("four distinct runtime application client IDs, separate from object IDs, required")
    if not isinstance(value["review_scopes"], dict) or set(value["review_scopes"]) != {"sources", "entitlements"}:
        raise BootstrapError("server-held review scope catalog required")
    packages = value["packages"]
    if not isinstance(packages, list) or len({item["service"] for item in packages}) != len(packages):
        raise BootstrapError("duplicate/invalid package list")
    if "deploy" in value["operations"] and {p["service"] for p in packages} != {"worker", "tools", "publisher"}:
        raise BootstrapError("deploy requires exactly the three Function packages")
    if "deploy-preflight" in value["operations"]:
        if value["operations"] != ["deploy-preflight"] or {p["service"] for p in packages} != {"worker-preflight"}:
            raise BootstrapError("preflight deployment must be isolated to its one worker package")
    for package in packages:
        service = package["service"]
        if (set(package) != {"service", "file", "sha256", "scm_host"}
                or service not in {"worker", "worker-preflight", "tools", "publisher"}
                or package["file"] != f"packages/{service}.zip"
                or package["scm_host"] != f"func-{service.removesuffix('-preflight')}-{suffix}.scm.azurewebsites.net"
                or not re.fullmatch("[a-f0-9]{64}", package["sha256"])):
            raise BootstrapError("package target/file/hash is not owned")
        if service == "worker-preflight" and value["operations"] != ["deploy-preflight"]:
            raise BootstrapError("preflight package requires explicit preflight-only authorization")
    return value


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text("utf-8")) if path.exists() else {}

    def save(self, key: str, value: dict) -> None:
        self.data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(".new")
        with staging.open("w", encoding="utf-8") as stream:
            json.dump(self.data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        staging.replace(self.path)


def _auth(credential, scope: str) -> dict:
    return {"Authorization": "Bearer " + credential.get_token(scope).token}


def deploy_function(package: dict, root: Path, credential, client, journal: Journal,
                    *, wait=time.sleep, max_polls=180) -> dict:
    """Persist submit intent before a non-idempotent POST; never blind-retry an uncertain submission."""
    service, host = package["service"], package["scm_host"]
    payload = (root / package["file"]).read_bytes()
    if sha256(payload).hexdigest() != package["sha256"]:
        raise BootstrapError("Function ZIP differs from approved digest")
    private_dns(host)
    key = f"deploy-{service}"
    previous = journal.data.get(key)
    if previous and previous.get("sha256") != package["sha256"]:
        raise BootstrapError("deployment journal cannot be reused for changed content")
    if previous and previous.get("state") == "complete":
        return previous
    if previous and previous.get("state") == "intent":
        raise BootstrapError(f"uncertain prior {service} submission; operator reconciliation required")
    status_url = previous.get("status_url") if previous else None
    if status_url:
        parsed = urlparse(status_url)
        if (parsed.scheme != "https" or parsed.netloc != host or parsed.query or parsed.fragment
                or not re.fullmatch(r"/api/deployments/[A-Za-z0-9_-]+", parsed.path)
                or parsed.path.endswith("/latest")):
            raise BootstrapError("persisted deployment receipt URL is not owned")
    if not status_url:
        journal.save(key, {"state": "intent", "sha256": package["sha256"]})
        response = client.post(
            f"https://{host}/api/publish?RemoteBuild=true&Deployer=itops_bootstrap",
            content=payload,
            headers={**_auth(credential, "https://management.azure.com/.default"),
                     "Content-Type": "application/zip", "Cache-Control": "no-cache"},
        )
        if response.status_code != 202:
            rejected = {"state": "intent", "sha256": package["sha256"], "http_status": response.status_code}
            error_code = response.headers.get("x-ms-error-code", "")
            if isinstance(error_code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", error_code):
                rejected["error_code"] = error_code
            text = response.text
            if isinstance(text, str):
                lowered = text[:8192].lower()
                if "web app is stopped" in lowered or "site is disabled" in lowered:
                    rejected["response_marker"] = "site_disabled"
                elif "ip forbidden" in lowered:
                    rejected["response_marker"] = "ip_forbidden"
            journal.save(key, rejected)
            raise BootstrapError(f"OneDeploy submit status {response.status_code}; do not resubmit without reconciliation")
        location = response.headers.get("Location", "")
        if location.startswith("/"):
            location = f"https://{host}{location}"
        parsed = urlparse(location)
        if (parsed.scheme != "https" or parsed.netloc != host
                or not re.fullmatch(r"/api/deployments/[A-Za-z0-9_-]+", parsed.path)
                or parsed.path.endswith("/latest") or parsed.query or parsed.fragment):
            # Flex can return the operation ID as its response body instead of Location.
            deployment_id = response.text.strip().strip('"')
            try:
                body = response.json()
                if isinstance(body, dict):
                    deployment_id = body.get("id", "")
            except ValueError:
                pass
            if (not isinstance(deployment_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", deployment_id) or deployment_id == "latest"):
                raise BootstrapError("OneDeploy gave no unique receipt ID; reconcile without using /latest")
            location = f"https://{host}/api/deployments/{deployment_id}"
        status_url = location
        journal.save(key, {"state": "submitted", "sha256": package["sha256"], "status_url": status_url})
    for _ in range(max_polls):
        response = client.get(status_url, headers=_auth(credential, "https://management.azure.com/.default"))
        latest = response.status_code == 404
        if latest:
            # The official Flex CLI polls latest. Use it only when the unique route
            # is unavailable, and require the returned ID to match our POST receipt.
            response = client.get(f"https://{host}/api/deployments/latest",
                                  headers=_auth(credential, "https://management.azure.com/.default"))
        if response.status_code in (202, 404, 429, 503):
            wait(5)
            continue
        if response.status_code != 200:
            raise BootstrapError(f"OneDeploy status lookup failed ({response.status_code})")
        status = response.json()
        if latest and status.get("id") != status_url.rsplit("/", 1)[1]:
            wait(5)
            continue
        if status.get("status") == 4:
            receipt = {"state": "complete", "sha256": package["sha256"], "status_url": status_url,
                       "deployment_id": str(status.get("id", status_url.rsplit("/", 1)[1]))}
            if receipt["deployment_id"] != status_url.rsplit("/", 1)[1]:
                raise BootstrapError("OneDeploy receipt identity differs from submitted operation")
            journal.save(key, receipt)
            return receipt
        if status.get("status") in (-1, 3, 5, 6):
            raise BootstrapError(f"OneDeploy did not complete successfully (status {status['status']})")
        wait(5)
    raise BootstrapError("OneDeploy polling budget expired; retain submitted receipt for reconciliation")


def _sql_connection(manifest: dict):
    import ctypes
    import mssql_python
    info = mssql_python.get_native_provider_info()
    ctypes.CDLL(info["driver_path"])
    server, database = manifest["sql_server"], manifest["sql_database"]
    if not re.fullmatch(r"[a-z0-9-]+\.database\.windows\.net", server) or database != "itops":
        raise BootstrapError("unexpected SQL target")
    client_id = UUID(manifest["bootstrap_client_id"])
    mssql_python.pooling(enabled=False)
    connection = mssql_python.connect(
        f"Server=tcp:{server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no;"
        f"Authentication=ActiveDirectoryMSI;UID={client_id};",
        timeout=15, autocommit=True, native_uuid=False,
    )
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT SUSER_SID()")
            actual = bytes(cursor.fetchone()[0])
            if actual != UUID(manifest["bootstrap_client_id"]).bytes_le:
                raise BootstrapError("Azure SQL did not identify this UAMI by its approved application client SID; no users will be created")
        finally:
            cursor.close()
    except BaseException:
        connection.close()
        raise
    return connection


def initialize_secrets(manifest: dict, credential) -> dict:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.keyvault.secrets import SecretClient
    oidc = os.environ.get("ITOPS_REVIEW_ENTRA_CLIENT_SECRET", "")
    if len(oidc) < 16:
        raise BootstrapError("approved review OIDC credential must be supplied directly to isolated bootstrap settings")
    with SecretClient(manifest["vault_url"], credential=credential) as client:
        try:
            session_key = client.get_secret("review-session-key").value
            if not session_key or len(session_key) < 32:
                raise BootstrapError("existing review session key is invalid; explicit rotation required")
        except ResourceNotFoundError:
            client.set_secret("review-session-key", secrets.token_urlsafe(48))
        for name, value in (("review-scopes", canonical(manifest["review_scopes"]).decode()),
                            ("review-oidc-secret", oidc)):
            try:
                current = client.get_secret(name).value
            except ResourceNotFoundError:
                current = None
            if current != value:
                client.set_secret(name, value)
    return {"state": "complete", "secret_names": ["review-session-key", "review-scopes", "review-oidc-secret"]}


def _scope_addition(previous, desired):
    for catalog in (previous, desired):
        if (not isinstance(catalog, dict) or set(catalog) != {"sources", "entitlements"}
                or any(not isinstance(catalog[key], list) or not catalog[key]
                       for key in ("sources", "entitlements"))):
            raise BootstrapError("scope activation requires complete nonempty catalogs")
    additions = {}
    for key in ("sources", "entitlements"):
        old, new = ({canonical(item) for item in catalog[key]} for catalog in (previous, desired))
        if (len(old) != len(previous[key]) or len(new) != len(desired[key])
                or not old < new or len(new - old) != 1):
            raise BootstrapError("scope activation may only append one source and one existing-actor entitlement")
        additions[key] = json.loads(next(iter(new - old)))
    source, entitlement = additions["sources"], additions["entitlements"]
    if (set(source) != {"producer_key", "run_id", "domain", "surfaces"}
            or set(entitlement) != {"oid", "producer_key", "run_id", "domain"}
            or any(not isinstance(source[key], str) or not source[key].strip()
                   for key in ("producer_key", "run_id", "domain"))
            or not isinstance(source["surfaces"], list) or not source["surfaces"]
            or any(not isinstance(item, str) for item in source["surfaces"])
            or len(set(source["surfaces"])) != len(source["surfaces"])
            or any(entitlement[key] != source[key] for key in ("producer_key", "run_id", "domain"))
            or any(item["run_id"] == source["run_id"] for item in previous["sources"])):
        raise BootstrapError("scope activation requires one new exact producer/run/domain")
    matching = [item for item in previous["sources"]
                if item["producer_key"] == source["producer_key"] and item["domain"] == source["domain"]
                and set(source["surfaces"]) <= set(item["surfaces"])]
    if not any({**entitlement, "run_id": item["run_id"]} in previous["entitlements"] for item in matching):
        raise BootstrapError("scope activation cannot add a principal, producer, domain or surface permission")


def initialize_review_scopes(manifest: dict, credential) -> dict:
    from azure.keyvault.secrets import SecretClient
    desired = canonical(manifest["review_scopes"])
    expected = sha256(desired).hexdigest()
    with SecretClient(manifest["vault_url"], credential=credential, retry_total=0) as client:
        current = client.get_secret("review-scopes")
        previous = json.loads(current.value)
        actual = sha256(canonical(previous)).hexdigest()
        if actual != expected:
            if actual != manifest["previous_review_scopes_sha256"]:
                raise BootstrapError("review catalog changed since approval; explicit reconciliation required")
            _scope_addition(previous, manifest["review_scopes"])
            client.set_secret("review-scopes", desired.decode())
            current = client.get_secret("review-scopes")
            if sha256(canonical(json.loads(current.value))).hexdigest() != expected:
                raise BootstrapError("scope-only catalog write failed readback")
    return {"state": "complete", "secret_names": ["review-scopes"],
            "previous_sha256": manifest["previous_review_scopes_sha256"],
            "current_sha256": expected, "version": current.properties.version}


def initialize_search(manifest: dict, credential, client) -> dict:
    definition = manifest["search_index"]
    if not isinstance(definition, dict) or definition.get("name") != "agentic-itops-knowledge":
        raise BootstrapError("explicit owned Search index definition required")
    endpoint = manifest["search_endpoint"]
    if not re.fullmatch(r"https://srch-[a-z0-9-]+\.search\.windows\.net", endpoint):
        raise BootstrapError("invalid owned Search endpoint")
    url = f"{endpoint}/indexes/{definition['name']}?api-version=2025-09-01"
    headers = _auth(credential, "https://search.azure.com/.default")
    response = client.get(url, headers=headers)
    if response.status_code == 404:
        response = client.put(url, headers={**headers, "If-None-Match": "*"}, json=definition)
        if response.status_code not in (200, 201):
            raise BootstrapError(f"Search index creation failed ({response.status_code})")
    elif response.status_code == 200:
        existing = response.json()
        if not matches_contract(definition, existing):
            raise BootstrapError("existing Search index differs; destructive/schema update requires new approval")
    else:
        raise BootstrapError(f"Search index lookup failed ({response.status_code})")
    return {"state": "complete", "index": definition["name"], "schema_sha256": sha256(canonical(definition)).hexdigest()}


def matches_contract(expected, actual) -> bool:
    """Search adds nested defaults; preserve every explicitly requested value and list member."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches_contract(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches_contract(left, right) for left, right in zip(expected, actual)
        )
    return type(expected) is type(actual) and expected == actual


def source_witness(manifest: dict, raw: str) -> dict:
    if len(raw) > 4096:
        raise BootstrapError("source witness exceeds count-only bound")
    witness = json.loads(raw)
    fields = {"contract_sha256", "observed_at", "observation_rows", "audit_rows", "witness_kind", "inference_input"}
    if (not isinstance(witness, dict) or set(witness) != fields
            or witness["contract_sha256"] != manifest["source_probe"]["contract_sha256"]
            or witness["witness_kind"] != "operator_count_only" or witness["inference_input"] is not False
            or any(type(witness[key]) is not int or witness[key] <= 0 for key in ("observation_rows", "audit_rows"))):
        raise BootstrapError("matching known-nonempty count-only operator witness required")
    observed = datetime.fromisoformat(witness["observed_at"].replace("Z", "+00:00"))
    if observed.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 300:
        raise BootstrapError("source witness must be refreshed within five minutes after remote build")
    return witness


def probe_source(manifest: dict, credential, client, *, environment=None) -> dict:
    environment = os.environ if environment is None else environment
    witness = source_witness(manifest, environment.get("ITOPS_SOURCE_PROBE_WITNESS_JSON", ""))
    suffix = manifest["bootstrap_site"].removeprefix("app-bootstrap-")
    host = f"func-worker-{suffix}.azurewebsites.net"
    private_dns(host)
    response = client.post(
        f"https://{host}/api/preflight/source-access",
        headers=_auth(credential, manifest["source_probe"]["token_scope"]), json={"witness": witness},
    )
    if response.status_code != 200:
        raise BootstrapError(f"worker source isolation proof failed (HTTP {response.status_code})")
    result = response.json()
    expected_fields = {
        "contract_sha256", "verified_at", "observation_rows", "audit_access", "denial_evidence",
        "independent_audit_rows", "inference_input", "execution_allowed", "status",
        "worker_client_id", "caller_oid", "probe_kind",
    }
    if (not isinstance(result, dict) or set(result) != expected_fields
            or result["status"] != "verified" or result["audit_access"] != "denied"
            or result["denial_evidence"] not in {"explicit_insufficient_access", "filtered_to_zero_against_nonempty_witness"}
            or result["probe_kind"] != "worker_managed_identity_count_only"
            or result["worker_client_id"] != manifest["source_probe"]["worker_client_id"]
            or result["caller_oid"] != manifest["bootstrap_object_id"]
            or result["contract_sha256"] != witness["contract_sha256"]
            or any(type(result[key]) is not int for key in ("observation_rows", "independent_audit_rows"))
            or result["observation_rows"] != witness["observation_rows"]
            or result["independent_audit_rows"] != witness["audit_rows"]
            or result["inference_input"] is not False or result["execution_allowed"] is not False):
        raise BootstrapError("worker source proof receipt identity/contract differs from approval")
    verified = datetime.fromisoformat(result["verified_at"].replace("Z", "+00:00"))
    observed = datetime.fromisoformat(witness["observed_at"].replace("Z", "+00:00"))
    if (verified.tzinfo is None or verified < observed
            or not 0 <= (datetime.now(timezone.utc) - verified).total_seconds() <= 300):
        raise BootstrapError("worker source proof receipt is not fresh")
    return {"state": "complete", "proof": result}


def run(root: Path = ROOT) -> dict:
    import fcntl
    state = STATE_DIRECTORY
    state.mkdir(parents=True, exist_ok=True)
    with (state / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BootstrapError("another bootstrap process is already running") from exc
        return _run_locked(root, state)


def _run_locked(root: Path, state: Path) -> dict:
    from azure.identity import ManagedIdentityCredential
    import httpx
    manifest = load_manifest(root / "bootstrap-manifest.json", os.environ)
    journal = Journal(state / f"{manifest['deployment_id']}.json")
    identity = {"sha256": sha256((root / "bootstrap-manifest.json").read_bytes()).hexdigest()}
    if journal.data.get("manifest", identity) != identity:
        raise BootstrapError("deployment ID is already bound to a different manifest")
    journal.save("manifest", identity)
    attempt = str(UUID(os.environ["ITOPS_BOOTSTRAP_ATTEMPT_ID"]))
    if (manifest["operations"] == ["probe-source"]
            and journal.data.get("probe-source", {}).get("state") == "complete"):
        journal.data["attempt"] = {"id": attempt}
        journal.save("bootstrap", {"state": "failed", "reason": "source proof requires a new approval/deployment ID"})
        raise BootstrapError("completed source proof requires a new approval/deployment ID")
    journal.save("attempt", {"id": attempt})
    if journal.data.get("bootstrap", {}).get("state") == "complete":
        return journal.data["bootstrap"]
    journal.save("bootstrap", {"state": "running"})
    private_evidence = {}
    for host in manifest["private_hosts"]:
        private_evidence[host] = (private_dns(host, port=1433)
                                  if host == manifest["sql_server"] else private_tls(host))
    journal.save("private-connectivity", {"state": "complete", "addresses": private_evidence,
                                         "tls": "validated except SQL (validated by native SQL connection)"})
    with ManagedIdentityCredential(client_id=manifest["bootstrap_client_id"]) as credential, \
            httpx.Client(timeout=120, follow_redirects=False) as client:
        for operation in manifest["operations"]:
            if operation in ("migrate", "grants"):
                connection = _sql_connection(manifest)
                journal.save("sql-identity", {"state": "verified", "sid_kind": "application-client-id",
                                              "client_id": manifest["bootstrap_client_id"]})
                try:
                    if operation == "migrate":
                        receipt = {"state": "complete", "migrations": apply_migrations(connection, root)}
                    else:
                        path = root / "deployment" / "sql-grants.json"
                        if not path.exists():
                            raise BootstrapError("source-reviewed SQL grants are not present; do not invent permissions")
                        allowlist = json.loads(path.read_text("utf-8"))
                        apply_grants(connection, manifest["runtime_client_ids"], allowlist)
                        receipt = {"state": "complete", "allowlist_sha256": sha256(path.read_bytes()).hexdigest()}
                finally:
                    connection.close()
            elif operation == "secrets":
                receipt = initialize_secrets(manifest, credential)
            elif operation == "review-scopes":
                receipt = initialize_review_scopes(manifest, credential)
            elif operation == "search":
                receipt = initialize_search(manifest, credential, client)
            elif operation == "probe-source":
                receipt = probe_source(manifest, credential, client)
            else:
                receipt = {"state": "complete", "packages": [
                    deploy_function(package, root, credential, client, journal) for package in manifest["packages"]
                ]}
            journal.save(operation, receipt)
    journal.save("bootstrap", {"state": "complete", "deployment_id": manifest["deployment_id"]})
    return journal.data["bootstrap"]


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()
        self.wfile.write(b"bootstrap host; execution result is available only in the private journal\n")

    def log_message(self, *_args):
        pass


def main() -> None:
    # App Service startup needs a listening process while bounded work runs.
    # Main ingress is deny-all in IaC; this route never accepts commands or returns secrets.
    server = ThreadingHTTPServer(("0.0.0.0", 8000), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        receipt = run() if os.environ.get("ITOPS_BOOTSTRAP_AUTHORIZED") == "true" else {"state": "disarmed"}
        print(json.dumps(receipt), flush=True)
    except Exception as exc:
        # SDK exception messages can contain service payloads; don't log them.
        failure = {"state": "failed", "error_type": type(exc).__name__}
        if isinstance(exc, (BootstrapError, MigrationError)):
            failure["reason"] = str(exc)
        elif type(exc).__module__.startswith("mssql_python."):
            failure["sql_diagnostics"] = sql_failure_metadata(exc)
        try:
            if isinstance(exc.__cause__, BlockingIOError):
                raise ValueError("do not overwrite another process journal")
            manifest = load_manifest(ROOT / "bootstrap-manifest.json", os.environ)
            deployment_id = str(UUID(manifest["deployment_id"]))
            journal = Journal(STATE_DIRECTORY / f"{deployment_id}.json")
            journal.save("bootstrap", failure)
        except (OSError, ValueError, KeyError, BootstrapError):
            pass
        print(json.dumps(failure), flush=True)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
