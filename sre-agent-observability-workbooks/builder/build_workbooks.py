#!/usr/bin/env python3
"""
build_workbooks.py - assemble the SRE Agent Observability Workbook suite.

Each workbook is built from shared libraries so the structure and the Top 10 SRE
Agent Recommendations section are identical across all ten pages:

    [ title + purpose ]  ->  [ shared parameters ]  ->  [ Top 10 SRE Agent
    Recommendations ]  ->  [ page-specific sections ]  ->  [ operating-model footer ]

Specs live in ``builder/specs/*.py``; each exposes ORDER, SLUG, NAME, PAGE,
PURPOSE and a ``sections()`` function returning the page body. Output is written
to ``workbooks/NN-slug.workbook.json`` and is deterministic.

Usage:
    python builder/build_workbooks.py [--output-dir ../workbooks]
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(HERE, "shared")
SPECS = os.path.join(HERE, "specs")
for p in (SHARED, SPECS):
    if p not in sys.path:
        sys.path.insert(0, p)

import helpers  # noqa: E402
from parameters import parameters_item  # noqa: E402
from recommendations import top10_section  # noqa: E402
from scorecards import scorecard_for  # noqa: E402

PLACEHOLDER_WORKSPACE = (
    "/subscriptions/<sub-id>/resourceGroups/<rg>/providers/"
    "Microsoft.OperationalInsights/workspaces/<law>"
)

_FOOTER = (
    "\n---\n### Operating model (safe-update)\n"
    "Live data on this page refreshes automatically. Changes to the **workbook "
    "definition** follow the suite's safe-update model:\n\n"
    "| Change | How it happens |\n| --- | --- |\n"
    "| Live data refresh | Automatic |\n"
    "| Low-risk metadata (titles, descriptions, layout) | Agent may auto-apply, versioned |\n"
    "| Medium/high-risk panel or query changes | Pull request + human approval |\n"
    "| SLO, alert, remediation, health-scoring, access scope, panel deletion | "
    "**Mandatory approval** - agent records a recommendation only |\n\n"
    "Every change is versioned, validated, auditable, and reversible. See "
    "`docs/operating-model.md`."
)


def _load_specs() -> list[Any]:
    specs = []
    for mod in pkgutil.iter_modules([SPECS]):
        if mod.name.startswith("_"):
            continue
        specs.append(importlib.import_module(mod.name))
    specs.sort(key=lambda m: m.ORDER)
    return specs


def build_workbook(spec: Any) -> dict[str, Any]:
    helpers.reset_names()
    page = getattr(spec, "PAGE", spec.NAME)

    header = helpers.markdown(
        f"# {spec.NAME}\n\n{spec.PURPOSE}\n\n"
        f"> **SRE Agent at the center.** This page is an operating surface for both "
        f"humans and the SRE Agent. Set the **Subscriptions** and **Log Analytics "
        f"workspace** parameters, then act on the Top 10 recommendations below.",
        name="page-header",
    )
    footer = helpers.markdown(_FOOTER, name="operating-model-footer")

    items: list[dict[str, Any]] = [header, parameters_item()]
    if getattr(spec, "SECTIONS_FIRST", False):
        # This page leads with its own tabbed navigation; put the page body above the
        # shared hero scorecard and Top 10 so the tabs sit at the very top.
        items += spec.sections()
        items += scorecard_for(spec.SLUG)
        items += top10_section(spec.NAME, page)
    else:
        items += scorecard_for(spec.SLUG)
        items += top10_section(spec.NAME, page)
        items += spec.sections()
    items.append(footer)

    return {
        "version": "Notebook/1.0",
        "items": items,
        "fallbackResourceIds": [PLACEHOLDER_WORKSPACE],
        "styleSettings": {"paddingStyle": "wide"},
        "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the SRE Agent Observability workbook suite.")
    parser.add_argument(
        "--output-dir",
        default=os.path.join(HERE, "..", "workbooks"),
        help="Directory for the built *.workbook.json files.",
    )
    args = parser.parse_args()
    out_dir = os.path.abspath(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    specs = _load_specs()
    if not specs:
        print("No specs found in builder/specs/", file=sys.stderr)
        return 1

    manifest = []
    for spec in specs:
        workbook = build_workbook(spec)
        fname = f"{spec.ORDER:02d}-{spec.SLUG}.workbook.json"
        path = os.path.join(out_dir, fname)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(workbook, f, indent=2, ensure_ascii=False)
            f.write("\n")
        manifest.append(
            {"order": spec.ORDER, "file": fname, "displayName": spec.NAME, "page": getattr(spec, "PAGE", spec.NAME)}
        )
        print(f"[build] wrote {fname} ({len(workbook['items'])} items)")

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"schemaVersion": "1.0", "suite": "SRE Agent Observability Workbooks", "workbooks": manifest}, f, indent=2)
        f.write("\n")
    print(f"[build] {len(manifest)} workbook(s) -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
