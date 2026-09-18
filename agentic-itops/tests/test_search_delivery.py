"""Real SDK wire tests with fake transports and SQLite; NOT live Azure/model proof."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import socket
import sqlite3
import time
from urllib.parse import unquote, urlparse

from azure.core.credentials import AccessToken
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.search.documents import SearchClient
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx2
import jwt
from jsonschema import validate
import pytest

from agentic_itops.consumer.budget import BudgetExceeded, BudgetLedger, BudgetLimits
from agentic_itops.review.contracts import OVERLAY, ReviewError, canonical, digest
from agentic_itops.review.publisher import PublisherDispatcher, document_for
from agentic_itops.review.repository import SQLiteReviewRepository
from agentic_itops.review.search_delivery import (
    DIMENSIONS, INDEX_NAME, INDEX_SCHEMA, MODEL, SCHEMA_HASH,
    SEARCH_OUTPUT_SCHEMA, SEARCH_REQUEST_SCHEMA, ApprovedKnowledgeRetriever, AzureSearchDelivery,
    BudgetLedgerEmbeddingGate, EmbeddingReservation, ManagedIdentityEmbedding,
    SearchServiceSettings, bootstrap_index, knowledge_index,
)
from tests.test_publisher import OWNER, approve, revoke, status
from tests.test_review import SOURCE

SEARCH_ENDPOINT = "https://offline-search.search.windows.net"
OPENAI_ENDPOINT = "https://offline-openai.openai.azure.com/"


@pytest.fixture(autouse=True)
def prohibit_live_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("live network is prohibited in Search SDK wire tests")
    monkeypatch.setattr(socket.socket, "connect", denied)


class WireCredential:
    def __init__(self):
        self.scopes = []
    def get_token(self, *scopes, **kwargs):
        self.scopes.append(scopes)
        return AccessToken("offline-transport-token", int(time.time()) + 3600)
    def close(self):
        pass


class RecordingFinance:
    def __init__(self):
        self.reservations, self.settlements = [], []
        self.error = None
    def reserve(self, **request):
        if self.error:
            raise self.error
        self.reservations.append(request)
        return EmbeddingReservation("offline-" + str(len(self.reservations)), 20)
    def settle(self, reservation, *, input_tokens):
        self.settlements.append((reservation.reservation_id, input_tokens))


class EmbeddingWire:
    def __init__(self):
        self.requests = []
        self.change = None
        self.error_status = None
        self.after_response = None
        self.headers = {"apim-request-id": "embedding-wire-request"}
    def handle(self, request):
        self.requests.append(request)
        if self.error_status:
            return httpx2.Response(self.error_status, json={"error": {"message": "wire failure"}}, request=request)
        body = {
            "object": "list", "model": MODEL, "usage": {"prompt_tokens": 3, "total_tokens": 3},
            "data": [{"index": 0, "object": "embedding", "embedding": [0.125] * DIMENSIONS}],
        }
        if self.change:
            self.change(body)
        if self.after_response:
            self.after_response()
        return httpx2.Response(200, json=body, headers=self.headers, request=request)
    def client(self):
        return httpx2.Client(transport=httpx2.MockTransport(self.handle), follow_redirects=False)


class SearchResponse(HttpResponse):
    def __init__(self, request, status, body, headers=None):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"content-type": "application/json", "request-id": "search-wire-request"}
        if headers is not None:
            self.headers = headers
        self.content_type = "application/json"
        self._body = json.dumps(body).encode("utf-8")
        self.reason = "offline wire response"
    def body(self):
        return self._body
    def text(self, encoding=None):
        return self._body.decode(encoding or "utf-8")
    def json(self):
        return json.loads(self._body)
    def read(self):
        return self._body
    @property
    def content(self):
        return self._body


class SearchWire(HttpTransport):
    def __init__(self):
        self.requests, self.documents = [], {}
        self.index = None
        self.indexing_change = None
        self.readback_change = None
        self.search_change = None
        self.write_error = None
        self.search_error = None
        self.keep_deleted = False
        self.hide_uploaded = False
        self.missing_request_id = False
    def open(self):
        pass
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()
    def send(self, request, **kwargs):
        self.requests.append((request, kwargs))
        path = unquote(urlparse(request.url).path)
        payload = json.loads(request.body) if request.body else None
        headers = {} if self.missing_request_id else None
        if request.method == "POST" and (path.endswith("/docs/search.index") or path.endswith("/docs/index")):
            if self.write_error:
                return SearchResponse(request, self.write_error, {"error": {"message": "wire failure"}})
            results = []
            for item in payload["value"]:
                action = item["@search.action"]
                key = item["document_id"]
                if action == "upload":
                    self.documents[key] = {k: v for k, v in item.items() if k != "@search.action"}
                elif action == "delete" and not self.keep_deleted:
                    self.documents.pop(key, None)
                results.append({"key": key, "status": True, "statusCode": 201 if action == "upload" else 200,
                                "errorMessage": None})
            if self.indexing_change:
                self.indexing_change(results)
            return SearchResponse(request, 200, {"value": results}, headers)
        if request.method == "POST" and (path.endswith("/docs/search") or path.endswith("/docs/search.post.search")):
            if ("semanticMaxWaitInMilliseconds" in payload
                    and payload.get("semanticErrorHandling") != "partial"):
                return SearchResponse(request, 400, {"error": {
                    "code": "InvalidRequestParameter",
                    "message": "semanticMaxWaitInMilliseconds requires semanticErrorHandling partial",
                }})
            if self.search_error:
                return SearchResponse(request, self.search_error, {"error": {"message": "wire search failure"}})
            body = {"value": [
                {**deepcopy(document), "@search.score": 1.0, "@search.rerankerScore": 3.5}
                for document in list(self.documents.values())[:3]
            ]}
            if self.search_change:
                self.search_change(body)
            return SearchResponse(request, 200, body, headers)
        if request.method == "GET" and "/docs" in path:
            match = re.search(r"/docs\('([^']+)'\)", path)
            key = match.group(1) if match else path.rsplit("/", 1)[-1]
            if key not in self.documents or self.hide_uploaded:
                return SearchResponse(request, 404, {"error": {"message": "Document not found"}})
            body = deepcopy(self.documents[key])
            if self.readback_change:
                self.readback_change(body)
            return SearchResponse(request, 200, body, headers)
        if request.method == "GET" and "/indexes" in path:
            return SearchResponse(request, 200, self.index) if self.index else SearchResponse(
                request, 404, {"error": {"message": "Index not found"}},
            )
        if request.method == "POST" and path.endswith("/indexes"):
            self.index = payload
            return SearchResponse(request, 201, payload)
        raise AssertionError(f"unexpected genuine SDK wire route: {request.method} {path}")


@pytest.fixture
def settings():
    return SearchServiceSettings(
        OWNER, "approved-snapshot-v1", frozenset({("producer-a", "run-a")}),
        search_endpoint=SEARCH_ENDPOINT, openai_endpoint=OPENAI_ENDPOINT,
    )


@pytest.fixture
def repo():
    repository = SQLiteReviewRepository()
    yield repository
    repository.close()


def adapters(settings, repo, *, finance=None):
    embedding_wire, search_wire = EmbeddingWire(), SearchWire()
    credential = WireCredential()
    gate = finance or RecordingFinance()
    embedding = ManagedIdentityEmbedding(settings, gate, credential=credential, http_client=embedding_wire.client())
    delivery = AzureSearchDelivery(settings, embedding, repo, credential=credential, transport=search_wire)
    retriever = ApprovedKnowledgeRetriever(settings, embedding, repo, credential=credential, transport=search_wire)
    return embedding_wire, search_wire, gate, embedding, delivery, retriever, credential


def retrieve(retriever, **overrides):
    return retriever.retrieve("synthetic energy incident", producer_key="producer-a", run_id="run-a",
                              snapshot_id="approved-snapshot-v1", **overrides)


def test_readiness_probe_uses_real_search_sdk_without_embedding_or_repository(settings):
    wire = SearchWire()
    retriever = ApprovedKnowledgeRetriever(settings, None, None, credential=WireCredential(), transport=wire)
    result = retriever.probe(producer_key="producer-a", run_id="run-a", snapshot_id=settings.snapshot_id)
    assert result == {
        "status": "verified", "probe_kind": "fixed_vector_contract_only",
        "request_id": "search-wire-request", "returned_hit_count": 0,
        "inference_performed": False, "embedding_performed": False,
    }
    assert len(wire.requests) == 1
    payload = json.loads(wire.requests[0][0].body)
    assert payload["semanticErrorHandling"] == "fail"
    assert "semanticMaxWaitInMilliseconds" not in payload
    assert payload["queryType"] == "semantic" and payload["vectorFilterMode"] == "preFilter"
    assert payload["vectorQueries"][0]["vector"] == [1.0] + [0.0] * (DIMENSIONS - 1)
    assert "producer_key eq 'producer-a'" in payload["filter"]
    assert "run_id eq 'run-a'" in payload["filter"]
    assert payload["minimumCoverage"] == 100


@pytest.mark.parametrize("failure", ["partial", "http", "foreign"])
def test_readiness_probe_never_turns_failure_into_empty_success(settings, failure):
    wire = SearchWire()
    if failure == "partial":
        wire.search_change = lambda body: body.update({"@search.semanticPartialResponseReason": "maxWaitExceeded"})
    if failure == "http":
        wire.search_error = 400
    retriever = ApprovedKnowledgeRetriever(settings, None, None, credential=WireCredential(), transport=wire)
    with pytest.raises((ReviewError, HttpResponseError)):
        retriever.probe(producer_key="foreign" if failure == "foreign" else "producer-a",
                        run_id="run-a", snapshot_id=settings.snapshot_id)
    assert len(wire.requests) == (0 if failure == "foreign" else 1)


def test_genuine_sdk_publish_readback_guarded_retrieve_revoke_delete(settings, repo):
    publication = approve(repo)
    emb_wire, search_wire, finance, _, delivery, retriever, credential = adapters(settings, repo)
    worker = PublisherDispatcher(repo, delivery, owner=OWNER)
    receipt = worker.run_once()
    assert receipt["status"] == "accepted"
    assert receipt["delivery"]["request_id"] == "search-wire-request"
    request = emb_wire.requests[0]
    assert str(request.url).startswith(OPENAI_ENDPOINT + "openai/deployments/text-embedding-3-small/embeddings?")
    assert "api-version=2024-10-21" in str(request.url)
    assert json.loads(request.content)["dimensions"] == 1536
    assert "api-key" not in request.headers
    assert finance.settlements == [("offline-1", 3)]
    assert ("https://cognitiveservices.azure.com/.default",) in credential.scopes
    assert ("https://search.azure.com/.default",) in credential.scopes
    artifact = retrieve(retriever, analysis_revision=1)
    validate(artifact, SEARCH_OUTPUT_SCHEMA)
    assert artifact["status"] == "complete" and len(artifact["documents"]) == 1
    document = artifact["documents"][0]
    assert document["publication_id"] == publication["publication_id"]
    assert document["revision"] == "1" and document["sha256"] == document["content_hash"]
    assert document["retained_until_utc"] == publication["eligible_until"]
    retriever.guard_documents(artifact["documents"], producer_key="producer-a", run_id="run-a",
                              snapshot_id=settings.snapshot_id)
    search_request = next(request for request, _ in search_wire.requests
                          if request.url.split("?")[0].endswith("/docs/search.post.search"))
    query = json.loads(search_request.body)
    assert query["top"] == 3 and query["queryType"] == "semantic"
    assert query["vectorQueries"][0]["kind"] == "vector" and query["vectorQueries"][0]["k"] == 50
    assert query["vectorFilterMode"] == "preFilter"
    assert query["semanticErrorHandling"] == "fail"
    assert "semanticMaxWaitInMilliseconds" not in query
    for clause in ("snapshot_id eq 'approved-snapshot-v1'", "producer_key eq 'producer-a'",
                   "run_id eq 'run-a'", "analysis_revision eq 1"):
        assert clause in query["filter"]
    revoke(repo, publication)
    # Simulate a stale physical index hit: SQL excludes it explicitly.
    rejected = retrieve(retriever)
    assert rejected["documents"] == [] and rejected["rejected_documents"] == [
        {"doc_id": document["doc_id"], "reason": "sql-ineligible"},
    ]
    with pytest.raises(ReviewError):
        retriever.guard_documents(artifact["documents"], producer_key="producer-a", run_id="run-a",
                                  snapshot_id=settings.snapshot_id)
    assert worker.run_once()["status"] == "removed"
    assert search_wire.documents == {}
    assert retrieve(retriever)["documents"] == []


@pytest.mark.parametrize("changes", [
    {"search_endpoint": "https://other.invalid"},
    {"openai_endpoint": "https://example.services.ai.azure.com/api/projects/project"},
    {"embedding_deployment": "latest"}, {"embedding_model": "text-embedding-ada-002"},
    {"embedding_model_version": "latest"}, {"dimensions": 3072}, {"dimensions": True},
    {"index_name": "another-index"}, {"timeout_seconds": 0}, {"client_id": "default"},
    {"allowed_sources": frozenset()}, {"snapshot_id": ""},
])
def test_endpoint_identity_model_and_scope_pins_fail_closed(settings, changes):
    with pytest.raises(ReviewError):
        replace(settings, **changes)


def test_real_embedding_requires_finance_before_any_provider_request(settings, repo):
    _, search_wire, finance, embedding, _, _, _ = adapters(settings, repo)
    finance.error = BudgetExceeded("no approved embedding budget available")
    with pytest.raises(BudgetExceeded):
        embedding.embed("synthetic input", operation_id="call-one")
    assert search_wire.requests == []
    with pytest.raises(ReviewError, match="finance"):
        ManagedIdentityEmbedding(settings, None, credential=WireCredential())


@pytest.mark.parametrize("change", [
    lambda body: body.update(model="different-model"),
    lambda body: body["data"][0].update(embedding=[0.1] * 10),
    lambda body: body["data"][0].update(embedding=[True] * DIMENSIONS),
    lambda body: body["data"][0].update(embedding=[0.0] * DIMENSIONS),
    lambda body: body["data"].append(deepcopy(body["data"][0])),
    lambda body: body.update(usage={"prompt_tokens": 999999, "total_tokens": 999999}),
])
def test_malformed_embedding_keeps_financial_reservation_and_prevents_search(settings, repo, change):
    publication = approve(repo)
    emb_wire, search_wire, finance, _, delivery, _, _ = adapters(settings, repo)
    emb_wire.change = change
    with pytest.raises((ReviewError, ValueError)):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert search_wire.requests == []
    assert finance.settlements in ([("offline-1", None)], [("offline-1", 999999)])
    assert not repo.get_publication(publication["publication_id"])["searchable"]


def test_embedding_and_search_errors_do_not_automatically_retry(settings, repo):
    approve(repo)
    emb_wire, search_wire, finance, _, delivery, _, _ = adapters(settings, repo)
    emb_wire.error_status = 429
    with pytest.raises(Exception):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert len(emb_wire.requests) == 1 and not search_wire.requests
    assert finance.settlements == [("offline-1", None)]
    emb_wire.error_status = None
    search_wire.write_error = 503
    with pytest.raises(HttpResponseError):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert len(search_wire.requests) == 1


@pytest.mark.parametrize("change", [
    lambda results: results[0].update(status=False, statusCode=500),
    lambda results: results.clear(),
    lambda results: results.append({"key": "other", "status": False, "statusCode": 503}),
    lambda results: results[0].update(key="wrong-key"),
])
def test_actual_sdk_item_failures_never_ack_or_read_back(settings, repo, change):
    publication = approve(repo)
    _, search_wire, _, _, delivery, _, _ = adapters(settings, repo)
    search_wire.indexing_change = change
    with pytest.raises(ReviewError):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert len(search_wire.requests) == 1
    assert not repo.get_publication(publication["publication_id"])["searchable"]


@pytest.mark.parametrize("change", [
    lambda body: body.update(content="tampered"),
    lambda body: body.update(snapshot_id="different"),
    lambda body: body.update(producer_key="Producer-A"),
    lambda body: body.update(analysis_revision=2),
    lambda body: body.update(content_vector=[0.8] * DIMENSIONS),
])
def test_upload_readback_must_match_exact_projection(settings, repo, change):
    publication = approve(repo)
    _, search_wire, _, _, delivery, _, _ = adapters(settings, repo)
    search_wire.readback_change = change
    with pytest.raises(ReviewError):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert len(search_wire.requests) == 2
    assert not repo.get_publication(publication["publication_id"])["searchable"]


def test_delete_readback_present_or_forbidden_is_not_confirmed_absence(settings, repo):
    publication = approve(repo)
    _, search_wire, _, _, delivery, _, _ = adapters(settings, repo)
    worker = PublisherDispatcher(repo, delivery, owner=OWNER)
    worker.run_once()
    revoke(repo, publication)
    search_wire.keep_deleted = True
    with pytest.raises(ReviewError, match="not yet visible"):
        worker.run_once()
    assert repo.get_publication(publication["publication_id"])["removal_state"] == "pending"
    search_wire.keep_deleted = False
    assert worker.run_once()["status"] == "removed"


def test_revoke_during_embedding_does_not_send_upload(settings, repo):
    publication = approve(repo)
    emb_wire, search_wire, _, _, delivery, _, _ = adapters(settings, repo)
    emb_wire.after_response = lambda: revoke(repo, publication)
    with pytest.raises(ReviewError, match="ineligible"):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert search_wire.requests == []


def test_sql_snapshot_binding_is_immutable_across_retries(settings, repo):
    publication = approve(repo)
    _, wire, _, _, delivery, _, _ = adapters(settings, repo)
    wire.write_error = 503
    with pytest.raises(HttpResponseError):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    different = replace(settings, snapshot_id="another-snapshot")
    emb2, wire2, _, _, delivery2, _, _ = adapters(different, repo)
    with pytest.raises(ReviewError, match="immutable publisher snapshot"):
        PublisherDispatcher(repo, delivery2, owner=OWNER).run_once()
    assert not emb2.requests and not wire2.requests
    assert not repo.get_publication(publication["publication_id"])["searchable"]


@pytest.mark.parametrize("change", [
    lambda body: body.update({"@search.semanticPartialResponseReason": "maxWaitExceeded"}),
    lambda body: body["value"][0].pop("@search.rerankerScore"),
    lambda body: body["value"][0].update(snapshot_id="wrong"),
    lambda body: body["value"][0].update(content_hash="0" * 64),
    lambda body: body["value"][0].update(producer_key="other"),
    lambda body: body["value"].append(deepcopy(body["value"][0])),
    lambda body: body.update({"@odata.nextLink": SEARCH_ENDPOINT + "/unexpected",
                              "@search.nextPageParameters": {"search": "query", "skip": 3}}),
])
def test_schema_scope_semantic_and_pagination_errors_are_not_empty_fallbacks(settings, repo, change):
    approve(repo)
    _, wire, _, _, delivery, retriever, _ = adapters(settings, repo)
    PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    before = len(wire.requests)
    wire.search_change = change
    with pytest.raises(ReviewError):
        retrieve(retriever)
    assert len(wire.requests) == before + 1


def test_scope_checks_precede_embedding_and_preserve_opaque_run_identity(settings, repo):
    emb_wire, _, _, _, _, retriever, _ = adapters(settings, repo)
    for arguments in [
        {"producer_key": "Producer-A", "run_id": "run-a", "snapshot_id": settings.snapshot_id},
        {"producer_key": "producer-a", "run_id": "Run-a", "snapshot_id": settings.snapshot_id},
        {"producer_key": "producer-a", "run_id": "run-a", "snapshot_id": "other"},
    ]:
        with pytest.raises(ReviewError):
            retriever.retrieve("bounded query", **arguments)
    assert not emb_wire.requests
    value = replace(settings, allowed_sources=frozenset({("producer-a", "run 'A with spaces")}))
    _, wire, _, _, _, scoped, _ = adapters(value, repo)
    scoped.retrieve("bounded query", producer_key="producer-a", run_id="run 'A with spaces",
                    snapshot_id=value.snapshot_id)
    assert "run_id eq 'run ''A with spaces'" in json.loads(wire.requests[-1][0].body)["filter"]


def test_index_bootstrap_uses_real_sdk_and_refuses_drift_without_replacement(settings):
    wire, credential = SearchWire(), WireCredential()
    receipt = bootstrap_index(settings, credential, approval_reference="offline-approval",
                              expected_schema_hash=SCHEMA_HASH, transport=wire)
    assert receipt["created"] is True
    assert wire.index == INDEX_SCHEMA
    assert knowledge_index().as_dict() == INDEX_SCHEMA
    assert bootstrap_index(settings, credential, approval_reference="offline-approval",
                           expected_schema_hash=SCHEMA_HASH, transport=wire)["created"] is False
    wire.index["fields"][0]["key"] = False
    before = len(wire.requests)
    with pytest.raises(ReviewError, match="schema differs"):
        bootstrap_index(settings, credential, approval_reference="offline-approval",
                        expected_schema_hash=SCHEMA_HASH, transport=wire)
    assert len(wire.requests) == before + 1
    with pytest.raises(ReviewError, match="approval hash"):
        bootstrap_index(settings, credential, approval_reference="offline-approval",
                        expected_schema_hash="wrong", transport=wire)


def test_real_budget_ledger_reserves_and_settles_embedding_usage(settings, repo, tmp_path):
    path = tmp_path / "budget.sqlite"
    limits = BudgetLimits(
        policy_version="offline-embedding-policy", pricing_reference="offline-only-no-live-price",
        model_context_tokens=8192, max_output_tokens=1,
        input_nanodollars_per_token=1, output_nanodollars_per_token=1,
        monthly_nanodollars=1000000, workflow_tokens=10000,
    )
    ledger = BudgetLedger(lambda: sqlite3.connect(path), limits, "embedding-offline", dialect="sqlite-local-test")
    ledger.migrate()
    _, _, _, embedding, _, _, _ = adapters(settings, repo, finance=BudgetLedgerEmbeddingGate(ledger))
    embedding.embed("synthetic input", operation_id="test-budget")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT state,input_tokens,output_tokens,charged_nanos FROM itops_runtime_call",
        ).fetchone() == ("settled", 3, 0, 3)
    blocked = replace(limits, monthly_nanodollars=10)
    gate = BudgetLedgerEmbeddingGate(BudgetLedger(
        lambda: sqlite3.connect(path), blocked, "embedding-blocked", dialect="sqlite-local-test",
    ))
    wire, _, _, embedding, _, _, _ = adapters(settings, repo, finance=gate)
    with pytest.raises(BudgetExceeded):
        embedding.embed("synthetic input", operation_id="budget-blocked")
    assert not wire.requests


@pytest.fixture
def function_module():
    path = Path("services") / "publisher" / "function_app.py"
    spec = importlib.util.spec_from_file_location("publisher_function_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def service_request(function_module, signing_key, *, overrides=None, body=b"{}", headers=None):
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{OWNER}/v2.0", "aud": "publisher-api", "tid": OWNER,
        "oid": OWNER, "idtyp": "app", "roles": ["Publisher.Invoke"],
        "iat": now - 5, "nbf": now - 5, "exp": now + 300,
    }
    claims.update(overrides or {})
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    return function_module.func.HttpRequest(
        method="POST", url="https://publisher.example.test/api/publisher/run-once", body=body,
        headers=headers if headers is not None else {"Authorization": "Bearer " + token},
    )


def test_publisher_trigger_bindings_match_python_signature(function_module):
    from inspect import signature

    functions = function_module.app.get_functions()
    assert len(functions) == 1
    function = functions[0]
    bindings = json.loads(function.get_function_json())["bindings"]
    declared = {binding["name"] for binding in bindings if binding["name"] != "$return"}
    assert declared == set(signature(function.get_user_function()).parameters)
    assert declared == {"req"}


def test_publisher_function_auth_and_real_sdk_dispatch(function_module, signing_key, settings, repo):
    approve(repo)
    _, wire, _, _, delivery, _, _ = adapters(settings, repo)
    authenticator = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    service = function_module.PublisherFunctionService(
        authenticator, PublisherDispatcher(repo, delivery, owner=OWNER), repo,
    )
    response = service.handle(service_request(function_module, signing_key))
    assert response.status_code == 200 and json.loads(response.get_body())["receipt"]["status"] == "accepted"
    assert len(wire.documents) == 1
    assert service.handle(service_request(function_module, signing_key)).status_code == 200


def test_publisher_selected_request_cannot_run_global_maintenance(function_module, signing_key):
    from unittest.mock import Mock
    from uuid import uuid4
    selected = str(uuid4())
    auth = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    dispatcher, repository = Mock(), Mock()
    dispatcher.run_once.return_value = {"outbox_id": selected, "status": "accepted"}
    service = function_module.PublisherFunctionService(auth, dispatcher, repository)
    response = service.handle(service_request(
        function_module, signing_key, body=json.dumps({"outbox_id": selected}).encode()))
    assert response.status_code == 200
    assert json.loads(response.get_body())["outbox_id"] == selected
    dispatcher.run_once.assert_called_once_with(outbox_id=selected)
    repository.reconcile_publisher.assert_not_called()
    dispatcher.reset_mock()
    for body in (b'{"outbox_id":null}', b'{"outbox_id":"invalid"}',
                 json.dumps({"outbox_id": selected, "other": True}).encode()):
        assert service.handle(service_request(function_module, signing_key, body=body)).status_code == 422
    dispatcher.run_once.assert_not_called()
    repository.reconcile_publisher.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"aud": "other"}, {"aud": ["publisher-api", "other"]},
    {"oid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
    {"idtyp": "user"}, {"scp": "access_as_user"}, {"exp": 1},
    {"iss": "https://attacker.invalid"}, {"tid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
])
def test_service_identity_allowlist_rejects_without_work(function_module, signing_key, overrides):
    authenticator = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    with pytest.raises(ReviewError):
        authenticator.authenticate(service_request(function_module, signing_key, overrides=overrides))


def test_function_ignores_unsigned_platform_headers_and_rejects_model_arguments(function_module, signing_key, repo):
    auth = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    for headers in ({}, {"X-MS-CLIENT-PRINCIPAL": OWNER}, {"X-MS-TOKEN-AAD-ID-TOKEN": "unsigned"}):
        with pytest.raises(ReviewError):
            auth.authenticate(service_request(function_module, signing_key, headers=headers))
    class NeverDispatch:
        def run_once(self):
            raise AssertionError("invalid body must not invoke publisher")
    service = function_module.PublisherFunctionService(auth, NeverDispatch(), repo)
    for body in (b'{"publication_id":"model-choice"}', b"[]", b"not-json", b"x" * 129):
        assert service.handle(service_request(function_module, signing_key, body=body)).status_code >= 400


def test_production_settings_and_compiler_schemas(settings):
    from agentic_itops.portfolio import compile_portfolio
    from tests.test_trusted_search_dispatch import trusted_config

    environment = {
        "AZURE_CLIENT_ID": OWNER, "ITOPS_SEARCH_SNAPSHOT_ID": settings.snapshot_id,
        "ITOPS_SEARCH_SCOPES_JSON": '[{"producer_key":"producer-a","run_id":"run-a"}]',
        "ITOPS_SEARCH_ENDPOINT": SEARCH_ENDPOINT, "ITOPS_SEARCH_INDEX": INDEX_NAME,
        "ITOPS_OPENAI_ENDPOINT": OPENAI_ENDPOINT, "ITOPS_EMBEDDING_DEPLOYMENT": MODEL,
        "ITOPS_EMBEDDING_MODEL": MODEL, "ITOPS_EMBEDDING_MODEL_VERSION": "1",
        "ITOPS_EMBEDDING_DIMENSIONS": "1536",
    }
    assert SearchServiceSettings.from_environment(environment) == settings
    config = trusted_config()
    config["search"]["search_knowledge"].update({
        "handler": "approved_knowledge.retrieve", "snapshot_id": settings.snapshot_id,
        "request_schema": SEARCH_REQUEST_SCHEMA, "output_schema": SEARCH_OUTPUT_SCHEMA,
    })
    portfolio = compile_portfolio(Path.cwd(), config)
    assert portfolio.bindings_complete
    assert next(f for f in portfolio.functions if f.name == "search_approved_knowledge").backend_binding[
        "requires_sql_eligibility_revalidation"
    ] is True


def test_real_requests_transport_response_surface_is_supported(settings, repo):
    """Exercise SDK 12's production REST response, not only a legacy test response."""
    from io import BytesIO
    from types import SimpleNamespace
    from azure.core.pipeline.transport import RequestsTransport
    import requests

    wire = SearchWire()
    class WireSession(requests.Session):
        def request(self, method, url, **kwargs):
            outgoing = SimpleNamespace(method=method, url=url, body=kwargs.get("data"), headers=kwargs.get("headers"))
            received = wire.send(outgoing)
            response = requests.Response()
            response.status_code, response.headers = received.status_code, received.headers
            response._content = received.content
            response.raw = BytesIO(received.content)
            response.request = requests.Request(method, url).prepare()
            response.url = url
            return response

    approve(repo)
    credential = WireCredential()
    embedding_wire = EmbeddingWire()
    embedding = ManagedIdentityEmbedding(settings, RecordingFinance(), credential=credential,
                                          http_client=embedding_wire.client())
    transport = RequestsTransport(session=WireSession(), session_owner=False)
    delivery = AzureSearchDelivery(settings, embedding, repo, credential=credential, transport=transport)
    assert PublisherDispatcher(repo, delivery, owner=OWNER).run_once()["status"] == "accepted"
    retriever = ApprovedKnowledgeRetriever(settings, embedding, repo, credential=credential, transport=transport)
    assert len(retrieve(retriever)["documents"]) == 1


