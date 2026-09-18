"""Bounded per-surface state transitions with a durable at-least-once outbox."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import threading
import uuid

from .contracts import SURFACES, domain_for
from . import events
from .transport import DeliveryError, StoreConflict, StoreUnavailable

LOG = logging.getLogger("energy_domains_v2")


class CommandConflict(ValueError):
    pass


class Backpressure(ValueError):
    pass


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Runtime:
    def __init__(self, store, sender, adapters, *, interval_seconds=10,
                 daily_bytes=100 * 1024 * 1024, pending_bytes=4 * 1024 * 1024,
                 clock=None, delivery_mode="local_capture"):
        self.store, self.sender, self.adapters = store, sender, adapters
        self.interval = interval_seconds
        self.daily_limit, self.pending_limit = daily_bytes, pending_bytes
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.delivery_mode = delivery_mode
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self.worker_error = None
        self.smoke_error = None

    def adapter(self, surface):
        if surface not in SURFACES:
            raise ValueError("Unknown surface")
        return self.adapters["inventory" if surface.endswith("-inventory") else "monitoring"]

    def _initial(self, surface):
        adapter = self.adapter(surface)
        model = adapter.initial_model(surface, 42)
        at = self.clock().isoformat()
        frame = adapter.frame(surface, model, 0, at)
        return {
            "version": 1, "surface": surface, "model": frame["model"],
            "snapshot": frame["snapshot"], "running": False, "run_id": None,
            "started_at": None, "deadline": None, "next_at": None,
            "step": 0, "sequence": 0, "pending": [], "accepted_records": 0,
            "commands": {}, "last_error": None, "last_command_at": 0.0,
            "day": at[:10], "daily_bytes": 0,
        }

    def _load(self, surface):
        self.adapter(surface)
        document, version = self.store.read(surface)
        if document is not None:
            if document.get("version") != 1 or document.get("surface") != surface:
                raise ValueError("Stored V2 state has an incompatible contract")
            return document, version
        return self._initial(surface), None

    def _change(self, surface, transform):
        with self._lock:
            for attempt in range(3):
                state, version = self._load(surface)
                updated = deepcopy(state)
                result = transform(updated)
                try:
                    if version is None or updated != state:
                        self.store.write(surface, updated, version)
                    return result
                except StoreConflict:
                    if attempt == 2:
                        raise
        raise StoreConflict("State transition conflict")

    def snapshot(self, surface):
        return deepcopy(self._load(surface)[0]["snapshot"])

    def status(self, surface):
        state, _ = self._load(surface)
        pending = state["pending"]
        pending_error = next((f"Log delivery failed: {batch['error']}"
                              for batch in pending if batch["error"]), None)
        worker_error = self.smoke_error or self.worker_error
        if self._thread is not None and not self.worker_alive:
            worker_error = "WorkerStopped"
        return {
            "surface": surface, "domain": domain_for(surface), "running": state["running"],
            "run_id": state["run_id"], "started_at": state["started_at"],
            "deadline": state["deadline"], "steps": state["step"],
            "scenario": state["model"].get("scenario", "normal"),
            "delivery_mode": self.delivery_mode,
            "accepted_records": state["accepted_records"],
            "pending_records": sum(len(batch["records"]) for batch in pending),
            "parked_batches": sum(batch["next_attempt"] is None for batch in pending),
            "last_error": state["last_error"] or pending_error, "worker_error": worker_error,
            "queryability": "not_checked", "daily_bytes": state["daily_bytes"],
            "daily_limit_bytes": self.daily_limit,
            "scenarios": self.adapter(surface).scenarios(surface),
        }

    def _append(self, state, table, records, now):
        new = events.batches(table, records)
        day = now.date().isoformat()
        if state["day"] != day:
            state["day"], state["daily_bytes"] = day, 0
        size = sum(len(events.encoded(batch["records"])) for batch in new)
        pending_size = sum(len(events.encoded(batch["records"])) for batch in state["pending"])
        reserve = 16_000 if table == events.AUDIT else 0
        if size + pending_size > self.pending_limit + reserve:
            raise Backpressure("Delivery backlog full; retry delivery before adding observations")
        if state["daily_bytes"] + size > self.daily_limit + reserve:
            raise Backpressure("Daily synthetic-log generation limit reached")
        state["daily_bytes"] += size
        state["pending"].extend(new)

    def _frame(self, state, now):
        at = now.isoformat()
        frame = self.adapter(state["surface"]).frame(
            state["surface"], deepcopy(state["model"]), state["step"], at)
        records = events.observations(frame, state, domain_for(state["surface"]), at)
        self._append(state, events.OBSERVATIONS, records, now)
        state["model"], state["snapshot"] = frame["model"], frame["snapshot"]
        state["step"] += 1
        state["sequence"] += 1
        state["last_error"] = None
        state["next_at"] = (now + timedelta(seconds=self.interval)).isoformat()

    def _audit(self, state, action, scenario, now):
        row = events.audit(state, domain_for(state["surface"]), action, scenario, now.isoformat())
        self._append(state, events.AUDIT, [row], now)
        state["sequence"] += 1

    def command(self, surface, action, scenario=None, key=None, duration_seconds=900, seed=42):
        self.adapter(surface)
        if not isinstance(action, str) or action not in {"start", "stop", "inject", "recover", "reset", "retry_delivery"}:
            raise ValueError("Unsupported command")
        if not isinstance(key, str):
            raise ValueError("A UUID Idempotency-Key is required")
        try:
            key = str(uuid.UUID(key))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Idempotency-Key must be a UUID") from exc
        if type(seed) is not int or not 0 <= seed <= 9007199254740991:
            raise ValueError("Seed must be a nonnegative safe integer")
        if type(duration_seconds) is not int or not 10 <= duration_seconds <= 1800:
            raise ValueError("Run duration must be between 10 and 1800 seconds")
        if action == "inject" and (not isinstance(scenario, str) or scenario not in {
                s["id"] for s in self.adapter(surface).scenarios(surface)}):
            raise ValueError("Unsupported scenario for this surface")
        if action != "inject" and scenario is not None:
            raise ValueError("Only inject accepts a scenario")
        fingerprint = hashlib.sha256(events.encoded([action, scenario, duration_seconds, seed])).hexdigest()
        now, new_run = self.clock(), str(uuid.uuid4())

        def update(state):
            prior = state["commands"].get(key)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise CommandConflict("Idempotency key was already used for another command")
                return prior["result"]
            if action not in {"stop", "retry_delivery"} and now.timestamp() - state["last_command_at"] < 1:
                raise CommandConflict("Wait one second between state-changing commands")
            audit_needed = True
            if action == "start":
                if state["running"]:
                    raise CommandConflict("A run is already active for this surface")
                state.update(run_id=new_run, running=True, started_at=now.isoformat(),
                             deadline=(now + timedelta(seconds=duration_seconds)).isoformat(),
                             step=0, sequence=0, model=self.adapter(surface).initial_model(surface, seed))
                self._frame(state, now)
            elif action == "stop":
                audit_needed = state["running"]
                state["running"] = False
            elif action == "retry_delivery":
                # Recovery must not require capacity in the delivery queue it unblocks.
                # The command remains durable in the idempotency journal below.
                audit_needed = False
                for batch in state["pending"]:
                    batch.update(attempts=0, next_attempt=0.0, error=None)
                state["last_error"] = None
            else:
                if action == "inject" and not state["running"]:
                    raise CommandConflict("Start a bounded run before injecting an anomaly")
                if state["run_id"] is None:
                    state["run_id"] = new_run
                state["model"] = self.adapter(surface).apply_action(
                    surface, deepcopy(state["model"]), action, scenario)
                self._frame(state, now)
            if state["run_id"] is None:
                state["run_id"] = new_run
            if audit_needed:
                self._audit(state, action, scenario, now)
            state["last_command_at"] = now.timestamp()
            result = {"accepted": True, "run_id": state["run_id"], "action": action,
                      "surface": surface, "delivery": "durably_queued" if action not in {"stop", "retry_delivery"} else "control_applied"}
            state["commands"][key] = {
                "fingerprint": fingerprint, "result": result, "accepted_at": now.timestamp(),
            }
            while len(state["commands"]) > 256:
                oldest = min(state["commands"], key=lambda name: (
                    state["commands"][name].get("accepted_at", 0), name))
                del state["commands"][oldest]
            return result

        return self._change(surface, update)

    def _tick(self, surface, now):
        def update(state):
            if not state["running"]:
                return
            if now >= _date(state["deadline"]):
                state["running"] = False
                self._audit(state, "complete", None, now)
                return
            if state["next_at"] is None or now >= _date(state["next_at"]):
                self._frame(state, now)
        self._change(surface, update)

    def _deliver(self, surface, now):
        state, _ = self._load(surface)
        batch = next((b for b in state["pending"]
                      if b["next_attempt"] is not None and b["next_attempt"] <= now.timestamp()), None)
        if batch is None:
            return
        failure = None
        try:
            self.sender.send(batch["table"], batch["records"])
        except DeliveryError as error:
            failure = error

        def acknowledge(current):
            target = next((b for b in current["pending"] if b["id"] == batch["id"]), None)
            if target is None:
                return
            if failure is None:
                current["accepted_records"] += len(target["records"])
                current["pending"].remove(target)
                current["last_error"] = None
            else:
                target["attempts"] += 1
                target["error"] = failure.code
                delay = max(failure.retry_after or 0, min(300, 2 ** target["attempts"]))
                target["next_attempt"] = (now.timestamp() + delay
                                          if failure.retryable and target["attempts"] < 5 else None)
                current["last_error"] = f"Log delivery failed: {failure.code}"
        self._change(surface, acknowledge)

    def cycle(self):
        keys = self.store.keys()
        self.worker_error = None
        for surface in keys:
            if surface not in SURFACES:
                continue
            now = self.clock()
            try:
                self._deliver(surface, now)
                self._tick(surface, now)
            except Backpressure as error:
                message = str(error)
                self._change(surface, lambda state: state.update(last_error=message, running=False))
            except (StoreUnavailable, StoreConflict) as error:
                self.worker_error = type(error).__name__
                LOG.warning("V2 durable store unavailable for %s: %s", surface, type(error).__name__)

    def start_worker(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._work, name="v2-outbox", daemon=True)
        self._thread.start()

    def _work(self):
        while not self._stop.is_set():
            try:
                self.cycle()
            except StoreUnavailable:
                self.worker_error = "StoreUnavailable"
                LOG.warning("V2 state discovery unavailable; retrying without discarding state")
            self._stop.wait(1)

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def worker_alive(self):
        return self._thread is not None and self._thread.is_alive()
