import json
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from operations.lifecycle import TARGETS
from operations.protocol import parse_request


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.request = {
            "request_id": str(uuid4()), "command": "inspect",
            "resource_id": next(iter(TARGETS)), "sent_at": self.now.isoformat(),
        }

    def parse(self, value):
        return parse_request(json.dumps(value), self.now)

    def test_admitted_request_is_target_bound(self):
        self.assertEqual(self.parse(self.request)["resource_id"], next(iter(TARGETS)))

    def test_caller_cannot_enable_faults_or_override_server_mode(self):
        for command in ("start_disk", "repair", "shell", "bootstrap"):
            with self.subTest(command=command), self.assertRaises(PermissionError):
                self.parse({**self.request, "command": command})
        with self.assertRaises(ValueError):
            self.parse({**self.request, "mode": "Autonomous"})

    def test_expiry_future_and_unaware_timestamps_are_rejected(self):
        for date in (self.now - timedelta(minutes=11), self.now + timedelta(minutes=1), self.now.replace(tzinfo=None)):
            with self.subTest(date=date), self.assertRaises(ValueError):
                self.parse({**self.request, "sent_at": date.isoformat()})

    def test_out_of_scope_target_and_extra_command_fields_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.parse({**self.request, "resource_id": "/subscriptions/other/vm"})
        with self.assertRaises(ValueError):
            self.parse({**self.request, "shell": "ignored-is-not-allowed"})
