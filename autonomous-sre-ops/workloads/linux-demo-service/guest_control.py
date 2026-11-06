#!/usr/bin/python3
"""Fixed guest operations over SSH stdin; caller-supplied commands/paths are forbidden."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from demo_common import CONFIG_PATH, DATA_ROOT, MAX_TTL_SECONDS, OWNER, Config, atomic_json, canonical_id, clean_scenario, validate_data_root

POLICY_PATH = Path("/etc/asre-demo/policy.json")
STATE_PATH = Path("/var/lib/asre-demo/state.json")
LOG = logging.getLogger("asre-demo-control")
OPERATIONS = {
    "inspect", "recover_workload", "restore_guest_agent", "restore_patch_install",
    "start_disk", "start_memory", "start_workload_oom", "stop_guest_agent", "block_patch_install",
}
FAULTS = {"start_disk", "start_memory", "start_workload_oom", "stop_guest_agent", "block_patch_install"}
# Commissioning never extends the approved 900-second ceiling. Reserve the two
# existing 30-second recovery command timeouts, 1-second timer accuracy and
# 4 seconds for owned-file I/O/headroom. Local measurements are not VM proof.
MAX_FAULT_TTL_SECONDS = 900
WATCHDOG_CLEANUP_MARGIN_SECONDS = 65
WATCHDOG_ACTIVATION_BUDGET_SECONDS = 35  # One 30-second command plus I/O/headroom.


def run(arguments: list[str], *, timeout: float = 30) -> str:
    return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    with STATE_PATH.open(encoding="utf-8") as stream:
        state = json.load(stream)
    canonical_id(state["scenario_id"])
    return state


def recovery(owner: str, expected_id: str) -> dict:
    state = read_state()
    if state is None:
        raise ValueError("There is no active scenario to recover.")
    if state["scenario_id"] != expected_id:
        raise ValueError("The recovery owner does not match the active scenario.")
    cleanup_started_at = time.time()
    cleanup_started = time.monotonic()
    if state["operation"] == "stop_guest_agent":
        run(["systemctl", "start", "walinuxagent.service"])
        run(["systemctl", "is-active", "--quiet", "walinuxagent.service"])
        removed = 0
    elif state["operation"] == "block_patch_install":
        from patch_failure import disable
        removed = int(disable(expected_id))
    else:
        Config().write()
        run(["systemctl", "restart", "asre-demo.service"])
        removed = clean_scenario(expected_id) if state["operation"] == "start_disk" else 0
    restored_at = time.time()
    timing = {
        "cleanup_started_at": cleanup_started_at,
        "cleanup_duration_seconds": time.monotonic() - cleanup_started,
        "cleanup_deadline_margin_seconds": state["expires_at"] - restored_at,
        "cleanup_before_deadline": restored_at < state["expires_at"],
    }
    state.update({"status": "restored", "recovery_owner": owner, "restored_at": restored_at, **timing})
    atomic_json(STATE_PATH, state, 0o600)
    LOG.warning("asre-demo restored scenario=%s recovery_owner=%s", expected_id, owner)
    return {"status": "restored", "scenario_id": expected_id, "recovery_owner": owner, "removed_files": removed, **timing}


def validate_request(request: dict, policy: dict, now: float) -> dict:
    if not isinstance(request, dict) or request.get("operation") not in OPERATIONS:
        raise ValueError("Unknown guest operation.")
    operation = request["operation"]
    fields = {"operation"} if operation == "inspect" else {"operation", "scenario_id"}
    if operation in FAULTS:
        fields |= {"ttl_seconds", "byte_limit", "interval_seconds"}
    if set(request) != fields:
        raise ValueError("Unexpected or missing operation fields.")
    if policy.get("owner") != OWNER or policy.get("role") not in {"linux", "patch", "backup"}:
        raise ValueError("The target is not an owned demonstration VM.")
    if operation != "inspect":
        canonical_id(request["scenario_id"])
    if operation in FAULTS:
        if policy.get("faults_enabled") is not True:
            raise PermissionError("Fault injection is disabled on this target.")
        ttl = request["ttl_seconds"]
        if type(ttl) is not int or not 30 <= ttl <= min(policy["max_ttl_seconds"], MAX_TTL_SECONDS, MAX_FAULT_TTL_SECONDS):
            raise ValueError("Fault lifetime exceeds the independent safety bound.")
        expected_role = {"stop_guest_agent": "backup", "block_patch_install": "patch"}.get(operation, "linux")
        if policy["role"] != expected_role:
            raise PermissionError("This fault does not belong to the target's role.")
        if operation == "stop_guest_agent":
            if request["byte_limit"] != 0 or request["interval_seconds"] != 2:
                raise ValueError("Backup faults do not accept workload growth settings.")
        elif operation == "block_patch_install":
            if request["byte_limit"] != 0 or request["interval_seconds"] != 2:
                raise ValueError("Patch faults do not accept workload growth settings.")
        else:
            modes = {"start_disk": "disk", "start_memory": "memory", "start_workload_oom": "memory-oom"}
            Config.parse(asdict(Config(
                mode=modes[operation], scenario_id=request["scenario_id"],
                expires_at=now + ttl, byte_limit=request["byte_limit"],
                interval_seconds=request["interval_seconds"],
            )))
    if operation == "restore_guest_agent" and policy["role"] != "backup":
        raise PermissionError("Guest-agent recovery is restricted to the backup target.")
    if operation == "restore_patch_install" and policy["role"] != "patch":
        raise PermissionError("Patch configuration recovery is restricted to the patch target.")
    if operation == "recover_workload" and policy["role"] != "linux":
        raise PermissionError("Workload recovery is restricted to the Linux incident target.")
    return request


def require_activation_window(state: dict) -> None:
    if time.time() + WATCHDOG_ACTIVATION_BUDGET_SECONDS >= state["watchdog_at"]:
        raise ValueError("Insufficient time remains before independent cleanup; fault not activated.")


def install_watchdog(state: dict) -> None:
    """Install and read back the original absolute UTC deadline, never a new TTL."""
    deadline = time.monotonic() + WATCHDOG_ACTIVATION_BUDGET_SECONDS
    def call(arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Independent cleanup startup budget exhausted.")
        raw = run(arguments, timeout=min(30, remaining))
        if time.monotonic() >= deadline:
            raise ValueError("Independent cleanup startup budget exhausted.")
        return raw

    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate independent cleanup response field.")
            result[key] = value
        return result

    def decode(raw, kind):
        response = json.loads(raw, object_pairs_hook=unique)
        if (type(response) is not dict or set(response) != {"type", "data"}
                or response["type"] != kind or type(response["data"]) is not list
                or len(response["data"]) != 1):
            raise ValueError("Unexpected independent cleanup response.")
        return response["data"][0]

    def value(properties, name, kind):
        item = properties[name]
        if type(item) is not dict or set(item) != {"type", "data"} or item["type"] != kind:
            raise ValueError("Unexpected independent cleanup property type.")
        data = item["data"]
        if ((kind == "b" and type(data) is not bool)
                or (kind == "t" and (type(data) is not int or data < 0))
                or (kind == "s" and type(data) is not str)):
            raise ValueError("Unexpected independent cleanup property value.")
        return data

    scenario_id = canonical_id(state["scenario_id"])
    unit = f"asre-demo-expiry-{scenario_id}"
    # Floor to microseconds so formatting cannot move cleanup later.
    trigger_us = int(state["watchdog_at"] * 1_000_000)
    calendar = datetime.fromtimestamp(trigger_us // 1_000_000, timezone.utc).replace(
        microsecond=trigger_us % 1_000_000,
    ).strftime("%Y-%m-%d %H:%M:%S.%f UTC")
    argv = ["/usr/bin/python3", "/usr/local/lib/asre-demo/guest_control.py", "--expire-id", scenario_id]
    # systemd-run v249 overrides RemainAfterElapse=false. Submit the fixed pair
    # directly, once, with collision-failing job mode and no recovery fallback.
    # Terminate busctl option parsing before the embedded --expire-id argument.
    raw = call([
        "busctl", "--json=short", "--", "call", "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "StartTransientUnit",
        "ssa(sv)a(sa(sv))", unit + ".timer", "fail", "4",
        "TimersCalendar", "a(ss)", "1", "OnCalendar", calendar,
        "AccuracyUSec", "t", "1000000", "RandomizedDelayUSec", "t", "0",
        "RemainAfterElapse", "b", "true", "1", unit + ".service", "3",
        "Type", "s", "simple", "RemainAfterExit", "b", "true",
        "ExecStart", "a(sasb)", "1", argv[0], "4", *argv, "false",
    ])
    job_path = decode(raw, "o")
    prefix = "/org/freedesktop/systemd1/job/"
    if (type(job_path) is not str or not job_path.startswith(prefix)
            or not job_path[len(prefix):].isascii() or not job_path[len(prefix):].isdecimal()
            or job_path[len(prefix):].startswith("0")):
        raise ValueError("Unexpected independent cleanup start job.")
    job_id = int(job_path[len(prefix):])
    # systemctl show renders this timestamp at second precision, even with
    # --timestamp=us+utc on supported Ubuntu. Read numeric microseconds instead.
    object_path = "/org/freedesktop/systemd1/unit/" + unit.replace("-", "_2d") + "_2etimer"
    def properties(path, interface):
        result = decode(call([
            "busctl", "--json=short", "call", "org.freedesktop.systemd1", path,
            "org.freedesktop.DBus.Properties", "GetAll", "s", "org.freedesktop.systemd1." + interface,
        ]), "a{sv}")
        if type(result) is not dict:
            raise ValueError("Unexpected independent cleanup properties.")
        return result

    try:
        while True:
            status = properties(object_path, "Unit")
            load = value(status, "LoadState", "s")
            active = value(status, "ActiveState", "s")
            job = value(status, "Job", "(uo)")
            if (type(job) is not list or len(job) != 2 or type(job[0]) is not int
                    or type(job[1]) is not str):
                raise ValueError("Unexpected independent cleanup unit job.")
            if load == "loaded" and active == "active" and job == [0, "/"]:
                break
            if (load not in ("loaded", "stub") or active not in ("inactive", "activating", "active")
                    or job != [job_id, job_path]):
                raise ValueError("Independent cleanup start job did not complete successfully.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Independent cleanup startup budget exhausted.")
            time.sleep(min(0.1, remaining))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Independent cleanup startup verification failed.") from error
    try:
        timer = properties(object_path, "Timer")
        for name, expected in (
            ("NextElapseUSecRealtime", trigger_us),
            ("AccuracyUSec", 1_000_000),
            ("RandomizedDelayUSec", 0),
            ("LastTriggerUSec", 0),
        ):
            if value(timer, name, "t") != expected:
                raise ValueError("Independent cleanup scheduling does not match its deadline.")
        if value(timer, "Unit", "s") != f"{unit}.service":
            raise ValueError("Independent cleanup does not own the expected service.")
        if value(timer, "RemainAfterElapse", "b") is not True:
            raise ValueError("Independent cleanup timer metadata will not be retained.")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Independent cleanup timer verification failed.") from error
    service_path = "/org/freedesktop/systemd1/unit/" + unit.replace("-", "_2d") + "_2eservice"
    try:
        service = properties(service_path, "Service")
        if value(service, "RemainAfterExit", "b") is not True:
            raise ValueError("Independent cleanup service metadata will not be retained.")
        entries = value(service, "ExecStart", "a(sasbttttuii)")
        if (value(service, "Type", "s") != "simple"
                or value(service, "User", "s") not in ("", "root")
                or type(entries) is not list or len(entries) != 1
                or type(entries[0]) is not list or len(entries[0]) != 10
                or entries[0][0] != argv[0] or entries[0][1] != argv or entries[0][2] is not False
                or value(service, "ExecMainStartTimestamp", "t") != 0
                or value(service, "ExecMainExitTimestamp", "t") != 0):
            raise ValueError("Independent cleanup service is not the unexecuted owned command.")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Independent cleanup service verification failed.") from error
    if time.monotonic() >= deadline:
        raise ValueError("Independent cleanup startup budget exhausted.")
    require_activation_window(state)


def inspect() -> dict:
    status = subprocess.run(
        ["systemctl", "show", "asre-demo.service", "walinuxagent.service",
         "--property=Id,ActiveState,SubState,Result,MemoryCurrent,MemoryPeak,NRestarts"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout
    try:
        with urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
            health = json.load(response)
    except OSError as error:
        health = {"status": "unreachable", "error": str(error)}
    journal = run(["journalctl", "-u", "asre-demo.service", "-n", "40", "--no-pager", "--output=short-iso"])
    state = read_state()
    return {
        "service": status, "health": health, "journal": journal, "observed_at": time.time(),
        "recovery_owner": state.get("recovery_owner") if state and state["status"] == "restored" else None,
    }


def dispatch(request: dict, *, inspect_only: bool = False, operator_fault: bool = False) -> dict:
    # This authority comes only from the fixed entry point, never from request
    # fields or SSH_ORIGINAL_COMMAND. Reject before reading privileged state.
    if inspect_only and (not isinstance(request, dict) or request.get("operation") != "inspect"):
        raise PermissionError("The runner entry point permits inspection only.")
    if operator_fault and (
        inspect_only or os.geteuid() != 0 or not isinstance(request, dict) or request.get("operation") not in FAULTS
    ):
        raise PermissionError("Only the root operator entry point may authorize a bounded fault.")
    with POLICY_PATH.open(encoding="utf-8") as stream:
        policy = json.load(stream)
    if operator_fault:
        # Root operator authority is invocation-local; the runner policy stays disabled.
        policy["faults_enabled"] = True
    validate_request(request, policy, time.time())
    operation = request["operation"]
    if operation == "inspect":
        return inspect()
    if operation in {"recover_workload", "restore_guest_agent", "restore_patch_install"}:
        state = read_state()
        expected_fault = {"restore_guest_agent": "stop_guest_agent",
                          "restore_patch_install": "block_patch_install"}.get(operation)
        if state is None or (expected_fault is not None and state["operation"] != expected_fault):
            raise ValueError("The requested repair does not match the active fault.")
        if state["scenario_id"] != request["scenario_id"]:
            raise ValueError("The requested scenario does not own this target.")
        if operation == "recover_workload" and state["operation"] == "stop_guest_agent":
            raise ValueError("A workload repair cannot change the guest agent.")
        if operation == "recover_workload" and state["operation"] == "block_patch_install":
            raise ValueError("A workload repair cannot change package configuration.")
        if state["status"] == "restored":
            return state
        return recovery("operator", request["scenario_id"])

    previous = read_state()
    if previous and previous["scenario_id"] == request["scenario_id"]:
        if previous["status"] == "active" and all(previous[key] == value for key, value in request.items()):
            return previous
        raise ValueError("A scenario ID cannot be reused or its safety lifetime changed.")
    if previous and previous["status"] != "restored":
        raise ValueError("A scenario already owns this target; expiry cannot be extended.")
    if operation == "start_disk":
        validate_data_root()
        if operator_fault:
            device = Path("/dev/disk/azure/scsi1/lun0").stat()
            if DATA_ROOT.stat().st_dev != device.st_rdev or any((DATA_ROOT / "data").iterdir()):
                raise ValueError("The fixed disposable LUN must be mounted with no prior owned data.")
            volume = os.statvfs(DATA_ROOT)
            capacity = volume.f_blocks * volume.f_frsize
            projected = (
                (volume.f_blocks - volume.f_bavail) * volume.f_frsize
                + request["byte_limit"] + 64 * 1024 * 1024
            )
            if capacity <= 0 or projected * 100 >= capacity * 85:
                raise ValueError("The disk fault must retain physical headroom below 85 percent.")
    state = {**request, "status": "armed", "expires_at": time.time() + request["ttl_seconds"]}
    state["watchdog_at"] = state["expires_at"] - WATCHDOG_CLEANUP_MARGIN_SECONDS
    state["watchdog_cleanup_margin_seconds"] = WATCHDOG_CLEANUP_MARGIN_SECONDS
    require_activation_window(state)
    scheduling_started = time.monotonic()
    atomic_json(STATE_PATH, state, 0o600)
    # Install the independent safety timer before changing the guest or workload.
    try:
        install_watchdog(state)
        state["watchdog_verified_at"] = time.time()
        state["watchdog_schedule_duration_seconds"] = time.monotonic() - scheduling_started
        atomic_json(STATE_PATH, state, 0o600)
        require_activation_window(state)
        if operation == "stop_guest_agent":
            run(["systemctl", "stop", "walinuxagent.service"])
        elif operation == "block_patch_install":
            from patch_failure import enable
            enable(request["scenario_id"])
        else:
            modes = {"start_disk": "disk", "start_memory": "memory", "start_workload_oom": "memory-oom"}
            Config(
                mode=modes[operation], scenario_id=request["scenario_id"],
                expires_at=state["expires_at"], byte_limit=request["byte_limit"],
                interval_seconds=request["interval_seconds"],
            ).write()
            run(["systemctl", "restart", "asre-demo.service"])
    except (OSError, ValueError, subprocess.SubprocessError):
        LOG.exception("Scenario activation failed; recording an explicit failed state")
        state["status"] = "activation_failed"
        atomic_json(STATE_PATH, state, 0o600)
        raise
    state["status"] = "active"
    atomic_json(STATE_PATH, state, 0o600)
    return state


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if os.geteuid() != 0:
        raise PermissionError("The fixed guest helper requires root.")
    arguments = sys.argv[1:]
    if arguments in ([], ["--inspect-only"], ["--operator-fault"]):
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError("Guest request exceeds the size limit.")
        request = json.loads(raw)
        if arguments == ["--inspect-only"]:
            # Inspection neither creates the mutation lock nor gains access to
            # operator dispatch, including when extra argv are supplied.
            print(json.dumps(dispatch(request, inspect_only=True), sort_keys=True))
            return
    elif len(arguments) != 2 or arguments[0] != "--expire-id":
        raise ValueError("Only --inspect-only, --operator-fault, root stdin, or root --expire-id are accepted.")

    import fcntl

    with Path("/run/asre-demo-control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if arguments and arguments[0] == "--expire-id":
            expected_id = canonical_id(arguments[1])
            state = read_state()
            if state is None or state["scenario_id"] != expected_id or state["status"] == "restored":
                LOG.info("Stale expiry timer has no active ownership.")
                result = {"status": "stale_timer", "scenario_id": expected_id}
            else:
                result = recovery("watchdog", expected_id)
        else:
            result = dispatch(request, operator_fault=arguments == ["--operator-fault"])
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        LOG.exception("Guest operation rejected or failed")
        sys.exit(1)
