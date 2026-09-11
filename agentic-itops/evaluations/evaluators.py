"""Evaluators for the Agentic ITOps skills.

Split deliberately into two kinds:

* **Deterministic assertions** for the code-hosted skills. Normalization,
  correlation and gate decisions have exactly one correct answer, so
  these are pass/fail, and a regression is a bug rather than a drift in
  quality.
* **Retrieval metrics** for the knowledge layer, where "correct" is a
  ranking rather than a value.

Gate evaluation is treated as a security test suite, not a quality
metric. Any failure there is release-blocking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

DATASETS = Path(__file__).parent / "datasets"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    expected: Any = None
    actual: Any = None
    detail: str = ""
    severity: str = "normal"  # normal | blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
            "severity": self.severity,
        }


@dataclass
class SuiteResult:
    name: str
    results: list[CaseResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def failures(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed]

    @property
    def blocking_failures(self) -> list[CaseResult]:
        return [r for r in self.failures if r.severity == "blocking"]

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.name,
            "passed": self.passed,
            "total": self.total,
            "pass_rate": self.pass_rate,
            "blocking_failures": len(self.blocking_failures),
            "metrics": self.metrics,
            "failures": [r.to_dict() for r in self.failures],
        }


def load_cases(filename: str) -> list[dict[str, Any]]:
    path = DATASETS / filename
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]


# --- deterministic field assertions -----------------------------------


def assert_fields(
    case_id: str,
    actual: dict[str, Any],
    expected: dict[str, Any],
    severity: str = "normal",
) -> CaseResult:
    """Check every expected key. Dotted paths are supported."""
    mismatches = []
    for key, want in expected.items():
        got = _read_path(actual, key)
        if got != want:
            mismatches.append(f"{key}: expected {want!r}, got {got!r}")
    return CaseResult(
        case_id=case_id,
        passed=not mismatches,
        expected=expected,
        actual={k: _read_path(actual, k) for k in expected},
        detail="; ".join(mismatches),
        severity=severity,
    )


def _read_path(obj: Any, path: str) -> Any:
    node = obj
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
        if node is None:
            return None
    if hasattr(node, "value"):  # unwrap Enum
        return node.value
    return node


# --- retrieval metrics ------------------------------------------------


def evaluate_retrieval(
    case_id: str,
    results: list[dict[str, Any]],
    expected_doc_id: str,
    k: int = 3,
) -> tuple[CaseResult, dict[str, float]]:
    """Hit@k and reciprocal rank for one query.

    ``expected_doc_id`` of ``"NONE"`` asserts that nothing should match --
    an important case, because returning a plausible-but-wrong runbook is
    worse than returning nothing.
    """
    ids = [str(r.get("doc_id", "")) for r in results[:k]]

    if expected_doc_id == "NONE":
        passed = len(results) == 0
        return (
            CaseResult(
                case_id=case_id,
                passed=passed,
                expected="no match",
                actual=ids,
                detail="" if passed else "returned a match where none should exist",
            ),
            {"hit": float(passed), "reciprocal_rank": float(passed)},
        )

    rank = next(
        (i + 1 for i, doc_id in enumerate(ids) if expected_doc_id in doc_id),
        0,
    )
    hit = 1.0 if rank else 0.0
    return (
        CaseResult(
            case_id=case_id,
            passed=bool(rank),
            expected=expected_doc_id,
            actual=ids,
            detail="" if rank else f"expected doc not in top {k}",
        ),
        {"hit": hit, "reciprocal_rank": (1.0 / rank) if rank else 0.0},
    )


def summarize_retrieval(metrics: Iterable[dict[str, float]], k: int = 3) -> dict[str, float]:
    collected = list(metrics)
    if not collected:
        return {}
    return {
        f"hit_at_{k}": round(sum(m["hit"] for m in collected) / len(collected), 3),
        "mrr": round(sum(m["reciprocal_rank"] for m in collected) / len(collected), 3),
        "cases": len(collected),
    }


# --- report rendering -------------------------------------------------


def render(suites: list[SuiteResult]) -> str:
    lines = ["", "=" * 74, "AGENTIC ITOPS -- EVALUATION RESULTS", "=" * 74]
    total_blocking = 0

    for suite in suites:
        status = "PASS" if not suite.failures else "FAIL"
        marker = " <-- BLOCKING" if suite.blocking_failures else ""
        lines.append(
            f"\n{status:>4}  {suite.name:<34} {suite.passed}/{suite.total} "
            f"({suite.pass_rate * 100:.0f}%){marker}"
        )
        for key, value in suite.metrics.items():
            lines.append(f"        {key}: {value}")
        for failure in suite.failures[:6]:
            lines.append(f"        x {failure.case_id}: {failure.detail}")
        if len(suite.failures) > 6:
            lines.append(f"        ... and {len(suite.failures) - 6} more")
        total_blocking += len(suite.blocking_failures)

    lines.append("\n" + "-" * 74)
    passed = sum(s.passed for s in suites)
    total = sum(s.total for s in suites)
    lines.append(f"TOTAL: {passed}/{total} cases passed")
    if total_blocking:
        lines.append(
            f"\n  {total_blocking} BLOCKING failure(s) in the safety-gate suite.\n"
            f"  These are security tests. Do not ship past them."
        )
    lines.append("=" * 74)
    return "\n".join(lines)
