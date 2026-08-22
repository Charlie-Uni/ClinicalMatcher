"""Deterministic abstention projection for restricted Apixaban facts."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple

from .apixaban_benchmark import (
    file_sha256,
    validate_apixaban_benchmark,
)
from .apixaban_contract import (
    KNOWN_FACT_EMPTY_EVIDENCE_EXCEPTION,
    known_fact_allows_empty_evidence,
    load_question_catalog,
    question_index,
)
from .apixaban_evaluation import validate_prediction_set
from .apixaban_neurosymbolic_audit import (
    build_neurosymbolic_readiness_report,
)
from .apixaban_split import (
    load_apixaban_split_manifest,
    write_private_json,
)
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


POLICY_VERSION = "1.1.0"
REPORT_VERSION = "1.1.0"
REPORT_SCHEMAS = {
    "1.0.0": "schemas/apixaban-abstention-report-1.0.0.schema.json",
    "1.1.0": "schemas/apixaban-abstention-report-1.1.0.schema.json",
}
REASON_CODES = (
    "invalid_schema",
    "unusable_evidence",
    "missing_evidence",
    "incompatible_unit",
    "verifier_conflict",
    "missing_fact",
)
POLICY_PRECEDENCE = REASON_CODES


class ApixabanAbstentionError(ValueError):
    """Raised when the deterministic abstention contract is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def abstention_policy() -> Dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "reason_codes": list(REASON_CODES),
        "precedence": list(POLICY_PRECEDENCE),
        "known_fact_requires_patient_local_evidence": True,
        "known_fact_empty_evidence_exceptions": [
            dict(KNOWN_FACT_EMPTY_EVIDENCE_EXCEPTION)
        ],
        "unit_must_equal_catalog_contract": True,
        "unknown_probability": None,
        "probabilities_used": False,
        "test_labels_used": False,
    }


def _legacy_abstention_policy_1_0_0() -> Dict[str, Any]:
    policy = abstention_policy()
    policy["policy_version"] = "1.0.0"
    policy.pop("known_fact_empty_evidence_exceptions")
    return policy


def _invalid_model_schema(prediction: Mapping[str, Any]) -> bool:
    return (
        prediction["abstention_reason"]
        == "invalid_model_structured_output"
        or "local_llm.structured_invalid" in prediction["trace_ids"]
    )


def _reason_for(
    prediction: Mapping[str, Any],
    question: Mapping[str, Any],
    known_evidence_ids: Set[str],
    conflict: bool,
) -> Optional[str]:
    if _invalid_model_schema(prediction):
        return "invalid_schema"
    cited = set(prediction["evidence_ids"])
    if not cited.issubset(known_evidence_ids):
        return "unusable_evidence"
    if (
        prediction["fact_status"] != "unknown"
        and not cited
        and not known_fact_allows_empty_evidence(question, prediction)
    ):
        return "missing_evidence"
    if prediction["unit"] != question["canonical_unit"]:
        return "incompatible_unit"
    if conflict:
        return "verifier_conflict"
    if prediction["fact_status"] == "unknown":
        return "missing_fact"
    return None


def _project_prediction(
    prediction: Mapping[str, Any], reason: Optional[str]
) -> Dict[str, Any]:
    projected = dict(prediction)
    projected["trace_ids"] = list(prediction["trace_ids"])
    if reason is None:
        return projected
    projected.update(
        {
            "fact_status": "unknown",
            "value": None,
            "abstained": True,
            "abstention_reason": reason,
            "trace_ids": list(
                dict.fromkeys(
                    [
                        *prediction["trace_ids"],
                        f"deterministic_abstention.{reason}",
                    ]
                )
            ),
        }
    )
    if reason == "unusable_evidence":
        projected["evidence_ids"] = []
    return projected


def _typed_correct(
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
    canonical_unit: Optional[str],
) -> bool:
    if prediction["fact_status"] != gold["fact_status"]:
        return False
    if prediction["fact_status"] == "unknown":
        return True
    if prediction["value"] != gold["value"]:
        return False
    return prediction["unit"] == canonical_unit


