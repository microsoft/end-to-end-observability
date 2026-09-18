"""Real, bounded Azure SDK Search/embedding adapters; SQL remains authority.

Production factories select a user-assigned ManagedIdentityCredential explicitly.
Injected credentials/transports support offline genuine-SDK wire tests, not live
service proof. There is no native Foundry Search approval or empty-error fallback.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
import re
from time import monotonic
from typing import Protocol
from uuid import uuid4
import httpx2

from azure.core.exceptions import ResourceNotFoundError
from azure.core.pipeline.policies import SansIOHTTPPolicy
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import SearchIndex
from azure.search.documents.models import VectorizedQuery
from openai import AzureOpenAI

from .contracts import OVERLAY, ReviewError, canonical, digest, fields, identifier, require, run_identifier, source, utc
from .publisher import (
    DeliveryRequest, IndexingResult, MAX_DOCUMENT_BYTES, SearchDeliveryReceipt,
    SearchDocument, canonical_uuid, validate_receipt,
)


INDEX_NAME = "agentic-itops-knowledge"
MODEL = "text-embedding-3-small"
MODEL_VERSION = "1"
DIMENSIONS = 1536
SEARCH_API_VERSION = "2025-09-01"
OPENAI_API_VERSION = "2024-10-21"
SEMANTIC_CONFIGURATION = "knowledge-semantic"
MAX_EMBEDDING_BYTES = 8000
MAX_QUERY_BYTES = 4096
MAX_RESPONSE_BYTES = 4_000_000


def _field(name, field_type="Edm.String", *, searchable=False, filterable=True, key=False):
    return {
        "name": name, "type": field_type, "key": key, "searchable": searchable,
        "filterable": filterable, "retrievable": True, "sortable": False, "facetable": False,
    }


INDEX_SCHEMA = {
    "name": INDEX_NAME,
    "fields": [
        _field("document_id", key=True),
        *[_field(name) for name in (
            "publication_id", "incident_id", "revision", "content_hash", "projection_hash",
            "schema_hash", "snapshot_id", "producer_key", "run_id", "embedding_model",
            "embedding_model_version", "embedding_request_id",
        )],
        _field("analysis_revision", "Edm.Int32"),
        _field("eligible_until", "Edm.DateTimeOffset"),
        _field("approved", "Edm.Boolean"), _field("revoked", "Edm.Boolean"),
        _field("IsSynthetic", "Edm.Boolean"), _field("OperationalUse"),
        _field("execution_allowed", "Edm.Boolean"),
        _field("title", searchable=True, filterable=False),
        _field("content", searchable=True, filterable=False),
        _field("content_json", filterable=False),
        {
            **_field("content_vector", "Collection(Edm.Single)", searchable=True, filterable=False),
            "dimensions": DIMENSIONS, "vectorSearchProfile": "knowledge-vector", "stored": True,
        },
    ],
    "vectorSearch": {
        "algorithms": [{
            "name": "knowledge-hnsw", "kind": "hnsw",
            "hnswParameters": {"metric": "cosine", "m": 4, "efConstruction": 400, "efSearch": 500},
        }],
        "profiles": [{"name": "knowledge-vector", "algorithm": "knowledge-hnsw"}],
    },
    "semantic": {
        "defaultConfiguration": SEMANTIC_CONFIGURATION,
        "configurations": [{
            "name": SEMANTIC_CONFIGURATION,
            "prioritizedFields": {
                "titleField": {"fieldName": "title"},
                "prioritizedContentFields": [{"fieldName": "content"}],
                "prioritizedKeywordsFields": [],
            },
        }],
    },
}
SCHEMA_HASH = digest(INDEX_SCHEMA)
SELECT_FIELDS = tuple(field["name"] for field in INDEX_SCHEMA["fields"] if field["name"] != "content_vector")

SEARCH_REQUEST_SCHEMA = {
    "type": "object", "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": MAX_QUERY_BYTES},
        "producer_key": {"type": "string", "minLength": 1, "maxLength": 180},
        "run_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "snapshot_id": {"type": "string", "minLength": 1, "maxLength": 180},
        "analysis_revision": {"type": "integer", "minimum": 1},
    }, "required": ["query", "producer_key", "run_id", "snapshot_id"], "additionalProperties": False,
}
_TEXT = {"type": "string", "minLength": 1}
_HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
_DOCUMENT_PROPERTIES = {
    **{key: _TEXT for key in ("doc_id", "revision", "publication_id", "snapshot_id", "title", "text",
                             "eligible_until", "retained_until_utc")},
    **{key: _HASH for key in ("content_hash", "sha256", "projection_hash")},
    "analysis_revision": {"type": "integer", "minimum": 1},
    "source": {
        "type": "object", "properties": {
            "producer_key": _TEXT, "run_id": _TEXT, "domain": _TEXT,
            "surfaces": {"type": "array", "minItems": 1, "maxItems": 32, "items": _TEXT},
        }, "required": ["producer_key", "run_id", "domain", "surfaces"], "additionalProperties": False,
    },
    "evidence_refs": {"type": "array", "maxItems": 10000, "items": {
        "type": "object", "properties": {
            "evidence_id": _TEXT, "sha256": _HASH, "retained_until_utc": _TEXT, "verified": {"const": True},
        }, "required": ["evidence_id", "sha256", "retained_until_utc", "verified"], "additionalProperties": False,
    }},
    **{key: {"const": value} for key, value in OVERLAY.items()},
}
SEARCH_DOCUMENT_SCHEMA = {
    "type": "object", "properties": _DOCUMENT_PROPERTIES,
    "required": list(_DOCUMENT_PROPERTIES), "additionalProperties": False,
}
_OUTPUT_PROPERTIES = {
    "status": {"const": "complete"}, "snapshot_id": _TEXT,
    "query_type": {"const": "vector_semantic_hybrid"}, "index_name": {"const": INDEX_NAME},
    "request_id": _TEXT, "embedding_request_id": _TEXT,
    "documents": {"type": "array", "maxItems": 3, "items": SEARCH_DOCUMENT_SCHEMA},
    "rejected_documents": {"type": "array", "maxItems": 3, "items": {
        "type": "object", "properties": {"doc_id": _TEXT, "reason": {"const": "sql-ineligible"}},
        "required": ["doc_id", "reason"], "additionalProperties": False,
    }},
    **{key: {"const": value} for key, value in OVERLAY.items()},
}
SEARCH_OUTPUT_SCHEMA = {
    "type": "object", "properties": _OUTPUT_PROPERTIES,
    "required": list(_OUTPUT_PROPERTIES), "additionalProperties": False,
}


def _required(environment, name):
    value = environment.get(name)
    require(isinstance(value, str) and value.strip() and value == value.strip()
            and not value.startswith("@Microsoft.KeyVault(") and "${" not in value,
            f"{name} is absent or unresolved", 503)
    return value


def _bounded_text(value, maximum, name):
    require(isinstance(value, str) and value.strip() and len(value.encode("utf-8")) <= maximum,
            f"bounded nonempty {name} required")
    return value


def _scope(producer_key, run_id):
    return identifier(producer_key, "producer_key"), run_identifier(run_id)


def _commercial_endpoint(value, suffix, *, trailing_slash=False):
    require(isinstance(value, str) and re.fullmatch(
        r"https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\." + re.escape(suffix) + r"/?", value,
    ), f"explicit commercial Azure HTTPS {suffix} endpoint required")
    return value.rstrip("/") + ("/" if trailing_slash else "")


@dataclass(frozen=True)
class SearchServiceSettings:
    client_id: str
    snapshot_id: str
    allowed_sources: frozenset[tuple[str, str]]
    search_endpoint: str = field(kw_only=True)
    index_name: str = INDEX_NAME
    openai_endpoint: str = field(kw_only=True)
    embedding_deployment: str = MODEL
    embedding_model: str = MODEL
    embedding_model_version: str = MODEL_VERSION
    dimensions: int = DIMENSIONS
    timeout_seconds: int = 20

    def __post_init__(self):
        canonical_uuid(self.client_id)
        identifier(self.snapshot_id, "snapshot_id")
        require(type(self.allowed_sources) is frozenset and 1 <= len(self.allowed_sources) <= 100,
                "explicit bounded server-held source scopes required")
        for pair in self.allowed_sources:
            require(type(pair) is tuple and len(pair) == 2, "source scope pair required")
            _scope(*pair)
        object.__setattr__(self, "search_endpoint", _commercial_endpoint(
            self.search_endpoint, "search.windows.net",
        ))
        object.__setattr__(self, "openai_endpoint", _commercial_endpoint(
            self.openai_endpoint, "openai.azure.com", trailing_slash=True,
        ))
        require(self.index_name == INDEX_NAME, "Search index differs from the approved schema pin")
        require(self.embedding_deployment == MODEL and self.embedding_model == MODEL
                and self.embedding_model_version == MODEL_VERSION
                and type(self.dimensions) is int and self.dimensions == DIMENSIONS,
                "embedding deployment/model/version/dimensions pin mismatch")
        require(type(self.timeout_seconds) is int and 1 <= self.timeout_seconds <= 30,
                "timeout_seconds must be 1..30")

    @classmethod
    def from_environment(cls, environment=None):
        environment = os.environ if environment is None else environment
        raw = _required(environment, "ITOPS_SEARCH_SCOPES_JSON")
        require(len(raw.encode("utf-8")) <= 64_000, "Search scope configuration too large")
        try:
            scopes = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ReviewError("Search scope configuration must be JSON", 503) from exc
        require(type(scopes) is list and 1 <= len(scopes) <= 100, "bounded Search scopes required")
        pairs = []
        for entry in scopes:
            fields(entry, {"producer_key", "run_id"})
            pairs.append(_scope(entry["producer_key"], entry["run_id"]))
        require(len(set(pairs)) == len(pairs), "duplicate Search scopes")
        dimension = _required(environment, "ITOPS_EMBEDDING_DIMENSIONS")
        require(dimension == str(DIMENSIONS), "embedding dimension pin mismatch")
        return cls(
            client_id=_required(environment, "AZURE_CLIENT_ID"),
            snapshot_id=_required(environment, "ITOPS_SEARCH_SNAPSHOT_ID"),
            allowed_sources=frozenset(pairs),
            search_endpoint=_required(environment, "ITOPS_SEARCH_ENDPOINT"),
            index_name=_required(environment, "ITOPS_SEARCH_INDEX"),
            openai_endpoint=_required(environment, "ITOPS_OPENAI_ENDPOINT"),
            embedding_deployment=_required(environment, "ITOPS_EMBEDDING_DEPLOYMENT"),
            embedding_model=_required(environment, "ITOPS_EMBEDDING_MODEL"),
            embedding_model_version=_required(environment, "ITOPS_EMBEDDING_MODEL_VERSION"),
            dimensions=int(dimension),
        )


@dataclass(frozen=True)
class EmbeddingReservation:
    reservation_id: str
    timeout_seconds: float


class EmbeddingFinanceGate(Protocol):
    def reserve(self, *, operation_id: str, request_sha256: str,
                input_tokens_upper_bound: int) -> EmbeddingReservation: ...
    def settle(self, reservation: EmbeddingReservation, *, input_tokens: int | None) -> None: ...


class BudgetLedgerEmbeddingGate:
    """Conservative durable admission using the existing BudgetLedger tables.

    A distinct approved embedding pricing policy/scope must be supplied. Unknown
    calls retain the full reservation; a retry is a new charged provider attempt.
    An optional workflow_id binds query embeddings to a host-owned workflow.
    """
    def __init__(self, ledger, *, workflow_id=None, parent_remaining_seconds=None):
        self.ledger = ledger
        require(parent_remaining_seconds is None or callable(parent_remaining_seconds),
                "parent deadline must be a trusted callable")
        self.parent_remaining_seconds = parent_remaining_seconds
        if workflow_id is not None:
            identifier(workflow_id, "parent embedding workflow ID")
            require(len(workflow_id) <= 128, "parent embedding workflow ID exceeds bound")
        self.workflow_id = None if workflow_id is None else "embedding-" + digest({
            "scope": ledger.scope, "workflow_id": workflow_id,
        })

    def _parent_remaining(self):
        value = self.parent_remaining_seconds()
        require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                "parent workflow deadline is exhausted or invalid", 503)
        return value

    def reserve(self, *, operation_id, request_sha256, input_tokens_upper_bound):
        from ..foundry_contracts import AgentVersionReference

        identifier(operation_id, "embedding operation ID")
        require(type(input_tokens_upper_bound) is int
                and 0 < input_tokens_upper_bound <= self.ledger.limits.model_context_tokens,
                "embedding input exceeds its approved finance token bound", 503)
        if self.parent_remaining_seconds is not None:
            self._parent_remaining()
        workflow = self.workflow_id or "embedding-" + uuid4().hex
        invocation = "embedding-" + uuid4().hex
        self.ledger.open_workflow(workflow)
        self.ledger.reserve(
            workflow, invocation, AgentVersionReference("embedding-" + MODEL, MODEL_VERSION), request_sha256,
        )
        remaining = self.ledger.remaining_seconds(workflow)
        if self.parent_remaining_seconds is not None:
            remaining = min(remaining, self._parent_remaining())
        return EmbeddingReservation(invocation, remaining)

    def settle(self, reservation, *, input_tokens):
        state = self.ledger.settle(
            reservation.reservation_id, input_tokens=input_tokens,
            output_tokens=0 if input_tokens is not None else None,
        )
        require(state != "overrun", "embedding provider exceeded its approved finance bound", 503)


@dataclass(frozen=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    request_id: str
    input_tokens: int


def _request_id(headers):
    value = next((headers[name] for name in ("request-id", "x-ms-request-id", "apim-request-id", "x-request-id")
                  if headers.get(name)), None)
    identifier(value, "real Azure response request ID")
    return value


def _vector(value):
    require(isinstance(value, (list, tuple)) and len(value) == DIMENSIONS, "embedding dimension mismatch", 502)
    require(all(type(number) in (int, float) and math.isfinite(number) for number in value),
            "embedding must contain finite numeric values", 502)
    require(any(number != 0 for number in value), "zero embedding is invalid", 502)
    return tuple(float(number) for number in value)


class ManagedIdentityEmbedding:
    def __init__(self, settings: SearchServiceSettings, finance_gate: EmbeddingFinanceGate, *,
                 credential=None, http_client=None):
        require(callable(getattr(finance_gate, "reserve", None)) and callable(getattr(finance_gate, "settle", None)),
                "explicit durable embedding finance gate required", 503)
        self.settings, self.finance = settings, finance_gate
        self.credential = credential if credential is not None else ManagedIdentityCredential(
            client_id=settings.client_id, connection_timeout=5, read_timeout=5, retry_total=0,
        )
        http_client = http_client if http_client is not None else httpx2.Client(
            timeout=settings.timeout_seconds, follow_redirects=False, trust_env=False,
        )
        self.token_provider = get_bearer_token_provider(
            self.credential, "https://cognitiveservices.azure.com/.default",
        )
        self.client = AzureOpenAI(
            azure_endpoint=settings.openai_endpoint, azure_deployment=settings.embedding_deployment,
            api_version=OPENAI_API_VERSION,
            azure_ad_token_provider=self.token_provider,
            max_retries=0, timeout=settings.timeout_seconds, http_client=http_client,
            _strict_response_validation=True,
        )

    def embed(self, text, *, operation_id):
        _bounded_text(text, MAX_EMBEDDING_BYTES, "embedding input")
        identifier(operation_id, "embedding operation ID")
        bound = len(text.encode("utf-8")) + 1
        payload = {"model": self.settings.embedding_deployment, "input": text,
                   "dimensions": DIMENSIONS, "encoding_format": "float"}
        ticket = self.finance.reserve(
            operation_id=operation_id, request_sha256=digest(payload), input_tokens_upper_bound=bound,
        )
        require(type(ticket) is EmbeddingReservation and isinstance(ticket.reservation_id, str)
                and ticket.reservation_id and type(ticket.timeout_seconds) in (int, float)
                and math.isfinite(ticket.timeout_seconds) and ticket.timeout_seconds > 0,
                "finance gate did not return a bounded reservation", 503)
        observed_overrun_tokens = None
        try:
            deadline = monotonic() + min(self.settings.timeout_seconds, ticket.timeout_seconds)
            token = self.token_provider()
            remaining = deadline - monotonic()
            require(remaining > 0, "embedding deadline expired during identity authentication", 503)
            client = self.client.with_options(azure_ad_token_provider=lambda: token)
            raw = client.with_raw_response.embeddings.create(
                **payload, timeout=remaining,
            )
            require(len(raw.content) <= 200_000, "embedding response exceeds byte bound", 502)
            wire = json.loads(raw.content)
            require(isinstance(wire, dict) and wire.get("object") == "list" and wire.get("model") == MODEL
                    and type(wire.get("data")) is list and len(wire["data"]) == 1,
                    "embedding wire model or batch mismatch", 502)
            item = wire["data"][0]
            require(isinstance(item, dict) and type(item.get("index")) is int and item["index"] == 0
                    and item.get("object") == "embedding", "embedding wire item mismatch", 502)
            vector = _vector(item.get("embedding"))
            usage = wire.get("usage")
            require(isinstance(usage, dict) and type(usage.get("prompt_tokens")) is int
                    and type(usage.get("total_tokens")) is int, "typed embedding usage required", 502)
            response = raw.parse()
            require(response.model == MODEL and len(response.data) == 1 and response.data[0].index == 0,
                    "embedding response model or batch mismatch", 502)
            require(_vector(response.data[0].embedding) == vector, "embedding SDK/wire mismatch", 502)
            tokens = usage["prompt_tokens"]
            require(0 < tokens <= 10**9 and response.usage.total_tokens == tokens,
                    "embedding usage is invalid", 502)
            request_id = _request_id(raw.headers)
            if tokens > bound:
                observed_overrun_tokens = tokens
                raise ReviewError("embedding usage violates its proven input bound", 502)
        except Exception:
            self.finance.settle(ticket, input_tokens=observed_overrun_tokens)
            raise
        self.finance.settle(ticket, input_tokens=tokens)
        return EmbeddingResult(vector, request_id, tokens)

    def close(self):
        self.client.close()
        self.credential.close()


class _BoundedResponse(SansIOHTTPPolicy):
    def on_response(self, request, response):
        body = response.http_response.content
        require(len(body) <= MAX_RESPONSE_BYTES, "Search response exceeds byte bound", 502)
        if 200 <= response.http_response.status_code < 300 and body:
            try:
                value = json.loads(body)
            except ValueError as exc:
                raise ReviewError("Search returned invalid JSON", 502) from exc
            require(isinstance(value, dict), "Search response must be an object", 502)
            require(not value.get("@search.semanticPartialResponseReason"),
                    "Search returned partial semantic results", 502)


def _search_client(settings, credential, transport=None):
    options = {
        "api_version": SEARCH_API_VERSION, "retry_total": 0, "retry_connect": 0,
        "retry_read": 0, "retry_status": 0, "connection_timeout": settings.timeout_seconds,
        "read_timeout": settings.timeout_seconds, "redirect_max": 0, "use_env_settings": False,
        "per_call_policies": [_BoundedResponse()],
    }
    if transport is not None:
        options["transport"] = transport
    return SearchClient(settings.search_endpoint, settings.index_name, credential, **options)


def _document_payload(document):
    require(type(document) is SearchDocument and len(document.content_json.encode("utf-8")) <= MAX_DOCUMENT_BYTES,
            "bounded immutable Search document required")
    try:
        value = json.loads(document.content_json)
    except ValueError as exc:
        raise ReviewError("invalid publication JSON") from exc
    require(isinstance(value, dict) and digest(value) == document.content_hash,
            "publication content hash mismatch")
    require(document.content_json == canonical(value), "publication JSON must use canonical encoding")
    require(value["publication_id"] == document.publication_id
            and value["analysis_revision"] == document.analysis_revision
            and document.document_id == "pub_" + digest([document.publication_id, document.analysis_revision]),
            "immutable publication document identity mismatch")
    source(value["source"])
    require(all(type(value.get(key)) is type(expected) and value.get(key) == expected
                for key, expected in OVERLAY.items()), "synthetic publication markers required")
    return value


def projection_for(settings, document):
    value = _document_payload(document)
    pair = _scope(value["source"]["producer_key"], value["source"]["run_id"])
    require(pair in settings.allowed_sources, "publication outside configured source scopes", 403)
    return {
        "publication_id": document.publication_id, "analysis_revision": document.analysis_revision,
        "producer_key": pair[0], "run_id": pair[1], "snapshot_id": settings.snapshot_id,
        "schema_hash": SCHEMA_HASH, "search_endpoint": settings.search_endpoint, "index_name": settings.index_name,
        "embedding_endpoint": settings.openai_endpoint, "embedding_deployment": settings.embedding_deployment,
        "embedding_model": settings.embedding_model, "embedding_model_version": settings.embedding_model_version,
        "dimensions": settings.dimensions,
    }


def _index_record(settings, document, embedding):
    value = _document_payload(document)
    projection = projection_for(settings, document)
    return {
        "document_id": document.document_id, "publication_id": document.publication_id,
        "incident_id": value["incident_id"], "analysis_revision": document.analysis_revision,
        "revision": str(document.analysis_revision), "content_hash": document.content_hash,
        "projection_hash": digest(projection), "schema_hash": SCHEMA_HASH,
        "snapshot_id": settings.snapshot_id, "producer_key": projection["producer_key"], "run_id": projection["run_id"],
        "embedding_model": MODEL, "embedding_model_version": MODEL_VERSION,
        "embedding_request_id": embedding.request_id, "eligible_until": value["eligible_until"],
        "approved": True, "revoked": False, "title": value["incident_id"],
        "content": value["draft"]["text"], "content_json": document.content_json,
        "content_vector": list(embedding.vector), **OVERLAY,
    }


def _same_record(actual, expected, *, vector=False):
    require(isinstance(actual, Mapping), "Search returned a malformed document", 502)
    for key in SELECT_FIELDS:
        require(key in actual, f"Search document missing {key}", 502)
        if key == "eligible_until":
            received = actual[key].isoformat() if isinstance(actual[key], datetime) else actual[key]
            require(utc(received) == utc(expected[key]), "Search readback expiry mismatch", 502)
        else:
            require(type(actual[key]) is type(expected[key]) and actual[key] == expected[key],
                    f"Search readback {key} mismatch", 502)
    if vector:
        returned = _vector(actual.get("content_vector"))
        require(all(math.isclose(left, right, rel_tol=1e-5, abs_tol=1e-6)
                    for left, right in zip(returned, expected["content_vector"])),
                "Search readback vector mismatch", 502)


class AzureSearchDelivery:
    def __init__(self, settings, embedding, review_repository, *, credential=None, transport=None):
        self.settings, self.embedding, self.repository = settings, embedding, review_repository
        self.credential = credential if credential is not None else ManagedIdentityCredential(
            client_id=settings.client_id, connection_timeout=5, read_timeout=5, retry_total=0,
        )
        self.client = _search_client(settings, self.credential, transport)

    def projection_for(self, document):
        return projection_for(self.settings, document)

    def _write(self, operation, document_id, operation_id, documents):
        headers = {}
        def capture(response):
            headers.update(response.http_response.headers)
            if response.http_response.status_code in (200, 207):
                wire = json.loads(response.http_response.content)
                require(type(wire.get("value")) is list and len(wire["value"]) == 1,
                        "one actual Search wire indexing result required", 502)
                item = wire["value"][0]
                require(isinstance(item, dict) and type(item.get("status")) is bool
                        and type(item.get("statusCode")) is int and isinstance(item.get("key"), str),
                        "typed Search wire indexing result required", 502)
        method = self.client.upload_documents if operation == "upload" else self.client.delete_documents
        results = method(documents, raw_response_hook=capture)
        require(type(results) is list and len(results) == 1, "one actual Search indexing result required", 502)
        result = results[0]
        receipt = SearchDeliveryReceipt(
            operation_id, operation, _request_id(headers),
            (IndexingResult(result.key, result.succeeded, result.status_code, result.error_message),),
        )
        validate_receipt(DeliveryRequest(operation_id, operation, document_id, None), receipt)
        return receipt

    def upload(self, document, *, operation_id):
        value = _document_payload(document)
        self.projection_for(document)
        embedding = self.embedding.embed(value["draft"]["text"], operation_id=operation_id)
        # Model latency must not turn a now-revoked publication into a new write.
        self.repository.publication_for_publish(document.publication_id)
        record = _index_record(self.settings, document, embedding)
        receipt = self._write("upload", document.document_id, operation_id, [record])
        readback = self.client.get_document(document.document_id, selected_fields=list(SELECT_FIELDS) + ["content_vector"])
        _same_record(readback, record, vector=True)
        return receipt

    def delete(self, document_id, *, operation_id):
        require(isinstance(document_id, str) and len(document_id) == 68 and document_id.startswith("pub_")
                and all(c in "0123456789abcdef" for c in document_id[4:]), "immutable Search document ID required")
        receipt = self._write("delete", document_id, operation_id, [{"document_id": document_id}])
        try:
            self.client.get_document(document_id, selected_fields=["document_id"])
        except ResourceNotFoundError as exc:
            require(exc.status_code == 404, "Search did not confirm document absence", 502)
            return receipt
        raise ReviewError("Search deletion is not yet visible in document readback", 502)

    def close(self):
        self.client.close()
        self.credential.close()


def _quote(value):
    return "'" + value.replace("'", "''") + "'"


def _artifact_document(value, document_id, content_hash, projection_hash, snapshot_id):
    return {
        "doc_id": document_id, "revision": str(value["analysis_revision"]),
        "publication_id": value["publication_id"], "analysis_revision": value["analysis_revision"],
        "content_hash": content_hash, "sha256": content_hash, "projection_hash": projection_hash,
        "snapshot_id": snapshot_id, "title": value["incident_id"], "text": value["draft"]["text"],
        "source": value["source"], "evidence_refs": value["evidence_refs"],
        "eligible_until": value["eligible_until"], "retained_until_utc": value["eligible_until"], **OVERLAY,
    }


class ApprovedKnowledgeRetriever:
    def __init__(self, settings, embedding, review_repository, *, credential=None, transport=None):
        self.settings, self.embedding, self.repository = settings, embedding, review_repository
        self.credential = credential if credential is not None else ManagedIdentityCredential(
            client_id=settings.client_id, connection_timeout=5, read_timeout=5, retry_total=0,
        )
        self.client = _search_client(settings, self.credential, transport)

    def _scope(self, producer_key, run_id, snapshot_id):
        pair = _scope(producer_key, run_id)
        require(pair in self.settings.allowed_sources, "retrieval outside configured source scopes", 403)
        require(snapshot_id == self.settings.snapshot_id, "retrieval snapshot differs from approved binding", 403)
        return pair

    def _page(self, query, vector, *, producer_key, run_id, snapshot_id, analysis_revision=None):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        clauses = [
            f"snapshot_id eq {_quote(snapshot_id)}", f"producer_key eq {_quote(producer_key)}",
            f"run_id eq {_quote(run_id)}", f"schema_hash eq '{SCHEMA_HASH}'",
            "approved eq true", "revoked eq false", "IsSynthetic eq true",
            "OperationalUse eq 'prohibited'", "execution_allowed eq false", f"eligible_until gt {now}",
        ]
        if analysis_revision is not None:
            clauses.append(f"analysis_revision eq {analysis_revision}")
        response_headers = {}
        def capture(response):
            response_headers.update(response.http_response.headers)
        results = self.client.search(
            search_text=query, vector_queries=[VectorizedQuery(
                vector=list(vector), k_nearest_neighbors=50, fields="content_vector",
            )], query_type="semantic", semantic_configuration_name=SEMANTIC_CONFIGURATION,
            semantic_error_mode="fail",
            vector_filter_mode="preFilter", top=3, filter=" and ".join(f"({clause})" for clause in clauses),
            search_fields=["title", "content"], select=list(SELECT_FIELDS), minimum_coverage=100,
            raw_response_hook=capture,
        )
        pages = results.by_page()
        page = next(pages)
        hits = []
        for hit in page:
            require(len(hits) < 3, "Search exceeded top-three result bound", 502)
            hits.append(hit)
        require(pages.continuation_token is None, "Search unexpectedly requested another page", 502)
        request_id = _request_id(response_headers)
        return hits, request_id

    def probe(self, *, producer_key, run_id, snapshot_id):
        # This verifies the real request contract, not embedding quality or published knowledge.
        self._scope(producer_key, run_id, snapshot_id)
        hits, request_id = self._page(
            "Synthetic dependency request-contract readiness", (1.0,) + (0.0,) * (DIMENSIONS - 1),
            producer_key=producer_key, run_id=run_id, snapshot_id=snapshot_id)
        return {
            "status": "verified", "probe_kind": "fixed_vector_contract_only",
            "request_id": request_id, "returned_hit_count": len(hits),
            "inference_performed": False, "embedding_performed": False,
        }

    def retrieve(self, query, *, producer_key, run_id, snapshot_id, analysis_revision=None):
        self._scope(producer_key, run_id, snapshot_id)
        _bounded_text(query, MAX_QUERY_BYTES, "Search query")
        require(analysis_revision is None or type(analysis_revision) is int and analysis_revision > 0,
                "positive exact analysis revision required")
        embedding = self.embedding.embed(query, operation_id="query-" + uuid4().hex)
        hits, request_id = self._page(
            query, embedding.vector, producer_key=producer_key, run_id=run_id,
            snapshot_id=snapshot_id, analysis_revision=analysis_revision)
        documents, rejected = [], []
        seen = set()
        for hit in hits:
            require(isinstance(hit, Mapping) and set(SELECT_FIELDS) <= hit.keys(),
                    "Search hit does not match the approved schema", 502)
            require(hit["document_id"] not in seen, "duplicate Search document", 502)
            seen.add(hit["document_id"])
            rank = hit.get("@search.rerankerScore")
            require(type(rank) in (int, float) and math.isfinite(rank), "semantic reranker evidence missing", 502)
            require(type(hit["analysis_revision"]) is int and hit["analysis_revision"] > 0,
                    "invalid Search revision", 502)
            identifier(hit["embedding_request_id"], "stored embedding request ID")
            candidate = SearchDocument(hit["document_id"], hit["publication_id"], hit["analysis_revision"],
                                       hit["content_json"], hit["content_hash"])
            value = _document_payload(candidate)
            expected = _index_record(
                self.settings, candidate, EmbeddingResult((), hit["embedding_request_id"], 0),
            )
            _same_record(hit, expected)
            require(value["source"]["producer_key"] == producer_key and value["source"]["run_id"] == run_id
                    and (analysis_revision is None or candidate.analysis_revision == analysis_revision),
                    "Search returned a document outside the exact source/revision filter", 502)
            try:
                authoritative = self.repository.publication_for_retrieval(
                    candidate.publication_id, candidate.document_id, candidate.content_hash,
                    snapshot_id=snapshot_id, producer_key=producer_key, run_id=run_id,
                    analysis_revision=candidate.analysis_revision, projection_hash=hit["projection_hash"],
                )
            except ReviewError as exc:
                if exc.status != 409 or str(exc) not in {
                    "publication is ineligible", "publication lineage is no longer eligible",
                    "Search projection is not accepted",
                }:
                    raise
                rejected.append({"doc_id": candidate.document_id, "reason": "sql-ineligible"})
                continue
            documents.append(_artifact_document(
                authoritative, candidate.document_id, candidate.content_hash, hit["projection_hash"], snapshot_id,
            ))
        return {
            "status": "complete", "snapshot_id": snapshot_id, "query_type": "vector_semantic_hybrid",
            "index_name": self.settings.index_name, "request_id": request_id,
            "embedding_request_id": embedding.request_id, "documents": documents,
            "rejected_documents": rejected, **OVERLAY,
        }

    def handle(self, request):
        fields(request, {"query", "producer_key", "run_id", "snapshot_id"}, {"analysis_revision"})
        return self.retrieve(**request)

    def guard_documents(self, documents, *, producer_key, run_id, snapshot_id):
        self._scope(producer_key, run_id, snapshot_id)
        require(type(documents) is list and len(documents) <= 3, "bounded knowledge documents required")
        seen = set()
        for document in documents:
            fields(document, set(_DOCUMENT_PROPERTIES))
            require(document["doc_id"] not in seen, "duplicate runtime knowledge document")
            seen.add(document["doc_id"])
            value = self.repository.publication_for_retrieval(
                document["publication_id"], document["doc_id"], document["content_hash"],
                snapshot_id=snapshot_id, producer_key=producer_key, run_id=run_id,
                analysis_revision=document["analysis_revision"], projection_hash=document["projection_hash"],
            )
            require(document == _artifact_document(
                value, document["doc_id"], document["content_hash"], document["projection_hash"], snapshot_id,
            ), "runtime knowledge differs from its authoritative revision", 409)

    def close(self):
        self.client.close()
        self.credential.close()


def knowledge_index():
    return SearchIndex(deepcopy(INDEX_SCHEMA))


def _schema_matches(actual):
    if any(actual.get(name) not in (None, []) for name in (
        "scoringProfiles", "suggesters", "analyzers", "normalizers", "tokenizers", "tokenFilters", "charFilters",
    )) or actual.get("defaultScoringProfile") is not None or actual.get("corsOptions") is not None:
        return False
    def subset(expected, received):
        if isinstance(expected, dict):
            return isinstance(received, dict) and all(key in received and subset(value, received[key])
                                                     for key, value in expected.items())
        if isinstance(expected, list):
            return isinstance(received, list) and len(expected) == len(received) \
                and all(subset(a, b) for a, b in zip(expected, received))
        return type(expected) is type(received) and expected == received
    return subset(INDEX_SCHEMA, actual)


def bootstrap_index(settings, credential, *, approval_reference, expected_schema_hash, transport=None):
    """Explicit parent-approved bootstrap only. Never called by a service host."""
    identifier(approval_reference, "index bootstrap approval reference")
    require(expected_schema_hash == SCHEMA_HASH, "index schema approval hash mismatch")
    options = {"api_version": SEARCH_API_VERSION, "retry_total": 0, "redirect_max": 0,
               "connection_timeout": settings.timeout_seconds, "read_timeout": settings.timeout_seconds}
    if transport is not None:
        options["transport"] = transport
    with SearchIndexClient(settings.search_endpoint, credential, **options) as client:
        created = False
        try:
            actual = client.get_index(settings.index_name)
        except ResourceNotFoundError as exc:
            require(exc.status_code == 404, "index existence was not confirmed", 502)
            client.create_index(knowledge_index())
            actual = client.get_index(settings.index_name)
            created = True
        require(_schema_matches(actual.as_dict()), "existing Search schema differs; explicit migration required", 409)
        return {"index_name": settings.index_name, "schema_hash": SCHEMA_HASH,
                "created": created, "approval_reference": approval_reference}
