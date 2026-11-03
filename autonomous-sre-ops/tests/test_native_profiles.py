"""Offline remaining-profile contracts, never live fault or delivery proof."""

import copy
import itertools
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from operations import agent_case as ac, servicenow as sn
from tests.test_agent_case import ALERT, COMMISSIONING, INVESTIGATION, NOW, Facade, Reader, Store, evidence
from tests.test_patch_case import native_fixture

POLICY = {"version": "1.0", "minimum_not_before": "2026-10-18T21:15:23.835Z"}
# The real incident: an ARG-sourced alert fired 30m05s after its reported window, and the
# failed job ended in between (window_end < job end <= fired_at).
STALE_BACKUP_ALERT = {"window_start": "2026-10-29T17:18:26Z", "window_end": "2026-10-29T17:28:26Z",
                      "fired_at": "2026-10-29T17:58:31Z"}
STALE_BACKUP_JOB_END = "2026-10-29T17:54:24Z"
# Live ARG returned one job's error_details in both of these key orders (~45/55).
ERROR_KEY_ORDER_A = (
    '[{"errorCode":400211,"recommendations":["Please correct the policy ..."],'
    '"errorString":"An invalid policy is configured on the VM which is preventing Snapshot operation.",'
    '"errorTitle":"UserErrorRequestDisallowedByPolicy"}]'
)
ERROR_KEY_ORDER_B = (
    '[{"recommendations":["Please correct the policy ..."],"errorCode":400211,'
    '"errorString":"An invalid policy is configured on the VM which is preventing Snapshot operation.",'
    '"errorTitle":"UserErrorRequestDisallowedByPolicy"}]'
)
ERROR_CANONICAL = (
    '[{"errorCode":400211,'
    '"errorString":"An invalid policy is configured on the VM which is preventing Snapshot operation.",'
    '"errorTitle":"UserErrorRequestDisallowedByPolicy","recommendations":["Please correct the policy ..."]}]'
)


def samples(profile):
    if profile.name == "backup-job":
        return [{
            "resource_id": ac.BACKUP_RESOURCE, "observed_at": "2026-10-18T22:01:30Z",
            "job_id": ac.BACKUP_VAULT + "/backupJobs/99999999-9999-4999-8999-999999999999",
            "status": "Failed", "protected_item_id": ac.BACKUP_ITEM, "error_details": "Offline fixture only.",
        }]
    if profile.name == "capacity-growth":
        start = ac.stamp("2026-10-18T21:56:00Z")
        return [{
            "resource_id": ac.RESOURCE, "observed_at": (start + timedelta(minutes=index)).isoformat(),
            "object_name": "Logical Disk", "counter_name": "% Used Space",
            "instance_name": "/srv/asre-demo", "value": 10.0 + index * 6,
        } for index in range(6)]
    return copy.deepcopy(Reader().disks[ALERT]["samples"])


class ProfileReader(Reader):
    def __init__(self, profile):
        super().__init__()
        self.thread["resource_id"] = profile.resource_id
        self.alerts[ALERT].update(rule_id=profile.rule_id, severity=profile.severity)
        self.data = {"alert_id": ALERT, "rule_id": profile.rule_id, "resource_id": profile.resource_id,
                     "query": profile.query, "samples": samples(profile)}

    def signal(self, identity):
        return copy.deepcopy(self.data)

    def disk(self, identity):
        return copy.deepcopy(self.data)


class GraphProfileReader(ProfileReader):
    """Fake native thread/alert reads, but the real AzureReader.signal over a patched ARG response."""
    signal = ac.AzureReader.signal

    def __init__(self, profile):
        super().__init__(profile)
        self.profile, self.credential = profile, object()


def graph_response(sample, error_details):
    return {"count": 1, "totalRecords": 1, "resultTruncated": "false",
            "data": [{**sample, "error_details": error_details, "resourceGroup": "rg-asre-backup-cus"}]}


