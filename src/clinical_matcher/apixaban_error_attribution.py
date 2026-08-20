"""Aggregate error attribution for restricted Apixaban fact predictions.

The released benchmark contains fact labels but no evidence-relevance gold.
This module therefore separates observable error classes without claiming to
identify retrieval or reasoning causes that the available data cannot prove.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Set

from .apixaban_benchmark import (
    EXPECTED_OFFICIAL_COUNTS,
    OFFICIAL_SOURCE_SHA256,
    file_sha256,
    validate_apixaban_benchmark,
)
from .apixaban_contract import load_question_catalog, question_index
from .apixaban_evaluation import validate_prediction_set
from .apixaban_split import load_apixaban_split_manifest, write_private_json
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .splits import current_git_commit
from .validation import validate_document


REPORT_VERSION = "1.0.0"
REPORT_SCHEMA = "schemas/apixaban-error-attribution-report-1.0.0.schema.json"
ERROR_CATEGORIES = (
    "unsupported_answering",
    "unit_contract_error",
    "abstention_on_gold_known",
    "numeric_value_error",
    "fact_status_error_with_patient_local_citation",
    "other_typed_error",
)
DIMENSION_STATUS = {
    "retrieval_failure": (
        "not_evaluable",
        "benchmark_has_no_gold_evidence_relevance",
    ),
    "reasoning_failure_with_usable_evidence": (
        "not_evaluable",
        "patient_local_citation_is_not_gold_evidence_relevance",
    ),
    "numeric_value_error": ("evaluated", None),
    "unit_error": (
        "evaluated",
        "evaluated_against_source_contract_not_clinical_equivalence",
    ),
    "time_error": (
        "not_evaluable",
        "source_has_no_index_or_observation_dates",
    ),
    "negation_error": (
        "not_evaluable",
        "prediction_contract_has_no_claim_negation_trace",
    ),
    "false_abstention": (
        "not_evaluable",
        "gold_known_does_not_prove_required_evidence_was_available",
    ),
    "unsupported_answering": ("evaluated", None),
}


class ApixabanErrorAttributionError(ValueError):
    """Raised when attribution inputs or outputs violate the contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _typed_correct(
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
    canonical_unit: Optional[str],
) -> bool:
    if prediction["fact_status"] != gold["fact_status"]:
        return False
    if prediction["unit"] != canonical_unit:
        return False
    if gold["fact_status"] == "unknown":
        return prediction["value"] is None
    return prediction["value"] == gold["value"]


def _attribute_row(
    *,
    gold: Mapping[str, Any],
    prediction: Mapping[str, Any],
    canonical_unit: Optional[str],
    patient_evidence_ids: Set[str],
) -> Optional[str]:
    """Return one observable error class using frozen precedence."""

    cited = set(prediction["evidence_ids"])
    known_prediction = prediction["fact_status"] != "unknown"
    if known_prediction and (not cited or not cited.issubset(patient_evidence_ids)):
        return "unsupported_answering"
    if prediction["unit"] != canonical_unit:
        return "unit_contract_error"
    if prediction["fact_status"] == "unknown" and gold["fact_status"] != "unknown":
        return "abstention_on_gold_known"
    if (
        prediction["question_type"] == "numeric"
        and prediction["fact_status"] == "present"
        and gold["fact_status"] == "present"
        and prediction["value"] != gold["value"]
    ):
        return "numeric_value_error"
    if prediction["fact_status"] != gold["fact_status"]:
        return "fact_status_error_with_patient_local_citation"
    if not _typed_correct(gold, prediction, canonical_unit):
        return "other_typed_error"
    return None


def _dimension_report() -> Dict[str, Dict[str, Optional[str]]]:
    return {
        name: {"status": status, "reason": reason}
        for name, (status, reason) in DIMENSION_STATUS.items()
    }


