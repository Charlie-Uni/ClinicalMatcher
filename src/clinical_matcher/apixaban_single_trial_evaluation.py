"""Three-axis evaluation for the frozen Apixaban single-trial diagnostic."""

import csv
import json
import os
import re
from collections import Counter
from datetime import date, datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import (
    file_sha256,
    validate_apixaban_benchmark,
)
from .apixaban_contract import load_question_catalog
from .apixaban_evaluation import validate_prediction_set
from .apixaban_single_trial import (
    RULE_IDS,
    build_intended_trial,
    load_intended_rule_contract,
    project_intended_class,
    project_mentor_reference_class,
)
from .apixaban_split import load_apixaban_split_manifest, write_private_json
from .apixaban_unit_adapter import adapt_fact_rows, load_unit_adapter_contract
from .evaluation import BootstrapInterval, clustered_bootstrap
from .ingestion.apixaban import validate_apixaban_id_map
from .ingestion.patients import assert_restricted_local_path
from .models import Evidence, Fact, Patient, TypedValue, ValueType
from .pipeline import evaluate_criterion
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


REPORT_VERSION = "1.0.0"
TRACE_VERSION = "1.0.0"
SUMMARY_RENDERER_VERSION = "1.1.0"
REPORT_SCHEMA = "schemas/apixaban-single-trial-report-1.0.0.schema.json"
RUN_CONTRACT_RESOURCE = (
    "resources/apixaban-single-trial-run-contract-1.0.0.json"
)
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 17
OUTCOMES = ("ideal", "semi-ideal", "non-ideal", "unknown")
REFERENCE_OUTCOMES = ("ideal", "semi-ideal", "non-ideal")
DECISIONS = ("eligible", "ineligible", "unknown")
EXPECTED_MENTOR_RESULTS_SHA256 = (
    "f358d18feb47997d87d27b104b0c3490d08bba913e64b33b17b75ab2c65c59d3"
)
EXPECTED_CANDIDATE_CSV_SHA256 = (
    "ff3871060b9e0ec97952d4b5bff998cb9504e7d8e3fd461edc2c976d199d70ea"
)
EXPECTED_SELECTED_PREDICTION_SHA256 = (
    "ccdd1e417253ece9b9a78d0975dfd0a116716b8c09bd673772b3806295c36ef0"
)
EXPECTED_UNSELECTED_PREDICTION_SHA256 = (
    "8fa9aa6ce9d379c71fc594981a8d20b1aec04b09e8ef8b0a955f28d7b518cc25"
)
PATIENT_ID_PATTERN = re.compile(r"^patient-[0-9a-f]{24}$")


