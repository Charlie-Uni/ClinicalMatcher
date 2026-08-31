"""Owner-approved unit mapping for the Apixaban single-trial diagnostic."""

import json
import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_contract import load_question_catalog, question_index
from .apixaban_single_trial import load_intended_rule_contract
from .splits import canonical_sha256


CONTRACT_RESOURCE = (
    "resources/apixaban-unit-adapter-contract-1.0.0.json"
)
CONTRACT_VERSION = "1.0.0"
OUT_OF_RANGE_REASON = "extreme_value_suggestive_of_source_or_unit_error"
NON_INTEGER_REASON = "non_integer_score"
UNEXPECTED_UNIT_REASON = "unexpected_source_unit_metadata"
SOURCE_NAMES = ("released_gold", "model_predictions")

EXPECTED_SPECS = {
    "apixaban-q-e6783d58af7c09d2": (
        "chads2", "chads2_score", None, Decimal("0"), Decimal("6"), True
    ),
    "apixaban-q-dc8ad785ff98b239": (
        "lvef",
        "left_ventricular_ejection_fraction",
        "%",
        Decimal("0"),
        Decimal("100"),
        False,
    ),
    "apixaban-q-0254f569c4046777": (
        "PLT",
        "platelet_count",
        "10^3/uL",
        Decimal("0"),
        Decimal("5000"),
        False,
    ),
    "apixaban-q-a69f4c14589e7f29": (
        "HGB", "hemoglobin", "g/dL", Decimal("0.6"), Decimal("30"), False
    ),
    "apixaban-q-b920477ded648b17": (
        "CREAT",
        "serum_creatinine",
        "mg/dL",
        Decimal("0"),
        Decimal("80"),
        False,
    ),
    "apixaban-q-758bd991545de193": (
        "AST",
        "aspartate_aminotransferase",
        "U/L",
        Decimal("0"),
        Decimal("50000"),
        False,
    ),
    "apixaban-q-e2c5d2226ca3a9f1": (
        "BILI",
        "total_bilirubin",
        "mg/dL",
        Decimal("0"),
        Decimal("200"),
        False,
    ),
    "apixaban-q-4547e40560979dc8": (
        "blood_glucose",
        "blood_glucose",
        "mg/dL",
        Decimal("10"),
        Decimal("2700"),
        False,
    ),
}


class ApixabanUnitAdapterError(ValueError):
    """Raised when the frozen unit adapter or its input is inconsistent."""


