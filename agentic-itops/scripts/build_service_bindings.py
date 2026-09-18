"""Offline: use actual deployed host settings and reviewed Search configuration."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.consumer.contracts import strict_json
from agentic_itops.portfolio import canonical_json, compile_portfolio
from agentic_itops.service_backends import deployment_bindings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path, required=True,
                        help="Ignored JSON deployment settings, not model/browser input")
    parser.add_argument("--search-binding", type=Path,
                        help="Optional exact real adapter contract; otherwise constructed from ITOPS_SEARCH_SNAPSHOT_ID")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = strict_json(args.settings.read_bytes())
    from agentic_itops.service_search import approved_search_binding
    search = approved_search_binding(settings["ITOPS_SEARCH_SNAPSHOT_ID"])
    if args.search_binding is not None and strict_json(args.search_binding.read_bytes()) != search:
        raise ValueError("Search binding differs from the implemented SQL-guarded adapter")
    binding = deployment_bindings(
        ROOT, tools_origin=settings["ITOPS_TOOLS_ORIGIN"], tools_audience=settings["ITOPS_TOOLS_AUDIENCE"],
        model_deployment=settings["ITOPS_MODEL_DEPLOYMENT"],
        search_binding=search)
    portfolio = compile_portfolio(ROOT, binding, mode="trusted_dispatch")
    if not portfolio.bindings_complete:
        raise ValueError("; ".join(portfolio.blockers))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(binding) + "\n", encoding="utf-8")
    print(json.dumps({"bindings_path": str(args.output), "portfolio_sha256": portfolio.content_sha256,
                      "agent_count": len(portfolio.agents), "live_acceptance": False}))


if __name__ == "__main__":
    main()
