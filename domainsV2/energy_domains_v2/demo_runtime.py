"""One bounded demo session, compact replay state and at-least-once delivery.

Status extras: ``baseline_readiness`` retains the first confirmed baseline evidence;
``readiness`` is the latest check, including subsequent failures. Global
``accepted_records`` includes observation and audit rows; ``generated_records``
counts only durably queued observations across sessions. Surface counts refer to
that surface. ``published_at`` identifies the last durably queued model, not an
ingestion acknowledgement or proof of queryability. ``legacy_pending_records``
reports preserved legacy backlog discovered by the worker.
``legacy_scan_complete`` distinguishes an unknown startup backlog from zero;
``prepare_blocked`` remains true until discovery and legacy delivery finish.
``access_probe`` is process-local evidence from the checker's fixed permission
probe: success is cached until restart, failures retry at most once per minute,
and local capture reports LocalOnly without claiming Azure permissions.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
import threading
import uuid

from . import events
from .contracts import SURFACES, domain_for
from .runtime import Backpressure, CommandConflict, _date
from .transport import DeliveryError, StoreConflict, StoreUnavailable

LOG = logging.getLogger("energy_domains_v2")
STATE_KEY = "demo-session-v2"


class DemoRuntime:
    def __init__(self, store, sender, adapters, *, replay, readiness, clock=None,
                 delivery_mode="local_capture", interval_seconds=60, warmup_seconds=600):
        if type(interval_seconds) is not int or interval_seconds != 60:
            raise ValueError("Demo cadence must be 60 seconds")
        if type(warmup_seconds) is not int or warmup_seconds != 600:
            raise ValueError("Demo baseline requires 600 seconds")
        self.store, self.sender, self.adapters = store, sender, adapters
        self.replay, self.readiness = replay, readiness
        self.fixture_version = replay.initial_model(SURFACES[0], 42)["fixture_version"]
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.delivery_mode = delivery_mode
        self.interval, self.warmup = interval_seconds, warmup_seconds
        self.pending_limit, self.daily_limit = 4 * 1024 * 1024, 100 * 1024 * 1024
        self._lock, self._cycle_lock = threading.RLock(), threading.Lock()
        self._stop, self._thread = threading.Event(), None
        self.worker_error = None
        self._legacy = None
        self._legacy_scanned = False
        self._legacy_status = {}
        self._legacy_due = {}
        self._legacy_retry = -1
        self._access_result = {"verified": False, "observations_allowed": False,
                               "audit_denied": False, "code": "NotChecked", "checked_at": None}
        self._access_next = None
        self._access_complete = False

    def adapter(self, surface):
        if not isinstance(surface, str) or surface not in SURFACES:
            raise ValueError("Unknown surface")
        return self.adapters["inventory" if surface.endswith("-inventory") else "monitoring"]

    def _initial(self):
        return {
            "version": 2, "replay_version": self.fixture_version,
            "session_id": None, "phase": "idle", "running": False,
            "selected_surfaces": [], "started_at": None, "warmup_until": None,
            "deadline": None, "next_at": None, "models": {}, "published": {}, "published_at": {},
            "published_envelopes": {},
            "surface_steps": {}, "surface_accepted": {}, "surface_generated": {}, "anomalies": {},
            "ready": False, "readiness": {"ready": False, "status": "pending",
                "checked_at": None, "surfaces": []},
            "baseline_readiness": None, "next_check": None, "pending": [],
            "commands": {}, "command_sequence": 0, "sequence": 0,
            "accepted_records": 0, "generated_records": 0, "last_error": None,
            "delivery_error": None, "day": None, "daily_bytes": 0, "retry_generation": 0,
        }

    def _load(self):
        state, version = self.store.read(STATE_KEY)
        if state is None:
            return self._initial(), None
        if state.get("version") != 2 or state.get("replay_version") != self.fixture_version:
            raise ValueError("Stored demo replay version is incompatible; no session was reset")
        return state, version

    def _change(self, transform):
        with self._lock:
            for attempt in range(3):
                state, version = self._load()
                updated = deepcopy(state)
                result = transform(updated)
                try:
                    if updated != state:
                        def metadata_size():
                            return len(events.encoded({k: v for k, v in updated.items() if k != "pending"}))
                        while metadata_size() >= 100_000 and len(updated["commands"]) > 1:
                            oldest = min(updated["commands"], key=lambda k: updated["commands"][k]["order"])
                            del updated["commands"][oldest]
                        if metadata_size() >= 100_000:
                            raise ValueError("Demo metadata exceeds its compact state bound")
                        self.store.write(STATE_KEY, updated, version)
                    return result
                except StoreConflict:
                    if attempt == 2:
                        raise

    @staticmethod
    def _key(key):
        if not isinstance(key, str):
            raise ValueError("A canonical UUID Idempotency-Key is required")
        try:
            if str(uuid.UUID(key)) != key:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Idempotency-Key must be a canonical UUID") from None
        return key

    def _execute(self, key, payload, transform):
        key = self._key(key)
        fingerprint = hashlib.sha256(events.encoded(payload)).hexdigest()

        def update(state):
            prior = state["commands"].get(key)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise CommandConflict("Idempotency key was already used for another command")
                return deepcopy(prior["result"])
            result = transform(state)
            state["command_sequence"] += 1
            state["commands"][key] = {"fingerprint": fingerprint, "result": result,
                                      "order": state["command_sequence"]}
            while len(state["commands"]) > 128:
                del state["commands"][min(state["commands"], key=lambda k: state["commands"][k]["order"])]
            return result

        return self._change(update)

    def _append(self, state, table, records, now, *, control=False):
        batches = events.batches(table, records)
        size = sum(len(events.encoded(b["records"])) for b in batches)
        pending_size = len(events.encoded(state["pending"] + batches))
        # Reserve growth for each batch's sanitized failure code and retry timestamp.
        pending_size += 96 * (len(state["pending"]) + len(batches))
        if state["day"] != now.date().isoformat():
            state["day"], state["daily_bytes"] = now.date().isoformat(), 0
        # A single stop audit uses reserved control capacity, never generation capacity.
        if not control:
            if pending_size > self.pending_limit - 4096:
                raise Backpressure("Delivery backlog full; generation paused until delivery recovers")
            if state["daily_bytes"] + size > self.daily_limit:
                raise Backpressure("Daily synthetic-log generation limit reached")
        state["daily_bytes"] += size
        state["pending"].extend(batches)

    def _audit(self, state, action, scenario, now, surface=None, *, control=False):
        envelope = {"run_id": state["session_id"], "sequence": state["sequence"],
                    "surface": surface or "demo-session"}
        row = events.audit(envelope, domain_for(surface) if surface else "all",
                           action, scenario, now.isoformat())
        self._append(state, events.AUDIT, [row], now, control=control)
        state["sequence"] += 1

    def _expire(self, state, now):
        if state["running"] and now >= _date(state["deadline"]):
            state.update(running=False, phase="stopped", next_at=None)
            self._audit(state, "complete", None, now, control=True)

    def session_command(self, action, *, key, surfaces=None, duration_seconds=7200):
        if not isinstance(action, str) or action not in {"prepare", "stop", "retry_delivery"}:
            raise ValueError("Unsupported demo session command")
        if type(duration_seconds) is not int or not 600 <= duration_seconds <= 7200:
            raise ValueError("Session duration must be between 600 and 7200 seconds, including warmup")
        if surfaces is not None and action != "prepare":
            raise ValueError("Only prepare accepts surfaces")
        selected = list(SURFACES) if surfaces is None else surfaces
        if (not isinstance(selected, list) or not selected
                or any(not isinstance(s, str) or s not in SURFACES for s in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError("Select unique supported surfaces")
        selected = [s for s in SURFACES if s in selected]
        new_id = str(uuid.uuid4())

        def update(state):
            now = self.clock()
            self._expire(state, now)
            if action == "prepare":
                if state["running"]:
                    raise CommandConflict("Stop the current demo before preparing another")
                if not self._legacy_scanned:
                    raise CommandConflict("Wait for startup legacy backlog discovery before preparing a demo")
                if self._legacy:
                    raise CommandConflict("Drain preserved legacy delivery backlog before preparing a demo; retry delivery if parked")
                models = {s: self.replay.initial_model(s, 42) for s in selected}
                state.update(session_id=new_id, running=True, phase="warming",
                    selected_surfaces=selected, started_at=now.isoformat(),
                    warmup_until=(now + timedelta(seconds=self.warmup)).isoformat(),
                    deadline=(now + timedelta(seconds=duration_seconds)).isoformat(),
                    next_at=now.isoformat(), models=models,
                    surface_steps={s: 0 for s in selected}, anomalies={}, ready=False,
                    readiness={"ready": False, "status": "pending", "checked_at": None,
                               "surfaces": []}, baseline_readiness=None,
                    next_check=None, last_error=None)
                self._audit(state, action, None, now)
            elif action == "stop":
                if state["running"]:
                    state.update(running=False, phase="stopped", next_at=None)
                    self._audit(state, action, None, now, control=True)
            else:
                for batch in state["pending"]:
                    batch.update(attempts=0, next_attempt=0.0, error=None)
                state.update(delivery_error=None, last_error=None,
                             retry_generation=state["retry_generation"] + 1)
            return {"accepted": True, "session_id": state["session_id"],
                    "run_id": state["session_id"], "action": action, "delivery": "control_applied"}

        return self._execute(key, ["session", action, selected, duration_seconds], update)

    def command(self, surface, action, scenario=None, key=None, duration_seconds=300, seed=42):
        self.adapter(surface)
        if not isinstance(action, str) or action not in {"inject", "recover", "reset"}:
            raise ValueError("Use global Prepare and Stop; surface commands are inject, recover or reset")
        if type(seed) is not int or seed != 42:
            raise ValueError("Replay supports only seed 42")
        if type(duration_seconds) is not int or not 60 <= duration_seconds <= 900:
            raise ValueError("Anomaly duration must be between 60 and 900 seconds")
        if action == "inject":
            if not isinstance(scenario, str) or scenario not in {
                    item["id"] for item in self.replay.scenarios(surface)}:
                raise ValueError("Unsupported scenario for this surface")
        elif scenario is not None:
            raise ValueError("Only inject accepts a scenario")
        def update(state):
            now = self.clock()
            if not state["running"] or now >= _date(state["deadline"]):
                raise CommandConflict("Prepare an active demo session first")
            if surface not in state["selected_surfaces"]:
                raise CommandConflict("Surface is not selected in this session")
            if action == "inject" and (now < _date(state["warmup_until"]) or not state["ready"]):
                raise CommandConflict("Wait for warmup and confirmed queryable baseline readiness")
            changed = action in {"inject", "reset"} or surface in state["anomalies"]
            if changed:
                state["models"][surface] = self.replay.apply_action(
                    surface, deepcopy(state["models"][surface]), action, scenario)
                if action == "inject":
                    until = min(_date(state["deadline"]), now + timedelta(seconds=duration_seconds))
                    state["anomalies"][surface] = {"scenario": scenario, "until": until.isoformat()}
                else:
                    state["anomalies"].pop(surface, None)
                self._audit(state, action, scenario, now, surface)
            return {"accepted": True, "session_id": state["session_id"],
                    "run_id": state["session_id"], "surface": surface,
                    "action": action, "delivery": "durably_queued"}

        return self._execute(key, [surface, action, scenario, duration_seconds, seed], update)

    def snapshot(self, surface):
        self.adapter(surface)
        state, _ = self._load()
        model = state["published"].get(surface)
        if model is None:
            model = self.replay.initial_model(surface, 42)
        return self.replay.snapshot(surface, deepcopy(model))

    def log_details(self, surface):
        self.adapter(surface)
        state, _ = self._load()
        model = state["published"].get(surface)
        envelope = state.get("published_envelopes", {}).get(surface)
        if model is None:
            model = self.replay.initial_model(surface, 42)
        frame = self.replay.observation_view(surface, deepcopy(model))
        queued = model.get("last_queued")
        if envelope is not None and queued is not None:
            records = events.observations(frame, envelope, domain_for(surface),
                                          state["published_at"][surface])
            view_state = "last_queued"
        else:
            records = frame["observations"]
            view_state = "legacy_frame_without_envelope" if queued else "preview_not_queued"
        return {
            "surface": surface, "fixture_version": self.fixture_version,
            "view_state": view_state, "record_count": len(records), "records": records,
            "delivery_claim": "queued_is_not_accepted_or_queryable",
            "message": ("Exact last-queued observation envelopes; verify ingestion in Log Analytics."
                        if view_state == "last_queued" else
                        "Read-only source projection; no upload or EventId is implied."),
        }

    def _summary(self, state, now):
        expired = state["running"] and now >= _date(state["deadline"])
        running = state["running"] and not expired
        pending = state["pending"]
        legacy = list(self._legacy_status.values())
        return {
            "session_id": state["session_id"], "phase": "stopped" if expired else state["phase"],
            "running": running, "selected_surfaces": state["selected_surfaces"],
            "started_at": state["started_at"], "warmup_until": state["warmup_until"],
            "deadline": state["deadline"],
            "remaining_seconds": max(0, int((_date(state["deadline"]) - now).total_seconds()))
                if running else 0,
            "warmup_remaining_seconds": max(0, int((_date(state["warmup_until"]) - now).total_seconds()))
                if running else 0,
            "ready": state["ready"] and running, "readiness": state["readiness"],
            "baseline_readiness": state["baseline_readiness"],
            "access_probe": deepcopy(self._access_result),
            "pending_records": sum(len(b["records"]) for b in pending) + sum(x["pending"] for x in legacy),
            "accepted_records": state["accepted_records"] + sum(x["accepted"] for x in legacy),
            "parked_batches": sum(b["next_attempt"] is None for b in pending) + sum(x["parked"] for x in legacy),
            "last_error": state["last_error"] or state["delivery_error"]
                or state["readiness"].get("error")
                or next((x["error"] for x in legacy if x["error"]), None)
                or ("Legacy backlog discovery pending; Prepare is temporarily blocked"
                    if not self._legacy_scanned else None),
            "worker_error": self.worker_error or
                ("WorkerStopped" if self._thread is not None and not self.worker_alive else None),
            "delivery_mode": self.delivery_mode, "generated_records": state["generated_records"],
            "daily_bytes": state["daily_bytes"], "daily_limit_bytes": self.daily_limit,
            "interval_seconds": self.interval, "legacy_pending_records": sum(x["pending"] for x in legacy),
            "legacy_scan_complete": self._legacy_scanned,
            "prepare_blocked": not self._legacy_scanned or bool(self._legacy) or running,
            "next_at": state["next_at"] if running else None,
        }

    def session_status(self):
        return self._summary(self._load()[0], self.clock())

    def status(self, surface):
        self.adapter(surface)
        state, _ = self._load()
        now = self.clock()
        summary = self._summary(state, now)
        selected = surface in state["selected_surfaces"]
        anomaly = state["anomalies"].get(surface)
        model = state["published"].get(surface) or self.replay.initial_model(surface, 42)
        pending = [b for b in state["pending"] if any(r.get("Surface") == surface for r in b["records"])]
        legacy = self._legacy_status.get(surface, {})
        return dict(summary, surface=surface, domain=domain_for(surface),
            running=summary["running"] and selected, selected=selected,
            session_phase=summary["phase"], run_id=state["session_id"],
            steps=state["surface_steps"].get(surface, 0), next_at=state["next_at"] if selected else None,
            scenario=model.get("scenario", "normal"), scenarios=self.replay.scenarios(surface),
            published_at=state["published_at"].get(surface),
            active_anomaly=anomaly["scenario"] if anomaly and now < _date(anomaly["until"]) else None,
            anomaly_until=anomaly["until"] if anomaly else None,
            queryability=state["readiness"]["status"],
            accepted_records=state["surface_accepted"].get(surface, 0) + legacy.get("accepted", 0),
            generated_records=state["surface_generated"].get(surface, 0),
            pending_records=sum(sum(r.get("Surface") == surface for r in b["records"])
                                for b in pending) + legacy.get("pending", 0),
            parked_batches=sum(b["next_attempt"] is None for b in pending) + legacy.get("parked", 0))

    def _tick(self, now):
        def update(state):
            now = self.clock()
            self._expire(state, now)
            if not state["running"] or now < _date(state["next_at"]):
                return
            rows = []
            for surface in state["selected_surfaces"]:
                anomaly = state["anomalies"].get(surface)
                if anomaly and now >= _date(anomaly["until"]):
                    state["models"][surface] = self.replay.apply_action(
                        surface, deepcopy(state["models"][surface]), "recover")
                    del state["anomalies"][surface]
                frame = self.replay.sample(surface, deepcopy(state["models"][surface]),
                                           state["surface_steps"][surface], now.isoformat())
                if not 1 <= len(frame["observations"]) <= 10:
                    raise ValueError("Replay must produce one to ten observations per surface")
                envelope = {"run_id": state["session_id"], "sequence": state["sequence"], "surface": surface}
                rows.extend(events.observations(frame, envelope, domain_for(surface), now.isoformat()))
                state["sequence"] += 1
                state["models"][surface] = frame["model"]
                state["published"][surface] = deepcopy(frame["model"])
                state["published_at"][surface] = now.isoformat()
                state.setdefault("published_envelopes", {})[surface] = envelope
                state["surface_steps"][surface] += 1
                state["surface_generated"][surface] = state["surface_generated"].get(surface, 0) + len(frame["observations"])
            self._append(state, events.OBSERVATIONS, rows, now)
            state["generated_records"] += len(rows)
            state.update(next_at=(now + timedelta(seconds=self.interval)).isoformat(), last_error=None)
        try:
            self._change(update)
        except Backpressure as error:
            self._change(lambda state: state.update(last_error=str(error),
                next_at=(now + timedelta(seconds=self.interval)).isoformat()) if state["running"] else None)

    def _check_readiness(self, now):
        state, _ = self._load()
        if (not state["running"] or now >= _date(state["deadline"])
                or now < _date(state["warmup_until"])
                or (state["next_check"] and now < _date(state["next_check"]))):
            return
        # Network queries deliberately run outside the mutation lock.
        from .readiness import ReadinessError
        try:
            result = self.readiness.check(state["session_id"], state["started_at"],
                                          state["warmup_until"], state["selected_surfaces"])
        except ReadinessError as error:
            result = {"ready": False, "status": "local" if self.delivery_mode == "local_capture" else "pending",
                      "checked_at": now.isoformat(),
                      "surfaces": [], "error": error.code}

        def update(current):
            if (current["session_id"] != state["session_id"] or not current["running"]
                    or self.clock() >= _date(current["deadline"])):
                return
            current["readiness"] = deepcopy(result)
            current["next_check"] = (now + timedelta(seconds=60)).isoformat()
            if result["ready"]:
                current.update(ready=True, phase="ready")
                if current["baseline_readiness"] is None:
                    current["baseline_readiness"] = deepcopy(result)
        self._change(update)

    def _check_access(self):
        now = self.clock()
        if self._access_complete or (self._access_next and now < self._access_next):
            return
        from .readiness import ReadinessError
        # Set the retry boundary before I/O so unexpected failures cannot hot-loop.
        self._access_next = now + timedelta(seconds=60)
        try:
            result = self.readiness.access_probe()
            if not isinstance(result, dict):
                raise ReadinessError("MalformedAccessProbe")
            code = result.get("code")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", code):
                raise ReadinessError("MalformedAccessProbe")
            if any(type(result.get(field)) is not bool
                   for field in ("verified", "observations_allowed", "audit_denied")):
                raise ReadinessError("MalformedAccessProbe")
            verified = result["verified"] and result["observations_allowed"] and result["audit_denied"]
            if self.delivery_mode == "local_capture":
                result = {"verified": False, "observations_allowed": False,
                          "audit_denied": False, "code": "LocalOnly"}
                self._access_complete = True
            else:
                result = {field: result[field] for field in
                          ("verified", "observations_allowed", "audit_denied", "code")}
                result["verified"] = verified
                self._access_complete = verified
        except ReadinessError as error:
            result = {"verified": False, "observations_allowed": False,
                      "audit_denied": False, "code": error.code}
        except Exception:
            result = {"verified": False, "observations_allowed": False,
                      "audit_denied": False, "code": "AccessProbeFailed"}
        checked_at = self.clock()
        if (result["code"] == "AuditNoVisibleRows"
                and now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                != checked_at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)):
            result.update(verified=False, audit_denied=False, code="ProbeWindowChanged")
            self._access_complete = False
        self._access_result = dict(result, checked_at=checked_at.isoformat())
        if not self._access_complete:
            self._access_next = self.clock() + timedelta(seconds=60)

    def _send(self, pending, now, limit=8):
        outcomes = []
        for batch in [b for b in pending if b["next_attempt"] is not None
                      and b["next_attempt"] <= now.timestamp()][:limit]:
            failure = None
            try:
                self.sender.send(batch["table"], batch["records"])
            except DeliveryError as error:
                failure = error
            outcomes.append((batch, failure))
        return outcomes

    @staticmethod
    def _ack(state, outcomes, now):
        for original, failure in outcomes:
            target = next((b for b in state["pending"] if b["id"] == original["id"]), None)
            if target is None:
                continue
            if failure is None:
                state["accepted_records"] = state.get("accepted_records", 0) + len(target["records"])
                if "surface_accepted" in state:
                    for row in target["records"]:
                        if row.get("Surface") in SURFACES:
                            surface = row["Surface"]
                            state["surface_accepted"][surface] = state["surface_accepted"].get(surface, 0) + 1
                state["pending"].remove(target)
            elif target == original:
                target["attempts"] += 1
                delay = max(failure.retry_after or 0, min(300, 2 ** target["attempts"]))
                target.update(error=failure.code, next_attempt=now.timestamp() + delay
                    if failure.retryable and target["attempts"] < 5 else None)
        error = next((f"Log delivery failed: {b['error']}" for b in state["pending"] if b["error"]), None)
        state["delivery_error" if "delivery_error" in state else "last_error"] = error

    def _deliver(self, now):
        outcomes = self._send(self._load()[0]["pending"], now)
        if outcomes:
            self._change(lambda state: self._ack(state, outcomes, now))
        return len(outcomes)

    def _legacy_change(self, key, transform):
        for attempt in range(3):
            state, version = self.store.read(key)
            if state is None:
                return None
            updated = deepcopy(state)
            transform(updated)
            try:
                if updated != state:
                    self.store.write(key, updated, version)
                return updated
            except StoreConflict:
                if attempt == 2:
                    raise

    def _drain_legacy(self, now, limit=8):
        if self._legacy is None:
            discovered = set()
            for key in self.store.keys():
                if key in SURFACES:
                    state, _ = self.store.read(key)
                    if state and state.get("pending"):
                        discovered.add(key)
                        self._remember_legacy(key, state)
            self._legacy = discovered
            self._legacy_scanned = True
        retry = self._load()[0]["retry_generation"]
        for key in sorted(self._legacy):
            retry_requested = retry > self._legacy_retry and retry > 0
            if not retry_requested and (self._legacy_due.get(key) is None
                    or self._legacy_due[key] > now.timestamp()):
                continue
            if retry_requested:
                def unpark(state):
                    if retry > state.get("demo_retry_generation", 0):
                        for batch in state.get("pending", []):
                            batch.update(attempts=0, error=None, next_attempt=0.0)
                        state["demo_retry_generation"] = retry
                self._legacy_change(key, unpark)
            state, _ = self.store.read(key)
            if state is None:
                self._legacy.remove(key)
                continue
            outcomes = self._send(state.get("pending", []), now, limit)
            limit -= len(outcomes)
            if outcomes:
                state = self._legacy_change(key, lambda current: self._ack(current, outcomes, now))
            if state is None:
                self._legacy.remove(key)
                self._legacy_status.pop(key, None)
                continue
            self._remember_legacy(key, state)
            if not state.get("pending"):
                self._legacy.remove(key)
        self._legacy_retry = retry

    def _remember_legacy(self, key, state):
        pending = state.get("pending", [])
        self._legacy_status[key] = {"pending": sum(len(b["records"]) for b in pending),
            "parked": sum(b["next_attempt"] is None for b in pending),
            "accepted": state.get("accepted_records", 0), "error": state.get("last_error")}
        due = [b["next_attempt"] for b in pending if b["next_attempt"] is not None]
        self._legacy_due[key] = min(due) if due else None

    def cycle(self):
        if not self._cycle_lock.acquire(blocking=False):
            return
        try:
            self.worker_error = None
            now = self.clock()
            sent = self._deliver(now)
            self._drain_legacy(self.clock(), 8 - sent)
            self._tick(self.clock())
            self._check_access()
            self._check_readiness(self.clock())
        except (StoreConflict, StoreUnavailable) as error:
            self.worker_error = type(error).__name__
            LOG.warning("Demo durable store unavailable: %s", type(error).__name__)
        finally:
            self._cycle_lock.release()

    def start_worker(self):
        if self.worker_alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._work, name="v2-demo-outbox", daemon=True)
        self._thread.start()

    def _work(self):
        while not self._stop.is_set():
            try:
                self.cycle()
            except Exception as error:
                self.worker_error = type(error).__name__
                LOG.exception("Demo worker cycle failed")
            self._stop.wait(1)

    @property
    def worker_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
