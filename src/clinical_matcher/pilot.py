import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .splits import current_git_commit
from .validation import validate_document


PILOT_MANIFEST_SCHEMA = "schemas/pilot-manifest-1.0.0.schema.json"
PILOT_ANNOTATION_SCHEMA = "schemas/pilot-annotation-1.0.0.schema.json"
PILOT_ADJUDICATION_SCHEMA = "schemas/pilot-adjudication-1.0.0.schema.json"
PILOT_SUMMARY_SCHEMA = "schemas/pilot-summary-1.0.0.schema.json"
DECISIONS = {"eligible", "ineligible", "unknown"}


class PilotValidationError(ValueError):
    """Raised when pilot records cannot support independent benchmark gold."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unsigned_hash(document: Dict[str, Any], hash_field: str) -> str:
    payload = dict(document)
    payload.pop(hash_field, None)
    return _canonical_hash(payload)


def validate_pilot_manifest(manifest: Dict[str, Any]) -> None:
    validate_document(manifest, PILOT_MANIFEST_SCHEMA)
    if manifest["manifest_sha256"] != _unsigned_hash(
        manifest,
        "manifest_sha256",
    ):
        raise PilotValidationError("Pilot manifest hash mismatch")
    annotators = manifest["annotator_ids"]
    if len(annotators) != len(set(annotators)):
        raise PilotValidationError("Pilot annotator IDs must be unique")
    unit_ids = [unit["unit_id"] for unit in manifest["units"]]
    if len(unit_ids) != len(set(unit_ids)):
        raise PilotValidationError("Pilot unit IDs must be unique")
    pairs = [
        (unit["patient_id"], unit["trial_id"])
        for unit in manifest["units"]
    ]
    if len(pairs) != len(set(pairs)):
        raise PilotValidationError(
            "Each patient-trial pair may appear only once"
        )
    for unit in manifest["units"]:
        if len(unit["criterion_ids"]) != len(set(unit["criterion_ids"])):
            raise PilotValidationError(
                f"Duplicate criterion IDs in {unit['unit_id']}"
            )
        if len(unit["allowed_evidence_ids"]) != len(
            set(unit["allowed_evidence_ids"])
        ):
            raise PilotValidationError(
                f"Duplicate evidence IDs in {unit['unit_id']}"
            )


def finalize_pilot_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    document = dict(manifest)
    document["manifest_sha256"] = _unsigned_hash(
        document,
        "manifest_sha256",
    )
    validate_pilot_manifest(document)
    return document


def build_annotation_template(
    manifest: Dict[str, Any],
    annotator_id: str,
) -> Dict[str, Any]:
    validate_pilot_manifest(manifest)
    if annotator_id not in manifest["annotator_ids"]:
        raise PilotValidationError("Annotator is not assigned to this pilot")
    document = {
        "pilot_annotation_version": "1.0.0",
        "pilot_id": manifest["pilot_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "annotation_manual_version": manifest[
            "annotation_manual_version"
        ],
        "annotator_id": annotator_id,
        "annotation_status": "draft",
        "independence_attestation": {
            "peer_annotations_not_viewed": False,
            "model_outputs_not_viewed": False,
        },
        "units": [
            {
                "unit_id": unit["unit_id"],
                "active_minutes": None,
                "trial_judgment": {
                    "decision": None,
                    "relevance_grade": None,
                    "rationale": None,
                },
                "criterion_judgments": [
                    {
                        "criterion_id": criterion_id,
                        "decision": None,
                        "evidence_ids": [],
                        "rationale": None,
                    }
                    for criterion_id in unit["criterion_ids"]
                ],
            }
            for unit in manifest["units"]
        ],
    }
    validate_annotation(manifest, document, require_completed=False)
    return document


def _manifest_units(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {unit["unit_id"]: unit for unit in manifest["units"]}


def validate_annotation(
    manifest: Dict[str, Any],
    annotation: Dict[str, Any],
    require_completed: bool = True,
) -> None:
    validate_pilot_manifest(manifest)
    validate_document(annotation, PILOT_ANNOTATION_SCHEMA)
    if annotation["pilot_id"] != manifest["pilot_id"]:
        raise PilotValidationError("Annotation references another pilot")
    if annotation["manifest_sha256"] != manifest["manifest_sha256"]:
        raise PilotValidationError("Annotation manifest hash mismatch")
    if (
        annotation["annotation_manual_version"]
        != manifest["annotation_manual_version"]
    ):
        raise PilotValidationError("Annotation manual version mismatch")
    if annotation["annotator_id"] not in manifest["annotator_ids"]:
        raise PilotValidationError("Unexpected pilot annotator")
    expected = _manifest_units(manifest)
    actual_ids = [unit["unit_id"] for unit in annotation["units"]]
    if len(actual_ids) != len(set(actual_ids)):
        raise PilotValidationError("Annotation unit IDs must be unique")
    if set(actual_ids) != set(expected):
        raise PilotValidationError("Annotation must cover every pilot unit")
    completed = annotation["annotation_status"] == "completed"
    if require_completed and not completed:
        raise PilotValidationError("Annotation is not completed")
    if completed and not all(annotation["independence_attestation"].values()):
        raise PilotValidationError(
            "Completed annotation requires both independence attestations"
        )
    for unit in annotation["units"]:
        specification = expected[unit["unit_id"]]
        criterion_ids = [
            item["criterion_id"]
            for item in unit["criterion_judgments"]
        ]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise PilotValidationError("Criterion judgments must be unique")
        if set(criterion_ids) != set(specification["criterion_ids"]):
            raise PilotValidationError(
                f"Criterion coverage mismatch in {unit['unit_id']}"
            )
        if not completed:
            continue
        if unit["active_minutes"] is None or unit["active_minutes"] <= 0:
            raise PilotValidationError(
                "Completed unit requires positive active_minutes"
            )
        trial = unit["trial_judgment"]
        if (
            trial["decision"] not in DECISIONS
            or trial["relevance_grade"] not in range(4)
            or not trial["rationale"]
        ):
            raise PilotValidationError("Completed trial judgment is incomplete")
        allowed_evidence = set(specification["allowed_evidence_ids"])
        for judgment in unit["criterion_judgments"]:
            if (
                judgment["decision"] not in DECISIONS
                or not judgment["rationale"]
            ):
                raise PilotValidationError(
                    "Completed criterion judgment is incomplete"
                )
            missing = set(judgment["evidence_ids"]) - allowed_evidence
            if missing:
                raise PilotValidationError(
                    "Criterion judgment references evidence outside the "
                    f"patient unit: {sorted(missing)}"
                )


def _annotation_units(
    annotation: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    return {unit["unit_id"]: unit for unit in annotation["units"]}


def _criterion_map(unit: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        item["criterion_id"]: item
        for item in unit["criterion_judgments"]
    }


def _criterion_equal(
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> bool:
    return (
        left["decision"] == right["decision"]
        and set(left["evidence_ids"]) == set(right["evidence_ids"])
    )


def _unit_disagreements(
    specification: Dict[str, Any],
    left: Dict[str, Any],
    right: Dict[str, Any],
) -> List[str]:
    disagreement_types = []
    if (
        left["trial_judgment"]["decision"]
        != right["trial_judgment"]["decision"]
    ):
        disagreement_types.append("trial_decision")
    if (
        left["trial_judgment"]["relevance_grade"]
        != right["trial_judgment"]["relevance_grade"]
    ):
        disagreement_types.append("trial_relevance")
    left_criteria = _criterion_map(left)
    right_criteria = _criterion_map(right)
    for criterion_id in specification["criterion_ids"]:
        first = left_criteria[criterion_id]
        second = right_criteria[criterion_id]
        if first["decision"] != second["decision"]:
            disagreement_types.append("criterion_decision")
        if set(first["evidence_ids"]) != set(second["evidence_ids"]):
            disagreement_types.append("criterion_evidence")
    return sorted(set(disagreement_types))


def build_adjudication_template(
    manifest: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    adjudicator_ids: Sequence[str],
) -> Dict[str, Any]:
    if len(annotations) != 2:
        raise PilotValidationError("Exactly two independent annotations required")
    for annotation in annotations:
        validate_annotation(manifest, annotation, require_completed=True)
    annotator_ids = [item["annotator_id"] for item in annotations]
    if len(set(annotator_ids)) != 2:
        raise PilotValidationError("Independent annotations must be from two people")
    if set(annotator_ids) != set(manifest["annotator_ids"]):
        raise PilotValidationError("Both assigned annotators are required")
    if not adjudicator_ids or len(adjudicator_ids) != len(set(adjudicator_ids)):
        raise PilotValidationError("Adjudicator IDs must be non-empty and unique")

    left_units = _annotation_units(annotations[0])
    right_units = _annotation_units(annotations[1])
    units = []
    for specification in manifest["units"]:
        unit_id = specification["unit_id"]
        left = left_units[unit_id]
        right = right_units[unit_id]
        left_trial = left["trial_judgment"]
        right_trial = right["trial_judgment"]
        disagreement_types = _unit_disagreements(
            specification,
            left,
            right,
        )
        left_criteria = _criterion_map(left)
        right_criteria = _criterion_map(right)
        criteria = []
        for criterion_id in specification["criterion_ids"]:
            first = left_criteria[criterion_id]
            second = right_criteria[criterion_id]
            agrees = _criterion_equal(first, second)
            criteria.append(
                {
                    "criterion_id": criterion_id,
                    "decision": first["decision"] if agrees else None,
                    "evidence_ids": (
                        sorted(set(first["evidence_ids"]))
                        if agrees
                        else []
                    ),
                    "rationale": None,
                }
            )
        units.append(
            {
                "unit_id": unit_id,
                "resolution_status": (
                    "agreed_without_dispute"
                    if not disagreement_types
                    else "unresolved"
                ),
                "disagreement_types": sorted(set(disagreement_types)),
                "active_person_minutes": 0.0,
                "trial_judgment": {
                    "decision": (
                        left_trial["decision"]
                        if left_trial["decision"] == right_trial["decision"]
                        else None
                    ),
                    "relevance_grade": (
                        left_trial["relevance_grade"]
                        if left_trial["relevance_grade"]
                        == right_trial["relevance_grade"]
                        else None
                    ),
                    "rationale": None,
                },
                "criterion_judgments": criteria,
            }
        )
    document = {
        "pilot_adjudication_version": "1.0.0",
        "pilot_id": manifest["pilot_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "annotation_manual_version": manifest[
            "annotation_manual_version"
        ],
        "source_annotator_ids": sorted(annotator_ids),
        "adjudicator_ids": sorted(adjudicator_ids),
        "adjudication_status": "draft",
        "units": units,
    }
    validate_adjudication(
        manifest,
        annotations,
        document,
        require_completed=False,
    )
    return document


def validate_adjudication(
    manifest: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    adjudication: Dict[str, Any],
    require_completed: bool = True,
) -> None:
    for annotation in annotations:
        validate_annotation(manifest, annotation, require_completed=True)
    validate_document(adjudication, PILOT_ADJUDICATION_SCHEMA)
    if adjudication["pilot_id"] != manifest["pilot_id"]:
        raise PilotValidationError("Adjudication references another pilot")
    if adjudication["manifest_sha256"] != manifest["manifest_sha256"]:
        raise PilotValidationError("Adjudication manifest hash mismatch")
    if (
        adjudication["annotation_manual_version"]
        != manifest["annotation_manual_version"]
    ):
        raise PilotValidationError("Adjudication manual version mismatch")
    if set(adjudication["source_annotator_ids"]) != {
        item["annotator_id"] for item in annotations
    }:
        raise PilotValidationError("Adjudication annotator set mismatch")
    expected = _manifest_units(manifest)
    unit_ids = [unit["unit_id"] for unit in adjudication["units"]]
    if len(unit_ids) != len(set(unit_ids)) or set(unit_ids) != set(expected):
        raise PilotValidationError("Adjudication must cover every unit once")
    completed = adjudication["adjudication_status"] == "completed"
    if require_completed and not completed:
        raise PilotValidationError("Adjudication is not completed")
    annotation_units = [
        _annotation_units(annotation) for annotation in annotations
    ]
    for unit in adjudication["units"]:
        specification = expected[unit["unit_id"]]
        criteria = _criterion_map(unit)
        if set(criteria) != set(specification["criterion_ids"]):
            raise PilotValidationError(
                "Adjudication criterion coverage mismatch"
            )
        originals = [
            source[unit["unit_id"]] for source in annotation_units
        ]
        expected_disagreements = _unit_disagreements(
            specification,
            originals[0],
            originals[1],
        )
        if unit["disagreement_types"] != expected_disagreements:
            raise PilotValidationError(
                "Adjudication disagreement types do not match annotations"
            )
        has_dispute = bool(expected_disagreements)
        if completed and unit["resolution_status"] == "unresolved":
            raise PilotValidationError(
                "Completed adjudication cannot contain unresolved units"
            )
        if (
            not has_dispute
            and unit["resolution_status"] != "agreed_without_dispute"
        ):
            raise PilotValidationError(
                "Non-disputed unit must be marked agreed_without_dispute"
            )
        if has_dispute and unit["resolution_status"] == "agreed_without_dispute":
            raise PilotValidationError("Disputed unit cannot be marked agreed")
        if has_dispute and completed and unit["active_person_minutes"] <= 0:
            raise PilotValidationError(
                "Resolved dispute requires positive adjudication person-minutes"
            )
        if completed:
            trial = unit["trial_judgment"]
            if (
                trial["decision"] not in DECISIONS
                or trial["relevance_grade"] not in range(4)
                or not trial["rationale"]
            ):
                raise PilotValidationError(
                    "Completed adjudicated trial judgment is incomplete"
                )
            allowed = set(specification["allowed_evidence_ids"])
            for judgment in unit["criterion_judgments"]:
                if (
                    judgment["decision"] not in DECISIONS
                    or not judgment["rationale"]
                ):
                    raise PilotValidationError(
                        "Completed adjudicated criterion is incomplete"
                    )
                if set(judgment["evidence_ids"]) - allowed:
                    raise PilotValidationError(
                        "Adjudication references evidence outside patient unit"
                    )
        if unit["resolution_status"] == "agreed_without_dispute":
            first, second = originals
            if (
                first["trial_judgment"]["decision"]
                != second["trial_judgment"]["decision"]
                or first["trial_judgment"]["relevance_grade"]
                != second["trial_judgment"]["relevance_grade"]
            ):
                raise PilotValidationError("Trial agreement flag is incorrect")
            for criterion_id in specification["criterion_ids"]:
                if not _criterion_equal(
                    _criterion_map(first)[criterion_id],
                    _criterion_map(second)[criterion_id],
                ):
                    raise PilotValidationError(
                        "Criterion agreement flag is incorrect"
                    )
            if completed:
                trial = unit["trial_judgment"]
                if (
                    trial["decision"]
                    != first["trial_judgment"]["decision"]
                    or trial["relevance_grade"]
                    != first["trial_judgment"]["relevance_grade"]
                ):
                    raise PilotValidationError(
                        "Agreed trial judgment was changed during adjudication"
                    )
                adjudicated_criteria = _criterion_map(unit)
                first_criteria = _criterion_map(first)
                for criterion_id in specification["criterion_ids"]:
                    if not _criterion_equal(
                        adjudicated_criteria[criterion_id],
                        first_criteria[criterion_id],
                    ):
                        raise PilotValidationError(
                            "Agreed criterion judgment was changed during "
                            "adjudication"
                        )


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return float(ordered[index])


def _cohen_kappa(left: Iterable[str], right: Iterable[str]) -> float:
    first = list(left)
    second = list(right)
    if len(first) != len(second) or not first:
        return 0.0
    observed = _rate(
        sum(a == b for a, b in zip(first, second)),
        len(first),
    )
    left_counts = Counter(first)
    right_counts = Counter(second)
    labels = set(left_counts) | set(right_counts)
    expected = sum(
        _rate(left_counts[label], len(first))
        * _rate(right_counts[label], len(second))
        for label in labels
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def build_pilot_summary(
    manifest: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    adjudication: Dict[str, Any],
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    validate_adjudication(
        manifest,
        annotations,
        adjudication,
        require_completed=True,
    )
    left_units = _annotation_units(annotations[0])
    right_units = _annotation_units(annotations[1])
    annotation_minutes = [
        unit["active_minutes"]
        for annotation in annotations
        for unit in annotation["units"]
    ]
    trial_decision_agreements = 0
    trial_relevance_agreements = 0
    criterion_decision_agreements = 0
    criterion_evidence_agreements = 0
    criterion_count = 0
    left_trial_decisions = []
    right_trial_decisions = []
    left_criterion_decisions = []
    right_criterion_decisions = []
    disputed_units = 0
    adjudication_minutes = 0.0
    for adjudicated in adjudication["units"]:
        unit_id = adjudicated["unit_id"]
        first = left_units[unit_id]
        second = right_units[unit_id]
        first_trial = first["trial_judgment"]
        second_trial = second["trial_judgment"]
        left_trial_decisions.append(first_trial["decision"])
        right_trial_decisions.append(second_trial["decision"])
        trial_decision_agreements += (
            first_trial["decision"] == second_trial["decision"]
        )
        trial_relevance_agreements += (
            first_trial["relevance_grade"]
            == second_trial["relevance_grade"]
        )
        first_criteria = _criterion_map(first)
        second_criteria = _criterion_map(second)
        for criterion_id in first_criteria:
            a = first_criteria[criterion_id]
            b = second_criteria[criterion_id]
            criterion_count += 1
            left_criterion_decisions.append(a["decision"])
            right_criterion_decisions.append(b["decision"])
            criterion_decision_agreements += a["decision"] == b["decision"]
            criterion_evidence_agreements += set(a["evidence_ids"]) == set(
                b["evidence_ids"]
            )
        if adjudicated["disagreement_types"]:
            disputed_units += 1
            adjudication_minutes += adjudicated["active_person_minutes"]

    unit_count = len(manifest["units"])
    mean_adjudication = (
        adjudication_minutes / disputed_units if disputed_units else 0.0
    )
    summary: Dict[str, Any] = {
        "pilot_summary_version": "1.0.0",
        "summary_sha256": "pending",
        "pilot_id": manifest["pilot_id"],
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "validated_for_capacity": True,
        "counts": {
            "annotator_count": len(annotations),
            "patient_trial_unit_count": unit_count,
            "criterion_unit_count": criterion_count,
            "annotation_observation_count": len(annotation_minutes),
            "disputed_patient_trial_unit_count": disputed_units,
            "unresolved_unit_count": 0,
        },
        "timing": {
            "annotation_total_person_minutes": round(
                sum(annotation_minutes),
                4,
            ),
            "annotation_median_minutes": round(
                _percentile(annotation_minutes, 0.5),
                4,
            ),
            "annotation_p75_minutes": round(
                _percentile(annotation_minutes, 0.75),
                4,
            ),
            "adjudication_total_person_minutes": round(
                adjudication_minutes,
                4,
            ),
            "adjudication_person_minutes_per_disputed_unit": round(
                mean_adjudication,
                4,
            ),
        },
        "agreement": {
            "patient_trial_disagreement_rate": round(
                _rate(disputed_units, unit_count),
                6,
            ),
            "trial_decision_exact_agreement": round(
                _rate(trial_decision_agreements, unit_count),
                6,
            ),
            "trial_decision_cohen_kappa": round(
                _cohen_kappa(
                    left_trial_decisions,
                    right_trial_decisions,
                ),
                6,
            ),
            "trial_relevance_exact_agreement": round(
                _rate(trial_relevance_agreements, unit_count),
                6,
            ),
            "criterion_decision_exact_agreement": round(
                _rate(criterion_decision_agreements, criterion_count),
                6,
            ),
            "criterion_decision_cohen_kappa": round(
                _cohen_kappa(
                    left_criterion_decisions,
                    right_criterion_decisions,
                ),
                6,
            ),
            "criterion_evidence_exact_agreement": round(
                _rate(criterion_evidence_agreements, criterion_count),
                6,
            ),
        },
        "capacity_inputs": {
            "annotator_count": len(annotations),
            "required_annotations_per_unit": len(annotations),
            "minutes_per_annotation": round(
                _percentile(annotation_minutes, 0.75),
                4,
            ),
            "expected_adjudication_rate": round(
                _rate(disputed_units, unit_count),
                6,
            ),
            "minutes_per_adjudication": round(mean_adjudication, 4),
            "pilot_unit_count": unit_count,
        },
        "disclosure_note": (
            "This report contains no patient, trial, criterion, evidence, or "
            "annotator IDs and no clinical text. It remains a restricted-data "
            "derivative until governance approves disclosure."
        ),
    }
    summary["summary_sha256"] = _unsigned_hash(
        summary,
        "summary_sha256",
    )
    validate_pilot_summary(summary)
    return summary


def validate_pilot_summary(summary: Dict[str, Any]) -> None:
    validate_document(summary, PILOT_SUMMARY_SCHEMA)
    if summary["summary_sha256"] != _unsigned_hash(
        summary,
        "summary_sha256",
    ):
        raise PilotValidationError("Pilot summary hash mismatch")
    if not summary["validated_for_capacity"]:
        raise PilotValidationError("Pilot summary is not capacity-valid")
    if summary["counts"]["unresolved_unit_count"]:
        raise PilotValidationError("Pilot summary contains unresolved units")
