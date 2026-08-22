import hashlib
import json
import math
from importlib.resources import files
from typing import Any, Dict, Iterable, Mapping, Optional

from .validation import validate_document


CATALOG_VERSION = "1.0.0"
FACT_ASSESSMENT_VERSION = "1.0.0"
CATALOG_RESOURCE = (
    "resources/apixaban-question-catalog-1.0.0.json"
)
CATALOG_SCHEMA = (
    "schemas/apixaban-question-catalog-1.0.0.schema.json"
)
FACT_ASSESSMENT_SCHEMA = (
    "schemas/apixaban-fact-assessment-1.0.0.schema.json"
)

KNOWN_FACT_EMPTY_EVIDENCE_EXCEPTION = {
    "source_criterion_label": "med_decisions",
    "fact_status": "absent",
    "value": False,
    "basis": "source_question_default_absent",
}


class ApixabanContractError(ValueError):
    """Raised when the frozen note-grounded task contract is violated."""


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _question_id(label: str, question_type: str, question: str) -> str:
    digest = hashlib.sha256(
        f"{label}\0{question_type}\0{question}".encode("utf-8")
    ).hexdigest()
    return f"apixaban-q-{digest[:16]}"


def validate_question_catalog(document: Dict[str, Any]) -> None:
    validate_document(document, CATALOG_SCHEMA)
    unsigned = dict(document)
    recorded_hash = unsigned.pop("catalog_sha256")
    if _canonical_sha256(unsigned) != recorded_hash:
        raise ApixabanContractError("Question catalog hash mismatch")

    questions = document["questions"]
    for field in ("question_id", "source_criterion_label", "fact_field"):
        values = [question[field] for question in questions]
        if len(values) != len(set(values)):
            raise ApixabanContractError(
                f"Question catalog {field} values must be unique"
            )

    type_counts = {"boolean": 0, "numeric": 0}
    for question in questions:
        question_type = question["question_type"]
        type_counts[question_type] += 1
        expected_id = _question_id(
            question["source_criterion_label"],
            question_type,
            question["source_question"],
        )
        if question["question_id"] != expected_id:
            raise ApixabanContractError(
                f"Question ID does not match its frozen source definition: "
                f"{question['source_criterion_label']}"
            )
        if question_type == "boolean":
            expected = {
                "aggregation": "question_defined_boolean",
                "value_type": "boolean",
                "allowed_fact_status": ["present", "absent", "unknown"],
            }
        else:
            expected = {
                "value_type": "number",
                "allowed_fact_status": ["present", "unknown"],
            }
            if question["aggregation"] not in {"minimum", "maximum"}:
                raise ApixabanContractError(
                    "Numeric questions must declare minimum or maximum"
                )
        for field, expected_value in expected.items():
            if question[field] != expected_value:
                raise ApixabanContractError(
                    f"Invalid {field} for {question['question_id']}"
                )
    if type_counts != {"boolean": 15, "numeric": 8}:
        raise ApixabanContractError(
            "Frozen catalog must contain 15 boolean and 8 numeric questions"
        )


def load_question_catalog() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CATALOG_RESOURCE)
    document: Dict[str, Any] = json.loads(
        resource.read_text(encoding="utf-8")
    )
    validate_question_catalog(document)
    return document


