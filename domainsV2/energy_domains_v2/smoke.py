"""Explicitly enabled, bounded and restart-idempotent deployment exercise."""

import hashlib
import logging
import threading
import time
import uuid

from .contracts import SURFACES
from .transport import StoreConflict, StoreUnavailable

LOG = logging.getLogger("energy_domains_v2")


def start_smoke(runtime, token):
    marker = "smoke-" + hashlib.sha256(token.encode()).hexdigest()[:24]

    def command(surface, action, scenario=None):
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"v2:{token}:{surface}:{action}:{scenario}"))
        return runtime.command(surface, action, scenario, key, 180, 42)

    def exercise():
        try:
            for attempt in range(30):
                try:
                    existing, version = runtime.store.read(marker)
                    break
                except StoreUnavailable:
                    if attempt == 29:
                        raise
                    time.sleep(10)
            if existing and existing.get("status") == "completed":
                return
            for surface in SURFACES:
                command(surface, "start")
            time.sleep(12)
            for choice in range(3):
                for surface in SURFACES:
                    options = runtime.adapter(surface).scenarios(surface)
                    command(surface, "inject", options[choice]["id"])
                time.sleep(12)
            for surface in SURFACES:
                command(surface, "recover")
                command(surface, "stop")
            runtime.store.write(marker, {"status": "completed", "surfaces": list(SURFACES)}, version)
            LOG.warning("V2 bounded deployment smoke commands completed; delivery remains asynchronous")
        except (ValueError, StoreUnavailable, StoreConflict) as error:
            LOG.error("V2 deployment smoke failed: %s", type(error).__name__)
            runtime.smoke_error = "DeploymentSmokeFailed"
            for surface in SURFACES:
                try:
                    command(surface, "stop")
                except (ValueError, StoreUnavailable, StoreConflict):
                    LOG.error("V2 smoke stop could not be persisted for %s; run deadline remains bounded", surface)
    threading.Thread(target=exercise, name="v2-deploy-smoke", daemon=True).start()
