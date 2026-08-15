"""Safely connect frozen model fact predictions to the typed verifier.

This adapter deliberately keeps two questions separate:

1. what fact did the model report; and
2. what does that fact mean for one explicitly supplied trial criterion?

Only schema-valid, evidence-grounded known facts enter the symbolic core.
Everything else remains an auditable abstention rather than an invented fact.
"""

from dataclasses import dataclass
from datetime import date
import hashlib
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_contract import load_question_catalog, question_index
from .apixaban_evaluation import validate_prediction_set
from .models import (
    ConditionExpression,
    Criterion,
    CriterionDecision,
    Evidence,
    ExpressionType,
    Fact,
    Patient,
    TypedValue,
    ValueType,
)
from .pipeline import evaluate_criterion


MAPPING_VERSION = "1.0.0"

MAPPED = "mapped"
MODEL_UNKNOWN = "model_unknown"
MISSING_EVIDENCE = "missing_evidence"
UNKNOWN_EVIDENCE = "unknown_evidence"
CRITERION_FIELD_MISMATCH = "criterion_field_mismatch"


class ModelFactVerifierError(ValueError):
    """Raised when the caller supplies an ambiguous or invalid binding."""


@dataclass(frozen=True)
class ModelFactVerification:
    """Typed fact mapping plus the decision for one explicit criterion."""

    mapping_version: str
    patient_id: str
    question_id: str
    criterion_id: str
    mapping_status: str
    mapping_reason: str
    fact: Optional[Fact]
    criterion_decision: CriterionDecision


def _criterion_fields(expression: ConditionExpression) -> Tuple[str, ...]:
    if expression.expression_type is ExpressionType.ATOM:
        if expression.atom is None:
            raise ModelFactVerifierError(
                "Validated ATOM unexpectedly has no atom"
            )
        return (expression.atom.field,)
    return tuple(
        field
        for child in expression.children
        for field in _criterion_fields(child)
    )


def _select_prediction(
    prediction_set: Mapping[str, Any],
    patient_id: str,
    question_id: str,
) -> Mapping[str, Any]:
    matches = [
        item
        for item in prediction_set["predictions"]
        if item["patient_id"] == patient_id
        and item["question_id"] == question_id
    ]
    if len(matches) != 1:
        raise ModelFactVerifierError(
            "Exactly one prediction must match patient_id and question_id"
        )
    return matches[0]


def _fact_id(
    prediction_set: Mapping[str, Any],
    patient_id: str,
    question_id: str,
) -> str:
    identity = "\0".join(
        (
            str(prediction_set["model_id"]),
            str(prediction_set["prompt_version"]),
            patient_id,
            question_id,
        )
    )
    return f"model-fact-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _unknown_decision(
    patient_id: str,
    index_date: date,
    criterion: Criterion,
) -> CriterionDecision:
    patient = Patient(
        patient_id=patient_id,
        index_date=index_date,
        facts=(),
        evidence=(),
    )
    return evaluate_criterion(patient, criterion)


def verify_model_fact_for_criterion(
    *,
    prediction_set: Dict[str, Any],
    patient_id: str,
    question_id: str,
    criterion: Criterion,
    patient_evidence: Sequence[Evidence],
    index_date: date,
    catalog: Optional[Dict[str, Any]] = None,
) -> ModelFactVerification:
    """Map one model fact and evaluate one explicitly supplied criterion.

    ``patient_evidence`` must be the caller's patient-local evidence inventory.
    Referenced IDs are checked against it; the adapter never creates evidence.
    """

    resolved_catalog = catalog or load_question_catalog()
    validate_prediction_set(prediction_set, resolved_catalog)
    questions = question_index(resolved_catalog)
    question = questions.get(question_id)
    if question is None:
        raise ModelFactVerifierError("Question is not present in the catalog")
    prediction = _select_prediction(prediction_set, patient_id, question_id)

    evidence_by_id = {item.evidence_id: item for item in patient_evidence}
    if len(evidence_by_id) != len(patient_evidence):
        raise ModelFactVerifierError("Patient evidence IDs must be unique")

    field = question["fact_field"]
    criterion_fields = set(_criterion_fields(criterion.expression))
    if field not in criterion_fields:
        status = CRITERION_FIELD_MISMATCH
        reason = (
            f"Fact field {field!r} is not used by criterion "
            f"{criterion.criterion_id!r}."
        )
        fact = None
    elif prediction["fact_status"] == "unknown":
        status = MODEL_UNKNOWN
        reason = "The model abstained or reported an unknown fact."
        fact = None
    elif not prediction["evidence_ids"]:
        status = MISSING_EVIDENCE
        reason = "A known model fact has no supporting evidence ID."
        fact = None
    else:
        missing_ids = sorted(
            set(prediction["evidence_ids"]) - set(evidence_by_id)
        )
        if missing_ids:
            status = UNKNOWN_EVIDENCE
            reason = "Model fact references evidence outside the supplied patient inventory."
            fact = None
        else:
            value_type = (
                ValueType.BOOLEAN
                if prediction["question_type"] == "boolean"
                else ValueType.NUMBER
            )
            fact = Fact(
                fact_id=_fact_id(prediction_set, patient_id, question_id),
                field=field,
                value=TypedValue(
                    value_type=value_type,
                    value=prediction["value"],
                    unit=prediction["unit"],
                ),
                evidence_ids=tuple(prediction["evidence_ids"]),
                observed_at=None,
            )
            status = MAPPED
            reason = "Schema-valid known fact mapped with existing patient evidence."

    if fact is None:
        decision = _unknown_decision(patient_id, index_date, criterion)
    else:
        cited_evidence = tuple(
            evidence_by_id[evidence_id]
            for evidence_id in fact.evidence_ids
        )
        patient = Patient(
            patient_id=patient_id,
            index_date=index_date,
            facts=(fact,),
            evidence=cited_evidence,
        )
        decision = evaluate_criterion(patient, criterion)

    return ModelFactVerification(
        mapping_version=MAPPING_VERSION,
        patient_id=patient_id,
        question_id=question_id,
        criterion_id=criterion.criterion_id,
        mapping_status=status,
        mapping_reason=reason,
        fact=fact,
        criterion_decision=decision,
    )
