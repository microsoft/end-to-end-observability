from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.issue_review_secret import issue_and_deliver
from scripts.setup_entra_apps import GraphFailure


@pytest.mark.parametrize("delivery_succeeds", [False, True])
def test_secret_is_never_journaled_and_creation_cannot_replay(tmp_path, delivery_succeeds):
    httpx = pytest.importorskip("httpx")
    now = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)
    object_id, app_id, key_id = str(uuid4()), str(uuid4()), str(uuid4())
    redirect = "https://app-review-example.azurewebsites.net/.auth/login/aad/callback"
    application_file, journal = tmp_path / "applications.json", tmp_path / "credential.json"
    application_file.write_text(json.dumps({"applications": {"agentic-itops-us93-review": {
        "object_id": object_id, "client_id": app_id,
    }}}), encoding="utf-8")
    secret = "offline-test-secret-never-persist"
    writes = []
    def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json={
                "appId": app_id, "displayName": "agentic-itops-us93-review", "signInAudience": "AzureADMyOrg",
                "requiredResourceAccess": [], "web": {"redirectUris": [redirect]},
            })
        writes.append(json.loads(request.content))
        return httpx.Response(200, json={"keyId": key_id, "secretText": secret,
                                        "endDateTime": (now + timedelta(days=30)).isoformat()})
    def deliver(value):
        assert value == secret
        assert secret not in journal.read_text(encoding="utf-8")
        if not delivery_succeeds:
            raise RuntimeError("test-only vault transport failure")
        return {"state": "complete"}
    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="offline"))
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        if delivery_succeeds:
            assert issue_and_deliver(application_file, journal, redirect, credential, client, deliver, now=now)["state"] == "delivered"
        else:
            with pytest.raises(RuntimeError, match="transport failure"):
                issue_and_deliver(application_file, journal, redirect, credential, client, deliver, now=now)
        with pytest.raises(GraphFailure, match="already exists"):
            issue_and_deliver(application_file, journal, redirect, credential, client, deliver, now=now)
    record = json.loads(journal.read_text(encoding="utf-8"))
    assert record["credential_key_id"] == key_id and secret not in journal.read_text(encoding="utf-8")
    assert record["state"] == ("delivered" if delivery_succeeds else "created_delivery_pending")
    assert len(writes) == 1 and "secretText" not in writes[0]["passwordCredential"]
