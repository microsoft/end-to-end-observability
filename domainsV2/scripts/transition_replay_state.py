"""Offline operator transition; stop ALL application writers before --apply.

Archives are immutable JSON-value copies, not raw blob-byte backups. No runtime
reset, deletion, permission changes, or delivery occurs. Cross-blob checks are
not a transaction: the operator must keep every writer stopped until deployment
of the matching replay. An interrupted archive-only operation can be rerun.
"""

from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from energy_domains_v2.contracts import SURFACES
from energy_domains_v2.demo_runtime import DemoRuntime, STATE_KEY
from energy_domains_v2.transport import BlobStateStore, StoreConflict, StoreUnavailable


ARCHIVE_PREFIX = "replay-archive-"
ACCOUNTING = (
    "day", "daily_bytes", "accepted_records", "generated_records",
    "surface_accepted", "surface_generated", "sequence", "command_sequence",
    "commands", "retry_generation",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def archive_key(old, new):
    return ARCHIVE_PREFIX + digest([STATE_KEY, old, new])


def drained(state, *, legacy=False):
    if not isinstance(state, dict):
        raise ValueError("Missing or invalid state")
    if state.get("running") is not False:
        raise ValueError("All demo and legacy writers must be stopped")
    if not legacy and state.get("phase") not in {"stopped", "idle"}:
        raise ValueError("Demo phase must be stopped or idle")
    if state.get("pending") != []:
        raise ValueError("Pending, parked, or unknown outbox cannot be transitioned")


def snapshot(store):
    keys = store.keys()
    result = {}
    for key in keys:
        if key == STATE_KEY or key.startswith(ARCHIVE_PREFIX):
            continue
        state, etag = store.read(key)
        # Retain the exact completed marker shape written by the retired
        # deployment exercise, and include its ETag in the concurrency check.
        if (re.fullmatch(r"smoke-[0-9a-f]{24}", key)
                and state == {"status": "completed", "surfaces": list(SURFACES)}):
            result[key] = (state, etag)
            continue
        if key not in SURFACES:
            raise ValueError("Unrecognized state key; operator review required")
        drained(state, legacy=True)
        result[key] = (state, etag)
    return keys, result


def transition(store, replay, expected_old_version, *, apply=False, writers_stopped=False):
    """Return a sanitized plan/result. Never retry a conflicting live-state CAS."""
    if not expected_old_version:
        raise ValueError("Expected old replay version is required")
    if apply and not writers_stopped:
        raise ValueError("Apply requires explicit confirmation that ALL writers are stopped")
    runtime = DemoRuntime(store, None, {}, replay=replay, readiness=None)
    new_version = runtime.fixture_version
    if expected_old_version == new_version:
        raise ValueError("Old and target replay versions must differ")
    key = archive_key(expected_old_version, new_version)
    state, etag = store.read(STATE_KEY)
    drained(state)
    if state.get("version") != 2 or not etag:
        raise ValueError("Only existing version-2 demo state can be transitioned")
    keys, legacy = snapshot(store)
    archive, archive_etag = store.read(key)
    marker = state.get("replay_transition")
    if state.get("replay_version") == new_version:
        if (not isinstance(archive, dict) or not archive_etag
                or archive.get("source_state", {}).get("replay_version") != expected_old_version
                or archive.get("target_replay_version") != new_version
                or archive.get("source_sha256") != digest(archive.get("source_state"))
                or marker != {"archive_key": key, "archive_sha256": digest(archive)}):
            raise ValueError("Target replay exists without matching verified transition archive")
        return {"status": "already_applied", "archive_key": key,
                "old_replay_version": expected_old_version, "new_replay_version": new_version,
                "source_etag": archive["source_etag"], "current_etag": etag}
    if state.get("replay_version") != expected_old_version:
        raise ValueError("Expected old replay version does not match stored state")
    expected_archive = {
        "archive_format": 1, "source_key": STATE_KEY, "source_etag": etag,
        "source_sha256": digest(state), "source_state": deepcopy(state),
        "target_replay_version": new_version,
    }
    if archive is not None and archive != expected_archive:
        raise StoreConflict("ExistingArchiveConflict")
    replacement = runtime._initial()
    for name in ACCOUNTING:
        if name in state:
            replacement[name] = deepcopy(state[name])
    replacement["replay_transition"] = {"archive_key": key,
                                       "archive_sha256": digest(expected_archive)}
    result = {"status": "dry_run", "archive_key": key, "source_etag": etag,
              "source_sha256": digest(state), "old_replay_version": expected_old_version,
              "new_replay_version": new_version, "pending_records": 0,
              "preserved_fields": list(ACCOUNTING)}
    if not apply:
        return result
    if archive is None:
        archive_etag = store.write(key, expected_archive, None)
    verified, verified_etag = store.read(key)
    if verified != expected_archive or verified_etag != archive_etag:
        raise StoreConflict("ArchiveVerificationFailed")
    # Recheck the entire legacy key set and ETags, including absent surface keys.
    final_keys, final_legacy = snapshot(store)
    if set(final_keys) != set(keys) | {key} or final_legacy != legacy:
        raise StoreConflict("LegacyStateChanged")
    current, current_etag = store.read(STATE_KEY)
    if current != state or current_etag != etag:
        raise StoreConflict("SourceStateChanged")
    replacement_etag = store.write(STATE_KEY, replacement, etag)
    verified, verified_etag = store.read(STATE_KEY)
    if verified != replacement or verified_etag != replacement_etag:
        raise StoreConflict("ReplacementVerificationFailed")
    result.update(status="applied", current_etag=replacement_etag)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-old-version", required=True)
    parser.add_argument("--apply", action="store_true", help="Default is read-only dry run")
    parser.add_argument("--writers-stopped", action="store_true",
                        help="Confirm ALL app/worker writers are stopped until matching deployment")
    args = parser.parse_args(argv)
    try:
        from energy_domains_v2 import replay
        store = BlobStateStore(os.environ["V2_STORAGE_ACCOUNT_URL"],
                               os.environ["V2_STORAGE_CONTAINER"])
        result = transition(store, replay, args.expected_old_version,
                            apply=args.apply, writers_stopped=args.writers_stopped)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, KeyError, StoreConflict, StoreUnavailable) as error:
        # Never render SDK exception bodies, credentials, or environment values.
        print(json.dumps({"status": "refused", "error": type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
