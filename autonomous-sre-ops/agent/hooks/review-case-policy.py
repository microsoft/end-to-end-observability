#!/usr/bin/env python3
import json
import re
import sys
from uuid import UUID

CASE_ID = "0000000000000000000000000000010a"
FOOTER = "Review only. No repair or recovery is claimed."
SKILLS = {
    "asre-linux-rca", "asre-proactive-analysis",
    "asre-patch-backup-recovery", "asre-case-and-verification",
}
TARGETS = {
    f"/subscriptions/00000001-000a-4000-a000-000000000001/"
    f"resourceGroups/rg-asre-{role}-cus/providers/Microsoft.Compute/"
    f"virtualMachines/vm-asre-{role}-cus"
    for role in ("linux", "patch", "backup")
}
TOOLS = {
    "ReadFile", "read_skill_file", "SearchMemory", "asre-review-evidence",
    "GetServiceNowIncident", "PostServiceNowDiscussionEntry",
}


def unique_object(pairs):
    result = {}
    seen = set()
    for key, value in pairs:
        folded = key.casefold()
        if folded in seen:
            raise ValueError("Duplicate hook or argument key.")
        seen.add(folded)
        result[key] = value
    return result


def arguments(value):
    if isinstance(value, str):
        value = json.loads(value, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be an object.")
    keys = [key.casefold() for key in value]
    if len(set(keys)) != len(keys):
        raise ValueError("Ambiguous argument keys.")
    return {key.casefold(): item for key, item in value.items()}


def trusted_skill_path(value):
    if not isinstance(value, str) or any(char in value for char in ("\\", "\0", "%")):
        return False
    parts = value.split("/")
    return (
        ".." not in parts and len(parts) >= 3
        and parts[-3] == "skills" and parts[-2] in SKILLS
        and parts[-1] == "SKILL.md"
    )


def canonical_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def permitted(context):
    if not isinstance(context, dict) or not isinstance(context.get("tool_name"), str):
        return False
    name = next((
        tool for tool in TOOLS
        if re.search(r"(?:^|[._:/-])" + re.escape(tool) + r"$", context["tool_name"])
    ), None)
    if not name:
        return False
    args = arguments(context.get("tool_input"))
    if name == "ReadFile":
        paths = [value for key, value in args.items() if key in {"path", "filepath", "file_path"}]
        return len(paths) == 1 and trusted_skill_path(paths[0])
    if name == "read_skill_file":
        skill = args.get("skill_name", args.get("skillname", args.get("name")))
        filename = args.get("file_name", args.get("filename", "SKILL.md"))
        return skill in SKILLS and filename == "SKILL.md"
    if name == "SearchMemory":
        query = args.get("query")
        return isinstance(query, str) and 0 < len(query.strip()) <= 2000
    if name == "asre-review-evidence":
        action = args.get("action")
        if args.get("resource_id") not in TARGETS:
            return False
        if action in {"inspect", "telemetry", "patch_jobs", "backup_jobs"}:
            return True
        page = args.get("page", 0)
        if isinstance(page, str) and re.fullmatch(r"0|[1-9][0-9]*", page):
            page = int(page)
        return (
            action == "result" and canonical_uuid(args.get("request_id"))
            and type(page) is int and 0 <= page <= 1000
        )
    if args.get("incidentsystemid") != CASE_ID:
        return False
    if name == "GetServiceNowIncident":
        return True
    customer_facing = args.get("customerfacing", False)
    if customer_facing is not False and customer_facing != "false":
        return False
    note = args.get("discussionentry")
    if not isinstance(note, str) or len(note) > 6000 or "\0" in note:
        return False
    header = re.match(r"\AASRE REVIEW \| INC0900001 \| ([0-9a-f-]{36})\n", note)
    return bool(header and canonical_uuid(header[1]) and note.rstrip().endswith(FOOTER))


def main():
    try:
        text = sys.stdin.read(65537)
        allowed = len(text) <= 65536 and permitted(json.loads(text, object_pairs_hook=unique_object))
    except (ValueError, TypeError, UnicodeError):
        allowed = False
    if allowed:
        print(json.dumps({"ok": True}))
    else:
        print(json.dumps({
            "ok": False,
            "reason": "ASRE Review policy denied an unknown tool, untrusted file, out-of-scope target or noninternal/noncorrelated case write.",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
            },
        }))


if __name__ == "__main__":
    main()
