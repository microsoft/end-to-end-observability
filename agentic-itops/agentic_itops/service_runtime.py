"""Production factories: explicit UAMI, fixed source, SQL authority, no polling."""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path, PureWindowsPath
import re
from uuid import UUID, uuid4

import httpx

from .application_jobs import ApplicationJobs
from .application_results import ApplicationResults
from .consumer.budget import BudgetLedger, BudgetLimits, MonthlyLimitAmendment
from .consumer.contracts import ContractError, ProducerScope, canonical, digest, strict_json, utc
from .consumer.detectors import DetectorPolicy
from .consumer.evidence import AzureEvidenceStore
from .consumer.logs import AzureLogsTransport, LogsReader
from .consumer.processing import AnalysisScope
from .consumer.responses import OpenAIResponsesTransport
from .consumer.sql_connect import managed_identity_sql_factory
from .consumer.storage import SqlServerWorkflowStore
from .consumer.storage_contract import EvidenceRef, IntegrityConflict
from .consumer.worker import ConsumerWorker
from .hosting import _required, verify_sql_native_runtime
from .portfolio import compile_portfolio
from .registration import RegistrationPlan, bind_registered_runtime_agents, project_endpoint
from .review import SqlServerReviewRepository
from .review.contracts import ReviewError
from .runtime import PortfolioRuntime, WorkflowContext
from .service_auth import ServiceAuthenticator
from .service_backends import CONTEXT_REQUEST, MONITOR_OPERATIONS, OVERLAY, EvidenceBackends, RemoteEvidenceTools, TrustedDispatch, backend_request


