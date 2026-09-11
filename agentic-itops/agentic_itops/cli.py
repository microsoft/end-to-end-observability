"""Command line interface.

    python -m agentic_itops normalize  --input data/alerts
    python -m agentic_itops hygiene    --input data/alerts/bulk-amba-sample.jsonl
    python -m agentic_itops gate       --action restart --resource <arm-id> --env prod
    python -m agentic_itops demo       --input data/alerts --limit 1
    python -m agentic_itops search     --query "pump cavitation"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import hygiene
from .normalizer import (
    load_directory,
    load_payloads,
    normalize_and_correlate,
    normalize_many,
    normalization_report,
)
from .orchestrator import FoundryReasoner, StubReasoner, run_demo
from .safety_gate import ChangeRequest, ChangeSafetyGate
from .tools import search_knowledge

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

#: Scenario time for the sample data: Monday 2026-09-14, 13:42 UTC.
#: Demos pin to this so output is reproducible and does not drift into
#: a change-freeze window depending on when you happen to run it.
DEFAULT_DEMO_AT = "2026-09-14T13:42:00+00:00"


def _parse_at(value: str) -> "datetime | None":
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"could not parse --at value: {value!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load(path_str: str) -> list[dict[str, Any]]:
    path = Path(path_str)
    if not path.exists():
        raise SystemExit(f"input not found: {path}")
    return load_directory(path) if path.is_dir() else load_payloads(path)


def cmd_normalize(args: argparse.Namespace) -> int:
    payloads = _load(args.input)
    incidents, failures = normalize_and_correlate(payloads)
    report = normalization_report(incidents, failures)

    if args.json:
        print(json.dumps(
            {"report": report, "incidents": [i.to_dict(include_raw=False) for i in incidents]},
            indent=2, default=str,
        ))
        return 0

    print("SIGNAL NORMALIZATION")
    print(f"  signals in:        {report['signals_in']}")
    print(f"  normalized:        {report['signals_normalized']}")
    print(f"  failed:            {report['signals_failed']}")
    print(f"  incidents out:     {report['incidents_out']}")
    print(f"  reduction:         {report['reduction_ratio'] * 100:.1f}%")
    print(f"  cross-source:      {report['cross_source_incidents']}")
    print()
    for incident in incidents[: args.limit]:
        primary = incident.primary
        marker = "  <-- CORRELATED" if incident.correlated else ""
        print(f"  {primary.summary_line()}{marker}")
        if incident.correlated:
            print(f"      sources: {', '.join(incident.sources)} ({len(incident.envelopes)} signals)")
    if failures:
        print(f"\n  {len(failures)} payload(s) had no matching adapter:")
        for failure in failures[:5]:
            print(f"    - {failure['error']}")
    return 0


def cmd_hygiene(args: argparse.Namespace) -> int:
    payloads = _load(args.input)
    envelopes, _ = normalize_many(payloads)
    report = hygiene.analyze(envelopes)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(hygiene.format_report(report, limit=args.limit))
        print("\nTop rationale:")
        for finding in report["findings"][:3]:
            print(f"  {finding['rule'][:60]}")
            for note in finding["rationale"]:
                print(f"    - {note}")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    gate = ChangeSafetyGate(
        policy=None if not args.policy else __import__("yaml").safe_load(
            Path(args.policy).read_text(encoding="utf-8")
        )
    )
    request = ChangeRequest(
        action=args.action,
        resource_id=args.resource,
        environment=args.env,
        service=args.service,
        estimated_affected=args.affected,
        justification=args.justification,
        at=_parse_at(args.at),
    )
    decision = gate.evaluate(request)
    if args.json:
        print(json.dumps(decision.to_dict(), indent=2))
    else:
        print(decision.explain())
    # Non-zero exit on anything that is not an outright allow, so this
    # is usable as a CI or pipeline check.
    return 0 if decision.allowed else 2


def cmd_search(args: argparse.Namespace) -> int:
    results = search_knowledge(args.query, top_k=args.limit, kind=args.kind)
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    if not results["results"]:
        print(f"No knowledge matched '{args.query}'.")
        print("That is a valid answer -- an invented runbook is worse than none.")
        return 0
    for item in results["results"]:
        print(f"[{item['score']:>6.2f}] {item['kind']:<11} {item['title']}")
        print(f"          {item['path']}")
        print(f"          {item['excerpt'][:180]}...")
        print()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    payloads = _load(args.input)
    reasoner = FoundryReasoner() if args.foundry else StubReasoner()
    results = run_demo(
        payloads, reasoner=reasoner, limit=args.limit, evaluate_at=_parse_at(args.at)
    )

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
        return 0

    for result in results:
        incident = result.incident
        print("=" * 78)
        print(f"INCIDENT  {incident['fingerprint'][:12]}  [{incident['severity'].upper()}]")
        print(f"  {incident['title']}")
        print(f"  service={incident['service']}  env={incident['environment']}  "
              f"symptom={incident['symptom_class']}")
        print(f"  {incident['signal_count']} signal(s) from {', '.join(incident['sources'])}"
              + ("   <-- CORRELATED ACROSS TOOLS" if incident["correlated_across_sources"] else ""))

        print("\n-- INSTITUTIONAL MEMORY " + "-" * 53)
        if result.knowledge.get("results"):
            for item in result.knowledge["results"][:2]:
                print(f"  [{item['score']:>5.2f}] {item['kind']}: {item['title']}")
        else:
            print("  no prior runbook or postmortem matched")

        print("\n-- WHAT CHANGED " + "-" * 61)
        changes = result.changes.get("changes", [])
        if changes:
            for change in changes[:3]:
                print(f"  {change.get('timestamp', '')[:19]}  {change.get('operation', '')}"
                      f"  by {change.get('caller', 'unknown')}")
                if change.get("correlation_hint"):
                    print(f"      hint: {change['correlation_hint']}")
        else:
            print("  no changes in the last 24h")

        print("\n-- HYPOTHESES " + "-" * 63)
        for i, hypothesis in enumerate(result.rca.get("hypotheses", []), 1):
            print(f"  {i}. [{hypothesis.get('confidence', '?'):>6}] {hypothesis.get('hypothesis')}")
            for line in hypothesis.get("evidence", []):
                if line:
                    print(f"         evidence: {line}")

        print("\n-- PROPOSED REMEDIATION " + "-" * 53)
        print(f"  action:    {result.plan.get('proposed_action')}")
        print(f"  rationale: {result.plan.get('rationale')}")

        print("\n-- CHANGE-SAFETY GATE (code, not a model) " + "-" * 35)
        decision = result.decision
        print(f"  EFFECT: {decision['effect'].upper()}")
        if decision["approval_tier"] != "none":
            print(f"  approval required: {decision['approval_tier']}")
        for reason in decision["reasons"]:
            print(f"    - {reason}")
        print(f"  audit id: {decision['audit_id']}  policy: {decision['policy_version']}")

        print("\n-- COMMS DRAFT " + "-" * 62)
        print(f"  engineer: {result.comms.get('engineer_update', '')}")
        print(f"  exec:     {result.comms.get('executive_update', '')}")

        print("\n  NO ACTION WAS TAKEN. Execution is handed to Azure SRE Agent")
        print("  only after a human approves. Foundry is the brain; SRE Agent is the hands.")
        print("=" * 78)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic_itops",
        description="Agentic ITOps -- Foundry agent and skill portfolio",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_norm = sub.add_parser("normalize", help="normalize and correlate alert payloads")
    p_norm.add_argument("--input", default=str(DATA_DIR / "alerts"))
    p_norm.add_argument("--limit", type=int, default=20)
    p_norm.add_argument("--json", action="store_true")
    p_norm.set_defaults(func=cmd_normalize)

    p_hyg = sub.add_parser("hygiene", help="alert hygiene analysis (Catalyst artifact)")
    p_hyg.add_argument("--input", default=str(DATA_DIR / "alerts"))
    p_hyg.add_argument("--limit", type=int, default=10)
    p_hyg.add_argument("--json", action="store_true")
    p_hyg.set_defaults(func=cmd_hygiene)

    p_gate = sub.add_parser("gate", help="evaluate a change request against policy")
    p_gate.add_argument("--action", required=True)
    p_gate.add_argument("--resource", required=True)
    p_gate.add_argument("--env", default="prod")
    p_gate.add_argument("--service", default="unknown")
    p_gate.add_argument("--affected", type=int, default=1)
    p_gate.add_argument("--justification", default="")
    p_gate.add_argument("--policy", default="")
    p_gate.add_argument("--at", default="", help="pin evaluation time (ISO-8601)")
    p_gate.add_argument("--json", action="store_true")
    p_gate.set_defaults(func=cmd_gate)

    p_search = sub.add_parser("search", help="search runbooks and postmortems")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=3)
    p_search.add_argument("--kind", default="", choices=["", "runbook", "postmortem"])
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_demo = sub.add_parser("demo", help="run the full incident loop")
    p_demo.add_argument("--input", default=str(DATA_DIR / "alerts"))
    p_demo.add_argument("--limit", type=int, default=1)
    p_demo.add_argument("--foundry", action="store_true", help="use Foundry agents instead of stubs")
    p_demo.add_argument(
        "--at",
        default=DEFAULT_DEMO_AT,
        help="pin gate evaluation time (ISO-8601); pass '' to use wall clock",
    )
    p_demo.add_argument("--json", action="store_true")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
