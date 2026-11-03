import unittest

from native_acceptance import independent_readback_unchanged, independently_prohibited_update_rejected, prohibited_update_rejected, require_native_journal


CASE = "a" * 32
CORRELATION = "00000036-000a-4000-9000-000000000036"
JOURNAL = "b" * 32


def journal():
    return {
        "accepted": True, "sysId": JOURNAL, "caseSysId": CASE,
        "correlation": CORRELATION, "matchingJournalCount": 1,
        "table": "incident", "element": "work_notes",
        "author": "copilot.integration", "valueMatches": True,
    }


def snapshot():
    return {
        "sys_id": CASE, "number": "INC0900002", "state": "1",
        "assignment_group": "c" * 32, "cmdb_ci": "d" * 32,
        "description": "Synthetic test only.", "comments": "",
        "active": "true", "sys_mod_count": "2",
    }


class NativeAcceptanceTests(unittest.TestCase):
    def independent(self, *, readback_only=False, **overrides):
        complete = snapshot()
        masked = {key: value for key, value in complete.items() if key != "cmdb_ci"}
        rows = [{
            "sys_id": JOURNAL, "name": "incident", "element": "work_notes",
            "element_id": CASE, "sys_created_by": "copilot.integration",
            "sys_created_on": "2026-10-17 02:43:01", "value": "Synthetic verified native journal.",
        }]
        inputs = {
            "status": 200, "response": {"result": dict(masked)}, "read_status": 200,
            "after": {"result": dict(masked)}, "native_baseline": masked,
            "canonical_before": complete, "canonical_after": dict(complete),
            "journals_before": rows, "journals_after": [dict(row) for row in rows],
            "proof": journal(), "correlation": CORRELATION,
            "context_before": {
                "case_sys_id": CASE, "ci": complete["cmdb_ci"], "assignment_group": complete["assignment_group"],
                "state": complete["state"], "description": complete["description"], "revision": complete["sys_mod_count"],
            },
        }
        inputs["context_after"] = dict(inputs["context_before"])
        inputs.update(overrides)
        if readback_only:
            inputs.pop("status")
            inputs.pop("response")
            return independent_readback_unchanged(**inputs)
        return independently_prohibited_update_rejected(**inputs)

    def test_unknown_transport_can_reconcile_state_but_cannot_prove_denial(self):
        self.assertTrue(self.independent(readback_only=True, status=None, response=None))
        self.assertFalse(self.independent(status=None, response=None))
        self.assertFalse(self.independent(readback_only=True, canonical_after={**snapshot(), "state": "2"}))
        self.assertFalse(self.independent(readback_only=True, context_after={}))

    def test_masked_ci_requires_independent_complete_record_and_journals(self):
        self.assertTrue(self.independent())
        self.assertTrue(self.independent(status=403, response={"error": {}}))
        for missing in snapshot():
            incomplete = {key: value for key, value in snapshot().items() if key != missing}
            with self.subTest(missing=missing):
                self.assertFalse(self.independent(canonical_before=incomplete, canonical_after=incomplete))

    def test_canonical_hidden_ci_or_revision_changes_cannot_be_masked(self):
        for field in snapshot():
            with self.subTest(field=field):
                self.assertFalse(self.independent(canonical_after={**snapshot(), field: "changed"}))
        self.assertFalse(self.independent(proof={}))
        self.assertFalse(self.independent(read_status=503))
        self.assertFalse(self.independent(status=500))
        self.assertFalse(self.independent(context_before={}))
        self.assertFalse(self.independent(context_after={"case_sys_id": CASE, "revision": "3"}))

    def test_new_customer_journal_is_rejected_even_if_record_revision_did_not_change(self):
        addition = {
            "sys_id": "e" * 32, "name": "incident", "element": "comments",
            "element_id": CASE, "sys_created_by": "copilot.integration",
            "sys_created_on": "2026-10-17 02:43:01", "value": "Must not persist.",
        }
        existing = {
            "sys_id": JOURNAL, "name": "incident", "element": "work_notes",
            "element_id": CASE, "sys_created_by": "copilot.integration",
            "sys_created_on": "2026-10-17 02:43:01", "value": "Synthetic verified native journal.",
        }
        self.assertFalse(self.independent(journals_after=[existing, addition]))
        for rows in [[], [{}], [addition, dict(addition)]]:
            with self.subTest(rows=rows):
                self.assertFalse(self.independent(journals_before=rows, journals_after=rows))

    def test_native_partial_or_mismatched_acknowledgement_still_fails_independent_checks(self):
        self.assertFalse(self.independent(response={"result": {"sys_id": CASE}}))
        self.assertFalse(self.independent(after={"result": {"sys_id": CASE}}))
        self.assertFalse(self.independent(native_baseline={}))
        self.assertFalse(self.independent(native_baseline={**snapshot(), "state": "2"}))

    def test_http_200_without_a_journal_never_accepts_a_native_write(self):
        for proof in [None, {}, {"status": 200, "result": {"sys_id": CASE}},
                      {"nativeMatches": 0, "completeListVerified": True}]:
            with self.subTest(proof=proof), self.assertRaisesRegex(ValueError, "not journal persistence"):
                require_native_journal(proof, CASE, CORRELATION)

    def test_exact_independent_native_journal_is_required(self):
        self.assertEqual(require_native_journal(journal(), CASE, CORRELATION), JOURNAL)
        for field, value in [
            ("accepted", False), ("accepted", 1), ("caseSysId", "c" * 32),
            ("correlation", "other"), ("matchingJournalCount", 0),
            ("matchingJournalCount", 2), ("matchingJournalCount", True),
            ("author", "admin"), ("table", "task"), ("element", "comments"),
            ("valueMatches", False), ("sysId", "not-a-journal"),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                require_native_journal({**journal(), field: value}, CASE, CORRELATION)

    def test_expected_context_must_be_canonical(self):
        for case, correlation in [(CASE.upper(), CORRELATION), (CASE, None),
                                  (CASE, CORRELATION.upper()), (CASE, "not-a-uuid")]:
            with self.subTest(case=case, correlation=correlation), self.assertRaises(ValueError):
                require_native_journal(journal(), case, correlation)

    def test_documented_field_acl_noop_requires_exact_response_and_readback(self):
        baseline = snapshot()
        unchanged = {"result": dict(baseline)}
        self.assertTrue(prohibited_update_rejected(200, unchanged, 200, unchanged, baseline))
        for status in [400, 403, 409]:
            with self.subTest(status=status):
                self.assertTrue(prohibited_update_rejected(status, {"error": {}}, 200, unchanged, baseline))
        for response in [{}, {"result": {"sys_id": CASE}}, {"result": {**baseline, "state": "2"}}]:
            with self.subTest(response=response):
                self.assertFalse(prohibited_update_rejected(200, response, 200, unchanged, baseline))

    def test_any_tested_field_or_revision_change_fails_negative_acceptance(self):
        baseline = snapshot()
        unchanged = {"result": dict(baseline)}
        for field in ["state", "assignment_group", "cmdb_ci", "description", "comments", "active", "sys_mod_count"]:
            for status in [200, 403]:
                changed = {"result": {**baseline, field: "changed-fixture"}}
                with self.subTest(field=field, status=status):
                    self.assertFalse(prohibited_update_rejected(status, unchanged, 200, changed, baseline))

    def test_partial_snapshot_or_failed_readback_cannot_prove_denial(self):
        baseline = snapshot()
        unchanged = {"result": dict(baseline)}
        for field in baseline:
            partial = {key: value for key, value in baseline.items() if key != field}
            with self.subTest(missing=field):
                self.assertFalse(prohibited_update_rejected(200, {"result": partial}, 200, {"result": partial}, partial))
        self.assertFalse(prohibited_update_rejected(200, unchanged, 503, unchanged, baseline))
        self.assertFalse(prohibited_update_rejected(500, unchanged, 200, unchanged, baseline))
        self.assertFalse(prohibited_update_rejected(200, unchanged, 200, None, baseline))


if __name__ == "__main__":
    unittest.main()
