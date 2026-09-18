"""One approved 30-day credential, delivered in memory to a private-vault operation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

from scripts.setup_entra_apps import GRAPH, GraphFailure, save


def issue_and_deliver(application_journal: Path, credential_journal: Path, redirect_uri: str,
                      credential, client, deliver, *, now: datetime | None = None) -> dict:
    if credential_journal.exists():
        raise GraphFailure("credential journal already exists; reconcile its key ID, never repeat addPassword")
    state = json.loads(application_journal.read_text(encoding="utf-8"))
    entry = state["applications"]["agentic-itops-us93-review"]
    object_id, client_id = str(UUID(entry["object_id"])), str(UUID(entry["client_id"]))
    token = credential.get_token("https://graph.microsoft.com/.default").token
    headers = {"Authorization": "Bearer " + token}
    endpoint = GRAPH + "/applications/" + object_id
    response = client.get(endpoint, headers=headers, timeout=60)
    if response.status_code != 200:
        raise GraphFailure(f"review application read-back failed: HTTP {response.status_code}")
    application = response.json()
    if (application.get("appId") != client_id
            or application.get("displayName") != "agentic-itops-us93-review"
            or application.get("signInAudience") != "AzureADMyOrg"
            or application.get("requiredResourceAccess") != []
            or application.get("web", {}).get("redirectUris") != [redirect_uri]):
        raise GraphFailure("review application differs from the approved credential target")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("credential issuance requires an explicit timezone")
    start = now.astimezone(timezone.utc).replace(microsecond=0)
    end = start + timedelta(days=30)
    operation_id = str(uuid4())
    record = {
        "schema_version": 1, "operation_id": operation_id, "application_object_id": object_id,
        "application_client_id": client_id, "state": "create_intent",
        "display_name": "us93-review-" + operation_id, "expires_at": end.isoformat(),
        "secret_persisted_locally": False,
    }
    credential_journal.parent.mkdir(parents=True, exist_ok=True)
    with credential_journal.open("x", encoding="utf-8") as handle:
        json.dump(record, handle)
    save(credential_journal, record)
    response = client.post(endpoint + "/addPassword", headers=headers, timeout=60, json={
        "passwordCredential": {
            "displayName": record["display_name"],
            "startDateTime": start.isoformat(), "endDateTime": end.isoformat(),
        },
    })
    if response.status_code not in (200, 201):
        record["state"] = "creation_unconfirmed"
        record["http_status"] = response.status_code
        save(credential_journal, record)
        raise GraphFailure(f"credential creation unconfirmed: HTTP {response.status_code}; reconcile before replacement")
    result = response.json()
    key_id = str(UUID(result["keyId"]))
    record["credential_key_id"] = key_id
    record["state"] = "created_delivery_pending"
    save(credential_journal, record)
    secret = result.pop("secretText", None)
    actual_end = datetime.fromisoformat(result["endDateTime"].replace("Z", "+00:00"))
    if not isinstance(secret, str) or len(secret) < 16 or actual_end != end:
        raise GraphFailure("credential response failed validation; reconcile recorded key ID")
    try:
        receipt = deliver(secret)
    finally:
        secret = None
    if not isinstance(receipt, dict) or receipt.get("state") != "complete":
        raise GraphFailure("private vault delivery was not confirmed; reconcile recorded credential key ID")
    record["state"] = "delivered"
    save(credential_journal, record)
    return record
