from dataclasses import asdict, replace

import pytest

from agentic_itops.consumer.contracts import ContractError, Observation, digest
from tests.test_consumer_detectors import series


def selected_policy():
    records, policy, _, _ = series("downstream", "recovery")
    row = records[0].row
    return records, replace(
        policy, entity_id=row["EntityId"], location_id=row["LocationId"],
        tag_id=row["Payload"].get("tag_id"), phase=row["Payload"].get("phase"))


def test_unselected_policy_hash_and_matching_remain_unchanged():
    records, policy, _, _ = series("downstream")
    original = asdict(policy)
    for name in ("entity_id", "location_id", "tag_id", "phase"):
        del original[name]
    original["allowed_quality"] = list(original["allowed_quality"])
    assert policy.sha256 == digest(original)
    assert all(policy.matches(row) for row in records)


@pytest.mark.parametrize("field", ["EntityId", "LocationId", "tag_id", "phase"])
def test_exact_selector_excludes_other_signals(field):
    records, policy = selected_policy()
    assert all(policy.matches(row) for row in records)
    row = records[0].row
    target = row if field in {"EntityId", "LocationId"} else row["Payload"]
    target[field] = "another-signal"
    assert not policy.matches(Observation.parse(records[0].producer, row))


def test_selector_is_bound_to_policy_hash():
    _, policy = selected_policy()
    for field in ("entity_id", "location_id", "tag_id", "phase"):
        assert replace(policy, **{field: "another-signal"}).sha256 != policy.sha256


def test_runtime_requires_approval_for_exact_selector(tmp_path):
    from agentic_itops.service_runtime import RuntimeConfiguration
    from tests.test_application_integration import configuration, integrated

    fixture = integrated(tmp_path)
    records, policy, _, _ = series("upstream")
    row = records[0].row
    selected = replace(policy, entity_id=row["EntityId"], location_id=row["LocationId"],
                       tag_id=row["Payload"].get("tag_id"), phase=row["Payload"].get("phase"))
    value = configuration(fixture)
    value["detector_policies"] = [asdict(selected)]
    value["approved_detector_policy_hashes"] = [policy.sha256]
    with pytest.raises(ContractError, match="approval"):
        RuntimeConfiguration.parse(value)
    value["approved_detector_policy_hashes"] = [selected.sha256]
    assert RuntimeConfiguration.parse(value).policies == (selected,)


@pytest.mark.parametrize("changes", [
    {"entity_id": "asset"}, {"location_id": "location"}, {"tag_id": "tag"},
    {"phase": "a"}, {"entity_id": "", "location_id": "location"},
    {"entity_id": "asset", "location_id": "location", "tag_id": 1},
    {"entity_id": "asset", "location_id": "location", "phase": ""},
])
def test_partial_or_invalid_selectors_are_rejected(changes):
    _, policy, _, _ = series("downstream")
    with pytest.raises(ContractError, match="selector"):
        replace(policy, **changes)
