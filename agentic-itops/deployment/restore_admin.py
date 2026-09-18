"""Explicit crash-recovery command. Dry-run by default; never removes original operator access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from .bootstrap import BootstrapError, Journal
from .control_plane import ARM, API, WEB_API, Operator


def restore(operator: Operator, journal: Journal, approved_object_id: str) -> dict:
    window = journal.data["admin-window"]
    original, plan = window["original"], window["approval"]
    if (str(UUID(approved_object_id)) != str(UUID(original["sid"]))
            or original["sid"].lower() != plan["restore_sql_admin_object_id"].lower()
            or window["resource_id"] != plan["sql_admin_id"]):
        raise BootstrapError("recovery record does not match explicitly approved original operator")
    current = operator.request("GET", ARM + window["resource_id"] + API).json()["properties"]
    if current["sid"].lower() not in {
        plan["temporary_sql_admin_object_id"].lower(), original["sid"].lower()
    }:
        raise BootstrapError("SQL administrator changed outside this window; do not overwrite another operator")
    site_id = plan["bootstrap_site_id"]
    try:
        operator.request("POST", ARM + site_id + "/stop" + WEB_API, accepted=(200, 202))
    finally:
        try:
            operator.set_admin(window["resource_id"], original)
            journal.save("admin-window", {**window, "state": "restored"})
        finally:
            operator.settings(site_id, {"ITOPS_BOOTSTRAP_AUTHORIZED": "false"},
                              remove=("ITOPS_REVIEW_ENTRA_CLIENT_SECRET", "ITOPS_BOOTSTRAP_MANIFEST_SHA256",
                                      "ITOPS_BOOTSTRAP_ATTEMPT_ID", "ITOPS_SOURCE_PROBE_WITNESS_JSON"))
    return {"state": "restored", "operator_object_id": original["sid"], "bootstrap_stopped": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--approved-restore-object-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    journal = Journal(args.journal)
    window = journal.data["admin-window"]
    if str(UUID(args.approved_restore_object_id)) != str(UUID(window["original"]["sid"])):
        raise BootstrapError("recovery operator differs from explicit approval")
    if not args.execute:
        print(json.dumps({"sql_admin_id": window["resource_id"], "restore_object_id": window["original"]["sid"],
                          "stop_site": window["approval"]["bootstrap_site_id"]}, sort_keys=True))
        return
    from azure.identity import AzureCliCredential
    import httpx
    with AzureCliCredential() as credential, httpx.Client(timeout=120, follow_redirects=False) as client:
        print(json.dumps(restore(Operator(credential, client), journal, args.approved_restore_object_id)))


if __name__ == "__main__":
    main()
