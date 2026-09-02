import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_benchmark import (
    EXPECTED_OFFICIAL_COUNTS,
    validate_apixaban_benchmark,
)
from clinical_matcher.apixaban_contract import (
    load_question_catalog,
    normalize_source_answer,
)
from clinical_matcher.apixaban_single_trial_evaluation import (
    ApixabanSingleTrialEvaluationError,
    _validate_mentor_summary,
    build_single_trial_evaluation,
    build_single_trial_evaluation_v1_1,
    load_single_trial_run_contract,
    validate_single_trial_report,
    validate_single_trial_run_contract,
    write_single_trial_evaluation,
)
from clinical_matcher.ingestion.apixaban import (
    DATASET_ID,
    DATASET_VERSION,
    OFFICIAL_SOURCE_SHA256,
)
from clinical_matcher.splits import canonical_sha256


def assessment_id(patient_id, question_id):
    digest = hashlib.sha256(
        f"{patient_id}\0{question_id}".encode("utf-8")
    ).hexdigest()
    return f"apixaban-a-{digest[:24]}"


def passing_numeric_value(label):
    return {
        "chads2": 3.0,
        "blood_glucose": 120,
        "lvef": 50,
        "PLT": 100,
        "HGB": 10,
        "CREAT": 2.5,
        "AST": 80,
        "BILI": 1.8,
    }[label]


def official_shape_synthetic_benchmark():
    catalog = load_question_catalog()
    patient_ids = [f"patient-{index:024x}" for index in range(100)]
    pairs = [
        (patient_id, question)
        for patient_id in patient_ids
        for question in catalog["questions"]
    ]
    assessments = []
    for index, (patient_id, question) in enumerate(pairs):
        if index < EXPECTED_OFFICIAL_COUNTS["not_specified_source_count"]:
            status = "not_specified"
            value = None
        elif index < (
            EXPECTED_OFFICIAL_COUNTS["not_specified_source_count"]
            + EXPECTED_OFFICIAL_COUNTS["source_anomaly_count"]
        ):
            status = "source_anomaly"
            value = None
        else:
            status = "answered"
            if question["question_type"] == "numeric":
                value = passing_numeric_value(question["source_criterion_label"])
            else:
                value = question["source_criterion_label"] == "afib"
        assessments.append(
            normalize_source_answer(
                assessment_id=assessment_id(patient_id, question["question_id"]),
                patient_id=patient_id,
                question_id=question["question_id"],
                answer_status=status,
                answer_value=value,
                catalog=catalog,
            )
        )
    assessments.sort(key=lambda row: (row["patient_id"], row["question_id"]))
    benchmark = {
        "apixaban_benchmark_version": "1.0.0",
        "source": {
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
            "source_csv_sha256": OFFICIAL_SOURCE_SHA256,
            "staging_corpus_sha256": "1" * 64,
            "import_manifest_sha256": "2" * 64,
        },
        "contract": {
            "question_catalog_version": catalog["catalog_version"],
            "question_catalog_sha256": catalog["catalog_sha256"],
            "fact_assessment_version": "1.0.0",
            "prediction_target": "note_grounded_fact_assessment",
            "patient_text_storage": "external_restricted_staging_corpus",
            "gold_evidence_status": "not_available_in_source",
        },
        "patient_ids": patient_ids,
        "assessments": assessments,
    }
    validate_apixaban_benchmark(benchmark)
    return benchmark


