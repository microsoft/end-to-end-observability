"""Offline sensor orchestration, parent lifetime, and queue budget tests."""

import io
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from operations import patch_provider as pp, patch_sensor as sensor, worker
from tests.test_patch_provider import NOW, snapshot, summary


def stuck_child(connection):
    time.sleep(30)  # Deliberately never sends/returns; parent must kill it.


class PatchSensorTests(unittest.TestCase):
    def test_only_archived_readback_can_produce_event(self):
        raw = pp.encode({"patchStatus": {"lastPatchInstallationSummary": summary()}})
        digest = pp.digest_for(pp.encode(snapshot()))
        with patch.object(pp, "read_compute", return_value=raw) as read, \
                patch.object(pp, "archive", return_value=digest) as archive:
            result = sensor.sense(object(), lambda: NOW)
        read.assert_called_once()
        archive.assert_called_once()
        self.assertEqual(result["event"], pp.event(snapshot(), digest))

    def test_read_and_archive_failures_are_not_no_failure(self):
        for operation in ("read_compute", "archive"):
            with self.subTest(operation=operation), \
                    patch.object(pp, "read_compute", return_value=pp.encode({
                        "patchStatus": {"lastPatchInstallationSummary": summary()}})), \
                    patch.object(pp, operation, side_effect=pp.ProviderError("offline failure")), \
                    self.assertRaises(pp.ProviderError):
                sensor.sense(object(), lambda: NOW)

    def test_healthy_summary_never_archives_or_emits(self):
        value = summary()
        value.update(status="Succeeded", failedPatchCount=0)
        with patch.object(pp, "read_compute", return_value=pp.encode({
                "patchStatus": {"lastPatchInstallationSummary": value}})), patch.object(pp, "archive") as archive:
            self.assertEqual(sensor.sense(object(), lambda: NOW), {"status": "no_qualifying_failure"})
        archive.assert_not_called()

    def test_real_spawned_stuck_child_is_killed_by_parent_deadline(self):
        output = io.StringIO()
        start = time.monotonic()
        with patch.object(sensor, "_child", stuck_child), patch.object(sensor, "DEADLINE_SECONDS", 0.2), redirect_stdout(output):
            sensor.run()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 3, "The parent must not wait for the child's 30-second call.")
        self.assertIn('"reason":"deadline_exceeded"', output.getvalue())
        self.assertNotIn(pp.MARKER, output.getvalue())

    def parent_result(self, value, *, exitcode=0):
        context, receiver, sender, child = Mock(), Mock(), Mock(), Mock()
        context.Pipe.return_value = (receiver, sender)
        context.Process.return_value = child
        child.is_alive.return_value = False
        child.exitcode = exitcode
        receiver.poll.return_value = True
        receiver.recv_bytes.return_value = pp.encode(value)
        output = io.StringIO()
        with patch.object(sensor.multiprocessing, "get_context", return_value=context), \
                patch.object(sensor, "datetime") as clock, redirect_stdout(output):
            clock.now.return_value = NOW
            sensor.run()
        receiver.recv_bytes.assert_called_once_with(pp.LOG_LIMIT) if exitcode == 0 else None
        child.close.assert_called_once()
        return output.getvalue()

    def test_parent_emits_exact_marker_only_after_valid_closed_child_message(self):
        value = snapshot()
        event = pp.event(value, pp.digest_for(pp.encode(value)))
        output = self.parent_result({"status": "archived", "event": event})
        self.assertIn(pp.MARKER + pp.encode(event).decode(), output)
        self.assertIn('"status":"emitted"', output)

    def test_parent_rejects_malformed_late_or_crashed_child_without_success_defaults(self):
        value = snapshot()
        event = pp.event(value, pp.digest_for(pp.encode(value)))
        for result in ({"status": "archived"}, {}, {"status": "emitted"},
                       {"status": "archived", "event": {**event, "provider_time": "2026-10-18T21:00:00Z"}}):
            output = self.parent_result(result)
            self.assertNotIn(pp.MARKER, output)
            self.assertIn('"status":"failed"', output)
        self.assertIn('"status":"failed"', self.parent_result({}, exitcode=1))

    def test_no_failure_and_failed_read_have_distinct_explicit_telemetry(self):
        self.assertIn('"status":"no_qualifying_failure"', self.parent_result({"status": "no_qualifying_failure"}))
        self.assertIn('"status":"failed"', self.parent_result(
            {"status": "failed", "reason": "provider_or_archive_unavailable"}))

    def test_queue_sensor_precedes_the_unchanged_queue_allowance_even_on_failure(self):
        order = []
        with patch.dict("os.environ", {"ASRE_EXECUTION_MODE": "Review"}), \
                patch.object(sensor, "run", side_effect=lambda: order.append("sensor_failed")), \
                patch.object(worker, "identity"), patch.object(worker, "AzureReader"), \
                patch.object(worker, "blobs"), patch.object(worker, "TableClient"), \
                patch.object(worker, "QueueClient") as queue, patch.object(worker, "Guest"), \
                patch.object(worker, "SecretClient"), patch.object(worker.time, "monotonic",
                    side_effect=lambda: order.append("queue_clock") or 10), patch("builtins.print"):
            queue.return_value.receive_messages.return_value = []
            worker.process()
        self.assertEqual(order, ["sensor_failed", "queue_clock", "queue_clock"])

    def test_other_modes_never_run_sensor(self):
        for mode in ("collect", "bootstrap", "verify", "feedback"):
            with self.subTest(mode=mode), patch("sys.argv", ["worker", mode]), \
                    patch.object(worker, "collect"), patch.object(worker, "bootstrap"), \
                    patch.object(worker, "verify"), patch("operations.feedback_runtime.run"), \
                    patch.object(sensor, "run") as run:
                worker.main()
            run.assert_not_called()