def _operating_point(
    predictions: Sequence[Mapping[str, Any]],
    gold_by_key: Mapping[Tuple[str, str], Mapping[str, Any]],
    questions: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    answered = [item for item in predictions if item["fact_status"] != "unknown"]
    errors = sum(
        not _typed_correct(
            gold_by_key[(item["patient_id"], item["question_id"])],
            item,
            questions[item["question_id"]]["canonical_unit"],
        )
        for item in answered
    )
    total = len(predictions)
    return {
        "answered_count": len(answered),
        "abstained_count": total - len(answered),
        "total_count": total,
        "coverage": len(answered) / total,
        "error_count": errors,
        "risk": errors / len(answered) if answered else None,
    }


def validate_abstention_report(document: Dict[str, Any]) -> None:
    report_version = document.get("report_version")
    schema = REPORT_SCHEMAS.get(report_version)
    if schema is None:
        raise ApixabanAbstentionError(
            f"Unsupported abstention report version: {report_version!r}"
        )
    validate_document(document, schema)
    counts = document["counts"]
    if counts["retained_known_count"] + counts["abstained_count"] != counts[
        "row_count"
    ]:
        raise ApixabanAbstentionError("Projection counts do not reconcile")
    if sum(document["reason_counts"].values()) != counts["abstained_count"]:
        raise ApixabanAbstentionError("Abstention reasons do not reconcile")
    if counts["decision_changed_count"] > counts["metadata_changed_count"]:
        raise ApixabanAbstentionError(
            "Decision changes cannot exceed metadata changes"
        )
    if counts["metadata_changed_count"] > counts["row_count"]:
        raise ApixabanAbstentionError("Metadata changes exceed row count")
    for name, point in document["coverage_risk_operating_points"].items():
        if point["answered_count"] + point["abstained_count"] != point[
            "total_count"
        ]:
            raise ApixabanAbstentionError(f"{name} counts do not reconcile")
        expected_coverage = point["answered_count"] / point["total_count"]
        if point["coverage"] != expected_coverage:
            raise ApixabanAbstentionError(f"{name} coverage is inconsistent")
        expected_risk = (
            point["error_count"] / point["answered_count"]
            if point["answered_count"]
            else None
        )
        if point["risk"] != expected_risk:
            raise ApixabanAbstentionError(f"{name} risk is inconsistent")
    expected_policy = (
        abstention_policy()
        if report_version == "1.1.0"
        else _legacy_abstention_policy_1_0_0()
    )
    if document["policy"] != expected_policy:
        raise ApixabanAbstentionError(
            f"Abstention policy is not frozen {report_version}"
        )
    conflict_input = document["verifier_conflict_input"]
    if conflict_input["status"] == "not_evaluable" and (
        conflict_input["provided_pair_count"] != 0
    ):
        raise ApixabanAbstentionError(
            "Unevaluable verifier conflicts cannot contain pairs"
        )


def validate_abstention_outputs(
    projection: Dict[str, Any], report: Dict[str, Any]
) -> None:
    validate_prediction_set(projection)
    validate_abstention_report(report)
    provenance = report["provenance"]
    if provenance["projected_prediction_content_sha256"] != canonical_sha256(
        projection
    ):
        raise ApixabanAbstentionError(
            "Projected prediction content hash mismatch"
        )
    if provenance["projected_model_id"] != projection["model_id"]:
        raise ApixabanAbstentionError("Projected model identity mismatch")
    if provenance["split_name"] != projection["split_name"]:
        raise ApixabanAbstentionError("Projected split identity mismatch")


def apply_deterministic_abstention(
    *,
    prediction_set: Dict[str, Any],
    staging_corpus: Dict[str, Any],
    expected_patient_ids: Sequence[str],
    gold_by_key: Mapping[Tuple[str, str], Mapping[str, Any]],
    source_prediction_sha256: str,
    split_manifest_sha256: str,
    staging_corpus_sha256: str,
    verifier_conflict_keys: Optional[Set[Tuple[str, str]]] = None,
    verifier_conflict_status: str = "not_evaluable",
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Create a separate fail-closed projection and aggregate report."""

    if verifier_conflict_status not in {"evaluated", "not_evaluable"}:
        raise ApixabanAbstentionError("Unsupported verifier conflict status")
    conflicts = verifier_conflict_keys or set()
    if verifier_conflict_status == "not_evaluable" and conflicts:
        raise ApixabanAbstentionError(
            "Verifier conflict pairs require an evaluated source"
        )
    catalog = load_question_catalog()
    questions = question_index(catalog)
    validate_prediction_set(prediction_set, catalog)
    validate_apixaban_staging_corpus(staging_corpus)
    expected_patients = set(expected_patient_ids)
    staging_by_patient = {
        item["patient_id"]: item for item in staging_corpus["patients"]
    }
    if not expected_patients.issubset(staging_by_patient):
        raise ApixabanAbstentionError(
            "Expected split patient is absent from staging corpus"
        )
    evidence_by_patient = {
        patient_id: {
            item["evidence_id"]
            for item in staging_by_patient[patient_id]["evidence"]
        }
        for patient_id in expected_patients
    }
    expected_keys = {
        (patient_id, question_id)
        for patient_id in expected_patients
        for question_id in questions
    }
    observed_keys = {
        (item["patient_id"], item["question_id"])
        for item in prediction_set["predictions"]
    }
    if observed_keys != expected_keys or set(gold_by_key) != expected_keys:
        raise ApixabanAbstentionError(
            "Prediction and gold must cover the exact split grid"
        )
    if not conflicts.issubset(expected_keys):
        raise ApixabanAbstentionError(
            "Verifier conflict key is outside the split grid"
        )

    projected_rows = []
    reasons = Counter()
    decision_changed = 0
    metadata_changed = 0
    for prediction in prediction_set["predictions"]:
        key = (prediction["patient_id"], prediction["question_id"])
        reason = _reason_for(
            prediction,
            questions[prediction["question_id"]],
            evidence_by_patient[prediction["patient_id"]],
            key in conflicts,
        )
        projected = _project_prediction(prediction, reason)
        projected_rows.append(projected)
        if reason is not None:
            reasons[reason] += 1
        if projected != prediction:
            metadata_changed += 1
        decision_fields = (
            "fact_status",
            "value",
            "unit",
            "abstained",
            "evidence_ids",
        )
        if any(projected[field] != prediction[field] for field in decision_fields):
            decision_changed += 1

    commit = code_commit or current_git_commit()
    timestamp = generated_at or _now()
    policy = abstention_policy()
    configuration = {
        "policy": policy,
        "source_prediction_sha256": source_prediction_sha256,
        "verifier_conflict_status": verifier_conflict_status,
        "verifier_conflict_keys_sha256": canonical_sha256(sorted(conflicts)),
    }
    projection = {
        "prediction_set_version": "1.2.0",
        "benchmark_sha256": prediction_set["benchmark_sha256"],
        "split_manifest_sha256": prediction_set["split_manifest_sha256"],
        "split_name": prediction_set["split_name"],
        "model_id": (
            f"{prediction_set['model_id']}+deterministic-abstention-{POLICY_VERSION}"
        ),
        "prompt_version": prediction_set["prompt_version"],
        "inference_config_sha256": canonical_sha256(configuration),
        "generated_at": timestamp,
        "code_commit": commit,
        "predictions": projected_rows,
    }
    validate_prediction_set(projection, catalog)

    before = _operating_point(
        prediction_set["predictions"], gold_by_key, questions
    )
    after = _operating_point(projected_rows, gold_by_key, questions)
    row_count = len(projected_rows)
    abstained_count = sum(
        item["fact_status"] == "unknown" for item in projected_rows
    )
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": timestamp,
        "code_commit": commit,
        "provenance": {
            "source_prediction_sha256": source_prediction_sha256,
            "projected_prediction_content_sha256": canonical_sha256(projection),
            "staging_corpus_sha256": staging_corpus_sha256,
            "split_manifest_sha256": split_manifest_sha256,
            "question_catalog_sha256": catalog["catalog_sha256"],
            "split_name": prediction_set["split_name"],
            "source_model_id": prediction_set["model_id"],
            "projected_model_id": projection["model_id"],
        },
        "policy": policy,
        "counts": {
            "patient_count": len(expected_patients),
            "row_count": row_count,
            "retained_known_count": row_count - abstained_count,
            "abstained_count": abstained_count,
            "decision_changed_count": decision_changed,
            "metadata_changed_count": metadata_changed,
        },
        "reason_counts": {
            code: reasons[code] for code in REASON_CODES
        },
        "coverage_risk_operating_points": {
            "before_policy": before,
            "after_policy": after,
        },
        "verifier_conflict_input": {
            "status": verifier_conflict_status,
            "provided_pair_count": len(conflicts),
            "reason": (
                None
                if verifier_conflict_status == "evaluated"
                else "criterion_level_model_verifier_comparison_unavailable"
            ),
        },
        "limitations": [
            "These are deterministic operating points, not probabilistic calibration.",
            "Risk is typed exact-match error among answered note-grounded facts.",
            "The projection is not a clinical eligibility decision.",
        ],
    }
    validate_abstention_outputs(projection, report)
    return projection, report


def run_deterministic_abstention(
    *,
    prediction_path: Path,
    benchmark_path: Path,
    staging_corpus_path: Path,
    frozen_split_path: Path,
    projection_output_path: Path,
    report_output_path: Path,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Tuple[Path, Path]:
    inputs = (
        prediction_path,
        benchmark_path,
        staging_corpus_path,
        frozen_split_path,
    )
    for path in inputs:
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanAbstentionError(
                f"Restricted abstention input is not owner-only: {path}"
            )
    prediction_set = json.loads(prediction_path.read_text(encoding="utf-8"))
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    staging = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    validate_prediction_set(prediction_set)
    validate_apixaban_benchmark(benchmark)
    validate_apixaban_staging_corpus(staging)
    split = load_apixaban_split_manifest(frozen_split_path)
    if split["status"] != "frozen":
        raise ApixabanAbstentionError("Abstention requires a frozen split")
    split_name = prediction_set["split_name"]
    if prediction_set["split_manifest_sha256"] != split["manifest_sha256"]:
        raise ApixabanAbstentionError("Prediction split lineage mismatch")
    benchmark_sha = file_sha256(benchmark_path)
    staging_sha = file_sha256(staging_corpus_path)
    if prediction_set["benchmark_sha256"] != benchmark_sha:
        raise ApixabanAbstentionError("Prediction benchmark lineage mismatch")
    if split["dataset"]["benchmark_sha256"] != benchmark_sha:
        raise ApixabanAbstentionError("Split benchmark lineage mismatch")
    if split["dataset"]["staging_corpus_sha256"] != staging_sha:
        raise ApixabanAbstentionError("Split staging lineage mismatch")
    expected_patients = split["splits"][split_name]["patient_ids"]
    gold_by_key = {
        (item["patient_id"], item["question_id"]): item
        for item in benchmark["assessments"]
        if item["patient_id"] in set(expected_patients)
    }

    build_neurosymbolic_readiness_report(
        prediction_set=prediction_set,
        staging_corpus=staging,
        expected_patient_ids=expected_patients,
        prediction_set_sha256=file_sha256(prediction_path),
        staging_corpus_sha256=staging_sha,
        split_manifest_sha256=split["manifest_sha256"],
        generated_at=generated_at,
        code_commit=code_commit,
    )
    projection, report = apply_deterministic_abstention(
        prediction_set=prediction_set,
        staging_corpus=staging,
        expected_patient_ids=expected_patients,
        gold_by_key=gold_by_key,
        source_prediction_sha256=file_sha256(prediction_path),
        split_manifest_sha256=split["manifest_sha256"],
        staging_corpus_sha256=staging_sha,
        generated_at=generated_at,
        code_commit=code_commit,
    )
    validate_abstention_outputs(projection, report)
    written_projection = write_private_json(projection, projection_output_path)
    try:
        written_report = write_private_json(report, report_output_path)
    except BaseException:
        written_projection.unlink(missing_ok=True)
        raise
    return written_projection, written_report