def test_actual_sdk_revoke_during_upload_reconciles_real_delete_wire(settings, repo):
    publication = approve(repo)
    _, wire, _, _, delivery, _, _ = adapters(settings, repo)
    def revoke_after_write(_):
        wire.indexing_change = None
        revoke(repo, publication)
    wire.indexing_change = revoke_after_write
    worker = PublisherDispatcher(repo, delivery, owner=OWNER)
    assert worker.run_once()["status"] == "rejected"
    assert len(wire.documents) == 1
    assert worker.run_once()["status"] == "removed"
    assert not wire.documents


def test_ambient_openai_keys_and_api_versions_cannot_override_managed_identity_pin(settings, repo, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "offline-key-must-not-be-used")
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "offline-token-must-not-be-used")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://attacker.invalid/")
    monkeypatch.setenv("OPENAI_API_VERSION", "unapproved")
    wire, _, _, embedding, _, _, _ = adapters(settings, repo)
    embedding.embed("synthetic input", operation_id="ambient-test")
    assert "api-key" not in wire.requests[0].headers
    assert wire.requests[0].headers["Authorization"] == "Bearer offline-transport-token"
    assert str(wire.requests[0].url).startswith(OPENAI_ENDPOINT)
    assert "api-version=2024-10-21" in str(wire.requests[0].url)


