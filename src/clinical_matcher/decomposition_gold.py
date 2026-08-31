"""Adjudication and final-gold contracts for public criterion decomposition."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .decomposition_annotation import (
    finalize_annotation,
    validate_annotation,
    validate_concept_catalog,
)
from .decomposition_benchmark import (
    PROTOCOL_VERSION,
    validate_decomposition_selection_document,
)
from .decomposition_evaluation import (
    MATCHING_VERSION,
    NORMALIZATION_VERSION,
    compare_decomposition_expressions,
)
from .validation import validate_document


ADJUDICATION_VERSION = "1.0.0"
GOLD_VERSION = "1.0.0"
ADJUDICATION_SCHEMA = "schemas/decomposition-adjudication-1.0.0.schema.json"
GOLD_SCHEMA = "schemas/decomposition-gold-1.0.0.schema.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DISAGREEMENT_TYPES = (
    "atom_identity",
    "atom_omission_or_addition",
    "field",
    "operator",
    "value_type",
    "value",
    "unit",
    "time_window",
    "fact_selection",
    "polarity",
    "structure",
    "source_span",
)
class DecompositionGoldError(ValueError):
    """Raised when adjudication or public decomposition gold is invalid."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _self_hash(document: Mapping[str, Any], id_field: str, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(id_field, None)
    payload.pop(hash_field, None)
    return _canonical_hash(payload)


def _annotation_reference(annotation: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "annotation_id": annotation["annotation_id"],
        "annotation_sha256": annotation["annotation_sha256"],
        "annotator_id": annotation["annotator_id"],
    }


def _ordered_annotations(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    *,
    expected_count: int,
    expected_mode: str,
) -> Tuple[Dict[str, Any], ...]:
    if len(annotations) != expected_count:
        raise DecompositionGoldError(
            f"Expected exactly {expected_count} source annotation(s)"
        )
    for annotation in annotations:
        validate_annotation(selection, catalog, annotation, require_completed=True)
        if annotation["annotation_mode"] != expected_mode:
            raise DecompositionGoldError(
                f"Source annotation mode must be {expected_mode}"
            )
    annotator_ids = [annotation["annotator_id"] for annotation in annotations]
    annotation_ids = [annotation["annotation_id"] for annotation in annotations]
    if len(set(annotator_ids)) != expected_count:
        raise DecompositionGoldError("Source annotators must be distinct")
    if len(set(annotation_ids)) != expected_count:
        raise DecompositionGoldError("Source annotation artifacts must be distinct")
    first = annotations[0]
    for annotation in annotations[1:]:
        for field in (
            "selection_manifest_id",
            "selection_manifest_sha256",
            "concept_catalog_id",
            "concept_catalog_sha256",
            "annotation_guide_version",
            "annotation_guide_sha256",
            "split",
        ):
            if annotation[field] != first[field]:
                raise DecompositionGoldError(
                    f"Source annotations disagree on frozen binding {field}"
                )
    return tuple(
        sorted(
            annotations,
            key=lambda item: (item["annotator_id"], item["annotation_id"]),
        )
    )


