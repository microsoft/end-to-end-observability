"""Absolute watchdog deadlines, fail-closed activation and disposable root proof."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

GUEST_DIR = Path(__file__).resolve().parents[1] / "workloads" / "linux-demo-service"
sys.path.insert(0, str(GUEST_DIR))
import demo_common
import guest_control


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.policy = self.root / "policy.json"
        self.state = self.root / "state.json"
        self.policy.write_text(json.dumps({
            "owner": demo_common.OWNER, "role": "linux", "faults_enabled": True,
            "max_ttl_seconds": 1800,
        }))
        self.request = {
            "operation": "start_disk", "scenario_id": str(uuid4()),
            "ttl_seconds": 900, "byte_limit": demo_common.CHUNK_BYTES, "interval_seconds": 2,
        }
        self.now = 1_800_000_000.125
        self.elapsed = 0
        self.calls = []
        self.timeouts = []
        self.schedule_delay = 0
        self.readback_delay = 0
        self.properties_changes = {}
        self.service_changes = {}
        self.startup_sequence = []
        self.job_response = json.dumps({"type": "o", "data": ["/org/freedesktop/systemd1/job/1"]})
        self.timer_active = "active"
        self.raw_readback = None
        self.addCleanup(patch.stopall)
        patch.object(guest_control, "POLICY_PATH", self.policy).start()
        patch.object(guest_control, "STATE_PATH", self.state).start()
        patch.object(guest_control.time, "time", side_effect=lambda: self.now).start()
        patch.object(guest_control.time, "monotonic", side_effect=lambda: self.elapsed).start()
        patch.object(guest_control.time, "sleep", side_effect=self.advance).start()
        self.config = patch.object(guest_control.Config, "write").start()
        self.volume = patch.object(guest_control, "validate_data_root").start()
        self.run = patch.object(guest_control, "run", side_effect=self.execute).start()

    def advance(self, seconds):
        self.now += seconds
        self.elapsed += seconds

    def execute(self, args, *, timeout=30):
        self.calls.append(args)
        self.timeouts.append((self.elapsed, timeout))
        if args[0] == "busctl" and args[7] == "StartTransientUnit":
            if self.schedule_delay > timeout:
                self.advance(timeout)
                raise subprocess.TimeoutExpired(args, timeout)
            self.advance(self.schedule_delay)
            return self.job_response
        if args[0] == "busctl":
            if args[-1] == "org.freedesktop.systemd1.Unit":
                properties = self.startup_sequence.pop(0) if self.startup_sequence else {
                    "LoadState": {"type": "s", "data": "loaded"},
                    "ActiveState": {"type": "s", "data": self.timer_active},
                    "Job": {"type": "(uo)", "data": [0, "/"]},
                }
                return json.dumps({"type": "a{sv}", "data": [properties]})
            if self.readback_delay > timeout:
                self.advance(timeout)
                raise subprocess.TimeoutExpired(args, timeout)
            self.advance(self.readback_delay)
            if self.raw_readback is not None:
                return self.raw_readback
            if args[-1] == "org.freedesktop.systemd1.Service":
                properties = {
                    "RemainAfterExit": {"type": "b", "data": True},
                    "Type": {"type": "s", "data": "simple"}, "User": {"type": "s", "data": "root"},
                    "ExecStart": {"type": "a(sasbttttuii)", "data": [[
                        "/usr/bin/python3", ["/usr/bin/python3", "/usr/local/lib/asre-demo/guest_control.py",
                        "--expire-id", self.request["scenario_id"]], False, 0, 0, 0, 0, 0, 0, 0]]},
                    "ExecMainStartTimestamp": {"type": "t", "data": 0},
                    "ExecMainExitTimestamp": {"type": "t", "data": 0},
                }
                properties.update(self.service_changes)
                return json.dumps({"type": "a{sv}", "data": [properties]})
            state = json.loads(self.state.read_text())
            properties = {
                "Unit": {"type": "s", "data": f"asre-demo-expiry-{self.request['scenario_id']}.service"},
                "NextElapseUSecRealtime": {"type": "t", "data": int(state["watchdog_at"] * 1_000_000)},
                "AccuracyUSec": {"type": "t", "data": 1_000_000},
                "RandomizedDelayUSec": {"type": "t", "data": 0},
                "RemainAfterElapse": {"type": "b", "data": True},
                "LastTriggerUSec": {"type": "t", "data": 0},
            }
            properties.update(self.properties_changes)
            return json.dumps({"type": "a{sv}", "data": [properties]})
        return ""

    def assert_not_activated(self):
        self.config.assert_not_called()
        self.assertFalse(any(args[:2] in (
            ["systemctl", "restart"], ["systemctl", "stop"],
        ) for args in self.calls))

    def test_absolute_original_deadline_includes_cleanup_margin_and_utc(self):
        original = self.now
        self.schedule_delay = 12
        self.readback_delay = 3
        state = guest_control.dispatch(self.request)
        self.assertEqual(state["expires_at"], original + 900)
        self.assertEqual(state["watchdog_at"], original + 900 - 65)
        self.assertEqual(state["watchdog_cleanup_margin_seconds"], 65)
        self.assertEqual(state["watchdog_verified_at"], original + self.schedule_delay + 2 * self.readback_delay)
        self.assertGreaterEqual(state["watchdog_schedule_duration_seconds"], 0)
        calendar = self.calls[0][self.calls[0].index("OnCalendar") + 1]
        scheduled = datetime.strptime(calendar, "%Y-%m-%d %H:%M:%S.%f UTC").replace(tzinfo=timezone.utc).timestamp()
        self.assertEqual(scheduled, state["watchdog_at"])
        self.assertFalse(any(arg.startswith("--on-active=") for arg in self.calls[0]))
        self.assertEqual(self.calls[0][-5:-1], [
            "/usr/bin/python3", "/usr/local/lib/asre-demo/guest_control.py",
            "--expire-id", self.request["scenario_id"],
        ])
        self.assertEqual([args[0] for args in self.calls], ["busctl"] * 4 + ["systemctl"])
        self.assertEqual([args[-1] for args in self.calls if args[0] == "busctl" and args[6] == "GetAll"], [
            "org.freedesktop.systemd1.Unit", "org.freedesktop.systemd1.Timer", "org.freedesktop.systemd1.Service",
        ])
        self.assertEqual(self.calls[0][self.calls[0].index("RemainAfterExit") + 1:][:2], ["b", "true"])
        self.assertEqual(self.calls[0][self.calls[0].index("RemainAfterElapse") + 1:][:2], ["b", "true"])
        self.assertEqual(state["status"], "active")

    def test_900_ceiling_is_enforced_even_with_more_permissive_policy(self):
        with self.assertRaises(ValueError):
            guest_control.dispatch({**self.request, "ttl_seconds": 901})
        self.assertFalse(self.state.exists())
        self.run.assert_not_called()
        self.assert_not_activated()

    def test_short_lifetimes_fail_closed_before_state_or_timer(self):
        for ttl in (30, 60, 100):
            with self.subTest(ttl=ttl), self.assertRaisesRegex(ValueError, "Insufficient time"):
                guest_control.dispatch({**self.request, "ttl_seconds": ttl})
        self.assertFalse(self.state.exists())
        self.run.assert_not_called()
        self.assert_not_activated()

    def test_schedule_and_readback_latency_cannot_move_original_deadline(self):
        original = self.now
        self.schedule_delay = 15
        self.readback_delay = 3
        with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaisesRegex(ValueError, "Insufficient time"):
            guest_control.dispatch({**self.request, "ttl_seconds": 120})
        state = json.loads(self.state.read_text())
        self.assertEqual(state["expires_at"], original + 120)
        self.assertEqual(state["watchdog_at"], original + 55)
        self.assertEqual(state["status"], "activation_failed")
        self.assert_not_activated()

    def test_state_persistence_latency_is_rechecked_before_activation(self):
        original = self.now
        def persist(path, value, mode):
            demo_common.atomic_json(path, value, mode)
            if "watchdog_verified_at" in value:
                self.now = original + 21
        with patch.object(guest_control, "atomic_json", side_effect=persist), \
                self.assertLogs("asre-demo-control", level="ERROR"), \
                self.assertRaisesRegex(ValueError, "Insufficient time"):
            guest_control.dispatch({**self.request, "ttl_seconds": 120})
        self.assertEqual(json.loads(self.state.read_text())["status"], "activation_failed")
        self.assert_not_activated()

    def test_inactive_timer_prevents_fault(self):
        self.timer_active = "inactive"
        with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
            guest_control.dispatch(self.request)
        self.assertEqual(json.loads(self.state.read_text())["status"], "activation_failed")
        self.assert_not_activated()

    def test_mismatched_timer_properties_prevent_fault(self):
        for property_name, value in (
            ("NextElapseUSecRealtime", {"type": "t", "data": 0}),
            ("AccuracyUSec", {"type": "t", "data": 60_000_000}),
            ("RandomizedDelayUSec", {"type": "t", "data": 1}),
            ("Unit", {"type": "s", "data": "unrelated.service"}),
            ("NextElapseUSecRealtime", {"type": "s", "data": "later"}),
        ):
            with self.subTest(property=property_name):
                self.state.unlink(missing_ok=True)
                self.properties_changes = {property_name: value}
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
                    guest_control.dispatch(self.request)
                self.assertEqual(json.loads(self.state.read_text())["status"], "activation_failed")
                self.assert_not_activated()

    def test_unavailable_or_malformed_readback_prevents_fault(self):
        for response in ("", "{}", "null", '{"type":"a{sv}","data":[]}'):
            with self.subTest(response=response):
                self.state.unlink(missing_ok=True)
                self.raw_readback = response
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
                    guest_control.dispatch(self.request)
                self.assert_not_activated()

    def test_failed_readback_command_prevents_backup_fault(self):
        self.policy.write_text(self.policy.read_text().replace('"linux"', '"backup"'))
        original_execute = self.execute
        def execute(args, **kwargs):
            if args[0] == "busctl" and args[6] == "GetAll":
                raise subprocess.TimeoutExpired(args, 30)
            return original_execute(args, **kwargs)
        self.run.side_effect = execute
        with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(subprocess.TimeoutExpired):
            guest_control.dispatch({**self.request, "operation": "stop_guest_agent", "byte_limit": 0})
        self.assert_not_activated()

    def test_replay_preserves_deadline_and_timer_measurements(self):
        original = guest_control.dispatch(self.request)
        before = self.state.read_bytes()
        self.run.reset_mock()
        self.config.reset_mock()
        self.now += 100
        self.assertEqual(guest_control.dispatch(self.request), original)
        with self.assertRaises(ValueError):
            guest_control.dispatch({**self.request, "ttl_seconds": 899})
        self.assertEqual(self.state.read_bytes(), before)
        self.run.assert_not_called()
        self.config.assert_not_called()

    def test_backup_fault_occurs_only_after_verified_timer(self):
        self.policy.write_text(self.policy.read_text().replace('"linux"', '"backup"'))
        state = guest_control.dispatch({**self.request, "operation": "stop_guest_agent", "byte_limit": 0})
        self.assertEqual(self.calls[-1], ["systemctl", "stop", "walinuxagent.service"])
        self.assertEqual(self.calls[-2][0], "busctl")
        self.assertEqual(self.calls[-2][-1], "org.freedesktop.systemd1.Service")
        self.assertEqual(self.calls[-3][0], "busctl")
        self.assertEqual(self.calls[-3][-1], "org.freedesktop.systemd1.Timer")
        self.assertIn("watchdog_verified_at", state)
        self.config.assert_not_called()

    def test_late_cleanup_is_recorded_as_failure_not_deadline_success(self):
        self.state.write_text(json.dumps({
            **self.request, "status": "active", "expires_at": self.now - 1.607906818,
        }))
        with patch.object(guest_control, "clean_scenario", return_value=1):
            result = guest_control.recovery("watchdog", self.request["scenario_id"])
        self.assertFalse(result["cleanup_before_deadline"])
        self.assertLess(result["cleanup_deadline_margin_seconds"], 0)
        self.assertEqual(result["recovery_owner"], "watchdog")

    def test_native_creation_wire_is_exact_once_and_terminates_option_parsing(self):
        state = guest_control.dispatch(self.request)
        unit = "asre-demo-expiry-" + self.request["scenario_id"]
        calendar = datetime.fromtimestamp(state["watchdog_at"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")
        self.assertEqual(self.calls[0], [
            "busctl", "--json=short", "--", "call", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "StartTransientUnit",
            "ssa(sv)a(sa(sv))", unit + ".timer", "fail", "4",
            "TimersCalendar", "a(ss)", "1", "OnCalendar", calendar,
            "AccuracyUSec", "t", "1000000", "RandomizedDelayUSec", "t", "0",
            "RemainAfterElapse", "b", "true", "1", unit + ".service", "3",
            "Type", "s", "simple", "RemainAfterExit", "b", "true",
            "ExecStart", "a(sasb)", "1", "/usr/bin/python3", "4", "/usr/bin/python3",
            "/usr/local/lib/asre-demo/guest_control.py", "--expire-id", self.request["scenario_id"], "false",
        ])
        self.assertEqual(sum("StartTransientUnit" in c for c in self.calls), 1)

    def test_native_pending_job_waits_without_resubmitting(self):
        pending = {"LoadState": {"type": "s", "data": "loaded"},
                   "ActiveState": {"type": "s", "data": "activating"},
                   "Job": {"type": "(uo)", "data": [1, "/org/freedesktop/systemd1/job/1"]}}
        self.startup_sequence = [pending] * 3
        state = guest_control.dispatch(self.request)
        self.assertAlmostEqual(state["watchdog_schedule_duration_seconds"], 0.3)
        self.assertEqual(sum("StartTransientUnit" in c for c in self.calls), 1)
        self.assertEqual(sum(c[-1] == "org.freedesktop.systemd1.Unit" for c in self.calls), 4)

    def test_native_failed_foreign_or_malformed_unit_job_prevents_effects(self):
        good = {"LoadState": {"type": "s", "data": "loaded"}, "ActiveState": {"type": "s", "data": "active"},
                "Job": {"type": "(uo)", "data": [0, "/"]}}
        for name, wrong in (("LoadState", {"type": "s", "data": "not-found"}),
                            ("ActiveState", {"type": "s", "data": "failed"}),
                            ("Job", {"type": "(uo)", "data": [True, "/"]}),
                            ("Job", {"type": "(uo)", "data": [2, "/org/freedesktop/systemd1/job/2"]}),
                            ("Job", {"type": "s", "data": "done"})):
            with self.subTest(name=name, wrong=wrong):
                self.state.unlink(missing_ok=True)
                self.startup_sequence = [{**good, name: wrong}]
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
                    guest_control.dispatch(self.request)
                self.assert_not_activated()

    def test_native_creation_malformed_typed_job_replies_fail_once(self):
        for raw in ('', '{}', 'null', '{"type":"s","data":["/org/freedesktop/systemd1/job/1"]}',
                    '{"type":"o","data":[]}', '{"type":"o","data":[1]}',
                    '{"type":"o","data":["/org/freedesktop/systemd1/job/0"]}',
                    '{"type":"o","data":["/foreign/1"]}',
                    '{"type":"o","data":["/org/freedesktop/systemd1/job/1"],"type":"o"}'):
            with self.subTest(raw=raw):
                self.state.unlink(missing_ok=True)
                self.calls.clear()
                self.job_response = raw
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
                    guest_control.dispatch(self.request)
                self.assertEqual(len(self.calls), 1)
                self.assert_not_activated()

    def test_native_collision_and_uncertain_submission_never_retry(self):
        for error in (subprocess.CalledProcessError(1, ["busctl"], stderr="UnitExists"),
                      subprocess.TimeoutExpired(["busctl"], 30), OSError("ambiguous D-Bus failure")):
            with self.subTest(error=error):
                self.state.unlink(missing_ok=True)
                self.run.reset_mock()
                self.run.side_effect = error
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(type(error)):
                    guest_control.dispatch(self.request)
                self.assertEqual(self.run.call_count, 1)
                self.assert_not_activated()

    def test_native_aggregate_deadline_charges_both_readbacks(self):
        self.schedule_delay = 12
        self.readback_delay = 12
        with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(subprocess.TimeoutExpired):
            guest_control.dispatch(self.request)
        self.assertEqual(self.elapsed, 35)
        self.assertEqual(self.calls[-1][-1], "org.freedesktop.systemd1.Service")
        self.assertEqual(self.timeouts[-1], (24, 11))
        self.assertTrue(all(0 < timeout <= min(30, 35 - start) for start, timeout in self.timeouts))
        self.assert_not_activated()

    def test_native_pending_job_cannot_reset_aggregate_budget(self):
        self.startup_sequence = [{"LoadState": {"type": "s", "data": "loaded"},
                                 "ActiveState": {"type": "s", "data": "activating"},
                                 "Job": {"type": "(uo)", "data": [1, "/org/freedesktop/systemd1/job/1"]}}] * 400
        with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
            guest_control.dispatch(self.request)
        self.assertLessEqual(self.elapsed, 35)
        self.assertEqual(sum("StartTransientUnit" in c for c in self.calls), 1)
        self.assert_not_activated()

    def test_native_effective_service_type_command_and_preexecution_are_required(self):
        for name, wrong in (("Type", {"type": "s", "data": "oneshot"}),
                            ("RemainAfterExit", {"type": "b", "data": 1}),
                            ("ExecStart", {"type": "a(sasbttttuii)", "data": []}),
                            ("ExecMainStartTimestamp", {"type": "t", "data": 1}),
                            ("ExecMainExitTimestamp", {"type": "t", "data": 1})):
            with self.subTest(name=name):
                self.state.unlink(missing_ok=True)
                self.service_changes = {name: wrong}
                with self.assertLogs("asre-demo-control", level="ERROR"), self.assertRaises(ValueError):
                    guest_control.dispatch(self.request)
                self.assert_not_activated()

    def test_run_default_and_optional_timeout_are_preserved(self):
        with patch.object(guest_control.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok\n")) as native:
            # setUp substitutes run, so use the original function via the mock's saved module source.
            source = (GUEST_DIR / "guest_control.py").read_text()
            import ast
            node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "run")
            scope = {"subprocess": subprocess}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<inert-run>", "exec"), scope)
            self.assertEqual(scope["run"](["INERT"]), "ok")
            self.assertEqual(native.call_args.kwargs["timeout"], 30)
            scope["run"](["INERT"], timeout=1.25)
            self.assertEqual(native.call_args.kwargs["timeout"], 1.25)


SYSTEMD_MANAGER_PROBE = ["systemctl", "--system", "show", "--property=Version", "--value"]


def require_local_systemd():
    """Skip only known unsupported environments; ambiguous probe failures error."""
    if sys.platform != "linux":
        raise unittest.SkipTest("Watchdog fixture not applicable: requires Linux")
    if getattr(os, "geteuid", lambda: -1)() != 0:
        raise unittest.SkipTest("Watchdog fixture not applicable: requires root")
    missing = [name for name in ("systemd-run", "systemctl", "busctl") if shutil.which(name) is None]
    if missing:
        raise unittest.SkipTest("Watchdog fixture not applicable: missing " + ", ".join(missing))
    try:
        Path("/run/systemd/system").stat()
    except FileNotFoundError:
        raise unittest.SkipTest("Watchdog fixture not applicable: local systemd boot marker absent")
    version = subprocess.run(
        SYSTEMD_MANAGER_PROBE, check=True, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not version:
        raise RuntimeError("Local systemd manager probe returned no Version")
    return version


class SystemdPrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(sys, "platform", "linux").start()
        patch.object(os, "geteuid", return_value=0, create=True).start()
        self.which = patch.object(shutil, "which", side_effect=lambda name: "/usr/bin/" + name).start()
        real_stat = Path.stat
        def stat_path(path, *args, **kwargs):
            if path == Path("/run/systemd/system"):
                return None
            return real_stat(path, *args, **kwargs)
        self.marker = patch.object(Path, "stat", autospec=True, side_effect=stat_path).start()
        self.probe_result = subprocess.CompletedProcess(SYSTEMD_MANAGER_PROBE, 0, "249.11\n", "")
        self.probe_error = None
        real_run = subprocess.run
        def probe_or_run(args, **kwargs):
            if args == SYSTEMD_MANAGER_PROBE:
                if self.probe_error is not None:
                    raise self.probe_error
                return self.probe_result
            return real_run(args, **kwargs)
        # Only the read-only prerequisite probe is substituted. Integration
        # controller/cleanup subprocesses still execute for the capability-loss test.
        self.probe = patch.object(subprocess, "run", side_effect=probe_or_run).start()

    def test_unsupported_platform_is_not_applicable(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform), patch.object(sys, "platform", platform):
                with self.assertRaisesRegex(unittest.SkipTest, "not applicable: requires Linux"):
                    require_local_systemd()
        self.which.assert_not_called()
        self.probe.assert_not_called()

    def test_non_root_is_not_applicable(self):
        with patch.object(os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(unittest.SkipTest, "not applicable: requires root"):
                require_local_systemd()
        self.which.assert_not_called()
        self.probe.assert_not_called()

    def test_missing_executables_skip_fixture_before_activation(self):
        for missing in ("systemd-run", "systemctl", "busctl"):
            with self.subTest(missing=missing):
                self.which.side_effect = lambda name: None if name == missing else "/usr/bin/" + name
                result = unittest.TestResult()
                RootWatchdogTests("test_disconnected_controller_absolute_timer_cleans_only_owned_fixture").run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(result.errors, [])
                self.assertEqual(result.failures, [])
                self.assertEqual(len(result.skipped), 1)
                self.assertIn("not applicable: missing " + missing, result.skipped[0][1])
        self.marker.assert_not_called()
        self.probe.assert_not_called()

    def test_absent_boot_marker_is_not_applicable(self):
        self.marker.side_effect = FileNotFoundError("/run/systemd/system")
        with self.assertRaisesRegex(unittest.SkipTest, "not applicable: local systemd boot marker absent"):
            require_local_systemd()
        self.probe.assert_not_called()

    def test_reachable_manager_uses_bounded_read_only_probe(self):
        self.assertEqual(require_local_systemd(), "249.11")
        self.assertEqual([call.args[0] for call in self.which.call_args_list],
                         ["systemd-run", "systemctl", "busctl"])
        self.probe.assert_called_once_with(
            SYSTEMD_MANAGER_PROBE, check=True, capture_output=True, text=True, timeout=5,
        )

    def test_degraded_but_reachable_manager_is_applicable(self):
        def degraded_manager(args, **kwargs):
            if args == SYSTEMD_MANAGER_PROBE:
                return self.probe_result
            if args == ["systemctl", "is-system-running"]:
                return subprocess.CompletedProcess(args, 1, "degraded\n", "")
            self.fail("Unexpected manager probe: " + repr(args))
        self.probe.side_effect = degraded_manager
        self.assertEqual(require_local_systemd(), "249.11")
        # Manager reachability, not overall health, determines applicability.
        self.probe.assert_called_once_with(
            SYSTEMD_MANAGER_PROBE, check=True, capture_output=True, text=True, timeout=5,
        )

    def test_probe_nonzero_is_error_not_skip(self):
        self.probe_error = subprocess.CalledProcessError(1, SYSTEMD_MANAGER_PROBE, stderr="Failed to connect to bus")
        with self.assertRaises(subprocess.CalledProcessError):
            require_local_systemd()

    def test_probe_timeout_is_error_not_skip(self):
        self.probe_error = subprocess.TimeoutExpired(SYSTEMD_MANAGER_PROBE, 5)
        with self.assertRaises(subprocess.TimeoutExpired):
            require_local_systemd()

    def test_missing_probe_after_successful_lookup_is_error_not_skip(self):
        self.probe_error = FileNotFoundError("systemctl disappeared after lookup")
        with self.assertRaises(FileNotFoundError):
            require_local_systemd()

    def test_empty_manager_version_is_error_not_skip(self):
        self.probe_result.stdout = "\n"
        with self.assertRaisesRegex(RuntimeError, "returned no Version"):
            require_local_systemd()

    def test_boot_marker_permission_failure_is_error_not_absence(self):
        self.marker.side_effect = PermissionError("/run/systemd/system")
        with self.assertRaises(PermissionError):
            require_local_systemd()
        self.probe.assert_not_called()

    def test_capability_loss_after_eligibility_remains_integration_error(self):
        # Prerequisite mocks report a supported host. Removing PATH models
        # tools disappearing afterward; the real integration body must ERROR,
        # not turn its controller/cleanup failure into a not-applicable skip.
        with patch.dict(os.environ, {"PATH": ""}):
            result = unittest.TestResult()
            RootWatchdogTests("test_disconnected_controller_absolute_timer_cleans_only_owned_fixture").run(result)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("FileNotFoundError", result.errors[0][1])
        self.assertIn("systemctl", result.errors[0][1])


class RootWatchdogTests(unittest.TestCase):
    def test_disconnected_controller_absolute_timer_cleans_only_owned_fixture(self):
        require_local_systemd()
        # No installed helper, live service, real workload or mounted data is
        # changed. Only fixture paths and the service action are substituted.
        with tempfile.TemporaryDirectory(prefix="asre-demo-deadline-") as directory:
            root = Path(directory)
            scenario_id = str(uuid4())
            scenario = root / "data" / scenario_id
            scenario.mkdir(parents=True)
            files = [f"{index:08d}.bin" for index in range(896)]
            for name in files:
                with (scenario / name).open("wb") as stream:
                    stream.write(b"x" * 4096)
                    stream.truncate(demo_common.CHUNK_BYTES)
            demo_common.atomic_json(scenario / "manifest.json", {
                "owner": demo_common.OWNER, "scenario_id": scenario_id, "files": files,
            })
            unrelated = root / "data" / "unrelated"
            unrelated.write_text("preserve")
            policy = root / "policy.json"
            policy.write_text(json.dumps({
                "owner": demo_common.OWNER, "role": "linux",
                "faults_enabled": True, "max_ttl_seconds": 900,
            }))
            state_path = root / "state.json"
            launcher = root / "controller.py"
            launcher.write_text(
                "import sys, inspect\n"
                "sys.dont_write_bytecode = True\n"
                f"sys.path.insert(0, {str(GUEST_DIR)!r})\n"
                "import demo_common, guest_control\n"
                "from pathlib import Path\n"
                f"root = Path({str(root)!r})\n"
                "guest_control.POLICY_PATH = root / 'policy.json'\n"
                "guest_control.STATE_PATH = root / 'state.json'\n"
                "guest_control.Path = lambda value: root / 'control.lock'\n"
                "guest_control.validate_data_root = lambda: None\n"
                "demo_common.validate_data_root = lambda root: None\n"
                "guest_control.clean_scenario = lambda value: demo_common.clean_scenario(value, root)\n"
                "guest_control.Config.write = lambda self: demo_common.atomic_json(root / 'config.json', demo_common.asdict(self))\n"
                "# Translate only the fixture helper path in the otherwise unchanged scheduler.\n"
                "source = inspect.getsource(guest_control.install_watchdog)\n"
                "assert source.count('/usr/local/lib/asre-demo/guest_control.py') == 1\n"
                "exec(source.replace('/usr/local/lib/asre-demo/guest_control.py', str(root / 'controller.py')), guest_control.__dict__)\n"
                "real_run = guest_control.run\n"
                "def run(args, **kwargs):\n"
                "    if args[:2] == ['systemctl', 'restart']:\n"
                "        assert args == ['systemctl', 'restart', 'asre-demo.service']\n"
                "        args = ['/usr/bin/true']\n"
                "    return real_run(args, **kwargs)\n"
                "guest_control.run = run\n"
                "guest_control.main()\n"
            )
            request = {
                "operation": "start_disk", "scenario_id": scenario_id,
                "ttl_seconds": 102, "byte_limit": demo_common.CHUNK_BYTES,
                "interval_seconds": 2,
            }
            unit = f"asre-demo-expiry-{scenario_id}"
            try:
                # This controller must EXIT before the timer can restore.
                controller = subprocess.run(
                    [sys.executable, "-B", str(launcher)], input=json.dumps(request),
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(controller.returncode, 0, controller.stderr)
                armed = json.loads(controller.stdout)
                self.assertEqual(armed["status"], "active")
                self.assertEqual(armed["ttl_seconds"], 102)
                self.assertAlmostEqual(armed["expires_at"] - armed["watchdog_at"], 65)
                self.assertLess(armed["watchdog_verified_at"], armed["watchdog_at"] - 35)
                stop_waiting = time.monotonic() + 45
                restored = armed
                while time.monotonic() < stop_waiting:
                    restored = json.loads(state_path.read_text())
                    if restored["status"] == "restored":
                        break
                    time.sleep(0.1)
                self.assertEqual(restored["status"], "restored")
                self.assertEqual(restored["recovery_owner"], "watchdog")
                self.assertEqual(restored["expires_at"], armed["expires_at"])
                self.assertTrue(restored["cleanup_before_deadline"])
                self.assertGreater(restored["cleanup_deadline_margin_seconds"], 0)
                self.assertFalse(scenario.exists())
                self.assertEqual(unrelated.read_text(), "preserve")
                self.assertEqual(json.loads((root / "config.json").read_text())["mode"], "normal")
                print("LOCAL_WATCHDOG_FIXTURE " + json.dumps({
                    key: restored[key] for key in (
                        "ttl_seconds", "expires_at", "watchdog_at",
                        "watchdog_schedule_duration_seconds", "cleanup_started_at",
                        "cleanup_duration_seconds", "cleanup_deadline_margin_seconds",
                        "cleanup_before_deadline",
                    )
                }, sort_keys=True))
            finally:
                subprocess.run(
                    ["systemctl", "stop", f"{unit}.timer", f"{unit}.service"],
                    capture_output=True, text=True, timeout=30,
                )
                subprocess.run(
                    ["systemctl", "reset-failed", f"{unit}.timer", f"{unit}.service"],
                    capture_output=True, text=True, timeout=30,
                )


if __name__ == "__main__":
    unittest.main()