def question_index(
    catalog: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    resolved = catalog or load_question_catalog()
    return {
        question["question_id"]: question
        for question in resolved["questions"]
    }


def known_fact_allows_empty_evidence(
    question: Mapping[str, Any], prediction: Mapping[str, Any]
) -> bool:
    """Return whether the frozen source question permits a known empty citation."""

    exception = KNOWN_FACT_EMPTY_EVIDENCE_EXCEPTION
    return (
        question["source_criterion_label"]
        == exception["source_criterion_label"]
        and prediction["fact_status"] == exception["fact_status"]
        and prediction["value"] is exception["value"]
    )


def validate_source_question_definitions(
    definitions: Mapping[str, Any],
    catalog: Optional[Dict[str, Any]] = None,
) -> None:
    resolved = catalog or load_question_catalog()
    expected = {
        question["source_criterion_label"]: (
            question["question_type"],
            question["source_question"],
        )
        for question in resolved["questions"]
    }
    normalized = {
        label: tuple(definition)
        for label, definition in definitions.items()
    }
    if normalized != expected:
        missing = sorted(set(expected) - set(normalized))
        unexpected = sorted(set(normalized) - set(expected))
        changed = sorted(
            label
            for label in set(expected) & set(normalized)
            if expected[label] != normalized[label]
        )
        raise ApixabanContractError(
            "Official source question definitions do not match catalog "
            f"1.0.0 (missing={missing}, unexpected={unexpected}, "
            f"changed={changed})"
        )


def validate_fact_assessment(
    document: Dict[str, Any],
    catalog: Optional[Dict[str, Any]] = None,
) -> None:
    validate_document(document, FACT_ASSESSMENT_SCHEMA)
    questions = question_index(catalog)
    question = questions.get(document["question_id"])
    if question is None:
        raise ApixabanContractError("Assessment references an unknown question")
    if document["question_type"] != question["question_type"]:
        raise ApixabanContractError(
            "Assessment question_type does not match the question catalog"
        )
    if document["fact_status"] not in question["allowed_fact_status"]:
        raise ApixabanContractError(
            "Assessment fact_status is not allowed for this question"
        )
    if document["unit"] != question["canonical_unit"]:
        raise ApixabanContractError(
            "Assessment unit does not match the frozen note-only gold contract"
        )
    value = document["value"]
    if isinstance(value, float) and not math.isfinite(value):
        raise ApixabanContractError("Assessment numeric value must be finite")


def normalize_source_answer(
    *,
    assessment_id: str,
    patient_id: str,
    question_id: str,
    answer_status: str,
    answer_value: Any,
    evidence_ids: Iterable[str] = (),
    catalog: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    questions = question_index(catalog)
    question = questions.get(question_id)
    if question is None:
        raise ApixabanContractError("Source answer references an unknown question")

    resolved_evidence_ids = list(evidence_ids)
    if answer_status == "answered":
        if question["question_type"] == "boolean":
            if not isinstance(answer_value, bool):
                raise ApixabanContractError(
                    "Answered boolean source label must be boolean"
                )
            fact_status = "present" if answer_value else "absent"
        else:
            if isinstance(answer_value, bool) or not isinstance(
                answer_value, (int, float)
            ):
                raise ApixabanContractError(
                    "Answered numeric source label must be numeric"
                )
            if not math.isfinite(float(answer_value)):
                raise ApixabanContractError(
                    "Answered numeric source label must be finite"
                )
            fact_status = "present"
        value = answer_value
        abstained = False
        abstention_reason = None
    elif answer_status in {"not_specified", "source_anomaly"}:
        if answer_value is not None:
            raise ApixabanContractError(
                "Unresolved source label must have a null value"
            )
        fact_status = "unknown"
        value = None
        abstained = True
        abstention_reason = (
            "source_not_specified"
            if answer_status == "not_specified"
            else "source_anomaly"
        )
    else:
        raise ApixabanContractError(
            f"Unsupported source answer status: {answer_status!r}"
        )

    document: Dict[str, Any] = {
        "fact_assessment_version": FACT_ASSESSMENT_VERSION,
        "question_catalog_version": CATALOG_VERSION,
        "assessment_id": assessment_id,
        "patient_id": patient_id,
        "question_id": question_id,
        "question_type": question["question_type"],
        "assessment_source": "released_source_label",
        "fact_status": fact_status,
        "value": value,
        "unit": question["canonical_unit"],
        "evidence_status": (
            "provided" if resolved_evidence_ids else "not_available_in_source"
        ),
        "evidence_ids": resolved_evidence_ids,
        "abstained": abstained,
        "abstention_reason": abstention_reason,
    }
    validate_fact_assessment(document, catalog)
    return document
