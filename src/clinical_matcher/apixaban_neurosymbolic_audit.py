"""Aggregate, restricted audit of real Apixaban model fact outputs.

The released benchmark defines note-grounded facts, not trial eligibility.
This module therefore audits only checks supported by the real prediction and
staging contracts. Criterion polarity, temporal eligibility, negation traces,
and model-versus-verifier conflicts remain explicitly not evaluable until a
reviewed criterion binding exists.
"""

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple

from .apixaban_benchmark import file_sha256
from .apixaban_contract import load_question_catalog, question_index
from .apixaban_evaluation import validate_prediction_set
from .apixaban_split import (
    load_apixaban_split_manifest,
    write_private_json,
)
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .splits import current_git_commit
from .validation import validate_document


AUDIT_VERSION = "1.0.0"
AUDIT_SCHEMA = "schemas/apixaban-neurosymbolic-audit-1.0.0.schema.json"
NOT_EVALUABLE_REASONS = {
    "time": "source_has_no_index_or_observation_dates",
    "negation": "prediction_contract_has_no_claim_negation_trace",
    "criterion_polarity": "catalog_declares_fact_only_no_direct_mapping",
}


class ApixabanNeurosymbolicAuditError(ValueError):
    """Raised when an audit input or report violates its frozen boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _check_counts(
    *,
    row_count: int,
    evaluable: int,
    passed: int,
    failed: int,
    not_evaluable: int,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "evaluable_count": evaluable,
        "pass_count": passed,
        "fail_count": failed,
        "not_evaluable_count": not_evaluable,
        "not_evaluable_reason": reason,
        "complete_for_declared_boundary": (
            evaluable + not_evaluable == row_count
            and passed + failed == evaluable
        ),
    }


def validate_neurosymbolic_audit_report(document: Dict[str, Any]) -> None:
    validate_document(document, AUDIT_SCHEMA)
    population = document["population"]
    row_count = population["row_count"]
    if population["boolean_row_count"] + population["numeric_row_count"] != row_count:
        raise ApixabanNeurosymbolicAuditError(
            "Question-type counts do not reconcile"
        )
    if (
        population["present_count"]
        + population["absent_count"]
        + population["unknown_count"]
        != row_count
    ):
        raise ApixabanNeurosymbolicAuditError(
            "Fact-status counts do not reconcile"
        )

    for name, check in document["checks"].items():
        if check["evaluable_count"] + check["not_evaluable_count"] != row_count:
            raise ApixabanNeurosymbolicAuditError(
                f"{name} coverage does not reconcile"
            )
        if check["pass_count"] + check["fail_count"] != check["evaluable_count"]:
            raise ApixabanNeurosymbolicAuditError(
                f"{name} outcomes do not reconcile"
            )
        if check["complete_for_declared_boundary"] is not True:
            raise ApixabanNeurosymbolicAuditError(
                f"{name} must account for every row"
            )
        if check["not_evaluable_count"] and not check["not_evaluable_reason"]:
            raise ApixabanNeurosymbolicAuditError(
                f"{name} must explain unevaluable rows"
            )
        if not check["not_evaluable_count"] and check["not_evaluable_reason"]:
            raise ApixabanNeurosymbolicAuditError(
                f"{name} cannot declare an unused boundary reason"
            )

    conflicts = document["model_verifier_conflicts"]
    comparisons = conflicts["comparison_count"]
    conflict_count = conflicts["conflict_count"]
    if conflict_count > comparisons:
        raise ApixabanNeurosymbolicAuditError(
            "Conflict count cannot exceed comparison count"
        )
    expected_rate = conflict_count / comparisons if comparisons else None
    if conflicts["conflict_rate"] != expected_rate:
        raise ApixabanNeurosymbolicAuditError("Conflict rate is inconsistent")
    if comparisons == 0 and conflicts["status"] != "not_evaluable":
        raise ApixabanNeurosymbolicAuditError(
            "Zero comparisons cannot be reported as an evaluated conflict rate"
        )

    integrity_failures = sum(
        document["checks"][name]["fail_count"]
        for name in ("numeric_type", "unit_contract", "evidence_link", "missingness")
    )
    gate = document["release_gate"]
    if gate["fact_integrity_checks_pass"] != (integrity_failures == 0):
        raise ApixabanNeurosymbolicAuditError(
            "Fact-integrity gate does not match check failures"
        )
    if gate["eligibility_audit_complete"] or gate["p4_2_complete"]:
        raise ApixabanNeurosymbolicAuditError(
            "Fact-only output cannot complete the eligibility audit"
        )
    if document["review_required_count"] > row_count:
        raise ApixabanNeurosymbolicAuditError(
            "Review-required count cannot exceed row count"
        )


def build_neurosymbolic_readiness_report(
    *,
    prediction_set: Dict[str, Any],
    staging_corpus: Dict[str, Any],
    expected_patient_ids: Sequence[str],
    prediction_set_sha256: str,
    staging_corpus_sha256: str,
    split_manifest_sha256: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit real fact outputs without reading or emitting patient text."""

    catalog = load_question_catalog()
    validate_prediction_set(prediction_set, catalog)
    validate_apixaban_staging_corpus(staging_corpus)
    questions = question_index(catalog)
    expected_patients = set(expected_patient_ids)
    if not expected_patients:
        raise ApixabanNeurosymbolicAuditError(
            "Expected split patient membership must not be empty"
        )

    staging_by_patient = {
        patient["patient_id"]: patient for patient in staging_corpus["patients"]
    }
    if not expected_patients.issubset(staging_by_patient):
        raise ApixabanNeurosymbolicAuditError(
            "Split contains a patient absent from the staging corpus"
        )
    expected_pairs = {
        (patient_id, question_id)
        for patient_id in expected_patients
        for question_id in questions
    }
    observed_pairs = {
        (item["patient_id"], item["question_id"])
        for item in prediction_set["predictions"]
    }
    if observed_pairs != expected_pairs:
        raise ApixabanNeurosymbolicAuditError(
            "Prediction set does not cover the exact split patient-question grid"
        )

    evidence_by_patient = {
        patient_id: {
            item["evidence_id"]
            for item in staging_by_patient[patient_id]["evidence"]
        }
        for patient_id in expected_patients
    }
    row_count = len(prediction_set["predictions"])
    type_counts = Counter()
    status_counts = Counter()
    numeric_pass = 0
    unit_pass = 0
    evidence_pass = 0
    missingness_pass = 0
    review_rows: Set[Tuple[str, str]] = set()

    for prediction in prediction_set["predictions"]:
        key = (prediction["patient_id"], prediction["question_id"])
        type_counts[prediction["question_type"]] += 1
        status_counts[prediction["fact_status"]] += 1

        if prediction["question_type"] == "numeric":
            value = prediction["value"]
            numeric_valid = (
                prediction["fact_status"] == "unknown" and value is None
            ) or (
                prediction["fact_status"] == "present"
                and not isinstance(value, bool)
                and isinstance(value, (int, float))
                and math.isfinite(float(value))
            )
            if numeric_valid:
                numeric_pass += 1
            else:
                review_rows.add(key)

            expected_unit = questions[prediction["question_id"]]["canonical_unit"]
            if prediction["unit"] == expected_unit:
                unit_pass += 1
            else:
                review_rows.add(key)

        cited = set(prediction["evidence_ids"])
        evidence_valid = cited.issubset(
            evidence_by_patient[prediction["patient_id"]]
        )
        if evidence_valid:
            evidence_pass += 1
        else:
            review_rows.add(key)

        missingness_valid = (
            prediction["fact_status"] == "unknown"
            or bool(prediction["evidence_ids"])
        )
        if missingness_valid:
            missingness_pass += 1
        else:
            review_rows.add(key)

    numeric_count = type_counts["numeric"]
    checks = {
        "numeric_type": _check_counts(
            row_count=row_count,
            evaluable=numeric_count,
            passed=numeric_pass,
            failed=numeric_count - numeric_pass,
            not_evaluable=row_count - numeric_count,
            reason="not_a_numeric_question" if row_count != numeric_count else None,
        ),
        "unit_contract": _check_counts(
            row_count=row_count,
            evaluable=numeric_count,
            passed=unit_pass,
            failed=numeric_count - unit_pass,
            not_evaluable=row_count - numeric_count,
            reason="not_a_numeric_question" if row_count != numeric_count else None,
        ),
        "evidence_link": _check_counts(
            row_count=row_count,
            evaluable=row_count,
            passed=evidence_pass,
            failed=row_count - evidence_pass,
            not_evaluable=0,
        ),
        "missingness": _check_counts(
            row_count=row_count,
            evaluable=row_count,
            passed=missingness_pass,
            failed=row_count - missingness_pass,
            not_evaluable=0,
        ),
        "time": _check_counts(
            row_count=row_count,
            evaluable=0,
            passed=0,
            failed=0,
            not_evaluable=row_count,
            reason=NOT_EVALUABLE_REASONS["time"],
        ),
        "negation": _check_counts(
            row_count=row_count,
            evaluable=0,
            passed=0,
            failed=0,
            not_evaluable=row_count,
            reason=NOT_EVALUABLE_REASONS["negation"],
        ),
        "criterion_polarity": _check_counts(
            row_count=row_count,
            evaluable=0,
            passed=0,
            failed=0,
            not_evaluable=row_count,
            reason=NOT_EVALUABLE_REASONS["criterion_polarity"],
        ),
    }
    integrity_failures = sum(
        checks[name]["fail_count"]
        for name in ("numeric_type", "unit_contract", "evidence_link", "missingness")
    )
    report = {
        "audit_version": AUDIT_VERSION,
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "provenance": {
            "prediction_set_sha256": prediction_set_sha256,
            "staging_corpus_sha256": staging_corpus_sha256,
            "split_manifest_sha256": split_manifest_sha256,
            "question_catalog_sha256": catalog["catalog_sha256"],
            "split_name": prediction_set["split_name"],
            "model_id": prediction_set["model_id"],
            "prompt_version": prediction_set["prompt_version"],
        },
        "population": {
            "patient_count": len(expected_patients),
            "row_count": row_count,
            "boolean_row_count": type_counts["boolean"],
            "numeric_row_count": numeric_count,
            "present_count": status_counts["present"],
            "absent_count": status_counts["absent"],
            "unknown_count": status_counts["unknown"],
        },
        "checks": checks,
        "review_required_count": len(review_rows),
        "model_verifier_conflicts": {
            "status": "not_evaluable",
            "comparison_count": 0,
            "conflict_count": 0,
            "conflict_rate": None,
            "reason": "model_output_has_no_criterion_decision_or_reviewed_binding",
        },
        "before_after_error_analysis": {
            "status": "not_evaluable",
            "reason": "no_criterion_level_model_decision_to_compare",
        },
        "release_gate": {
            "fact_integrity_checks_pass": integrity_failures == 0,
            "eligibility_audit_complete": False,
            "p4_2_complete": False,
            "blocking_reasons": [
                NOT_EVALUABLE_REASONS["time"],
                NOT_EVALUABLE_REASONS["negation"],
                NOT_EVALUABLE_REASONS["criterion_polarity"],
                "model_output_has_no_criterion_decision_or_reviewed_binding",
            ],
        },
        "limitations": [
            "This owner-only report audits fact-output integrity, not clinical eligibility.",
            "The released source questions define no canonical clinical units.",
            "A null conflict rate means not evaluable; it must not be interpreted as zero conflicts.",
        ],
    }
    validate_neurosymbolic_audit_report(report)
    return report


