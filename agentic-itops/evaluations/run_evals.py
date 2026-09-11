"""Run the evaluation suites.

    python evaluations/run_evals.py
    python evaluations/run_evals.py --suite gate
    python evaluations/run_evals.py --json > results.json

Exit codes:
    0  all passed
    1  non-blocking failures
    2  blocking failures in the safety-gate suite -- do not ship
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentic_itops.normalizer import (  # noqa: E402
    load_payloads,
    normalize_and_correlate,
    normalize_one,
)
from agentic_itops.safety_gate import (  # noqa: E402
    ChangeRequest,
    ChangeSafetyGate,
    load_policy,
)
from agentic_itops.tools import search_knowledge  # noqa: E402
from evaluators import (  # noqa: E402
    CaseResult,
    SuiteResult,
    assert_fields,
    evaluate_retrieval,
    load_cases,
    render,
    summarize_retrieval,
)

ALERTS = Path(__file__).resolve().parents[1] / "data" / "alerts"


def suite_normalization() -> SuiteResult:
    suite = SuiteResult("normalization")
    for case in load_cases("normalization_cases.jsonl"):
        try:
            envelope = normalize_one(case["payload"])
            suite.results.append(
                assert_fields(case["case_id"], envelope.to_dict(include_raw=False), case["expect"])
            )
        except Exception as exc:
            suite.results.append(
                CaseResult(case["case_id"], False, case["expect"], None, f"raised {exc}")
            )
    return suite


def suite_correlation() -> SuiteResult:
    suite = SuiteResult("correlation")
    for case in load_cases("correlation_cases.jsonl"):
        payloads = []
        for filename in case["input_files"]:
            payloads.extend(load_payloads(ALERTS / filename))
        incidents, failures = normalize_and_correlate(payloads)

        problems = []
        if len(incidents) != case["expect_incidents"]:
            problems.append(
                f"expected {case['expect_incidents']} incidents, got {len(incidents)}"
            )
        if failures:
            problems.append(f"{len(failures)} payload(s) failed to normalize")

        wanted_sources = case.get("expect_sources") or []
        if wanted_sources:
            got = set(incidents[0].sources) if incidents else set()
            missing = set(wanted_sources) - got
            if missing:
                problems.append(f"incident 0 missing sources: {sorted(missing)}")

        suite.results.append(
            CaseResult(
                case_id=case["case_id"],
                passed=not problems,
                expected={"incidents": case["expect_incidents"], "sources": wanted_sources},
                actual={
                    "incidents": len(incidents),
                    "sources": incidents[0].sources if incidents else [],
                },
                detail="; ".join(problems),
            )
        )
    return suite


def suite_retrieval() -> SuiteResult:
    suite = SuiteResult("knowledge_retrieval")
    metrics = []
    for case in load_cases("retrieval_cases.jsonl"):
        # min_score filters weak topical matches so "no match" stays meaningful.
        found = search_knowledge(case["query"], top_k=3, min_score=2.0)
        result, metric = evaluate_retrieval(
            case["case_id"], found.get("results", []), case["expect_doc_id"], k=3
        )
        suite.results.append(result)
        metrics.append(metric)
    suite.metrics = summarize_retrieval(metrics, k=3)
    return suite


def suite_gate() -> SuiteResult:
    """Security tests. Every case is blocking."""
    suite = SuiteResult("change_safety_gate")
    base_policy = load_policy()

    for case in load_cases("safety_gate_cases.jsonl"):
        policy = copy.deepcopy(base_policy)
        policy.update(case.get("policy_override") or {})
        gate = ChangeSafetyGate(policy=policy)

        at = case.get("at")
        request = ChangeRequest(
            **case["request"],
            at=datetime.fromisoformat(at) if at else datetime.now(timezone.utc),
        )
        decision = gate.evaluate(request)

        problems = []
        if decision.effect.value != case["expect_effect"]:
            problems.append(
                f"effect: expected {case['expect_effect']}, got {decision.effect.value}"
            )
        if case.get("expect_tier") and decision.approval_tier != case["expect_tier"]:
            problems.append(
                f"tier: expected {case['expect_tier']}, got {decision.approval_tier}"
            )
        if case.get("expect_rule") and case["expect_rule"] not in decision.matched_rules:
            problems.append(
                f"rule: expected {case['expect_rule']} in {decision.matched_rules}"
            )
        if not decision.reasons:
            problems.append("decision carried no reasons -- a silent verdict is unusable")
        if not decision.audit_id:
            problems.append("decision carried no audit id")

        suite.results.append(
            CaseResult(
                case_id=case["case_id"],
                passed=not problems,
                expected=case["expect_effect"],
                actual=decision.effect.value,
                detail="; ".join(problems),
                severity="blocking",
            )
        )
    return suite


SUITES = {
    "normalization": suite_normalization,
    "correlation": suite_correlation,
    "retrieval": suite_retrieval,
    "gate": suite_gate,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Agentic ITOps evaluations")
    parser.add_argument("--suite", choices=sorted(SUITES), help="run one suite only")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    selected = [args.suite] if args.suite else list(SUITES)
    results = [SUITES[name]() for name in selected]

    if args.json:
        print(json.dumps([s.to_dict() for s in results], indent=2, default=str))
    else:
        print(render(results))

    if any(s.blocking_failures for s in results):
        return 2
    if any(s.failures for s in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