def synthetic_evaluation_inputs():
    benchmark = official_shape_synthetic_benchmark()
    validation_ids = benchmark["patient_ids"][-2:]
    benchmark_sha256 = "3" * 64
    split_sha256 = "4" * 64
    split = {
        "status": "frozen",
        "freeze": {"test_locked": True},
        "manifest_sha256": split_sha256,
        "splits": {"validation": {"patient_ids": validation_ids}},
    }
    predictions = []
    for assessment in benchmark["assessments"]:
        if assessment["patient_id"] not in validation_ids:
            continue
        predictions.append(
            {
                "patient_id": assessment["patient_id"],
                "question_id": assessment["question_id"],
                "question_type": assessment["question_type"],
                "fact_status": assessment["fact_status"],
                "value": assessment["value"],
                "unit": None,
                "evidence_ids": [],
                "trace_ids": ["synthetic-trace"],
                "abstained": assessment["fact_status"] == "unknown",
                "abstention_reason": (
                    "synthetic_unknown"
                    if assessment["fact_status"] == "unknown"
                    else None
                ),
            }
        )
    afib_id = next(
        question["question_id"]
        for question in load_question_catalog()["questions"]
        if question["source_criterion_label"] == "afib"
    )
    changed = next(
        row for row in predictions
        if row["patient_id"] == validation_ids[-1]
        and row["question_id"] == afib_id
    )
    changed.update({"fact_status": "absent", "value": False})
    prediction_set = {
        "prediction_set_version": "1.2.0",
        "inference_config_sha256": (
            "9a512404d817711110a9e0cdc524060e4d30459f6170ef64ba373393a8fc606c"
        ),
        "benchmark_sha256": benchmark_sha256,
        "split_manifest_sha256": split_sha256,
        "split_name": "validation",
        "model_id": (
            "ollama/llama3.1:8b-instruct-q4_k_m@sha256:"
            "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
            "+deterministic-abstention-1.0.0"
        ),
        "prompt_version": "apixaban-23-facts-structured-1.0.0",
        "generated_at": "2026-01-01T00:00:00Z",
        "code_commit": "5" * 40,
        "predictions": predictions,
    }
    mentor_reference = {
        patient_id: "ideal" for patient_id in benchmark["patient_ids"]
    }
    return benchmark, split, prediction_set, mentor_reference


class ApixabanSingleTrialEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inputs = synthetic_evaluation_inputs()
        cls.inputs = inputs
        cls.run_contract = load_single_trial_run_contract()
        cls.report, cls.trace = build_single_trial_evaluation(
            *inputs,
            benchmark_sha256="3" * 64,
            prediction_set_sha256=cls.run_contract["selected_artifact"][
                "prediction_set_sha256"
            ],
            mentor_results_sha256="7" * 64,
            candidate_csv_sha256="8" * 64,
            id_map_sha256="9" * 64,
            run_contract=cls.run_contract,
            generated_at="2026-01-02T00:00:00Z",
            code_commit="a" * 40,
        )

    def test_three_axes_share_complete_patient_denominator(self):
        self.assertEqual(2, self.report["population"]["patient_count"])
        self.assertEqual(46, self.report["population"]["assessment_count"])
        axes = self.report["axes"]
        self.assertEqual(3, len(axes))
        self.assertTrue(all(axis["patient_count"] == 2 for axis in axes.values()))
        axis_b = axes["axis_b_intended_model_vs_intended_gold"]
        self.assertEqual(1, axis_b["exact_agreement_count"])
        self.assertEqual(0.5, axis_b["complete_denominator_exact_agreement"])
        self.assertEqual(5, len(axis_b["per_rule"]))
        self.assertIsNone(axis_b["conditional_three_class"])

    def test_owner_selection_is_hash_bound_and_pre_result(self):
        validate_single_trial_run_contract(self.run_contract)
        self.assertEqual(
            self.run_contract["contract_sha256"],
            self.report["provenance"]["run_contract_sha256"],
        )
        self.assertEqual(
            "long_context_plus_p4_3_abstention",
            self.report["model_selection"]["selected_configuration"],
        )
        self.assertFalse(
            self.report["model_selection"][
                "single_trial_three_class_results_seen_before_selection"
            ]
        )
        self.assertFalse(
            self.report["model_selection"]["unselected_artifact_evaluated"]
        )

    def test_unselected_prediction_hash_fails_closed(self):
        benchmark, split, predictions, reference = copy.deepcopy(self.inputs)
        with self.assertRaisesRegex(
            ApixabanSingleTrialEvaluationError, "owner-selected long-context"
        ):
            build_single_trial_evaluation(
                benchmark,
                split,
                predictions,
                reference,
                benchmark_sha256="3" * 64,
                prediction_set_sha256=(
                    self.run_contract["unselected_artifact"][
                        "prediction_set_sha256"
                    ]
                ),
                mentor_results_sha256="7" * 64,
                candidate_csv_sha256="8" * 64,
                id_map_sha256="9" * 64,
                run_contract=self.run_contract,
                generated_at="2026-01-02T00:00:00Z",
                code_commit="a" * 40,
            )

    def test_report_has_per_question_unit_diagnostics_without_patient_ids(self):
        self.assertNotIn("patient-", repr(self.report))
        for source in ("released_gold", "model_predictions"):
            diagnostics = self.report["adapter_diagnostics"][source]
            self.assertEqual(46, diagnostics["row_count"])
            self.assertEqual(16, diagnostics["numeric_row_count"])
            self.assertEqual(8, len(diagnostics["per_question"]))
            for item in diagnostics["per_question"]:
                self.assertEqual(2, item["total_count"])
                self.assertIn("out_of_range_count", item)
                self.assertIn("out_of_range_fraction_of_all_rows", item)

    def test_trace_is_separate_owner_only_and_hash_bound(self):
        self.assertEqual(2, self.trace["patient_count"])
        self.assertTrue(self.trace["restricted_local_only"])
        self.assertEqual(
            self.trace["trace_sha256"], self.report["provenance"]["trace_sha256"]
        )
        self.assertTrue(
            all("patient_id" in row for row in self.trace["rows"])
        )

    def test_rehashed_count_tamper_fails_reconciliation(self):
        changed = copy.deepcopy(self.report)
        axis = changed["axes"]["axis_a_intended_gold_vs_mentor_reference"]
        axis["reference_outcome_counts"]["ideal"] += 1
        unsigned = dict(changed)
        unsigned.pop("report_sha256")
        changed["report_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(
            ApixabanSingleTrialEvaluationError, "reference counts"
        ):
            validate_single_trial_report(changed)

    def test_additive_1_1_validation_uses_current_projection(self):
        benchmark, split, predictions, reference = copy.deepcopy(self.inputs)
        predictions["model_id"] = predictions["model_id"].replace(
            "deterministic-abstention-1.0.0",
            "deterministic-abstention-1.1.0",
        )
        report, trace = build_single_trial_evaluation_v1_1(
            benchmark,
            split,
            predictions,
            reference,
            split_name="validation",
            benchmark_sha256="3" * 64,
            prediction_set_sha256="6" * 64,
            mentor_results_sha256="7" * 64,
            candidate_csv_sha256="8" * 64,
            id_map_sha256="9" * 64,
            p7_contract_sha256="b" * 64,
            generated_at="2026-01-03T00:00:00Z",
            code_commit="a" * 40,
        )

        self.assertEqual("1.1.0", report["report_version"])
        self.assertFalse(report["provenance"]["locked_test_labels_used"])
        self.assertTrue(report["model_selection"]["post_observation_additive"])
        self.assertEqual("1.1.0", trace["report_version"])

    def test_incomplete_model_grid_fails_closed(self):
        benchmark, split, predictions, reference = copy.deepcopy(self.inputs)
        predictions["predictions"].pop()
        with self.assertRaisesRegex(
            ApixabanSingleTrialEvaluationError, "complete validation grid"
        ):
            build_single_trial_evaluation(
                benchmark,
                split,
                predictions,
                reference,
                benchmark_sha256="3" * 64,
                prediction_set_sha256=self.run_contract["selected_artifact"][
                    "prediction_set_sha256"
                ],
                mentor_results_sha256="7" * 64,
                candidate_csv_sha256="8" * 64,
                id_map_sha256="9" * 64,
                run_contract=self.run_contract,
                generated_at="2026-01-02T00:00:00Z",
                code_commit="a" * 40,
            )

    def test_mentor_summary_requires_ideal_subset(self):
        summary = {
            "Semi_Ideal_Candidate": {
                "total_matches": 1,
                "percentage": "1.0%",
                "patient_numbers": [1],
            },
            "Ideal_Candidate": {
                "total_matches": 1,
                "percentage": "1.0%",
                "patient_numbers": [2],
            },
        }
        with self.assertRaisesRegex(
            ApixabanSingleTrialEvaluationError, "subset"
        ):
            _validate_mentor_summary(summary)

    def test_outputs_are_owner_only_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "restricted"
            report_path, trace_path, summary_path = write_single_trial_evaluation(
                self.report, self.trace, output
            )
            self.assertEqual(0o600, os.stat(report_path).st_mode & 0o777)
            self.assertEqual(0o600, os.stat(trace_path).st_mode & 0o777)
            self.assertEqual(0o600, os.stat(summary_path).st_mode & 0o777)
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("Three mandatory axes", summary)
            self.assertIn("Summary renderer: `1.1.0`", summary)
            self.assertIn("Candidate UNKNOWN", summary)
            self.assertIn("Confusion matrices", summary)
            self.assertIn("Reference \\ Candidate", summary)
            self.assertIn("Criterion-level agreement", summary)
            self.assertIn("apixaban-rule-5", summary)
            self.assertIn("Unit-adapter diagnostics", summary)
            self.assertNotIn("patient-", summary)
            with self.assertRaises(FileExistsError):
                write_single_trial_evaluation(self.report, self.trace, output)


if __name__ == "__main__":
    unittest.main()
