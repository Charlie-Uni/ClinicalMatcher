import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from .decomposition_benchmark import (
    PROTOCOL_VERSION,
    normalize_detection_text,
    validate_decomposition_selection_document,
)
from .validation import validate_document


CONCEPT_CATALOG_VERSION = "1.0.0"
ANNOTATION_VERSION = "1.0.0"
CONCEPT_CATALOG_SCHEMA = (
    "schemas/decomposition-concept-catalog-1.0.0.schema.json"
)
ANNOTATION_SCHEMA = "schemas/decomposition-annotation-1.0.0.schema.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DecompositionAnnotationError(ValueError):
    """Raised when public decomposition annotations break their contract."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _self_hash(document: Dict[str, Any], id_field: str, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(id_field, None)
    payload.pop(hash_field, None)
    return _canonical_hash(payload)


def _selected_records(
    selection: Dict[str, Any], split: str
) -> List[Dict[str, Any]]:
    records = [
        record
        for record in selection["records"]
        if record["selected"] and record["assigned_split"] == split
    ]
    if len(records) != 40:
        raise DecompositionAnnotationError(
            f"Selection must contain exactly 40 selected {split} records"
        )
    return sorted(records, key=lambda item: (item["nct_id"], item["criterion_id"]))


def finalize_concept_catalog(
    selection: Dict[str, Any],
    draft: Dict[str, Any],
) -> Dict[str, Any]:
    """Bind an owner-authored split catalog to its frozen selection."""
    validate_decomposition_selection_document(selection)
    document = dict(draft)
    document.pop("concept_catalog_id", None)
    document.pop("concept_catalog_sha256", None)
    document["concept_catalog_version"] = CONCEPT_CATALOG_VERSION
    document["protocol_version"] = PROTOCOL_VERSION
    document["selection_manifest_id"] = selection["selection_manifest_id"]
    document["selection_manifest_sha256"] = selection[
        "selection_manifest_sha256"
    ]
    split = document.get("split")
    if split not in {"dev", "test"}:
        raise DecompositionAnnotationError("Catalog split must be dev or test")
    digest = _self_hash(
        document,
        "concept_catalog_id",
        "concept_catalog_sha256",
    )
    document["concept_catalog_id"] = (
        f"decomposition-catalog-{split}-{digest[:16]}"
    )
    document["concept_catalog_sha256"] = digest
    validate_concept_catalog(selection, document)
    return document


def validate_concept_catalog(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
) -> None:
    validate_decomposition_selection_document(selection)
    validate_document(catalog, CONCEPT_CATALOG_SCHEMA)
    expected_hash = _self_hash(
        catalog,
        "concept_catalog_id",
        "concept_catalog_sha256",
    )
    if catalog["concept_catalog_sha256"] != expected_hash:
        raise DecompositionAnnotationError("Concept catalog hash mismatch")
    expected_id = f"decomposition-catalog-{catalog['split']}-{expected_hash[:16]}"
    if catalog["concept_catalog_id"] != expected_id:
        raise DecompositionAnnotationError("Concept catalog ID mismatch")
    if (
        catalog["selection_manifest_id"] != selection["selection_manifest_id"]
        or catalog["selection_manifest_sha256"]
        != selection["selection_manifest_sha256"]
    ):
        raise DecompositionAnnotationError(
            "Concept catalog references another selection manifest"
        )
    field_ids = [entry["field_id"] for entry in catalog["entries"]]
    if len(field_ids) != len(set(field_ids)):
        raise DecompositionAnnotationError("Concept field IDs must be unique")
    normalized_aliases: Dict[str, str] = {}
    source_corpus = "\n".join(
        normalize_detection_text(record["source_text"])
        for record in _selected_records(selection, catalog["split"])
    )
    for entry in catalog["entries"]:
        for alias in entry["aliases"]:
            normalized = normalize_detection_text(alias)
            existing = normalized_aliases.get(normalized)
            if existing is not None:
                raise DecompositionAnnotationError(
                    "Catalog aliases must be unique after normalization: "
                    f"{alias!r} already belongs to {existing}"
                )
            normalized_aliases[normalized] = entry["field_id"]
            if normalized not in source_corpus:
                raise DecompositionAnnotationError(
                    f"Catalog alias is not grounded in {catalog['split']} "
                    f"source text: {alias!r}"
                )


def _annotation_items(
    selection: Dict[str, Any], split: str
) -> List[Dict[str, Any]]:
    keys = (
        "nct_id",
        "criterion_id",
        "criterion_type",
        "source_id",
        "source_record_version",
        "protocol_sha256",
        "eligibility_sha256",
        "source_span",
        "source_text",
    )
    return [
        {**{key: record[key] for key in keys}, "expression": None}
        for record in _selected_records(selection, split)
    ]


def _rehash_annotation(document: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(document)
    result.pop("annotation_id", None)
    result.pop("annotation_sha256", None)
    digest = _self_hash(result, "annotation_id", "annotation_sha256")
    result["annotation_id"] = (
        f"decomposition-annotation-{result['split']}-{digest[:16]}"
    )
    result["annotation_sha256"] = digest
    return result


def build_annotation_template(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotator_id: str,
    annotation_mode: str,
    annotation_guide_version: str,
    annotation_guide_sha256: str,
) -> Dict[str, Any]:
    validate_decomposition_selection_document(selection)
    validate_concept_catalog(selection, catalog)
    if not annotator_id.strip():
        raise DecompositionAnnotationError("Annotator ID must be non-empty")
    if annotation_mode not in {
        "dual_independent_with_adjudication",
        "single_annotator",
    }:
        raise DecompositionAnnotationError("Unsupported annotation mode")
    if not annotation_guide_version.strip() or not SHA256_PATTERN.fullmatch(
        annotation_guide_sha256
    ):
        raise DecompositionAnnotationError(
            "Annotation guide requires a version and SHA-256"
        )
    document = {
        "decomposition_annotation_version": ANNOTATION_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection[
            "selection_manifest_sha256"
        ],
        "concept_catalog_id": catalog["concept_catalog_id"],
        "concept_catalog_sha256": catalog["concept_catalog_sha256"],
        "annotation_guide_version": annotation_guide_version,
        "annotation_guide_sha256": annotation_guide_sha256,
        "split": catalog["split"],
        "annotator_id": annotator_id.strip(),
        "annotation_mode": annotation_mode,
        "annotation_status": "draft",
        "independence_attestation": {
            "other_annotations_not_viewed": False,
            "model_outputs_not_viewed": False,
        },
        "items": _annotation_items(selection, catalog["split"]),
    }
    document = _rehash_annotation(document)
    validate_annotation(selection, catalog, document, require_completed=False)
    return document


def _walk_atoms(expression: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    if expression["expression_type"] == "atom":
        yield expression["atom"]
        return
    for child in expression["children"]:
        yield from _walk_atoms(child)


def _validate_expression(
    expression: Dict[str, Any],
    source_id: str,
    source_text: str,
    allowed_fields: Set[str],
    seen_condition_ids: Set[str],
) -> None:
    for atom in _walk_atoms(expression):
        condition_id = atom["condition_id"]
        if condition_id in seen_condition_ids:
            raise DecompositionAnnotationError(
                f"Duplicate condition ID: {condition_id}"
            )
        seen_condition_ids.add(condition_id)
        if atom["field"] not in allowed_fields:
            raise DecompositionAnnotationError(
                f"Atom field is absent from the frozen catalog: {atom['field']}"
            )
        provenance = atom["provenance"]
        if provenance["source_id"] != source_id:
            raise DecompositionAnnotationError(
                "Atom provenance references another criterion source"
            )
        span = provenance["source_span"]
        if span["end"] > len(source_text) or not source_text[
            span["start"] : span["end"]
        ].strip():
            raise DecompositionAnnotationError(
                f"Atom source span is empty or outside source text: {condition_id}"
            )
        value_type = atom["expected"]["value_type"]
        value = atom["expected"]["value"]
        if value_type == "number" and not math.isfinite(value):
            raise DecompositionAnnotationError(
                f"Numeric atom value must be finite: {condition_id}"
            )
        if value_type in {"boolean", "string"} and atom["operator"] not in {
            "==",
            "!=",
        }:
            raise DecompositionAnnotationError(
                f"{value_type} atom requires == or !=: {condition_id}"
            )


def validate_annotation(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    annotation: Dict[str, Any],
    require_completed: bool = True,
) -> None:
    validate_decomposition_selection_document(selection)
    validate_concept_catalog(selection, catalog)
    validate_document(annotation, ANNOTATION_SCHEMA)
    expected_hash = _self_hash(
        annotation,
        "annotation_id",
        "annotation_sha256",
    )
    if annotation["annotation_sha256"] != expected_hash:
        raise DecompositionAnnotationError("Annotation hash mismatch")
    expected_id = (
        f"decomposition-annotation-{annotation['split']}-{expected_hash[:16]}"
    )
    if annotation["annotation_id"] != expected_id:
        raise DecompositionAnnotationError("Annotation ID mismatch")
    if (
        annotation["selection_manifest_id"] != selection["selection_manifest_id"]
        or annotation["selection_manifest_sha256"]
        != selection["selection_manifest_sha256"]
    ):
        raise DecompositionAnnotationError(
            "Annotation references another selection manifest"
        )
    if (
        annotation["concept_catalog_id"] != catalog["concept_catalog_id"]
        or annotation["concept_catalog_sha256"]
        != catalog["concept_catalog_sha256"]
        or annotation["split"] != catalog["split"]
    ):
        raise DecompositionAnnotationError(
            "Annotation references another concept catalog or split"
        )

    completed = annotation["annotation_status"] == "completed"
    if require_completed and not completed:
        raise DecompositionAnnotationError("Annotation is not completed")
    if completed and not all(annotation["independence_attestation"].values()):
        raise DecompositionAnnotationError(
            "Completed annotation requires both independence attestations"
        )

    expected_records = {
        (record["nct_id"], record["criterion_id"]): record
        for record in _selected_records(selection, annotation["split"])
    }
    actual_keys = [
        (item["nct_id"], item["criterion_id"])
        for item in annotation["items"]
    ]
    if len(actual_keys) != len(set(actual_keys)) or set(actual_keys) != set(
        expected_records
    ):
        raise DecompositionAnnotationError(
            "Annotation must cover each selected split criterion exactly once"
        )

    allowed_fields = {entry["field_id"] for entry in catalog["entries"]}
    seen_condition_ids: Set[str] = set()
    identity_fields = (
        "nct_id",
        "criterion_id",
        "criterion_type",
        "source_id",
        "source_record_version",
        "protocol_sha256",
        "eligibility_sha256",
        "source_span",
        "source_text",
    )
    for item in annotation["items"]:
        expected = expected_records[(item["nct_id"], item["criterion_id"])]
        if any(item[field] != expected[field] for field in identity_fields):
            raise DecompositionAnnotationError(
                f"Criterion source identity mismatch: {item['criterion_id']}"
            )
        expression = item["expression"]
        if completed and expression is None:
            raise DecompositionAnnotationError(
                f"Completed annotation is missing a tree: {item['criterion_id']}"
            )
        if expression is not None:
            _validate_expression(
                expression,
                item["source_id"],
                item["source_text"],
                allowed_fields,
                seen_condition_ids,
            )


def finalize_annotation(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    draft: Dict[str, Any],
) -> Dict[str, Any]:
    document = dict(draft)
    document["annotation_status"] = "completed"
    document = _rehash_annotation(document)
    validate_annotation(selection, catalog, document, require_completed=True)
    return document
