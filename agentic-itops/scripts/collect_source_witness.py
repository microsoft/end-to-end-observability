"""Collect a fresh count-only operator witness for the separate worker-MI probe."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.access_probe import AccessProofError, LogsCountClient, operator_witness, query_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import httpx
    from azure.identity import AzureCliCredential
    from azure.core.exceptions import ClientAuthenticationError

    try:
        tenant = str(UUID(args.tenant_id))
        contract = query_contract(args.workspace_id, args.run_id, args.start, args.end)
        path = args.output.resolve()
        if not path.is_relative_to(ROOT / ".build") or path.exists():
            raise ValueError("use a new ignored .build file for the secret-free source witness")
        with AzureCliCredential(tenant_id=tenant, process_timeout=60) as credential, httpx.Client() as client:
            witness = operator_witness(contract, LogsCountClient(credential, client).query)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump({"contract": contract, "witness": witness}, handle, sort_keys=True, indent=2)
            handle.write("\n")
        print(f"Count-only operator witness saved to {path}; expires five minutes after collection.")
        return 0
    except (ValueError, OSError, AccessProofError) as exc:
        print(f"Source witness stopped: {exc}", file=sys.stderr)
        return 2
    except (ClientAuthenticationError, httpx.RequestError) as exc:
        print(f"Source witness stopped: {type(exc).__name__}; no MI access proof produced.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