class ProfileFacade(Facade):
    def __init__(self, store, profile):
        super().__init__(store)
        self.profile = profile

    def context(self, episode):
        result = super().context(episode)
        result.update(resource_id=self.profile.resource_id, mode=self.profile.mode,
                      signal_family=self.profile.signal_family)
        result["ci"]["sys_id"] = sn.CIS[sn.TARGETS[self.profile.resource_id.lower()]]
        return result


class NativeProfileTests(unittest.TestCase):
    def setup_profile(self, profile, reader=ProfileReader):
        self.profile = profile
        self.reader, self.store = reader(profile), Store()
        self.facade = ProfileFacade(self.store, profile)
        self.configuration = {**copy.deepcopy(COMMISSIONING), "profiles": list(ac.CASE_PROFILES)}
        self.tool = ac.CaseTool(
            self.reader, self.configuration, self.store,
            sn.CaseClient(lambda: "offline-fixture", transport=self.facade), lambda: NOW,
            policy=POLICY, profile=profile.name,
        )

    def payload(self, **times):
        result = evidence(**times)
        result.update(resource_id=self.profile.resource_id, mode=self.profile.mode,
                      signal_family=self.profile.signal_family)
        result["evidence"][1] = {
            "source": self.profile.telemetry_source, "observed_at": self.reader.data["samples"][-1]["observed_at"],
            "reference": self.profile.telemetry_reference, "finding": "Offline profile-native observation only.",
        }
        return result

    def create(self, payload=None):
        return self.tool.run("create", INVESTIGATION, ALERT,
                             json.dumps(self.payload() if payload is None else payload), "Offline findings; no recovery.")

    def stale_backup(self, observed_at=STALE_BACKUP_JOB_END, *, reader=ProfileReader, **alert):
        """Backup profile on the stale ARG window; returns the matching create payload."""
        self.setup_profile(ac.BACKUP_PROFILE, reader)
        times = {**STALE_BACKUP_ALERT, **alert}
        self.reader.alerts[ALERT].update(times)
        self.reader.data["samples"][0]["observed_at"] = observed_at
        self.reader.thread["created_at"] = times["fired_at"]
        self.tool.clock = lambda: ac.stamp(times["fired_at"]) + timedelta(minutes=1)
        return self.payload(detected=times["fired_at"])

    def test_each_profile_creates_one_case_note_and_reconciles_without_rewrite(self):
        for profile in (ac.NOISE_PROFILE, ac.GROWTH_PROFILE, ac.BACKUP_PROFILE):
            with self.subTest(profile=profile.name):
                self.setup_profile(profile)
                before = self.tool.run("read")
                self.assertEqual(before["binding"]["rule_id"], profile.rule_id)
                self.assertEqual(self.facade.posts, [])
                self.assertEqual(self.create()["status"], "persisted")
                self.assertEqual(self.create()["status"], "persisted")
                posts = len(self.facade.posts)
                result = self.tool.run("reconcile", INVESTIGATION, ALERT)
                self.assertEqual(result["status"], "persisted")
                self.assertEqual(len(self.facade.posts), posts)
                self.assertEqual(self.facade.state["case_count"], 1)
                self.assertEqual(self.facade.state["note_count"], 1)

    def test_native_decoder_binds_exact_profile_severity_rule_and_resource(self):
        for profile in (ac.NOISE_PROFILE, ac.GROWTH_PROFILE, ac.BACKUP_PROFILE):
            raw = native_fixture()
            raw["incidentSource"]["incidentId"] = profile.rule_id
            raw["incidentDetails"]["incidentPriority"] = profile.severity
            with self.subTest(profile=profile.name):
                self.assertEqual(ac.decode_native_incident(raw, profile)["resource_id"], profile.resource_id)
                with self.assertRaises(ac.CaseToolError):
                    ac.decode_native_incident(raw)
                raw["incidentDetails"]["incidentPriority"] = "Sev0"
                with self.assertRaises(ac.CaseToolError):
                    ac.decode_native_incident(raw, profile)

    def test_growth_returns_two_endpoints_and_a_relative_percentage_forecast(self):
        self.setup_profile(ac.GROWTH_PROFILE)
        result = self.tool.run("read")
        self.assertEqual(len(result["signal"]["samples"]), 2)
        self.assertEqual(result["signal"]["sample_count"], 6)
        self.assertEqual(result["signal"]["forecast"], {
            "baseline_seconds": 300.0, "threshold_percent": 80,
            "seconds_from_last_sample_to_threshold": 400.0,
        })

    def test_growth_rejects_sparse_duplicate_gapped_reset_flat_and_breached_series(self):
        original = samples(ac.GROWTH_PROFILE)
        mutations = (
            lambda rows: rows.pop(),
            lambda rows: rows[2].update(observed_at=rows[1]["observed_at"]),
            lambda rows: rows[0].update(observed_at="2026-10-18T21:53:00Z"),
            lambda rows: rows[2].update(value=2),
            lambda rows: [row.update(value=30) for row in rows],
            lambda rows: rows[-1].update(value=80),
            lambda rows: rows[-1].update(observed_at="2026-10-18T22:02:01Z"),  # after window_end; only backup is relaxed
            lambda rows: rows[-1].update(value=True),
            lambda rows: rows[-1].update(value=float("nan")),
            lambda rows: [row.update(value=index / 100) for index, row in enumerate(rows)],
        )
        for mutation in mutations:
            self.setup_profile(ac.GROWTH_PROFILE)
            self.reader.data["samples"] = copy.deepcopy(original)
            mutation(self.reader.data["samples"])
            with self.subTest(mutation=mutation), self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.facade.posts, [])
            self.assertEqual(self.store.creations, 0)

    def test_backup_requires_terminal_failed_backup_for_exact_vault_and_item(self):
        for field, value in (
            ("status", "Completed"), ("status", "InProgress"), ("status", "Cancelled"),
            ("protected_item_id", ac.BACKUP_ITEM.replace("vm-asre", "vm-other")),
            ("job_id", ac.BACKUP_VAULT + "/backupJobs/not-a-job"),
            ("job_id", ac.BACKUP_VAULT + "/backupJobs/nested/99999999-9999-4999-8999-999999999999"),
            ("resource_id", ac.RESOURCE), ("observed_at", "2026-10-18T22:04:00Z"),
        ):
            self.setup_profile(ac.BACKUP_PROFILE)
            self.reader.data["samples"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises((ac.CaseToolError, ValueError)):
                self.create()
            self.assertEqual(self.store.creations, 0)

    def test_native_profile_cannot_claim_another_mode_family_or_repair(self):
        for profile in (ac.NOISE_PROFILE, ac.GROWTH_PROFILE, ac.BACKUP_PROFILE):
            self.setup_profile(profile)
            for field, value in (("mode", "reactive" if profile.mode == "proactive" else "proactive"),
                                 ("signal_family", "patch-installation")):
                payload = self.payload()
                payload[field] = value
                with self.subTest(profile=profile.name, field=field), self.assertRaises(ac.CaseToolError):
                    self.create(payload)
            self.assertEqual(self.facade.posts, [])

    def test_backup_native_graph_reader_is_complete_bound_and_uses_exact_item(self):
        profile = ac.BACKUP_PROFILE
        reader = ac.AzureReader(object(), profile=profile)
        alert = Reader().alerts[ALERT]
        response = {"count": 1, "totalRecords": 1, "resultTruncated": "false",
                    "data": [{**samples(profile)[0], "resourceGroup": "rg-asre-backup-cus"}]}
        with patch.object(reader, "alert", return_value=alert), patch.object(ac, "_azure_json", return_value=response) as read:
            self.assertEqual(reader.signal(ALERT)["samples"], samples(profile))
            query = read.call_args.args[3]["query"]
            self.assertIn(ac.BACKUP_ITEM, query)
            self.assertIn("properties.status) == 'Failed'", query)
            self.assertNotIn("ago(10m)", query)
        for field, value in (("count", 2), ("totalRecords", 2), ("resultTruncated", "true"), ("$skipToken", "more")):
            wrong = {**response, field: value}
            with patch.object(reader, "alert", return_value=alert), patch.object(ac, "_azure_json", return_value=wrong), \
                    self.subTest(field=field), self.assertRaises(ac.CaseToolError):
                reader.signal(ALERT)

    def test_backup_reader_bounds_evidence_to_window_start_and_firing_not_stale_window_end(self):
        profile = ac.BACKUP_PROFILE
        reader = ac.AzureReader(object(), profile=profile)
        job = {**samples(profile)[0], "observed_at": STALE_BACKUP_JOB_END}
        response = {"count": 1, "totalRecords": 1, "resultTruncated": "false",
                    "data": [{**job, "resourceGroup": "rg-asre-backup-cus"}]}
        for fired_at, upper in (("2026-10-29T17:58:31Z", "2026-10-29T17:58:31+00:00"),
                                ("2026-10-29T18:28:26Z", "2026-10-29T18:28:26+00:00")):  # lag exactly 3600 s
            alert = {**Reader().alerts[ALERT], **STALE_BACKUP_ALERT, "fired_at": fired_at}
            with patch.object(reader, "alert", return_value=alert), \
                    patch.object(ac, "_azure_json", return_value=response) as read, self.subTest(fired_at=fired_at):
                self.assertEqual(reader.signal(ALERT)["samples"], [job])
                query = read.call_args.args[3]["query"]
                self.assertIn("| where observed_at > datetime(2026-10-29T17:18:26+00:00) |", query)
                self.assertTrue(query.endswith(f"| where observed_at <= datetime({upper}) | take 101"), query)
                self.assertNotIn("17:28:26", query)

    def test_backup_reader_rejects_inverted_or_over_lagged_evaluation_before_querying(self):
        reader = ac.AzureReader(object(), profile=ac.BACKUP_PROFILE)
        for changes in (
            {"fired_at": "2026-10-29T18:28:27Z"},      # 3601 s after window_end
            {"fired_at": "2026-10-29T17:28:25Z"},      # fired before window_end
            {"window_start": "2026-10-29T17:28:26Z"},  # empty window
        ):
            alert = {**Reader().alerts[ALERT], **STALE_BACKUP_ALERT, **changes}
            with patch.object(reader, "alert", return_value=alert), patch.object(ac, "_azure_json") as read, \
                    self.subTest(changes=changes), self.assertRaises(ac.CaseToolError):
                reader.signal(ALERT)
            read.assert_not_called()

    def test_backup_stale_window_job_persists_one_case_and_frozen_proof_reconciles_without_rewrite(self):
        payload = self.stale_backup()
        self.assertEqual(self.tool.run("read")["signal"]["samples"][0]["observed_at"], STALE_BACKUP_JOB_END)
        self.assertEqual(self.facade.posts, [])
        self.assertEqual(self.create(payload)["status"], "persisted")
        frozen = self.tool.load(ac.episode_for(INVESTIGATION))  # validate_intent re-runs the same proof rule
        self.assertEqual(frozen["proof"]["signal"]["samples"][0]["observed_at"], STALE_BACKUP_JOB_END)
        self.assertEqual(self.create(payload)["status"], "persisted")
        posts = len(self.facade.posts)
        self.assertEqual(self.tool.run("reconcile", INVESTIGATION, ALERT)["status"], "persisted")
        self.assertEqual(len(self.facade.posts), posts)
        self.assertEqual((self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1))

    def test_backup_stale_window_evidence_never_precedes_window_start_follows_firing_or_lags_over_an_hour(self):
        for observed_at, alert in (
            ("2026-10-29T17:18:25Z", {}),                                  # ended before window_start
            ("2026-10-29T17:58:32Z", {}),                                  # ended after the alert fired
            ("2026-10-29T17:20:00Z", {"fired_at": "2026-10-29T18:28:27Z"}),  # in-window job, lag 3601 s
        ):
            payload = self.stale_backup(observed_at, **alert)
            with self.subTest(observed_at=observed_at, alert=alert), self.assertRaises(ac.CaseToolError):
                self.create(payload)
            self.assertEqual((self.facade.posts, self.store.creations), ([], 0))

    def test_backup_reader_error_details_do_not_depend_on_arg_json_key_order(self):
        self.assertNotEqual(ERROR_KEY_ORDER_A, ERROR_KEY_ORDER_B)
        profile = ac.BACKUP_PROFILE
        reader = ac.AzureReader(object(), profile=profile)
        alert = {**Reader().alerts[ALERT], **STALE_BACKUP_ALERT}
        job = {**samples(profile)[0], "observed_at": STALE_BACKUP_JOB_END}
        read = []
        for details in (ERROR_KEY_ORDER_A, ERROR_KEY_ORDER_B):
            with patch.object(reader, "alert", return_value=alert), \
                    patch.object(ac, "_azure_json", return_value=graph_response(job, details)):
                read.append(reader.signal(ALERT)["samples"])
        self.assertEqual(read[0], read[1])
        self.assertEqual(ac.wire(read[0]), ac.wire(read[1]))
        self.assertEqual(read[0][0]["error_details"], ERROR_CANONICAL)

    def test_backup_create_survives_arg_key_order_flipping_between_freeze_and_write_time_reread(self):
        for order in ((ERROR_KEY_ORDER_A, ERROR_KEY_ORDER_B), (ERROR_KEY_ORDER_B, ERROR_KEY_ORDER_A)):
            payload = self.stale_backup(reader=GraphProfileReader)
            job, served = self.reader.data["samples"][0], itertools.cycle(order)
            with self.subTest(first=order[0][:16] + order[0][-8:]), patch.object(
                ac, "_azure_json", side_effect=lambda *args: graph_response(job, next(served)),
            ) as read:
                self.assertEqual(self.create(payload)["status"], "persisted")
                self.assertEqual(self.create(payload)["status"], "persisted")
                posts = len(self.facade.posts)
                self.assertEqual(self.tool.run("reconcile", INVESTIGATION, ALERT)["status"], "persisted")
                self.assertEqual(len(self.facade.posts), posts)
            self.assertGreater(read.call_count, 2)
            self.assertEqual((self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1))

    def test_error_details_canonicalization_leaves_unprovable_values_byte_identical(self):
        for raw in (
            None, 7,
            '[{"errorCode":400211,"recommendations":["cut at the 1600-char substring',  # truncated JSON
            "", '{"a":1,"a":2}', '[{"x":{"b":1,"b":1}}]',  # duplicate keys, also nested
            '{"a":NaN}', "[Infinity]", "[-Infinity]",
            '"text"', "12", "null", "true",  # scalar JSON
            '{"a":"\\ud800"}',  # canonical text cannot encode a lone surrogate
            "[" * 5000 + "]" * 5000,  # RecursionError
            "[" + ",".join(["1e5"] * 300) + "]",  # raw 1201 chars; canonical 100000.0 forms exceed 1600
        ):
            with self.subTest(raw=str(raw)[:40]):
                result = ac.canonical_error_details(raw)
                self.assertEqual(result, raw)
                self.assertIs(type(result), type(raw))
        self.assertEqual(ac.canonical_error_details("[1e5, 2]"), "[100000.0,2]")  # control: same shape, in bound

    def test_only_the_backup_read_states_its_evidence_window_through_the_firing(self):
        self.stale_backup()
        result = self.tool.run("read")
        self.assertEqual(result["signal"]["evidence_window"],
                         {"start": STALE_BACKUP_ALERT["window_start"], "end": STALE_BACKUP_ALERT["fired_at"]})
        self.assertEqual(set(result["signal"]), {"samples", "sample_count", "reference", "evidence_window"})
        self.assertEqual(self.facade.posts, [])
        for profile in (ac.DISK_PROFILE, ac.NOISE_PROFILE, ac.GROWTH_PROFILE):
            with self.subTest(profile=profile.name):
                self.setup_profile(profile)
                self.assertNotIn("evidence_window", self.tool.run("read")[self.tool.proof_key])

    def test_new_profile_requires_protected_release_selection(self):
        for profile in (ac.NOISE_PROFILE, ac.GROWTH_PROFILE, ac.BACKUP_PROFILE):
            with self.subTest(profile=profile.name), self.assertRaises(ac.CaseToolError):
                ac.CaseTool(Reader(), COMMISSIONING, Store(), object(), policy=POLICY, profile=profile.name)


if __name__ == "__main__":
    unittest.main()
