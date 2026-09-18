"""Offline scope regressions; these fixtures are not live SQL acceptance evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agentic_itops.review import Principal, SQLiteReviewRepository, create_app
from agentic_itops.review.advancement import descendant_source
from agentic_itops.review.contracts import OVERLAY, ReviewError
from tests.test_review import OID, ORIGIN, SOURCE


ROOT, INGEST, DETECTOR, COVERAGE, MODEL, UNRELATED = [char * 64 for char in "abcdef"]
WINDOW = {"start_utc": "2026-09-17T22:00:00Z", "end_utc": "2026-09-17T22:20:00Z"}


@pytest.fixture
def graph():
    source = deepcopy(SOURCE)
    child = {
        "producer_key": source["producer_key"], "run_id": source["run_id"],
        "window": {"run_id": source["run_id"], "surfaces": source["surfaces"],
                   "observed_start": WINDOW["start_utc"], "observed_end": WINDOW["end_utc"]},
        **OVERLAY,
    }
    jobs = {
        ROOT: {"kind": "analysis", "payload": {"source": source, "window": WINDOW, **OVERLAY},
               "status": "completed", "next_job_ids": [INGEST]},
        INGEST: {"kind": "ingest", "payload": {"source": source, **OVERLAY},
                 "status": "completed", "result": {"child_job_ids": [DETECTOR, COVERAGE]}},
        DETECTOR: {"kind": "detector", "payload": deepcopy(child), "status": "completed", "next_job_ids": [MODEL]},
        COVERAGE: {"kind": "coverage", "payload": deepcopy(child), "status": "completed", "next_job_ids": [MODEL]},
        MODEL: {"kind": "reasoning", "status": "queued", "payload": {
            "producer_key": source["producer_key"], "run_id": source["run_id"], **OVERLAY}},
        UNRELATED: {"kind": "reasoning", "status": "queued", "payload": {
            "producer_key": source["producer_key"], "run_id": source["run_id"], **OVERLAY}},
    }
    return SimpleNamespace(jobs=jobs, get_job=lambda job_id: jobs.get(job_id))


def test_immutable_sql_lineage_resolves_shared_internal_child_without_mutation(graph):
    before = deepcopy(graph.jobs)
    assert descendant_source(graph, ROOT, MODEL, graph.jobs[ROOT]) == SOURCE
    assert graph.jobs == before
    assert "source" not in graph.jobs[MODEL]["payload"]


@pytest.mark.parametrize("mutate", [
    lambda jobs: jobs[DETECTOR]["payload"].update(producer_key="foreign"),
    lambda jobs: jobs[DETECTOR]["payload"].update(run_id="foreign"),
    lambda jobs: jobs[DETECTOR]["payload"]["window"].update(run_id="foreign"),
    lambda jobs: jobs[DETECTOR]["payload"]["window"].update(surfaces=["downstream"]),
    lambda jobs: jobs[DETECTOR]["payload"]["window"].update(surfaces=[]),
    lambda jobs: jobs[DETECTOR]["payload"]["window"].update(observed_start="2026-09-17T21:59:59Z"),
    lambda jobs: jobs[DETECTOR]["payload"]["window"].update(observed_end="2026-09-17T22:20:01Z"),
    lambda jobs: jobs[DETECTOR]["payload"].update(execution_allowed=True),
    lambda jobs: jobs[MODEL]["payload"].update(source={**SOURCE, "domain": "foreign"}),
    lambda jobs: jobs[MODEL]["payload"].pop("run_id"),
    lambda jobs: jobs[DETECTOR].update(next_job_ids=[ROOT]),
    lambda jobs: jobs[DETECTOR].update(next_job_ids=["not-a-job"]),
    lambda jobs: jobs[ROOT].update(next_job_ids=[MODEL]),
    lambda jobs: jobs[INGEST].update(status="queued"),
    lambda jobs: jobs.pop(MODEL),
])
def test_scope_drift_and_unproven_lineage_fail_closed(graph, mutate):
    mutate(graph.jobs)
    with pytest.raises(ReviewError):
        descendant_source(graph, ROOT, MODEL, graph.jobs[ROOT])


def test_unrelated_same_run_job_is_not_authorized_by_catalog_or_root(graph):
    with pytest.raises(ReviewError, match="not a durable descendant"):
        descendant_source(graph, ROOT, UNRELATED, graph.jobs[ROOT])


def test_scope_graph_has_deadline_and_fanout_bounds(graph, monkeypatch):
    from agentic_itops.review import advancement
    clock = iter([0, 11])
    monkeypatch.setattr(advancement.time, "monotonic", lambda: next(clock))
    with pytest.raises(ReviewError, match="deadline"):
        descendant_source(graph, ROOT, MODEL, graph.jobs[ROOT])
    monkeypatch.setattr(advancement.time, "monotonic", lambda: 0)
    graph.jobs[ROOT]["next_job_ids"] = [f"{i:064x}" for i in range(65)]
    with pytest.raises(ReviewError, match="bounded child graph"):
        descendant_source(graph, ROOT, MODEL, graph.jobs[ROOT])


@pytest.fixture
def browser(graph):
    calls = []
    advancer = SimpleNamespace(
        readiness=lambda job_id: calls.append(("readiness", job_id)) or {"job_id": job_id, **OVERLAY},
        budget=lambda job_id: calls.append(("budget", job_id)) or {"job_id": job_id, **OVERLAY},
        advance=lambda job_id: calls.append(("advance", job_id)) or {"job_id": job_id, **OVERLAY},
    )
    repo = SQLiteReviewRepository()
    app = create_app(
        {"TESTING": True, "PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "offline-only-" * 4,
         "TEST_AUTHENTICATOR": SimpleNamespace(
             authenticate=lambda req: Principal(OID, frozenset({"Operator"}), "bearer"))},
        repository=repo, work_submitter=graph, work_advancer=advancer,
        entitlements={OID: {(SOURCE["producer_key"], SOURCE["run_id"], SOURCE["domain"])}},
        source_catalog={(SOURCE["producer_key"], SOURCE["run_id"], SOURCE["domain"]): SOURCE},
    )
    yield app.test_client(), calls
    repo.close()


@pytest.mark.parametrize("route", ["jobs", "job-budgets", "job-readiness"])
def test_child_read_requires_authorized_exact_root(browser, graph, route):
    client, calls = browser
    assert client.get(f"/{route}/{MODEL}").status_code == 403
    assert client.get(f"/{route}/{MODEL}?root_job_id={ROOT}").status_code == 200
    if route == "jobs":
        assert client.get(f"/{route}/{MODEL}?root_job_id={ROOT}").json["source"] == SOURCE
    assert client.get(f"/{route}/{UNRELATED}?root_job_id={ROOT}").status_code == 403
    before = list(calls)
    graph.jobs[ROOT]["payload"]["source"] = {**SOURCE, "domain": "foreign"}
    assert client.get(f"/{route}/{MODEL}?root_job_id={ROOT}").status_code == 403
    assert calls == before


def test_duplicate_or_invalid_context_is_rejected_and_ui_exposes_exact_context_fields(browser):
    client, calls = browser
    assert client.get(f"/jobs/{MODEL}?root_job_id={ROOT}&root_job_id={ROOT}").status_code == 422
    assert client.get(f"/jobs/{MODEL}?root_job_id=invalid").status_code == 422
    assert not calls
    page = client.get("/")
    assert page.status_code == 200
    for purpose in ("Lookup", "Budget", "Readiness", "Advancement", "Recovery"):
        assert (purpose + " root context (optional)").encode() in page.data


def test_direct_source_job_and_explicit_child_advance_remain_separate(browser):
    client, calls = browser
    assert client.get(f"/jobs/{ROOT}").status_code == 200
    denied = client.post(f"/jobs/{MODEL}/advance", json={}, headers={"Idempotency-Key": "denied"})
    assert denied.status_code == 403
    assert not calls
    allowed = client.post(f"/jobs/{MODEL}/advance?root_job_id={ROOT}", json={},
                          headers={"Idempotency-Key": "authorized"})
    assert allowed.status_code == 200
    assert calls == [("advance", MODEL)]
