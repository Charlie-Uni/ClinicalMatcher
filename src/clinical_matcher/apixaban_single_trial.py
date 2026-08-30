import json
from collections import Counter
from importlib.resources import files
from typing import Any, Dict, Mapping, Optional, Tuple

from .apixaban_contract import load_question_catalog
from .models import (
    AtomProvenance,
    AtomicCondition,
    ComparisonOperator,
    ConditionExpression,
    Criterion,
    CriterionSource,
    CriterionType,
    Decision,
    DecompositionMethod,
    ExpressionType,
    FactSelection,
    SourceSpan,
    Trial,
    TypedValue,
    ValueType,
)
from .splits import canonical_sha256


CONTRACT_RESOURCE = "resources/apixaban-intended-rule-contract-1.0.0.json"
CONTRACT_VERSION = "1.0.0"
RULE_IDS = tuple(f"apixaban-rule-{index}" for index in range(1, 6))
EXPECTED_SOURCE_HASHES = {
    "criteria_json_sha256": (
        "bd4e4ae54a2b59e5202c2eded4c1dd4e25c86d74361a331c188435230e6cec6e"
    ),
    "scoring_docx_sha256": (
        "be872d1f2e6baa4883b3dbcdc53895a8ff3d0362e27a13193ecee1181e9a14ec"
    ),
    "mentor_results_sha256": (
        "f358d18feb47997d87d27b104b0c3490d08bba913e64b33b17b75ab2c65c59d3"
    ),
    "official_readme_sha256": (
        "88b77606cbcead4a263d3eb1d3e58ca16ed9098b082b498672f39ebd3ffdab30"
    ),
    "official_csv_sha256": (
        "8e8083b0b5e3d038ad912a812be1bb8a53f8a59bc37a4c29d8a420cb4296e267"
    ),
}


class ApixabanSingleTrialError(ValueError):
    """Raised when the intended five-rule contract is inconsistent."""


