from copy import deepcopy

import pytest

from agentic_itops.consumer.contracts import ContractError, canonical, digest
from agentic_itops.model_context import model_candidate, restore_observation
from agentic_itops.runtime import WorkflowContext


def candidate():
    rows = [{
        "EventId": f"event-{index}",
        "ObservedAt": f"2026-09-14T23:{index // 60:02}:{index % 60:02}.123456Z",
        "IsSynthetic": True,
        "Payload": {"reading": index / 10, "measurement": "temperature"},
        "Context": {"source": "synthetic fixture " * 80, "location": {"name": "fictional"}},
        "Optional": {} if index % 2 else {"present": None},
        "Typed": True if index % 2 else 1,
    } for index in range(117)]
    return {
        "observations": rows,
        "evidence_refs": [{"event_id": row["EventId"], "source_sha256": digest(row)} for row in rows],
        "source": {"domain": "power-grid"},
    }


def test_all_records_fields_types_hashes_and_order_survive_factoring():
    original = candidate()
    before = deepcopy(original)
    factored = model_candidate(original)
    assert factored["observation_encoding"]["record_count"] == 117
    assert len(factored["observations"]) == 117
    assert len(canonical(factored)) < len(canonical(original)) * 0.4
    restored = [restore_observation(factored["observation_common_fields"], row)
                for row in factored["observations"]]
    assert canonical(restored) == canonical(original["observations"])
    assert [digest(row) for row in restored] == [
        item["source_sha256"] for item in original["evidence_refs"]]
    assert canonical(original) == canonical(before)
    assert factored["evidence_refs"] == original["evidence_refs"]
    assert "Typed" not in factored["observation_common_fields"]


def test_only_model_input_changes_not_authoritative_candidate_or_tools():
    original = candidate()
    context = WorkflowContext("workflow", original)
    encoded = context.model_input()["incident"]
    assert encoded["observation_encoding"]["format"] == "shared-fields-v1"
    assert context.candidate is original
    assert "observation_encoding" not in original
    assert context.citation_ids == {row["EventId"] for row in original["observations"]}
    assert canonical(context.model_input()) == canonical(context.model_input())


@pytest.mark.parametrize("rows", [[], [{"EventId": "one"}], [{"a": 1}, {"a": 2}]])
def test_small_context_is_not_expanded(rows):
    original = {"observations": rows}
    assert model_candidate(original) is original


@pytest.mark.parametrize("field", ["observation_common_fields", "observation_encoding"])
def test_source_cannot_supply_encoding_directives(field):
    original = candidate()
    original[field] = {}
    with pytest.raises(ContractError, match="override"):
        model_candidate(original)


def test_non_object_observations_fail_explicitly():
    with pytest.raises(ContractError, match="complete observation"):
        model_candidate({"observations": ["not an observation"]})
