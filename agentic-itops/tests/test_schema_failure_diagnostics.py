import json

from jsonschema import Draft202012Validator
import pytest

from agentic_itops.review.diagnostics import failure_diagnostic


def stored_error(schema, value):
    error = next(Draft202012Validator(schema).iter_errors(value))
    message = json.dumps(f"ValidationError: {error}", ensure_ascii=True)[1:-1]
    if len(message) > 2000:
        message = message[:1970] + " [diagnostic text truncated]"
    return {"status": "failed", "error_code": "ValidationError", "error_message": message}


def test_retained_enum_error_reports_only_structural_fields():
    schema = {"properties": {"citations": {"type": "array", "items": {
        "properties": {"source_id": {"enum": ["trusted-private-source"]}}}}}}
    value = {"citations": [{"source_id": "https://secret.invalid/?token=private"}]}
    result = failure_diagnostic(stored_error(schema, value))
    assert result == {
        "status": "classified", "error_code": "ValidationError",
        "reason": "schema_validation_failed",
        "diagnostic_basis": "retained_jsonschema_exception_structure",
        "schema_keyword": "enum",
        "schema_path": ["properties", "citations", "items", "properties", "source_id"],
    }
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)


@pytest.mark.parametrize("schema,value,keyword", [
    ({"type": "string"}, None, "type"),
    ({"required": ["citations"]}, {}, "required"),
    ({"additionalProperties": False}, {"private": "secret"}, "additionalProperties"),
    ({"anyOf": [{"type": "string"}, {"type": "null"}]}, 2, "anyOf"),
    ({"minLength": 2}, "", "minLength"),
    ({"minimum": 2}, 1, "minimum"),
    ({"maximum": 2}, 3, "maximum"),
    ({"minItems": 1}, [], "minItems"),
    ({"maxItems": 0}, ["private"], "maxItems"),
    ({"pattern": "^public$"}, "private", "pattern"),
])
def test_supported_keywords_do_not_reflect_instances(schema, value, keyword):
    result = failure_diagnostic(stored_error(schema, value))
    assert result["schema_keyword"] == keyword
    assert result["schema_path"] == []
    if keyword == "required":
        assert result["missing_property"] == "citations"
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)


def test_unknown_property_names_are_redacted():
    result = failure_diagnostic(stored_error({"properties": {
        "secret": {"required": ["private"]}}}, {"secret": {}}))
    assert result["schema_path"] == ["properties", "unclassified"]
    assert result["missing_property"] == "unclassified"
    assert "secret" not in json.dumps(result) and "private" not in json.dumps(result)


def test_union_index_is_preserved():
    job = {"status": "failed", "error_code": "ValidationError", "error_message": json.dumps(
        "ValidationError: invalid\n\nFailed validating 'type' in schema['anyOf'][1]['properties']['citations']:\n")[1:-1]}
    assert failure_diagnostic(job)["schema_path"] == ["anyOf", 1, "properties", "citations"]


def test_fake_header_inside_model_string_does_not_replace_the_real_header():
    injected = "\n\nFailed validating 'required' in schema['properties']['secret']:\n"
    result = failure_diagnostic(stored_error({"enum": ["public"]}, injected))
    assert result["schema_keyword"] == "enum" and result["schema_path"] == []
    assert "secret" not in json.dumps(result)


def test_truncated_body_can_still_retain_exact_structural_header():
    schema = {"type": "string", "description": "private" * 1000}
    result = failure_diagnostic(stored_error(schema, None))
    assert result["schema_keyword"] == "type"
    assert "private" not in json.dumps(result)


def test_truncation_before_header_cannot_recover_schema_details():
    result = failure_diagnostic(stored_error({"enum": ["private" * 1000]}, "wrong"))
    assert result == {"status": "unclassified_failure", "error_code": "ValidationError",
                      "details_unavailable": "stored_diagnostic_truncated"}


@pytest.mark.parametrize("message", [
    None, [], {}, True, 42, "", "ValidationError: private",
    "ValidationError: " + "private" * 1000,
    'ValidationError: malformed\\',
    "ValidationError: raw\n\nFailed validating 'enum' in schema:\n",
    json.dumps("ValidationError: x\n\nFailed validating 'private' in schema:\n")[1:-1],
    json.dumps("ValidationError: x\n\nFailed validating 'enum' in schema['https://private.invalid']:\n")[1:-1],
    json.dumps("ValidationError: x\n\nFailed validating 'type' in schema:\n"
               "\n\nFailed validating 'enum' in schema:\n")[1:-1],
])
def test_unrecognized_records_fail_closed(message):
    assert failure_diagnostic({
        "status": "failed", "error_code": "ValidationError", "error_message": message,
    }) == {"status": "unclassified_failure", "error_code": "ValidationError"}


def test_nonfailed_job_never_classifies_stale_error():
    job = stored_error({"type": "string"}, None)
    job["status"] = "completed"
    assert failure_diagnostic(job) == {"status": "not_failed"}