@pytest.mark.parametrize("body", [
    "", " " * 10, "x" * 8001, "\U0001f600" * 2001,
])
def test_embedding_utf8_byte_bound_precedes_financial_admission(settings, repo, body):
    wire, _, gate, embedding, _, _, _ = adapters(settings, repo)
    with pytest.raises(ReviewError):
        embedding.embed(body, operation_id="bad-input")
    assert not wire.requests and not gate.reservations


def test_sdk_response_request_id_is_never_invented(settings, repo):
    publication = approve(repo)
    _, wire, _, _, delivery, _, _ = adapters(settings, repo)
    wire.missing_request_id = True
    with pytest.raises(ReviewError, match="request ID"):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    assert not repo.get_publication(publication["publication_id"])["searchable"]


def test_sql_scope_gate_rejects_wrong_snapshot_and_producer_independently(settings, repo):
    approve(repo)
    _, _, _, _, delivery, retriever, _ = adapters(settings, repo)
    PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    artifact = retrieve(retriever)
    doc = artifact["documents"][0]
    for changes in [
        {"snapshot_id": "other"}, {"producer_key": "Producer-A"},
        {"run_id": "Run-A"}, {"analysis_revision": 2}, {"projection_hash": "0" * 64},
    ]:
        scope = {"snapshot_id": settings.snapshot_id, "producer_key": "producer-a", "run_id": "run-a",
                 "analysis_revision": 1, "projection_hash": doc["projection_hash"], **changes}
        with pytest.raises(ReviewError):
            repo.publication_for_retrieval(doc["publication_id"], doc["doc_id"], doc["content_hash"], **scope)
    tampered = deepcopy(artifact["documents"])
    tampered[0]["text"] = "different runtime content"
    with pytest.raises(ReviewError):
        retriever.guard_documents(tampered, producer_key="producer-a", run_id="run-a", snapshot_id=settings.snapshot_id)


