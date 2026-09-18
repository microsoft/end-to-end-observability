"""Compile the all-nine source portfolio offline; never deploy or authenticate.

Run from agentic-itops:
  .\\.venv\\Scripts\\python.exe scripts\\compile_portfolio.py --output portfolio.json
  .\\.venv\\Scripts\\python.exe scripts\\compile_portfolio.py --bindings bindings.json --search-approvals isolation.json --search-budget-approvals bounded-runtime.json --sdk

A review plan may be blocked. Use --require-bound to make missing bindings an
error. --sdk requires complete bindings and only serializes SDK objects locally.
The approved/default mode is trusted_dispatch. Native Search requires an exact
snapshot/filter approval from the separately supplied --search-approvals registry
(binding SHA256 -> verified evidence ID). Never derive approvals from model text.
--search-budget-approvals separately proves bounded hidden tool/model rounds in
a compatible runtime. A function-only worker cannot supply that approval.
All plans retain live verification gates even when binding-complete.
The alternative registered-handler Search binding emits search_approved_knowledge
as a strict no-argument function. It requires explicit snapshot/filter and backend
contracts, not fabricated native approvals. Backend authorization and eligibility
must still be implemented and verified separately.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.portfolio import build_sdk_definitions, compile_portfolio
from agentic_itops.portfolio_schemas import PortfolioError


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PortfolioError(f"duplicate binding field: {key}")
        result[key] = value
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--bindings", type=Path)
    parser.add_argument("--mode", choices=["trusted_dispatch"], default="trusted_dispatch")
    parser.add_argument("--search-approvals", type=Path)
    parser.add_argument("--search-budget-approvals", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-bound", action="store_true")
    parser.add_argument("--sdk", action="store_true", help="optional offline SDK object serialization")
    args = parser.parse_args()
    try:
        bindings = (json.loads(args.bindings.read_text(encoding="utf-8"), object_pairs_hook=_unique)
                    if args.bindings else None)
        if bindings is not None and not isinstance(bindings, dict):
            raise PortfolioError("binding configuration must be a JSON object")
        approvals = (json.loads(args.search_approvals.read_text(encoding="utf-8"), object_pairs_hook=_unique)
                     if args.search_approvals else None)
        budget_approvals = (
            json.loads(args.search_budget_approvals.read_text(encoding="utf-8"), object_pairs_hook=_unique)
            if args.search_budget_approvals else None
        )
        portfolio = compile_portfolio(args.root, bindings, mode=args.mode, search_approvals=approvals,
                                      search_budget_approvals=budget_approvals)
        result = portfolio.to_dict()
        if args.require_bound and not portfolio.bindings_complete:
            raise PortfolioError("\n".join(portfolio.blockers))
        if args.sdk:
            result["sdk_definitions"] = {
                name: definition.as_dict() for name, definition in build_sdk_definitions(portfolio).items()
            }
        text = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            output = args.output.resolve()
            root = args.root.resolve()
            if not output.is_relative_to(root) or output.is_relative_to(root / "agents"):
                raise PortfolioError("output must be within project and outside agent sources")
            if output.exists():
                raise PortfolioError("refusing to overwrite an existing output file")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    except (PortfolioError, OSError, ValueError, ImportError) as exc:
        print(f"portfolio compilation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
