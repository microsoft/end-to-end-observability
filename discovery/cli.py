"""Command line entry point for the discovery tool.

Usage:
    python -m discovery scan \
        --subscription <subId> [--subscription <subId> ...] \
        [--management-group <mgId>] \
        [--rules discovery/rules/default.yaml] \
        [--out-dir ./out]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .graph_client import GraphClient, GraphScope, iter_resources
from .queries import INVENTORY_WITH_DIAGNOSTICS
from .report import write_json, write_markdown
from .rules_engine import evaluate, load_rules

_DEFAULT_RULES = Path(__file__).parent / "rules" / "default.yaml"

log = logging.getLogger("discovery")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discovery",
        description="Scan an Azure estate and recommend monitoring actions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Run a discovery sweep.")
    scan.add_argument(
        "--subscription",
        action="append",
        default=[],
        help="Subscription ID to scan (repeatable).",
    )
    scan.add_argument(
        "--management-group",
        action="append",
        default=[],
        help="Management group ID to scan (repeatable).",
    )
    scan.add_argument(
        "--rules",
        type=Path,
        default=_DEFAULT_RULES,
        help="Path to rules YAML (default: built-in rules).",
    )
    scan.add_argument(
        "--out-dir",
        type=Path,
        default=Path("./out"),
        help="Output directory for the report (default: ./out).",
    )
    scan.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command != "scan":
        return 2

    scope = GraphScope(
        subscriptions=tuple(args.subscription),
        management_groups=tuple(args.management_group),
    )
    log.info("Querying Azure Resource Graph (scope=%s)", scope)
    client = GraphClient(scope)
    rows = client.query(INVENTORY_WITH_DIAGNOSTICS)
    log.info("Returned %d resources", len(rows))

    rules = load_rules(args.rules)
    log.info("Loaded %d rules from %s", len(rules), args.rules)

    findings = evaluate(iter_resources(rows), rules)
    log.info("Produced %d findings", len(findings))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = write_json(findings, args.out_dir / "discovery-report.json")
    md_path = write_markdown(findings, args.out_dir / "discovery-report.md")
    log.info("Wrote %s", json_path)
    log.info("Wrote %s", md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
