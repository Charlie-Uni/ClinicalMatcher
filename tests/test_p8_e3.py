import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p8_e3 import aggregates, build_e3_combination
from clinical_matcher.p8_safety import P8Error, seal
from tests.test_p8_e1 import _partition, _register, _unknown_grid
from tests.test_p8_safety import access_fixture


QUESTIONS = load_question_catalog()["questions"]


def _numeric_question():
    return next(q for q in QUESTIONS if q["question_type"] == "numeric")


class P8E3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        root.chmod(0o700)
        manifest = access_fixture(root)
        self.ids = sorted(manifest["populations"]["validation"])
        evidence_rows = [{"patient_id": pid, "evidence": [
            {"evidence_id": f"{pid}-e1", "text": "Synthetic hemoglobin 11.2."}]}
            for pid in self.ids]
        manifest = _register(manifest, root, "validation.evidence",
                             _partition(manifest, "validation", "evidence", evidence_rows))
        gold_rows = _unknown_grid(self.ids)
        numeric = _numeric_question()
        # Gold knows the numeric fact for every patient; the v2 arm abstains on it.
        for row in gold_rows:
            if row["question_id"] == numeric["question_id"]:
                row.update(fact_status="present", value=11.2)
        manifest = _register(manifest, root, "validation.gold",
                             _partition(manifest, "validation", "gold", gold_rows))
        rule_rows = []
        for row in _unknown_grid(self.ids):
            row = dict(row, abstained=True, abstention_reason="missing_fact")
            if row["question_id"] == numeric["question_id"]:
                row.update(fact_status="present", value=11.2, abstained=False,
                           abstention_reason=None,
                           evidence_ids=[f"{row['patient_id']}-e1"])
            rule_rows.append(row)
        rules_doc = {"prediction_set_version": "1.1.0", "split_name": "validation",
                     "benchmark_sha256": manifest["source_metadata"]["split"]["dataset"]["benchmark_sha256"],
                     "partition": "validation", "kind": "raw_predictions",
                     "predictions": rule_rows}
        self.manifest = _register(manifest, root, "rules", seal(rules_doc))
        v2_rows = []
        for row in _unknown_grid(self.ids):
            v2_rows.append(dict(row, abstained=True, abstention_reason="model_unknown",
                                trace_ids=["p8.v2.accepted"]))
        self.e1_run = seal({"p8_e1_run_version": "1.0.1", "rows": v2_rows,
                            "evaluation": {"metrics": {"typed_exact_match": 0.0},
                                           "unknown_count": len(v2_rows)}})
        self.numeric = numeric

    def test_a1_fills_v2_abstentions_from_cited_rules_only(self):
        document = build_e3_combination(self.manifest, self.e1_run, synthetic=True)
        summary = aggregates(document)
        filled = len(self.ids)
        self.assertEqual({"rules": filled, "llm": len(self.ids) * 23 - filled},
                         document["source_counts"])
        self.assertEqual(filled, document["reason_counts"]["llm_abstained_rule_fallback"])
        self.assertEqual(summary["raw_unknown"], len(self.ids) * 23 - filled)
        self.assertEqual(summary["safety_typed_exact_match"], summary["raw_typed_exact_match"])
        self.assertEqual("prompt_v2.A1", summary["candidate"])
        self.assertEqual(0.0, summary["e1_raw_reference"]["typed_exact_match"])

    def test_only_the_e0_selected_policy_is_allowed(self):
        with self.assertRaises(P8Error):
            build_e3_combination(self.manifest, self.e1_run, policy_id="A2", synthetic=True)

    def test_tampered_e1_run_is_rejected_before_any_read(self):
        tampered = dict(self.e1_run)
        tampered["rows"] = list(tampered["rows"])[:-1]
        with self.assertRaises(P8Error):
            build_e3_combination(self.manifest, tampered, synthetic=True)


if __name__ == "__main__":
    unittest.main()
