"""Offline scoped-dispatch tests; these are not live Azure Search receipts."""
import json
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from agentic_itops.review import EntraAuthenticator, create_app
from agentic_itops.review.advancement import RemotePublisherAdvancer
from agentic_itops.review.contracts import OVERLAY, ReviewError, digest
from tests.test_publisher import LocalSearch, approve, dispatch, repo, revoke
from tests.test_review import AUDIENCE, OID, ORIGIN, SOURCE, TENANT, headers, result, signing_key, token


@pytest.fixture
def publication_client(repo, signing_key):
    search = LocalSearch()
    dispatcher = dispatch(repo, search)
    advancer = Mock()
    advancer.advance.side_effect = lambda outbox_id: dispatcher.run_once(outbox_id=outbox_id)
    app = create_app(
        {"TESTING": True, "PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "offline-test-only-" * 4},
        repository=repo, publication_advancer=advancer,
        authenticator=EntraAuthenticator(TENANT, AUDIENCE, key_resolver=lambda _: signing_key.public_key()),
        entitlements={OID: {(SOURCE["producer_key"], SOURCE["run_id"])}},
        source_catalog={(SOURCE["producer_key"], SOURCE["run_id"]): SOURCE},
    )
    return app.test_client(), search, advancer


def test_publication_advance_selects_one_item_and_preserves_selection_on_retry(repo, publication_client, token):
    client, search, advancer = publication_client
    publication = approve(repo)
    other = approve(repo, "incident-b")
    route = f"/publications/{publication['publication_id']}/advance"
    first = client.post(route, json={}, headers=headers(token, key="advance-one"))
    assert first.status_code == 200, first.json
    selected = first.json["outbox_id"]
    assert first.json["status"] == "selected" and len(search.operations) == 1
    revoke(repo, publication)
    retry = client.post(route, json={}, headers=headers(token, key="advance-one"))
    assert retry.status_code == 200 and retry.json["outbox_id"] == selected
    assert len(search.operations) == 1
    removal = client.post(route, json={}, headers=headers(token, key="advance-two"))
    assert removal.status_code == 200 and removal.json["outbox_id"] != selected
    assert len(search.operations) == 2 and search.operations[-1][1] == "delete"
    assert not search.documents
    other_selection = repo.reserve_publication_advance(OID, "/offline", "other", other["publication_id"])
    assert other_selection["status"] == "selected"
    assert advancer.advance.call_count == 3


@pytest.mark.parametrize("failure", ["role", "scope", "body", "missing-key"])
def test_publication_advance_rejects_unauthorized_or_unbounded_requests(repo, publication_client, token, failure):
    client, _, advancer = publication_client
    value = result()
    if failure == "scope":
        value["source"] = {**value["source"], "producer_key": "not-entitled"}
        value["source_hash"] = digest(value["source"])
    publication = approve(repo, value=value)
    request_headers = headers(token, roles=["Viewer"] if failure == "role" else None)
    if failure == "missing-key":
        request_headers.pop("Idempotency-Key")
    response = client.post(
        f"/publications/{publication['publication_id']}/advance",
        json={"outbox_id": str(uuid4())} if failure == "body" else {}, headers=request_headers)
    assert response.status_code == (403 if failure in {"role", "scope"} else 422), response.json
    advancer.advance.assert_not_called()


def test_exact_claim_does_not_fall_back_to_another_publication(repo):
    approve(repo)
    assert repo.claim_publisher(str(uuid4()), outbox_id=str(uuid4())) is None
    assert repo.claim_publisher(str(uuid4())) is not None


@pytest.mark.parametrize("mutation", ["none", "other-id", "other-receipt", "global-maintenance", "operational"])
def test_private_publisher_proxy_binds_the_exact_receipt_and_never_retries(mutation):
    selected = str(uuid4())
    response = {"status": "attempted", "outbox_id": selected, "reconciliation_jobs": 0,
                "receipt": {"outbox_id": selected}, **OVERLAY}
    if mutation == "other-id":
        response["outbox_id"] = str(uuid4())
    elif mutation == "other-receipt":
        response["receipt"] = {"outbox_id": str(uuid4())}
    elif mutation == "global-maintenance":
        response["reconciliation_jobs"] = 1
    elif mutation == "operational":
        response["execution_allowed"] = True
    requests = []
    def handle(request):
        requests.append(request)
        assert json.loads(request.content) == {"outbox_id": selected}
        assert request.headers["Authorization"] == "Bearer service-test-token"
        return httpx.Response(200, json=response)
    credential = Mock()
    credential.get_token.return_value.token = "service-test-token"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        proxy = RemotePublisherAdvancer(
            "https://publisher.example.test", credential, client,
            token_scope=f"api://{uuid4()}/.default")
        if mutation == "none":
            assert proxy.advance(selected) == response
        else:
            with pytest.raises(ReviewError):
                proxy.advance(selected)
    assert len(requests) == 1