def _self_hash(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("contract_sha256", None)
    return canonical_sha256(unsigned)


def _atom_signature(atom: Mapping[str, Any]) -> Tuple[Any, ...]:
    expected = atom["expected"]
    return (
        atom["source_criterion_label"],
        atom["fact_field"],
        atom["operator"],
        expected["value_type"],
        expected["value"],
        expected.get("unit"),
    )


EXPECTED_ATOMS = {
    "r1-afib-present": (
        "afib", "atrial_fibrillation", "==", "boolean", True, None
    ),
    "r1-ablation-absent": (
        "afib_ablation", "afib_ablation", "==", "boolean", False, None
    ),
    "r1-valvular-absent": (
        "surgical_valvular_disease",
        "valvular_disease_requiring_surgery",
        "==",
        "boolean",
        False,
        None,
    ),
    "r1-hemorrhagic-absent": (
        "hemorrhagic",
        "hemorrhagic_tendency_or_blood_dyscrasia",
        "==",
        "boolean",
        False,
        None,
    ),
    "r1-ulcer-absent": (
        "peptic_ulcer_disease",
        "peptic_ulcer_disease",
        "==",
        "boolean",
        False,
        None,
    ),
    "r1-bleeding-absent": (
        "bleeding",
        "serious_bleeding_within_6_months",
        "==",
        "boolean",
        False,
        None,
    ),
    "r2-chads2-max": (
        "chads2", "chads2_score", "<=", "number", 3, None
    ),
    "r2-lvef-min": (
        "lvef", "left_ventricular_ejection_fraction", ">=", "number", 50, "%"
    ),
    "r2-recent-stroke-absent": (
        "recent_stroke",
        "stroke_during_admission_or_within_last_month",
        "==",
        "boolean",
        False,
        None,
    ),
    "r2-prior-stroke-absent": (
        "prior_stroke", "prior_stroke_or_tia", "==", "boolean", False, None
    ),
    "r2-heart-failure-absent": (
        "heart_failure", "heart_failure", "==", "boolean", False, None
    ),
    "r3-platelet-min": (
        "PLT", "platelet_count", ">=", "number", 100, "10^3/uL"
    ),
    "r3-bilirubin-max": (
        "BILI", "total_bilirubin", "<=", "number", 1.8, "mg/dL"
    ),
    "r3-ast-max": (
        "AST", "aspartate_aminotransferase", "<=", "number", 80, "U/L"
    ),
    "r3-creatinine-max": (
        "CREAT", "serum_creatinine", "<=", "number", 2.5, "mg/dL"
    ),
    "r3-hemoglobin-min": (
        "HGB", "hemoglobin", ">=", "number", 10, "g/dL"
    ),
    "r4-mdd-absent": (
        "mdd", "major_depressive_disorder", "==", "boolean", False, None
    ),
    "r4-schizophrenia-absent": (
        "schizophrenia",
        "schizophrenia_or_schizoaffective_disorder",
        "==",
        "boolean",
        False,
        None,
    ),
    "r4-bipolar-absent": (
        "bipolar", "bipolar_disorder", "==", "boolean", False, None
    ),
    "r4-decision-capacity-present": (
        "med_decisions",
        "unable_to_make_medical_decisions",
        "==",
        "boolean",
        False,
        None,
    ),
    "r5-no-diabetes": (
        "t2d", "diabetes_mellitus", "==", "boolean", False, None
    ),
    "r5-no-hypertension": (
        "arterial_hypertension",
        "treated_arterial_hypertension",
        "==",
        "boolean",
        False,
        None,
    ),
    "r5-diabetes": (
        "t2d", "diabetes_mellitus", "==", "boolean", True, None
    ),
    "r5-glucose-max": (
        "blood_glucose", "blood_glucose", "<=", "number", 180, "mg/dL"
    ),
}

EXPECTED_TOPOLOGY = {
    "apixaban-rule-1": (
        "all",
        "r1-afib-present",
        ("any", "r1-ablation-absent", "r1-valvular-absent"),
        "r1-hemorrhagic-absent",
        "r1-ulcer-absent",
        "r1-bleeding-absent",
    ),
    "apixaban-rule-2": (
        "all",
        "r2-chads2-max",
        "r2-lvef-min",
        "r2-recent-stroke-absent",
        "r2-prior-stroke-absent",
        "r2-heart-failure-absent",
    ),
    "apixaban-rule-3": (
        "all",
        "r3-platelet-min",
        "r3-bilirubin-max",
        "r3-ast-max",
        "r3-creatinine-max",
        "r3-hemoglobin-min",
    ),
    "apixaban-rule-4": (
        "all",
        "r4-mdd-absent",
        "r4-schizophrenia-absent",
        "r4-bipolar-absent",
        "r4-decision-capacity-present",
    ),
    "apixaban-rule-5": (
        "any",
        ("all", "r5-no-diabetes", "r5-no-hypertension"),
        ("all", "r5-diabetes", "r5-glucose-max"),
    ),
}


def _topology(expression: Mapping[str, Any]) -> Any:
    if set(expression) == {"atom"}:
        return expression["atom"]["condition_id"]
    if set(expression) != {"operator", "children"}:
        raise ApixabanSingleTrialError("Malformed intended-rule expression")
    operator = expression["operator"]
    if operator not in {"all", "any"} or not expression["children"]:
        raise ApixabanSingleTrialError("Invalid intended-rule expression operator")
    return (operator, *(_topology(child) for child in expression["children"]))


def _walk_atoms(expression: Mapping[str, Any]) -> Tuple[Mapping[str, Any], ...]:
    if "atom" in expression:
        return (expression["atom"],)
    return tuple(
        atom for child in expression["children"] for atom in _walk_atoms(child)
    )


def _typed_value(raw: Mapping[str, Any]) -> TypedValue:
    return TypedValue(
        value_type=ValueType(raw["value_type"]),
        value=raw["value"],
        unit=raw.get("unit"),
    )


def _build_expression(
    raw: Mapping[str, Any], *, source_id: str, source_length: int
) -> ConditionExpression:
    if "atom" in raw:
        atom = raw["atom"]
        return ConditionExpression(
            expression_type=ExpressionType.ATOM,
            atom=AtomicCondition(
                condition_id=atom["condition_id"],
                field=atom["fact_field"],
                operator=ComparisonOperator(atom["operator"]),
                expected=_typed_value(atom["expected"]),
                fact_selection=FactSelection.ANY,
                provenance=AtomProvenance(
                    source_id=source_id,
                    source_span=SourceSpan(start=0, end=source_length),
                    method=DecompositionMethod.HUMAN,
                ),
            ),
        )
    return ConditionExpression(
        expression_type=ExpressionType(raw["operator"]),
        children=tuple(
            _build_expression(
                child, source_id=source_id, source_length=source_length
            )
            for child in raw["children"]
        ),
    )


def _build_trial_unvalidated(document: Mapping[str, Any]) -> Trial:
    criteria = []
    for rule in document["rules"]:
        source_id = f"apixaban-intended-rule-contract:{rule['rule_id']}"
        source_text = rule["source_formula"]
        criteria.append(
            Criterion(
                criterion_id=rule["rule_id"],
                criterion_type=CriterionType.INCLUSION,
                description=rule["description"],
                source=CriterionSource(
                    source_id=source_id,
                    source_text=source_text,
                    section=CriterionType.INCLUSION,
                    document_version=document["contract_sha256"],
                ),
                expression=_build_expression(
                    rule["expression"],
                    source_id=source_id,
                    source_length=len(source_text),
                ),
                hard=rule["hard"],
                weight=1.0,
            )
        )
    return Trial(
        trial_id="apixaban-intended-five-rule-1.0.0",
        title="Mentor-intended Apixaban five-rule diagnostic",
        criteria=tuple(criteria),
    )


def validate_intended_rule_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_status",
        "contract_sha256",
        "provenance",
        "source_precedence",
        "semantics",
        "rules",
    }
    if set(document) != required:
        raise ApixabanSingleTrialError("Intended-rule contract fields are incomplete")
    if document["contract_version"] != CONTRACT_VERSION:
        raise ApixabanSingleTrialError("Unsupported intended-rule contract version")
    if document["contract_status"] != "scoring_frozen_pre_validation":
        raise ApixabanSingleTrialError("Unexpected intended-rule contract status")
    if _self_hash(document) != document["contract_sha256"]:
        raise ApixabanSingleTrialError("Intended-rule contract hash mismatch")

    provenance = document["provenance"]
    for field, expected in EXPECTED_SOURCE_HASHES.items():
        if provenance.get(field) != expected:
            raise ApixabanSingleTrialError(f"Intended-rule {field} mismatch")
    if provenance.get("mentor_ground_truth_role") != (
        "mentor_designated_rule_derived_project_ground_truth"
    ):
        raise ApixabanSingleTrialError("Mentor ground-truth role is overstated")
    if provenance.get("independent_clinical_gold") is not False:
        raise ApixabanSingleTrialError("Independent clinical gold must remain false")
    if provenance.get("result_generator_available") is not False:
        raise ApixabanSingleTrialError("Missing result generator must remain disclosed")
    if provenance.get("validation_labels_used") is not False or provenance.get(
        "locked_test_labels_used"
    ) is not False:
        raise ApixabanSingleTrialError("Holdout labels cannot influence the contract")

    expected_precedence = [
        "criteria_json_defines_intended_criteria_identity",
        "docx_arrow_formulas_define_executable_scoring",
        "official_readme_and_docx_define_full_annotation_intent",
        "official_csv_and_catalog_define_stable_fact_ids",
        "frozen_kleene_three_value_policy_handles_scoring_omissions",
    ]
    if document["source_precedence"] != expected_precedence:
        raise ApixabanSingleTrialError("Intended-rule source precedence changed")

    semantics = document["semantics"]
    if semantics != {
        "unknown_policy": "kleene_unknown_never_automatically_passes",
        "time_window_policy": "already_encoded_by_source_question_no_second_filter",
        "unit_assignment_basis": "mentor_docx_scoring_standard",
        "official_numeric_labels_store_units": False,
        "unit_assignment_is_mapping_assumption": True,
        "clinical_unit_safety_claim_allowed": False,
        "input_fact_cardinality": "one_normalized_fact_per_patient_question",
        "validation_run_authorized": False,
        "semi_ideal_rule_ids": list(RULE_IDS[:4]),
        "ideal_rule_ids": list(RULE_IDS),
        "exclusive_class_precedence": ["ideal", "semi-ideal", "non-ideal"],
    }:
        raise ApixabanSingleTrialError("Intended-rule semantics changed")

    rules = document["rules"]
    if [rule.get("rule_id") for rule in rules] != list(RULE_IDS):
        raise ApixabanSingleTrialError("Five intended rules must remain ordered")
    atoms = []
    for rule in rules:
        if set(rule) != {
            "rule_id",
            "description",
            "source_formula",
            "hard",
            "expression",
        }:
            raise ApixabanSingleTrialError("Intended rule fields are incomplete")
        if rule["hard"] is not True or not rule["source_formula"]:
            raise ApixabanSingleTrialError("Every intended rule must be a hard gate")
        if _topology(rule["expression"]) != EXPECTED_TOPOLOGY[rule["rule_id"]]:
            raise ApixabanSingleTrialError("Intended-rule topology changed")
        atoms.extend(_walk_atoms(rule["expression"]))

    condition_ids = [atom.get("condition_id") for atom in atoms]
    if len(condition_ids) != len(set(condition_ids)) or set(condition_ids) != set(
        EXPECTED_ATOMS
    ):
        raise ApixabanSingleTrialError("Intended atomic condition set changed")
    for atom in atoms:
        if _atom_signature(atom) != EXPECTED_ATOMS[atom["condition_id"]]:
            raise ApixabanSingleTrialError(
                f"Intended atom changed: {atom['condition_id']}"
            )

    catalog = load_question_catalog()
    if provenance.get("question_catalog_sha256") != catalog["catalog_sha256"]:
        raise ApixabanSingleTrialError("Intended-rule question catalog mismatch")
    catalog_fields = {
        question["source_criterion_label"]: question["fact_field"]
        for question in catalog["questions"]
    }
    for atom in atoms:
        if catalog_fields.get(atom["source_criterion_label"]) != atom["fact_field"]:
            raise ApixabanSingleTrialError("Intended atom does not match fact catalog")
    counts = Counter(atom["source_criterion_label"] for atom in atoms)
    if set(counts) != set(catalog_fields) or counts != Counter(
        {**{label: 1 for label in catalog_fields}, "t2d": 2}
    ):
        raise ApixabanSingleTrialError(
            "Intended rules must cover 23 facts; diabetes appears in both branches"
        )
    _build_trial_unvalidated(document)


