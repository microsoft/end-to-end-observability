"""Transport tests use local project scratch space and mocked Azure SDKs only."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import multiprocessing
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

from azure.core import MatchConditions
from azure.core.exceptions import (
    ClientAuthenticationError, HttpResponseError, ResourceExistsError,
    ResourceModifiedError, ResourceNotFoundError, ServiceRequestError, ServiceResponseError,
)

from energy_domains_v2 import transport as t


ENDPOINT = "https://v2-dcr.centralus-1.ingest.monitor.azure.com"
ACCOUNT = "https://v2storage123.blob.core.windows.net"
RULE = "dcr-" + "a" * 32
TABLE = "EnergyObservations_CL"
HOST_MARKERS = {name: "" for name in (
    "WEBSITE_SITE_NAME", "WEBSITE_INSTANCE_ID", "IDENTITY_ENDPOINT", "MSI_ENDPOINT",
)}


def _race(directory, version, barrier, queue, value):
    store = t.LocalStateStore(directory)
    barrier.wait(timeout=15)
    try:
        store.write("power-grid", {"value": value}, version)
        queue.put("written")
    except t.StoreConflict:
        queue.put("conflict")


def _die_with_lock(directory, ready, release):
    with t.LocalStateStore(directory)._locked():
        ready.set()
        release.wait(timeout=15)
        os._exit(13)


def _http(status, headers=None, error_type=HttpResponseError, code=None):
    response = SimpleNamespace(
        status_code=status, headers=headers or {}, reason="secret-token-must-not-leak",
        content_type="application/json",
    )
    error = error_type(message="secret-token-must-not-leak", response=response)
    if code is not None:
        error.error_code = code
    return error


class ScratchTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".transport-tests-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.environment = patch.dict(os.environ, HOST_MARKERS)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(lambda: shutil.rmtree(self.root))


class LocalStateTests(ScratchTest):
    def test_restart_compare_swap_and_document_isolation(self):
        store = t.LocalStateStore(self.root)
        self.assertEqual(store.read("power-grid"), (None, None))
        original = {"pending": [{"id": "stable-event", "value": None}], "stock": 0}
        first = store.write("power-grid", original, None)
        original["stock"] = 99
        restarted = t.LocalStateStore(self.root)
        document, version = restarted.read("power-grid")
        self.assertEqual(first, version)
        self.assertEqual(document["stock"], 0)
        document["stock"] = 2
        second = restarted.write("power-grid", document, first)
        self.assertNotEqual(first, second)
        for stale in (None, first):
            with self.assertRaises(t.StoreConflict):
                store.write("power-grid", {"stock": -1}, stale)
        self.assertEqual(store.read("power-grid"), (document, second))
        self.assertEqual(store.keys(), ["power-grid"])

    def test_missing_with_version_conflicts(self):
        with self.assertRaises(t.StoreConflict):
            t.LocalStateStore(self.root).write("missing", {}, "old")

    def test_multi_process_create_and_update_have_one_winner(self):
        store = t.LocalStateStore(self.root)
        context = multiprocessing.get_context("spawn")
        for version in (None, "read_current"):
            if version is not None:
                version = store.read("power-grid")[1]
            barrier, queue = context.Barrier(3), context.Queue()
            workers = [
                context.Process(target=_race, args=(str(self.root), version, barrier, queue, i))
                for i in range(2)
            ]
            try:
                for worker in workers:
                    worker.start()
                barrier.wait(timeout=15)
                results = [queue.get(timeout=15) for _ in workers]
                for worker in workers:
                    worker.join(timeout=15)
                    self.assertEqual(worker.exitcode, 0)
                self.assertEqual(sorted(results), ["conflict", "written"])
            finally:
                for worker in workers:
                    if worker.is_alive():
                        worker.terminate()
                        worker.join(timeout=5)
                queue.close()

    def test_crashed_owner_releases_os_lock_and_busy_is_bounded(self):
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        worker = context.Process(target=_die_with_lock, args=(str(self.root), ready, release))
        worker.start()
        try:
            self.assertTrue(ready.wait(timeout=15))
            with patch.object(t, "LOCK_TIMEOUT_SECONDS", 0.05):
                with self.assertRaises(t.StoreUnavailable):
                    t.LocalStateStore(self.root).read("power-grid")
            release.set()
            worker.join(timeout=15)
            self.assertEqual(worker.exitcode, 13)
            t.LocalStateStore(self.root).write("power-grid", {"recovered": True}, None)
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)

    def test_atomic_replace_failure_preserves_committed_document(self):
        store = t.LocalStateStore(self.root)
        version = store.write("power-grid", {"pending": ["a"]}, None)
        with patch.object(t.os, "replace", side_effect=OSError("secret-path")):
            with self.assertRaisesRegex(t.StoreUnavailable, "^LocalStateUnavailable$"):
                store.write("power-grid", {"pending": []}, version)
        self.assertEqual(store.read("power-grid"), ({"pending": ["a"]}, version))
        self.assertEqual(list(self.root.glob("*.pending")), [])

    def test_invalid_keys_never_touch_paths(self):
        store = t.LocalStateStore(self.root)
        for key in ("../power-grid", "a/b", "a\\b", "Upper", "", "a" * 81,
                    "con", "nul", "lpt1", ".hidden", "a:stream", None):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    store.write(key, {}, None)
                with self.assertRaises(ValueError):
                    store.read(key)
        store.write("quota", {}, None)
        store.write("a" * 80, {}, None)
        self.assertEqual(len(store.keys()), 2)

    def test_corruption_nonfinite_and_invalid_json_do_not_reset_state(self):
        store = t.LocalStateStore(self.root)
        for contents in ('{', '{"version":"abc","document":{}}', '{"document":{"v":NaN}}'):
            (self.root / "power-grid.json").write_text(contents, encoding="utf-8")
            with self.assertRaises(t.StoreUnavailable):
                store.read("power-grid")
            with self.assertRaises(t.StoreUnavailable):
                store.write("power-grid", {}, None)
        for document in ({"v": float("nan")}, {"v": float("inf")}, {1: "bad"}, {"v": (1, 2)}):
            with self.assertRaises(ValueError):
                store.write("valid", document, None)

    def test_keys_are_sorted_bounded_and_reject_unsafe_files(self):
        store = t.LocalStateStore(self.root)
        for key in ("upstream", "power-grid"):
            store.write(key, {}, None)
        with patch.object(t, "MAX_LISTED_KEYS", 1):
            with self.assertRaises(t.StoreUnavailable):
                store.keys()
        (self.root / "Upper.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(t.StoreUnavailable):
            store.keys()


class BlobStateTests(unittest.TestCase):
    def setUp(self):
        self.sdk = patch.object(t, "BlobServiceClient")
        self.service = self.sdk.start().return_value
        self.addCleanup(self.sdk.stop)
        self.env = patch.dict(os.environ, HOST_MARKERS)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.container = self.service.get_container_client.return_value
        self.blob = self.container.get_blob_client.return_value
        self.credential = MagicMock()
        self.store = t.BlobStateStore(ACCOUNT, "v2-state", credential=self.credential)

    def test_conditional_create_and_update(self):
        self.blob.upload_blob.return_value = {"etag": '"version-1"'}
        version = self.store.write("power-grid", {"pending": ["one"]}, None)
        self.assertEqual(version, '"version-1"')
        self.assertFalse(self.blob.upload_blob.call_args.kwargs["overwrite"])
        self.assertNotIn("etag", self.blob.upload_blob.call_args.kwargs)
        self.blob.upload_blob.return_value = {"etag": '"version-2"'}
        self.assertEqual(self.store.write("power-grid", {}, version), '"version-2"')
        self.assertEqual(self.blob.upload_blob.call_args.kwargs["etag"], version)
        self.assertEqual(self.blob.upload_blob.call_args.kwargs["match_condition"],
                         MatchConditions.IfNotModified)

    def test_etag_and_body_are_from_same_read(self):
        download = self.blob.download_blob.return_value
        download.size = 32
        download.readall.return_value = b'{"pending":[{"id":"stable"}]}'
        download.properties.etag = '"same-get"'
        self.assertEqual(self.store.read("power-grid"), ({"pending": [{"id": "stable"}]}, '"same-get"'))
        self.blob.get_blob_properties.assert_not_called()
        self.assertEqual(self.blob.download_blob.call_args.kwargs["max_concurrency"], 1)

    def test_missing_blob_is_distinct_from_missing_container(self):
        self.blob.download_blob.side_effect = _http(
            404, error_type=ResourceNotFoundError, code="BlobNotFound")
        self.assertEqual(self.store.read("power-grid"), (None, None))
        self.blob.download_blob.side_effect = _http(
            404, error_type=ResourceNotFoundError, code="ContainerNotFound")
        with self.assertRaises(t.StoreUnavailable):
            self.store.read("power-grid")

    def test_collision_and_transient_failure_never_unconditionally_retry(self):
        for error in (ResourceExistsError("collision"), ResourceModifiedError("collision"),
                      _http(412), _http(409)):
            with self.subTest(error=type(error)):
                self.blob.upload_blob.reset_mock()
                self.blob.upload_blob.side_effect = error
                with self.assertRaises(t.StoreConflict):
                    self.store.write("power-grid", {}, '"stale"')
                self.blob.upload_blob.assert_called_once()
        for error in (_http(503), ServiceRequestError("secret"), ClientAuthenticationError("secret")):
            self.blob.upload_blob.side_effect = error
            with self.assertRaisesRegex(t.StoreUnavailable, "^BlobStateUnavailable$"):
                self.store.write("power-grid", {}, None)

    def test_keys_safe_sorted_bounded_and_storage_failure_is_visible(self):
        self.container.list_blobs.return_value = [
            SimpleNamespace(name="upstream.json"), SimpleNamespace(name="power-grid.json")]
        self.assertEqual(self.store.keys(), ["power-grid", "upstream"])
        with patch.object(t, "MAX_LISTED_KEYS", 1):
            with self.assertRaises(t.StoreUnavailable):
                self.store.keys()
        self.container.list_blobs.return_value = [SimpleNamespace(name="../escape.json")]
        with self.assertRaises(t.StoreUnavailable):
            self.store.keys()
        self.container.list_blobs.side_effect = ServiceResponseError("secret")
        with self.assertRaises(t.StoreUnavailable):
            self.store.keys()

    def test_corrupt_and_oversized_blob_are_not_missing(self):
        download = self.blob.download_blob.return_value
        download.size = 30
        download.readall.return_value = b'{"v": NaN}'
        with self.assertRaises(t.StoreUnavailable):
            self.store.read("power-grid")
        download.size = t.MAX_STATE_BYTES + 1
        download.readall.reset_mock()
        with self.assertRaises(t.StoreUnavailable):
            self.store.read("power-grid")
        download.readall.assert_not_called()

    def test_missing_etag_cannot_turn_next_update_into_a_create(self):
        self.blob.upload_blob.return_value = {}
        with self.assertRaises(t.StoreUnavailable):
            self.store.write("power-grid", {}, None)
        download = self.blob.download_blob.return_value
        download.size = 2
        download.readall.return_value = b"{}"
        download.properties.etag = None
        with self.assertRaises(t.StoreUnavailable):
            self.store.read("power-grid")

    def test_blob_sdk_retry_policy_has_short_bounded_backoff(self):
        with patch.object(t, "BlobServiceClient") as client:
            t.BlobStateStore(ACCOUNT, "v2-state", self.credential)
            kwargs = client.call_args.kwargs
            self.assertIsInstance(kwargs["retry_policy"], t.LinearRetry)
            self.assertEqual(kwargs["retry_policy"].total_retries, 2)
            self.assertEqual(kwargs["retry_policy"].backoff, 1)
            self.assertEqual(kwargs["retry_policy"].random_jitter_range, 0)
            self.assertEqual(kwargs["connection_timeout"], 5)
            self.assertEqual(kwargs["read_timeout"], 15)

    def test_invalid_urls_containers_and_credentials_rejected_before_sdk_creation(self):
        for account in (
            "http://v2storage123.blob.core.windows.net", "https://127.0.0.1",
            ACCOUNT + "/../other", ACCOUNT + "?sig=secret", ACCOUNT + "#fragment",
            "https://v2storage123.blob.core.windows.net.evil.test",
            "https://secret@v2storage123.blob.core.windows.net",
            ACCOUNT + ":443", ACCOUNT + "\\evil", ACCOUNT + "\n",
        ):
            with self.subTest(account=account), self.assertRaises(ValueError):
                t.BlobStateStore(account, "v2-state", self.credential)
        for container in ("../state", "a/b", "a\\b", "a--b", "UPPER", "ab", "a" * 64):
            with self.subTest(container=container), self.assertRaises(ValueError):
                t.BlobStateStore(ACCOUNT, container, self.credential)
        for credential in ("account-key", {"account_key": "secret"}):
            with self.assertRaises(ValueError):
                t.BlobStateStore(ACCOUNT, "v2-state", credential)


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.upload.return_value = None
        self.sender = t.IngestionSender(ENDPOINT, RULE, client=self.client)

    def test_none_is_accepted_exact_table_mapping_without_callback(self):
        records = [{"Payload": {"value": None, "stock": 0}, "IsSynthetic": True}]
        for table in t.TABLE_STREAMS:
            self.assertIsNone(self.sender.send(table, records))
            kwargs = self.client.upload.call_args.kwargs
            self.assertEqual(kwargs["stream_name"], f"Custom-{table}")
            self.assertEqual(kwargs["rule_id"], RULE)
            self.assertEqual(kwargs["logs"], records)
            self.assertNotIn("on_error", kwargs)
            self.assertEqual(kwargs["connection_timeout"], 5)
            self.assertEqual(kwargs["read_timeout"], 15)

    def test_partial_error_raises_and_outer_retry_can_accept_same_records(self):
        records = [{"EventId": "stable-one"}, {"EventId": "stable-two"}]
        self.client.upload.side_effect = [_http(503, {"Retry-After": "12"}), None]
        with self.assertRaises(t.DeliveryError) as failure:
            self.sender.send(TABLE, records)
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(failure.exception.retry_after, 12)
        self.assertEqual(failure.exception.code, "Http503")
        self.assertIsNone(self.sender.send(TABLE, records))
        for call in self.client.upload.call_args_list:
            self.assertEqual(call.kwargs["logs"], records)
            self.assertNotIn("on_error", call.kwargs)

    def test_http_status_classification_and_sanitization(self):
        for status in (400, 401, 403, 404, 408, 413, 429, 500, 503, 599):
            self.client.upload.side_effect = _http(status, {"Retry-After": "2"})
            with self.subTest(status=status), self.assertRaises(t.DeliveryError) as failure:
                self.sender.send(TABLE, [{}])
            error = failure.exception
            self.assertEqual(error.retryable, status == 429 or status >= 500)
            self.assertEqual(error.code, f"Http{status}")
            self.assertEqual(error.retry_after, 2)
            self.assertNotIn("secret", str(error))
            self.assertIsNone(error.__cause__)

    def test_transport_authentication_and_unexpected_response(self):
        for error, retryable in (
            (ServiceRequestError("secret"), True), (ServiceResponseError("secret"), True),
            (ClientAuthenticationError("secret"), False),
        ):
            self.client.upload.side_effect = error
            with self.assertRaises(t.DeliveryError) as failure:
                self.sender.send(TABLE, [{}])
            self.assertEqual(failure.exception.retryable, retryable)
            self.assertNotIn("secret", str(failure.exception))
        self.client.upload.side_effect = None
        self.client.upload.return_value = {"errors": ["partial failure"]}
        with self.assertRaises(t.DeliveryError):
            self.sender.send(TABLE, [{}])

    def test_retry_after_seconds_dates_invalid_and_milliseconds(self):
        self.assertEqual(t._retry_after({"Retry-After": "12.5"}), 12.5)
        self.assertEqual(t._retry_after({"x-ms-retry-after-ms": "250"}), 0.25)
        later = datetime.now(timezone.utc) + timedelta(seconds=120)
        self.assertTrue(118 <= t._retry_after({"retry-after": format_datetime(later)}) <= 120)
        self.assertEqual(t._retry_after({"Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"}), 0)
        for value in ("nan", "inf", "bad", "-5", "1e999999", "Wed, 99 Jan 2020 00:00:00 GMT"):
            self.assertIsNone(t._retry_after({"Retry-After": value}))

    def test_sdk_retries_defer_long_retry_after_without_early_retry(self):
        policy = t._BoundedRetryPolicy(retry_total=2)
        settings = policy.configure_retries({})
        response = SimpleNamespace(http_response=SimpleNamespace(
            status_code=429, headers={"Retry-After": "90"}))
        self.assertFalse(policy.is_retry(settings, response))
        self.assertEqual(policy.get_retry_after(response), 90)
        response.http_response.headers = {"Retry-After": "1"}
        self.assertTrue(policy.is_retry(settings, response))

    def test_invalid_table_payload_field_and_request_never_reach_client(self):
        for table, records, code in (
            ("Custom-EnergyObservations_CL", [{}], "InvalidTable"),
            ("Other_CL", [{}], "InvalidTable"),
            (TABLE, [], "InvalidRecords"), (TABLE, [1], "InvalidRecords"),
            (TABLE, [{"v": float("nan")}], "InvalidJson"),
            (TABLE, [{"v": float("inf")}], "InvalidJson"),
            (TABLE, [{1: "not-string-key"}], "InvalidJson"),
            (TABLE, [{"v": b"bytes"}], "InvalidJson"),
            (TABLE, [{"v": "x" * t.MAX_FIELD_BYTES}], "FieldTooLarge"),
            (TABLE, [{"v": "\u2603" * 11_000}], "FieldTooLarge"),
            (TABLE, [{"Payload": {"a": "x" * 30_000, "b": "x" * 30_000}}], "FieldTooLarge"),
            (TABLE, [{"v": "x" * 50_000}] * 5, "RequestTooLarge"),
            (TABLE, [{"v": "\u2603" * 9000}] * 4, "RequestTooLarge"),
        ):
            with self.subTest(code=code), self.assertRaises(t.DeliveryError) as failure:
                self.sender.send(table, records)
            self.assertEqual(failure.exception.code, code)
            self.assertFalse(failure.exception.retryable)
        self.client.upload.assert_not_called()

    def test_valid_request_is_one_actual_sdk_chunk(self):
        from azure.monitor.ingestion._helpers import _create_gzip_requests
        records = [{"v": "x" * 45_000, "EntityId": str(i)} for i in range(4)]
        self.sender.send(TABLE, records)
        self.assertEqual(len(list(_create_gzip_requests(records))), 1)

    def test_endpoint_and_rule_validation_precedes_injected_client_use(self):
        for endpoint in ("https://127.0.0.1", ENDPOINT + "/path", ENDPOINT + "?token=x",
                         ENDPOINT + ".evil.test", ENDPOINT.replace("https:", "http:")):
            with self.assertRaises(ValueError):
                t.IngestionSender(endpoint, RULE, client=self.client)
        for rule in ("resource/id", "dcr-invalid", RULE + "/streams", ""):
            with self.assertRaises(ValueError):
                t.IngestionSender(ENDPOINT, rule, client=self.client)
        self.client.upload.assert_not_called()

    def test_client_configuration_uses_bounded_sdk_policy_and_local_identity(self):
        with patch.dict(os.environ, HOST_MARKERS), patch.object(
                t, "DefaultAzureCredential") as identity, patch.object(t, "LogsIngestionClient") as client:
            t.IngestionSender(ENDPOINT, RULE)
            identity.assert_called_once_with()
            kwargs = client.call_args.kwargs
            self.assertIs(kwargs["credential"], identity.return_value)
            self.assertIsInstance(kwargs["retry_policy"], t._BoundedRetryPolicy)
            self.assertEqual(kwargs["retry_policy"].total_retries, 2)
            self.assertEqual(kwargs["connection_timeout"], 5)
            self.assertEqual(kwargs["read_timeout"], 15)

    def test_hosted_identity_is_managed_only(self):
        with patch.dict(os.environ, {**HOST_MARKERS, "WEBSITE_SITE_NAME": "v2-app"}), patch.object(
                t, "ManagedIdentityCredential") as managed, patch.object(
                t, "DefaultAzureCredential") as default, patch.object(t, "LogsIngestionClient"):
            t.IngestionSender(ENDPOINT, RULE)
            managed.assert_called_once_with()
            default.assert_not_called()
        with patch.dict(os.environ, {**HOST_MARKERS, "WEBSITE_SITE_NAME": "v2-app"}):
            with self.assertRaises(ValueError):
                t.BlobStateStore(ACCOUNT, "v2-state", MagicMock())


class CaptureTests(ScratchTest):
    def test_explicit_capture_persists_records_and_is_labeled_not_azure(self):
        sender = t.LocalCaptureSender(self.root)
        self.assertEqual(sender.delivery_mode, "local_capture")
        records = [{"EventId": "stable", "value": None, "stock": 0}]
        self.assertIsNone(sender.send(TABLE, records))
        t.LocalCaptureSender(self.root).send("EnergyInjectionAudit_CL", [{"Action": "reset"}])
        files = sorted(self.root.glob("*.json"))
        self.assertEqual(len(files), 2)
        captured = [json.loads(path.read_bytes()) for path in files]
        self.assertTrue(all(row["delivery_mode"] == "local_capture" for row in captured))
        self.assertEqual(next(row["records"] for row in captured if row["table"] == TABLE), records)

    def test_capture_failure_and_validation_are_visible(self):
        sender = t.LocalCaptureSender(self.root)
        with patch.object(t.os, "replace", side_effect=OSError("secret")):
            with self.assertRaisesRegex(t.DeliveryError, "^LocalCaptureUnavailable$"):
                sender.send(TABLE, [{}])
        self.assertEqual(list(self.root.glob("*.json")), [])
        with self.assertRaises(t.DeliveryError):
            sender.send(TABLE, [{"value": float("nan")}])

    def test_capture_cannot_be_selected_when_hosted(self):
        with patch.dict(os.environ, {"WEBSITE_SITE_NAME": "v2-app"}):
            with self.assertRaises(ValueError):
                t.LocalCaptureSender(self.root)


if __name__ == "__main__":
    unittest.main()
