"""Round 3: deterministic boolean arbitration over a sealed reader run (synthetic only)."""

import copy
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p8_round3 import POLICIES, aggregates, arbitrate, build_round3, policy_document
from clinical_matcher.p8_safety import P8Error, check_seal, seal
from tests.test_p8_e1 import _partition, _register, _unknown_grid
from tests.test_p8_safety import access_fixture


QUESTIONS = load_question_catalog()["questions"]
BOOLEANS = [q for q in QUESTIONS if q["question_type"] == "boolean"
            and q["source_criterion_label"] != "med_decisions"]
NUMERIC = next(q for q in QUESTIONS if q["question_type"] == "numeric")
B_ABSENT, B_PRESENT = BOOLEANS[0], BOOLEANS[1]


class P8Round3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        root.chmod(0o700)
        manifest = access_fixture(root)
        self.ids = sorted(manifest["populations"]["validation"])
        evidence_rows = [{"patient_id": pid, "evidence": [
            {"evidence_id": f"{pid}-e1", "text": "Synthetic hemoglobin 11.2."}]} for pid in self.ids]
        manifest = _register(manifest, root, "validation.evidence",
                             _partition(manifest, "validation", "evidence", evidence_rows))
        gold_rows = _unknown_grid(self.ids)
        for row in gold_rows:
            if row["question_id"] == B_ABSENT["question_id"]:
                row.update(fact_status="absent", value=False)
            elif row["question_id"] == B_PRESENT["question_id"]:
                row.update(fact_status="present", value=True)
            elif row["question_id"] == NUMERIC["question_id"]:
                row.update(fact_status="present", value=11.2)
        manifest = _register(manifest, root, "validation.gold",
                             _partition(manifest, "validation", "gold", gold_rows))
        rule_rows = []
        for row in _unknown_grid(self.ids):
            row = dict(row, abstained=True, abstention_reason="missing_fact")
            if row["question_id"] == B_PRESENT["question_id"]:
                row.update(fact_status="present", value=True, abstained=False, abstention_reason=None,
                           evidence_ids=[f"{row['patient_id']}-e1"])
            rule_rows.append(row)
        rules_doc = {"prediction_set_version": "1.1.0", "split_name": "validation",
                     "benchmark_sha256": manifest["source_metadata"]["split"]["dataset"]["benchmark_sha256"],
                     "partition": "validation", "kind": "raw_predictions", "predictions": rule_rows}
        self.manifest = _register(manifest, root, "rules", seal(rules_doc))
        model_rows = []
        for row in _unknown_grid(self.ids):
            row = dict(row, abstained=True, abstention_reason="model_unknown",
                       trace_ids=["p8.reader.H.accepted"])
            cited = [f"{row['patient_id']}-e1"]
            if row["question_id"] in (B_ABSENT["question_id"], B_PRESENT["question_id"]):
                # The model over-calls present on both; only one is right.
                row.update(fact_status="present", value=True, abstained=False,
                           abstention_reason=None, evidence_ids=cited)
            elif row["question_id"] == NUMERIC["question_id"]:
                row.update(fact_status="present", value=11.2, abstained=False,
                           abstention_reason=None, evidence_ids=cited)
            model_rows.append(row)
        self.reader_run = seal({"p8_reader_run_version": "1.3.0", "arm": "H",
                                "prompt_version": "synthetic", "rows": model_rows,
                                "evaluation": {"metrics": {"typed_exact_match": 0.0},
                                               "unknown_count": 0}})

    def _rows(self, document, question):
        return [r for r in document["rows"] if r["question_id"] == question["question_id"]]

    def test_v1_vetoes_only_unsupported_boolean_presents_and_keeps_numerics(self):
        document = build_round3(self.manifest, self.reader_run, policy_id="V1", synthetic=True)
        check_seal(document)
        n = len(self.ids)
        self.assertTrue(all(r["fact_status"] == "absent" and r["value"] is False and r["evidence_ids"] == []
                            and r["trace_ids"] == ["p8.round3.V1.vetoed_present_without_rule_support"]
                            for r in self._rows(document, B_ABSENT)))
        self.assertTrue(all(r["fact_status"] == "present" for r in self._rows(document, B_PRESENT)))
        self.assertEqual(n, document["reason_counts"]["vetoed_present_without_rule_support"])
        self.assertEqual(n, document["source_counts"]["policy"])
        originals = {(r["patient_id"], r["question_id"]): r for r in self.reader_run["rows"]}
        for row in document["rows"]:
            if row["question_type"] == "numeric":
                self.assertEqual(originals[(row["patient_id"], row["question_id"])], row)
        summary = aggregates(document)
        # The veto turns the false positives into correct answers in the raw view, while the
        # safety view projects the uncited closed-world absents back to unknown.
        self.assertGreater(summary["raw_typed_exact_match"], summary["safety_typed_exact_match"])
        self.assertGreater(summary["safety_unknown"], summary["raw_unknown"])
        self.assertEqual("H", summary["base"]["arm"])

    def test_v2_takes_booleans_from_rules_under_a_closed_world(self):
        document = build_round3(self.manifest, self.reader_run, policy_id="V2", synthetic=True)
        n = len(self.ids)
        present = self._rows(document, B_PRESENT)
        self.assertTrue(all(r["fact_status"] == "present" and r["evidence_ids"] for r in present))
        self.assertEqual(n, document["source_counts"]["rules"])
        booleans = [r for r in document["rows"] if r["question_type"] == "boolean"]
        self.assertTrue(all(r["fact_status"] in ("present", "absent") for r in booleans))
        self.assertEqual(len(booleans) - n, document["reason_counts"]["closed_world_absent"])
        numerics = [r for r in document["rows"] if r["question_id"] == NUMERIC["question_id"]]
        self.assertTrue(all(r["value"] == 11.2 and r["trace_ids"] == ["p8.reader.H.accepted"] for r in numerics))

    def test_policies_are_declared_sealed_and_gold_free(self):
        self.assertEqual({"V1", "V2"}, set(POLICIES))
        document = policy_document()
        check_seal(document)
        self.assertEqual(0, document["model_calls"])
        import inspect
        self.assertNotIn("gold", inspect.signature(arbitrate).parameters)
        with self.assertRaises(P8Error):
            build_round3(self.manifest, self.reader_run, policy_id="V9", synthetic=True)
        tampered = copy.deepcopy(self.reader_run)
        tampered["arm"] = "tampered"
        with self.assertRaises(P8Error):
            build_round3(self.manifest, tampered, policy_id="V1", synthetic=True)


if __name__ == "__main__":
    unittest.main()
