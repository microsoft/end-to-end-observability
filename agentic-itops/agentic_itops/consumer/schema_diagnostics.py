"""Bounded schema failure metadata, independent of rejected values and schemas."""
from functools import lru_cache
import json

from jsonschema import Draft202012Validator, ValidationError

from ..portfolio_schemas import output_schema


PREFIX = "schema_validation_failure_v1 "
KEYWORDS = frozenset({
    "type", "enum", "required", "additionalProperties", "anyOf", "minLength",
    "minimum", "maximum", "minItems", "maxItems", "pattern",
})
BOUNDARIES = frozenset({"agent_output", "trusted_citations", "function_arguments", "function_output"})
JSON_TYPES = frozenset({"null", "boolean", "object", "array", "string", "integer", "number"})
MAX_STORED_LENGTH = 1800


def _agents():
    from ..runtime import AGENTS
    return AGENTS


@lru_cache(maxsize=1)
def schema_fields():
    fields = {"properties", "items", "anyOf"}

    def visit(value):
        if isinstance(value, dict):
            fields.update(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for agent in _agents():
        visit(output_schema(agent))
    return frozenset(fields)


def _token(value, allowed):
    return value if type(value) is str and value in allowed else "unclassified"


def _path(parts):
    values = list(parts)
    result = [
        part if type(part) is int and 0 <= part <= 99999
        else _token(part, schema_fields())
        for part in values[:12]
    ]
    if len(values) > 12:
        result.append("unclassified")
    return result


def _json_type(value):
    return {
        type(None): "null", bool: "boolean", dict: "object", list: "array",
        str: "string", int: "integer", float: "number",
    }.get(type(value), "unclassified")


def _structure(error):
    parts = list(error.absolute_schema_path)
    if parts and parts[-1] == error.validator:
        parts.pop()
    result = {"schema_keyword": _token(error.validator, KEYWORDS), "schema_path": _path(parts)}
    if error.validator == "required" and isinstance(error.instance, dict):
        missing = next((name for name in error.validator_value if name not in error.instance), None)
        result["missing_property"] = _token(missing, schema_fields())
    return result


def _stored_length(message):
    return len(json.dumps("ValidationError: " + message, ensure_ascii=True)[1:-1])


def validate_at_boundary(schema, value, *, agent, boundary):
    try:
        Draft202012Validator(schema).validate(value)
        return
    except ValidationError as error:
        record = {
            "agent": _token(agent, _agents()),
            "boundary": _token(boundary, BOUNDARIES),
            **_structure(error),
            "instance_path": _path(error.absolute_path),
            "actual_type": _json_type(error.instance),
            "causes": [_structure(child) for child in error.context[:8]],
            "causes_omitted": len(error.context) > 8,
        }
        message = PREFIX + json.dumps(record, separators=(",", ":"), ensure_ascii=True)
        while _stored_length(message) > MAX_STORED_LENGTH and record["causes"]:
            record["causes"].pop()
            record["causes_omitted"] = True
            message = PREFIX + json.dumps(record, separators=(",", ":"), ensure_ascii=True)
        while _stored_length(message) > MAX_STORED_LENGTH:
            key = max(("schema_path", "instance_path"), key=lambda name: len(record[name]))
            record[key].pop()
            record["paths_truncated"] = True
            message = PREFIX + json.dumps(record, separators=(",", ":"), ensure_ascii=True)
    # Raise outside the handler so the original value-bearing exception is not chained.
    raise ValidationError(message) from None


def from_stored_message(message):
    if type(message) is not str or len(message) > MAX_STORED_LENGTH:
        return None
    try:
        decoded = json.loads('"' + message + '"')
        prefix = "ValidationError: " + PREFIX
        if not decoded.startswith(prefix):
            return None
        record = json.loads(decoded[len(prefix):])
    except (json.JSONDecodeError, UnicodeError):
        return None
    required = {"agent", "boundary", "schema_keyword", "schema_path", "instance_path",
                "actual_type", "causes", "causes_omitted"}
    optional = {"missing_property", "paths_truncated"}
    if (type(record) is not dict or not required.issubset(record)
            or set(record) - required - optional):
        return None
    tokens = (("agent", _agents()), ("boundary", BOUNDARIES),
              ("schema_keyword", KEYWORDS), ("actual_type", JSON_TYPES))
    if any(type(record[key]) is not str or record[key] not in set(allowed) | {"unclassified"}
           for key, allowed in tokens):
        return None

    def valid_path(value):
        return (type(value) is list and len(value) <= 13
                and all((type(part) is int and 0 <= part <= 99999)
                        or (type(part) is str and part in schema_fields() | {"unclassified"})
                        for part in value))

    def valid_missing(value):
        return ("missing_property" not in value
                or (value["schema_keyword"] == "required"
                    and type(value["missing_property"]) is str
                    and value["missing_property"] in schema_fields() | {"unclassified"}))

    if (not valid_path(record["schema_path"]) or not valid_path(record["instance_path"])
            or type(record["causes_omitted"]) is not bool
            or type(record["causes"]) is not list or len(record["causes"]) > 8
            or not valid_missing(record)
            or ("paths_truncated" in record and record["paths_truncated"] is not True)):
        return None
    for cause in record["causes"]:
        if (type(cause) is not dict
                or not {"schema_keyword", "schema_path"}.issubset(cause)
                or set(cause) - {"schema_keyword", "schema_path", "missing_property"}
                or type(cause["schema_keyword"]) is not str
                or cause["schema_keyword"] not in KEYWORDS | {"unclassified"}
                or not valid_path(cause["schema_path"]) or not valid_missing(cause)):
            return None
    return record
