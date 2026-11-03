"""Private scheduled evidence worker. Review mode cannot execute remediation."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient
from azure.identity import ManagedIdentityCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.storage.queue import QueueClient, TextBase64DecodePolicy
from paramiko import SSHException

from operations.cloud import AzureReader, AzureReadError
from operations.lifecycle import EVIDENCE_HOST, TARGETS, canonical_uuid
from operations.protocol import fingerprint, parse_request
from operations.ssh import Guest, GuestError, parse_host_trust

LOG = logging.getLogger("asre-operations")
ACCOUNT = EVIDENCE_HOST.split(".")[0]
QUEUE = "agent-requests"
VAULT = "https://kv-asre-8a327010f9.vault.azure.net/"


def identity(name: str):
    return ManagedIdentityCredential(client_id=canonical_uuid(os.environ[name]))


def blobs(credential) -> BlobServiceClient:
    return BlobServiceClient("https://" + EVIDENCE_HOST, credential=credential, retry_total=0)


def write_blob(client: BlobServiceClient, path: str, value: dict) -> None:
    client.get_blob_client("evidence", path).upload_blob(
        json.dumps(value, sort_keys=True).encode(), overwrite=False,
        content_settings=ContentSettings(content_type="application/json"),
    )


def bootstrap() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    credential = identity("ASRE_BOOTSTRAP_CLIENT_ID")
    vault = SecretClient(VAULT, credential, retry_total=0)
    known = base64.b64decode(os.environ["ASRE_SSH_KNOWN_HOSTS_B64"], validate=True).decode()
    parse_host_trust(known)
    try:
        private = vault.get_secret("runner-ssh-private-key").value
    except ResourceNotFoundError:
        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()
        vault.set_secret("runner-ssh-private-key", private, content_type="application/x-openssh-private-key")
        LOG.info("Generated the runner key inside the private runtime and stored it in Key Vault.")
    if not private:
        raise ValueError("The existing runner secret is empty; refusing to replace it silently.")
    key = serialization.load_ssh_private_key(private.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("The existing runner credential is not an Ed25519 private key.")
    public = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ).decode()
    vault.set_secret("runner-ssh-known-hosts", known, content_type="text/plain")
    print("ASRE_RUNTIME_SSH_PUBLIC_KEY " + public, flush=True)


def collect() -> None:
    credential = identity("ASRE_COLLECTOR_CLIENT_ID")
    evidence = AzureReader(credential).collect()
    path = "baselines/jobs/" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + str(uuid4()) + ".json"
    write_blob(blobs(credential), path, evidence)
    print(json.dumps({"status": "collected", "evidence_uri": "https://" + EVIDENCE_HOST + "/evidence/" + path}))


def verify() -> None:
    if os.environ.get("ASRE_EXECUTION_MODE") != "Review":
        raise PermissionError("Runtime verification admits Review mode only.")
    runner = identity("ASRE_RUNNER_CLIENT_ID")
    guest = Guest(SecretClient(VAULT, runner, retry_total=0))
    reader = AzureReader(identity("ASRE_COLLECTOR_CLIENT_ID"))
    observations = {}
    for target in TARGETS:
        data = guest.inspect(target)
        if data["health"].get("status") != "ok" or data["health"].get("mode") != "normal":
            raise GuestError("The independent SSH baseline did not observe a healthy normal workload.")
        if not -30 <= time.time() - float(data["observed_at"]) <= 60:
            raise GuestError("The independent guest inspection is not fresh.")
        if data["service"].count("ActiveState=active") != 2:
            raise GuestError("The workload and Azure guest agent are not both active.")
        observations[target] = {"guest": data, "telemetry": reader.telemetry(target)}
    evidence = {
        "status": "verified", "mode": "Review", "mechanism": "pinned-host-ssh",
        "observed_at": datetime.now(timezone.utc).isoformat(), "targets": observations,
        "cloud_jobs": reader.collect(),
    }
    check_id = str(uuid4())
    path = "baselines/runtime/" + check_id + ".json"
    write_blob(blobs(runner), path, evidence)
    table = TableClient("https://" + ACCOUNT + ".table.core.windows.net", "Episodes", credential=runner, retry_total=0)
    table.create_entity({
        "PartitionKey": "runtime-verification", "RowKey": check_id,
        "status": "verified", "evidence_uri": "https://" + EVIDENCE_HOST + "/evidence/" + path,
    })
    if table.get_entity("runtime-verification", check_id)["status"] != "verified":
        raise ValueError("The private evidence ledger did not persist the verification receipt.")
    print("ASRE_RUNTIME_VERIFIED " + json.dumps({
        "status": "verified", "mode": "Review", "targets": len(observations),
        "evidence_uri": "https://" + EVIDENCE_HOST + "/evidence/" + path,
    }), flush=True)


def receipt(request: dict, **result) -> dict:
    return {
        **result, "request_id": request["request_id"], "command": request["command"],
        "resource_id": request["resource_id"], "mode": "Review",
        "fingerprint": fingerprint(request), "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def execute(request: dict, table, storage, handlers) -> str:
    row = {
        "PartitionKey": "tool-requests", "RowKey": request["request_id"],
        "fingerprint": fingerprint(request), "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    path = "tool-results/" + request["request_id"] + ".json"
    try:
        table.create_entity(row)
    except ResourceExistsError:
        row = table.get_entity(row["PartitionKey"], row["RowKey"])
        if row["fingerprint"] != fingerprint(request):
            raise PermissionError("An idempotency key was reused with different input.")
        try:
            persisted = json.loads(storage.get_blob_client("evidence", path).download_blob().readall())
        except ResourceNotFoundError:
            if row["status"] != "running":
                raise ValueError("A terminal request is missing its immutable evidence receipt.")
            started = datetime.fromisoformat(row["started_at"])
            if started.tzinfo is None:
                raise ValueError("A request claim has an invalid start timestamp.")
            if (datetime.now(timezone.utc) - started).total_seconds() < 360:
                LOG.warning("Duplicate request remains in progress; preserving its queued reconciliation request=%s", request["request_id"])
                return "running"
            persisted = receipt(
                request, status="failed",
                error="The worker exceeded its 300-second execution bound without a receipt; no recovery is inferred.",
            )
            write_blob(storage, path, persisted)
        if persisted.get("fingerprint") != fingerprint(request) or persisted.get("status") not in {"completed", "failed"}:
            raise ValueError("The existing receipt does not match its durable request claim.")
        row["status"] = persisted["status"]
        table.update_entity(row, mode="replace")
        return row["status"]
    try:
        data = handlers[request["command"]](request["resource_id"])
    except (AzureError, AzureReadError, GuestError, SSHException, OSError, ValueError, TypeError, KeyError):
        LOG.exception("Evidence command failed request=%s command=%s", request["request_id"], request["command"])
        result = receipt(request, status="failed", error="The authoritative evidence command failed; inspect runtime logs.")
    else:
        result = receipt(request, status="completed", data=data)
    write_blob(storage, path, result)
    row["status"] = result["status"]
    table.update_entity(row, mode="replace")
    return row["status"]


def process() -> None:
    if os.environ.get("ASRE_EXECUTION_MODE") != "Review":
        raise PermissionError("This deployed worker admits Review mode only; autonomous execution is not configured.")
    from operations.patch_sensor import run
    run()  # Parent-bounded, before (not charged to) the original queue budget.
    runner = identity("ASRE_RUNNER_CLIENT_ID")
    reader = AzureReader(identity("ASRE_COLLECTOR_CLIENT_ID"))
    storage = blobs(runner)
    table = TableClient("https://" + ACCOUNT + ".table.core.windows.net", "Episodes", credential=runner, retry_total=0)
    queue = QueueClient(
        "https://" + ACCOUNT + ".queue.core.windows.net", QUEUE, credential=runner,
        message_decode_policy=TextBase64DecodePolicy(), retry_total=0,
    )
    guest = Guest(SecretClient(VAULT, runner, retry_total=0))
    handlers = {
        "inspect": guest.inspect,
        "telemetry": reader.telemetry,
        "patch_jobs": reader.patches,
        "backup_jobs": reader.backup,
    }
    deadline = time.monotonic() + 40
    count = 0
    while time.monotonic() < deadline and count < 10:
        messages = queue.receive_messages(messages_per_page=1, visibility_timeout=180)
        message = next(iter(messages), None)
        if message is None:
            break
        count += 1
        try:
            request = parse_request(message.content)
        except (ValueError, TypeError, KeyError, PermissionError):
            LOG.exception("Rejected malformed, expired or unauthorized queue message id=%s", message.id)
            queue.delete_message(message)
            continue
        try:
            status = execute(request, table, storage, handlers)
        except PermissionError:
            LOG.exception("Rejected a conflicting idempotency key request=%s", request["request_id"])
            queue.delete_message(message)
            continue
        if status != "running":
            queue.delete_message(message)
        LOG.info("Recorded tool request=%s status=%s", request["request_id"], status)
    print(json.dumps({"status": "processed", "requests": count, "mode": "Review"}))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("azure").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("queue", "collect", "bootstrap", "verify", "feedback"))
    args = parser.parse_args()
    if args.mode == "feedback":
        from operations.feedback_runtime import run
        run()
        return
    {"queue": process, "collect": collect, "bootstrap": bootstrap, "verify": verify}[args.mode]()


if __name__ == "__main__":
    main()
