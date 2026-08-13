import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import (
    EXPECTED_OFFICIAL_COUNTS,
    OFFICIAL_SOURCE_SHA256,
    file_sha256,
    validate_apixaban_benchmark,
)
from .apixaban_contract import load_question_catalog, question_index
from .apixaban_split import (
    ApixabanSplitError,
    load_apixaban_split_manifest,
)
from .evaluation import BootstrapInterval, clustered_bootstrap
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


PREDICTION_SET_SCHEMAS = {
    "1.0.0": "schemas/apixaban-prediction-set-1.0.0.schema.json",
    "1.1.0": "schemas/apixaban-prediction-set-1.1.0.schema.json",
}
EVALUATION_REPORT_VERSION = "1.0.0"
EVALUATION_REPORT_SCHEMA = (
    "schemas/apixaban-evaluation-report-1.0.0.schema.json"
)
FACT_LABELS = ("present", "absent", "unknown")
NUMERIC_STATUS_LABELS = ("present", "unknown")
MISSING_LABEL = "missing"


class ApixabanEvaluationError(ValueError):
    """Raised when typed-fact evaluation inputs or metrics are invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FactEvaluationRecord:
    patient_id: str
    question_id: str
    question_type: str
    gold_status: str
    predicted_status: Optional[str]
    gold_value: Any
    predicted_value: Any
    gold_unit: Optional[str]
    predicted_unit: Optional[str]
    tolerance: float

    def __post_init__(self) -> None:
        if self.question_type not in {"boolean", "numeric"}:
            raise ApixabanEvaluationError("Unsupported question type")
        allowed = (
            FACT_LABELS
            if self.question_type == "boolean"
            else NUMERIC_STATUS_LABELS
        )
        if self.gold_status not in allowed:
            raise ApixabanEvaluationError("Invalid gold fact status")
        if self.predicted_status is not None and (
            self.predicted_status not in allowed
        ):
            raise ApixabanEvaluationError("Invalid predicted fact status")
        if not math.isfinite(self.tolerance) or self.tolerance < 0:
            raise ApixabanEvaluationError(
                "Numeric tolerance must be finite and non-negative"
            )


def exact_source_tolerance_policy(
    catalog: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    resolved = dict(catalog or load_question_catalog())
    numeric_ids = sorted(
        question["question_id"]
        for question in resolved["questions"]
        if question["question_type"] == "numeric"
    )
    return {
        "policy_version": "1.0.0",
        "policy_id": "source-exact-no-canonical-units-v1",
        "question_catalog_sha256": resolved["catalog_sha256"],
        "development_splits": ["train", "validation"],
        "test_labels_used": False,
        "unit_policy": "source_questions_define_no_canonical_units",
        "interpretation": (
            "Zero tolerance evaluates exact released source-value extraction; "
            "it is not a clinical-equivalence tolerance."
        ),
        "absolute_tolerance_by_question": {
            question_id: 0.0 for question_id in numeric_ids
        },
    }


def validate_tolerance_policy(
    policy: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    resolved = dict(catalog or load_question_catalog())
    required = {
        "policy_version",
        "policy_id",
        "question_catalog_sha256",
        "development_splits",
        "test_labels_used",
        "unit_policy",
        "interpretation",
        "absolute_tolerance_by_question",
    }
    if set(policy) != required:
        raise ApixabanEvaluationError("Tolerance policy fields are incomplete")
    if policy["policy_version"] != "1.0.0":
        raise ApixabanEvaluationError("Unsupported tolerance policy version")
    if policy["question_catalog_sha256"] != resolved["catalog_sha256"]:
        raise ApixabanEvaluationError("Tolerance policy catalog hash mismatch")
    if policy["development_splits"] != ["train", "validation"]:
        raise ApixabanEvaluationError(
            "Tolerance policy must be developed on train/validation only"
        )
    if policy["test_labels_used"] is not False:
        raise ApixabanEvaluationError(
            "A tolerance policy influenced by test labels is forbidden"
        )
    numeric_ids = {
        question["question_id"]
        for question in resolved["questions"]
        if question["question_type"] == "numeric"
    }
    tolerances = policy["absolute_tolerance_by_question"]
    if set(tolerances) != numeric_ids:
        raise ApixabanEvaluationError(
            "Tolerance policy must cover exactly the numeric questions"
        )
    for value in tolerances.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ApixabanEvaluationError("Tolerance values must be numeric")
        if not math.isfinite(float(value)) or value < 0:
            raise ApixabanEvaluationError(
                "Tolerance values must be finite and non-negative"
            )
    if any(
        question["canonical_unit"] is None and tolerances[question["question_id"]]
        != 0
        for question in resolved["questions"]
        if question["question_type"] == "numeric"
    ):
        raise ApixabanEvaluationError(
            "Non-zero tolerances require a reviewed canonical-unit contract"
        )


def validate_prediction_set(
    document: Dict[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    version = document.get("prediction_set_version")
    schema = PREDICTION_SET_SCHEMAS.get(version)
    if schema is None:
        raise ApixabanEvaluationError("Unsupported prediction-set version")
    validate_document(document, schema)
    questions = question_index(dict(catalog) if catalog else None)
    seen = set()
    for prediction in document["predictions"]:
        question = questions.get(prediction["question_id"])
        if question is None:
            raise ApixabanEvaluationError(
                "Prediction references an unknown question"
            )
        if prediction["question_type"] != question["question_type"]:
            raise ApixabanEvaluationError("Prediction question type mismatch")
        key = (prediction["patient_id"], prediction["question_id"])
        if key in seen:
            raise ApixabanEvaluationError(
                "Prediction set contains duplicate patient-question rows"
            )
        seen.add(key)
        value = prediction["value"]
        if isinstance(value, float) and not math.isfinite(value):
            raise ApixabanEvaluationError("Prediction value must be finite")


def _classification_metrics(
    records: Sequence[FactEvaluationRecord],
    labels: Sequence[str],
) -> Dict[str, Any]:
    if not records:
        raise ApixabanEvaluationError("Classification records must not be empty")
    predicted_labels = tuple(labels) + (MISSING_LABEL,)
    matrix = {
        gold: {predicted: 0 for predicted in predicted_labels}
        for gold in labels
    }
    for record in records:
        predicted = record.predicted_status or MISSING_LABEL
        matrix[record.gold_status][predicted] += 1
    per_class = {}
    correct = 0
    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[gold][label] for gold in labels if gold != label)
        fn = sum(
            count
            for predicted, count in matrix[label].items()
            if predicted != label
        )
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        support = sum(matrix[label].values())
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        }
        correct += tp
    return {
        "count": len(records),
        "missing_prediction_count": sum(
            row[MISSING_LABEL] for row in matrix.values()
        ),
        "accuracy": correct / len(records),
        "micro_f1": correct / len(records),
        "macro_f1": sum(per_class[label]["f1"] for label in labels)
        / len(labels),
        "unknown_f1": per_class["unknown"]["f1"],
        "per_class": per_class,
        "confusion_matrix": matrix,
    }


def _typed_exact_match(record: FactEvaluationRecord) -> bool:
    if record.predicted_status is None:
        return False
    if record.predicted_status != record.gold_status:
        return False
    if record.predicted_unit != record.gold_unit:
        return False
    if record.gold_status == "unknown":
        return record.predicted_value is None
    return record.predicted_value == record.gold_value


def _numeric_value_metrics(
    records: Sequence[FactEvaluationRecord],
) -> Dict[str, Any]:
    gold_present = [record for record in records if record.gold_status == "present"]
    errors = []
    within_tolerance = 0
    invalid_units = 0
    missing_values = 0
    for record in gold_present:
        if record.predicted_status != "present":
            missing_values += 1
            continue
        value = record.predicted_value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            missing_values += 1
            continue
        if not math.isfinite(float(value)):
            missing_values += 1
            continue
        if record.predicted_unit != record.gold_unit:
            invalid_units += 1
            continue
        error = abs(float(value) - float(record.gold_value))
        errors.append(error)
        if error <= record.tolerance:
            within_tolerance += 1
    denominator = len(gold_present)
    return {
        "gold_present_count": denominator,
        "valid_value_pair_count": len(errors),
        "value_coverage": len(errors) / denominator if denominator else None,
        "missing_or_non_numeric_value_count": missing_values,
        "invalid_unit_count": invalid_units,
        "mae_valid_pairs": sum(errors) / len(errors) if errors else None,
        "tolerance_accuracy_all_gold_present": (
            within_tolerance / denominator if denominator else None
        ),
        "tolerance_accuracy_valid_pairs": (
            within_tolerance / len(errors) if errors else None
        ),
    }


def mixed_fact_metrics(
    records: Sequence[FactEvaluationRecord],
) -> Dict[str, Any]:
    if not records:
        raise ApixabanEvaluationError("Evaluation records must not be empty")
    by_question: Dict[str, List[FactEvaluationRecord]] = {}
    for record in records:
        by_question.setdefault(record.question_id, []).append(record)
    boolean_records = [r for r in records if r.question_type == "boolean"]
    numeric_records = [r for r in records if r.question_type == "numeric"]
    if not boolean_records or not numeric_records:
        raise ApixabanEvaluationError(
            "Mixed evaluation requires boolean and numeric records"
        )
    boolean = _classification_metrics(boolean_records, FACT_LABELS)
    numeric_status = _classification_metrics(
        numeric_records, NUMERIC_STATUS_LABELS
    )
    numeric_values = _numeric_value_metrics(numeric_records)
    exact_count = sum(_typed_exact_match(record) for record in records)
    per_question = {}
    for question_id, question_records in sorted(by_question.items()):
        question_type = question_records[0].question_type
        classification = _classification_metrics(
            question_records,
            FACT_LABELS if question_type == "boolean" else NUMERIC_STATUS_LABELS,
        )
        item = {
            "question_type": question_type,
            "count": len(question_records),
            "typed_exact_match": sum(
                _typed_exact_match(record) for record in question_records
            )
            / len(question_records),
            "classification": classification,
        }
        if question_type == "numeric":
            item["numeric_values"] = _numeric_value_metrics(question_records)
        per_question[question_id] = item
    boolean_questions = [
        item for item in per_question.values()
        if item["question_type"] == "boolean"
    ]
    numeric_questions = [
        item for item in per_question.values()
        if item["question_type"] == "numeric"
    ]
    numeric_maes = [
        item["numeric_values"]["mae_valid_pairs"]
        for item in numeric_questions
        if item["numeric_values"]["mae_valid_pairs"] is not None
    ]
    tolerance_accuracies = [
        item["numeric_values"][
            "tolerance_accuracy_all_gold_present"
        ]
        for item in numeric_questions
        if item["numeric_values"][
            "tolerance_accuracy_all_gold_present"
        ] is not None
    ]
    return {
        "count": len(records),
        "typed_exact_match": exact_count / len(records),
        "boolean": boolean,
        "numeric_status": numeric_status,
        "numeric_values": numeric_values,
        "macro_by_question": {
            "typed_exact_match": sum(
                item["typed_exact_match"] for item in per_question.values()
            )
            / len(per_question),
            "boolean_macro_f1": sum(
                item["classification"]["macro_f1"]
                for item in boolean_questions
            )
            / len(boolean_questions),
            "numeric_status_macro_f1": sum(
                item["classification"]["macro_f1"]
                for item in numeric_questions
            )
            / len(numeric_questions),
            "numeric_mae_valid_questions": (
                sum(numeric_maes) / len(numeric_maes)
                if numeric_maes else None
            ),
            "numeric_tolerance_accuracy_all_gold_present": sum(
                tolerance_accuracies
            ) / len(tolerance_accuracies) if tolerance_accuracies else None,
            "numeric_tolerance_evaluable_question_count": len(
                tolerance_accuracies
            ),
        },
        "per_question": per_question,
    }


def _interval(interval: BootstrapInterval) -> Dict[str, Any]:
    return {
        "estimate": interval.estimate,
        "lower": interval.lower,
        "upper": interval.upper,
        "confidence": interval.confidence,
        "samples": interval.samples,
        "cluster_count": interval.cluster_count,
    }


def mixed_fact_bootstrap(
    records: Sequence[FactEvaluationRecord],
    samples: int,
    seed: int,
) -> Dict[str, Any]:
    statistics = {
        "typed_exact_match": lambda rows: mixed_fact_metrics(rows)[
            "typed_exact_match"
        ],
        "boolean_macro_f1": lambda rows: mixed_fact_metrics(rows)["boolean"][
            "macro_f1"
        ],
        "unknown_f1": lambda rows: _classification_metrics(
            rows,
            FACT_LABELS,
        )["unknown_f1"],
    }
    intervals = {
        name: _interval(
            clustered_bootstrap(
                records,
                cluster_key=lambda item: item.patient_id,
                statistic=statistic,
                samples=samples,
                seed=seed,
            )
        )
        for name, statistic in statistics.items()
    }
    numeric_gold_present = [
        record
        for record in records
        if record.question_type == "numeric"
        and record.gold_status == "present"
    ]
    if numeric_gold_present:
        intervals["numeric_tolerance_accuracy_all_gold_present"] = _interval(
            clustered_bootstrap(
                numeric_gold_present,
                cluster_key=lambda item: item.patient_id,
                statistic=lambda rows: _numeric_value_metrics(rows)[
                    "tolerance_accuracy_all_gold_present"
                ],
                samples=samples,
                seed=seed,
            )
        )
    return intervals


def records_from_documents(
    benchmark: Mapping[str, Any],
    split: Mapping[str, Any],
    predictions: Mapping[str, Any],
    tolerance_policy: Mapping[str, Any],
    split_name: str,
) -> Tuple[FactEvaluationRecord, ...]:
    questions = question_index()
    patient_ids = set(split["splits"][split_name]["patient_ids"])
    gold = {
        (item["patient_id"], item["question_id"]): item
        for item in benchmark["assessments"]
        if item["patient_id"] in patient_ids
    }
    predicted = {
        (item["patient_id"], item["question_id"]): item
        for item in predictions["predictions"]
    }
    unexpected = set(predicted) - set(gold)
    if unexpected:
        raise ApixabanEvaluationError(
            "Predictions contain rows outside the selected split"
        )
    tolerances = tolerance_policy["absolute_tolerance_by_question"]
    records = []
    for key, gold_item in sorted(gold.items()):
        prediction = predicted.get(key)
        question = questions[gold_item["question_id"]]
        records.append(
            FactEvaluationRecord(
                patient_id=gold_item["patient_id"],
                question_id=gold_item["question_id"],
                question_type=gold_item["question_type"],
                gold_status=gold_item["fact_status"],
                predicted_status=(
                    prediction["fact_status"] if prediction else None
                ),
                gold_value=gold_item["value"],
                predicted_value=prediction["value"] if prediction else None,
                gold_unit=question["canonical_unit"],
                predicted_unit=prediction["unit"] if prediction else None,
                tolerance=(
                    tolerances[gold_item["question_id"]]
                    if gold_item["question_type"] == "numeric"
                    else 0.0
                ),
            )
        )
    return tuple(records)


def evaluate_apixaban_predictions(
    benchmark_path: Path,
    split_path: Path,
    prediction_path: Path,
    split_name: str,
    bootstrap_samples: int = 1000,
    code_commit: Optional[str] = None,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Dict[str, Any]:
    if split_name not in {"train", "validation", "test"}:
        raise ApixabanEvaluationError("Unsupported split name")
    for path in (benchmark_path, split_path, prediction_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanEvaluationError(
                f"Restricted evaluation input is not owner-only: {path}"
            )
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=required_source_sha256,
        required_counts=required_counts,
    )
    split = load_apixaban_split_manifest(split_path)
    if split["status"] != "frozen" or not split["freeze"]["test_locked"]:
        raise ApixabanSplitError("Evaluation requires the frozen benchmark split")
    benchmark_hash = file_sha256(benchmark_path)
    if split["dataset"]["benchmark_sha256"] != benchmark_hash:
        raise ApixabanEvaluationError("Benchmark does not match frozen split")
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    validate_prediction_set(predictions)
    if predictions["benchmark_sha256"] != benchmark_hash:
        raise ApixabanEvaluationError("Prediction benchmark hash mismatch")
    if predictions["split_manifest_sha256"] != split["manifest_sha256"]:
        raise ApixabanEvaluationError("Prediction split hash mismatch")
    if predictions["split_name"] != split_name:
        raise ApixabanEvaluationError("Prediction split name mismatch")
    policy = exact_source_tolerance_policy()
    validate_tolerance_policy(policy)
    records = records_from_documents(
        benchmark, split, predictions, policy, split_name
    )
    metrics = mixed_fact_metrics(records)
    bootstrap = mixed_fact_bootstrap(
        records, bootstrap_samples, split["policy"]["seed"]
    )
    runtime_commit = code_commit or current_git_commit()
    report = {
        "report_version": EVALUATION_REPORT_VERSION,
        "run_id": canonical_sha256(
            {
                "benchmark_sha256": benchmark_hash,
                "split_manifest_sha256": split["manifest_sha256"],
                "split_name": split_name,
                "prediction_set_sha256": file_sha256(prediction_path),
                "tolerance_policy_sha256": canonical_sha256(policy),
                "bootstrap_samples": bootstrap_samples,
                "code_commit": runtime_commit,
            }
        ),
        "generated_at": _now(),
        "provenance": {
            "benchmark_sha256": benchmark_hash,
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": split_name,
            "split_seed": split["policy"]["seed"],
            "prediction_set_sha256": file_sha256(prediction_path),
            "model_id": predictions["model_id"],
            "prompt_version": predictions["prompt_version"],
            "prediction_code_commit": predictions["code_commit"],
            "code_commit": runtime_commit,
        },
        "tolerance_policy": policy,
        "metrics": metrics,
        "bootstrap": bootstrap,
        "limitations": [
            "Numeric zero tolerance measures exact released source-value "
            "extraction, not clinical equivalence.",
            "MAE is computed only on valid value/unit pairs; value coverage "
            "and all-gold-present tolerance accuracy prevent selective "
            "prediction from being hidden.",
            "The frozen test split must not guide model, prompt, retriever, "
            "or threshold selection.",
        ],
        "disclosure_note": (
            "Restricted aggregate evaluation derived from MIMIC data. Keep "
            "local unless separately approved for disclosure."
        ),
    }
    validate_document(report, EVALUATION_REPORT_SCHEMA)
    return report


def render_apixaban_evaluation_markdown(report: Mapping[str, Any]) -> str:
    metrics = report["metrics"]
    numeric = metrics["numeric_values"]
    lines = [
        "# Restricted Apixaban fact evaluation",
        "",
        "> Research evaluation only; not a clinical decision tool.",
        "",
        "## Provenance",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Split: `{report['provenance']['split_name']}`",
        f"- Split manifest: `{report['provenance']['split_manifest_sha256']}`",
        f"- Model: `{report['provenance']['model_id']}`",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Typed exact match | {metrics['typed_exact_match']:.6f} |",
        f"| Boolean macro-F1 | {metrics['boolean']['macro_f1']:.6f} |",
        f"| Boolean micro-F1 | {metrics['boolean']['micro_f1']:.6f} |",
        f"| Boolean unknown-F1 | {metrics['boolean']['unknown_f1']:.6f} |",
        f"| Numeric status macro-F1 | {metrics['numeric_status']['macro_f1']:.6f} |",
        f"| Numeric value coverage | {numeric['value_coverage'] if numeric['value_coverage'] is not None else 'n/a'} |",
        f"| Numeric MAE (valid pairs) | {numeric['mae_valid_pairs'] if numeric['mae_valid_pairs'] is not None else 'n/a'} |",
        f"| Numeric tolerance accuracy (all gold present) | {numeric['tolerance_accuracy_all_gold_present'] if numeric['tolerance_accuracy_all_gold_present'] is not None else 'n/a'} |",
        "",
        "## Patient-cluster bootstrap",
        "",
        "| Metric | Estimate | 95% interval | Patients |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, interval in report["bootstrap"].items():
        lines.append(
            f"| `{name}` | {interval['estimate']:.6f} | "
            f"[{interval['lower']:.6f}, {interval['upper']:.6f}] | "
            f"{interval['cluster_count']} |"
        )
    lines.extend(
        [
            "",
            "## Per-question metrics",
            "",
            "| Question ID | Type | Count | Exact match | Macro-F1 | Unknown-F1 | Value coverage | MAE | Tolerance accuracy |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for question_id, item in metrics["per_question"].items():
        numeric_item = item.get("numeric_values", {})
        values = []
        for name in (
            "value_coverage",
            "mae_valid_pairs",
            "tolerance_accuracy_all_gold_present",
        ):
            value = numeric_item.get(name)
            values.append("n/a" if value is None else f"{value:.6f}")
        lines.append(
            f"| `{question_id}` | {item['question_type']} | "
            f"{item['count']} | {item['typed_exact_match']:.6f} | "
            f"{item['classification']['macro_f1']:.6f} | "
            f"{item['classification']['unknown_f1']:.6f} | "
            f"{values[0]} | {values[1]} | {values[2]} |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def write_apixaban_evaluation_report(
    report: Dict[str, Any], output_directory: Path
) -> Tuple[Path, Path]:
    assert_restricted_local_path(output_directory)
    validate_document(report, EVALUATION_REPORT_SCHEMA)
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "report.json"
    markdown_path = output_directory / "report.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError("Refusing to overwrite evaluation report")
    payloads = (
        (json_path, json.dumps(report, indent=2, sort_keys=True) + "\n"),
        (markdown_path, render_apixaban_evaluation_markdown(report)),
    )
    written = []
    try:
        for path, payload in payloads:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path
