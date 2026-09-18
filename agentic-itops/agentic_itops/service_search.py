"""Worker boundary for real hybrid Search with per-workflow embedding finance."""
from __future__ import annotations

from dataclasses import replace

from jsonschema import Draft202012Validator

from .consumer.budget import BudgetLedger, BudgetLimits
from .consumer.contracts import ContractError, canonical, digest, strict_json
from .consumer.sql_connect import managed_identity_sql_factory
from .hosting import _required
from .registration import project_endpoint
from .review.search_delivery import (
    ApprovedKnowledgeRetriever, BudgetLedgerEmbeddingGate, ManagedIdentityEmbedding,
    SEARCH_OUTPUT_SCHEMA, SEARCH_REQUEST_SCHEMA, SearchServiceSettings,
)
from .service_backends import check_context


SEARCH_HANDLER = "itops.approved-knowledge.v1"
SEARCH_FILTER = "approved eq true and revoked eq false"


def approved_search_binding(snapshot_id):
    return {
        "backend": "registered_handler", "handler": SEARCH_HANDLER, "auth": "managed_identity",
        "snapshot_field": "snapshot_id", "snapshot_id": snapshot_id, "filter": SEARCH_FILTER,
        "request_schema": SEARCH_REQUEST_SCHEMA, "output_schema": SEARCH_OUTPUT_SCHEMA,
    }


class WorkflowKnowledge:
    def __init__(self, settings, credential, repository, ledger, scope,
                 *, parent_ledger=None, retriever_type=ApprovedKnowledgeRetriever,
                 embedding_type=ManagedIdentityEmbedding):
        self.settings, self.credential, self.repository = settings, credential, repository
        self.ledger, self.scope = ledger, scope
        self.parent_ledger = parent_ledger
        self.retriever_type, self.embedding_type = retriever_type, embedding_type

    @staticmethod
    def within_context(document, context):
        actual, permitted = document["source"], context.candidate["source"]
        return (all(actual[key] == permitted[key] for key in ("producer_key", "run_id", "domain"))
                and set(actual["surfaces"]).issubset(permitted["surfaces"]))

    def __call__(self, context):
        check_context(context, self.scope.producer.key, self.scope.run_id)
        candidate = context.candidate
        # Retrieval text is deterministic source metadata, not a model-authored
        # command, query, run selection, or prompt instruction.
        query = canonical({
            "industry": "energy_utilities", "domain": candidate["source"]["domain"],
            "surfaces": candidate["source"]["surfaces"], "component": candidate["target"]["component"],
            "unit": candidate["detector"]["unit"], "observed_lifecycle": candidate["observed_lifecycle"],
        }).decode()
        request = {"query": query, "producer_key": self.scope.producer.key,
                   "run_id": self.scope.run_id, "snapshot_id": self.settings.snapshot_id}
        Draft202012Validator(SEARCH_REQUEST_SCHEMA).validate(request)
        embedding = self.embedding_type(
            self.settings, BudgetLedgerEmbeddingGate(
                self.ledger, workflow_id=context.workflow_id,
                parent_remaining_seconds=(lambda: self.parent_ledger.remaining_seconds(context.workflow_id))
                if self.parent_ledger is not None else None),
            credential=self.credential)
        retriever = self.retriever_type(self.settings, embedding, self.repository, credential=self.credential)
        try:
            result = retriever.handle(request)
            Draft202012Validator(SEARCH_OUTPUT_SCHEMA).validate(result)
            # The provider's bounded top-three request is producer/run scoped.
            # Do not expose another review domain's content or rejected IDs.
            result = {**result, "documents": [
                document for document in result["documents"] if self.within_context(document, context)],
                "rejected_documents": []}
            source_ids = {item["event_id"] for item in candidate["evidence_refs"]}
            for document in result["documents"]:
                doc_id = document["doc_id"]
                if doc_id in source_ids:
                    raise ContractError("retrieved document cannot shadow an observation ID")
                previous = context.knowledge.get(doc_id)
                if previous is not None and previous != document:
                    raise ContractError("a retrieved document changed within this immutable workflow")
                context.knowledge[doc_id] = strict_json(canonical(document))
            self.guard(context)
            return result
        finally:
            # SDK clients are per invocation; the explicitly selected shared UAMI
            # credential is owned by the host and must not be closed here.
            retriever.client.close()
            embedding.client.close()

    def guard(self, context):
        check_context(context, self.scope.producer.key, self.scope.run_id)
        retriever = self.retriever_type(self.settings, None, self.repository, credential=self.credential)
        try:
            documents = list(context.knowledge.values())
            if len(documents) > 96:
                raise ContractError("retrieved knowledge exceeds the bounded workflow tool budget")
            if any(not self.within_context(document, context) for document in documents):
                raise ContractError("retrieved knowledge exceeds the workflow domain/surface authority")
            for start in range(0, len(documents), 3):
                retriever.guard_documents(
                    documents[start:start + 3], producer_key=self.scope.producer.key,
                    run_id=self.scope.run_id, snapshot_id=self.settings.snapshot_id)
        finally:
            retriever.client.close()


def create_workflow_knowledge(environment, credential, repository, configuration, binding):
    settings = SearchServiceSettings.from_environment(environment)
    if settings.allowed_sources != frozenset({(configuration.scope.producer.key, configuration.scope.run_id)}):
        raise ContractError("worker Search scopes must equal the single configured observation producer/run")
    if binding != approved_search_binding(settings.snapshot_id):
        raise ContractError("compiled Search contract differs from the real guarded handler")
    raw_budget = strict_json(_required(environment, "ITOPS_EMBEDDING_BUDGET_JSON"))
    limits = BudgetLimits.parse(raw_budget)
    from .investigations import approval, usage_authorization, validate_batch_budgets
    configured = approval(strict_json(environment["ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"])) \
        if "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON" in environment else None
    if limits.usage_mode != configuration.budget.usage_mode:
        raise ContractError("runtime chat and embedding usage modes must match")
    if configured is not None:
        validate_batch_budgets(configuration.budget, limits, configured.get("limits"), configured=configured)
    authorization = usage_authorization(limits, configured, "embedding")
    parent_authorization = usage_authorization(configuration.budget, configured, "chat")
    if limits.deadline_seconds > configuration.budget.deadline_seconds:
        raise ContractError("query embedding deadline cannot exceed the parent reasoning budget")
    factory = managed_identity_sql_factory(
        _required(environment, "ITOPS_SQL_SERVER"), _required(environment, "ITOPS_SQL_DATABASE"),
        _required(environment, "AZURE_CLIENT_ID"))
    ledger = BudgetLedger(factory, limits, _required(environment, "ITOPS_EMBEDDING_BUDGET_SCOPE"),
                          usage_authorization=authorization)
    parent_ledger = BudgetLedger(
        factory, configuration.budget, digest(project_endpoint(_required(environment, "ITOPS_PROJECT_ENDPOINT"))),
        usage_authorization=parent_authorization)
    return WorkflowKnowledge(replace(settings, timeout_seconds=10), credential, repository, ledger,
                             configuration.scope, parent_ledger=parent_ledger)
