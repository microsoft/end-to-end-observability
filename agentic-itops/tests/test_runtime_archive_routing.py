"""Offline runtime boundaries; no model endpoints or Azure dependencies are called."""
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from agentic_itops import service_projection, service_runtime as runtime
from agentic_itops.consumer.contracts import ContractError, canonical, iso
from agentic_itops.consumer.logs import CompleteBatch
from agentic_itops.consumer.storage_contract import WorkRequest
from agentic_itops.review.contracts import OVERLAY, ReviewError
from tests.test_application_integration import configuration, integrated
from tests.test_consumer_worker import setup as consumer_setup
from tests.test_projection_recovery import Auth, prepared

ACTIVE_RUN = "eeea15c9-1ced-4fbc-a5e9-062237b1fd1e"
HEADERS = {"Authorization": "unit"}


def configure(monkeypatch, fixture, config, *, archives=(), approval="not-json"):
    def forbidden(*args, **kwargs):
        raise AssertionError("model-free factory constructed a model or budget dependency")

    evidence = getattr(fixture, "evidence", None) or fixture.runtime.evidence
    monkeypatch.setattr(runtime, "_common", lambda *args: (
        config, None, object(), forbidden, fixture.store, Auth(), evidence))
    monkeypatch.setattr(runtime, "SqlServerReviewRepository", lambda _: fixture.repository)
    for name in ("BudgetLedger", "OpenAIResponsesTransport", "PortfolioRuntime", "create_worker_service"):
        monkeypatch.setattr(runtime, name, forbidden)
    from agentic_itops.review import search_delivery
    from azure.ai import projects
    monkeypatch.setattr(projects, "AIProjectClient", forbidden)
    monkeypatch.setattr(search_delivery, "ManagedIdentityEmbedding", forbidden)

    class Projector:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, result):
            return fixture.repository.register_result(result)

    monkeypatch.setattr(service_projection, "RemoteResultProjector", Projector)
    env = {
        "ITOPS_REVIEW_ORIGIN": "https://unit-review.azurewebsites.net",
        "ITOPS_PROJECTION_AUDIENCE": "unit", "ITOPS_PROJECTION_TOKEN_SCOPE": "api://unit/.default",
        "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": approval,
        "ITOPS_CHAT_LIMIT_AMENDMENT_JSON": "not-json",
        "ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON": json.dumps(list(archives)),
    }
    return env


def archived_fixture(tmp_path, monkeypatch, *, expired=False):
    fixture = prepared(tmp_path)
    document = configuration(fixture)
    if expired:
        document["evidence_retained_until"] = "2000-01-01T00:00:00Z"
    (tmp_path / "archive.json").write_bytes(canonical(document))
    active = runtime.RuntimeConfiguration.parse({
        **configuration(fixture),
        "source": {**document["source"], "run_id": ACTIVE_RUN},
    })
    env = configure(monkeypatch, fixture, active, archives=("archive.json",))
    return fixture, runtime.create_worker_model_free_service(env, root=tmp_path), env, active


