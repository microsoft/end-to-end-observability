"""Report writers: JSON (machine-readable) and Markdown (human-readable)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .rules_engine import Finding


def write_json(findings: Iterable[Finding], path: str | Path) -> Path:
    path = Path(path)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "schemaVersion": "0.1",
        "findings": [asdict(f) for f in findings],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_markdown(findings: list[Finding], path: str | Path) -> Path:
    path = Path(path)
    by_severity: Counter[str] = Counter(f.severity for f in findings)
    by_type: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_type[f.resource_type].append(f)

    lines: list[str] = []
    lines.append("# Azure observability discovery report")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total findings: **{len(findings)}**")
    for sev in ("high", "medium", "low"):
        lines.append(f"- {sev.capitalize()}: {by_severity.get(sev, 0)}")
    lines.append("")

    if not findings:
        lines.append("No gaps detected. :tada:")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    lines.append("## Findings by resource type")
    lines.append("")
    for rtype, items in sorted(by_type.items()):
        lines.append(f"### `{rtype}` ({len(items)})")
        lines.append("")
        lines.append("| Resource | Subscription | Rule | Severity | Recommendations |")
        lines.append("|---|---|---|---|---|")
        for f in items:
            recs = ", ".join(f.recommendations) or "-"
            lines.append(
                f"| `{f.resource_name}` | `{f.subscription_id}` | "
                f"{f.rule_id} | {f.severity} | {recs} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