def test_source_only_function_package_is_deterministic_and_resolves_profiles(tmp_path):
    import zipfile
    from deployment.package import ROOT, sources, validate_function_zip, write_zip

    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    files = sources(ROOT, "publisher")
    a, b = write_zip(first, files), write_zip(second, files)
    assert a["sha256"] == b["sha256"]
    validate_function_zip(first.read_bytes(), "publisher")
    with zipfile.ZipFile(first) as archive:
        names = set(archive.namelist())
        assert {"host.json", "function_app.py", "agentic_itops/review/search_delivery.py",
                "services/publisher/knowledge-index.schema.json"} <= names
        requirements = archive.read("requirements.txt").decode()
        assert "-r " not in requirements
        assert "azure-search-documents==12.0.0" in requirements
        assert "azure-functions==2.3.0" in requirements
        assert all(".venv" not in name and "__pycache__" not in name and not name.endswith(".env") for name in names)


def test_function_provider_failure_is_non_success_and_not_secret_leak(function_module, signing_key, repo):
    auth = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    class FailingDispatch:
        def run_once(self):
            raise RuntimeError("provider-secret-configuration")
    service = function_module.PublisherFunctionService(auth, FailingDispatch(), repo)
    response = service.handle(service_request(function_module, signing_key))
    assert response.status_code == 503
    assert b"provider-secret" not in response.get_body()
    assert json.loads(response.get_body())["status"] == "failed"