@dataclass(frozen=True)
class RuntimeConfiguration:
    scope: AnalysisScope
    workspace_id: str
    retained_until: datetime
    policies: tuple
    budget: BudgetLimits
    gate_policy: dict

    @classmethod
    def parse(cls, value, *, allow_expired=False):
        if not isinstance(value, dict) or set(value) != {
                "schema_version", "source", "evidence_retained_until", "detector_policies",
                "approved_detector_policy_hashes", "budget", "gate_policy"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ContractError("explicit runtime configuration version/fields required")
        source = value["source"]
        if not isinstance(source, dict) or set(source) != {
                "workspace_resource_id", "workspace_id", "run_id", "surfaces", "baseline_start",
                "reference_time", "ingestion_start", "ingestion_end"}:
            raise ContractError("explicit producer and bounded source/run windows required")
        scope = AnalysisScope(
            ProducerScope(source["workspace_resource_id"]), source["run_id"], tuple(source["surfaces"]),
            source["baseline_start"], source["reference_time"], source["ingestion_start"], source["ingestion_end"])
        if scope.ingestion_end - scope.ingestion_start > timedelta(hours=2):
            raise ContractError("HTTP-operated ingestion batch is limited to eight 15-minute slices")
        if max(scope.reference_time, scope.ingestion_end) > datetime.now(timezone.utc):
            raise ContractError("retained source window cannot end in the future")
        retained_until = utc(value["evidence_retained_until"])
        if not allow_expired and retained_until <= datetime.now(timezone.utc):
            raise ContractError("configured immutable evidence retention has expired")
        policies = []
        approvals = value["approved_detector_policy_hashes"]
        if (not isinstance(approvals, list) or len(set(approvals)) != len(approvals)
                or any(not isinstance(item, str) or not re.fullmatch("[a-f0-9]{64}", item) for item in approvals)):
            raise ContractError("approved detector policies require exact externally approved content hashes")
        if not isinstance(value["detector_policies"], list):
            raise ContractError("detector policy array required; empty remains disabled")
        for raw in value["detector_policies"]:
            raw = dict(raw)
            if "allowed_quality" in raw:
                raw["allowed_quality"] = tuple(raw["allowed_quality"])
            policy = DetectorPolicy(**raw)
            if policy.surface not in scope.surfaces:
                raise ContractError("detector policy is outside the server-held surface set")
            if policy.enabled and policy.sha256 not in approvals:
                raise ContractError("enabled detector policy has no exact external approval")
            policies.append(policy)
        if set(approvals) - {policy.sha256 for policy in policies if policy.enabled}:
            raise ContractError("unused or mismatched detector approval")
        budget = BudgetLimits.parse(value["budget"])
        if budget.deadline_seconds > 180:
            raise ContractError("HTTP reasoning budget deadline must be at most 180 seconds")
        if not budget.caps_enforced and budget.max_output_tokens > 8192:
            raise ContractError("observe-only test usage retains the 8192-token output ceiling")
        if not isinstance(value["gate_policy"], dict):
            raise ContractError("explicit deterministic gate policy object required")
        workspace_id = UUID(source["workspace_id"])
        if workspace_id.int == 0:
            raise ContractError("workspace customer ID cannot be a placeholder")
        return cls(scope, str(workspace_id), retained_until, tuple(policies),
                   budget, strict_json(canonical(value["gate_policy"])))


def _document(environment, name, root):
    path = Path(_required(environment, name))
    path = path if path.is_absolute() else root / path
    if path.stat().st_size > 2_000_000:
        raise ContractError("deployment artifact exceeds supported bound")
    return strict_json(path.read_bytes())


def _archived_configurations(environment, root, active):
    raw = environment.get("ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON", "[]")
    if not isinstance(raw, str) or len(raw) > 8192:
        raise ContractError("bounded archived runtime path array required")
    paths = strict_json(raw)
    if not isinstance(paths, list) or len(paths) > 8:
        raise ContractError("at most eight archived runtime artifacts are permitted")
    seen = {(active.scope.producer.key, active.scope.run_id)} if paths else set()
    archives = []
    root = root.resolve()
    for value in paths:
        if (not isinstance(value, str) or not value or len(value) > 512
                or PureWindowsPath(value).drive or PureWindowsPath(value).root
                or ".." in PureWindowsPath(value).parts):
            raise ContractError("archive paths must be relative deployment artifacts")
        path = (root / value.replace("\\", "/")).resolve()
        if not path.is_relative_to(root):
            raise ContractError("archive path escapes deployment artifacts")
        config = RuntimeConfiguration.parse(
            _document({"archive": str(path)}, "archive", root), allow_expired=True)
        key = (config.scope.producer.key, config.scope.run_id)
        if key in seen:
            raise ContractError("duplicate active/archive producer and run")
        if (config.scope.producer.key != active.scope.producer.key
                or config.workspace_id != active.workspace_id):
            raise ContractError("archives must use the active producer and workspace")
        # Preserve historical policy provenance. Recovery services below receive
        # no detector policies and archive routing permits only exact projection.
        seen.add(key)
        archives.append(config)
    return tuple(archives)


def _authorized_scope(active, payload, archives=()):
    source = payload.get("source", payload)
    if (not isinstance(source, dict)
            or any(payload.get(key) != value for key, value in OVERLAY.items())):
        raise ContractError("job differs from configured non-operational source/run")
    key = (source.get("producer_key"), source.get("run_id"))
    if key == (active.producer.key, active.run_id):
        return active
    for config in archives:
        if key == (config.scope.producer.key, config.scope.run_id):
            # Do not renew old evidence retention or let its expiry break active work.
            if config.retained_until <= datetime.now(timezone.utc):
                raise ContractError("archived evidence retention has expired")
            return config.scope
    raise ContractError("job differs from configured non-operational source/run")


def _identity(environment):
    from azure.identity import ManagedIdentityCredential
    client_id = str(UUID(_required(environment, "AZURE_CLIENT_ID")))
    if UUID(client_id).int == 0:
        raise ContractError("explicit nonzero user-assigned identity required")
    return ManagedIdentityCredential(client_id=client_id, retry_total=0, connection_timeout=5, read_timeout=5)


def _evidence(environment, credential):
    from azure.storage.blob import ContainerClient
    endpoint = _required(environment, "ITOPS_EVIDENCE_BLOB_ENDPOINT")
    container = _required(environment, "ITOPS_EVIDENCE_CONTAINER")
    if not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net/?", endpoint):
        raise ContractError("explicit commercial evidence Blob endpoint required")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]", container):
        raise ContractError("explicit evidence container required")
    return AzureEvidenceStore(ContainerClient(
        endpoint, container, credential=credential, retry_total=0,
        connection_timeout=5, read_timeout=10))


