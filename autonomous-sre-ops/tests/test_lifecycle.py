import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

from operations import context
from operations.lifecycle import (
    ALLOWED_ACTIONS, EVIDENCE_HOST, SUBSCRIPTION, TARGETS, AzureTable, CaseProof, ConflictError,
    OperationLedger, Repair, normalized_target,
)

CTX = context.load()


class MemoryTable:
    """Atomic test double, never selected by the production Azure adapter."""

    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def get(self, partition, key):
        with self.lock:
            value, version = self.rows[(partition, key)]
            return copy.deepcopy(value), str(version)

    def create_batch(self, entities):
        with self.lock:
            keys = [(value["PartitionKey"], value["RowKey"]) for value in entities]
            if any(key in self.rows for key in keys):
                raise ConflictError("Already claimed")
            for key, value in zip(keys, entities):
                self.rows[key] = (copy.deepcopy(value), 1)

    def replace(self, entity, etag):
        with self.lock:
            key = (entity["PartitionKey"], entity["RowKey"])
            if str(self.rows[key][1]) != etag:
                raise ConflictError("Stale ETag")
            self.rows[key] = (copy.deepcopy(entity), int(etag) + 1)

    def finish(self, entity, etag, lock, lock_etag):
        with self.lock:
            key = (entity["PartitionKey"], entity["RowKey"])
            lock_key = (lock["PartitionKey"], lock["RowKey"])
            if str(self.rows[key][1]) != etag or str(self.rows[lock_key][1]) != lock_etag:
                raise ConflictError("Stale transaction")
            self.rows[key] = (copy.deepcopy(entity), int(etag) + 1)
            del self.rows[lock_key]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.table = MemoryTable()
        self.ledger = OperationLedger(self.table)
        self.target = next(key for key, role in TARGETS.items() if role == "linux")
        self.case = CaseProof(str(uuid4()), self.target, "a" * 32, "INC1000001", True)
        self.repair = Repair(str(uuid4()), "recover_workload", self.case)

    def submitted(self):
        entity, _ = self.ledger.reserve(self.repair, mode="Autonomous")
        return self.ledger.submitted(entity["PartitionKey"], self.repair.request_id, "fixture-job-1")

    def finish(self, entity, **overrides):
        arguments = {
            "status": "verified", "evidence_operation_id": "fixture-job-1",
            "observed_at": datetime.now(timezone.utc) + timedelta(seconds=1),
            "recovery_owner": "agent", "evidence_uri": f"https://{EVIDENCE_HOST}/evidence/fixture.json",
            "verification_checks": {"service_healthy": True, "resource_below_threshold": True, "stable_observation_window": True},
        }
        arguments.update(overrides)
        return self.ledger.finish(entity["PartitionKey"], self.repair.request_id, **arguments)

    def test_review_is_nonexecuting(self):
        for mode in ("Review", "ReadOnly", "invalid"):
            with self.subTest(mode=mode), self.assertRaises(PermissionError):
                self.ledger.reserve(self.repair, mode=mode)
        self.assertFalse(self.table.rows)

    def test_case_failure_blocks_execution(self):
        case = CaseProof(self.case.episode_id, self.target, self.case.sys_id, self.case.record_number, False)
        with self.assertRaises(PermissionError):
            self.ledger.reserve(Repair(str(uuid4()), "recover_workload", case), mode="Autonomous")

    def test_idempotent_concurrent_notifications_claim_once(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.ledger.reserve(self.repair, mode="Autonomous")[1], range(16)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.table.rows), 3)

    def test_target_lock_blocks_a_second_episode(self):
        self.ledger.reserve(self.repair, mode="Autonomous")
        other_case = CaseProof(str(uuid4()), self.target, "b" * 32, "INC1000002", True)
        with self.assertRaises(ConflictError):
            self.ledger.reserve(Repair(str(uuid4()), "recover_workload", other_case), mode="Autonomous")

    def test_ambiguous_submission_retains_lock_and_budget(self):
        entity, _ = self.ledger.reserve(self.repair, mode="Autonomous")
        self.ledger.uncertain_submission(entity["PartitionKey"], self.repair.request_id, "response connection lost")
        retry, should_execute = self.ledger.reserve(self.repair, mode="Autonomous")
        self.assertFalse(should_execute)
        self.assertEqual(retry["status"], "submission_unknown")
        self.assertIn((entity["PartitionKey"], "target-lock"), self.table.rows)

    def test_job_submission_is_not_success(self):
        entity = self.submitted()
        self.assertEqual(entity["status"], "submitted")
        self.assertIn((entity["PartitionKey"], "target-lock"), self.table.rows)

    def test_stale_wrong_job_watchdog_and_missing_checks_cannot_resolve(self):
        entity = self.submitted()
        for override in (
            {"evidence_operation_id": "different-job"},
            {"observed_at": datetime.now(timezone.utc) - timedelta(hours=1)},
            {"recovery_owner": "watchdog"},
            {"verification_checks": {"service_healthy": True}},
            {"evidence_uri": f"https://{EVIDENCE_HOST}/evidence/fixture.json?sig=forbidden"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.finish(entity, **override)

    def test_verified_repair_releases_lock_but_not_repair_budget(self):
        entity = self.submitted()
        result = self.finish(entity)
        self.assertEqual(result["status"], "verified")
        self.assertNotIn((entity["PartitionKey"], "target-lock"), self.table.rows)
        with self.assertRaises(ConflictError):
            self.ledger.reserve(Repair(str(uuid4()), "recover_workload", self.case), mode="Autonomous")

    def test_failed_repair_has_explicit_reason(self):
        entity = self.submitted()
        with self.assertRaises(ValueError):
            self.finish(entity, status="failed")
        result = self.finish(entity, status="failed", failure_reason="Fresh service probe remained unhealthy")
        self.assertEqual(result["status"], "failed")

    def test_scope_and_action_allowlists(self):
        with self.assertRaises(PermissionError):
            normalized_target(self.target.replace("linux-cus", "production-cus"))
        for target, role in TARGETS.items():
            case = CaseProof(str(uuid4()), target, "c" * 32, "TASK10001", True)
            for action in ALLOWED_ACTIONS[role]:
                Repair(str(uuid4()), action, case).entity()
            with self.assertRaises(PermissionError):
                Repair(str(uuid4()), "run_shell", case).entity()

    def test_scope_values_come_from_the_deployment_context(self):
        self.assertEqual(SUBSCRIPTION, CTX.subscription_id)
        self.assertIs(type(TARGETS), dict)
        self.assertEqual(list(TARGETS.items()), list(CTX.roles_by_resource_id().items()))
        self.assertEqual(list(TARGETS.values()), ["linux", "patch", "backup"])
        for resource_id in TARGETS:
            self.assertTrue(resource_id.startswith(f"/subscriptions/{CTX.subscription_id}/resourcegroups/"))
            self.assertEqual(normalized_target(resource_id), resource_id)
        self.assertEqual(EVIDENCE_HOST, urlparse(CTX.storage.blob_endpoint).hostname)
        self.assertTrue(EVIDENCE_HOST.startswith(CTX.storage.account_name + "."))
        elsewhere = "/subscriptions/{}/resourcegroups/rg/providers/microsoft.compute/virtualmachines/vm"
        with self.assertRaises(PermissionError):
            normalized_target(elsewhere.format(str(uuid4())))

    def test_the_operations_table_accepts_only_the_context_storage_endpoint(self):
        endpoint = CTX.storage.table_endpoint
        other = endpoint.replace(CTX.storage.account_name, "storageofanotherdeployment")
        self.assertNotEqual(other, endpoint)
        with patch("azure.data.tables.TableClient") as client, patch("azure.identity.ManagedIdentityCredential") as credential:
            for accepted in (endpoint, endpoint + "/"):
                with self.subTest(accepted=accepted):
                    AzureTable(accepted, "00000000-0000-4000-8000-000000000000")
                    self.assertEqual(client.call_args.kwargs["endpoint"], accepted)
                    self.assertEqual(client.call_args.kwargs["table_name"], "Operations")
                    self.assertIs(client.call_args.kwargs["credential"], credential.return_value)
            client.reset_mock()
            for rejected in (other, other + "/", endpoint + "//", endpoint + "/x", endpoint + "?sig=x", endpoint + ":443",
                             endpoint.replace("https://", "http://", 1), endpoint.upper(), endpoint.replace(".table.", ".blob."),
                             "", None):
                with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                    AzureTable(rejected, "00000000-0000-4000-8000-000000000000")
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
