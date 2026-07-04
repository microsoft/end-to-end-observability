#!/usr/bin/env python3
"""
validate_workbooks.py — structural validation for the workbook suite.

Checks every ``workbooks/*.workbook.json`` for:
  * valid JSON and ``version == 'Notebook/1.0'`` with a ``$schema``;
  * a shared parameter block (type 9) exposing all required parameters;
  * the mandatory **Top 10 SRE Agent Recommendations** tile bound to
    ``SREAgentRecommendations_CL`` on every page;
  * no leftover build-time placeholders (``{{ ... }}``);
  * every KQL/ARG tile declares a ``resourceType`` and ``crossComponentResources``.

Exit code 0 = all good, 1 = one or more failures. No third-party dependencies.
Run:  python tests/validate_workbooks.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORKBOOKS_DIR = os.path.abspath(os.path.join(HERE, "..", "workbooks"))

REQUIRED_PARAMS = {
    "TimeRange", "Subscriptions", "LogAnalyticsWorkspaceId", "ManagementGroup",
    "ResourceGroup", "Region", "Environment", "Workload", "OwnerTeam",
    "BusinessCriticality", "ResourceType", "Severity", "RecommendationStatus",
}

PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")


def _iter_items(items):
    for item in items:
        yield item
        content = item.get("content", {}) if isinstance(item, dict) else {}
        if isinstance(content, dict) and isinstance(content.get("items"), list):
            yield from _iter_items(content["items"])


def validate_file(path: str) -> list[str]:
    errors: list[str] = []
    name = os.path.basename(path)
    raw = open(path, encoding="utf-8").read()

    leftover = PLACEHOLDER_RE.findall(raw)
    if leftover:
        errors.append(f"{name}: unresolved build placeholder(s): {sorted(set(leftover))[:5]}")

    try:
        wb = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"{name}: invalid JSON: {exc}"]

    if wb.get("version") != "Notebook/1.0":
        errors.append(f"{name}: version must be 'Notebook/1.0'")
    if "$schema" not in wb:
        errors.append(f"{name}: missing $schema")

    items = list(_iter_items(wb.get("items", [])))

    # Parameter block.
    param_names: set[str] = set()
    for it in items:
        if it.get("type") == 9:
            for p in it.get("content", {}).get("parameters", []):
                param_names.add(p.get("name"))
    missing = REQUIRED_PARAMS - param_names
    if missing:
        errors.append(f"{name}: missing required parameters: {sorted(missing)}")

    # Mandatory Top 10 recommendations tile.
    top10 = [it for it in items if it.get("name") == "top10-recommendations"]
    if not top10:
        errors.append(f"{name}: missing 'top10-recommendations' tile")
    else:
        q = top10[0].get("content", {}).get("query", "")
        if "SREAgentRecommendations_CL" not in q:
            errors.append(f"{name}: Top 10 tile does not query SREAgentRecommendations_CL")

    # Query tiles must be scoped.
    for it in items:
        if it.get("type") == 3:
            c = it.get("content", {})
            if not c.get("resourceType"):
                errors.append(f"{name}: query tile '{it.get('name')}' missing resourceType")
            if not c.get("crossComponentResources"):
                errors.append(f"{name}: query tile '{it.get('name')}' missing crossComponentResources")

    return errors


def main() -> int:
    files = sorted(glob.glob(os.path.join(WORKBOOKS_DIR, "*.workbook.json")))
    if not files:
        print(f"No workbooks found in {WORKBOOKS_DIR}. Run builder/build_workbooks.py first.", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    for f in files:
        errs = validate_file(f)
        status = "OK" if not errs else "FAIL"
        print(f"[{status}] {os.path.basename(f)}")
        all_errors.extend(errs)

    print("-" * 60)
    if all_errors:
        for e in all_errors:
            print(f"  - {e}")
        print(f"{len(all_errors)} problem(s) across {len(files)} workbook(s).")
        return 1
    print(f"All {len(files)} workbook(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
