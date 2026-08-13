import unittest

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_deterministic import (
    extract_patient_predictions,
    load_deterministic_rule_set,
    validate_deterministic_rule_set,
)
from clinical_matcher.apixaban_evaluation import validate_prediction_set


PATIENT_ID = "patient-0123456789abcdef01234567"


def patient(*texts: str):
    return {
        "patient_id": PATIENT_ID,
        "evidence": [
            {
                "evidence_id": f"evidence-0123456789abcdef01234567-{index:03d}",
                "text": text,
            }
            for index, text in enumerate(texts)
        ],
    }


class ApixabanDeterministicExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()
        cls.rule_set = load_deterministic_rule_set()

    def extract(self, *texts: str):
        predictions = extract_patient_predictions(
            patient(*texts), self.catalog, self.rule_set
        )
        return {
            question["source_criterion_label"]: prediction
            for question, prediction in zip(
                self.catalog["questions"], predictions
            )
        }

    def test_rule_set_is_complete_and_declares_no_test_label_use(self):
        validate_deterministic_rule_set(self.rule_set, self.catalog)
        self.assertEqual(len(self.rule_set["rules"]), 23)
        self.assertFalse(self.rule_set["test_labels_used"])

    def test_positive_negative_and_ambiguous_mentions(self):
        result = self.extract(
            "Persistent atrial fibrillation is documented.",
            "No history of heart failure.",
            "Possible bipolar disorder is being evaluated.",
        )
        self.assertEqual(result["afib"]["fact_status"], "present")
        self.assertEqual(
            result["heart_failure"]["fact_status"], "absent"
        )
        self.assertEqual(result["bipolar"]["fact_status"], "unknown")
        self.assertEqual(
            result["bipolar"]["abstention_reason"],
            "ambiguous_or_missing_required_context",
        )

    def test_conflicting_boolean_mentions_abstain(self):
        result = self.extract(
            "Heart failure is documented.",
            "No history of heart failure.",
        )
        prediction = result["heart_failure"]
        self.assertEqual(prediction["fact_status"], "unknown")
        self.assertEqual(
            prediction["abstention_reason"],
            "conflicting_positive_and_negative_mentions",
        )
        self.assertEqual(len(prediction["evidence_ids"]), 2)

    def test_numeric_min_max_and_lvef_question_normalization(self):
        result = self.extract(
            "Glucose: 100. Blood glucose = 140.",
            "HGB 13.2; hemoglobin: 11.0.",
            "LVEF 60%. Ejection fraction: 58 percent.",
        )
        self.assertEqual(result["blood_glucose"]["value"], 140.0)
        self.assertEqual(result["HGB"]["value"], 11.0)
        self.assertEqual(result["lvef"]["value"], 55.0)
        self.assertEqual(result["lvef"]["fact_status"], "present")

    def test_reverse_numeric_mention_is_supported(self):
        result = self.extract("The latest result was 1.7 creatinine.")
        self.assertEqual(result["CREAT"]["value"], 1.7)

    def test_missing_fact_is_unknown_except_source_defined_default(self):
        result = self.extract("The patient attended a routine appointment.")
        self.assertEqual(result["afib"]["fact_status"], "unknown")
        self.assertEqual(result["afib"]["evidence_ids"], [])
        self.assertEqual(result["med_decisions"]["fact_status"], "absent")
        self.assertIn("source_defined_default_absent", result["med_decisions"]["rule_ids"][0])

    def test_temporal_bleeding_rule_does_not_guess_missing_window(self):
        unclear = self.extract("A major bleeding event is in the history.")
        recent = self.extract("Major bleeding within the last 6 months.")
        self.assertEqual(unclear["bleeding"]["fact_status"], "unknown")
        self.assertEqual(recent["bleeding"]["fact_status"], "present")

    def test_evidence_linked_prediction_set_version_is_valid(self):
        predictions = extract_patient_predictions(
            patient(
                "Atrial fibrillation is present. PLT 140. HGB 12.1. "
                "Creatinine 1.2. AST 30. Total bilirubin 0.8."
            ),
            self.catalog,
            self.rule_set,
        )
        document = {
            "prediction_set_version": "1.1.0",
            "benchmark_sha256": "a" * 64,
            "split_manifest_sha256": "b" * 64,
            "split_name": "validation",
            "model_id": "deterministic-test",
            "prompt_version": "not-applicable:test",
            "rule_set_sha256": "c" * 64,
            "generated_at": "2026-01-01T00:00:00Z",
            "code_commit": "d" * 40,
            "predictions": predictions,
        }
        validate_prediction_set(document, self.catalog)


if __name__ == "__main__":
    unittest.main()
