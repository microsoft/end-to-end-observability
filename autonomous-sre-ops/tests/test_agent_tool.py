import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from xml.etree import ElementTree

from agent.tools.review_evidence import main
from operations.lifecycle import TARGETS
from operations.protocol import parse_request


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.target = next(target for target, role in TARGETS.items() if role == "linux")
        self.request_id = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"

    def response(self, data: bytes, status=200):
        response = io.BytesIO(data)
        response.status = status
        return response

    def test_submitted_wire_request_is_accepted_by_the_authoritative_server_contract(self):
        opener = Mock()
        opener.open.return_value = self.response(b"<QueueMessagesList><QueueMessage><MessageId>fixture-message</MessageId></QueueMessage></QueueMessagesList>", 201)
        with patch("azure.identity.DefaultAzureCredential") as credential, patch("urllib.request.build_opener", return_value=opener):
            credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
            result = main("inspect", self.target)
        request = opener.open.call_args.args[0]
        payload = base64.b64decode(ElementTree.fromstring(request.data).findtext("MessageText")).decode()
        parsed = parse_request(payload)
        self.assertEqual(parsed["resource_id"], self.target)
        self.assertEqual(parsed["request_id"], result["request_id"])
        self.assertEqual(result["status"], "accepted")
        self.assertNotIn("fixture-only", json.dumps(result))
        credential.return_value.get_token.assert_called_once_with("https://storage.azure.com/.default")

    def test_client_rejects_mutation_wrong_target_and_caller_uuid_before_authentication(self):
        with patch("azure.identity.DefaultAzureCredential") as credential:
            for action, target, request_id in (
                ("repair", self.target, ""), ("inspect", "/subscriptions/other/vm", ""),
                ("patch_jobs", self.target, ""), ("backup_jobs", self.target, ""),
                ("inspect", self.target, self.request_id), ("result", self.target, "../not-a-uuid"),
            ):
                with self.subTest(action=action, target=target), self.assertRaises((ValueError, PermissionError)):
                    main(action, target, request_id)
            credential.assert_not_called()

    def test_missing_receipt_is_not_success_or_inferred_health(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://fixture.invalid", 404, "Absent", {}, None)
        with (
            patch("azure.identity.DefaultAzureCredential") as credential,
            patch("urllib.request.build_opener", return_value=opener),
            patch("time.monotonic", side_effect=[0, 0, 91]),
            patch("time.sleep") as sleep,
        ):
            credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
            self.assertEqual(main("result", self.target, self.request_id)["status"], "not_available")
            sleep.assert_not_called()

    def test_result_waits_for_actual_receipt_without_resubmitting_or_changing_identity(self):
        receipt = {
            "request_id": self.request_id, "resource_id": self.target, "mode": "Review",
            "command": "inspect", "status": "completed", "data": {"health": {"status": "ok"}},
        }
        opener = Mock()
        opener.open.side_effect = [
            HTTPError("https://fixture.invalid", 404, "Absent", {}, None),
            self.response(json.dumps(receipt).encode()),
        ]
        with (
            patch("azure.identity.DefaultAzureCredential") as credential,
            patch("urllib.request.build_opener", return_value=opener),
            patch("time.monotonic", side_effect=[0, 0, 1, 6]),
            patch("time.sleep") as sleep,
        ):
            credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
            result = main("result", self.target, self.request_id)
        sleep.assert_called_once_with(5)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(opener.open.call_count, 2)
        self.assertIs(opener.open.call_args_list[0].args[0], opener.open.call_args_list[1].args[0])
        self.assertEqual(opener.open.call_args.args[0].get_method(), "GET")

    def test_lookup_deadline_caps_sleep_and_http_timeout_and_does_not_retry_errors(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("https://fixture.invalid", 404, "Absent", {}, None)
        with (
            patch("azure.identity.DefaultAzureCredential") as credential,
            patch("urllib.request.build_opener", return_value=opener),
            patch("time.monotonic", side_effect=[0, 89, 89, 90, 91]),
            patch("time.sleep") as sleep,
        ):
            credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
            self.assertEqual(main("result", self.target, self.request_id)["status"], "not_available")
        sleep.assert_called_once_with(1)
        self.assertTrue(all(call.kwargs["timeout"] == 1 for call in opener.open.call_args_list))
        for action, page, status in (("inspect", 0, 404), ("result", 0, 403), ("result", 0, 500)):
            opener.reset_mock()
            opener.open.side_effect = HTTPError("https://fixture.invalid", status, "Rejected", {}, None)
            with (
                patch("azure.identity.DefaultAzureCredential") as credential,
                patch("urllib.request.build_opener", return_value=opener),
                patch("time.sleep") as sleep,
                self.assertRaises(RuntimeError),
            ):
                credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
                main(action, self.target, self.request_id if action == "result" else "", page)
            self.assertEqual(opener.open.call_count, 1)
            sleep.assert_not_called()

    def test_receipt_must_bind_mode_target_command_and_request(self):
        result = {
            "request_id": self.request_id, "resource_id": self.target,
            "mode": "Review", "command": "inspect", "status": "completed", "data": {"health": {"status": "unreachable"}},
        }
        for bad in ({**result, "mode": "Autonomous"}, {**result, "resource_id": "other"}, {**result, "command": "repair"}):
            opener = Mock()
            opener.open.return_value = self.response(json.dumps(bad).encode())
            with patch("azure.identity.DefaultAzureCredential") as credential, patch("urllib.request.build_opener", return_value=opener), self.assertRaises(ValueError):
                credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
                main("result", self.target, self.request_id)
        opener = Mock()
        opener.open.return_value = self.response(json.dumps(result).encode())
        with patch("azure.identity.DefaultAzureCredential") as credential, patch("urllib.request.build_opener", return_value=opener):
            credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
            self.assertEqual(main("result", self.target, self.request_id)["data"]["health"]["status"], "unreachable")

    def test_large_receipt_pages_are_bounded_lossless_and_remain_target_bound(self):
        receipt = {
            "request_id": self.request_id, "resource_id": self.target, "mode": "Review",
            "command": "telemetry", "status": "completed",
            "data": {"rows": ['"\n\\' * 2500, "\u00e9" * 2500]},
        }
        raw = json.dumps(receipt).encode()
        opener = Mock()
        opener.open.side_effect = lambda *args, **kwargs: self.response(raw)
        with patch("azure.identity.DefaultAzureCredential") as credential, patch("urllib.request.build_opener", return_value=opener):
            credential.return_value.get_token.return_value = SimpleNamespace(token="fixture-only")
            first = main("result", self.target, self.request_id)
            pages = [first] + [
                main("result", self.target, self.request_id, page)
                for page in range(1, first["page_count"])
            ]
            with self.assertRaises(ValueError):
                main("result", self.target, self.request_id, first["page_count"])
        for page in pages:
            self.assertLess(len(json.dumps(page).encode()), 3500)
            self.assertEqual(page["resource_id"], self.target)
            self.assertEqual(page["representation"], "receipt_fragment")
        self.assertIsNone(pages[-1]["next_page"])
        self.assertEqual(json.loads("".join(page["receipt_fragment"] for page in pages)), receipt)

    def test_invalid_receipt_pages_are_rejected_before_authentication(self):
        with patch("azure.identity.DefaultAzureCredential") as credential:
            for action, page in (("result", -1), ("result", True), ("result", "0"), ("inspect", 1)):
                with self.subTest(action=action, page=page), self.assertRaises(ValueError):
                    main(action, self.target, self.request_id, page)
            credential.assert_not_called()
