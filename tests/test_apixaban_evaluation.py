import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_benchmark import file_sha256
from clinical_matcher.apixaban_evaluation import (
    ApixabanEvaluationError,
    FactEvaluationRecord,
    evaluate_apixaban_predictions,
    exact_source_tolerance_policy,
    mixed_fact_bootstrap,
    mixed_fact_metrics,
    validate_tolerance_policy,
    write_apixaban_evaluation_report,
)
from clinical_matcher.apixaban_split import (
    freeze_apixaban_split,
    split_manifest_view,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from tests.test_apixaban_split import SPLIT_COUNTS, build_candidate


def record(
    patient_id,
    question_id,
    question_type,
    gold_status,
    predicted_status,
    gold_value,
    predicted_value,
    gold_unit=None,
    predicted_unit=None,
    tolerance=0.0,
):
    return FactEvaluationRecord(
        patient_id=patient_id,
        question_id=question_id,
        question_type=question_type,
        gold_status=gold_status,
        predicted_status=predicted_status,
        gold_value=gold_value,
        predicted_value=predicted_value,
        gold_unit=gold_unit,
        predicted_unit=predicted_unit,
        tolerance=tolerance,
    )


def mixed_records():
    rows = []
    for patient_id, boolean_prediction, numeric_prediction in (
        ("patient-a", "present", 12.0),
        ("patient-b", None, 10.5),
    ):
        rows.extend(
            [
                record(
                    patient_id,
                    "boolean-question",
                    "boolean",
                    "present",
                    boolean_prediction,
                    True,
                    True if boolean_prediction else None,
                ),
                record(
                    patient_id,
                    "unknown-question",
                    "boolean",
                    "unknown",
                    "unknown",
                    None,
                    None,
                ),
                record(
                    patient_id,
                    "numeric-question",
                    "numeric",
                    "present",
                    "present",
                    10.0,
                    numeric_prediction,
                    tolerance=1.0,
                ),
                record(
                    patient_id,
                    "numeric-unknown",
                    "numeric",
                    "unknown",
                    "unknown",
                    None,
                    None,
                    tolerance=1.0,
                ),
            ]
        )
    return rows


def _write_private(path, document):
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


class ApixabanEvaluationTest(unittest.TestCase):
    def test_mixed_metrics_keep_boolean_and_numeric_layers_separate(self):
        metrics = mixed_fact_metrics(mixed_records())
        self.assertEqual(8, metrics["count"])
        self.assertEqual(1.25, metrics["numeric_values"]["mae_valid_pairs"])
        self.assertEqual(
            0.5,
            metrics["numeric_values"][
                "tolerance_accuracy_all_gold_present"
            ],
        )
        self.assertEqual(1.0, metrics["numeric_values"]["value_coverage"])
        self.assertEqual(1.0, metrics["boolean"]["unknown_f1"])
        self.assertEqual(1, metrics["boolean"]["missing_prediction_count"])

    def test_missing_unknown_prediction_is_not_rewarded(self):
        rows = mixed_records()
        target = rows[1]
        rows[1] = record(
            target.patient_id,
            target.question_id,
            "boolean",
            "unknown",
            None,
            None,
            None,
        )
        metrics = mixed_fact_metrics(rows)
        self.assertLess(metrics["boolean"]["unknown_f1"], 1.0)
        self.assertEqual(
            1,
            metrics["boolean"]["confusion_matrix"]["unknown"]["missing"],
        )

    def test_invalid_unit_reduces_coverage_and_all_gold_accuracy(self):
        rows = mixed_records()
        target = rows[2]
        rows[2] = record(
            target.patient_id,
            target.question_id,
            "numeric",
            "present",
            "present",
            10.0,
            10.0,
            gold_unit=None,
            predicted_unit="mg/dL",
            tolerance=1.0,
        )
        numeric = mixed_fact_metrics(rows)["numeric_values"]
        self.assertEqual(1, numeric["invalid_unit_count"])
        self.assertEqual(0.5, numeric["value_coverage"])
        self.assertEqual(0.5, numeric["tolerance_accuracy_all_gold_present"])
        self.assertEqual(1.0, numeric["tolerance_accuracy_valid_pairs"])

    def test_unreviewed_nonzero_tolerance_and_test_use_are_rejected(self):
        policy = exact_source_tolerance_policy()
        question_id = next(iter(policy["absolute_tolerance_by_question"]))
        changed = copy.deepcopy(policy)
        changed["absolute_tolerance_by_question"][question_id] = 1.0
        with self.assertRaisesRegex(
            ApixabanEvaluationError, "canonical-unit contract"
        ):
            validate_tolerance_policy(changed)
        changed = copy.deepcopy(policy)
        changed["test_labels_used"] = True
        with self.assertRaisesRegex(ApixabanEvaluationError, "test labels"):
            validate_tolerance_policy(changed)

    def test_numeric_macro_excludes_question_without_gold_present(self):
        rows = mixed_records()
        rows.extend(
            [
                record(
                    patient_id,
                    "all-unknown-numeric",
                    "numeric",
                    "unknown",
                    "unknown",
                    None,
                    None,
                )
                for patient_id in ("patient-a", "patient-b")
            ]
        )
        macro = mixed_fact_metrics(rows)["macro_by_question"]
        self.assertEqual(1, macro["numeric_tolerance_evaluable_question_count"])
        self.assertEqual(
            0.5,
            macro["numeric_tolerance_accuracy_all_gold_present"],
        )

    def test_bootstrap_resamples_patients_and_is_deterministic(self):
        first = mixed_fact_bootstrap(mixed_records(), samples=50, seed=17)
        second = mixed_fact_bootstrap(mixed_records(), samples=50, seed=17)
        self.assertEqual(first, second)
        for interval in first.values():
            self.assertEqual(2, interval["cluster_count"])

    def test_end_to_end_report_is_frozen_split_bound_and_owner_only(self):
        candidate, inputs = build_candidate()
        view = split_manifest_view(candidate)
        expected_pairs = sum(
            len(view.splits[left].entity_ids["patient"])
            * len(view.splits[right].entity_ids["patient"])
            for left, right in (
                ("train", "validation"),
                ("train", "test"),
                ("validation", "test"),
            )
        )
        summary = build_semantic_scan_summary(
            view,
            "patient",
            (),
            "synthetic-encoder",
            "synthetic-v1",
            "mean",
            True,
            "exhaustive_cosine",
            expected_pairs,
        )
        frozen = freeze_apixaban_split(candidate, summary, "SYNTHETIC-TEST")
        benchmark = inputs[0]
        selected = set(frozen["splits"]["validation"]["patient_ids"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark_path = root / "benchmark.json"
            split_path = root / "split.json"
            prediction_path = root / "predictions.json"
            _write_private(benchmark_path, benchmark)
            _write_private(split_path, frozen)
            predictions = {
                "prediction_set_version": "1.0.0",
                "benchmark_sha256": file_sha256(benchmark_path),
                "split_manifest_sha256": frozen["manifest_sha256"],
                "split_name": "validation",
                "model_id": "synthetic-perfect@1",
                "prompt_version": "not-applicable:synthetic",
                "generated_at": "2026-08-13T08:00:00Z",
                "code_commit": "7" * 40,
                "predictions": [
                    {
                        "patient_id": item["patient_id"],
                        "question_id": item["question_id"],
                        "question_type": item["question_type"],
                        "fact_status": item["fact_status"],
                        "value": item["value"],
                        "unit": item["unit"],
                        "abstained": item["abstained"],
                        "abstention_reason": item["abstention_reason"],
                    }
                    for item in benchmark["assessments"]
                    if item["patient_id"] in selected
                ],
            }
            _write_private(prediction_path, predictions)
            report = evaluate_apixaban_predictions(
                benchmark_path,
                split_path,
                prediction_path,
                "validation",
                bootstrap_samples=20,
                code_commit="6" * 40,
                required_source_sha256=None,
                required_counts=SPLIT_COUNTS,
            )
            self.assertEqual(1.0, report["metrics"]["typed_exact_match"])
            self.assertEqual(2, next(iter(report["bootstrap"].values()))[
                "cluster_count"
            ])
            output = root / "report"
            json_path, markdown_path = write_apixaban_evaluation_report(
                report, output
            )
            self.assertEqual(0, os.stat(json_path).st_mode & 0o077)
            self.assertEqual(0, os.stat(markdown_path).st_mode & 0o077)
            with self.assertRaises(FileExistsError):
                write_apixaban_evaluation_report(report, output)


if __name__ == "__main__":
    unittest.main()