def _self_hash(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("contract_sha256", None)
    return canonical_sha256(unsigned)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApixabanUnitAdapterError("Known numeric fact must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        raise ApixabanUnitAdapterError("Known numeric fact must be finite")
    try:
        resolved = Decimal(str(value))
    except InvalidOperation as error:
        raise ApixabanUnitAdapterError("Known numeric fact is invalid") from error
    if not resolved.is_finite():
        raise ApixabanUnitAdapterError("Known numeric fact must be finite")
    return resolved


def validate_unit_adapter_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_status",
        "contract_sha256",
        "contract_hash_scope",
        "question_catalog_sha256",
        "intended_rule_contract_sha256",
        "mentor_docx_sha256",
        "source_numeric_labels_store_units",
        "unit_assignment_role",
        "clinical_unit_compatibility_claim_allowed",
        "automatic_unit_conversion_allowed",
        "alternative_unit_guessing_allowed",
        "validation_authorization",
        "threshold_scale_coherence_check",
        "range_policy",
        "entries",
        "limitations",
        "owner_review",
    }
    if set(document) != required:
        raise ApixabanUnitAdapterError("Unit-adapter contract fields are incomplete")
    if document["contract_version"] != CONTRACT_VERSION:
        raise ApixabanUnitAdapterError("Unsupported unit-adapter contract version")
    if document["contract_status"] != "owner_approved_frozen_pre_validation":
        raise ApixabanUnitAdapterError("Unit-adapter contract is not owner approved")
    if document["contract_hash_scope"] != (
        "canonical_json_excluding_contract_sha256"
    ):
        raise ApixabanUnitAdapterError("Unit-adapter hash scope changed")
    if _self_hash(document) != document["contract_sha256"]:
        raise ApixabanUnitAdapterError("Unit-adapter contract hash mismatch")

    catalog = load_question_catalog()
    intended = load_intended_rule_contract()
    if document["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanUnitAdapterError("Unit-adapter question catalog mismatch")
    if document["intended_rule_contract_sha256"] != intended["contract_sha256"]:
        raise ApixabanUnitAdapterError("Unit-adapter intended contract mismatch")
    if document["mentor_docx_sha256"] != (
        "be872d1f2e6baa4883b3dbcdc53895a8ff3d0362e27a13193ecee1181e9a14ec"
    ):
        raise ApixabanUnitAdapterError("Unit-adapter DOCX provenance changed")

    if document["source_numeric_labels_store_units"] is not False:
        raise ApixabanUnitAdapterError("Released numeric labels do not store units")
    if document["unit_assignment_role"] != (
        "docx_based_project_mapping_assumption_not_observed_unit_metadata"
    ):
        raise ApixabanUnitAdapterError("Unit-assignment role is overstated")
    for field in (
        "clinical_unit_compatibility_claim_allowed",
        "automatic_unit_conversion_allowed",
        "alternative_unit_guessing_allowed",
    ):
        if document[field] is not False:
            raise ApixabanUnitAdapterError(f"Forbidden unit behavior enabled: {field}")

    authorization = document["validation_authorization"]
    if authorization != {
        "authorized": True,
        "authorized_by": "project_owner",
        "authorized_at": "2026-08-31",
        "authorized_intended_rule_contract_sha256": intended["contract_sha256"],
        "authorization_scope": (
            "single_predeclared_validation_evaluation_after_implementation_"
            "and_synthetic_tests_pass"
        ),
        "locked_test_authorized": False,
    }:
        raise ApixabanUnitAdapterError("Validation authorization changed")

    range_policy = document["range_policy"]
    if range_policy["purpose"] != (
        "fail_closed_extreme_value_diagnostic_before_assumed_unit_assignment"
    ):
        raise ApixabanUnitAdapterError("Range-policy purpose changed")
    if range_policy["not_a_reference_interval"] is not True or range_policy[
        "not_a_diagnostic_or_treatment_threshold"
    ] is not True:
        raise ApixabanUnitAdapterError("Range bounds are clinically overstated")
    if range_policy["boundary_behavior"] != (
        "inclusive_bounds_pass_values_outside_abstain"
    ):
        raise ApixabanUnitAdapterError("Range boundary behavior changed")
    if range_policy["out_of_range_result"] != "unknown" or range_policy[
        "out_of_range_reason"
    ] != OUT_OF_RANGE_REASON:
        raise ApixabanUnitAdapterError("Out-of-range behavior changed")
    required_report_fields = range_policy["aggregate_reporting"][
        "required_per_question_fields"
    ]
    if required_report_fields != [
        "total_count",
        "known_input_count",
        "source_unknown_count",
        "accepted_count",
        "out_of_range_count",
        "out_of_range_fraction_of_known_inputs",
        "out_of_range_fraction_of_all_rows",
    ]:
        raise ApixabanUnitAdapterError("Unit-adapter reporting fields changed")
    if range_policy["aggregate_reporting"][
        "integer_violation_reported_separately"
    ] is not True:
        raise ApixabanUnitAdapterError("Integer violations must remain separate")

    questions = question_index(catalog)
    entries = document["entries"]
    if len(entries) != len(EXPECTED_SPECS):
        raise ApixabanUnitAdapterError("Unit adapter must contain eight entries")
    seen = set()
    for entry in entries:
        question_id = entry["question_id"]
        if question_id in seen or question_id not in EXPECTED_SPECS:
            raise ApixabanUnitAdapterError("Unit-adapter question set changed")
        seen.add(question_id)
        label, field, unit, lower, upper, integer_required = EXPECTED_SPECS[
            question_id
        ]
        question = questions[question_id]
        if question["question_type"] != "numeric":
            raise ApixabanUnitAdapterError("Unit adapter may cover numeric facts only")
        if (
            entry["source_criterion_label"] != label
            or entry["fact_field"] != field
            or question["source_criterion_label"] != label
            or question["fact_field"] != field
            or entry["assumed_unit"] != unit
            or Decimal(str(entry["minimum_inclusive"])) != lower
            or Decimal(str(entry["maximum_inclusive"])) != upper
            or entry["integer_required"] is not integer_required
        ):
            raise ApixabanUnitAdapterError(
                f"Unit-adapter entry changed: {question_id}"
            )
        expected_integer_semantics = (
            "mathematical_integer_storage_type_independent_3.0_passes_3.2_abstains"
            if integer_required
            else None
        )
        if entry["integer_semantics"] != expected_integer_semantics:
            raise ApixabanUnitAdapterError("Integer semantics changed")
    if seen != set(EXPECTED_SPECS):
        raise ApixabanUnitAdapterError("Unit-adapter question set is incomplete")

    owner_review = document["owner_review"]
    if owner_review["approved"] is not True or owner_review[
        "reviewer_role"
    ] != "project_owner":
        raise ApixabanUnitAdapterError("Unit-adapter owner review is incomplete")
    if "chads2_integer_is_mathematical_not_storage_type" not in owner_review[
        "approved_decisions"
    ]:
        raise ApixabanUnitAdapterError("Mathematical integer rule is not approved")
    limitations = document["limitations"]
    if not any("6.2 mmol/L" in item for item in limitations):
        raise ApixabanUnitAdapterError("Undetectable unit mismatch is undisclosed")


def load_unit_adapter_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_unit_adapter_contract(document)
    return document


def _adapt_numeric_row(
    row: Mapping[str, Any], entry: Mapping[str, Any]
) -> Dict[str, Any]:
    status = row["fact_status"]
    if status == "unknown":
        if row["value"] is not None:
            raise ApixabanUnitAdapterError("Unknown numeric fact must have null value")
        return {
            "fact_status": "unknown",
            "value": None,
            "unit": None,
            "adapter_reason": "source_unknown",
        }
    if status != "present":
        raise ApixabanUnitAdapterError("Numeric fact status must be present or unknown")
    value = _decimal(row["value"])
    if row.get("unit") is not None:
        return {
            "fact_status": "unknown",
            "value": None,
            "unit": None,
            "adapter_reason": UNEXPECTED_UNIT_REASON,
        }
    if entry["integer_required"] and value != value.to_integral_value():
        return {
            "fact_status": "unknown",
            "value": None,
            "unit": None,
            "adapter_reason": NON_INTEGER_REASON,
        }
    lower = Decimal(str(entry["minimum_inclusive"]))
    upper = Decimal(str(entry["maximum_inclusive"]))
    if value < lower or value > upper:
        return {
            "fact_status": "unknown",
            "value": None,
            "unit": None,
            "adapter_reason": OUT_OF_RANGE_REASON,
        }
    return {
        "fact_status": "present",
        "value": row["value"],
        "unit": entry["assumed_unit"],
        "adapter_reason": None,
    }


def adapt_fact_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_name: str,
    contract: Optional[Mapping[str, Any]] = None,
) -> Tuple[Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    """Assign approved units and return adapted rows plus PHI-free diagnostics."""

    resolved = dict(contract or load_unit_adapter_contract())
    validate_unit_adapter_contract(resolved)
    if source_name not in SOURCE_NAMES:
        raise ApixabanUnitAdapterError("Unit-adapter source name is not approved")
    catalog = load_question_catalog()
    questions = question_index(catalog)
    entries = {entry["question_id"]: entry for entry in resolved["entries"]}
    adapted = []
    seen = set()
    counters: Dict[str, Counter[str]] = {
        question_id: Counter() for question_id in entries
    }
    for row in rows:
        required = {
            "patient_id",
            "question_id",
            "question_type",
            "fact_status",
            "value",
            "unit",
        }
        if not required.issubset(row):
            raise ApixabanUnitAdapterError("Fact row is missing adapter fields")
        question_id = row["question_id"]
        question = questions.get(question_id)
        if question is None or row["question_type"] != question["question_type"]:
            raise ApixabanUnitAdapterError(
                "Fact row does not match question catalog"
            )
        key = (row["patient_id"], question_id)
        if key in seen:
            raise ApixabanUnitAdapterError("Duplicate patient-question fact row")
        seen.add(key)
        if question["question_type"] == "numeric":
            result = _adapt_numeric_row(row, entries[question_id])
            counts = counters[question_id]
            counts["total"] += 1
            if row["fact_status"] == "unknown":
                counts["source_unknown"] += 1
            else:
                counts["known_input"] += 1
            reason = result["adapter_reason"]
            if reason is None:
                counts["accepted"] += 1
            elif reason == OUT_OF_RANGE_REASON:
                counts["out_of_range"] += 1
            elif reason == NON_INTEGER_REASON:
                counts["integer_violation"] += 1
            elif reason == UNEXPECTED_UNIT_REASON:
                counts["unexpected_unit"] += 1
        else:
            if row["unit"] is not None:
                raise ApixabanUnitAdapterError("Boolean fact unit must be null")
            status = row["fact_status"]
            value = row["value"]
            valid = (
                (status == "present" and value is True)
                or (status == "absent" and value is False)
                or (status == "unknown" and value is None)
            )
            if not valid:
                raise ApixabanUnitAdapterError("Boolean fact encoding is invalid")
            result = {
                "fact_status": status,
                "value": value,
                "unit": None,
                "adapter_reason": "source_unknown" if status == "unknown" else None,
            }
        adapted.append(
            {
                "patient_id": row["patient_id"],
                "question_id": question_id,
                "question_type": question["question_type"],
                "fact_field": question["fact_field"],
                "source_fact_status": row["fact_status"],
                **result,
            }
        )

    per_question = []
    for entry in resolved["entries"]:
        question_id = entry["question_id"]
        counts = counters[question_id]
        total = counts["total"]
        known = counts["known_input"]
        if total == 0:
            raise ApixabanUnitAdapterError(
                "Every numeric question must occur in adapter input"
            )
        if total != known + counts["source_unknown"]:
            raise ApixabanUnitAdapterError(
                "Unit-adapter source counts do not reconcile"
            )
        if known != (
            counts["accepted"]
            + counts["out_of_range"]
            + counts["integer_violation"]
            + counts["unexpected_unit"]
        ):
            raise ApixabanUnitAdapterError(
                "Unit-adapter outcome counts do not reconcile"
            )
        per_question.append(
            {
                "question_id": question_id,
                "source_criterion_label": entry["source_criterion_label"],
                "total_count": total,
                "known_input_count": known,
                "source_unknown_count": counts["source_unknown"],
                "accepted_count": counts["accepted"],
                "out_of_range_count": counts["out_of_range"],
                "out_of_range_fraction_of_known_inputs": (
                    counts["out_of_range"] / known if known else None
                ),
                "out_of_range_fraction_of_all_rows": counts["out_of_range"] / total,
                "integer_violation_count": counts["integer_violation"],
                "unexpected_source_unit_count": counts["unexpected_unit"],
            }
        )
    report = {
        "unit_adapter_contract_sha256": resolved["contract_sha256"],
        "source_name": source_name,
        "row_count": len(rows),
        "numeric_row_count": sum(item["total_count"] for item in per_question),
        "per_question": per_question,
        "clinical_unit_compatibility_claim_allowed": False,
        "automatic_unit_conversion_used": False,
        "alternative_unit_guessing_used": False,
    }
    return tuple(adapted), report
