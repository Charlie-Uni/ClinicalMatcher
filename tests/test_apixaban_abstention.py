import copy
import unittest

from clinical_matcher.apixaban_abstention import (
    ApixabanAbstentionError,
    apply_deterministic_abstention,
    validate_abstention_outputs,
    validate_abstention_report,
)
from clinical_matcher.apixaban_abstention_cli import main
from clinical_matcher.apixaban_contract import load_question_catalog

from tests.test_apixaban_neurosymbolic_audit import (
    EVIDENCE_ID,
    PATIENT_ID,
    prediction_set,
    staging_corpus,
)


def gold_from_predictions(document):
    return {
        (item["patient_id"], item["question_id"]): {
            "fact_status": item["fact_status"],
            "value": item["value"],
        }
        for item in document["predictions"]
    }


class ApixabanAbstentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()
        cls.staging = staging_corpus(cls.catalog)

    def apply(
        self,
        predictions,
        *,
        gold=None,
        conflicts=None,
        conflict_status="not_evaluable",
    ):
        return apply_deterministic_abstention(
            prediction_set=predictions,
            staging_corpus=self.staging,
            expected_patient_ids=[PATIENT_ID],
            gold_by_key=gold or gold_from_predictions(predictions),
            source_prediction_sha256="1" * 64,
            split_manifest_sha256="2" * 64,
            staging_corpus_sha256="3" * 64,
            verifier_conflict_keys=conflicts,
            verifier_conflict_status=conflict_status,
            generated_at="2026-01-01T00:00:00Z",
            code_commit="4" * 40,
        )

    def test_known_missing_evidence_abstains_and_reduces_coverage(self):
        predictions = prediction_set(self.catalog)
        gold = gold_from_predictions(predictions)
        predictions["predictions"][0]["evidence_ids"] = []

        projection, report = self.apply(predictions, gold=gold)

        row = projection["predictions"][0]
        self.assertEqual("unknown", row["fact_status"])
        self.assertEqual("missing_evidence", row["abstention_reason"])
        self.assertIsNone(row["value"])
        self.assertEqual(1, report["reason_counts"]["missing_evidence"])
        self.assertEqual(1, report["counts"]["decision_changed_count"])
        before = report["coverage_risk_operating_points"]["before_policy"]
        after = report["coverage_risk_operating_points"]["after_policy"]
        self.assertEqual(1.0, before["coverage"])
        self.assertEqual(22 / 23, after["coverage"])
        self.assertEqual(0.0, after["risk"])

    def test_existing_unknown_gets_missing_fact_code_without_half_score(self):
        predictions = prediction_set(self.catalog)
        row = predictions["predictions"][0]
        row.update(
            {
                "fact_status": "unknown",
                "value": None,
                "abstained": True,
                "abstention_reason": "model_returned_unknown",
                "evidence_ids": [],
            }
        )
        gold = gold_from_predictions(predictions)

        projection, report = self.apply(predictions, gold=gold)

        projected = projection["predictions"][0]
        self.assertEqual("missing_fact", projected["abstention_reason"])
        self.assertIsNone(report["policy"]["unknown_probability"])
        self.assertFalse(report["policy"]["probabilities_used"])
        before = report["coverage_risk_operating_points"]["before_policy"]
        after = report["coverage_risk_operating_points"]["after_policy"]
        self.assertEqual(before["coverage"], after["coverage"])
        self.assertEqual(0, report["counts"]["decision_changed_count"])
        self.assertEqual(1, report["counts"]["metadata_changed_count"])

    def test_schema_invalid_model_response_has_dedicated_reason(self):
        predictions = prediction_set(self.catalog)
        row = predictions["predictions"][0]
        row.update(
            {
                "fact_status": "unknown",
                "value": None,
                "abstained": True,
                "abstention_reason": "invalid_model_structured_output",
                "evidence_ids": [],
                "trace_ids": ["local_llm.structured_invalid"],
            }
        )

        projection, report = self.apply(predictions)

        self.assertEqual(
            "invalid_schema",
            projection["predictions"][0]["abstention_reason"],
        )
        self.assertEqual(1, report["reason_counts"]["invalid_schema"])

    def test_unusable_evidence_is_removed_and_abstained(self):
        predictions = prediction_set(self.catalog)
        predictions["predictions"][0]["evidence_ids"] = [
            "evidence-ffffffffffffffffffffffff-000"
        ]

        projection, report = self.apply(predictions)

        row = projection["predictions"][0]
        self.assertEqual("unusable_evidence", row["abstention_reason"])
        self.assertEqual([], row["evidence_ids"])
        self.assertEqual(1, report["reason_counts"]["unusable_evidence"])

    def test_incompatible_unit_abstains_without_conversion(self):
        predictions = prediction_set(self.catalog)
        numeric = next(
            item
            for item in predictions["predictions"]
            if item["question_type"] == "numeric"
        )
        numeric["unit"] = "invented-unit"

        projection, report = self.apply(predictions)
        projected = next(
            item
            for item in projection["predictions"]
            if item["question_id"] == numeric["question_id"]
        )

        self.assertEqual("incompatible_unit", projected["abstention_reason"])
        self.assertEqual("invented-unit", projected["unit"])
        self.assertIsNone(projected["value"])
        self.assertEqual(1, report["reason_counts"]["incompatible_unit"])

    def test_evaluated_verifier_conflict_abstains_without_overwriting_source(self):
        predictions = prediction_set(self.catalog)
        original = copy.deepcopy(predictions)
        row = predictions["predictions"][0]
        key = (row["patient_id"], row["question_id"])

        projection, report = self.apply(
            predictions,
            conflicts={key},
            conflict_status="evaluated",
        )

        self.assertEqual("verifier_conflict", projection["predictions"][0]["abstention_reason"])
        self.assertEqual(1, report["reason_counts"]["verifier_conflict"])
        self.assertEqual(original, predictions)

    def test_precedence_is_stable_when_multiple_failures_exist(self):
        predictions = prediction_set(self.catalog)
        numeric = next(
            item
            for item in predictions["predictions"]
            if item["question_type"] == "numeric"
        )
        numeric["evidence_ids"] = []
        numeric["unit"] = "invented-unit"

        projection, _ = self.apply(predictions)
        projected = next(
            item
            for item in projection["predictions"]
            if item["question_id"] == numeric["question_id"]
        )

        self.assertEqual("missing_evidence", projected["abstention_reason"])

    def test_unavailable_conflicts_cannot_smuggle_conflict_pairs(self):
        predictions = prediction_set(self.catalog)
        row = predictions["predictions"][0]
        key = (row["patient_id"], row["question_id"])

        with self.assertRaisesRegex(
            ApixabanAbstentionError, "require an evaluated"
        ):
            self.apply(predictions, conflicts={key})

    def test_report_validator_recomputes_coverage_and_risk(self):
        predictions = prediction_set(self.catalog)
        _, report = self.apply(predictions)
        report["coverage_risk_operating_points"]["after_policy"]["coverage"] = 0.5

        with self.assertRaisesRegex(ApixabanAbstentionError, "coverage"):
            validate_abstention_report(report)

    def test_risk_counts_wrong_answer_among_retained_known_facts(self):
        predictions = prediction_set(self.catalog)
        gold = gold_from_predictions(predictions)
        predictions["predictions"][0]["value"] = False
        predictions["predictions"][0]["fact_status"] = "absent"

        _, report = self.apply(predictions, gold=gold)

        before = report["coverage_risk_operating_points"]["before_policy"]
        after = report["coverage_risk_operating_points"]["after_policy"]
        self.assertEqual(1, before["error_count"])
        self.assertEqual(1 / 23, before["risk"])
        self.assertEqual(before, after)

    def test_projection_hash_is_revalidated(self):
        predictions = prediction_set(self.catalog)
        projection, report = self.apply(predictions)
        projection["model_id"] += "-tampered"

        with self.assertRaisesRegex(
            ApixabanAbstentionError, "content hash"
        ):
            validate_abstention_outputs(projection, report)

    def test_cli_requires_restricted_acknowledgement(self):
        with self.assertRaisesRegex(
            SystemExit, "Restricted-data acknowledgement"
        ):
            main(
                [
                    "--predictions",
                    "missing.json",
                    "--benchmark",
                    "missing.json",
                    "--staging-corpus",
                    "missing.json",
                    "--frozen-split",
                    "missing.json",
                    "--projection-output",
                    "missing.json",
                    "--report-output",
                    "missing.json",
                ]
            )


if __name__ == "__main__":
    unittest.main()
