"""Offline review tests. SQLite/fake queue results are NOT Azure SQL or Entra proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import pytest

from agentic_itops.review import (
    ConsumerWorkAdapter, EntraAuthenticator, Principal, SQLiteReviewRepository,
    SqlServerReviewRepository, create_app,
)
from agentic_itops.review.contracts import AGENTS, OVERLAY, ReviewError, digest


TENANT = "00000000-0000-0000-0000-000000000001"
OID = "00000000-0000-0000-0000-000000000002"
AUTHOR = "00000000-0000-0000-0000-000000000003"
OTHER = "00000000-0000-0000-0000-000000000004"
AUDIENCE = "review-api"
ORIGIN = "https://review.example.test"
SOURCE = {"producer_key": "producer-a", "run_id": "run-a", "domain": "energy",
          "surfaces": ["upstream"]}
ROLES = ["Viewer", "Analyst", "Reviewer", "KnowledgeReviewer", "Operator"]


def iso(days=0):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


def result(incident_id="incident-a", revision=1):
    draft = "AI draft: no action was performed. Observed state remains active."
    return {
        "schema_version": "1.0", "result_id": f"{incident_id}-result-{revision}",
        "incident_id": incident_id, "analysis_revision": revision,
        "workflow_id": "workflow-a", "status": "completed", "source": deepcopy(SOURCE),
        "source_hash": digest(SOURCE), "observed_lifecycle": "active",
        "evidence_refs": [{"evidence_id": "event-a", "sha256": "a" * 64,
                           "retained_until_utc": "2098-01-01T00:00:00Z", "verified": True}],
        "stage_results": {"ops-orchestrator": {"status": "completed", "output": "Hypothesis only."},
                          "postmortem-drafter": {"status": "completed", "output": draft}},
        "versions": {"detector": "v1", "workflow": "v1"},
        "created_at_utc": "2026-01-01T00:00:00Z",
        "postmortem_draft": {"draft_id": "draft-a", "text": draft, "sha256": digest(draft),
                             "authorship": {"kind": "ai"}, "applicability": "Synthetic energy only"},
        **OVERLAY,
    }


class FakeQueue:
    """Test double only; production must inject a durable consumer adapter."""
    def __init__(self):
        self.requests = {}
        self.jobs = {}

    def enqueue(self, request):
        prior = self.requests.get(request.logical_key)
        if prior:
            assert prior.payload == request.payload
            return request.logical_key
        self.requests[request.logical_key] = request
        self.jobs[request.logical_key] = {"payload": request.payload, "status": "queued"}
        return request.logical_key

    def get_job(self, job_id):
        return self.jobs.get(job_id)


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(signing_key):
    def make(roles=None, **overrides):
        now = datetime.now(timezone.utc).timestamp()
        claims = {
            "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": AUDIENCE,
            "tid": TENANT, "oid": OID, "roles": ROLES if roles is None else roles,
            "iat": int(now) - 10, "nbf": int(now) - 10, "exp": int(now) + 3600,
        }
        claims.update(overrides)
        return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "offline"})
    return make


@pytest.fixture
def repo():
    value = SQLiteReviewRepository()
    yield value
    value.close()


@pytest.fixture
def queue():
    return FakeQueue()


@pytest.fixture
def app(repo, queue, signing_key):
    auth = EntraAuthenticator(TENANT, AUDIENCE, key_resolver=lambda _: signing_key.public_key(),
                               allow_easyauth=True)
    return create_app(
        {"TESTING": True, "PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "offline-test-only-" * 4},
        repository=repo, work_submitter=queue, authenticator=auth,
        entitlements={OID: {("producer-a", "run-a")}, AUTHOR: {("producer-a", "run-a")}},
        source_catalog={("producer-a", "run-a"): SOURCE},
    )


@pytest.fixture
def client(app):
    return app.test_client()


def headers(token, etag=None, key="request-one", **claims):
    value = {"Authorization": "Bearer " + token(**claims), "Idempotency-Key": key}
    if etag:
        value["If-Match"] = etag
    return value


def post_review(client, token, etag, action="accept", key="review-one", reason="Evidence inspected.", **claims):
    return client.post("/incidents/incident-a/reviews", json={"action": action, "reason": reason},
                       headers=headers(token, etag, key, **claims))


def accepted(repo, client, token, value=None):
    revision = repo.register_result(value or result())
    receipt = post_review(client, token, revision["etag"])
    assert receipt.status_code == 200, receipt.json
    return receipt.json["etag"]


def approval_body():
    return {"incident_id": "incident-a", "analysis_revision": 1,
            "draft_sha256": result()["postmortem_draft"]["sha256"], "reason": "Independent review complete.",
            "expires_at_utc": "2099-01-01T00:00:00Z"}


def test_work_window_inputs_accept_exact_utc_seconds_and_microseconds(client, token):
    from html.parser import HTMLParser
    import re

    class WindowInputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inputs = []

        def handle_starttag(self, tag, attrs):
            value = dict(attrs)
            if tag == "input" and value.get("name") in {"start_utc", "end_utc"}:
                self.inputs.append(value)

    page = client.get("/", headers=headers(token))
    assert page.status_code == 200
    parser = WindowInputs()
    parser.feed(page.get_data(as_text=True))
    assert len(parser.inputs) == 6
    for field in parser.inputs:
        assert field["type"] == "text" and "required" in field
        assert "step" not in field
        pattern = field["pattern"]
        assert re.fullmatch(pattern, "2026-09-14T23:03:30Z")
        assert re.fullmatch(pattern, "2026-09-14T23:03:30.123456Z")
        for invalid in ("2026-09-14T23:03Z", "2026-09-14T23:03:30",
                        "2026-09-14T23:03:30+04:00", "09/14/2026 11:03 PM"):
            assert not re.fullmatch(pattern, invalid)


@pytest.mark.parametrize("fraction", ["", ".123456"])
def test_work_receipt_preserves_explicit_utc_precision(client, token, queue, fraction):
    value = {"source": deepcopy(SOURCE), "window": {
        "start_utc": f"2026-01-01T00:00:30{fraction}Z",
        "end_utc": f"2026-01-01T01:00:30{fraction}Z",
    }}
    response = client.post("/analysis-jobs", json=value, headers=headers(token))
    assert response.status_code == 202
    assert response.json["window"] == value["window"]


def approve(repo, client, token, value=None):
    etag = accepted(repo, client, token, value)
    response = client.post("/publications", json=approval_body(), headers=headers(token, etag, "publish-one"))
    assert response.status_code == 202, response.json
    return response.json


@pytest.mark.parametrize("path", ["/", "/incidents", "/csrf", "/jobs/guessed",
                                 "/incidents/guessed/revisions/1", "/publications/guessed"])
def test_every_data_endpoint_requires_authentication(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.json["OperationalUse"] == "prohibited"


def test_health_discloses_only_liveness(client):
    assert client.get("/health").json == {"status": "alive"}


@pytest.mark.parametrize("claims", [
    {"tid": OTHER}, {"aud": "wrong"}, {"exp": 1},
    {"nbf": 9999999999}, {"iss": "https://attacker.invalid/"},
    {"oid": "alice@example.test"}, {"roles": "Reviewer"},
    {"aud": [AUDIENCE, "another-audience"]},
])
def test_bad_jwt_claims_fail_closed(client, token, claims):
    assert client.get("/incidents", headers=headers(token, **claims)).status_code == 401


def test_bad_signature_and_unsecured_token_denied(client, token):
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    decoded = jwt.decode(token(), options={"verify_signature": False})
    for value in (jwt.encode(decoded, wrong, algorithm="RS256"),
                  jwt.encode(decoded, key="", algorithm="none"),
                  jwt.encode(decoded, key="offline-test-HMAC-key-not-RSA-" * 2, algorithm="HS256"),
                  "not-a-jwt"):
        assert client.get("/incidents", headers={"Authorization": f"Bearer {value}"}).status_code == 401


@pytest.mark.parametrize("missing", ["exp", "nbf", "iat", "iss", "aud", "tid", "oid"])
def test_required_claims_cannot_be_omitted(client, token, signing_key, missing):
    claims = jwt.decode(token(), options={"verify_signature": False})
    del claims[missing]
    value = jwt.encode(claims, signing_key, algorithm="RS256")
    assert client.get("/incidents", headers={"Authorization": f"Bearer {value}"}).status_code == 401


@pytest.mark.parametrize("role", ROLES)
def test_signed_application_roles_can_read(client, token, role):
    assert client.get("/incidents", headers=headers(token, roles=[role])).status_code == 200


def test_unknown_role_and_platform_actor_headers_grant_nothing(client, token):
    assert client.get("/incidents", headers=headers(token, roles=["Administrator"])).status_code == 403
    assert client.get("/incidents", headers={
        "X-MS-CLIENT-PRINCIPAL": '{"oid":"' + OID + '","roles":["Reviewer"]}',
        "X-Actor-Oid": OID, "X-Roles": "Reviewer",
    }).status_code == 401


def test_easyauth_requires_signed_token_and_bearer_takes_precedence(client, token):
    assert client.get("/incidents", headers={"X-MS-TOKEN-AAD-ID-TOKEN": token()}).status_code == 200
    assert client.get("/incidents", headers={"X-MS-TOKEN-AAD-ID-TOKEN": "bad"}).status_code == 401
    assert client.get("/incidents", headers={
        "X-MS-TOKEN-AAD-ID-TOKEN": token(), "Authorization": "Bearer invalid",
    }).status_code == 401


@pytest.mark.parametrize("path", ["/", "/incidents", "/csrf", "/not-found"])
def test_referrer_policy_supports_same_origin_csrf_without_external_disclosure(client, token, path):
    response = client.get(path, headers=headers(token))
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_scope_is_server_controlled_for_list_detail_export_and_write(repo, client, token):
    revision = repo.register_result(result())
    unauthorized = headers(token, revision["etag"], oid=OTHER)
    assert client.get("/incidents", headers=unauthorized).json["incidents"] == []
    for path in ("/incidents/incident-a/revisions/1", "/incidents/incident-a/revisions/1/export",
                 "/?incident_id=incident-a"):
        assert client.get(path, headers=unauthorized).status_code == 403
    assert client.post("/incidents/incident-a/reviews", headers=unauthorized,
                       json={"action": "accept", "reason": "Guessed ID"}).status_code == 403
    assert post_review(client, token, revision["etag"], roles=["Viewer"]).status_code == 403


def test_production_factory_has_no_test_or_default_trust_bypass(repo, signing_key):
    config = {"PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "offline-test-only-" * 4,
              "ENTRA_TENANT_ID": TENANT, "ENTRA_AUDIENCE": AUDIENCE}
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
        create_app(repository=repo)
    with pytest.raises(ValueError, match="test authenticator"):
        create_app({**config, "TEST_AUTHENTICATOR": object()}, repository=repo)
    with pytest.raises(ValueError, match="EntraAuthenticator"):
        create_app(config, repository=repo, authenticator=object())
    for origin in ("http://review.example.test", "https://review.example.test/path", "",
                   "https://user:password@review.example.test", "https://review.example.test?x=1"):
        with pytest.raises(ValueError, match="PUBLIC_ORIGIN"):
            create_app({**config, "PUBLIC_ORIGIN": origin}, repository=repo)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        create_app({**config, "SECRET_KEY": None}, repository=repo)
    with pytest.raises(ValueError, match="tenant"):
        create_app({**config, "ENTRA_TENANT_ID": None}, repository=repo)


def test_explicit_test_authenticator_only_works_in_testing(repo):
    class TestAuth:
        def authenticate(self, request):
            return Principal(OID, frozenset({"Viewer"}))
    app = create_app({"TESTING": True, "PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "test-only-" * 5,
                      "TEST_AUTHENTICATOR": TestAuth()}, repository=repo)
    assert app.test_client().get("/incidents").status_code == 200
    assert app.test_client().get("/incidents").json["incidents"] == []


def test_easyauth_csrf_requires_allowed_origin_and_identity(repo, client, token):
    revision = repo.register_result(result())
    browser = {"X-MS-TOKEN-AAD-ID-TOKEN": token(), "If-Match": revision["etag"],
               "Idempotency-Key": "browser-one"}
    body = {"action": "accept", "reason": "Browser review"}
    assert client.post("/incidents/incident-a/reviews", json=body, headers=browser).status_code == 403
    csrf = client.get("/csrf", headers=browser, base_url=ORIGIN)
    assert "Secure;" in csrf.headers["Set-Cookie"] and "HttpOnly;" in csrf.headers["Set-Cookie"]
    browser["X-CSRF-Token"] = csrf.json["csrf_token"]
    browser["Origin"] = "https://attacker.invalid"
    assert client.post("/incidents/incident-a/reviews", json=body, headers=browser,
                       base_url=ORIGIN).status_code == 403
    browser["Origin"] = ORIGIN
    switched = {**browser, "X-MS-TOKEN-AAD-ID-TOKEN": token(oid=AUTHOR)}
    assert client.post("/incidents/incident-a/reviews", json=body, headers=switched,
                       base_url=ORIGIN).status_code == 403
    response = client.post("/incidents/incident-a/reviews", json=body, headers=browser, base_url=ORIGIN)
    assert response.status_code == 200, response.json


def test_bearer_is_csrf_exempt_but_not_scope_or_role(repo, client, token):
    revision = repo.register_result(result())
    assert post_review(client, token, revision["etag"], oid=OTHER).status_code == 403
    assert post_review(client, token, revision["etag"], roles=["Analyst"]).status_code == 403
    assert post_review(client, token, revision["etag"], roles=["Reviewer"]).status_code == 200


def test_reviews_are_idempotent_conflict_checked_and_cas_bound(repo, client, token):
    revision = repo.register_result(result())
    first = post_review(client, token, revision["etag"])
    assert first.status_code == 200
    retry = post_review(client, token, revision["etag"])
    assert retry.json == first.json
    assert post_review(client, token, revision["etag"], reason="changed").status_code == 409
    assert post_review(client, token, revision["etag"], key="new-request").status_code == 409
    assert post_review(client, token, first.json["etag"], key="close-one", action="close").status_code == 200
    current = repo.get_revision("incident-a", 1)
    assert current["result"]["observed_lifecycle"] == "active"
    assert current["result"]["execution_allowed"] is False
    assert current["review_state"] == "closed"
    assert len(current["review_history"]) == 2


@pytest.mark.parametrize("body", [
    {"action": "execute", "reason": "Never"}, {"action": "accept", "reason": ""},
    {"action": "accept", "reason": "x", "execution_allowed": True},
    {"action": "accept", "reason": "x", "observed_lifecycle": "resolved"},
    {"action": ["accept"], "reason": "x"}, {"action": "send", "reason": "x"},
    {"action": "suppress", "reason": "x"}, {"action": "close"},
])
def test_unknown_or_escalating_review_bodies_are_422(repo, client, token, body):
    revision = repo.register_result(result())
    response = client.post("/incidents/incident-a/reviews", json=body, headers=headers(token, revision["etag"]))
    assert response.status_code == 422
    assert repo.get_revision("incident-a", 1)["review_state"] == "pending"


def test_close_requires_acceptance_and_writes_require_headers(repo, client, token):
    revision = repo.register_result(result())
    assert post_review(client, token, revision["etag"], action="close").status_code == 409
    assert client.post("/incidents/incident-a/reviews", json={"action": "accept", "reason": "x"},
                       headers={"Authorization": "Bearer " + token()}).status_code == 422
    assert client.post("/incidents/incident-a/reviews", data='{"action":"accept","action":"close","reason":"x"}',
                       content_type="application/json",
                       headers=headers(token, revision["etag"])).status_code == 422


def test_internal_results_are_immutable_and_no_public_injection(repo, client, token):
    value = result()
    first = repo.register_result(value)
    assert repo.register_result(deepcopy(value))["result_hash"] == first["result_hash"]
    value["status"] = "failed"
    with pytest.raises(ReviewError, match="immutable revision"):
        repo.register_result(value)
    newer = result(revision=2)
    newer["source"]["run_id"] = "changed"
    newer["source_hash"] = digest(newer["source"])
    with pytest.raises(ReviewError, match="source is immutable"):
        repo.register_result(newer)
    newer = result(revision=2)
    newer["evidence_refs"][0]["sha256"] = "b" * 64
    with pytest.raises(ReviewError, match="evidence hash"):
        repo.register_result(newer)
    assert client.post("/results", json=result(), headers=headers(token)).status_code == 404


@pytest.mark.parametrize("change", [
    lambda v: v.update(execution_allowed=True),
    lambda v: v.update(IsSynthetic=False),
    lambda v: v.update(OperationalUse="permitted"),
    lambda v: v.update(analysis_revision=True),
    lambda v: v.update(analysis_revision=0),
    lambda v: v.update(stage_results={"unknown-agent": {"status": "completed"}}),
    lambda v: v["stage_results"]["ops-orchestrator"].update(output={"execution_allowed": True}),
    lambda v: v["evidence_refs"][0].update(sha256="missing"),
    lambda v: v["postmortem_draft"].update(sha256="wrong"),
    lambda v: v.update(source_hash="wrong"),
    lambda v: v["stage_results"]["ops-orchestrator"].update(evidence_refs=["unknown"]),
])
def test_nested_result_contracts_reject_invalid_authority_and_binding(repo, change):
    value = result()
    change(value)
    with pytest.raises(ReviewError):
        repo.register_result(value)


def test_correction_preserves_receipts_and_marks_closed_review_needs_evidence(repo, client, token):
    etag = accepted(repo, client, token)
    closed = post_review(client, token, etag, action="close", key="close")
    assert closed.status_code == 200
    revised = repo.register_result(result(revision=2))
    assert revised["review_state"] == "needs_evidence"
    assert revised["result"]["observed_lifecycle"] == "active"
    old = repo.get_revision("incident-a", 1)
    assert old["review_state"] == "stale" and not old["eligible"]
    assert {receipt["action"] for receipt in old["review_history"]} == {"accept", "close"}


def test_publication_is_independently_approved_transactional_pending_not_searchable(repo, client, token):
    publication = approve(repo, client, token)
    assert publication["status"] == "approved"
    assert publication["indexing_state"] == "pending" and publication["searchable"] is False
    assert publication["eligible_until"] == "2098-01-01T00:00:00Z"
    assert publication["draft"]["authorship"] == {"kind": "ai"}
    pending = repo.pending_outbox()
    assert len(pending) == 1 and pending[0]["kind"] == "publish-approved-revision"
    assert pending[0]["payload"]["result_hash"] == publication["result_hash"]
    assert pending[0]["payload"]["execution_allowed"] is False
    response = client.get("/publications/" + publication["publication_id"], headers=headers(token))
    assert response.json["eligible"] and not response.json["searchable"]
    assert client.get("/publications/" + publication["publication_id"],
                      headers=headers(token, oid=OTHER)).status_code == 403


def test_publication_retries_and_stale_revision(repo, client, token):
    etag = accepted(repo, client, token)
    first = client.post("/publications", json=approval_body(), headers=headers(token, etag, "pub"))
    retry = client.post("/publications", json=approval_body(), headers=headers(token, etag, "pub"))
    assert first.status_code == 202 and retry.json == first.json
    changed = {**approval_body(), "reason": "changed"}
    assert client.post("/publications", json=changed, headers=headers(token, etag, "pub")).status_code == 409
    assert client.post("/publications", json=approval_body(),
                       headers=headers(token, etag, "pub-new")).status_code == 409


def test_human_author_cannot_self_approve_or_escalate_role(repo, client, token):
    value = result()
    value["postmortem_draft"]["authorship"] = {"kind": "human", "oid": OID}
    etag = accepted(repo, client, token, value)
    assert client.post("/publications", json=approval_body(),
                       headers=headers(token, etag, roles=["Reviewer"])).status_code == 403
    assert client.post("/publications", json=approval_body(),
                       headers=headers(token, etag)).status_code == 403
    assert repo.pending_outbox() == []
    independent = client.post("/publications", json=approval_body(),
                              headers=headers(token, etag, oid=AUTHOR))
    assert independent.status_code == 202


@pytest.mark.parametrize("change", [
    lambda v: v["evidence_refs"][0].update(verified=False),
    lambda v: v["stage_results"]["postmortem-drafter"].update(status="failed"),
    lambda v: v.pop("postmortem_draft"),
])
def test_publication_requires_verified_complete_exact_draft(repo, client, token, change):
    value = result()
    change(value)
    etag = accepted(repo, client, token, value)
    assert client.post("/publications", json=approval_body(),
                       headers=headers(token, etag)).status_code == 409


def test_publication_failure_rolls_back_approval_and_outbox(repo, client, token, monkeypatch):
    etag = accepted(repo, client, token)
    def fail(*args):
        raise RuntimeError("fake dependency failure containing a secret")
    monkeypatch.setattr(repo, "_outbox", fail)
    response = client.post("/publications", json=approval_body(), headers=headers(token, etag))
    assert response.status_code == 503 and "secret" not in response.get_data(as_text=True)
    assert repo.get_revision("incident-a", 1)["etag"] == etag
    with repo.transaction() as cursor:
        assert cursor.execute("SELECT COUNT(*) FROM review_publications").fetchone()[0] == 0
    assert repo.pending_outbox() == []


def test_revocation_invalidates_reverse_dependencies_and_cancels_pending_publish(repo, client, token):
    publication = approve(repo, client, token)
    dependent = result("incident-b")
    dependent["knowledge_dependencies"] = [publication["publication_id"]]
    repo.register_result(dependent)
    pub = repo.get_publication(publication["publication_id"])
    response = client.post(f"/publications/{pub['publication_id']}/revoke", json={"reason": "Evidence withdrawn"},
                           headers=headers(token, pub["etag"], "revoke"))
    assert response.status_code == 200 and response.json["removal_state"] == "pending"
    assert not repo.get_revision("incident-b", 1)["eligible"]
    assert not repo.get_publication(pub["publication_id"])["eligible"]
    assert all(row["kind"] != "publish-approved-revision" for row in repo.pending_outbox())
    assert {"exclude-publication", "invalidate-workflows"} <= {row["kind"] for row in repo.pending_outbox()}
    retry = client.post(f"/publications/{pub['publication_id']}/revoke", json={"reason": "Evidence withdrawn"},
                        headers=headers(token, pub["etag"], "revoke"))
    assert retry.json == response.json


def test_supersession_revokes_old_publication_and_history_remains(repo, client, token):
    publication = approve(repo, client, token)
    repo.register_result(result(revision=2))
    old = repo.get_publication(publication["publication_id"])
    assert old["status"] == "revoked" and old["searchable"] is False
    assert repo.get_revision("incident-a", 1)["review_history"]
    assert repo.get_revision("incident-a", 2)["review_state"] == "pending"


def test_evidence_invalidation_is_internal_and_revokes_publications(repo, client, token):
    publication = approve(repo, client, token)
    assert repo.invalidate_evidence("event-a", "Retention/erasure requirement") == [("incident-a", 1)]
    assert not repo.get_publication(publication["publication_id"])["eligible"]
    assert not repo.get_revision("incident-a", 1)["eligible"]


def work():
    return {"source": deepcopy(SOURCE), "window": {"start_utc": "2026-01-01T00:00:00Z",
                                                  "end_utc": "2026-01-02T00:00:00Z"}}


@pytest.mark.parametrize("route", ["/analysis-jobs", "/hygiene-jobs"])
def test_work_submission_queues_without_execution_and_retries(repo, queue, client, token, route):
    first = client.post(route, json=work(), headers=headers(token))
    assert first.status_code == 202 and first.json["status"] == "queued"
    assert client.post(route, json=work(), headers=headers(token)).json == first.json
    assert len(queue.requests) == 1
    request = next(iter(queue.requests.values()))
    assert request.payload["execution_allowed"] is False
    assert request.payload["OperationalUse"] == "prohibited"
    status = client.get(first.headers["Location"], headers=headers(token))
    assert status.json["status"] == "queued"
    assert client.get(first.headers["Location"], headers=headers(token, oid=OTHER)).status_code == 403
    changed = work()
    changed["window"]["end_utc"] = "2026-01-03T00:00:00Z"
    assert client.post(route, json=changed, headers=headers(token)).status_code == 409
    assert client.post(route, json=work(), headers=headers(token, roles=["Viewer"])).status_code == 403


@pytest.mark.parametrize("change", [
    lambda v: v.update(query="arbitrary KQL"),
    lambda v: v.update(url="https://attacker.invalid"),
    lambda v: v.update(agent="override"),
    lambda v: v.update(index="override"),
    lambda v: v["source"].update(blob="arbitrary"),
    lambda v: v["window"].update(start_utc="2026-01-01"),
    lambda v: v["window"].update(end_utc="2028-01-01T00:00:00Z"),
    lambda v: v["window"].update(end_utc="2025-01-01T00:00:00Z"),
])
def test_work_rejects_arbitrary_queries_locations_or_unbounded_windows(client, token, change):
    value = work()
    change(value)
    assert client.post("/analysis-jobs", json=value, headers=headers(token)).status_code == 422


def test_work_source_catalog_is_authoritative(client, token):
    value = work()
    value["source"]["domain"] = "unregistered"
    assert client.post("/analysis-jobs", json=value, headers=headers(token)).status_code == 403
    value = work()
    value["source"]["surfaces"] = ["unregistered"]
    assert client.post("/analysis-jobs", json=value, headers=headers(token)).status_code == 403


def test_dashboard_is_literal_xss_safe_and_nine_stages_are_honest(repo, client, token):
    value = result()
    value["summary"] = '<script>alert("model")</script><img src=x onerror=alert(1)>'
    value["stage_results"]["ops-orchestrator"]["output"] = value["summary"]
    repo.register_result(value)
    response = client.get("/?incident_id=incident-a&scoutTheme=light", headers=headers(token))
    assert response.status_code == 200, response.json
    html = response.get_data(as_text=True)
    assert '&lt;script&gt;alert(' in html and "<script>alert" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    for agent in AGENTS:
        assert f"<h4>{agent}</h4>" in html
    assert html.count("No contribution recorded. This is not a completed stage.") == 7
    assert "SYNTHETIC ONLY" in html and "OperationalUse=prohibited" in html
    assert "No gate receipt supplied. Do not infer gate success." in html
    assert "script-src 'self' 'nonce-" in response.headers["Content-Security-Policy"]
    for section in ("inbox", "incident", "proposal", "learning", "hygiene"):
        assert f'id="{section}"' in html
    js = client.get("/static/review.js", headers=headers(token)).get_data(as_text=True)
    assert "innerHTML" not in js and "textContent" in js
    assert 'explicit === "light" || explicit === "dark"' in html
    assert "var(--cp-" in client.get("/static/review.css", headers=headers(token)).get_data(as_text=True)


def test_empty_failed_and_pending_dashboard_states_are_not_fabricated(repo, client, token):
    html = client.get("/", headers=headers(token)).get_data(as_text=True)
    assert "No authorized analysis has been registered" in html
    value = result()
    value["status"], value["stage_results"] = "running", {}
    repo.register_result(value)
    html = client.get("/?incident_id=incident-a", headers=headers(token)).get_data(as_text=True)
    assert html.count('<span class="pill">pending</span>') == 9
    assert "No gate receipt supplied" in html
    assert post_review(client, token, repo.get_revision("incident-a", 1)["etag"]).status_code == 409


def test_export_requires_role_scope_and_retains_overlay(repo, client, token):
    repo.register_result(result())
    path = "/incidents/incident-a/revisions/1/export"
    assert client.get(path, headers=headers(token, roles=["Viewer"])).status_code == 403
    response = client.get(path, headers=headers(token, roles=["Analyst"]))
    assert response.status_code == 200
    assert response.json["execution_allowed"] is False and "SYNTHETIC" in response.json["banner"]
    assert "attachment" in response.headers["Content-Disposition"]


def test_pagination_filters_only_authorized_results(repo, client, token):
    for name in ("incident-a", "incident-b", "incident-c"):
        repo.register_result(result(name))
    first = client.get("/incidents?limit=2&domain=energy", headers=headers(token)).json
    assert len(first["incidents"]) == 2 and first["next_cursor"] == "incident-b"
    next_page = client.get("/incidents?cursor=incident-b&limit=2", headers=headers(token)).json
    assert len(next_page["incidents"]) == 1 and next_page["next_cursor"] is None
    assert client.get("/incidents?domain=other", headers=headers(token)).json["incidents"] == []
    assert client.get("/incidents?query=arbitrary", headers=headers(token)).status_code == 422


def test_sqlite_local_persistence_survives_reopen():
    path = Path("tests") / f".review-local-test-{uuid4()}.sqlite"
    repository = None
    try:
        repository = SQLiteReviewRepository(path)
        expected = repository.register_result(result())["result_hash"]
        repository.close()
        repository = SQLiteReviewRepository(path)
        assert repository.get_revision("incident-a", 1)["result_hash"] == expected
    finally:
        if repository:
            repository.close()
        path.unlink(missing_ok=True)


def test_sqlserver_path_rejects_autocommit_without_claiming_live_sql_proof():
    class Connection:
        autocommit = True
        rolled_back = False
        closed = False
        def cursor(self):
            return object()
        def rollback(self):
            self.rolled_back = True
        def close(self):
            self.closed = True
    connection = Connection()
    repository = SqlServerReviewRepository(lambda: connection)
    with pytest.raises(ReviewError, match="autocommit=False"):
        repository.register_result(result())
    assert connection.rolled_back and connection.closed


def test_consumer_adapter_preserves_work_contract(queue):
    from agentic_itops.review.work import WorkRequest
    adapter = ConsumerWorkAdapter(queue, WorkRequest)
    request = WorkRequest("analysis", "logical-one", work())
    assert adapter.enqueue(request) == "logical-one"
    assert adapter.get_job("logical-one")["payload"] == work()


def test_revoked_evidence_cannot_be_reintroduced_by_a_new_result(repo, client, token):
    repo.register_result(result())
    repo.invalidate_evidence("event-a", "Evidence unavailable")
    revised = repo.register_result(result(revision=2))
    assert revised["eligible"] is False
    assert post_review(client, token, revised["etag"]).status_code == 409
    assert repo.invalidate_evidence("not-yet-ingested", "Preemptive source erasure") == []


def test_publisher_preflight_never_treats_pending_intent_as_current_authority(repo, client, token):
    publication = approve(repo, client, token)
    assert repo.publication_for_publish(publication["publication_id"])["searchable"] is False
    repo.invalidate_evidence("event-a", "Evidence removed")
    with pytest.raises(ReviewError, match="ineligible"):
        repo.publication_for_publish(publication["publication_id"])


def test_expiry_denies_publication_and_queues_exclusion(repo, client, token):
    publication = approve(repo, client, token)
    # Advance the test ledger's deadline; no clock or cloud side effects.
    with repo.transaction() as cursor:
        cursor.execute("UPDATE review_publications SET eligible_until=? WHERE publication_id=?",
                       ("2000-01-01T00:00:00Z", publication["publication_id"]))
    assert repo.get_publication(publication["publication_id"])["eligible"] is False
    assert repo.expire_publications() == [publication["publication_id"]]
    assert any(row["kind"] == "exclude-publication" for row in repo.pending_outbox())
    assert repo.expire_publications() == []


def test_job_errors_are_redacted_and_unknown_ids_are_not_success(client, token, queue):
    receipt = client.post("/analysis-jobs", json=work(), headers=headers(token)).json
    queue.jobs[receipt["job_id"]].update(status="failed", error="SECRET connection string",
                                       access_token="NEVER-RETURN")
    response = client.get(f"/jobs/{receipt['job_id']}", headers=headers(token))
    assert response.json["status"] == "failed"
    assert "SECRET" not in response.get_data(as_text=True)
    assert "NEVER-RETURN" not in response.get_data(as_text=True)
    assert client.get("/jobs/unknown", headers=headers(token)).status_code == 404


def test_switching_out_of_testing_disables_test_authenticator(repo):
    class TestAuth:
        def authenticate(self, request):
            return Principal(OID, frozenset({"Viewer"}))
    app = create_app({"TESTING": True, "PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "test-only-" * 5,
                      "TEST_AUTHENTICATOR": TestAuth()}, repository=repo)
    app.testing = False
    assert app.test_client().get("/incidents").status_code == 401
    assert app.test_client().get("/health").status_code == 200


def test_real_local_consumer_queue_recovers_commit_before_review_receipt(repo, monkeypatch):
    """Integration is LOCAL SQLite, explicitly not SQL Server transaction proof."""
    from contextlib import closing
    import sqlite3
    from agentic_itops.consumer.storage import LocalSQLiteWorkflowStore
    from agentic_itops.consumer.storage_contract import IntegrityConflict, WorkRequest

    path = Path("tests") / f".review-consumer-test-{uuid4()}.sqlite"
    try:
        store = LocalSQLiteWorkflowStore(lambda: sqlite3.connect(path))
        store.bootstrap_schema()
        adapter = ConsumerWorkAdapter(store, WorkRequest, conflict_type=IntegrityConflict)
        original = repo._receipt
        def fail(*args, **kwargs):
            raise RuntimeError("local crash after consumer commit")
        monkeypatch.setattr(repo, "_receipt", fail)
        with pytest.raises(RuntimeError):
            repo.submit_work(adapter, OID, "/analysis-jobs", "crash-retry", work())
        monkeypatch.setattr(repo, "_receipt", original)
        different = work()
        different["window"]["end_utc"] = "2026-01-03T00:00:00Z"
        with pytest.raises(ReviewError, match="idempotency"):
            repo.submit_work(adapter, OID, "/analysis-jobs", "crash-retry", different)
        receipt = repo.submit_work(adapter, OID, "/analysis-jobs", "crash-retry", work())
        assert receipt["status"] == "queued"
        assert adapter.get_job(receipt["job_id"])["status"] == "queued"
        assert adapter.get_job("unknown") is None
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM consumer_jobs").fetchone()[0] == 1
    finally:
        path.unlink(missing_ok=True)


def test_concurrent_review_cas_has_one_winner(repo):
    from concurrent.futures import ThreadPoolExecutor

    revision = repo.register_result(result())
    def write(key):
        try:
            repo.review(OID, "incident-a", revision["etag"], key,
                        {"action": "accept", "reason": "Concurrent review"})
            return 200
        except ReviewError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ["concurrent-a", "concurrent-b"])) == [200, 409]
    assert len(repo.get_revision("incident-a", 1)["review_history"]) == 1


def test_local_database_enforces_immutable_revision_payload(repo):
    import sqlite3

    repo.register_result(result())
    with pytest.raises(sqlite3.IntegrityError, match="immutable review revision"):
        with repo.transaction() as cursor:
            cursor.execute("UPDATE review_revisions SET payload_json=? WHERE incident_id=?",
                           ('{"execution_allowed":true}', "incident-a"))
    assert repo.get_revision("incident-a", 1)["result"]["execution_allowed"] is False


def test_sql_migration_orders_objects_before_immutable_triggers():
    """Static migration guard only; this is not a live SQL Server validation."""
    path = Path("agentic_itops") / "review" / "migrations" / "001_review.sql"
    sql = path.read_text(encoding="utf-8")
    for table in ("revisions", "receipts", "publications"):
        assert sql.index(f"CREATE TABLE review.{table}") < sql.index(f"CREATE TRIGGER review.{table}_immutable")
    assert "CREATE TABLE review.evidence_tombstones" in sql
    assert "consumer_" not in sql


@pytest.mark.parametrize("field,changed", [("run_id", "Run-A"), ("producer_key", "Producer-A")])
def test_source_entitlements_are_case_sensitive_independent_of_sql_collation(
        repo, client, token, field, changed):
    value = result()
    value["source"][field] = changed
    value["source_hash"] = digest(value["source"])
    revision = repo.register_result(value)
    assert client.get("/incidents", headers=headers(token)).json["incidents"] == []
    for path in ("/?incident_id=incident-a", "/incidents/incident-a/revisions/1",
                 "/incidents/incident-a/revisions/1/export"):
        assert client.get(path, headers=headers(token)).status_code == 403
    assert post_review(client, token, revision["etag"]).status_code == 403
    request = work()
    request["source"][field] = changed
    assert client.post("/analysis-jobs", json=request, headers=headers(token)).status_code == 403
    assert client.post("/hygiene-jobs", json=request, headers=headers(token)).status_code == 403


def test_case_only_run_correction_cannot_rebind_an_incident(repo):
    repo.register_result(result())
    changed = result(revision=2)
    changed["source"]["run_id"] = "Run-A"
    changed["source_hash"] = digest(changed["source"])
    with pytest.raises(ReviewError, match="source is immutable"):
        repo.register_result(changed)
