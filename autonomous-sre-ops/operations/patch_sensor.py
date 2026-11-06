"""Queue-only latest-summary sensor. The parent owns a hard lifetime budget."""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from datetime import datetime, timezone

from operations import patch_provider as provider

DEADLINE_SECONDS = 20
STATUS_MARKER = "ASRE_PATCH_SENSOR_STATUS "


def sense(credential, clock=lambda: datetime.now(timezone.utc)):
    value = provider.snapshot(provider.read_compute(credential), clock())
    if not provider.qualifying(value):
        return {"status": "no_qualifying_failure"}
    digest = provider.archive(credential, value)
    return {"status": "archived", "event": provider.event(value, digest)}


def _child(connection):
    # No provider body, token, SDK log or traceback reaches the console.
    logging.disable(logging.CRITICAL)
    from azure.core.exceptions import AzureError
    from azure.identity import ManagedIdentityCredential

    try:
        client_id = provider.guid(os.environ["ASRE_COLLECTOR_CLIENT_ID"])
        with ManagedIdentityCredential(client_id=client_id, retry_total=0,
                                       connection_timeout=5, read_timeout=5) as credential:
            result = sense(credential)
    except (provider.ProviderError, AzureError, OSError, KeyError, TypeError):
        result = {"status": "failed", "reason": "provider_or_archive_unavailable"}
    except Exception:
        # A child-boundary failure is explicit and redacted, never an SDK
        # traceback/body/token or a success-shaped empty observation.
        result = {"status": "failed", "reason": "unexpected_sensor_failure"}
    try:
        connection.send_bytes(provider.encode(result, provider.LOG_LIMIT))
    finally:
        connection.close()


def run():
    """A stuck token/data call cannot consume the original queue allowance.

    Spawn (not fork) avoids sharing the parent's Azure clients. One bounded
    message is read only after child exit; no unbounded stdout capture/threads.
    """
    deadline = time.monotonic() + DEADLINE_SECONDS
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_child, args=(sender,), daemon=True)
    result = {"status": "failed", "reason": "sensor_start_or_protocol"}
    try:
        child.start()
        sender.close()
        child.join(max(0, deadline - time.monotonic()))
        if child.is_alive():
            child.kill()
            child.join()
            result = {"status": "failed", "reason": "deadline_exceeded"}
        elif child.exitcode == 0 and receiver.poll():
            result = provider.decode(receiver.recv_bytes(provider.LOG_LIMIT), provider.LOG_LIMIT)
            if result == {"status": "no_qualifying_failure"}:
                pass
            elif set(result) == {"status", "event"} and result["status"] == "archived":
                provider.validate_event(result["event"], datetime.now(timezone.utc))
                print(provider.MARKER + provider.encode(result["event"]).decode("ascii"), flush=True)
                result = {"status": "emitted"}
            elif (set(result) != {"status", "reason"} or result["status"] != "failed"
                  or result["reason"] not in {"provider_or_archive_unavailable", "unexpected_sensor_failure"}):
                raise provider.ProviderError("Invalid sensor protocol.")
    except (provider.ProviderError, OSError, EOFError, ValueError, RuntimeError):
        result = {"status": "failed", "reason": "sensor_start_or_protocol"}
    finally:
        if child.pid is not None:
            if child.is_alive():
                child.kill()
                child.join()
            child.close()
        receiver.close()
        sender.close()
    print(STATUS_MARKER + provider.encode(result, provider.LOG_LIMIT).decode("ascii"), flush=True)