def validate_error_attribution_report(document: Dict[str, Any]) -> None:
    validate_document(document, REPORT_SCHEMA)
    population = document["population"]
    attributed = sum(document["category_counts"].values())
    if attributed != population["attributed_error_count"]:
        raise ApixabanErrorAttributionError(
            "Error categories do not reconcile to attributed errors"
        )
    if attributed + population["no_attributed_error_count"] != population["row_count"]:
        raise ApixabanErrorAttributionError(
            "Attributed and non-attributed rows do not reconcile"
        )
    if document["policy"]["precedence"] != list(ERROR_CATEGORIES):
        raise ApixabanErrorAttributionError(
            "Error-attribution precedence is not frozen 1.0.0"
        )
    if document["requested_dimensions"] != _dimension_report():
        raise ApixabanErrorAttributionError(
            "Requested attribution dimensions misstate evaluability"
        )
    review = document["representative_case_review"]
    if review["status"] == "pending_authorized_environment" and review[
        "reviewed_error_count"
    ]:
        raise ApixabanErrorAttributionError(
            "Pending review cannot report reviewed cases"
        )
    if review["reviewed_error_count"] > population["attributed_error_count"]:
        raise ApixabanErrorAttributionError(
            "Reviewed error count exceeds attributed errors"
        )


