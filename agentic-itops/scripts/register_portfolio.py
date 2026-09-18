"""Prepare offline, or explicitly register all nine agents; never invoke a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.consumer.contracts import strict_json
from agentic_itops.portfolio import compile_portfolio
from agentic_itops.registration import (
    FoundryVersionService, RegistrationError, RegistrationJournal,
    prepare_registration, register_portfolio,
)


def _artifact(path: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(ROOT / ".build"):
        raise RegistrationError("registration artifacts must stay in the project's ignored .build directory")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--model-deployment", required=True)
    parser.add_argument("--sampling", required=True, choices=["source", "model_defaults"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--credential", choices=["azure_cli", "managed_identity"])
    parser.add_argument("--tenant-id")
    parser.add_argument("--client-id")
    args = parser.parse_args()
    try:
        output = _artifact(args.output)
        if output.exists():
            raise RegistrationError("refusing to overwrite an existing registration receipt")
        bindings = strict_json(args.bindings.read_text(encoding="utf-8"))
        portfolio = compile_portfolio(ROOT, bindings, mode="trusted_dispatch")
        plan = prepare_registration(
            portfolio, args.project_endpoint, args.model_deployment, sampling=args.sampling,
        )
        if not args.apply:
            if any((args.approve_plan_sha256, args.journal, args.credential, args.tenant_id, args.client_id)):
                raise RegistrationError("credential/approval options require explicit --apply")
            result = {"plan_sha256": plan.sha256, "plan": plan.document, "azure_writes": False}
        else:
            if args.approve_plan_sha256 != plan.sha256 or not args.journal:
                raise RegistrationError("--apply requires the exact reviewed digest and a persistent journal")
            journal_path = _artifact(args.journal)
            if journal_path == output:
                raise RegistrationError("journal and output must be different paths")
            from azure.ai.projects import AIProjectClient
            from azure.identity import AzureCliCredential, ManagedIdentityCredential

            if args.credential == "azure_cli" and args.tenant_id and not args.client_id:
                credential = AzureCliCredential(tenant_id=str(UUID(args.tenant_id)), process_timeout=60)
            elif args.credential == "managed_identity" and args.client_id and not args.tenant_id:
                credential = ManagedIdentityCredential(client_id=str(UUID(args.client_id)))
            else:
                raise RegistrationError("select Azure CLI + tenant or explicit managed identity + client UUID")
            journal = RegistrationJournal(journal_path, plan)
            try:
                with credential, AIProjectClient(
                    endpoint=args.project_endpoint, credential=credential,
                    retry_total=0, connection_timeout=10, read_timeout=60,
                ) as client:
                    result = register_portfolio(
                        plan, journal, FoundryVersionService(client),
                        approved_sha256=args.approve_plan_sha256,
                    )
            finally:
                journal.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
        print(f"{'Registered' if args.apply else 'Prepared'} all nine agents; receipt: {output}")
        return 0
    except Exception as exc:
        print(f"portfolio registration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