@pytest.mark.parametrize("approval", ["not-json", '{"expires_at_utc":"2000-01-01T00:00:00Z"}'])
def test_active_analysis_ingestion_and_detection_queue_but_never_execute_reasoning(
        tmp_path, monkeypatch, approval):
    worker, store, evidence, _, batches, scope = consumer_setup(tmp_path)
    from agentic_itops.review import SQLiteReviewRepository
    fixture = SimpleNamespace(scope=scope, store=store, evidence=evidence,
                              repository=SQLiteReviewRepository())
    document = configuration(fixture)
    document["detector_policies"] = [asdict(worker.policies[0])]
    document["approved_detector_policy_hashes"] = [worker.policies[0].sha256]
    config = runtime.RuntimeConfiguration.parse(document)
    env = configure(monkeypatch, fixture, config, approval=approval)
    service = runtime.create_worker_model_free_service(env, root=tmp_path)
    assert service.jobs.runtime is service.jobs.investigation_approval is None
    assert service.jobs.detector_policies == config.policies
    assert set(service.worker.handlers) == {"analysis", "projection", "replacement"}
    calls = []

    class Reader:
        producer = scope.producer

        def read(self, window):
            calls.append(window)
            records = tuple(record for batch in batches for record in batch.records
                            if window.ingestion_start <= record.ingested_at < window.ingestion_end)
            return CompleteBatch(scope.producer, window, records, 0, len(records))

    service.worker.reader = Reader()
    payload = {
        "source": {"producer_key": scope.producer.key, "run_id": scope.run_id,
                   "surfaces": list(scope.surfaces)},
        "window": {"start_utc": iso(scope.baseline_start), "end_utc": iso(scope.reference_time)},
        **OVERLAY,
    }
    analysis = store.enqueue(WorkRequest("analysis", "model-free-analysis", payload))
    assert service.advance(HEADERS, analysis, {})["status"] == "completed"
    for job_id in store.get_job(analysis)["next_job_ids"]:
        assert service.advance(HEADERS, job_id, {})["status"] == "completed"
    assert calls
    child_ids = set()
    for job_id in store.get_job(analysis)["next_job_ids"]:
        child_ids.update(store.get_job(job_id)["result"]["child_job_ids"])
    reasoning = set()
    for job_id in child_ids:
        assert store.get_job(job_id)["kind"] in {"detector", "coverage"}
        result = service.advance(HEADERS, job_id, {})
        assert result["status"] == "completed"
        reasoning.update(result["next_job_ids"])
    assert len(reasoning) == 1
    for job_id in reasoning:
        assert service.job(HEADERS, job_id)["status"] == "queued"
        assert service.advance_kind(HEADERS, job_id) == "reasoning"
        with pytest.raises(ContractError, match="model-free"):
            service.advance(HEADERS, job_id, {})
        assert store.get_job(job_id)["status"] == "queued"
    for kind in runtime.PAID_WORK_KINDS:
        with pytest.raises(ContractError, match="model-free"):
            service.invoke(HEADERS, kind, {})
    with pytest.raises(ContractError, match="lifecycle"):
        service.postmortem(HEADERS, {"incident_id": "anything"})
    fixture.repository.close()


def test_archived_exact_reads_and_projection_survive_active_run_switch(tmp_path, monkeypatch):
    f, service, _, active = archived_fixture(tmp_path, monkeypatch)
    original = deepcopy(f.store.get_job(f.original_projection))
    original_parent = deepcopy(f.store.get_job(f.job_id))
    assert service.jobs.scope == active.scope
    assert set(service.worker.scopes) == {ACTIVE_RUN}
    assert service.job(HEADERS, f.job_id)["status"] == "completed"
    retry = service.projection(
        {**HEADERS, "Idempotency-Key": "archive-retry"}, {"workflow_id": f.job_id})["job_id"]
    assert service.advance_kind(HEADERS, retry) == "projection"
    assert service.invoke(HEADERS, "projection", {})["status"] == "no_queued_work"
    assert f.store.get_job(retry)["status"] == "queued"
    assert service.advance(HEADERS, retry, {})["status"] == "completed"
    assert f.results.get(f.job_id)["projected"] == 1
    assert f.store.get_job(f.original_projection) == original
    assert f.store.get_job(f.job_id) == original_parent
    assert len(f.transport.calls) == f.model_calls_before_recovery
    assert f.repository.get_revision(f.candidate["incident_id"], 1)["review_state"] == "pending"


@pytest.mark.parametrize("kind", sorted((runtime.MODEL_FREE_WORK_KINDS | runtime.PAID_WORK_KINDS) - {"projection"}))
def test_archive_cannot_authorize_any_other_work(tmp_path, monkeypatch, kind):
    f, service, _, _ = archived_fixture(tmp_path, monkeypatch)
    job_id = f.store.enqueue(WorkRequest(kind, "archived-" + kind, {
        "producer_key": f.scope.producer.key, "run_id": f.scope.run_id, **OVERLAY}))
    assert service.job(HEADERS, job_id)["status"] == "queued"
    with pytest.raises(ContractError, match="archived work"):
        service.advance_kind(HEADERS, job_id)
    with pytest.raises(ContractError, match="archived work"):
        service.advance(HEADERS, job_id, {})
    if kind in runtime.MODEL_FREE_WORK_KINDS:
        assert service.invoke(HEADERS, kind, {})["status"] == "no_queued_work"
    assert f.store.get_job(job_id)["status"] == "queued"
    assert len(f.transport.calls) == f.model_calls_before_recovery


def test_expired_archive_fails_locally_without_disabling_active_work(tmp_path, monkeypatch):
    f, service, _, active = archived_fixture(tmp_path, monkeypatch, expired=True)
    assert service.archives[0].retained_until.year == 2000
    with pytest.raises(ContractError, match="archived evidence retention"):
        service.job(HEADERS, f.job_id)
    with pytest.raises(ContractError, match="archived evidence retention"):
        service.projection({**HEADERS, "Idempotency-Key": "expired"}, {"workflow_id": f.job_id})
    job_id = f.store.enqueue(WorkRequest("analysis", "active", {
        "source": {"producer_key": active.scope.producer.key, "run_id": ACTIVE_RUN,
                   "surfaces": list(active.scope.surfaces)},
        "window": {"start_utc": iso(active.scope.baseline_start),
                   "end_utc": iso(active.scope.reference_time)}, **OVERLAY}))
    assert service.advance(HEADERS, job_id, {})["status"] == "completed"


