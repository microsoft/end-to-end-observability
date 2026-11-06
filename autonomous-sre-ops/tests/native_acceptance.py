"""Shared assertions for isolated native ServiceNow acceptance, not authorization."""

import re
from uuid import UUID


SNAPSHOT_FIELDS = frozenset({
    "sys_id", "number", "state", "assignment_group", "cmdb_ci",
    "description", "comments", "active", "sys_mod_count",
})


def require_native_journal(proof, case_id, correlation):
    if not isinstance(case_id, str) or not re.fullmatch(r"[0-9a-f]{32}", case_id):
        raise ValueError("An exact canonical synthetic case ID is required.")
    try:
        canonical_correlation = str(UUID(correlation))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A canonical native write correlation is required.") from error
    if canonical_correlation != correlation:
        raise ValueError("The native write correlation must be canonical.")
    expected = {
        "accepted": True, "caseSysId": case_id, "correlation": correlation,
        "table": "incident", "element": "work_notes",
        "author": "copilot.integration", "valueMatches": True,
    }
    if (not isinstance(proof, dict)
            or any(type(proof.get(key)) is not type(value) or proof.get(key) != value
                   for key, value in expected.items())
            or type(proof.get("matchingJournalCount")) is not int
            or proof["matchingJournalCount"] != 1
            or not isinstance(proof.get("sysId"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", proof["sysId"])):
        raise ValueError("HTTP acknowledgement is not journal persistence; exact independent native journal proof is required.")
    return proof["sysId"]


def _unchanged_snapshot(baseline, actual):
    return (isinstance(baseline, dict) and set(baseline) == SNAPSHOT_FIELDS
            and all(isinstance(value, str) for value in baseline.values())
            and re.fullmatch(r"[0-9a-f]{32}", baseline["sys_id"]) is not None
            and re.fullmatch(r"[0-9]+", baseline["sys_mod_count"]) is not None
            and actual == baseline)


def prohibited_update_rejected(status, response, read_status, after, baseline):
    """KB3154852 permits 200/no-op; require a complete unchanged snapshot."""
    if (read_status != 200 or not isinstance(after, dict)
            or not _unchanged_snapshot(baseline, after.get("result"))):
        return False
    if status in {400, 403, 409}:
        return True
    return status == 200 and isinstance(response, dict) and response.get("result") == baseline


def independent_readback_unchanged(
    *, read_status, after, native_baseline, canonical_before,
    canonical_after, journals_before, journals_after, proof, correlation,
    context_before, context_after,
):
    """Reconcile complete actual state; this alone does not prove HTTP denial."""
    if not _unchanged_snapshot(canonical_before, canonical_after):
        return False
    for context, snapshot in ((context_before, canonical_before), (context_after, canonical_after)):
        if (not isinstance(context, dict)
                or context.get("case_sys_id") != snapshot["sys_id"]
                or any(context.get(key) != snapshot[field] for key, field in (
                    ("ci", "cmdb_ci"), ("assignment_group", "assignment_group"),
                    ("state", "state"), ("description", "description"), ("revision", "sys_mod_count"),
                ))):
            return False
    try:
        journal_id = require_native_journal(proof, canonical_before["sys_id"], correlation)
    except ValueError:
        return False
    if (not isinstance(native_baseline, dict)
            or set(native_baseline) not in (SNAPSHOT_FIELDS, SNAPSHOT_FIELDS - {"cmdb_ci"})
            or any(value != canonical_before[name] for name, value in native_baseline.items())
            or read_status != 200 or not isinstance(after, dict)
            or after.get("result") != native_baseline):
        return False
    fields = {
        "sys_id", "name", "element", "element_id", "sys_created_by", "sys_created_on", "value",
    }
    canonical_journals = []
    for rows in (journals_before, journals_after):
        if not isinstance(rows, list) or not 1 <= len(rows) < 20:
            return False
        if any(
            not isinstance(row, dict) or set(row) != fields
            or not all(isinstance(value, str) for value in row.values())
            or not re.fullmatch(r"[0-9a-f]{32}", row["sys_id"])
            or row["name"] != "incident" or row["element"] not in {"work_notes", "comments"}
            or row["element_id"] != canonical_before["sys_id"]
            for row in rows
        ):
            return False
        if len({row["sys_id"] for row in rows}) != len(rows):
            return False
        positive = [row for row in rows if row["sys_id"] == journal_id]
        if (len(positive) != 1 or positive[0]["element"] != "work_notes"
                or positive[0]["sys_created_by"] != "copilot.integration"):
            return False
        canonical_journals.append(sorted(rows, key=lambda row: row["sys_id"]))
    if canonical_journals[0] != canonical_journals[1]:
        return False
    return True


def independently_prohibited_update_rejected(*, status, response, **readback):
    """A masked CI needs complete canonical record AND journal readback, not a default."""
    if not independent_readback_unchanged(**readback):
        return False
    if status in {400, 403, 409}:
        return True
    return (status == 200 and isinstance(response, dict)
            and response.get("result") == readback["native_baseline"])
