"""Offline SQL/HTTP doubles; these tests do not assert live Azure acceptance."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jsonschema import Draft202012Validator, ValidationError

from agentic_itops.application_jobs import ApplicationJobs
from agentic_itops.application_results import ApplicationResults, IndeterminateAttempt, persist_result
from agentic_itops.consumer.contracts import ContractError, canonical, digest, iso, strict_json
from agentic_itops.consumer.storage import LocalSQLiteWorkflowStore
from agentic_itops.consumer.storage_contract import EvidenceRef, IntegrityConflict, WorkRequest
from agentic_itops.consumer.worker import ConsumerWorker
from agentic_itops.portfolio import compile_portfolio
from agentic_itops.review import SQLiteReviewRepository
from agentic_itops.review.contracts import ReviewError
from agentic_itops.runtime import AGENTS, WorkflowContext
from agentic_itops.service_auth import ServiceAuthenticator
from agentic_itops.service_backends import (
    CONTEXT_OUTPUT, HTTP_OPERATIONS, MONITOR_OPERATIONS, OVERLAY, EvidenceBackends,
    RemoteEvidenceTools, TrustedDispatch, backend_request, deployment_bindings,
)
from agentic_itops.service_runtime import RuntimeConfiguration, ToolsService
from tests.test_portfolio_runtime import setup as runtime_setup
from tests.test_trusted_search_dispatch import trusted_config


ROOT = Path(__file__).resolve().parents[1]
TENANT = "11111111-1111-1111-1111-111111111111"
OID = "22222222-2222-2222-2222-222222222222"
CLIENT = "33333333-3333-3333-3333-333333333333"


def portfolio():
    return compile_portfolio(ROOT, deployment_bindings(
        ROOT, tools_origin="https://itops-unit-tools.azurewebsites.net", tools_audience=CLIENT,
        model_deployment="gpt-5-6-luna", search_binding=trusted_config()["search"]["search_knowledge"]))


def integrated(tmp_path, *, clock=None):
    runtime, transport, reference, scope, citation = runtime_setup(tmp_path)
    store = LocalSQLiteWorkflowStore(lambda: sqlite3.connect(tmp_path / "jobs.db"), **({"clock": clock} if clock else {}))
    store.bootstrap_schema()
    results = ApplicationResults(store)
    results.migrate()
    repository = SQLiteReviewRepository(tmp_path / "review.db")
    jobs = ApplicationJobs(store, runtime.evidence, results, repository, runtime, scope,
                           datetime.now(timezone.utc) + timedelta(days=1))
    worker = ConsumerWorker(store, runtime.evidence, SimpleNamespace(producer=scope.producer),
                            {scope.run_id: scope}, [], "unit-worker", handlers=jobs.handlers)
    candidate = strict_json(runtime.evidence.read(reference))
    payload = {"incident_id": candidate["incident_id"], "producer_key": scope.producer.key,
               "run_id": scope.run_id, "candidate_evidence": asdict(reference), **OVERLAY}
    job_id = store.enqueue(WorkRequest("reasoning", reference.sha256, payload))
    return SimpleNamespace(**locals())


def test_reasoning_receipt_and_projection_are_durable_and_idempotent(tmp_path):
    f = integrated(tmp_path)
    assert f.worker.process_once("reasoning")
    job = f.store.get_job(f.job_id)
    assert job["result"]["status"] == "result_persisted"
    calls = len(f.transport.calls)
    assert f.worker.process_once("projection")
    current = f.repository.get_revision(f.candidate["incident_id"], 1)
    assert current["result"]["result_id"] == f.job_id
    assert current["review_state"] == "pending"
    assert f.results.get(f.job_id)["projected"] == 1
    replay = f.jobs.enqueue_projection(f.job_id, "explicit-projection-retry")
    assert replay == f.jobs.enqueue_projection(f.job_id, "explicit-projection-retry")
    assert f.worker.process_once("projection")
    assert f.store.get_job(replay)["status"] == "completed"
    assert f.repository.current_revision(f.candidate["incident_id"]) == 1
    assert f.repository.get_revision(f.candidate["incident_id"], 1)["etag"] == current["etag"]
    assert len(f.transport.calls) == calls


def test_full_nine_jobs_require_authoritative_closure_and_new_review(tmp_path):
    f = integrated(tmp_path)
    f.worker.process_once("reasoning")
    f.worker.process_once("projection")
    with pytest.raises(ContractError, match="closure"):
        f.jobs.enqueue_postmortem(f.candidate["incident_id"])
    current = f.repository.get_revision(f.candidate["incident_id"], 1)
    accepted = f.repository.review(OID, f.candidate["incident_id"], current["etag"], "accept",
                                    {"action": "accept", "reason": "Reviewed immutable result"})
    f.repository.review(OID, f.candidate["incident_id"], accepted["etag"], "close",
                        {"action": "close", "reason": "Administrative synthetic closure"})
    postmortem = f.jobs.enqueue_postmortem(f.candidate["incident_id"])
    assert f.jobs.enqueue_postmortem(f.candidate["incident_id"]) == postmortem
    f.worker.process_once("postmortem")
    f.worker.process_once("projection")
    second = f.repository.get_revision(f.candidate["incident_id"], 2)
    assert second["review_state"] == "needs_evidence"
    assert second["result"]["postmortem_draft"]["authorship"] == {"kind": "ai"}
    assert second["result"]["stage_results"]["postmortem-drafter"]["output"]["executed_action"] == "none"
    assert second["result"]["coverage"]["lifecycle_trigger"]["closure_receipt_id"]
    assert len(second["result"]["coverage"]["lifecycle_trigger"]["closure_receipt_sha256"]) == 64
    with pytest.raises(ContractError, match="closure"):
        f.jobs._current(second, "hygiene")
    accepted_again = f.repository.review(OID, f.candidate["incident_id"], second["etag"], "accept-second",
                                        {"action": "accept", "reason": "Reviewed new AI draft"})
    f.repository.review(OID, f.candidate["incident_id"], accepted_again["etag"], "close-second",
                        {"action": "close", "reason": "Administrative closure of reviewed draft"})
    hygiene = {"source": f.candidate["source"], "window": {
        "start_utc": iso(f.scope.baseline_start), "end_utc": iso(f.scope.reference_time)}, **OVERLAY}
    f.store.enqueue(WorkRequest("hygiene", "authorized-hygiene-window", hygiene))
    assert f.worker.process_once("hygiene")
    assert f.worker.process_once("hygiene")
    assert f.worker.process_once("projection")
    third = f.repository.get_revision(f.candidate["incident_id"], 3)
    assert third["result"]["stage_results"]["alert-hygiene"]["output"]["estimated_volume_reduction_pct"] is None
    assert set(name for name, _ in f.transport.calls) == set(AGENTS)
    assert third["result"]["execution_allowed"] is False


def test_crashed_attempt_is_not_reexecuted_with_fresh_model_outcomes(tmp_path):
    f = integrated(tmp_path)
    lease = f.store.claim("reasoning", "interrupted")
    f.results.begin(lease, f.candidate, f.reference, base_revision=0, portfolio_hash=f.runtime.portfolio_hash)
    with pytest.raises(IndeterminateAttempt):
        f.jobs.reasoning(lease, lambda: None)
    assert f.transport.calls == []


@pytest.mark.parametrize("value", [None, [], "workflow", "f" * 65, "F" * 64])
def test_external_result_identity_is_strictly_bounded(tmp_path, value):
    f = integrated(tmp_path)
    with pytest.raises(ContractError, match="digest"):
        f.results.get(value)


def test_final_receipt_survives_crash_before_consumer_completion(tmp_path):
    f = integrated(tmp_path)
    lease = f.store.claim("reasoning", "interrupted")
    first = f.jobs.reasoning(lease, lambda: None)
    calls = len(f.transport.calls)
    replay = f.jobs.reasoning(lease, lambda: None)
    assert replay == first
    assert len(f.transport.calls) == calls
    f.store.complete(lease, replay.result, replay.next_work)
    f.worker.process_once("projection")
    assert f.repository.current_revision(f.candidate["incident_id"]) == 1


def test_result_journal_fences_stale_workers_and_changed_candidates(tmp_path):
    clock = [datetime.now(timezone.utc)]
    f = integrated(tmp_path, clock=lambda: clock[0])
    lease = f.store.claim("reasoning", "first", lease_seconds=1)
    f.results.begin(lease, f.candidate, f.reference, base_revision=0, portfolio_hash=f.runtime.portfolio_hash)
    clock[0] += timedelta(seconds=2)
    next_lease = f.store.claim("reasoning", "replacement")
    from agentic_itops.consumer.storage_contract import LostLease
    with pytest.raises(LostLease):
        f.results.finish(lease, f.reference)
    with pytest.raises(IndeterminateAttempt):
        f.results.begin(next_lease, f.candidate, f.reference, base_revision=0, portfolio_hash=f.runtime.portfolio_hash)


def test_application_receipt_immutability_is_enforced_below_python(tmp_path):
    f = integrated(tmp_path)
    lease = f.store.claim("reasoning", "worker")
    f.results.begin(lease, f.candidate, f.reference, base_revision=0, portfolio_hash=f.runtime.portfolio_hash)
    f.results.finish(lease, f.reference)
    for mutation in ("UPDATE consumer_application_runs SET run_id='replacement'",
                     "UPDATE consumer_application_runs SET result_json=NULL",
                     "DELETE FROM consumer_application_runs"):
        with pytest.raises(sqlite3.IntegrityError, match="Immutable"):
            with sqlite3.connect(tmp_path / "jobs.db") as connection:
                connection.execute(mutation)
    assert f.results.get(f.job_id)["result"] == asdict(f.reference)


def test_production_application_schema_cannot_bypass_bootstrap_ledger():
    store = SimpleNamespace(sql_server=True, _table=lambda name: "dbo.consumer_" + name)
    with pytest.raises(ContractError, match="deployment.migrate"):
        ApplicationResults(store).migrate()


def test_production_jobs_cannot_fall_back_to_direct_review_writes():
    with pytest.raises(ContractError, match="remote result projector"):
        ApplicationJobs(SimpleNamespace(sql_server=True), None, None, None, None, None,
                        datetime.now(timezone.utc) + timedelta(days=1))


def test_stale_closure_trigger_never_calls_lifecycle_model(tmp_path):
    f = integrated(tmp_path)
    f.worker.process_once("reasoning")
    f.worker.process_once("projection")
    current = f.repository.get_revision(f.candidate["incident_id"], 1)
    accepted = f.repository.review(OID, f.candidate["incident_id"], current["etag"], "a",
                                    {"action": "accept", "reason": "review"})
    closed = f.repository.review(OID, f.candidate["incident_id"], accepted["etag"], "c",
                                 {"action": "close", "reason": "close"})
    f.jobs.enqueue_postmortem(f.candidate["incident_id"])
    f.repository.review(OID, f.candidate["incident_id"], closed["etag"], "withdraw",
                        {"action": "request_evidence", "reason": "more evidence"})
    calls = len(f.transport.calls)
    with pytest.raises(ContractError, match="closure"):
        f.worker.process_once("postmortem")
    assert len(f.transport.calls) == calls


def test_closed_state_requires_exact_receipt_not_history_order(tmp_path, monkeypatch):
    f = integrated(tmp_path)
    f.worker.process_once("reasoning")
    f.worker.process_once("projection")
    current = f.repository.get_revision(f.candidate["incident_id"], 1)
    accepted = f.repository.review(OID, f.candidate["incident_id"], current["etag"], "accept",
                                    {"action": "accept", "reason": "review"})
    f.repository.review(OID, f.candidate["incident_id"], accepted["etag"], "close",
                        {"action": "close", "reason": "administrative closure"})
    closed = f.repository.get_revision(f.candidate["incident_id"], 1)
    receipt = ApplicationJobs._closure_receipt(closed)
    shuffled = deepcopy(closed)
    shuffled["review_history"] = [receipt, {**receipt, "result_hash": "f" * 64}]
    assert ApplicationJobs._closure_receipt(shuffled) == receipt
    for history in ([], [{**receipt, "action": "accept"}], [{**receipt, "result_hash": "f" * 64}],
                    [{**receipt, "etag": '"stale"'}], [receipt, receipt]):
        with pytest.raises(ContractError, match="closure receipt"):
            ApplicationJobs._closure_receipt({**closed, "review_history": history})
    monkeypatch.setattr(f.repository, "get_revision", lambda *args: {**closed, "review_history": []})
    calls = len(f.transport.calls)
    with pytest.raises(ContractError, match="closure receipt"):
        f.jobs.enqueue_postmortem(f.candidate["incident_id"])
    assert len(f.transport.calls) == calls


def test_analysis_fans_out_only_bounded_configured_ingestion_windows(tmp_path):
    f = integrated(tmp_path)
    payload = {"source": f.candidate["source"], "window": {
        "start_utc": iso(f.scope.baseline_start), "end_utc": iso(f.scope.reference_time)}, **OVERLAY}
    job_id = f.store.enqueue(WorkRequest("analysis", "window", payload))
    assert f.worker.process_once("analysis")
    jobs = [f.store.get_job(item) for item in f.store.get_job(job_id)["next_job_ids"]]
    assert jobs and all(job["kind"] == "ingest" for job in jobs)
    from agentic_itops.consumer.contracts import utc
    assert all(utc(job["payload"]["ingestion_window"]["end"]) -
               utc(job["payload"]["ingestion_window"]["start"]) <= timedelta(minutes=15) for job in jobs)
    payload["window"]["end_utc"] = iso(f.scope.reference_time + timedelta(minutes=1))
    f.store.enqueue(WorkRequest("analysis", "unauthorized-window", payload))
    with pytest.raises(ContractError, match="window"):
        f.worker.process_once("analysis")


def test_six_backends_and_monitor_contracts_are_real_and_synthetic_safe(tmp_path):
    f = integrated(tmp_path)
    context = WorkflowContext(f.job_id, f.candidate)
    backends = EvidenceBackends(f.scope.producer.key, f.scope.run_id)
    compiled = portfolio()
    for operation in HTTP_OPERATIONS:
        requirement = next(item for item in compiled.functions if item.backend_operation == operation)
        request = backend_request(operation, context)
        Draft202012Validator(requirement.backend_request_schema).validate(request)
        output = backends.call(operation, context)
        Draft202012Validator(requirement.output_schema).validate(output)
        if operation in ("getResourceContext", "getRecentChanges"):
            assert output["status"] == "not_applicable"
            assert "not_an_empty_Azure_result" in output["coverage"]
    logs = backends.call("runKqlQuery", context)
    assert logs["row_count"] == len(f.candidate["observations"])
    assert logs["table"] == "EnergyObservations_CL"
    normalized = backends.call("normalizeSignals", context)
    assert normalized["incidents"][0]["incident_fingerprint"] == f.candidate["incident_fingerprint"]
    assert backends.call("analyzeAlertHygiene", context)["estimated_volume_reduction_pct"] is None
    for operation in MONITOR_OPERATIONS:
        Draft202012Validator(CONTEXT_OUTPUT).validate(backends.call(operation, context))


def test_no_model_selected_queries_resources_or_operations(tmp_path):
    f = integrated(tmp_path)
    context = WorkflowContext(f.job_id, f.candidate)
    backends = EvidenceBackends(f.scope.producer.key, f.scope.run_id)
    dispatch = TrustedDispatch(portfolio(), lambda agent, op, ctx: backends.call(op, ctx),
                               lambda context: (_ for _ in ()).throw(AssertionError("no Search request")))
    with pytest.raises(ContractError):
        dispatch("triage-dedup", "getResourceContext", {"resource_id": "/subscriptions/real"}, context)
    f.candidate["target"]["id"] = "/subscriptions/real"
    with pytest.raises(ContractError, match="ARM"):
        backends.call("getResourceContext", context)


class Authorized:
    def authenticate(self, headers, role):
        assert role == "Tools.Invoke"


def test_tools_validate_sql_authority_original_requests_and_mcp(tmp_path):
    f = integrated(tmp_path)
    lease = f.store.claim("reasoning", "worker")
    f.results.begin(lease, f.candidate, f.reference, base_revision=0, portfolio_hash=f.runtime.portfolio_hash)
    document = {"workflow_id": f.job_id, "candidate": f.candidate, "stages": {},
                "gate_receipt": None, "trigger_payload": None}
    reference = f.runtime.evidence.put(canonical(document))
    service = ToolsService(Authorized(), portfolio(), f.results, f.runtime.evidence, f.scope)
    headers = {"X-Itops-Agent": "rca-investigator", "X-Itops-Workflow": f.job_id,
               "X-Itops-Context": canonical(asdict(reference)).decode()}
    request = backend_request("runKqlQuery", WorkflowContext(f.job_id, f.candidate))
    assert service.openapi(headers, "runKqlQuery", request)["row_count"] > 0
    with pytest.raises(ContractError):
        service.openapi(headers, "runKqlQuery", {"query": "EnergyInjectionAudit_CL", "timespan": "PT2H"})
    result = service.mcp({}, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "get_observation_context", "arguments": {"workflow_id": f.job_id, "context": asdict(reference)}}})
    assert result["result"]["structuredContent"]["status"] == "evidence_context"
    initialized = service.mcp({}, {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "unit", "version": "1"}}})
    assert initialized["result"]["protocolVersion"] == "2025-11-25"
    assert service.mcp({}, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    f.results.finish(lease, reference)
    with pytest.raises(IntegrityConflict, match="unreplayed"):
        service.openapi(headers, "runKqlQuery", request)


def test_remote_worker_uses_original_http_contract_and_explicit_identity(tmp_path):
    f = integrated(tmp_path)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "not_applicable"})
    scopes = []
    credential = SimpleNamespace(get_token=lambda scope: (scopes.append(scope) or SimpleNamespace(token="test-only")))
    remote = RemoteEvidenceTools("https://unit.azurewebsites.net", CLIENT, credential,
                                 httpx.Client(transport=httpx.MockTransport(handler)), f.runtime.evidence,
                                 token_scope="api://" + CLIENT + "/.default")
    assert remote("triage-dedup", "getResourceContext", WorkflowContext(f.job_id, f.candidate))["status"] == "not_applicable"
    assert seen[0].method == "GET" and seen[0].url.path == "/api/resource"
    assert seen[0].url.params["resource_id"] == f.candidate["target"]["id"]
    assert scopes == ["api://" + CLIENT + "/.default"]


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize("override,status", [
    ({"idtyp": "user"}, 401), ({"scp": "user_impersonation"}, 401),
    ({"azp": TENANT}, 403), ({"oid": TENANT}, 403), ({"roles": []}, 403),
    ({"aud": "wrong"}, 401), ({"tid": OID}, 401), ({"roles": "Tools.Invoke"}, 401),
])
def test_function_identity_rejects_users_wrong_roles_and_cross_tenant(signing_key, override, status):
    auth = ServiceAuthenticator(TENANT, CLIENT,
        [{"oid": OID, "client_id": CLIENT, "roles": ["Tools.Invoke"]}],
        key_resolver=lambda token: signing_key.public_key())
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": CLIENT,
              "tid": TENANT, "oid": OID, "azp": CLIENT, "idtyp": "app", "roles": ["Tools.Invoke"],
              "iat": now - 10, "nbf": now - 10, "exp": now + 600}
    valid = jwt.encode(claims, signing_key, algorithm="RS256")
    assert auth.authenticate({"Authorization": "Bearer " + valid}, "Tools.Invoke") == OID
    invalid = jwt.encode({**claims, **override}, signing_key, algorithm="RS256")
    with pytest.raises(ReviewError) as error:
        auth.authenticate({"Authorization": "Bearer " + invalid}, "Tools.Invoke")
    assert error.value.status == status


def configuration(f):
    from tests.test_consumer_budget import LIMITS
    return {
        "schema_version": 1, "source": {
            "workspace_resource_id": f.scope.producer.workspace_resource_id,
            "workspace_id": TENANT, "run_id": f.scope.run_id, "surfaces": list(f.scope.surfaces),
            **{name: iso(getattr(f.scope, name)) for name in
               ("baseline_start", "reference_time", "ingestion_start", "ingestion_end")}},
        "evidence_retained_until": iso(datetime.now(timezone.utc) + timedelta(days=1)),
        "detector_policies": [], "approved_detector_policy_hashes": [],
        "budget": asdict(replace(LIMITS, deadline_seconds=120)), "gate_policy": {}}


def test_runtime_configuration_has_no_silent_detector_or_budget_defaults(tmp_path):
    f = integrated(tmp_path)
    value = configuration(f)
    config = RuntimeConfiguration.parse(value)
    assert config.policies == ()
    assert config.budget.deadline_seconds == 120
    value["budget"]["deadline_seconds"] = 180
    assert RuntimeConfiguration.parse(value).budget.deadline_seconds == 180
    value["budget"]["deadline_seconds"] = 181
    with pytest.raises(ContractError, match="180"):
        RuntimeConfiguration.parse(value)


def test_enabled_detector_requires_exact_reviewed_policy_hash(tmp_path):
    from tests.test_consumer_detectors import series
    f = integrated(tmp_path)
    value = configuration(f)
    _, policy, _, _ = series("upstream", "step-up")
    value["detector_policies"] = [asdict(policy)]
    with pytest.raises(ContractError, match="approval"):
        RuntimeConfiguration.parse(value)
    value["approved_detector_policy_hashes"] = [policy.sha256]
    assert RuntimeConfiguration.parse(value).policies[0].enabled is True


def test_function_entrypoints_have_actual_routes_and_no_schedules():
    import importlib.util
    for host, expected in (("worker", {"work/{kind}", "lifecycle/postmortem", "jobs/{job_id}", "results/project",
                                     "preflight/source-access", "jobs/{job_id}/advance", "jobs/{job_id}/budget",
                                     "jobs/{job_id}/readiness", "results/replacement"}),
                           ("tools", {"dispatch", "mcp", *[item[1] for item in HTTP_OPERATIONS.values()]})):
        path = ROOT / "services" / host / "function_app.py"
        spec = importlib.util.spec_from_file_location("unit_" + host, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        functions = module.app.get_functions()
        bindings = [json.loads(item.get_function_json())["bindings"] for item in functions]
        triggers = [item for group in bindings for item in group if item["type"] == "httpTrigger"]
        assert {item["route"] for item in triggers} == expected
        assert len(triggers) == len(functions)


def test_real_search_binding_is_registered_for_all_declared_users():
    from agentic_itops.service_search import approved_search_binding
    compiled = compile_portfolio(ROOT, deployment_bindings(
        ROOT, tools_origin="https://unit-tools.azurewebsites.net", tools_audience="api://tools",
        model_deployment="gpt-5-6-luna", search_binding=approved_search_binding("unit-snapshot")))
    assert compiled.bindings_complete
    search = next(item for item in compiled.functions if item.name == "search_approved_knowledge")
    assert set(search.agents) == {"industry-signal-pack", "runbook-memory", "remediation-planner", "postmortem-drafter"}
    assert search.backend_binding["requires_sql_eligibility_revalidation"] is True
    assert search.backend_binding["native_model_tool"] is False


def test_workflow_search_queries_are_source_owned_and_guarded(tmp_path):
    from agentic_itops.service_search import WorkflowKnowledge
    f = integrated(tmp_path)
    calls, guards, closes = [], [], []
    settings = SimpleNamespace(
        client_id=CLIENT, snapshot_id="unit-snapshot",
        allowed_sources=frozenset({(f.scope.producer.key, f.scope.run_id)}))
    class Embedding:
        def __init__(self, settings, finance, credential):
            from agentic_itops.review.search_delivery import BudgetLedgerEmbeddingGate
            self.client = SimpleNamespace(close=lambda: closes.append("embedding"))
            assert finance.workflow_id == BudgetLedgerEmbeddingGate(
                finance.ledger, workflow_id=f.job_id).workflow_id
            assert finance.workflow_id != f.job_id
            assert finance.parent_remaining_seconds() == 5
    class Retriever:
        def __init__(self, settings, embedding, repository, credential):
            self.client = SimpleNamespace(close=lambda: closes.append("search"))
        def handle(self, request):
            calls.append(request)
            return {"status": "complete", "snapshot_id": "unit-snapshot", "query_type": "vector_semantic_hybrid",
                    "index_name": "agentic-itops-knowledge", "request_id": "actual-test-response",
                    "embedding_request_id": "actual-test-embedding", "documents": [], "rejected_documents": [], **OVERLAY}
        def guard_documents(self, documents, **scope):
            guards.append((documents, scope))
    parent_calls = []
    parent = SimpleNamespace(remaining_seconds=lambda workflow: parent_calls.append(workflow) or 5)
    search = WorkflowKnowledge(settings, object(), object(), SimpleNamespace(scope="query-embedding"), f.scope,
                                parent_ledger=parent, retriever_type=Retriever, embedding_type=Embedding)
    context = WorkflowContext(f.job_id, f.candidate)
    assert search(context)["status"] == "complete"
    assert calls[0]["producer_key"] == f.scope.producer.key and calls[0]["run_id"] == f.scope.run_id
    assert "observations" not in calls[0]["query"]
    assert set(closes) == {"embedding", "search"}
    assert parent_calls == [f.job_id]


def test_query_embedding_factory_uses_explicit_shared_monthly_scope(tmp_path, monkeypatch):
    import agentic_itops.service_search as module
    f = integrated(tmp_path)
    @dataclass
    class Settings:
        snapshot_id: str
        allowed_sources: frozenset
        timeout_seconds: int = 20
    settings = Settings("unit-snapshot", frozenset({(f.scope.producer.key, f.scope.run_id)}))
    monkeypatch.setattr(module, "SearchServiceSettings",
                        SimpleNamespace(from_environment=lambda environment: settings))
    monkeypatch.setattr(module, "managed_identity_sql_factory",
                        lambda *args: lambda: sqlite3.connect(tmp_path / "unopened.db"))
    limits = replace(f.runtime.ledger.limits, model_context_tokens=8192, max_output_tokens=1,
                     deadline_seconds=120)
    configuration = SimpleNamespace(scope=f.scope, budget=replace(f.runtime.ledger.limits, deadline_seconds=120))
    environment = {
        "ITOPS_SQL_SERVER": "unit.database.windows.net", "ITOPS_SQL_DATABASE": "itops",
        "AZURE_CLIENT_ID": CLIENT, "ITOPS_PROJECT_ENDPOINT": "https://unit.services.ai.azure.com/api/projects/itops",
        "ITOPS_EMBEDDING_BUDGET_JSON": json.dumps(asdict(limits)),
        "ITOPS_EMBEDDING_BUDGET_SCOPE": "approved-shared-embedding-allowance"}
    reader = module.create_workflow_knowledge(
        environment, object(), object(), configuration, module.approved_search_binding("unit-snapshot"))
    assert reader.ledger.scope == "approved-shared-embedding-allowance"
    assert reader.parent_ledger.scope != reader.ledger.scope
    from agentic_itops.registration import RegistrationError, project_endpoint
    assert reader.parent_ledger.scope == digest(project_endpoint(environment["ITOPS_PROJECT_ENDPOINT"]))
    assert not (tmp_path / "unopened.db").exists()
    with pytest.raises(RegistrationError):
        module.create_workflow_knowledge(
            {**environment, "ITOPS_PROJECT_ENDPOINT": environment["ITOPS_PROJECT_ENDPOINT"] + "/"},
            object(), object(), configuration, module.approved_search_binding("unit-snapshot"))
    del environment["ITOPS_EMBEDDING_BUDGET_SCOPE"]
    with pytest.raises(ContractError, match="ITOPS_EMBEDDING_BUDGET_SCOPE"):
        module.create_workflow_knowledge(
            environment, object(), object(), configuration, module.approved_search_binding("unit-snapshot"))


def test_source_zip_is_deterministic_and_contains_flattened_runtime_dependencies(tmp_path):
    import importlib.util
    from zipfile import ZipFile
    f = integrated(tmp_path)
    path = ROOT / "scripts" / "package_application_service.py"
    spec = importlib.util.spec_from_file_location("application_packager", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime_path, bindings_path = tmp_path / "runtime.json", tmp_path / "bindings.json"
    runtime_path.write_bytes(canonical(configuration(f)))
    bindings_path.write_text(portfolio().bindings_json, encoding="utf-8")
    artifacts = {"runtime.json": runtime_path, "bindings.json": bindings_path}
    archive = tmp_path / "tools.zip"
    first = module.package("tools", artifacts, archive)
    second = module.package("tools", artifacts, archive)
    assert first["sha256"] == second["sha256"]
    with ZipFile(archive) as packaged:
        assert "function_app.py" in packaged.namelist()
        assert "agentic_itops/consumer/runtime_sql/application.sql" in packaged.namelist()
        assert ".runtime/runtime.json" in packaged.namelist()
        dependencies = packaged.read("requirements.txt").decode()
        assert "-r " not in dependencies
        assert "azure-functions==2.3.0" in dependencies
        assert "azure-search-documents==" in dependencies
        assert not any("local.settings" in name or ".env" in name or "__pycache__" in name
                       for name in packaged.namelist())


def test_projection_boundary_uses_sql_receipt_not_worker_supplied_content(tmp_path):
    from agentic_itops.service_projection import ResultProjectionService
    f = integrated(tmp_path)
    f.worker.process_once("reasoning")
    class ProjectorAuth:
        def authenticate(self, headers, role):
            assert role == "Results.Project"
    projection = ResultProjectionService(
        ProjectorAuth(), f.results, f.runtime.evidence, f.repository,
        {(f.scope.producer.key, f.scope.run_id): f.candidate["source"]})
    with pytest.raises(ContractError):
        projection.project({}, {"workflow_id": f.job_id, "review_state": "accepted"})
    first = projection.project({}, {"workflow_id": f.job_id})
    assert projection.project({}, {"workflow_id": f.job_id}) == first
    assert f.repository.get_revision(f.candidate["incident_id"], 1)["review_state"] == "pending"
    projection.sources = {}
    with pytest.raises(IntegrityConflict):
        projection.project({}, {"workflow_id": f.job_id})


def test_remote_projection_sends_only_workflow_identity_and_requires_exact_ack(tmp_path):
    from agentic_itops.service_projection import RemoteResultProjector
    f = integrated(tmp_path)
    f.worker.process_once("reasoning")
    result = strict_json(f.runtime.evidence.read(EvidenceRef(**f.results.get(f.job_id)["result"])))
    seen = []
    def handler(request):
        seen.append(strict_json(request.content))
        return httpx.Response(200, json={"result_id": f.job_id, "analysis_revision": 1, "etag": '"exact"'})
    remote = RemoteResultProjector(
        "https://unit-review.azurewebsites.net", "api://services",
        SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test-only")),
        httpx.Client(transport=httpx.MockTransport(handler)), token_scope="api://services/.default")
    assert remote(result)["etag"] == '"exact"'
    assert seen == [{"workflow_id": f.job_id}]


@pytest.mark.parametrize("with_amendment,observe_only", [(False, False), (True, False), (False, True)])
def test_production_factory_binds_exact_nine_versions_without_cloud_calls(
        tmp_path, monkeypatch, with_amendment, observe_only):
    import agentic_itops.service_runtime as hosting
    from agentic_itops.registration import prepare_registration
    from agentic_itops.service_projection import RemoteResultProjector
    import azure.ai.projects
    f = integrated(tmp_path)
    compiled = portfolio()
    endpoint = "https://unit-project.services.ai.azure.com/api/projects/itops"
    plan = prepare_registration(compiled, endpoint, "gpt-5-6-luna", sampling="model_defaults")
    receipt = {
        "schema_version": 1, "status": "registered_and_read_back", "project_endpoint": endpoint,
        "plan_sha256": plan.sha256, "portfolio_sha256": compiled.content_sha256,
        "versions": {name: str(index + 1) for index, name in enumerate(AGENTS)},
        "live_acceptance": False, "execution_allowed": False}
    environment = {
        "AZURE_CLIENT_ID": CLIENT, "ITOPS_SQL_SERVER": "unit.database.windows.net", "ITOPS_SQL_DATABASE": "itops",
        "ENTRA_TENANT_ID": TENANT, "ENTRA_AUDIENCE": CLIENT,
        "ITOPS_SERVICE_CALLERS_JSON": json.dumps([{"oid": OID, "client_id": CLIENT, "roles": ["Worker.Invoke"]}]),
        "ITOPS_PROJECT_ENDPOINT": endpoint, "ITOPS_TOOLS_ORIGIN": "https://itops-unit-tools.azurewebsites.net",
        "ITOPS_TOOLS_AUDIENCE": CLIENT, "ITOPS_REVIEW_ORIGIN": "https://unit-review.azurewebsites.net",
        "ITOPS_PROJECTION_AUDIENCE": CLIENT,
        "ITOPS_TOOLS_TOKEN_SCOPE": "api://" + CLIENT + "/.default",
        "ITOPS_PROJECTION_TOKEN_SCOPE": "api://" + CLIENT + "/.default"}
    config = configuration(f)
    environment["ITOPS_EMBEDDING_BUDGET_JSON"] = json.dumps(config["budget"])
    if observe_only:
        from tests.test_usage_policy import approved_usage
        from agentic_itops.consumer.budget import BudgetLimits
        config["budget"].update(usage_mode="observe_only", policy_version="offline-observe-v1")
        policy = BudgetLimits(**config["budget"])
        embedding = replace(policy, max_output_tokens=1)
        environment["ITOPS_EMBEDDING_BUDGET_JSON"] = json.dumps(asdict(embedding))
        environment["ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"] = json.dumps(approved_usage(policy, embedding))
    if with_amendment:
        from agentic_itops.consumer.budget import BudgetLimits
        from agentic_itops.consumer.contracts import digest
        now = datetime.now(timezone.utc)
        expiry = iso(now + timedelta(minutes=10))
        config["budget"].update(policy_version="approved-v2", monthly_nanodollars=9_900_000_000,
                                deadline_seconds=180)
        policy = BudgetLimits(**config["budget"])
        environment["ITOPS_EMBEDDING_BUDGET_JSON"] = json.dumps({
            **config["budget"], "api_calls": 3, "monthly_nanodollars": 100_000_000})
        environment["ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"] = json.dumps({
            "approval_id": "approved-v2", "actor_oid": OID, "expires_at_utc": expiry,
            "limits": {"max_total_nanodollars": 10_000_000_000, "max_total_model_calls": 144,
                       "max_total_dispatches": 96, "max_total_embedding_calls": 20,
                       "max_workflow_seconds": 180}})
        amendment = {
            "approval_id": "approved-v2", "workflow_id": "future", "scope_key": digest(endpoint),
            "month_key": now.strftime("%Y-%m"), "policy_hash": policy.sha256,
            "expected_limit_nanodollars": 4_900_000_000, "new_limit_nanodollars": 9_900_000_000,
            "expected_accounted_nanodollars": 0, "expires_at_utc": expiry}
        environment["ITOPS_CHAT_LIMIT_AMENDMENT_JSON"] = json.dumps(amendment)
    for name, value in {
            "ITOPS_RUNTIME_CONFIG_PATH": config, "ITOPS_BINDINGS_PATH": strict_json(compiled.bindings_json),
            "ITOPS_REGISTRATION_PLAN_PATH": plan.document, "ITOPS_REGISTRATION_RECEIPT_PATH": receipt}.items():
        path = tmp_path / (name + ".json")
        path.write_bytes(canonical(value))
        environment[name] = str(path)
    credential = object()
    monkeypatch.setattr(hosting, "_identity", lambda env: credential)
    monkeypatch.setattr(hosting, "_evidence", lambda env, identity: f.runtime.evidence)
    monkeypatch.setattr(hosting, "verify_sql_native_runtime", lambda: None)
    monkeypatch.setattr(hosting, "managed_identity_sql_factory", lambda *args: lambda: sqlite3.connect(tmp_path / "unused.db"))
    projects = []
    class Project:
        def __init__(self, **kwargs):
            projects.append(kwargs)
        def get_openai_client(self):
            return object()
    monkeypatch.setattr(azure.ai.projects, "AIProjectClient", Project)
    class Search:
        def __call__(self, context):
            raise AssertionError("factory must never invoke Search")
        def guard(self, context):
            raise AssertionError("factory must never query SQL eligibility")
    service = hosting.create_worker_service(environment, root=ROOT, search_factory=lambda *args: Search())
    assert projects == [{"endpoint": endpoint, "credential": credential, "retry_total": 0}]
    assert set(service.jobs.runtime.agents) == set(AGENTS)
    assert {name: agent.reference.version for name, agent in service.jobs.runtime.agents.items()} == receipt["versions"]
    assert isinstance(service.jobs.projector, RemoteResultProjector)
    assert service.worker.max_query_attempts == 2
    assert service.jobs.runtime.ledger.limits.deadline_seconds == (180 if with_amendment else 120)
    assert service.jobs.runtime.ledger.limits.usage_mode == ("observe_only" if observe_only else "enforced")
    if observe_only:
        assert service.jobs.runtime.ledger.usage_authorization.policy_hash == policy.sha256
        with pytest.raises(ContractError, match="approval"):
            hosting.create_worker_service(
                {key: value for key, value in environment.items()
                 if key != "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"},
                root=ROOT, search_factory=lambda *args: Search())
    if with_amendment:
        assert asdict(service.jobs.runtime.ledger.monthly_limit_amendment) == amendment
        for field, invalid in (("approval_id", "unapproved"), ("scope_key", "foreign"),
                               ("policy_hash", "0" * 64), ("expires_at_utc", "2099-01-01T00:00:00Z")):
            environment["ITOPS_CHAT_LIMIT_AMENDMENT_JSON"] = json.dumps({**amendment, field: invalid})
            with pytest.raises(ContractError):
                hosting.create_worker_service(environment, root=ROOT, search_factory=lambda *args: Search())
        environment["ITOPS_CHAT_LIMIT_AMENDMENT_JSON"] = json.dumps(amendment)
    changed = deepcopy(receipt)
    changed["portfolio_sha256"] = "f" * 64
    Path(environment["ITOPS_REGISTRATION_RECEIPT_PATH"]).write_bytes(canonical(changed))
    from agentic_itops.registration import RegistrationError
    with pytest.raises(RegistrationError):
        hosting.create_worker_service(environment, root=ROOT, search_factory=lambda *args: Search())
    assert len(projects) == 1


def test_explicit_managed_identity_factory_has_no_developer_chain(monkeypatch):
    from agentic_itops.service_runtime import _identity
    import azure.identity
    calls = []
    monkeypatch.setattr(azure.identity, "ManagedIdentityCredential", lambda **kwargs: calls.append(kwargs) or "identity")
    assert _identity({"AZURE_CLIENT_ID": CLIENT}) == "identity"
    assert calls == [{"client_id": CLIENT, "retry_total": 0, "connection_timeout": 5, "read_timeout": 5}]
    with pytest.raises(ContractError):
        _identity({})
    with pytest.raises(ContractError):
        _identity({"AZURE_CLIENT_ID": "00000000-0000-0000-0000-000000000000"})


@pytest.mark.parametrize("scope", [CLIENT + "/.default", "api://" + CLIENT, "api:///.default", None])
def test_service_token_scope_is_not_inferred_from_jwt_audience(scope):
    from agentic_itops.service_auth import application_token_scope
    with pytest.raises(ContractError):
        application_token_scope(scope)
