from copy import deepcopy

import pytest

pytest.importorskip("flask", reason="install requirements-consumer.txt")
pytest.importorskip("jwt", reason="install requirements-consumer.txt")

from agentic_itops.review import SQLiteReviewRepository
from agentic_itops.review.contracts import ReviewError, digest, result_header, source
from tests.test_review import SOURCE, approval_body, result


def test_uuid_case_cannot_bypass_independent_publication_approval():
    repository = SQLiteReviewRepository()
    actor = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    value = result()
    value["postmortem_draft"]["authorship"] = {"kind": "human", "oid": actor.upper()}
    saved = repository.register_result(value)
    assert saved["result"]["postmortem_draft"]["authorship"]["oid"] == actor
    accepted = repository.review(actor, value["incident_id"], saved["etag"], "accept-one",
                                 {"action": "accept", "reason": "Reviewed"})
    for spelling in (actor, actor.upper(), "{" + actor.upper() + "}"):
        with pytest.raises(ReviewError, match="authors cannot") as failure:
            repository.approve_publication(spelling, accepted["etag"], "publish", approval_body())
        assert failure.value.status == 403
    repository.close()
    assert value["postmortem_draft"]["authorship"]["oid"] == actor.upper()


@pytest.mark.parametrize("run_id", ["run-a ", "run with spaces", "r" * 256, "MixedCase", "   "])
def test_review_preserves_valid_opaque_consumer_run_ids(run_id):
    value = deepcopy(SOURCE)
    value["run_id"] = run_id
    assert source(value)["run_id"] == run_id
    header = result()
    header["source"] = value
    header["source_hash"] = digest(value)
    assert result_header(header)["source"]["run_id"] == run_id


@pytest.mark.parametrize("run_id", ["", "r" * 257, "run\nx", "run\x00x", None])
def test_review_rejects_run_ids_outside_the_consumer_contract(run_id):
    value = deepcopy(SOURCE)
    value["run_id"] = run_id
    with pytest.raises(ReviewError, match="run_id"):
        source(value)


def test_human_author_must_be_a_real_uuid_shape_not_an_opaque_display_name():
    value = result()
    value["postmortem_draft"]["authorship"] = {"kind": "human", "oid": "someone"}
    with pytest.raises(ReviewError, match="UUID"):
        result_header(value)
