"""Offline schema by default; Azure bootstrap requires explicit approval flags."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentic_itops.review.search_delivery import (
    INDEX_SCHEMA, SCHEMA_HASH, SearchServiceSettings, bootstrap_index,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-schema", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approval-reference")
    parser.add_argument("--expected-schema-hash")
    parser.add_argument("--auth", choices=["managed-identity", "azure-cli"], default="managed-identity")
    args = parser.parse_args()
    if args.write_schema:
        args.write_schema.write_text(json.dumps(INDEX_SCHEMA, indent=2) + "\n", encoding="utf-8")
    if not args.apply:
        print(json.dumps({"mode": "offline-schema-only", "schema_hash": SCHEMA_HASH, "index": INDEX_SCHEMA}, indent=2))
        return
    if not args.approval_reference or args.expected_schema_hash != SCHEMA_HASH:
        parser.error("--apply requires an explicit approval reference and the exact current schema hash")
    from azure.identity import AzureCliCredential, ManagedIdentityCredential

    settings = SearchServiceSettings.from_environment()
    credential = AzureCliCredential(process_timeout=15) if args.auth == "azure-cli" else ManagedIdentityCredential(
        client_id=settings.client_id,
    )
    try:
        print(json.dumps(bootstrap_index(
            settings, credential, approval_reference=args.approval_reference,
            expected_schema_hash=args.expected_schema_hash,
        ), indent=2))
    finally:
        credential.close()


if __name__ == "__main__":
    main()