def test_production_constructors_explicitly_select_only_managed_identity(settings, repo, monkeypatch):
    from agentic_itops.review import search_delivery

    selected = []
    def managed_identity(**kwargs):
        selected.append(kwargs)
        return WireCredential()
    monkeypatch.setattr(search_delivery, "ManagedIdentityCredential", managed_identity)
    embedding = ManagedIdentityEmbedding(settings, RecordingFinance())
    delivery = AzureSearchDelivery(settings, embedding, repo)
    retriever = ApprovedKnowledgeRetriever(settings, embedding, repo)
    assert len(selected) == 3
    assert all(item["client_id"] == OWNER and item["retry_total"] == 0 for item in selected)
    assert embedding.client.max_retries == 0
    assert embedding.client._client.follow_redirects is False
    assert embedding.client._client.trust_env is False
    retriever.close()
    delivery.close()
    embedding.close()


def test_snapshot_projection_sqlite_trigger_and_sql_migration_are_explicit(settings, repo):
    approve(repo)
    _, _, _, _, delivery, _, _ = adapters(settings, repo)
    PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    with pytest.raises(sqlite3.IntegrityError, match="immutable publisher projection"):
        with repo.transaction() as cursor:
            cursor.execute("UPDATE review_publisher_projections SET projection_json='{}'")
    migration = (Path("agentic_itops") / "review" / "migrations" / "003_search_projection.sql").read_text()
    assert "BEGIN TRANSACTION" in migration and "COMMIT TRANSACTION" in migration
    assert "CREATE TRIGGER review.publisher_projection_immutable" in migration
    assert "GRANT " not in migration and "CREATE ROLE" not in migration


