"""One local facade rehearsal record, not a queue, bridge or delivery service."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from operations import context as deployment
from operations.lifecycle import EVIDENCE_HOST, canonical_uuid
from operations.servicenow import (
    CLIENT_ID, SENSITIVE, CaseClient, ClientCredentials, Contracts, ServiceNowError,
)

CTX = deployment.load()
RESOURCE = CTX.targets["linux"].resource_id
LIMIT = 48000
TERMINAL = {"applied", "already_applied"}
STATES = {"prepared", "pending", "applied", "blocked"}


class DemoError(RuntimeError):
    """Fixed messages only: never include an input, response or credential."""


def encode(value: dict) -> bytes:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > LIMIT or SENSITIVE.search(raw.decode()):
        raise DemoError("Input is oversized or contains suspected credentials.")
    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if re.search(r"secret|password|token|authorization|private.?key|api.?key", key, re.I):
                    raise DemoError("Input contains a credential-shaped field.")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
    inspect(value)
    return raw


def load(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DemoError("Duplicate JSON fields are not admitted.")
            result[key] = value
        return result
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise DemoError("The local JSON exceeds its bound.")
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise DemoError("A JSON object is required.")
    encode(value)
    return value


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise DemoError("The rehearsal envelope has unexpected or missing fields.")


def validate_input(value: dict) -> None:
    """Reuse the case schema; cross-check references the schema cannot correlate."""
    encode(value)
    keys(value, {"kind", "episode_id", "alert", "receipts", "evidence", "note"})
    if value["kind"] not in {"synthetic", "operator-supplied"}:
        raise DemoError("Evidence provenance must be explicit.")
    episode = canonical_uuid(value["episode_id"])
    evidence = value["evidence"]
    Contracts().validate("servicenow-case", evidence)
    if (
        evidence["episode_id"] != episode or evidence["resource_id"] != RESOURCE
        or evidence["mode"] != "reactive" or evidence["signal_family"] != "filesystem"
        or "case_reference" in evidence or evidence["actions"]
        or evidence["verification"] != {"status": "not_started", "recovery_owner": "none"}
    ):
        raise DemoError("Only a new Linux/reactive/filesystem case without recovery claims is admitted.")
    alert = value["alert"]
    keys(alert, {"episode_id", "id", "resource_id", "mode", "signal_family"})
    if (
        alert["episode_id"] != episode or alert["resource_id"] != RESOURCE
        or alert["mode"] != "reactive" or alert["signal_family"] != "filesystem"
        or not isinstance(alert["id"], str) or not 1 <= len(alert["id"]) <= 2048
        or evidence["source_event_ids"] != [alert["id"]]
    ):
        raise DemoError("Alert and episode references do not match.")
    receipts = value["receipts"]
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 4:
        raise DemoError("One to four terminal Review receipt references are required.")
    references = {}
    for receipt in receipts:
        # These are the existing review_evidence result fields, not a new receipt service.
        keys(receipt, {"request_id", "resource_id", "mode", "command", "status", "receipt_uri"},
             {"observed_at", "fingerprint", "data"})
        request = canonical_uuid(receipt["request_id"])
        uri = "https://" + EVIDENCE_HOST + "/evidence/tool-results/" + request + ".json"
        if (
            receipt["resource_id"].lower() != RESOURCE.lower() or receipt["mode"] != "Review"
            or receipt["command"] not in {"inspect", "telemetry"} or receipt["status"] != "completed"
            or receipt["receipt_uri"] != uri or uri in references
        ):
            raise DemoError("A receipt does not match its exact Review request, target or URI.")
        references[uri] = "GuestInspection" if receipt["command"] == "inspect" else "AzureMonitor"
    used = set()
    for item in evidence["evidence"]:
        reference = item["reference"]
        if reference == alert["id"] and item["source"] == "AzureMonitor":
            continue
        if references.get(reference) != item["source"]:
            raise DemoError("Evidence references do not match the supplied receipts.")
        used.add(reference)
    if used != set(references):
        raise DemoError("Every receipt must be referenced by the exact case evidence.")
    if value["kind"] == "synthetic" and any(
        "synthetic" not in text.lower() or "not real-alert proof" not in text.lower()
        for text in (evidence["summary"], value["note"])
    ):
        raise DemoError("Synthetic input must explicitly disclaim real-alert proof.")
    Contracts().validate("servicenow-note-write", {
        "transport_version": "1.0", "write_request_id": episode, "note": value["note"], "evidence": evidence,
    })


def synthetic() -> dict:
    episode, request = str(uuid4()), str(uuid4())
    uri = "https://" + EVIDENCE_HOST + "/evidence/tool-results/" + request + ".json"
    label = "Synthetic rehearsal; not real-alert proof."
    return {
        "kind": "synthetic", "episode_id": episode,
        "alert": {"episode_id": episode, "id": "synthetic-alert:" + episode,
                  "resource_id": RESOURCE, "mode": "reactive", "signal_family": "filesystem"},
        "receipts": [{"request_id": request, "resource_id": RESOURCE.lower(), "mode": "Review",
                      "command": "inspect", "status": "completed", "receipt_uri": uri}],
        "evidence": {
            "contract_version": "1.0", "episode_id": episode,
            "source_event_ids": ["synthetic-alert:" + episode], "resource_id": RESOURCE,
            "mode": "reactive", "signal_family": "filesystem", "detected_at": "2026-10-18T00:00:00Z",
            "summary": label,
            "evidence": [{"source": "GuestInspection", "observed_at": "2026-10-18T00:00:00Z",
                          "reference": uri, "finding": label}],
            "diagnosis": {"finding": label, "confidence": 0, "gaps": ["No live alert or receipt verified."]},
            "actions": [], "verification": {"status": "not_started", "recovery_owner": "none"},
        },
        "note": label + " Review only; no repair or recovery is asserted.",
    }


def intent(value: dict) -> dict:
    validate_input(value)
    episode = value["episode_id"]
    return {
        "input": copy.deepcopy(value),
        "create": {"transport_version": "1.0", "write_request_id": str(uuid5(UUID(episode), "facade-demo/create")),
                   "evidence": copy.deepcopy(value["evidence"])},
        "note": {"transport_version": "1.0", "write_request_id": str(uuid5(UUID(episode), "facade-demo/note")),
                 "note": value["note"], "evidence": copy.deepcopy(value["evidence"])},
    }


def validate_record(record: dict) -> None:
    keys(record, {"version", "intent", "writes"})
    if record["version"] != 1:
        raise DemoError("Unsupported rehearsal record version.")
    plan = record["intent"]
    keys(plan, {"input", "create", "note"})
    if plan != intent(plan["input"]):
        raise DemoError("The original payloads or stable identities have changed.")
    contracts = Contracts()
    contracts.validate("servicenow-case-write", plan["create"])
    contracts.validate("servicenow-note-write", plan["note"])
    keys(record["writes"], {"create", "note"})
    for name, write in record["writes"].items():
        keys(write, {"status", "receipt"})
        if write["status"] not in STATES:
            raise DemoError("Invalid local write state.")
        receipt = write["receipt"]
        if receipt is None:
            if write["status"] not in {"prepared", "pending"}:
                raise DemoError("A terminal local write is missing its receipt.")
            continue
        contracts.validate("servicenow-adapter-response", receipt, definition="write")
        if (
            receipt["episode_id"] != plan["input"]["episode_id"]
            or receipt["write_request_id"] != plan[name]["write_request_id"]
            or write["status"] != ("applied" if receipt["outcome"] in TERMINAL else
                                   "blocked" if receipt["outcome"] == "not_applied" else "pending")
        ):
            raise DemoError("The local receipt does not match its original write.")
        if name == "note" and receipt["outcome"] in TERMINAL:
            if (
                not re.fullmatch(r"[0-9a-f]{32}", receipt.get("journal_sys_id", ""))
                or record["writes"]["create"]["status"] != "applied"
                or receipt["case_reference"] != record["writes"]["create"]["receipt"]["case_reference"]
            ):
                raise DemoError("The note receipt does not match its owned case.")
    if record["writes"]["note"]["status"] != "prepared" and record["writes"]["create"]["status"] != "applied":
        raise DemoError("A note cannot precede terminal case creation.")


@contextmanager
def locked(path: Path):
    """Concurrent operators fail closed; interrupted lock removal is manual."""
    lock = path.with_name(path.name + ".lock")
    handle = lock.open("x", encoding="utf-8")
    try:
        handle.close()
        yield
    finally:
        lock.unlink()


def persist(path: Path, record: dict, *, first=False) -> None:
    validate_record(record)
    raw = encode(record)
    if first:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + ".tmp")
    # A stale temp file after interruption is deliberately not overwritten.
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def prepare(path: Path, source: Path | None, fixture: bool) -> dict:
    with locked(path):
        if path.exists():
            record = load(path)
            validate_record(record)
            if source is not None and record["intent"] != intent(load(source)):
                raise DemoError("An existing rehearsal cannot be overwritten with different content.")
            if fixture and record["intent"]["input"]["kind"] != "synthetic":
                raise DemoError("An existing rehearsal cannot be replaced with a fixture.")
            return record
        value = synthetic() if fixture else load(source)
        record = {"version": 1, "intent": intent(value),
                  "writes": {name: {"status": "prepared", "receipt": None} for name in ("create", "note")}}
        persist(path, record, first=True)
        return record


def save_receipt(path: Path, record: dict, name: str, receipt: dict) -> None:
    # Never retain untrusted response text in the local non-secret rehearsal.
    allowed = {"outcome", "episode_id", "write_request_id", "case_reference", "journal_sys_id"}
    receipt = {key: value for key, value in receipt.items() if key in allowed}
    if receipt["outcome"] == "unknown":
        receipt["message"] = "Reconcile the original write."
    if receipt["outcome"] == "not_applied":
        receipt["error_code"] = "facade_not_applied"
    record["writes"][name] = {
        "status": "applied" if receipt["outcome"] in TERMINAL else
                  "blocked" if receipt["outcome"] == "not_applied" else "pending",
        "receipt": receipt,
    }
    persist(path, record)


def send(path: Path, client: CaseClient) -> dict:
    with locked(path):
        record = load(path)
        validate_record(record)
        if any(write["status"] in {"pending", "blocked"} for write in record["writes"].values()):
            raise DemoError("Reconcile the original write; no automatic repost is permitted.")
        plan = record["intent"]
        episode = plan["input"]["episode_id"]
        for name in ("create", "note"):
            if record["writes"][name]["status"] == "applied":
                continue
            case = None
            if name == "note":
                case = client.read_case(episode, RESOURCE, record["writes"]["create"]["receipt"]["case_reference"])
            record["writes"][name]["status"] = "pending"
            persist(path, record)  # Must precede POST, including process interruption.
            receipt = client.create_case(plan[name]) if name == "create" else client.append_note(case, plan[name])
            save_receipt(path, record, name, receipt)
            if record["writes"][name]["status"] != "applied":
                raise DemoError("The write is not confirmed; reconcile its original identity.")
        return record


def reconcile(path: Path, client: CaseClient, name: str) -> dict:
    with locked(path):
        record = load(path)
        validate_record(record)
        if record["writes"][name]["status"] == "prepared":
            raise DemoError("This write has not been attempted.")
        plan = record["intent"]
        episode = plan["input"]["episode_id"]
        receipt = client.reconcile(episode, plan[name]["write_request_id"])
        if receipt["outcome"] in TERMINAL:
            reference = receipt["case_reference"]
            if name == "note" and reference != record["writes"]["create"]["receipt"]["case_reference"]:
                raise DemoError("Reconciled note belongs to another case.")
            client.read_case(episode, RESOURCE, reference)
        save_receipt(path, record, name, receipt)
        return record


def summary(action: str, record: dict) -> dict:
    plan = record["intent"]
    return {
        "action": action, "status": "checked" if action in {"prepare", "check"} else
        "complete" if all(w["status"] == "applied" for w in record["writes"].values()) else "incomplete",
        "kind": plan["input"]["kind"], "episode_id": plan["input"]["episode_id"],
        "create_write_id": plan["create"]["write_request_id"], "note_write_id": plan["note"]["write_request_id"],
        "writes": {name: write["status"] for name, write in record["writes"].items()},
        "evidence_proof": "not verified offline", "independent_journal_verified": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "readiness", "send", "reconcile"))
    parser.add_argument("--state", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--client-id", default=CLIENT_ID)
    parser.add_argument("--secret-stdin", action="store_true")
    parser.add_argument("--confirm-new-case", action="store_true")
    parser.add_argument("--write", choices=("create", "note"))
    args = parser.parse_args(argv)
    try:
        if args.action != "readiness" and args.state is None:
            raise DemoError("A local rehearsal state path is required.")
        if args.action != "prepare" and (args.input is not None or args.synthetic):
            raise DemoError("Input selection is available only for prepare.")
        if args.action == "prepare":
            if (args.input is not None) == args.synthetic:
                raise DemoError("Choose exactly one input file or synthetic fixture.")
            record = prepare(args.state, args.input, args.synthetic)
        elif args.action == "check":
            record = load(args.state)
            validate_record(record)
        else:
            if not args.secret_stdin or sys.stdin.isatty():
                raise DemoError("Live actions require the bounded credential stdin pipe.")
            if args.action == "send" and not args.confirm_new_case:
                raise DemoError("Explicit new-case live intent is required.")
            if args.action == "reconcile" and args.write is None:
                raise DemoError("Select the original create or note identity.")
            raw = sys.stdin.buffer.read(4097)
            secret = raw.decode("utf-8")
            if not raw or len(raw) > 4096 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in secret):
                raise DemoError("The credential pipe is empty or invalid.")
            credentials = ClientCredentials(lambda: secret, client_id=args.client_id)
            client = CaseClient(credentials.token)
            if args.action == "readiness":
                client.health()
                print(json.dumps({"action": "readiness", "status": "ready",
                                  "boundary": "facade health only; not write or evidence acceptance"}))
                return 0
            record = send(args.state, client) if args.action == "send" else reconcile(args.state, client, args.write)
        result = summary(args.action, record)
        print(json.dumps(result))
        return 0 if args.action != "reconcile" or record["writes"][args.write]["status"] == "applied" else 2
    except (Exception, KeyboardInterrupt):
        # No input, secret, remote body, traceback or exception text is diagnostic output.
        print('{"status":"not_ready","error":"Facade command not confirmed; check local input/state or reconcile the original write. No automatic retry."}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
