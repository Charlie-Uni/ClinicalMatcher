import copy
import hashlib
import unittest

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_error_attribution import (
    ApixabanErrorAttributionError,
    build_error_attribution_report,
    validate_error_attribution_report,
)
from clinical_matcher.apixaban_error_attribution_cli import main

from tests.test_apixaban_neurosymbolic_audit import (
    PATIENT_ID,
    prediction_set,
    staging_corpus,
)


def _assessment_id(patient_id, question_id):
    digest = hashlib.sha256(
        f"{patient_id}\0{question_id}".encode("utf-8")
    ).hexdigest()
    return f"apixaban-a-{digest[:24]}"


def benchmark_from_predictions(catalog, predictions):
    assessments = []
    for item in predictions["predictions"]:
        unknown = item["fact_status"] == "unknown"
        assessments.append(
            {
                "fact_assessment_version": "1.0.0",
                "question_catalog_version": "1.0.0",
                "assessment_id": _assessment_id(
                    item["patient_id"], item["question_id"]
                ),
                "patient_id": item["patient_id"],
                "question_id": item["question_id"],
                "question_type": item["question_type"],
                "assessment_source": "released_source_label",
                "fact_status": item["fact_status"],
                "value": item["value"],
                "unit": None,
                "evidence_status": "not_available_in_source",
                "evidence_ids": [],
                "abstained": unknown,
                "abstention_reason": "source_not_specified" if unknown else None,
            }
        )
    return {
        "apixaban_benchmark_version": "1.0.0",
        "source": {
            "dataset_id": "MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions",
            "dataset_version": "1.0.0",
            "source_csv_sha256": "a" * 64,
            "staging_corpus_sha256": "b" * 64,
            "import_manifest_sha256": "c" * 64,
        },
        "contract": {
            "question_catalog_version": "1.0.0",
            "question_catalog_sha256": catalog["catalog_sha256"],
            "fact_assessment_version": "1.0.0",
            "prediction_target": "note_grounded_fact_assessment",
            "patient_text_storage": "external_restricted_staging_corpus",
            "gold_evidence_status": "not_available_in_source",
        },
        "patient_ids": [PATIENT_ID],
        "assessments": sorted(
            assessments,
            key=lambda item: (item["patient_id"], item["question_id"]),
        ),
    }


class ApixabanErrorAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()
        cls.staging = staging_corpus(cls.catalog)
        cls.base_predictions = prediction_set(cls.catalog)
        cls.benchmark = benchmark_from_predictions(
            cls.catalog, cls.base_predictions
        )

    def report(self, predictions=None):
        return build_error_attribution_report(
            prediction_set=predictions or copy.deepcopy(self.base_predictions),
            benchmark=copy.deepcopy(self.benchmark),
            staging_corpus=copy.deepcopy(self.staging),
            expected_patient_ids=[PATIENT_ID],
            prediction_set_sha256="1" * 64,
            benchmark_sha256="2" * 64,
            staging_corpus_sha256="3" * 64,
            split_manifest_sha256="4" * 64,
            generated_at="2026-01-01T00:00:00Z",
            code_commit="5" * 40,
        )

    def test_matching_supported_predictions_have_no_attributed_error(self):
        report = self.report()

        self.assertEqual(0, report["population"]["attributed_error_count"])
        self.assertEqual(23, report["population"]["no_attributed_error_count"])
        self.assertEqual(0, sum(report["category_counts"].values()))

    def test_observable_categories_are_mutually_exclusive_and_reconcile(self):
        predictions = copy.deepcopy(self.base_predictions)
        booleans = [
            item for item in predictions["predictions"]
            if item["question_type"] == "boolean"
        ]
        numerics = [
            item for item in predictions["predictions"]
            if item["question_type"] == "numeric"
        ]
        booleans[0]["evidence_ids"] = []
        numerics[0]["unit"] = "invented-unit"
        booleans[1].update(
            {
                "fact_status": "unknown",
                "value": None,
                "abstained": True,
                "abstention_reason": "model_returned_unknown",
            }
        )
        numerics[1]["value"] = 99
        booleans[2].update({"fact_status": "absent", "value": False})

        report = self.report(predictions)

        counts = report["category_counts"]
        self.assertEqual(1, counts["unsupported_answering"])
        self.assertEqual(1, counts["unit_contract_error"])
        self.assertEqual(1, counts["abstention_on_gold_known"])
        self.assertEqual(1, counts["numeric_value_error"])
        self.assertEqual(
            1, counts["fact_status_error_with_patient_local_citation"]
        )
        self.assertEqual(5, report["population"]["attributed_error_count"])
        self.assertEqual(5, sum(counts.values()))
        self.assertEqual(23, 5 + report["population"]["no_attributed_error_count"])

    def test_frozen_precedence_prevents_double_counting(self):
        predictions = copy.deepcopy(self.base_predictions)
        numeric = next(
            item for item in predictions["predictions"]
            if item["question_type"] == "numeric"
        )
        numeric["evidence_ids"] = []
        numeric["unit"] = "invented-unit"
        numeric["value"] = 99

        report = self.report(predictions)

        self.assertEqual(1, report["category_counts"]["unsupported_answering"])
        self.assertEqual(0, report["category_counts"]["unit_contract_error"])
        self.assertEqual(0, report["category_counts"]["numeric_value_error"])
        self.assertEqual(1, report["population"]["attributed_error_count"])

    def test_missing_gold_dimensions_remain_not_evaluable(self):
        report = self.report()

        for name in (
            "retrieval_failure",
            "reasoning_failure_with_usable_evidence",
            "time_error",
            "negation_error",
            "false_abstention",
        ):
            self.assertEqual(
                "not_evaluable",
                report["requested_dimensions"][name]["status"],
            )
            self.assertTrue(report["requested_dimensions"][name]["reason"])
        self.assertEqual(
            "pending_authorized_environment",
            report["representative_case_review"]["status"],
        )

    def test_report_validator_rejects_non_reconciling_counts(self):
        report = self.report()
        report["category_counts"]["unsupported_answering"] = 1

        with self.assertRaisesRegex(
            ApixabanErrorAttributionError, "reconcile"
        ):
            validate_error_attribution_report(report)

    def test_report_validator_rejects_false_evaluability_claim(self):
        report = self.report()
        report["requested_dimensions"]["retrieval_failure"] = {
            "status": "evaluated",
            "reason": None,
        }

        with self.assertRaisesRegex(
            ApixabanErrorAttributionError, "evaluability"
        ):
            validate_error_attribution_report(report)

    def test_incomplete_prediction_grid_is_rejected(self):
        predictions = copy.deepcopy(self.base_predictions)
        predictions["predictions"].pop()

        with self.assertRaisesRegex(
            ApixabanErrorAttributionError, "exact split"
        ):
            self.report(predictions)

    def test_cli_requires_restricted_acknowledgement(self):
        with self.assertRaisesRegex(
            SystemExit, "Restricted-data acknowledgement"
        ):
            main(
                [
                    "--predictions", "missing.json",
                    "--benchmark", "missing.json",
                    "--staging-corpus", "missing.json",
                    "--frozen-split", "missing.json",
                    "--output", "missing.json",
                ]
            )


if __name__ == "__main__":
    unittest.main()