def test_unknown_scope_job_or_unauthenticated_request_never_advances(tmp_path, monkeypatch):
    f, service, _, _ = archived_fixture(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        service.job(HEADERS, "f" * 64)
    with pytest.raises(ContractError, match="job ID"):
        service.job(HEADERS, "../artifact.json")
    with pytest.raises(ReviewError):
        service.advance_kind({}, f.job_id)
    job_id = f.store.enqueue(WorkRequest("reasoning", "foreign", {
        "producer_key": f.scope.producer.key, "run_id": "unapproved", **OVERLAY}))
    for action in (service.job, service.advance_kind):
        with pytest.raises(ContractError, match="source/run"):
            action(HEADERS, job_id)
    assert f.store.get_job(job_id)["status"] == "queued"


@pytest.mark.parametrize("paths", [
    "null", "{}", '["../archive.json"]', '["C:\\\\archive.json"]',
    '["\\\\\\\\server\\\\archive.json"]', '["/archive.json"]', "[4]", '[""]',
    json.dumps(["archive.json"] * 9), json.dumps(["archive.json"] * 2),
])
def test_archive_paths_are_bounded_deployment_relative_artifacts(tmp_path, paths):
    f = integrated(tmp_path)
    old = configuration(f)
    (tmp_path / "archive.json").write_bytes(canonical(old))
    active = runtime.RuntimeConfiguration.parse({
        **old, "source": {**old["source"], "run_id": ACTIVE_RUN}})
    with pytest.raises(ContractError):
        runtime._archived_configurations(
            {"ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON": paths}, tmp_path, active)


@pytest.mark.parametrize("mutation", [
    lambda doc: doc["source"].update(run_id=ACTIVE_RUN),
    lambda doc: doc["source"].update(workspace_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
    lambda doc: doc["source"].update(workspace_resource_id=doc["source"]["workspace_resource_id"] + "-other"),
    lambda doc: doc.update(schema_version=2),
])
def test_archive_contract_cannot_expand_producer_or_duplicate_active(tmp_path, mutation):
    f = integrated(tmp_path)
    doc = configuration(f)
    active = runtime.RuntimeConfiguration.parse({
        **doc, "source": {**doc["source"], "run_id": ACTIVE_RUN}})
    mutation(doc)
    (tmp_path / "archive.json").write_bytes(canonical(doc))
    with pytest.raises(ContractError):
        runtime._archived_configurations(
            {"ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON": '["archive.json"]'}, tmp_path, active)


def test_archived_enabled_policy_retains_exact_historical_hash_approval(tmp_path):
    f = integrated(tmp_path)
    doc = configuration(f)
    active = runtime.RuntimeConfiguration.parse({
        **doc, "source": {**doc["source"], "run_id": ACTIVE_RUN}})
    from tests.test_consumer_detectors import series
    _, policy, _, _ = series("upstream")
    doc["detector_policies"] = [asdict(policy)]
    doc["detector_policies"][0]["allowed_quality"] = list(policy.allowed_quality)
    doc["approved_detector_policy_hashes"] = [policy.sha256]
    (tmp_path / "archive.json").write_bytes(canonical(doc))
    archived, = runtime._archived_configurations(
        {"ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON": '["archive.json"]'}, tmp_path, active)
    assert archived.policies == (policy,)
    assert archived.budget.sha256 == runtime.RuntimeConfiguration.parse(doc).budget.sha256
    doc["approved_detector_policy_hashes"] = []
    (tmp_path / "archive.json").write_bytes(canonical(doc))
    with pytest.raises(ContractError, match="approval"):
        runtime._archived_configurations(
            {"ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON": '["archive.json"]'}, tmp_path, active)


def test_observe_only_active_and_two_exact_enforced_archives_never_activate_old_detectors(
        tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.test_consumer_detectors import series
    from tests.test_usage_policy import approved_usage
    f = prepared(tmp_path)
    historical = configuration(f)
    _, policy, _, _ = series("upstream")
    historical["detector_policies"] = [asdict(policy)]
    historical["detector_policies"][0]["allowed_quality"] = list(policy.allowed_quality)
    historical["approved_detector_policy_hashes"] = [policy.sha256]
    earlier = configuration(f)
    earlier["source"]["run_id"] = "earlier-exact-archive"
    documents = {"archive-1.json": canonical(earlier), "archive-2.json": canonical(historical)}
    for name, content in documents.items():
        (tmp_path / name).write_bytes(content)
    active_document = configuration(f)
    active_document["source"]["run_id"] = ACTIVE_RUN
    active_document["budget"]["usage_mode"] = "observe_only"
    active = runtime.RuntimeConfiguration.parse(active_document)
    approved = approved_usage(active.budget, replace(active.budget, max_output_tokens=1))
    env = configure(monkeypatch, f, active, archives=tuple(documents), approval=json.dumps(approved))
    service = runtime.create_worker_model_free_service(env, root=tmp_path)
    assert service.jobs.investigation_approval is None
    assert service.jobs.detector_policies == ()
    assert len(service.archives) == 2
    assert service.archives[1].policies == (policy,)
    assert all(archive.budget.usage_mode == "enforced" for archive in service.archives)
    for archived in service.archived_services.values():
        assert archived.worker.policies == []
        assert archived.jobs.detector_policies == ()
        assert archived.jobs.investigation_approval is None
        assert set(archived.worker.handlers) == {"projection"}
    assert service.job(HEADERS, f.job_id)["status"] == "completed"
    payload = {
        "source": {"producer_key": active.scope.producer.key, "run_id": ACTIVE_RUN,
                   "surfaces": list(active.scope.surfaces)},
        "window": {"start_utc": iso(active.scope.baseline_start),
                   "end_utc": iso(active.scope.reference_time)}, **OVERLAY,
    }
    root_id = f.store.enqueue(WorkRequest("analysis", "new-source-model-free", payload))
    assert service.advance(HEADERS, root_id, {})["status"] == "completed"
    assert f.store.get_job(root_id)["next_job_ids"]
    assert service.jobs.investigation_approval is None
    for kind in ("analysis", "ingest", "coverage", "detector", "reasoning", "investigation", "replacement"):
        archived_job = f.store.enqueue(WorkRequest(kind, "blocked-archive-" + kind, {
            "producer_key": f.scope.producer.key, "run_id": f.scope.run_id, **OVERLAY}))
        with pytest.raises(ContractError, match="archived work"):
            service.advance(HEADERS, archived_job, {})
        assert f.store.get_job(archived_job)["status"] == "queued"
    retry = service.projection(
        {**HEADERS, "Idempotency-Key": "exact-history"}, {"workflow_id": f.job_id})["job_id"]
    assert service.advance(HEADERS, retry, {})["status"] == "completed"
    assert len(f.transport.calls) == f.model_calls_before_recovery
    for name, content in documents.items():
        assert (tmp_path / name).read_bytes() == content


def test_no_archive_configuration_preserves_active_only_lookup(tmp_path, monkeypatch):
    f, _, env, _ = archived_fixture(tmp_path, monkeypatch)
    del env["ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON"]
    service = runtime.create_worker_model_free_service(env, root=tmp_path)
    assert service.archives == ()
    with pytest.raises(ContractError, match="source/run"):
        service.job(HEADERS, f.job_id)


def test_complete_model_free_factory_loads_observe_only_runtime_and_exact_archives(
        tmp_path, monkeypatch):
    from dataclasses import replace
    from datetime import timedelta
    import shutil
    from tests.test_application_integration import CLIENT, OID, ROOT, TENANT, portfolio
    from tests.test_consumer_detectors import series
    from tests.test_usage_policy import approved_usage

    f = integrated(tmp_path)
    for directory in ("agents", "tools"):
        shutil.copytree(ROOT / directory, tmp_path / directory)
    artifacts = tmp_path / ".runtime"
    artifacts.mkdir()
    historical = configuration(f)
    _, policy, _, _ = series("upstream")
    historical["detector_policies"] = [asdict(policy)]
    historical["detector_policies"][0]["allowed_quality"] = list(policy.allowed_quality)
    historical["approved_detector_policy_hashes"] = [policy.sha256]
    earlier = configuration(f)
    earlier["source"]["run_id"] = "earlier-exact-archive"
    active = configuration(f)
    active["source"].update(
        run_id=ACTIVE_RUN, baseline_start=iso(f.scope.baseline_start),
        reference_time=iso(f.scope.baseline_start + timedelta(minutes=21)),
        ingestion_start=iso(f.scope.baseline_start),
        ingestion_end=iso(f.scope.baseline_start + timedelta(minutes=24)))
    active["budget"].update(usage_mode="observe_only", deadline_seconds=180)
    documents = {"runtime.json": active, "runtime-archive-1.json": earlier,
                 "runtime-archive-2.json": historical,
                 "bindings.json": json.loads(portfolio().bindings_json)}
    for name, document in documents.items():
        (artifacts / name).write_bytes(canonical(document))
    before = {name: (artifacts / name).read_bytes() for name in documents}
    parsed = runtime.RuntimeConfiguration.parse(active)
    embedding = replace(parsed.budget, api_calls=3, max_output_tokens=1)
    approved = approved_usage(parsed.budget, embedding)
    environment = {
        "ITOPS_RUNTIME_CONFIG_PATH": ".runtime/runtime.json",
        "ITOPS_BINDINGS_PATH": ".runtime/bindings.json",
        "ITOPS_ARCHIVED_RUNTIME_CONFIG_PATHS_JSON": json.dumps([
            ".runtime/runtime-archive-1.json", ".runtime/runtime-archive-2.json"]),
        "AZURE_CLIENT_ID": CLIENT, "ITOPS_SQL_SERVER": "unit.database.windows.net",
        "ITOPS_SQL_DATABASE": "itops", "ENTRA_TENANT_ID": TENANT, "ENTRA_AUDIENCE": CLIENT,
        "ITOPS_SERVICE_CALLERS_JSON": json.dumps([
            {"oid": OID, "client_id": CLIENT, "roles": ["Worker.Invoke"]}]),
        "ITOPS_REVIEW_ORIGIN": "https://itops-unit-review.azurewebsites.net",
        "ITOPS_PROJECTION_AUDIENCE": CLIENT, "ITOPS_PROJECTION_TOKEN_SCOPE": f"api://{CLIENT}/.default",
        "ITOPS_EMBEDDING_BUDGET_JSON": json.dumps(asdict(embedding)),
        "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": json.dumps(approved),
    }
    def forbidden(*args, **kwargs):
        raise AssertionError("model-free composition cannot make network/model calls")
    monkeypatch.setattr(runtime, "_identity", lambda _: SimpleNamespace(get_token=forbidden))
    monkeypatch.setattr(runtime, "_evidence", lambda *args: f.runtime.evidence)
    monkeypatch.setattr(runtime, "verify_sql_native_runtime", lambda: None)
    monkeypatch.setattr(runtime, "managed_identity_sql_factory", lambda *args: forbidden)
    monkeypatch.setattr(runtime, "SqlServerWorkflowStore", lambda _: f.store)
    monkeypatch.setattr(runtime, "SqlServerReviewRepository", lambda _: f.repository)
    monkeypatch.setattr(runtime.ServiceAuthenticator, "authenticate",
                        lambda self, headers, role: Auth().authenticate(headers, role))
    for name in ("BudgetLedger", "OpenAIResponsesTransport", "PortfolioRuntime"):
        monkeypatch.setattr(runtime, name, forbidden)
    service = runtime.create_worker_model_free_service(environment, root=tmp_path)
    assert service.jobs.scope == parsed.scope
    assert service.jobs.runtime is service.jobs.investigation_approval is None
    assert service.archives[1].policies == (policy,)
    assert all(not archived.worker.policies for archived in service.archived_services.values())
    payload = {
        "source": {"producer_key": parsed.scope.producer.key, "run_id": ACTIVE_RUN,
                   "surfaces": list(parsed.scope.surfaces)},
        "window": {"start_utc": iso(parsed.scope.baseline_start),
                   "end_utc": iso(parsed.scope.reference_time)}, **OVERLAY,
    }
    root_id = f.store.enqueue(WorkRequest("analysis", "complete-factory-source-switch", payload))
    assert service.advance(HEADERS, root_id, {})["status"] == "completed"
    children = [f.store.get_job(key) for key in f.store.get_job(root_id)["next_job_ids"]]
    assert len(children) == 2 and all(child["kind"] == "ingest" for child in children)
    assert children[0]["payload"]["ingestion_window"] == {
        "start": iso(parsed.scope.ingestion_start),
        "end": iso(parsed.scope.ingestion_start + timedelta(minutes=15))}
    assert children[1]["payload"]["ingestion_window"] == {
        "start": iso(parsed.scope.ingestion_start + timedelta(minutes=15)),
        "end": iso(parsed.scope.ingestion_end)}
    assert before == {name: (artifacts / name).read_bytes() for name in documents}