def _item_map(annotation: Mapping[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return {
        (item["nct_id"], item["criterion_id"]): item
        for item in annotation["items"]
    }


def _agreement_records(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    left_items = _item_map(left)
    right_items = _item_map(right)
    if set(left_items) != set(right_items):
        raise DecompositionGoldError("Source annotation criterion sets differ")
    records: List[Dict[str, Any]] = []
    for key in sorted(left_items):
        result = compare_decomposition_expressions(
            left_items[key]["expression"],
            right_items[key]["expression"],
        )
        records.append({"nct_id": key[0], "criterion_id": key[1], **result})
    return records


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _agreement_summary(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    left_atoms = sum(record["left_atoms"] for record in records)
    right_atoms = sum(record["right_atoms"] for record in records)
    matched_atoms = sum(record["matched_atoms"] for record in records)
    precision = _safe_ratio(matched_atoms, left_atoms)
    recall = _safe_ratio(matched_atoms, right_atoms)
    disagreement_counts: Counter[str] = Counter()
    for record in records:
        disagreement_counts.update(record["disagreement_types"])
    criteria = len(records)
    tree_exact = sum(int(record["normalized_tree_exact"]) for record in records)
    topology_exact = sum(
        int(record["operator_topology_exact"]) for record in records
    )
    return {
        "criteria": criteria,
        "normalized_tree_exact_count": tree_exact,
        "normalized_tree_exact_rate": _safe_ratio(tree_exact, criteria),
        "operator_topology_exact_count": topology_exact,
        "operator_topology_exact_rate": _safe_ratio(topology_exact, criteria),
        "left_atoms": left_atoms,
        "right_atoms": right_atoms,
        "matched_atoms": matched_atoms,
        "atom_micro_f1": _safe_ratio(
            2.0 * precision * recall, precision + recall
        ),
        "atom_macro_f1": _safe_ratio(
            sum(record["atom_f1"] for record in records), criteria
        ),
        "span_exact_count": sum(record["span_exact"] for record in records),
        "span_exact_rate": _safe_ratio(
            sum(record["span_exact"] for record in records), matched_atoms
        ),
        "span_mean_iou": _safe_ratio(
            sum(record["span_iou_sum"] for record in records), matched_atoms
        ),
        "equivalence_review_queued_count": sum(
            int(record["equivalence_review_queued"]) for record in records
        ),
        "disagreement_counts": {
            name: disagreement_counts[name] for name in DISAGREEMENT_TYPES
        },
    }


def _rehash_adjudication(document: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(document)
    result.pop("adjudication_id", None)
    result.pop("adjudication_sha256", None)
    digest = _self_hash(result, "adjudication_id", "adjudication_sha256")
    result["adjudication_id"] = (
        f"decomposition-adjudication-{result['split']}-{digest[:16]}"
    )
    result["adjudication_sha256"] = digest
    return result


def build_adjudication_template(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    adjudicator_ids: Sequence[str],
) -> Dict[str, Any]:
    """Create a deterministic disagreement package from two locked annotations."""
    validate_decomposition_selection_document(selection)
    validate_concept_catalog(selection, catalog)
    ordered = _ordered_annotations(
        selection,
        catalog,
        annotations,
        expected_count=2,
        expected_mode="dual_independent_with_adjudication",
    )
    cleaned_adjudicators = [value.strip() for value in adjudicator_ids]
    if (
        any(not value for value in cleaned_adjudicators)
        or len(cleaned_adjudicators) != len(set(cleaned_adjudicators))
    ):
        raise DecompositionGoldError(
            "Adjudicator IDs must be non-empty and unique"
        )
    adjudicators = sorted(cleaned_adjudicators)
    source_annotators = {annotation["annotator_id"] for annotation in ordered}
    if not source_annotators.issubset(adjudicators):
        raise DecompositionGoldError(
            "Both source annotators must participate in consensus adjudication"
        )
    comparisons = _agreement_records(ordered[0], ordered[1])
    left_items = _item_map(ordered[0])
    items = []
    for comparison in comparisons:
        key = (comparison["nct_id"], comparison["criterion_id"])
        disagreements = comparison["disagreement_types"]
        items.append(
            {
                "nct_id": key[0],
                "criterion_id": key[1],
                "resolution_status": (
                    "unresolved" if disagreements else "agreed_without_dispute"
                ),
                "disagreement_types": disagreements,
                "equivalence_review_queued": comparison[
                    "equivalence_review_queued"
                ],
                "equivalence_review_judgment": None,
                "expression": (
                    None
                    if disagreements
                    else copy.deepcopy(left_items[key]["expression"])
                ),
                "rationale": None,
            }
        )
    document = {
        "decomposition_adjudication_version": ADJUDICATION_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "matching_version": MATCHING_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "concept_catalog_id": catalog["concept_catalog_id"],
        "concept_catalog_sha256": catalog["concept_catalog_sha256"],
        "annotation_guide_version": ordered[0]["annotation_guide_version"],
        "annotation_guide_sha256": ordered[0]["annotation_guide_sha256"],
        "split": ordered[0]["split"],
        "annotation_mode": "dual_independent_with_adjudication",
        "source_annotations": [_annotation_reference(item) for item in ordered],
        "adjudicator_ids": adjudicators,
        "adjudication_status": "draft",
        "pre_adjudication_agreement": _agreement_summary(comparisons),
        "items": items,
    }
    document = _rehash_adjudication(document)
    validate_adjudication(
        selection,
        catalog,
        ordered,
        document,
        require_completed=False,
    )
    return document


def _validate_adjudicated_expressions(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    source_annotation: Dict[str, Any],
    adjudication: Mapping[str, Any],
) -> None:
    candidate = copy.deepcopy(source_annotation)
    candidate_items = _item_map(candidate)
    for item in adjudication["items"]:
        if item["expression"] is not None:
            candidate_items[(item["nct_id"], item["criterion_id"])]["expression"] = (
                copy.deepcopy(item["expression"])
            )
    finalize_annotation(selection, catalog, candidate)


def validate_adjudication(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    adjudication: Dict[str, Any],
    *,
    require_completed: bool = True,
) -> None:
    validate_decomposition_selection_document(selection)
    validate_concept_catalog(selection, catalog)
    ordered = _ordered_annotations(
        selection,
        catalog,
        annotations,
        expected_count=2,
        expected_mode="dual_independent_with_adjudication",
    )
    validate_document(adjudication, ADJUDICATION_SCHEMA)
    expected_hash = _self_hash(
        adjudication, "adjudication_id", "adjudication_sha256"
    )
    if adjudication["adjudication_sha256"] != expected_hash:
        raise DecompositionGoldError("Adjudication hash mismatch")
    expected_id = (
        f"decomposition-adjudication-{adjudication['split']}-{expected_hash[:16]}"
    )
    if adjudication["adjudication_id"] != expected_id:
        raise DecompositionGoldError("Adjudication ID mismatch")
    for field, expected in (
        ("selection_manifest_id", selection["selection_manifest_id"]),
        ("selection_manifest_sha256", selection["selection_manifest_sha256"]),
        ("concept_catalog_id", catalog["concept_catalog_id"]),
        ("concept_catalog_sha256", catalog["concept_catalog_sha256"]),
        ("annotation_guide_version", ordered[0]["annotation_guide_version"]),
        ("annotation_guide_sha256", ordered[0]["annotation_guide_sha256"]),
        ("split", ordered[0]["split"]),
    ):
        if adjudication[field] != expected:
            raise DecompositionGoldError(f"Adjudication binding mismatch: {field}")
    expected_references = [_annotation_reference(item) for item in ordered]
    if adjudication["source_annotations"] != expected_references:
        raise DecompositionGoldError("Adjudication source references mismatch")
    if not {item["annotator_id"] for item in ordered}.issubset(
        set(adjudication["adjudicator_ids"])
    ):
        raise DecompositionGoldError("Both source annotators must remain adjudicators")

    comparisons = _agreement_records(ordered[0], ordered[1])
    if adjudication["pre_adjudication_agreement"] != _agreement_summary(comparisons):
        raise DecompositionGoldError("Pre-adjudication agreement was modified")
    comparison_map = {
        (item["nct_id"], item["criterion_id"]): item for item in comparisons
    }
    actual_keys = [
        (item["nct_id"], item["criterion_id"])
        for item in adjudication["items"]
    ]
    if len(actual_keys) != len(set(actual_keys)) or set(actual_keys) != set(
        comparison_map
    ):
        raise DecompositionGoldError(
            "Adjudication must cover every selected criterion exactly once"
        )
    left_items = _item_map(ordered[0])
    unresolved = 0
    completed = adjudication["adjudication_status"] == "completed"
    for item in adjudication["items"]:
        key = (item["nct_id"], item["criterion_id"])
        expected_disagreements = comparison_map[key]["disagreement_types"]
        if item["disagreement_types"] != expected_disagreements:
            raise DecompositionGoldError(
                f"Disagreement types were modified: {item['criterion_id']}"
            )
        expected_equivalence_queue = comparison_map[key][
            "equivalence_review_queued"
        ]
        if item["equivalence_review_queued"] != expected_equivalence_queue:
            raise DecompositionGoldError(
                f"Equivalence-review routing was modified: {item['criterion_id']}"
            )
        if expected_equivalence_queue:
            if completed and item["equivalence_review_judgment"] is None:
                raise DecompositionGoldError(
                    "Completed adjudication requires queued equivalence review"
                )
        elif item["equivalence_review_judgment"] is not None:
            raise DecompositionGoldError(
                "Unqueued item cannot receive an equivalence-review judgment"
            )
        if not expected_disagreements:
            if (
                item["resolution_status"] != "agreed_without_dispute"
                or item["expression"] != left_items[key]["expression"]
                or item["rationale"] is not None
            ):
                raise DecompositionGoldError(
                    "An agreed annotation cannot be changed during adjudication"
                )
            continue
        if item["resolution_status"] == "unresolved":
            unresolved += 1
            if item["expression"] is not None or item["rationale"] is not None:
                raise DecompositionGoldError(
                    "Unresolved disagreement cannot contain a resolution"
                )
        elif item["resolution_status"] == "resolved":
            if item["expression"] is None or not (item["rationale"] or "").strip():
                raise DecompositionGoldError(
                    "Resolved disagreement requires a tree and rationale"
                )
        else:
            raise DecompositionGoldError("Disputed item must be unresolved or resolved")
    if require_completed and not completed:
        raise DecompositionGoldError("Adjudication is not completed")
    if completed and unresolved:
        raise DecompositionGoldError(
            "Completed adjudication cannot contain unresolved items"
        )
    _validate_adjudicated_expressions(selection, catalog, ordered[0], adjudication)


def finalize_adjudication(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    draft: Dict[str, Any],
) -> Dict[str, Any]:
    document = copy.deepcopy(draft)
    document["adjudication_status"] = "completed"
    document = _rehash_adjudication(document)
    validate_adjudication(
        selection, catalog, annotations, document, require_completed=True
    )
    return document


def _rehash_gold(document: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(document)
    result.pop("gold_id", None)
    result.pop("gold_sha256", None)
    digest = _self_hash(result, "gold_id", "gold_sha256")
    result["gold_id"] = f"decomposition-gold-{result['split']}-{digest[:16]}"
    result["gold_sha256"] = digest
    return result


def build_adjudicated_gold(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    adjudication: Dict[str, Any],
) -> Dict[str, Any]:
    ordered = _ordered_annotations(
        selection,
        catalog,
        annotations,
        expected_count=2,
        expected_mode="dual_independent_with_adjudication",
    )
    validate_adjudication(
        selection, catalog, ordered, adjudication, require_completed=True
    )
    document = {
        "decomposition_gold_version": GOLD_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "concept_catalog_id": catalog["concept_catalog_id"],
        "concept_catalog_sha256": catalog["concept_catalog_sha256"],
        "annotation_guide_version": ordered[0]["annotation_guide_version"],
        "annotation_guide_sha256": ordered[0]["annotation_guide_sha256"],
        "split": ordered[0]["split"],
        "annotation_mode": "dual_independent_with_adjudication",
        "gold_label": "adjudicated_gold",
        "source_annotations": [_annotation_reference(item) for item in ordered],
        "adjudication": {
            "adjudication_id": adjudication["adjudication_id"],
            "adjudication_sha256": adjudication["adjudication_sha256"],
        },
        "single_annotator_downgrade": None,
    }
    document = _rehash_gold(document)
    validate_gold(selection, catalog, ordered, document, adjudication=adjudication)
    return document


def build_single_annotator_gold(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotation: Dict[str, Any],
    *,
    downgrade_decision_version: str,
    downgrade_decision_sha256: str,
) -> Dict[str, Any]:
    ordered = _ordered_annotations(
        selection,
        catalog,
        (annotation,),
        expected_count=1,
        expected_mode="single_annotator",
    )
    if not downgrade_decision_version.strip() or not SHA256_PATTERN.fullmatch(
        downgrade_decision_sha256
    ):
        raise DecompositionGoldError(
            "Single-annotator gold requires a pre-annotation downgrade decision"
        )
    document = {
        "decomposition_gold_version": GOLD_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "concept_catalog_id": catalog["concept_catalog_id"],
        "concept_catalog_sha256": catalog["concept_catalog_sha256"],
        "annotation_guide_version": ordered[0]["annotation_guide_version"],
        "annotation_guide_sha256": ordered[0]["annotation_guide_sha256"],
        "split": ordered[0]["split"],
        "annotation_mode": "single_annotator",
        "gold_label": "single_annotator_reference_gold",
        "source_annotations": [_annotation_reference(ordered[0])],
        "adjudication": None,
        "single_annotator_downgrade": {
            "decision_version": downgrade_decision_version.strip(),
            "decision_sha256": downgrade_decision_sha256,
            "approved_before_first_annotation": True,
        },
    }
    document = _rehash_gold(document)
    validate_gold(selection, catalog, ordered, document)
    return document


def validate_gold(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotations: Sequence[Dict[str, Any]],
    gold: Dict[str, Any],
    *,
    adjudication: Optional[Dict[str, Any]] = None,
) -> None:
    validate_decomposition_selection_document(selection)
    validate_concept_catalog(selection, catalog)
    validate_document(gold, GOLD_SCHEMA)
    expected_hash = _self_hash(gold, "gold_id", "gold_sha256")
    if gold["gold_sha256"] != expected_hash:
        raise DecompositionGoldError("Gold hash mismatch")
    expected_id = f"decomposition-gold-{gold['split']}-{expected_hash[:16]}"
    if gold["gold_id"] != expected_id:
        raise DecompositionGoldError("Gold ID mismatch")

    mode = gold["annotation_mode"]
    expected_count = 2 if mode == "dual_independent_with_adjudication" else 1
    ordered = _ordered_annotations(
        selection,
        catalog,
        annotations,
        expected_count=expected_count,
        expected_mode=mode,
    )
    for field, expected in (
        ("selection_manifest_id", selection["selection_manifest_id"]),
        ("selection_manifest_sha256", selection["selection_manifest_sha256"]),
        ("concept_catalog_id", catalog["concept_catalog_id"]),
        ("concept_catalog_sha256", catalog["concept_catalog_sha256"]),
        ("annotation_guide_version", ordered[0]["annotation_guide_version"]),
        ("annotation_guide_sha256", ordered[0]["annotation_guide_sha256"]),
        ("split", ordered[0]["split"]),
    ):
        if gold[field] != expected:
            raise DecompositionGoldError(f"Gold binding mismatch: {field}")
    if gold["source_annotations"] != [
        _annotation_reference(item) for item in ordered
    ]:
        raise DecompositionGoldError("Gold source references mismatch")

    if mode == "dual_independent_with_adjudication":
        if gold["gold_label"] != "adjudicated_gold":
            raise DecompositionGoldError("Dual gold must be labelled adjudicated_gold")
        if gold["single_annotator_downgrade"] is not None:
            raise DecompositionGoldError("Dual gold cannot carry a downgrade decision")
        if adjudication is None:
            raise DecompositionGoldError("Dual gold requires its adjudication record")
        validate_adjudication(
            selection, catalog, ordered, adjudication, require_completed=True
        )
        expected_adjudication = {
            "adjudication_id": adjudication["adjudication_id"],
            "adjudication_sha256": adjudication["adjudication_sha256"],
        }
        if gold["adjudication"] != expected_adjudication:
            raise DecompositionGoldError("Gold adjudication reference mismatch")
    else:
        if gold["gold_label"] != "single_annotator_reference_gold":
            raise DecompositionGoldError(
                "Single-annotator gold must retain its limited-validity label"
            )
        if gold["adjudication"] is not None or adjudication is not None:
            raise DecompositionGoldError(
                "Single-annotator reference gold cannot claim adjudication"
            )
        if gold["single_annotator_downgrade"] is None:
            raise DecompositionGoldError(
                "Single-annotator reference gold requires a downgrade decision"
            )