class ApixabanSingleTrialEvaluationError(ValueError):
    """Raised when a frozen single-trial evaluation invariant fails."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any], field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _interval(interval: BootstrapInterval) -> Dict[str, Any]:
    return {
        "estimate": interval.estimate,
        "lower": interval.lower,
        "upper": interval.upper,
        "confidence": interval.confidence,
        "samples": interval.samples,
        "cluster_count": interval.cluster_count,
        "resampling_unit": "patient",
        "seed": BOOTSTRAP_SEED,
    }


def validate_single_trial_run_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_id",
        "contract_status",
        "contract_sha256",
        "contract_hash_scope",
        "decision",
        "selected_artifact",
        "unselected_artifact",
        "selection_evidence",
        "execution_scope",
    }
    if set(document) != required:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial run contract fields changed"
        )
    if document["contract_version"] != "1.0.0" or document[
        "contract_id"
    ] != "apixaban-single-trial-long-context-p4.3-validation-v1":
        raise ApixabanSingleTrialEvaluationError(
            "Unsupported single-trial run contract"
        )
    if document["contract_status"] != "owner_approved_frozen_pre_validation":
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial run contract is not owner approved"
        )
    if document["contract_hash_scope"] != (
        "canonical_json_excluding_contract_sha256"
    ) or _self_hash(document, "contract_sha256") != document["contract_sha256"]:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial run contract hash mismatch"
        )

    decision = document["decision"]
    expected_decision = {
        "selected_by": "project_owner",
        "selected_at": "2026-08-31",
        "selection_basis": "pre_existing_p2_3_fact_level_validation_results_only",
        "selection_rationale": (
            "Long-context had the stronger previously recorded fact-level "
            "typed result and uses the complete-note input policy; no "
            "single-trial three-class result had been viewed."
        ),
        "single_trial_three_class_results_seen_before_selection": False,
        "locked_test_labels_used": False,
    }
    if decision != expected_decision:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial selection decision changed"
        )

    selected = document["selected_artifact"]
    if selected != {
        "configuration": "long_context_plus_p4_3_abstention",
        "prediction_set_sha256": EXPECTED_SELECTED_PREDICTION_SHA256,
        "prediction_set_version": "1.2.0",
        "model_id": (
            "ollama/llama3.1:8b-instruct-q4_k_m@sha256:"
            "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
            "+deterministic-abstention-1.0.0"
        ),
        "prompt_version": "apixaban-23-facts-structured-1.0.0",
        "inference_config_sha256": (
            "9a512404d817711110a9e0cdc524060e4d30459f6170ef64ba373393a8fc606c"
        ),
        "status": "authorized_for_one_validation_evaluation",
    }:
        raise ApixabanSingleTrialEvaluationError(
            "Selected single-trial artifact changed"
        )
    if document["unselected_artifact"] != {
        "configuration": "structured_plus_p4_3_abstention",
        "prediction_set_sha256": EXPECTED_UNSELECTED_PREDICTION_SHA256,
        "status": "available_not_evaluated",
        "future_evaluation_requires_new_recorded_owner_decision": True,
    }:
        raise ApixabanSingleTrialEvaluationError(
            "Unselected single-trial artifact state changed"
        )
    if document["selection_evidence"] != {
        "metric": "fact_level_typed_exact_match_before_p4_3_projection",
        "denominator": 345,
        "structured": {
            "correct_count": 187,
            "typed_mismatch_count": 158,
            "error_attribution_report_sha256": (
                "b259098870081a7e3662eb8df9b404b9b5ff86bded5b0c2ae9da79378cfb50fb"
            ),
        },
        "long_context": {
            "correct_count": 211,
            "typed_mismatch_count": 134,
            "error_attribution_report_sha256": (
                "3a818785b3161ee9ae39bc05e905283340777479a7c7eb9c864e1eed9ba8d932"
            ),
        },
    }:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial selection evidence changed"
        )
    if document["execution_scope"] != {
        "split": "validation",
        "single_execution_authorized": True,
        "locked_test_authorized": False,
        "unselected_artifact_execution_authorized": False,
        "public_disclosure_requires_post_run_review": True,
    }:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial execution scope changed"
        )


def load_single_trial_run_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(RUN_CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_single_trial_run_contract(document)
    return document


def _parse_flag(value: str, field: str) -> bool:
    if value not in {"0", "1"}:
        raise ApixabanSingleTrialEvaluationError(
            f"Mentor candidate {field} must be 0 or 1"
        )
    return value == "1"


def _validate_mentor_summary(summary: Mapping[str, Any]) -> Tuple[set[int], set[int]]:
    required = {"Semi_Ideal_Candidate", "Ideal_Candidate"}
    if not required.issubset(summary):
        raise ApixabanSingleTrialEvaluationError(
            "Mentor screening summary lacks final candidate sets"
        )
    resolved = []
    for name in ("Semi_Ideal_Candidate", "Ideal_Candidate"):
        item = summary[name]
        if set(item) != {"total_matches", "percentage", "patient_numbers"}:
            raise ApixabanSingleTrialEvaluationError(
                f"Mentor screening summary fields changed: {name}"
            )
        numbers = item["patient_numbers"]
        if (
            any(type(number) is not int for number in numbers)
            or len(numbers) != len(set(numbers))
            or any(number < 1 or number > 100 for number in numbers)
            or item["total_matches"] != len(numbers)
        ):
            raise ApixabanSingleTrialEvaluationError(
                f"Mentor screening patient set is invalid: {name}"
            )
        resolved.append(set(numbers))
    semi, ideal = resolved
    if not ideal.issubset(semi):
        raise ApixabanSingleTrialEvaluationError(
            "Mentor ideal candidates must remain a subset of semi-ideal"
        )
    return semi, ideal


def load_mentor_reference(
    mentor_results_path: Path,
    candidate_csv_path: Path,
    id_map_path: Path,
) -> Dict[str, str]:
    """Validate both mentor artifacts and map their flags to pseudonyms."""

    for path in (mentor_results_path, candidate_csv_path, id_map_path):
        assert_restricted_local_path(path)
    if file_sha256(mentor_results_path) != EXPECTED_MENTOR_RESULTS_SHA256:
        raise ApixabanSingleTrialEvaluationError("Mentor results hash mismatch")
    if file_sha256(candidate_csv_path) != EXPECTED_CANDIDATE_CSV_SHA256:
        raise ApixabanSingleTrialEvaluationError("Mentor candidate CSV hash mismatch")

    summary = json.loads(mentor_results_path.read_text(encoding="utf-8"))
    summary_semi, summary_ideal = _validate_mentor_summary(summary)
    id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
    validate_apixaban_id_map(id_map)
    pseudonyms = {
        (record["note_id"], record["hadm_id"]): record["patient_id"]
        for record in id_map["records"]
    }

    with candidate_csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_fields = [
            "patient_id",
            "note_id",
            "hadm_id",
            "semi_ideal_candidate",
            "ideal_candidate",
        ]
        if reader.fieldnames != expected_fields:
            raise ApixabanSingleTrialEvaluationError(
                "Mentor candidate CSV fields changed"
            )
        rows = list(reader)
    if len(rows) != 100:
        raise ApixabanSingleTrialEvaluationError(
            "Mentor candidate CSV must contain 100 patients"
        )

    by_patient_number = {}
    reference = {}
    seen_pairs = set()
    for row in rows:
        try:
            patient_number = int(row["patient_id"])
        except ValueError as error:
            raise ApixabanSingleTrialEvaluationError(
                "Mentor candidate patient number is invalid"
            ) from error
        if patient_number in by_patient_number or not 1 <= patient_number <= 100:
            raise ApixabanSingleTrialEvaluationError(
                "Mentor candidate patient numbers must be unique 1-100"
            )
        key = (row["note_id"], row["hadm_id"])
        if key in seen_pairs or key not in pseudonyms:
            raise ApixabanSingleTrialEvaluationError(
                "Mentor candidate key does not align to the frozen ID map"
            )
        seen_pairs.add(key)
        semi = _parse_flag(row["semi_ideal_candidate"], "semi_ideal_candidate")
        ideal = _parse_flag(row["ideal_candidate"], "ideal_candidate")
        projected = project_mentor_reference_class(
            ideal_candidate=ideal,
            semi_ideal_candidate=semi,
        )
        by_patient_number[patient_number] = (semi, ideal)
        reference[pseudonyms[key]] = projected
    if set(by_patient_number) != set(range(1, 101)):
        raise ApixabanSingleTrialEvaluationError(
            "Mentor candidate patient-number grid changed"
        )
    csv_semi = {number for number, flags in by_patient_number.items() if flags[0]}
    csv_ideal = {number for number, flags in by_patient_number.items() if flags[1]}
    if csv_semi != summary_semi or csv_ideal != summary_ideal:
        raise ApixabanSingleTrialEvaluationError(
            "Mentor summary and candidate CSV flags differ"
        )
    if set(reference) != {record["patient_id"] for record in id_map["records"]}:
        raise ApixabanSingleTrialEvaluationError(
            "Mentor reference does not cover the complete ID map"
        )
    return reference


def _technical_patient(
    patient_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    source_name: str,
) -> Patient:
    facts = []
    evidence = []
    for row in rows:
        if row["fact_status"] == "unknown":
            continue
        value_type = (
            ValueType.NUMBER
            if row["question_type"] == "numeric"
            else ValueType.BOOLEAN
        )
        carrier_id = f"adapter-carrier-{row['question_id']}"
        evidence.append(
            Evidence(
                evidence_id=carrier_id,
                source_id=f"{source_name}-fact-carrier",
                text="Technical fact carrier; not clinical evidence.",
            )
        )
        facts.append(
            Fact(
                fact_id=f"adapted-fact-{row['question_id']}",
                field=row["fact_field"],
                value=TypedValue(
                    value_type=value_type,
                    value=row["value"],
                    unit=row["unit"],
                ),
                evidence_ids=(carrier_id,),
            )
        )
    return Patient(
        patient_id=patient_id,
        index_date=date(1970, 1, 1),
        facts=tuple(facts),
        evidence=tuple(evidence),
    )


def _project_patients(
    adapted_rows: Sequence[Mapping[str, Any]],
    patient_ids: Sequence[str],
    *,
    source_name: str,
) -> Dict[str, Dict[str, Any]]:
    by_patient: Dict[str, list[Mapping[str, Any]]] = {
        patient_id: [] for patient_id in patient_ids
    }
    for row in adapted_rows:
        if row["patient_id"] not in by_patient:
            raise ApixabanSingleTrialEvaluationError(
                "Adapted row is outside validation membership"
            )
        by_patient[row["patient_id"]].append(row)
    trial = build_intended_trial()
    projections = {}
    for patient_id in patient_ids:
        rows = by_patient[patient_id]
        if len(rows) != 23:
            raise ApixabanSingleTrialEvaluationError(
                "Every validation patient must have 23 adapted facts"
            )
        patient = _technical_patient(patient_id, rows, source_name=source_name)
        rule_decisions = {
            criterion.criterion_id: evaluate_criterion(patient, criterion).decision
            for criterion in trial.criteria
        }
        projections[patient_id] = {
            "class": project_intended_class(rule_decisions),
            "rule_decisions": {
                rule_id: rule_decisions[rule_id].value for rule_id in RULE_IDS
            },
            "adapter_reasons": {
                row["question_id"]: row["adapter_reason"]
                for row in rows
                if row["adapter_reason"] is not None
            },
        }
    return projections


def _axis_metrics(
    rows: Sequence[Mapping[str, str]],
    *,
    reference_labels: Sequence[str],
    candidate_labels: Sequence[str],
    conditional_known: bool,
) -> Dict[str, Any]:
    matrix = {
        reference: {candidate: 0 for candidate in candidate_labels}
        for reference in reference_labels
    }
    for row in rows:
        matrix[row["reference"]][row["candidate"]] += 1
    count = len(rows)
    exact = sum(row["reference"] == row["candidate"] for row in rows)
    candidate_counts = Counter(row["candidate"] for row in rows)
    known = count - candidate_counts["unknown"]
    interval = clustered_bootstrap(
        rows,
        cluster_key=lambda row: row["patient_id"],
        statistic=lambda sample: sum(
            item["reference"] == item["candidate"] for item in sample
        )
        / len(sample),
        samples=BOOTSTRAP_SAMPLES,
        seed=BOOTSTRAP_SEED,
    )
    metrics: Dict[str, Any] = {
        "patient_count": count,
        "reference_outcome_counts": {
            label: sum(row["reference"] == label for row in rows)
            for label in reference_labels
        },
        "candidate_outcome_counts": {
            label: candidate_counts[label] for label in candidate_labels
        },
        "candidate_known_count": known,
        "candidate_unknown_count": candidate_counts["unknown"],
        "candidate_coverage": known / count,
        "exact_agreement_count": exact,
        "complete_denominator_exact_agreement": exact / count,
        "complete_denominator_exact_agreement_ci": _interval(interval),
        "confusion_matrix": matrix,
        "conditional_three_class": None,
    }
    if conditional_known:
        metrics["conditional_three_class"] = {
            "denominator": known,
            "exact_agreement_count": sum(
                row["candidate"] != "unknown"
                and row["candidate"] == row["reference"]
                for row in rows
            ),
            "exact_agreement": (
                sum(
                    row["candidate"] != "unknown"
                    and row["candidate"] == row["reference"]
                    for row in rows
                )
                / known
                if known
                else None
            ),
            "label": "conditional_on_intended_known",
        }
    return metrics


def _per_rule_metrics(
    gold: Mapping[str, Mapping[str, Any]],
    model: Mapping[str, Mapping[str, Any]],
    patient_ids: Sequence[str],
) -> Dict[str, Any]:
    result = {}
    for rule_id in RULE_IDS:
        matrix = {
            reference: {candidate: 0 for candidate in DECISIONS}
            for reference in DECISIONS
        }
        exact = 0
        for patient_id in patient_ids:
            reference = gold[patient_id]["rule_decisions"][rule_id]
            candidate = model[patient_id]["rule_decisions"][rule_id]
            matrix[reference][candidate] += 1
            exact += reference == candidate
        result[rule_id] = {
            "patient_count": len(patient_ids),
            "exact_agreement_count": exact,
            "exact_agreement": exact / len(patient_ids),
            "confusion_matrix": matrix,
        }
    return result


def _no_patient_identifiers(value: Any) -> bool:
    if isinstance(value, Mapping):
        return all(
            _no_patient_identifiers(key) and _no_patient_identifiers(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return all(_no_patient_identifiers(item) for item in value)
    return not (isinstance(value, str) and PATIENT_ID_PATTERN.fullmatch(value))


def validate_single_trial_report(document: Mapping[str, Any]) -> None:
    validate_document(dict(document), REPORT_SCHEMA)
    if _self_hash(document, "report_sha256") != document["report_sha256"]:
        raise ApixabanSingleTrialEvaluationError("Single-trial report hash mismatch")
    if not _no_patient_identifiers(document):
        raise ApixabanSingleTrialEvaluationError(
            "Aggregate report contains a patient identifier"
        )
    patient_count = document["population"]["patient_count"]
    if document["population"]["assessment_count"] != patient_count * 23:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial assessment denominator changed"
        )
    expected_labels = {
        "axis_a_intended_gold_vs_mentor_reference": (
            set(REFERENCE_OUTCOMES), set(OUTCOMES)
        ),
        "axis_b_intended_model_vs_intended_gold": (
            set(OUTCOMES), set(OUTCOMES)
        ),
        "axis_c_intended_model_vs_mentor_reference": (
            set(REFERENCE_OUTCOMES), set(OUTCOMES)
        ),
    }
    for name, axis in document["axes"].items():
        if axis["patient_count"] != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} patient denominator changed"
            )
        if sum(axis["reference_outcome_counts"].values()) != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} reference counts do not reconcile"
            )
        if sum(axis["candidate_outcome_counts"].values()) != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} candidate counts do not reconcile"
            )
        if axis["candidate_known_count"] + axis[
            "candidate_unknown_count"
        ] != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} coverage counts do not reconcile"
            )
        if axis["candidate_unknown_count"] != axis[
            "candidate_outcome_counts"
        ]["unknown"]:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} unknown count changed"
            )
        if axis["candidate_coverage"] != (
            axis["candidate_known_count"] / patient_count
        ):
            raise ApixabanSingleTrialEvaluationError(
                f"{name} coverage rate does not reconcile"
            )
        if axis["complete_denominator_exact_agreement"] != (
            axis["exact_agreement_count"] / patient_count
        ):
            raise ApixabanSingleTrialEvaluationError(
                f"{name} exact agreement does not reconcile"
            )
        if axis["complete_denominator_exact_agreement_ci"]["estimate"] != axis[
            "complete_denominator_exact_agreement"
        ]:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} bootstrap estimate changed"
            )
        reference_labels, candidate_labels = expected_labels[name]
        if set(axis["reference_outcome_counts"]) != reference_labels or set(
            axis["candidate_outcome_counts"]
        ) != candidate_labels:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} outcome labels changed"
            )
        if set(axis["confusion_matrix"]) != reference_labels or any(
            set(row) != candidate_labels
            for row in axis["confusion_matrix"].values()
        ):
            raise ApixabanSingleTrialEvaluationError(
                f"{name} confusion labels changed"
            )
        if sum(sum(row.values()) for row in axis["confusion_matrix"].values()) != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} confusion matrix does not reconcile"
            )
        expected_conditional = name != "axis_b_intended_model_vs_intended_gold"
        if (axis["conditional_three_class"] is not None) is not expected_conditional:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} conditional metric policy changed"
            )
        conditional = axis["conditional_three_class"]
        if conditional is not None:
            if conditional["denominator"] != axis["candidate_known_count"]:
                raise ApixabanSingleTrialEvaluationError(
                    f"{name} conditional denominator changed"
                )
            expected_rate = (
                conditional["exact_agreement_count"] / conditional["denominator"]
                if conditional["denominator"]
                else None
            )
            if conditional["exact_agreement"] != expected_rate:
                raise ApixabanSingleTrialEvaluationError(
                    f"{name} conditional agreement does not reconcile"
                )
    for source in ("released_gold", "model_predictions"):
        diagnostics = document["adapter_diagnostics"][source]
        if diagnostics["source_name"] != source:
            raise ApixabanSingleTrialEvaluationError(
                "Unit-adapter source label changed"
            )
        if diagnostics["unit_adapter_contract_sha256"] != document[
            "provenance"
        ]["unit_adapter_contract_sha256"]:
            raise ApixabanSingleTrialEvaluationError(
                "Unit-adapter report provenance changed"
            )
        if diagnostics["row_count"] != document["population"][
            "assessment_count"
        ]:
            raise ApixabanSingleTrialEvaluationError(
                "Unit-adapter row denominator changed"
            )
        if len(diagnostics["per_question"]) != 8:
            raise ApixabanSingleTrialEvaluationError(
                "Unit-adapter report must contain eight numeric questions"
            )
        if sum(item["total_count"] for item in diagnostics["per_question"]) != (
            diagnostics["numeric_row_count"]
        ):
            raise ApixabanSingleTrialEvaluationError(
                "Unit-adapter numeric denominator changed"
            )
        for item in diagnostics["per_question"]:
            if item["total_count"] != (
                item["known_input_count"] + item["source_unknown_count"]
            ):
                raise ApixabanSingleTrialEvaluationError(
                    "Unit-adapter source counts do not reconcile"
                )
            resolved_known = (
                item["accepted_count"]
                + item["out_of_range_count"]
                + item["integer_violation_count"]
                + item["unexpected_source_unit_count"]
            )
            if item["known_input_count"] != resolved_known:
                raise ApixabanSingleTrialEvaluationError(
                    "Unit-adapter outcome counts do not reconcile"
                )
            expected_known_rate = (
                item["out_of_range_count"] / item["known_input_count"]
                if item["known_input_count"]
                else None
            )
            if item["out_of_range_fraction_of_known_inputs"] != expected_known_rate:
                raise ApixabanSingleTrialEvaluationError(
                    "Unit-adapter known-input rate does not reconcile"
                )
            if item["out_of_range_fraction_of_all_rows"] != (
                item["out_of_range_count"] / item["total_count"]
            ):
                raise ApixabanSingleTrialEvaluationError(
                    "Unit-adapter all-row rate does not reconcile"
                )

    axis_b = document["axes"]["axis_b_intended_model_vs_intended_gold"]
    if set(axis_b["per_rule"]) != set(RULE_IDS):
        raise ApixabanSingleTrialEvaluationError("Per-rule metric set changed")
    for rule_id, metrics in axis_b["per_rule"].items():
        if metrics["patient_count"] != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{rule_id} patient denominator changed"
            )
        if set(metrics["confusion_matrix"]) != set(DECISIONS) or any(
            set(row) != set(DECISIONS)
            for row in metrics["confusion_matrix"].values()
        ):
            raise ApixabanSingleTrialEvaluationError(
                f"{rule_id} decision labels changed"
            )
        if sum(
            sum(row.values()) for row in metrics["confusion_matrix"].values()
        ) != patient_count:
            raise ApixabanSingleTrialEvaluationError(
                f"{rule_id} confusion counts do not reconcile"
            )
        if metrics["exact_agreement"] != (
            metrics["exact_agreement_count"] / patient_count
        ):
            raise ApixabanSingleTrialEvaluationError(
                f"{rule_id} exact agreement does not reconcile"
            )


def validate_single_trial_trace(document: Mapping[str, Any]) -> None:
    required = {
        "trace_version",
        "trace_sha256",
        "report_version",
        "patient_count",
        "rows",
        "restricted_local_only",
        "clinical_evidence_claim_allowed",
    }
    if set(document) != required or document["trace_version"] != TRACE_VERSION:
        raise ApixabanSingleTrialEvaluationError("Single-trial trace fields changed")
    if _self_hash(document, "trace_sha256") != document["trace_sha256"]:
        raise ApixabanSingleTrialEvaluationError("Single-trial trace hash mismatch")
    if document["restricted_local_only"] is not True or document[
        "clinical_evidence_claim_allowed"
    ] is not False:
        raise ApixabanSingleTrialEvaluationError("Trace disclosure boundary changed")
    if len(document["rows"]) != document["patient_count"]:
        raise ApixabanSingleTrialEvaluationError("Trace row count changed")
    patient_ids = [row["patient_id"] for row in document["rows"]]
    if (
        patient_ids != sorted(patient_ids)
        or len(patient_ids) != len(set(patient_ids))
        or any(not PATIENT_ID_PATTERN.fullmatch(item) for item in patient_ids)
    ):
        raise ApixabanSingleTrialEvaluationError("Trace patient grid is invalid")
    for row in document["rows"]:
        if set(row) != {
            "patient_id",
            "intended_gold",
            "intended_model",
            "mentor_reference_class",
        }:
            raise ApixabanSingleTrialEvaluationError("Trace row fields changed")
        if row["mentor_reference_class"] not in REFERENCE_OUTCOMES:
            raise ApixabanSingleTrialEvaluationError(
                "Trace mentor reference class changed"
            )
        for name in ("intended_gold", "intended_model"):
            projection = row[name]
            if set(projection) != {
                "class",
                "rule_decisions",
                "adapter_reasons",
            }:
                raise ApixabanSingleTrialEvaluationError(
                    "Trace projection fields changed"
                )
            if projection["class"] not in OUTCOMES or set(
                projection["rule_decisions"]
            ) != set(RULE_IDS):
                raise ApixabanSingleTrialEvaluationError(
                    "Trace projection outcomes changed"
                )
            if any(
                decision not in DECISIONS
                for decision in projection["rule_decisions"].values()
            ):
                raise ApixabanSingleTrialEvaluationError(
                    "Trace rule decision changed"
                )


def build_single_trial_evaluation(
    benchmark: Mapping[str, Any],
    split: Mapping[str, Any],
    predictions: Mapping[str, Any],
    mentor_reference: Mapping[str, str],
    *,
    benchmark_sha256: str,
    prediction_set_sha256: str,
    mentor_results_sha256: str,
    candidate_csv_sha256: str,
    id_map_sha256: str,
    run_contract: Mapping[str, Any],
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    validate_single_trial_run_contract(run_contract)
    validate_apixaban_benchmark(dict(benchmark))
    validate_prediction_set(dict(predictions))
    if split["status"] != "frozen" or split["freeze"]["test_locked"] is not True:
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial evaluation requires a frozen, test-locked split"
        )
    patient_ids = sorted(split["splits"]["validation"]["patient_ids"])
    if not patient_ids:
        raise ApixabanSingleTrialEvaluationError("Validation split is empty")
    if predictions["split_name"] != "validation":
        raise ApixabanSingleTrialEvaluationError(
            "Single-trial evaluation is validation-only"
        )
    selected = run_contract["selected_artifact"]
    if prediction_set_sha256 != selected["prediction_set_sha256"]:
        raise ApixabanSingleTrialEvaluationError(
            "Prediction artifact is not the owner-selected long-context run"
        )
    if (
        predictions["prediction_set_version"]
        != selected["prediction_set_version"]
        or predictions["model_id"] != selected["model_id"]
        or predictions["prompt_version"] != selected["prompt_version"]
        or predictions["inference_config_sha256"]
        != selected["inference_config_sha256"]
    ):
        raise ApixabanSingleTrialEvaluationError(
            "Prediction metadata differs from the selected run contract"
        )
    if predictions["benchmark_sha256"] != benchmark_sha256 or predictions[
        "split_manifest_sha256"
    ] != split["manifest_sha256"]:
        raise ApixabanSingleTrialEvaluationError(
            "Prediction provenance differs from evaluation inputs"
        )
    catalog = load_question_catalog()
    expected_keys = {
        (patient_id, question["question_id"])
        for patient_id in patient_ids
        for question in catalog["questions"]
    }
    gold_rows = [
        row for row in benchmark["assessments"] if row["patient_id"] in patient_ids
    ]
    model_rows = list(predictions["predictions"])
    for name, rows in (("gold", gold_rows), ("model", model_rows)):
        keys = {(row["patient_id"], row["question_id"]) for row in rows}
        if len(rows) != len(keys) or keys != expected_keys:
            raise ApixabanSingleTrialEvaluationError(
                f"{name} facts do not cover the complete validation grid"
            )
    if set(mentor_reference) != set(benchmark["patient_ids"]):
        raise ApixabanSingleTrialEvaluationError(
            "Mentor reference does not align to benchmark cohort"
        )

    adapted_gold, gold_diagnostics = adapt_fact_rows(
        gold_rows, source_name="released_gold"
    )
    adapted_model, model_diagnostics = adapt_fact_rows(
        model_rows, source_name="model_predictions"
    )
    gold_projection = _project_patients(
        adapted_gold, patient_ids, source_name="released-gold"
    )
    model_projection = _project_patients(
        adapted_model, patient_ids, source_name="model-predictions"
    )

    axis_a_rows = [
        {
            "patient_id": patient_id,
            "reference": mentor_reference[patient_id],
            "candidate": gold_projection[patient_id]["class"],
        }
        for patient_id in patient_ids
    ]
    axis_b_rows = [
        {
            "patient_id": patient_id,
            "reference": gold_projection[patient_id]["class"],
            "candidate": model_projection[patient_id]["class"],
        }
        for patient_id in patient_ids
    ]
    axis_c_rows = [
        {
            "patient_id": patient_id,
            "reference": mentor_reference[patient_id],
            "candidate": model_projection[patient_id]["class"],
        }
        for patient_id in patient_ids
    ]
    trace: Dict[str, Any] = {
        "trace_version": TRACE_VERSION,
        "trace_sha256": "pending",
        "report_version": REPORT_VERSION,
        "patient_count": len(patient_ids),
        "rows": [
            {
                "patient_id": patient_id,
                "intended_gold": gold_projection[patient_id],
                "intended_model": model_projection[patient_id],
                "mentor_reference_class": mentor_reference[patient_id],
            }
            for patient_id in patient_ids
        ],
        "restricted_local_only": True,
        "clinical_evidence_claim_allowed": False,
    }
    trace["trace_sha256"] = _self_hash(trace, "trace_sha256")
    validate_single_trial_trace(trace)

    intended = load_intended_rule_contract()
    unit_contract = load_unit_adapter_contract()
    report: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "report_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "provenance": {
            "benchmark_sha256": benchmark_sha256,
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": "validation",
            "prediction_set_sha256": prediction_set_sha256,
            "prediction_set_version": predictions["prediction_set_version"],
            "model_id": predictions["model_id"],
            "prompt_version": predictions["prompt_version"],
            "mentor_results_sha256": mentor_results_sha256,
            "mentor_candidate_csv_sha256": candidate_csv_sha256,
            "id_map_sha256": id_map_sha256,
            "intended_rule_contract_sha256": intended["contract_sha256"],
            "unit_adapter_contract_sha256": unit_contract["contract_sha256"],
            "run_contract_sha256": run_contract["contract_sha256"],
            "evaluation_protocol_version": (
                "apixaban-single-trial-evaluation-1.0.0"
            ),
            "trace_sha256": trace["trace_sha256"],
            "locked_test_labels_used": False,
        },
        "model_selection": {
            "selected_configuration": selected["configuration"],
            "selection_basis": run_contract["decision"]["selection_basis"],
            "selection_rationale": run_contract["decision"][
                "selection_rationale"
            ],
            "single_trial_three_class_results_seen_before_selection": False,
            "unselected_configuration": run_contract["unselected_artifact"][
                "configuration"
            ],
            "unselected_artifact_evaluated": False,
        },
        "population": {
            "patient_count": len(patient_ids),
            "question_count": len(catalog["questions"]),
            "assessment_count": len(expected_keys),
            "complete_validation_grid": True,
        },
        "runtime_semantics": {
            "technical_fact_carrier_is_clinical_evidence": False,
            "technical_index_date": "1970-01-01",
            "technical_index_date_used_for_time_filtering": False,
            "reason": (
                "The frozen rules contain no evaluator time windows and the "
                "source questions already encode their own temporal intent."
            ),
        },
        "adapter_diagnostics": {
            "released_gold": gold_diagnostics,
            "model_predictions": model_diagnostics,
        },
        "axes": {
            "axis_a_intended_gold_vs_mentor_reference": _axis_metrics(
                axis_a_rows,
                reference_labels=REFERENCE_OUTCOMES,
                candidate_labels=OUTCOMES,
                conditional_known=True,
            ),
            "axis_b_intended_model_vs_intended_gold": {
                **_axis_metrics(
                    axis_b_rows,
                    reference_labels=OUTCOMES,
                    candidate_labels=OUTCOMES,
                    conditional_known=False,
                ),
                "per_rule": _per_rule_metrics(
                    gold_projection, model_projection, patient_ids
                ),
            },
            "axis_c_intended_model_vs_mentor_reference": _axis_metrics(
                axis_c_rows,
                reference_labels=REFERENCE_OUTCOMES,
                candidate_labels=OUTCOMES,
                conditional_known=True,
            ),
        },
        "interpretation": {
            "axis_a_name": "observed_reference_discrepancy",
            "axis_a_is_pure_semantic_distance": False,
            "axis_b_name": "observed_fact_error_propagation",
            "axis_b_is_causal_reasoning_attribution": False,
            "axis_c_name": "combined_mentor_designated_project_reference_result",
            "mentor_reference_is_independent_clinical_gold": False,
            "clinical_eligibility_accuracy_claim_allowed": False,
            "unit_compatibility_claim_allowed": False,
            "required_report_wording": (
                "The intended contract was specified by the owner through a "
                "source-precedence rule and was not confirmed through "
                "item-by-item qualified clinical review."
            ),
        },
        "disclosure": {
            "owner_only": True,
            "patient_identifiers_in_report": False,
            "row_level_trace_separate_and_owner_only": True,
            "public_disclosure_requires_separate_review": True,
        },
    }
    report["report_sha256"] = _self_hash(report, "report_sha256")
    validate_single_trial_report(report)
    return report, trace


def evaluate_single_trial_from_paths(
    benchmark_path: Path,
    split_path: Path,
    prediction_path: Path,
    mentor_results_path: Path,
    candidate_csv_path: Path,
    id_map_path: Path,
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    paths = (
        benchmark_path,
        split_path,
        prediction_path,
        mentor_results_path,
        candidate_csv_path,
        id_map_path,
    )
    for path in paths:
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanSingleTrialEvaluationError(
                f"Restricted evaluation input is not owner-only: {path}"
            )
    run_contract = load_single_trial_run_contract()
    prediction_set_sha256 = file_sha256(prediction_path)
    if prediction_set_sha256 != run_contract["selected_artifact"][
        "prediction_set_sha256"
    ]:
        raise ApixabanSingleTrialEvaluationError(
            "Prediction artifact is not the owner-selected long-context run"
        )
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    validate_apixaban_benchmark(benchmark)
    split = load_apixaban_split_manifest(split_path)
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    validate_prediction_set(predictions)
    id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
    validate_apixaban_id_map(id_map)
    id_map_sha256 = file_sha256(id_map_path)
    if id_map_sha256 != split["dataset"]["id_map_sha256"]:
        raise ApixabanSingleTrialEvaluationError(
            "ID map does not match frozen split provenance"
        )
    benchmark_sha256 = file_sha256(benchmark_path)
    if benchmark_sha256 != split["dataset"]["benchmark_sha256"]:
        raise ApixabanSingleTrialEvaluationError(
            "Benchmark does not match frozen split provenance"
        )
    mentor_reference = load_mentor_reference(
        mentor_results_path, candidate_csv_path, id_map_path
    )
    return build_single_trial_evaluation(
        benchmark,
        split,
        predictions,
        mentor_reference,
        benchmark_sha256=benchmark_sha256,
        prediction_set_sha256=prediction_set_sha256,
        mentor_results_sha256=file_sha256(mentor_results_path),
        candidate_csv_sha256=file_sha256(candidate_csv_path),
        id_map_sha256=id_map_sha256,
        run_contract=run_contract,
        generated_at=generated_at,
        code_commit=code_commit,
    )


def write_single_trial_evaluation(
    report: Mapping[str, Any],
    trace: Mapping[str, Any],
    output_dir: Path,
) -> Tuple[Path, Path, Path]:
    validate_single_trial_report(report)
    validate_single_trial_trace(trace)
    if report["provenance"]["trace_sha256"] != trace["trace_sha256"]:
        raise ApixabanSingleTrialEvaluationError(
            "Report does not bind the supplied row-level trace"
        )
    assert_restricted_local_path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "single-trial-three-axis.report.json"
    trace_path = output_dir / "single-trial-three-axis.trace.json"
    summary_path = output_dir / "single-trial-three-axis.summary.md"
    if report_path.exists() or trace_path.exists() or summary_path.exists():
        raise FileExistsError("Refusing to overwrite single-trial evaluation")
    try:
        write_private_json(dict(trace), trace_path)
        write_private_json(dict(report), report_path)
        _write_private_text(_render_markdown(report), summary_path)
    except BaseException:
        for path in (trace_path, report_path, summary_path):
            path.unlink(missing_ok=True)
        raise
    return report_path, trace_path, summary_path


def _percentage(value: Optional[float]) -> str:
    return "NA" if value is None else f"{100 * value:.1f}%"


def _confusion_matrix_markdown(
    label: str,
    key: str,
    axis: Mapping[str, Any],
) -> Sequence[str]:
    reference_labels = (
        OUTCOMES
        if key == "axis_b_intended_model_vs_intended_gold"
        else REFERENCE_OUTCOMES
    )
    matrix = axis["confusion_matrix"]
    header = "| Reference \\ Candidate | " + " | ".join(OUTCOMES) + " |"
    separator = "|---|" + "---:|" * len(OUTCOMES)
    rows = [
        "| "
        + reference
        + " | "
        + " | ".join(str(matrix[reference][candidate]) for candidate in OUTCOMES)
        + " |"
        for reference in reference_labels
    ]
    return (
        f"### {label}",
        "",
        f"Candidate known: {axis['candidate_known_count']}; candidate UNKNOWN: "
        f"{axis['candidate_unknown_count']}.",
        "",
        header,
        separator,
        *rows,
        "",
    )


def _render_markdown(report: Mapping[str, Any]) -> str:
    axes = report["axes"]
    axis_rows = []
    axis_definitions = (
        (
            "A: intended(gold) vs mentor reference",
            "axis_a_intended_gold_vs_mentor_reference",
        ),
        (
            "B: intended(model) vs intended(gold)",
            "axis_b_intended_model_vs_intended_gold",
        ),
        (
            "C: intended(model) vs mentor reference",
            "axis_c_intended_model_vs_mentor_reference",
        ),
    )
    for label, key in axis_definitions:
        axis = axes[key]
        conditional = axis["conditional_three_class"]
        axis_rows.append(
            "| "
            + " | ".join(
                (
                    label,
                    str(axis["patient_count"]),
                    f"{axis['exact_agreement_count']}/{axis['patient_count']}",
                    _percentage(axis["complete_denominator_exact_agreement"]),
                    _percentage(axis["candidate_coverage"]),
                    str(axis["candidate_known_count"]),
                    str(axis["candidate_unknown_count"]),
                    _percentage(
                        conditional["exact_agreement"] if conditional else None
                    ),
                )
            )
            + " |"
        )
    confusion_lines = []
    for label, key in axis_definitions:
        confusion_lines.extend(
            _confusion_matrix_markdown(label, key, axes[key])
        )
    per_rule_rows = []
    for rule_id, metrics in axes[
        "axis_b_intended_model_vs_intended_gold"
    ]["per_rule"].items():
        per_rule_rows.append(
            "| "
            + " | ".join(
                (
                    rule_id,
                    f"{metrics['exact_agreement_count']}/{metrics['patient_count']}",
                    _percentage(metrics["exact_agreement"]),
                )
            )
            + " |"
        )
    unit_rows = []
    for source in ("released_gold", "model_predictions"):
        for item in report["adapter_diagnostics"][source]["per_question"]:
            unit_rows.append(
                "| "
                + " | ".join(
                    (
                        source,
                        item["source_criterion_label"],
                        str(item["out_of_range_count"]),
                        _percentage(
                            item["out_of_range_fraction_of_known_inputs"]
                        ),
                        _percentage(item["out_of_range_fraction_of_all_rows"]),
                        str(item["integer_violation_count"]),
                        str(item["unexpected_source_unit_count"]),
                    )
                )
                + " |"
            )
    wording = report["interpretation"]["required_report_wording"]
    lines = [
        "# Owner-only Apixaban single-trial validation summary",
        "",
        f"Report hash: `{report['report_sha256']}`",
        "",
        f"Summary renderer: `{SUMMARY_RENDERER_VERSION}`",
        "",
        f"Model: `{report['provenance']['model_id']}`",
        "",
        "Selected configuration: "
        f"`{report['model_selection']['selected_configuration']}`",
        "",
        "Selection was frozen from pre-existing P2.3 fact-level validation "
        "results before any single-trial three-class result was viewed. The "
        "structured artifact was not evaluated in this run.",
        "",
        f"> {wording}",
        "",
        "This report measures agreement with released fact labels and a legacy "
        "rule-derived reference. It does not measure independent clinical "
        "eligibility accuracy.",
        "",
        "## Three mandatory axes",
        "",
        "| Axis | Patients | Exact | Complete-denominator agreement | "
        "Coverage | Candidate known | Candidate UNKNOWN | Conditional "
        "known-only agreement |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *axis_rows,
        "",
        "Axis A is an observed reference discrepancy, not pure semantic "
        "distance. Axis B is observed fact-error propagation, not causal "
        "reasoning attribution. Axis C mixes both effects.",
        "",
        "## Confusion matrices",
        "",
        "Rows are reference outcomes and columns are candidate outcomes. "
        "UNKNOWN remains an explicit outcome and is never removed from the "
        "complete denominator.",
        "",
        *confusion_lines,
        "## Criterion-level agreement",
        "",
        "Axis B final-class agreement can hide rule-level errors, so all five "
        "frozen rule agreements are shown separately.",
        "",
        "| Rule | Exact | Agreement |",
        "|---|---:|---:|",
        *per_rule_rows,
        "",
        "## Unit-adapter diagnostics",
        "",
        "| Source | Question | Out of range | % known inputs | % all rows | "
        "Non-integer | Unexpected source unit |",
        "|---|---|---:|---:|---:|---:|---:|",
        *unit_rows,
        "",
        "The unit mappings are DOCX-based project assumptions, not unit "
        "metadata stored in the released labels. Extreme-value checks are not "
        "clinical reference intervals or proof of unit compatibility. No unit "
        "conversion or alternative-unit guessing was used.",
        "",
        "Owner-only restricted aggregate. Public disclosure requires a "
        "separate governance review.",
        "",
    ]
    return "\n".join(lines)


def _write_private_text(content: str, path: Path) -> None:
    assert_restricted_local_path(path)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite restricted output: {path}"
        ) from None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