@pytest.mark.parametrize("observe_only", [False, True])
def test_function_is_http_only_and_factory_uses_explicit_budget_and_identity(
        function_module, repo, monkeypatch, observe_only):
    from dataclasses import asdict, replace
    from tests.test_usage_policy import approved_usage

    functions = function_module.app.get_functions()
    assert len(functions) == 1
    bindings = [binding.get_dict_repr() for binding in functions[0].get_bindings()]
    assert {binding["type"] for binding in bindings} == {"httpTrigger", "http"}
    trigger = next(binding for binding in bindings if binding["type"] == "httpTrigger")
    assert trigger["route"] == "publisher/run-once" and trigger["methods"] == [function_module.func.HttpMethod.POST]
    environment = {
        "AZURE_CLIENT_ID": OWNER, "ITOPS_SEARCH_SNAPSHOT_ID": "approved-snapshot-v1",
        "ITOPS_SEARCH_SCOPES_JSON": '[{"producer_key":"producer-a","run_id":"run-a"}]',
        "ITOPS_SEARCH_ENDPOINT": SEARCH_ENDPOINT, "ITOPS_SEARCH_INDEX": INDEX_NAME,
        "ITOPS_OPENAI_ENDPOINT": OPENAI_ENDPOINT, "ITOPS_EMBEDDING_DEPLOYMENT": MODEL,
        "ITOPS_EMBEDDING_MODEL": MODEL, "ITOPS_EMBEDDING_MODEL_VERSION": "1",
        "ITOPS_EMBEDDING_DIMENSIONS": "1536", "ITOPS_PUBLISHER_ENABLED": "true",
        "ENTRA_TENANT_ID": OWNER, "ITOPS_PUBLISHER_AUDIENCE": "publisher-api",
        "ITOPS_PUBLISHER_CALLER_OIDS_JSON": json.dumps([OWNER]),
        "ITOPS_SQL_SERVER": "offline.database.windows.net", "ITOPS_SQL_DATABASE": "offline",
        "ITOPS_EMBEDDING_BUDGET_SCOPE": "offline-embedding",
        "ITOPS_EMBEDDING_BUDGET_JSON": json.dumps(asdict(BudgetLimits(
            policy_version="offline-policy", pricing_reference="offline-only",
            model_context_tokens=8192, max_output_tokens=1, input_nanodollars_per_token=1,
            output_nanodollars_per_token=1, monthly_nanodollars=100000, workflow_tokens=20000,
        ))),
    }
    if observe_only:
        limits = BudgetLimits(**{
            **json.loads(environment["ITOPS_EMBEDDING_BUDGET_JSON"]),
            "usage_mode": "observe_only", "api_calls": 4, "deadline_seconds": 120})
        environment["ITOPS_EMBEDDING_BUDGET_JSON"] = json.dumps(asdict(limits))
        environment["ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"] = json.dumps(
            approved_usage(replace(limits, max_output_tokens=100), limits))
    sql_identity = []
    def sql_factory(server, database, client_id):
        sql_identity.append((server, database, client_id))
        return lambda: None
    monkeypatch.setattr(function_module, "managed_identity_sql_factory", sql_factory)
    monkeypatch.setattr(function_module, "SqlServerReviewRepository", lambda _: repo)
    ledgers = []
    def capture_ledger(*args, **kwargs):
        ledgers.append((args, kwargs))
        return object()
    monkeypatch.setattr(function_module, "BudgetLedger", capture_ledger)
    monkeypatch.setattr(function_module, "ManagedIdentityEmbedding", lambda *args: object())
    class Delivery:
        def __init__(self, *args):
            pass
        def upload(self, *args, **kwargs):
            raise AssertionError("factory test must not call provider")
        def delete(self, *args, **kwargs):
            raise AssertionError("factory test must not call provider")
    monkeypatch.setattr(function_module, "AzureSearchDelivery", Delivery)
    service = function_module.create_publisher_service(environment)
    assert service.authenticator.caller_oids == frozenset({OWNER})
    assert service.dispatcher.lease_seconds == 180
    assert sql_identity == [("offline.database.windows.net", "offline", OWNER)]
    assert ledgers[0][0][1].caps_enforced is not observe_only
    authorization = ledgers[0][1]["usage_authorization"]
    assert (authorization is not None) is observe_only
    if observe_only:
        assert authorization.policy_hash == limits.sha256
        assert ledgers[0][0][1].deadline_seconds == 120
        assert ledgers[0][0][1].api_calls == 4
        from agentic_itops.consumer.contracts import ContractError
        with pytest.raises(ContractError, match="approval"):
            function_module.create_publisher_service({
                key: value for key, value in environment.items()
                if key != "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"})
        assert len(ledgers) == 1
    with pytest.raises(ReviewError, match="not enabled"):
        function_module.create_publisher_service({**environment, "ITOPS_PUBLISHER_ENABLED": "false"})


