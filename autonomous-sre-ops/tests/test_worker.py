import copy
import base64
import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from paramiko import Ed25519Key, SSHException

from operations.lifecycle import TARGETS
from operations.protocol import fingerprint
from operations.ssh import GuestError, parse_host_trust
from operations.worker import bootstrap, execute, process, receipt, verify


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "request_id": str(uuid4()), "command": "inspect", "resource_id": next(iter(TARGETS)),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.table = Mock()
        self.storage = Mock()
        self.handler = Mock(return_value={"fixture": True})

    def run_request(self):
        return execute(self.request, self.table, self.storage, {"inspect": self.handler})

    def prior_claim(self, *, seconds=1, status="running"):
        self.table.create_entity.side_effect = ResourceExistsError("fixture claim exists")
        self.table.get_entity.return_value = {
            "PartitionKey": "tool-requests", "RowKey": self.request["request_id"],
            "status": status, "fingerprint": fingerprint(self.request),
            "started_at": (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(),
        }
        self.storage.get_blob_client.return_value.download_blob.side_effect = ResourceNotFoundError("fixture receipt absent")

    def test_review_mode_cannot_be_overridden(self):
        with patch.dict("os.environ", {"ASRE_EXECUTION_MODE": "Autonomous"}), self.assertRaises(PermissionError):
            process()
        with patch.dict("os.environ", {"ASRE_EXECUTION_MODE": "Autonomous"}), self.assertRaises(PermissionError):
            verify()

    def test_only_an_actual_completed_handler_writes_completed(self):
        self.assertEqual(self.run_request(), "completed")
        self.handler.assert_called_once_with(self.request["resource_id"])
        upload = self.storage.get_blob_client.return_value.upload_blob
        persisted = json.loads(upload.call_args.args[0])
        self.assertEqual(persisted["fingerprint"], fingerprint(self.request))
        self.assertFalse(upload.call_args.kwargs["overwrite"])
        self.assertEqual(self.table.update_entity.call_args.args[0]["status"], "completed")

    def test_authoritative_handler_failure_is_an_explicit_failed_receipt(self):
        self.handler.side_effect = GuestError("fixture SSH failure")
        with self.assertLogs("asre-operations", "ERROR"):
            self.assertEqual(self.run_request(), "failed")
        persisted = json.loads(self.storage.get_blob_client.return_value.upload_blob.call_args.args[0])
        self.assertEqual(persisted["status"], "failed")
        self.assertNotIn("data", persisted)

    def test_live_duplicate_is_preserved_without_reexecution(self):
        self.prior_claim()
        with self.assertLogs("asre-operations", "WARNING"):
            self.assertEqual(self.run_request(), "running")
        self.handler.assert_not_called()
        self.storage.get_blob_client.return_value.upload_blob.assert_not_called()
        self.table.update_entity.assert_not_called()

    def test_orphan_claim_becomes_failed_never_reexecuted_or_completed(self):
        self.prior_claim(seconds=400)
        self.assertEqual(self.run_request(), "failed")
        self.handler.assert_not_called()
        result = json.loads(self.storage.get_blob_client.return_value.upload_blob.call_args.args[0])
        self.assertIn("without a receipt", result["error"])

    def test_receipt_survives_table_update_or_queue_ack_failure(self):
        self.prior_claim()
        blob = self.storage.get_blob_client.return_value
        blob.download_blob.side_effect = None
        blob.download_blob.return_value.readall.return_value = json.dumps(receipt(self.request, status="completed", data={"fixture": True})).encode()
        self.assertEqual(self.run_request(), "completed")
        self.handler.assert_not_called()
        blob.upload_blob.assert_not_called()
        self.assertEqual(self.table.update_entity.call_args.args[0]["status"], "completed")

    def test_conflicting_input_and_missing_terminal_receipt_fail_closed(self):
        self.prior_claim()
        changed = copy.deepcopy(self.request)
        changed["sent_at"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        self.request = changed
        with self.assertRaises(PermissionError):
            self.run_request()
        self.handler.assert_not_called()
        self.prior_claim(status="completed")
        with self.assertRaises(ValueError):
            self.run_request()

    def test_malformed_incomplete_duplicate_and_untrusted_host_keys_are_rejected(self):
        for known in ("", "\n\n\n", "10.42.0.10 ssh-ed25519 invalid", "outside.example ssh-ed25519 invalid"):
            with self.subTest(known=known), self.assertRaises((ValueError, SSHException)):
                parse_host_trust(known)

    def test_private_runtime_verification_requires_fresh_normal_independent_observations(self):
        import time

        healthy = {
            "health": {"status": "ok", "mode": "normal"},
            "observed_at": time.time(), "service": "ActiveState=active\n\nActiveState=active",
        }
        for observation in (
            {**healthy, "health": {"status": "ok", "mode": "memory"}},
            {**healthy, "observed_at": time.time() - 120},
            {**healthy, "service": "ActiveState=active"},
        ):
            with self.subTest(observation=observation), patch.dict("os.environ", {"ASRE_EXECUTION_MODE": "Review"}), \
                 patch("operations.worker.identity"), patch("operations.worker.SecretClient"), \
                 patch("operations.worker.Guest") as guest, patch("operations.worker.AzureReader"), \
                 patch("operations.worker.write_blob") as write, self.assertRaises(GuestError):
                guest.return_value.inspect.return_value = observation
                verify()
            write.assert_not_called()

    def test_valid_host_trust_requires_all_three_distinct_pinned_ed25519_hosts(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        public = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode()
        lines = [f"{address} {public}" for address in ("10.42.0.10", "10.42.0.11", "10.42.0.12")]
        self.assertEqual(len(parse_host_trust("\n".join(lines))), 3)
        for wrong in (lines[:2], lines + [lines[0]]):
            with self.assertRaises(ValueError):
                parse_host_trust("\n".join(wrong))

    def ssh_material(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        private = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption(),
        ).decode()
        public = key.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode()
        known = "\n".join(f"{host} {public}" for host in ("10.42.0.10", "10.42.0.11", "10.42.0.12"))
        return private, public, known

    def test_azure_local_bootstrap_generates_a_paramiko_compatible_key_without_disclosing_it(self):
        _, _, known = self.ssh_material()
        with patch.dict("os.environ", {"ASRE_SSH_KNOWN_HOSTS_B64": base64.b64encode(known.encode()).decode()}), \
             patch("operations.worker.identity"), patch("operations.worker.SecretClient") as client, \
             patch("builtins.print") as output:
            vault = client.return_value
            vault.get_secret.side_effect = ResourceNotFoundError("fixture secret absent")
            bootstrap()
        private = vault.set_secret.call_args_list[0].args[1]
        key = Ed25519Key.from_private_key(io.StringIO(private))
        output.assert_called_once_with("ASRE_RUNTIME_SSH_PUBLIC_KEY ssh-ed25519 " + key.get_base64(), flush=True)
        self.assertNotIn(private, str(output.call_args_list))
        vault.set_secret.assert_any_call("runner-ssh-known-hosts", known, content_type="text/plain")

    def test_bootstrap_reuses_the_existing_private_key_instead_of_rotating_it(self):
        private, public, known = self.ssh_material()
        with patch.dict("os.environ", {"ASRE_SSH_KNOWN_HOSTS_B64": base64.b64encode(known.encode()).decode()}), \
             patch("operations.worker.identity"), patch("operations.worker.SecretClient") as client, \
             patch("builtins.print") as output:
            vault = client.return_value
            vault.get_secret.return_value.value = private
            bootstrap()
        vault.set_secret.assert_called_once_with("runner-ssh-known-hosts", known, content_type="text/plain")
        output.assert_called_once_with("ASRE_RUNTIME_SSH_PUBLIC_KEY " + public, flush=True)

    def test_successful_private_verification_persists_evidence_and_reads_back_its_table_claim(self):
        import time

        healthy = {
            "health": {"status": "ok", "mode": "normal"},
            "observed_at": time.time(), "service": "ActiveState=active\n\nActiveState=active",
        }
        with patch.dict("os.environ", {"ASRE_EXECUTION_MODE": "Review"}), \
             patch("operations.worker.identity"), patch("operations.worker.SecretClient"), \
             patch("operations.worker.Guest") as guest, patch("operations.worker.AzureReader") as reader, \
             patch("operations.worker.blobs") as storage, patch("operations.worker.TableClient") as table, \
             patch("builtins.print") as output:
            guest.return_value.inspect.return_value = healthy
            reader.return_value.telemetry.return_value = {"fixture": "telemetry"}
            reader.return_value.collect.return_value = {"fixture": "authoritative jobs"}
            table.return_value.get_entity.return_value = {"status": "verified"}
            verify()
        self.assertEqual(guest.return_value.inspect.call_count, 3)
        upload = storage.return_value.get_blob_client.return_value.upload_blob
        evidence = json.loads(upload.call_args.args[0])
        self.assertEqual(len(evidence["targets"]), 3)
        self.assertFalse(upload.call_args.kwargs["overwrite"])
        row = table.return_value.create_entity.call_args.args[0]
        table.return_value.get_entity.assert_called_once_with("runtime-verification", row["RowKey"])
        self.assertIn(row["evidence_uri"], output.call_args.args[0])