def _common(environment, root):
    config = RuntimeConfiguration.parse(_document(environment, "ITOPS_RUNTIME_CONFIG_PATH", root))
    bindings = _document(environment, "ITOPS_BINDINGS_PATH", root)
    portfolio = compile_portfolio(root, bindings, mode="trusted_dispatch")
    if not portfolio.bindings_complete:
        raise ContractError("production portfolio bindings are incomplete")
    credential = _identity(environment)
    verify_sql_native_runtime()
    factory = managed_identity_sql_factory(
        _required(environment, "ITOPS_SQL_SERVER"), _required(environment, "ITOPS_SQL_DATABASE"),
        _required(environment, "AZURE_CLIENT_ID"))
    store = SqlServerWorkflowStore(factory)
    auth = ServiceAuthenticator(
        _required(environment, "ENTRA_TENANT_ID"), _required(environment, "ENTRA_AUDIENCE"),
        strict_json(_required(environment, "ITOPS_SERVICE_CALLERS_JSON")))
    return config, portfolio, credential, factory, store, auth, _evidence(environment, credential)


class WorkerService:
    def __init__(self, worker, jobs, auth):
        self.worker, self.jobs, self.auth = worker, jobs, auth

    def invoke(self, headers, kind, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if body != {}:
            raise ContractError("work calls only select a bounded preconfigured job kind")
        if kind not in {"analysis", "ingest", "coverage", "detector", "reasoning", "projection",
                        "postmortem", "hygiene", "investigation", "replacement"}:
            raise ContractError("unsupported work kind")
        processed = self.worker.process_once(kind)
        return {"status": "processed" if processed else "no_queued_work", "kind": kind, **OVERLAY}

    def postmortem(self, headers, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if set(body) != {"incident_id"}:
            raise ContractError("lifecycle accepts only an incident ID, never a claimed review state")
        return {"job_id": self.jobs.enqueue_postmortem(body["incident_id"]), "status": "queued", **OVERLAY}

    def projection(self, headers, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if set(body) != {"workflow_id"}:
            raise ContractError("projection retry accepts only a durable workflow identity")
        job_id = self.jobs.enqueue_projection(body["workflow_id"], headers.get("Idempotency-Key"))
        return {"job_id": job_id, "status": "queued", **OVERLAY}

    def replacement(self, headers, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if set(body) != {"workflow_id", "actor_oid"}:
            raise ContractError("replacement accepts only the failed workflow and authenticated operator")
        job_id = self.jobs.enqueue_replacement(body["workflow_id"], body["actor_oid"])
        return {"job_id": job_id, "status": "queued", **OVERLAY}

    def job(self, headers, job_id):
        self.auth.authenticate(headers, "Worker.Invoke")
        value = self.worker.store.get_job(job_id)
        self.jobs._scope(value["payload"])
        return {"job_id": value["job_id"], "kind": value["kind"], "status": value["status"],
                "result": value["result"], "next_job_ids": list(value["next_job_ids"]),
                "error_code": value["error_code"], **OVERLAY}

    def advance(self, headers, job_id, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if body != {}:
            raise ContractError("exact job advancement accepts only an empty body")
        value = self.worker.store.get_job(job_id)
        self.jobs._scope(value["payload"])
        self.worker.process_once(value["kind"], job_id=job_id)
        return self.job(headers, job_id)


class ProjectionWorkerService(WorkerService):
    def invoke(self, headers, kind, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if kind != "projection":
            raise ContractError("model-free worker only executes projection jobs")
        return super().invoke(headers, kind, body)

    def advance(self, headers, job_id, body):
        if self.job(headers, job_id)["kind"] != "projection":
            raise ContractError("model-free worker only executes projection jobs")
        return super().advance(headers, job_id, body)


MODEL_FREE_WORK_KINDS = frozenset({"analysis", "ingest", "coverage", "detector", "projection", "replacement"})
PAID_WORK_KINDS = frozenset({"reasoning", "postmortem", "hygiene", "investigation"})


class ModelFreeWorkerService(WorkerService):
    def __init__(self, worker, jobs, auth, archives, archived_services, *, projection_only=False):
        super().__init__(worker, jobs, auth)
        self.archives, self.archived_services = archives, archived_services
        self.allowed_kinds = frozenset({"projection"}) if projection_only else MODEL_FREE_WORK_KINDS

    def _selected(self, payload):
        scope = _authorized_scope(self.jobs.scope, payload, self.archives)
        return self if scope == self.jobs.scope else self.archived_services[scope.run_id]

    def _job(self, headers, job_id):
        self.auth.authenticate(headers, "Worker.Invoke")
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{64}", job_id):
            raise ContractError("exact durable job ID required")
        value = self.worker.store.get_job(job_id)
        return value, self._selected(value["payload"])

    def job(self, headers, job_id):
        _, selected = self._job(headers, job_id)
        return WorkerService.job(selected, headers, job_id)

    def advance_kind(self, headers, job_id):
        value, selected = self._job(headers, job_id)
        if selected is not self and value["kind"] != "projection":
            raise ContractError("archived work permits only persisted-result projection")
        if value["kind"] not in MODEL_FREE_WORK_KINDS | PAID_WORK_KINDS:
            raise ContractError("unsupported work kind")
        return value["kind"]

    def advance(self, headers, job_id, body):
        kind = self.advance_kind(headers, job_id)
        if kind not in self.allowed_kinds:
            raise ContractError("model-free worker only executes " + ", ".join(sorted(self.allowed_kinds)) + " jobs")
        _, selected = self._job(headers, job_id)
        return WorkerService.advance(selected, headers, job_id, body)

    def invoke(self, headers, kind, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if kind not in self.allowed_kinds:
            raise ContractError("model-free worker only executes " + ", ".join(sorted(self.allowed_kinds)) + " jobs")
        # Unscoped claims are active-only, including projection.
        return super().invoke(headers, kind, body)

    def postmortem(self, headers, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        raise ContractError("model-free worker cannot enqueue lifecycle work")

    def projection(self, headers, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if set(body) != {"workflow_id"}:
            raise ContractError("projection retry accepts only a durable workflow identity")
        row = self.jobs.results.get(body["workflow_id"])
        selected = self._selected({
            "producer_key": row["producer_key"], "run_id": row["run_id"], **OVERLAY})
        return WorkerService.projection(selected, headers, body)

    def replacement(self, headers, body):
        self.auth.authenticate(headers, "Worker.Invoke")
        if "replacement" not in self.allowed_kinds or set(body) != {"workflow_id", "actor_oid"}:
            raise ContractError("only active model-free service may prepare an exact replacement")
        row = self.jobs.results.get(body["workflow_id"])
        if self._selected({"producer_key": row["producer_key"], "run_id": row["run_id"], **OVERLAY}) is not self:
            raise ContractError("archived work is not eligible for replacement")
        return WorkerService.replacement(self, headers, body)


def _replacement_hooks(environment, config, factory):
    def load():
        from .investigations import approval, validate_batch_budgets, validate_detector_configuration
        configured = approval(strict_json(_required(environment, "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON")))
        validate_batch_budgets(config.budget, BudgetLimits.parse(strict_json(
            _required(environment, "ITOPS_EMBEDDING_BUDGET_JSON"))), configured.get("limits"),
            configured=configured)
        validate_detector_configuration(configured, config.scope, config.policies)
        return configured

    def inspect(workflow_id, configured):
        from .replacement import require_capacity
        chat = BudgetLedger(factory, config.budget, digest(project_endpoint(
            _required(environment, "ITOPS_PROJECT_ENDPOINT"))))
        embedding = BudgetLedger(factory, BudgetLimits(**strict_json(
            _required(environment, "ITOPS_EMBEDDING_BUDGET_JSON"))),
            _required(environment, "ITOPS_EMBEDDING_BUDGET_SCOPE"))
        return require_capacity(chat, embedding, workflow_id, configured)

    return load, inspect


def _create_model_free_worker_service(environment=None, *, root=None, projection_only=False):
    from .service_projection import RemoteResultProjector
    environment = os.environ if environment is None else environment
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    config, _, credential, factory, store, auth, evidence = _common(environment, root)
    archives = _archived_configurations(environment, root, config)
    client = httpx.Client(timeout=15, follow_redirects=False)
    projector = RemoteResultProjector(
        _required(environment, "ITOPS_REVIEW_ORIGIN"), _required(environment, "ITOPS_PROJECTION_AUDIENCE"),
        credential, client, token_scope=_required(environment, "ITOPS_PROJECTION_TOKEN_SCOPE"))
    results, repository = ApplicationResults(store), SqlServerReviewRepository(factory)
    reader = LogsReader(config.scope.producer, config.workspace_id, AzureLogsTransport(credential, client))

    def build(selected, *, recovery=False):
        load, inspect = _replacement_hooks(environment, selected, factory)
        jobs = ApplicationJobs(store, evidence, results, repository, None,
                               selected.scope, selected.retained_until, projector=projector,
                               detector_policies=() if recovery or projection_only else selected.policies,
                               replacement_approval_loader=load, replacement_accounting=inspect)
        handlers = {"projection": jobs.project}
        if not recovery and not projection_only:
            handlers["analysis"] = jobs.analysis
            handlers["replacement"] = jobs.replacement
        worker = ConsumerWorker(
            store, evidence, reader, {selected.scope.run_id: selected.scope},
            [] if recovery or projection_only else list(selected.policies), uuid4().hex,
            handlers=handlers, max_query_attempts=2, max_ingestion_seconds=90)
        return ProjectionWorkerService(worker, jobs, auth)

    active = build(config)
    archived_services = {item.scope.run_id: build(item, recovery=True) for item in archives}
    return ModelFreeWorkerService(active.worker, active.jobs, auth, archives, archived_services,
                                  projection_only=projection_only)


def create_worker_projection_service(environment=None, *, root=None):
    """Compatibility factory for exact reads and model-free persisted-output recovery."""
    return _create_model_free_worker_service(environment, root=root, projection_only=True)


def create_worker_model_free_service(environment=None, *, root=None):
    """Active telemetry/detection plus exact archived reads and projection; no models."""
    return _create_model_free_worker_service(environment, root=root)


def _diagnostic_job(scope, store, auth, headers, job_id, archives=()):
    auth.authenticate(headers, "Worker.Invoke")
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{64}", job_id):
        raise ContractError("exact model workflow job ID required")
    job = store.get_job(job_id)
    payload = job["payload"]
    _authorized_scope(scope, payload, archives)
    if job["kind"] not in {"reasoning", "postmortem", "hygiene"}:
        raise ContractError("budget inspection is outside the configured model-work scope")
    return job


class WorkerBudgetDiagnostics:
    def __init__(self, scope, store, auth, chat, embedding, *, archives=()):
        self.scope, self.store, self.auth = scope, store, auth
        self.chat, self.embedding = chat, embedding
        self.archives = archives

    def read(self, headers, job_id):
        from .review.search_delivery import BudgetLedgerEmbeddingGate
        _diagnostic_job(self.scope, self.store, self.auth, headers, job_id, self.archives)
        embedding_id = BudgetLedgerEmbeddingGate(self.embedding, workflow_id=job_id).workflow_id
        return {
            "job_id": job_id, "chat": self.chat.inspect_workflow(job_id, include_dispatches=True),
            "embedding": self.embedding.inspect_workflow(embedding_id),
            "scope_totals": {"chat": self.chat.inspect_scope(), "embedding": self.embedding.inspect_scope()},
            "monthly_limits": {"chat": self.chat.inspect_month(), "embedding": self.embedding.inspect_month()},
            **OVERLAY,
        }


class WorkerDependencyReadiness:
    def __init__(self, scope, store, auth, evidence, search_probe, projection_probe):
        self.scope, self.store, self.auth = scope, store, auth
        self.evidence, self.search_probe = evidence, search_probe
        self.projection_probe = projection_probe

    def read(self, headers, job_id):
        job = _diagnostic_job(self.scope, self.store, self.auth, headers, job_id)
        if "candidate_evidence" not in job["payload"] or "incident_id" not in job["payload"]:
            raise ContractError("readiness requires a prepared candidate-bearing model job")
        reference = EvidenceRef(**job["payload"]["candidate_evidence"])
        candidate = strict_json(self.evidence.read(reference))
        source = candidate["source"]
        if (source["producer_key"] != self.scope.producer.key or source["run_id"] != self.scope.run_id
                or candidate["incident_id"] != job["payload"]["incident_id"]
                or any(candidate.get(key) != value for key, value in OVERLAY.items())):
            raise ContractError("readiness evidence differs from the authorized job")
        projection = self.projection_probe()
        search = self.search_probe()
        return {"job_id": job_id, "status": "verified", "sql_job_readable": True,
                "candidate_evidence_sha256": reference.sha256, "search": search,
                "projection": projection, **OVERLAY}


def create_worker_dependency_readiness(environment=None, *, root=None):
    from .review.search_delivery import ApprovedKnowledgeRetriever, SearchServiceSettings
    from .service_projection import RemoteResultProjector
    environment = os.environ if environment is None else environment
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    config, _, credential, _, store, auth, evidence = _common(environment, root)
    settings = replace(SearchServiceSettings.from_environment(environment), timeout_seconds=10)
    if settings.allowed_sources != frozenset({(config.scope.producer.key, config.scope.run_id)}):
        raise ContractError("readiness Search scopes differ from the configured source")

    def search_probe():
        retriever = ApprovedKnowledgeRetriever(settings, None, None, credential=credential)
        try:
            return retriever.probe(producer_key=config.scope.producer.key,
                                   run_id=config.scope.run_id, snapshot_id=settings.snapshot_id)
        finally:
            retriever.client.close()

    origin = _required(environment, "ITOPS_REVIEW_ORIGIN")
    audience = _required(environment, "ITOPS_PROJECTION_AUDIENCE")
    token_scope = _required(environment, "ITOPS_PROJECTION_TOKEN_SCOPE")

    def projection_probe():
        with httpx.Client(transport=httpx.HTTPTransport(retries=0)) as client:
            return RemoteResultProjector(
                origin, audience, credential, client, token_scope=token_scope).probe()

    return WorkerDependencyReadiness(config.scope, store, auth, evidence, search_probe, projection_probe)


def create_worker_budget_diagnostics(environment=None, *, root=None):
    environment = os.environ if environment is None else environment
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    config, _, _, factory, store, auth, _ = _common(environment, root)
    archives = _archived_configurations(environment, root, config)
    chat = BudgetLedger(factory, config.budget,
                        digest(project_endpoint(_required(environment, "ITOPS_PROJECT_ENDPOINT"))))
    embedding = BudgetLedger(
        factory, BudgetLimits(**strict_json(_required(environment, "ITOPS_EMBEDDING_BUDGET_JSON"))),
        _required(environment, "ITOPS_EMBEDDING_BUDGET_SCOPE"))
    return WorkerBudgetDiagnostics(config.scope, store, auth, chat, embedding, archives=archives)


def create_worker_service(environment=None, *, search_factory=None, root=None):
    environment = os.environ if environment is None else environment
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    config, portfolio, credential, factory, store, auth, evidence = _common(environment, root)
    from .investigations import approval, usage_authorization, validate_batch_budgets, validate_detector_configuration
    investigation_approval = None
    embedding_limits = BudgetLimits.parse(strict_json(_required(environment, "ITOPS_EMBEDDING_BUDGET_JSON")))
    if "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON" in environment:
        investigation_approval = approval(strict_json(_required(environment, "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON")))
        validate_batch_budgets(config.budget, embedding_limits, investigation_approval.get("limits"),
                               configured=investigation_approval)
        validate_detector_configuration(investigation_approval, config.scope, config.policies)
    if config.budget.usage_mode != embedding_limits.usage_mode:
        raise ContractError("runtime chat and embedding usage modes must match")
    authorization = usage_authorization(config.budget, investigation_approval, "chat")
    usage_authorization(embedding_limits, investigation_approval, "embedding")
    plan = RegistrationPlan(canonical(_document(environment, "ITOPS_REGISTRATION_PLAN_PATH", root)).decode())
    endpoint = project_endpoint(_required(environment, "ITOPS_PROJECT_ENDPOINT"))
    if (plan.document["project_endpoint"] != endpoint or plan.document["sampling"] != "model_defaults"
            or plan.document["model_deployment"] != "gpt-5-6-luna"):
        raise ContractError("registration must match this exact project and approved Luna model defaults")
    agents = bind_registered_runtime_agents(
        portfolio, plan, _document(environment, "ITOPS_REGISTRATION_RECEIPT_PATH", root))
    amendment = None
    if "ITOPS_CHAT_LIMIT_AMENDMENT_JSON" in environment:
        document = strict_json(_required(environment, "ITOPS_CHAT_LIMIT_AMENDMENT_JSON"))
        if (not isinstance(document, dict) or set(document) != {field.name for field in fields(MonthlyLimitAmendment)}
                or investigation_approval is None or "limits" not in investigation_approval
                or document["approval_id"] != investigation_approval["approval_id"]
                or document["expires_at_utc"] != investigation_approval["expires_at_utc"]):
            raise ContractError("monthly amendment requires the current explicit operator approval")
        amendment = MonthlyLimitAmendment(**document)
    ledger = BudgetLedger(factory, config.budget, digest(endpoint), monthly_limit_amendment=amendment,
                          usage_authorization=authorization)
    from azure.ai.projects import AIProjectClient
    project = AIProjectClient(endpoint=endpoint, credential=credential, retry_total=0)
    transport = OpenAIResponsesTransport(project.get_openai_client())
    repository = SqlServerReviewRepository(factory)
    client = httpx.Client(timeout=15, follow_redirects=False)
    tools_origin = _required(environment, "ITOPS_TOOLS_ORIGIN")
    tools_audience = _required(environment, "ITOPS_TOOLS_AUDIENCE")
    bindings = strict_json(portfolio.bindings_json)
    if any(binding != {"backend": "openapi_http", "base_url": tools_origin.rstrip("/") + "/api", "audience": tools_audience}
           for binding in bindings["openapi"].values()):
        raise ContractError("compiled tool binding does not match deployed host/audience settings")
    if search_factory is None:
        from .service_search import create_workflow_knowledge
        search_factory = create_workflow_knowledge
    search = search_factory(environment, credential, repository, config, bindings["search"]["search_knowledge"])
    if not callable(search) or not callable(getattr(search, "guard", None)):
        raise ContractError("Search adapter requires retrieval and independent eligibility guard")
    backend = TrustedDispatch(portfolio, RemoteEvidenceTools(
        tools_origin, tools_audience, credential, client, evidence,
        token_scope=_required(environment, "ITOPS_TOOLS_TOKEN_SCOPE")), search)
    runtime = PortfolioRuntime(agents, transport, ledger, evidence, config.gate_policy, backend,
                               knowledge_guard=search.guard)
    from .service_projection import RemoteResultProjector
    projector = RemoteResultProjector(
        _required(environment, "ITOPS_REVIEW_ORIGIN"), _required(environment, "ITOPS_PROJECTION_AUDIENCE"),
        credential, client, token_scope=_required(environment, "ITOPS_PROJECTION_TOKEN_SCOPE"))
    _, replacement_accounting = _replacement_hooks(environment, config, factory)
    jobs = ApplicationJobs(store, evidence, ApplicationResults(store), repository, runtime,
                           config.scope, config.retained_until, projector=projector,
                           investigation_approval=investigation_approval, detector_policies=config.policies,
                           replacement_accounting=replacement_accounting)
    reader = LogsReader(config.scope.producer, config.workspace_id, AzureLogsTransport(credential, client))
    worker = ConsumerWorker(
        store, evidence, reader, {config.scope.run_id: config.scope}, list(config.policies), uuid4().hex,
        handlers=jobs.handlers, max_query_attempts=2, max_ingestion_seconds=90)
    return WorkerService(worker, jobs, auth)


class ToolsService:
    def __init__(self, auth, portfolio, results, evidence, scope, *, retained_until=None):
        self.auth, self.portfolio, self.results, self.evidence, self.scope = auth, portfolio, results, evidence, scope
        self.retained_until = utc(retained_until) if retained_until is not None else None
        self.backends = EvidenceBackends(scope.producer.key, scope.run_id)

    def _context(self, headers, body):
        from jsonschema import Draft202012Validator
        self.auth.authenticate(headers, "Tools.Invoke")
        if self.retained_until is not None and datetime.now(timezone.utc) >= self.retained_until:
            raise ContractError("tool source evidence retention has expired")
        if set(body) != {"agent", "operation", "request"}:
            raise ContractError("explicit trusted tool envelope required")
        Draft202012Validator(CONTEXT_REQUEST).validate(body["request"])
        matches = [item for item in self.portfolio.functions_for(body["agent"])
                   if item.backend_operation == body["operation"] and item.source_tool != "search_knowledge"]
        if len(matches) != 1:
            raise ContractError("operation is not declared for the bound agent")
        document = strict_json(self.evidence.read(EvidenceRef(**body["request"]["context"])))
        if (set(document) != {"workflow_id", "candidate", "stages", "gate_receipt", "trigger_payload"}
                or document["workflow_id"] != body["request"]["workflow_id"]):
            raise ContractError("immutable tool context envelope mismatch")
        self.results.authorize_context(document["workflow_id"], document["candidate"])
        context = WorkflowContext(document["workflow_id"], document["candidate"], document["stages"],
                                  gate_receipt=document["gate_receipt"], trigger_payload=document["trigger_payload"])
        return matches[0], context

    def dispatch(self, headers, body):
        from jsonschema import Draft202012Validator
        requirement, context = self._context(headers, body)
        output = self.backends.call(body["operation"], context)
        Draft202012Validator(requirement.output_schema).validate(output)
        return output

    def openapi(self, headers, operation, request):
        from jsonschema import Draft202012Validator
        context_header = headers.get("X-Itops-Context", "")
        self.auth.authenticate(headers, "Tools.Invoke")
        if len(context_header) > 1000:
            raise ContractError("bounded tool evidence reference required")
        requirement, context = self._context(headers, {
            "agent": headers.get("X-Itops-Agent"), "operation": operation,
            "request": {"workflow_id": headers.get("X-Itops-Workflow"),
                        "context": strict_json(context_header)}})
        expected = backend_request(operation, context)
        # HTTP query parameters are strings; conversion is limited to this one
        # code-owned integer field before enforcing the exact source schema.
        if operation == "getRecentChanges" and isinstance(request.get("hours"), str):
            request = {**request, "hours": int(request["hours"])}
        Draft202012Validator(requirement.backend_request_schema).validate(request)
        if request != expected:
            raise ContractError("request differs from worker-assembled original OpenAPI contract")
        output = self.backends.call(operation, context)
        Draft202012Validator(requirement.output_schema).validate(output)
        return output

    def mcp(self, headers, body):
        self.auth.authenticate(headers, "Tools.Invoke")
        if body.get("jsonrpc") != "2.0" or set(body) - {"jsonrpc", "id", "method", "params"}:
            raise ContractError("bounded JSON-RPC MCP request required")
        method, params = body.get("method"), body.get("params", {})
        if not isinstance(params, dict):
            raise ContractError("MCP parameters must be an object")
        if method == "notifications/initialized":
            if "id" in body or params:
                raise ContractError("invalid MCP initialization notification")
            return None
        if type(body.get("id")) not in (str, int):
            raise ContractError("MCP requests require an explicit string or integer request ID")
        if method == "initialize":
            if params.get("protocolVersion") not in {"2025-03-26", "2025-06-18", "2025-11-25"}:
                raise ContractError("unsupported MCP protocol version")
            result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "itops-bounded-monitor", "version": "1.0.0"}}
        elif method == "tools/list":
            if params:
                raise ContractError("the fixed MCP registry has no pagination cursor")
            result = {"tools": [{"name": name, "description": item["description"],
                                 "inputSchema": item["request_schema"], "outputSchema": item["output_schema"]}
                                for name, item in MONITOR_OPERATIONS.items()]}
        elif method == "ping":
            if params:
                raise ContractError("MCP ping takes no parameters")
            result = {}
        elif method == "tools/call":
            if set(params) != {"name", "arguments"} or params["name"] not in MONITOR_OPERATIONS:
                raise ContractError("MCP operation is not allowlisted")
            output = self.dispatch(headers, {"agent": "rca-investigator", "operation": params["name"],
                                             "request": params["arguments"]})
            result = {"content": [{"type": "text", "text": canonical(output).decode()}],
                      "structuredContent": output, "isError": False}
        else:
            raise ContractError("MCP method is not supported")
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": result}


def create_tools_service(environment=None, *, root=None):
    environment = os.environ if environment is None else environment
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    config, portfolio, credential, factory, store, auth, evidence = _common(environment, root)
    return ToolsService(auth, portfolio, ApplicationResults(store), evidence, config.scope,
                        retained_until=config.retained_until)