def run_neurosymbolic_readiness_audit(
    *,
    prediction_path: Path,
    staging_corpus_path: Path,
    frozen_split_path: Path,
    output_path: Path,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Path:
    for path in (prediction_path, staging_corpus_path, frozen_split_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanNeurosymbolicAuditError(
                f"Restricted audit input is not owner-only: {path}"
            )
    prediction_set = json.loads(prediction_path.read_text(encoding="utf-8"))
    staging_corpus = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    split = load_apixaban_split_manifest(frozen_split_path)
    if split["status"] != "frozen":
        raise ApixabanNeurosymbolicAuditError("Audit requires a frozen split")
    split_name = prediction_set.get("split_name")
    if split_name not in split["splits"]:
        raise ApixabanNeurosymbolicAuditError("Prediction split is invalid")
    if prediction_set.get("split_manifest_sha256") != split["manifest_sha256"]:
        raise ApixabanNeurosymbolicAuditError(
            "Prediction set is not bound to the supplied frozen split"
        )
    if prediction_set.get("benchmark_sha256") != split["dataset"]["benchmark_sha256"]:
        raise ApixabanNeurosymbolicAuditError(
            "Prediction benchmark does not match the frozen split"
        )
    catalog = load_question_catalog()
    if split["dataset"]["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanNeurosymbolicAuditError(
            "Frozen split question catalog does not match the runtime catalog"
        )
    staging_sha = file_sha256(staging_corpus_path)
    if staging_sha != split["dataset"]["staging_corpus_sha256"]:
        raise ApixabanNeurosymbolicAuditError(
            "Staging corpus does not match the frozen split"
        )
    report = build_neurosymbolic_readiness_report(
        prediction_set=prediction_set,
        staging_corpus=staging_corpus,
        expected_patient_ids=split["splits"][split_name]["patient_ids"],
        prediction_set_sha256=file_sha256(prediction_path),
        staging_corpus_sha256=staging_sha,
        split_manifest_sha256=split["manifest_sha256"],
        generated_at=generated_at,
        code_commit=code_commit,
    )
    return write_private_json(report, output_path)