def test_reported_embedding_overrun_is_charged_and_blocks_its_budget_workflow(settings, repo, tmp_path):
    path = tmp_path / "overrun.sqlite"
    limits = BudgetLimits(
        policy_version="offline-overrun", pricing_reference="offline-only",
        model_context_tokens=8192, max_output_tokens=1, input_nanodollars_per_token=1,
        output_nanodollars_per_token=1, monthly_nanodollars=100000, workflow_tokens=20000,
    )
    ledger = BudgetLedger(lambda: sqlite3.connect(path), limits, "overrun", dialect="sqlite-local-test")
    ledger.migrate()
    wire, _, _, embedding, _, _, _ = adapters(
        settings, repo, finance=BudgetLedgerEmbeddingGate(ledger, workflow_id="fixed-workflow"),
    )
    wire.change = lambda body: body.update(usage={"prompt_tokens": 9000, "total_tokens": 9000})
    with pytest.raises(ReviewError, match="finance bound"):
        embedding.embed("synthetic input", operation_id="overrun")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT state,input_tokens,charged_nanos FROM itops_runtime_call").fetchone() == (
            "overrun", 9000, 9000,
        )
    with pytest.raises(BudgetExceeded, match="workflow blocked"):
        embedding.embed("synthetic input", operation_id="second-attempt")
    assert len(wire.requests) == 1


@pytest.mark.parametrize("roles,status_code", [
    ([], 403), (["Worker.Invoke"], 403), (["Tools.Invoke"], 403),
    (["Worker.Invoke", "Tools.Invoke"], 403), (["publisher.invoke"], 403),
    (["Reviewer", "KnowledgeReviewer", "Operator"], 403),
    ("Publisher.Invoke", 401), (None, 401), ([True], 401),
    (["Publisher.Invoke"] * 33, 401),
])
def test_publisher_endpoint_requires_its_signed_role_before_any_work(
        function_module, signing_key, roles, status_code):
    auth = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    class NoWork:
        def reconcile_publisher(self, **kwargs):
            raise AssertionError("role denial must precede maintenance")
        def run_once(self):
            raise AssertionError("role denial must precede dispatch")
    no_work = NoWork()
    service = function_module.PublisherFunctionService(auth, no_work, no_work)
    response = service.handle(service_request(function_module, signing_key, overrides={"roles": roles}))
    assert response.status_code == status_code