def build_error_attribution_report(
    *,
    prediction_set: Dict[str, Any],
    benchmark: Dict[str, Any],
    staging_corpus: Dict[str, Any],
    expected_patient_ids: Sequence[str],
    prediction_set_sha256: str,
    benchmark_sha256: str,
    staging_corpus_sha256: str,
    split_manifest_sha256: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a PHI-free aggregate report from restricted inputs."""

    catalog = load_question_catalog()
    questions = question_index(catalog)
    validate_prediction_set(prediction_set, catalog)
    validate_apixaban_benchmark(
        benchmark, required_source_sha256=None, required_counts=None
    )
    validate_apixaban_staging_corpus(staging_corpus)
    if benchmark["contract"]["gold_evidence_status"] != "not_available_in_source":
        raise ApixabanErrorAttributionError(
            "Attribution expects the released no-evidence-gold boundary"
        )

    expected_patients = set(expected_patient_ids)
    if not expected_patients:
        raise ApixabanErrorAttributionError(
            "Expected split patient membership must not be empty"
        )
    expected_pairs = {
        (patient_id, question_id)
        for patient_id in expected_patients
        for question_id in questions
    }
    predictions_by_key = {
        (item["patient_id"], item["question_id"]): item
        for item in prediction_set["predictions"]
    }
    gold_by_key = {
        (item["patient_id"], item["question_id"]): item
        for item in benchmark["assessments"]
        if item["patient_id"] in expected_patients
    }
    if set(predictions_by_key) != expected_pairs:
        raise ApixabanErrorAttributionError(
            "Prediction set does not cover the exact split patient-question grid"
        )
    if set(gold_by_key) != expected_pairs:
        raise ApixabanErrorAttributionError(
            "Benchmark does not cover the exact split patient-question grid"
        )

    staging_by_patient = {
        item["patient_id"]: item for item in staging_corpus["patients"]
    }
    if not expected_patients.issubset(staging_by_patient):
        raise ApixabanErrorAttributionError(
            "Split contains a patient absent from the staging corpus"
        )
    evidence_by_patient = {
        patient_id: {
            evidence["evidence_id"]
            for evidence in staging_by_patient[patient_id]["evidence"]
        }
        for patient_id in expected_patients
    }

    counts: Counter[str] = Counter()
    typed_mismatch_count = 0
    known_without_usable_evidence_count = 0
    for key in sorted(expected_pairs):
        prediction = predictions_by_key[key]
        gold = gold_by_key[key]
        canonical_unit = questions[prediction["question_id"]]["canonical_unit"]
        if not _typed_correct(gold, prediction, canonical_unit):
            typed_mismatch_count += 1
        cited = set(prediction["evidence_ids"])
        patient_evidence = evidence_by_patient[prediction["patient_id"]]
        if prediction["fact_status"] != "unknown" and (
            not cited or not cited.issubset(patient_evidence)
        ):
            known_without_usable_evidence_count += 1
        category = _attribute_row(
            gold=gold,
            prediction=prediction,
            canonical_unit=canonical_unit,
            patient_evidence_ids=patient_evidence,
        )
        if category is not None:
            counts[category] += 1

    row_count = len(expected_pairs)
    category_counts = {name: counts[name] for name in ERROR_CATEGORIES}
    attributed = sum(category_counts.values())
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "provenance": {
            "prediction_set_sha256": prediction_set_sha256,
            "benchmark_sha256": benchmark_sha256,
            "staging_corpus_sha256": staging_corpus_sha256,
            "split_manifest_sha256": split_manifest_sha256,
            "question_catalog_sha256": catalog["catalog_sha256"],
            "split_name": prediction_set["split_name"],
            "model_id": prediction_set["model_id"],
            "prompt_version": prediction_set["prompt_version"],
        },
        "policy": {
            "policy_version": "1.0.0",
            "error_universe": (
                "typed_gold_mismatch_or_known_fact_evidence_or_unit_contract_violation"
            ),
            "precedence": list(ERROR_CATEGORIES),
            "diagnostic_not_causal_proof": True,
            "test_labels_used_for_design": False,
        },
        "population": {
            "patient_count": len(expected_patients),
            "row_count": row_count,
            "typed_gold_mismatch_count": typed_mismatch_count,
            "known_without_usable_evidence_count": known_without_usable_evidence_count,
            "attributed_error_count": attributed,
            "no_attributed_error_count": row_count - attributed,
        },
        "category_counts": category_counts,
        "requested_dimensions": _dimension_report(),
        "representative_case_review": {
            "status": "pending_authorized_environment",
            "reviewed_error_count": 0,
            "case_identifiers_emitted": False,
            "required_for_p4_5_completion": True,
        },
        "limitations": [
            "A patient-local citation is not independently adjudicated evidence relevance.",
            "Abstention on a known gold fact is descriptive and is not labelled false abstention.",
            "The report contains aggregates only and cannot replace authorized case review.",
        ],
    }
    validate_error_attribution_report(report)
    return report


def run_error_attribution(
    *,
    prediction_path: Path,
    benchmark_path: Path,
    staging_corpus_path: Path,
    frozen_split_path: Path,
    output_path: Path,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Path:
    """Validate restricted lineage, then write an owner-only report."""

    for path in (
        prediction_path,
        benchmark_path,
        staging_corpus_path,
        frozen_split_path,
    ):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanErrorAttributionError(
                f"Restricted attribution input is not owner-only: {path}"
            )
    prediction_set = json.loads(prediction_path.read_text(encoding="utf-8"))
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    staging_corpus = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    split = load_apixaban_split_manifest(frozen_split_path)
    if split["status"] != "frozen" or not split["freeze"]["test_locked"]:
        raise ApixabanErrorAttributionError(
            "Error attribution requires the frozen locked split"
        )
    split_name = prediction_set.get("split_name")
    if split_name not in split["splits"]:
        raise ApixabanErrorAttributionError("Prediction split is invalid")
    if prediction_set.get("split_manifest_sha256") != split["manifest_sha256"]:
        raise ApixabanErrorAttributionError(
            "Prediction set is not bound to the supplied frozen split"
        )
    catalog = load_question_catalog()
    if split["dataset"]["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanErrorAttributionError(
            "Frozen split question catalog does not match the runtime catalog"
        )
    benchmark_sha = file_sha256(benchmark_path)
    if benchmark_sha != split["dataset"]["benchmark_sha256"]:
        raise ApixabanErrorAttributionError("Benchmark does not match the frozen split")
    if prediction_set.get("benchmark_sha256") != benchmark_sha:
        raise ApixabanErrorAttributionError(
            "Prediction benchmark does not match the frozen split"
        )
    staging_sha = file_sha256(staging_corpus_path)
    if staging_sha != split["dataset"]["staging_corpus_sha256"]:
        raise ApixabanErrorAttributionError(
            "Staging corpus does not match the frozen split"
        )
    validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=OFFICIAL_SOURCE_SHA256,
        required_counts=EXPECTED_OFFICIAL_COUNTS,
    )
    report = build_error_attribution_report(
        prediction_set=prediction_set,
        benchmark=benchmark,
        staging_corpus=staging_corpus,
        expected_patient_ids=split["splits"][split_name]["patient_ids"],
        prediction_set_sha256=file_sha256(prediction_path),
        benchmark_sha256=benchmark_sha,
        staging_corpus_sha256=staging_sha,
        split_manifest_sha256=split["manifest_sha256"],
        generated_at=generated_at,
        code_commit=code_commit,
    )
    return write_private_json(report, output_path)
