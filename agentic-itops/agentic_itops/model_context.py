"""Lossless removal of repeated observation fields from model-only context."""
from .consumer.contracts import ContractError, canonical


def _common(rows):
    shared = {}
    for key in set(rows[0]).intersection(*(set(row) for row in rows[1:])):
        values = [row[key] for row in rows]
        if all(canonical(value) == canonical(values[0]) for value in values):
            shared[key] = values[0]
        elif all(isinstance(value, dict) for value in values):
            nested = _common(values)
            if nested:
                shared[key] = nested
    return shared


def _difference(row, shared):
    result = {}
    for key, value in row.items():
        if key not in shared:
            result[key] = value
        elif isinstance(value, dict) and isinstance(shared[key], dict):
            nested = _difference(value, shared[key])
            if nested:
                result[key] = nested
        elif canonical(value) != canonical(shared[key]):
            raise ContractError("shared observation field differs from source")
    return result


def restore_observation(shared, row):
    result = dict(shared)
    for key, value in row.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = restore_observation(result[key], value)
        else:
            result[key] = value
    return result


def model_candidate(candidate):
    rows = candidate["observations"]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ContractError("model context requires complete observation objects")
    if len(rows) < 2:
        return candidate
    if {"observation_common_fields", "observation_encoding"} & set(candidate):
        raise ContractError("source cannot override model observation encoding")
    shared = _common(rows)
    differences = [_difference(row, shared) for row in rows]
    for original, difference in zip(rows, differences):
        if canonical(restore_observation(shared, difference)) != canonical(original):
            raise ContractError("model observation representation lost source fields")
    result = {
        **candidate,
        "observations": differences,
        "observation_common_fields": shared,
        "observation_encoding": {
            "format": "shared-fields-v1",
            "record_count": len(rows),
            "rule": (
                "Recursively merge observation_common_fields with each observations entry "
                "to reconstruct its complete original record. Every record and field is retained. "
                "Evidence source hashes apply to reconstructed records, not these differences."
            ),
        },
    }
    return result if len(canonical(result)) + 1024 < len(canonical(candidate)) else candidate