def test_isolated_bootstrap_roles_do_not_bypass_the_oid_allowlist(function_module, signing_key):
    auth = function_module.ServiceCallerAuthenticator(
        OWNER, "publisher-api", frozenset({OWNER}), key_resolver=lambda _: signing_key.public_key(),
    )
    all_roles = ["Worker.Invoke", "Tools.Invoke", "Publisher.Invoke"]
    assert auth.authenticate(service_request(function_module, signing_key, overrides={"roles": all_roles})) == OWNER
    with pytest.raises(ReviewError) as failure:
        auth.authenticate(service_request(function_module, signing_key, overrides={
            "roles": all_roles, "oid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        }))
    assert failure.value.status == 403


def test_embedding_workflow_namespace_cannot_collide_with_chat_policy(settings, repo, tmp_path):
    path = tmp_path / "shared-budget.sqlite"
    common = dict(
        pricing_reference="offline-only", model_context_tokens=8192,
        input_nanodollars_per_token=1, output_nanodollars_per_token=1,
        monthly_nanodollars=100000, workflow_tokens=20000,
    )
    factory = lambda: sqlite3.connect(path)
    chat = BudgetLedger(factory, BudgetLimits(policy_version="chat-v1", max_output_tokens=100, **common),
                        "chat", dialect="sqlite-local-test")
    chat.migrate()
    chat.open_workflow("logical-workflow")
    embedding_ledger = BudgetLedger(
        factory, BudgetLimits(policy_version="embedding-v1", max_output_tokens=1, **common),
        "embedding", dialect="sqlite-local-test",
    )
    gate = BudgetLedgerEmbeddingGate(embedding_ledger, workflow_id="logical-workflow")
    assert gate.workflow_id != "logical-workflow"
    assert gate.workflow_id == BudgetLedgerEmbeddingGate(
        embedding_ledger, workflow_id="logical-workflow",
    ).workflow_id
    _, _, _, embedding, _, _, _ = adapters(settings, repo, finance=gate)
    embedding.embed("synthetic input", operation_id="bound-query")
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT workflow_id,scope_key FROM itops_runtime_workflow ORDER BY scope_key",
        ).fetchall()
    assert rows == [("logical-workflow", "chat"), (gate.workflow_id, "embedding")]


@pytest.mark.parametrize("field,value", [
    ("search_endpoint", "http://offline-search.search.windows.net"),
    ("search_endpoint", "https://offline-search.search.windows.net.evil.invalid"),
    ("search_endpoint", "https://user@offline-search.search.windows.net"),
    ("search_endpoint", "https://offline-search.search.windows.net:443"),
    ("search_endpoint", "https://offline-search.search.windows.net/indexes"),
    ("search_endpoint", "https://offline-search.search.windows.net?api-key=not-allowed"),
    ("search_endpoint", "https://offline-search.search.windows.net#fragment"),
    ("search_endpoint", "https://127.0.0.1"),
    ("search_endpoint", "https://-invalid.search.windows.net"),
    ("openai_endpoint", "https://example.services.ai.azure.com/api/projects/project"),
    ("openai_endpoint", "https://example.cognitiveservices.azure.com/"),
    ("openai_endpoint", "https://example.openai.azure.com/openai/deployments/model"),
    ("openai_endpoint", "https://example.openai.azure.com:443/"),
    ("openai_endpoint", "https://example.openai.azure.com/?query=1"),
    ("openai_endpoint", "https://example.openai.azure.com.evil.invalid/"),
])
def test_environment_endpoints_reject_noncommercial_or_nonroot_targets(settings, field, value):
    with pytest.raises(ReviewError, match="commercial Azure"):
        replace(settings, **{field: value})


def test_environment_endpoints_are_required_portable_and_canonical(settings):
    with pytest.raises(TypeError):
        SearchServiceSettings(OWNER, settings.snapshot_id, settings.allowed_sources)
    portable = replace(
        settings, search_endpoint="https://different-search.search.windows.net/",
        openai_endpoint="https://different-openai.openai.azure.com",
    )
    assert portable.search_endpoint == "https://different-search.search.windows.net"
    assert portable.openai_endpoint == "https://different-openai.openai.azure.com/"
    assert portable.index_name == INDEX_NAME and portable.embedding_model == MODEL
    code = (Path("agentic_itops") / "review" / "search_delivery.py").read_text()
    assert "SEARCH_ENDPOINT =" not in code and "OPENAI_ENDPOINT =" not in code


def test_environment_target_change_cannot_rebind_existing_publication(settings, repo):
    approve(repo)
    _, wire, _, _, delivery, _, _ = adapters(settings, repo)
    wire.write_error = 503
    with pytest.raises(HttpResponseError):
        PublisherDispatcher(repo, delivery, owner=OWNER).run_once()
    changed = replace(settings, search_endpoint="https://different-search.search.windows.net")
    model, search, _, _, replacement, _, _ = adapters(changed, repo)
    with pytest.raises(ReviewError, match="immutable publisher snapshot"):
        PublisherDispatcher(repo, replacement, owner=OWNER).run_once()
    assert not model.requests and not search.requests


def test_embedding_reservation_uses_parent_remaining_time_and_rejects_expiry(settings, repo, tmp_path):
    path = tmp_path / "deadline.sqlite"
    limits = BudgetLimits(
        policy_version="deadline-policy", pricing_reference="offline-only",
        model_context_tokens=8192, max_output_tokens=1, input_nanodollars_per_token=1,
        output_nanodollars_per_token=1, monthly_nanodollars=100000, workflow_tokens=20000,
    )
    ledger = BudgetLedger(lambda: sqlite3.connect(path), limits, "deadline", dialect="sqlite-local-test")
    ledger.migrate()
    remaining = iter([4.0, 2.0])
    gate = BudgetLedgerEmbeddingGate(
        ledger, workflow_id="parent", parent_remaining_seconds=lambda: next(remaining),
    )
    ticket = gate.reserve(operation_id="one", request_sha256="a" * 64, input_tokens_upper_bound=20)
    assert ticket.timeout_seconds == 2.0
    expired = BudgetLedgerEmbeddingGate(ledger, workflow_id="expired", parent_remaining_seconds=lambda: 0)
    wire, _, _, embedding, _, _, _ = adapters(settings, repo, finance=expired)
    with pytest.raises(ReviewError, match="parent workflow deadline"):
        embedding.embed("synthetic input", operation_id="expired")
    assert not wire.requests
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM itops_runtime_call").fetchone()[0] == 1


def test_identity_latency_cannot_start_model_call_after_reserved_deadline(settings, repo, monkeypatch):
    from agentic_itops.review import search_delivery

    wire, _, finance, embedding, _, _, _ = adapters(settings, repo)
    clock = iter([10.0, 31.0])
    monkeypatch.setattr(search_delivery, "monotonic", lambda: next(clock))
    with pytest.raises(ReviewError, match="identity authentication"):
        embedding.embed("synthetic input", operation_id="expired-during-auth")
    assert not wire.requests
    assert finance.settlements == [("offline-1", None)]