def load_intended_rule_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_intended_rule_contract(document)
    return document


def build_intended_trial(
    contract: Optional[Mapping[str, Any]] = None,
) -> Trial:
    resolved = dict(contract or load_intended_rule_contract())
    validate_intended_rule_contract(resolved)
    return _build_trial_unvalidated(resolved)


def project_intended_class(
    rule_decisions: Mapping[str, Decision | str],
) -> str:
    if set(rule_decisions) != set(RULE_IDS):
        raise ApixabanSingleTrialError("Class projection requires all five rules")
    decisions = {
        rule_id: (
            value if isinstance(value, Decision) else Decision(value)
        )
        for rule_id, value in rule_decisions.items()
    }
    first_four = [decisions[rule_id] for rule_id in RULE_IDS[:4]]
    if Decision.INELIGIBLE in first_four:
        return "non-ideal"
    if Decision.UNKNOWN in first_four:
        return "unknown"
    rule_five = decisions[RULE_IDS[4]]
    if rule_five is Decision.UNKNOWN:
        return "unknown"
    return "ideal" if rule_five is Decision.ELIGIBLE else "semi-ideal"


def project_mentor_reference_class(
    *, ideal_candidate: bool, semi_ideal_candidate: bool
) -> str:
    if (
        type(ideal_candidate) is not bool
        or type(semi_ideal_candidate) is not bool
    ):
        raise ApixabanSingleTrialError("Mentor reference flags must be booleans")
    if ideal_candidate and not semi_ideal_candidate:
        raise ApixabanSingleTrialError(
            "Mentor ideal flag must remain a subset of semi-ideal"
        )
    if ideal_candidate:
        return "ideal"
    if semi_ideal_candidate:
        return "semi-ideal"
    return "non-ideal"
