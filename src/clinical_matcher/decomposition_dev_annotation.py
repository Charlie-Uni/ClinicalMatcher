"""Manual, model-free workflow for the frozen public dev annotation package."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from jsonschema import Draft202012Validator

from .decomposition_annotation import (
    DecompositionAnnotationError,
    _validate_condition_id_order,
    _validate_expression,
)
from .validation import validate_document


WORK_VERSION = "1.0.0"
WORK_SCHEMA = "schemas/decomposition-dev-annotation-work-1.0.0.schema.json"
EXPRESSION_SCHEMA = "schemas/decomposition-annotation-1.0.0.schema.json"


class DecompositionDevAnnotationError(ValueError):
    """Raised when manual dev annotation work violates the frozen inputs."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _self_hash(document: Mapping[str, Any]) -> str:
    payload = dict(document)
    payload.pop("work_id", None)
    payload.pop("work_sha256", None)
    return _canonical_hash(payload)


def _rehash(document: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(document))
    result.pop("work_id", None)
    result.pop("work_sha256", None)
    digest = _self_hash(result)
    result["work_id"] = f"decomposition-dev-work-{digest[:16]}"
    result["work_sha256"] = digest
    return result


def _expression_validator() -> Draft202012Validator:
    schema = json.loads(
        files("clinical_matcher")
        .joinpath(EXPRESSION_SCHEMA)
        .read_text(encoding="utf-8")
    )
    expression_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/expression",
        "$defs": schema["$defs"],
    }
    return Draft202012Validator(expression_schema)


def _package_items(package: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {item["criterion_id"]: item for item in package["items"]}


def _catalog_fields(catalog: Mapping[str, Any]) -> set[str]:
    return {entry["field_id"] for entry in catalog["entries"]}


def validate_work(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    *,
    require_completed: bool = False,
) -> None:
    """Validate bindings and every human-supplied expression without repair."""
    validate_document(work, WORK_SCHEMA)
    digest = _self_hash(work)
    if work["work_sha256"] != digest or work["work_id"] != (
        f"decomposition-dev-work-{digest[:16]}"
    ):
        raise DecompositionDevAnnotationError("Annotation work identity mismatch")
    if work["source_package_id"] != package["package_id"] or work[
        "source_package_sha256"
    ] != package["package_sha256"]:
        raise DecompositionDevAnnotationError("Annotation work package binding mismatch")
    if package["concept_catalog_id"] != catalog["concept_catalog_id"] or package[
        "concept_catalog_sha256"
    ] != catalog["concept_catalog_sha256"]:
        raise DecompositionDevAnnotationError("Package and catalog bindings disagree")

    expected = _package_items(package)
    observed_ids = [item["criterion_id"] for item in work["items"]]
    if observed_ids != list(expected):
        raise DecompositionDevAnnotationError(
            "Annotation items must preserve the frozen package order and membership"
        )
    if len(observed_ids) != len(set(observed_ids)):
        raise DecompositionDevAnnotationError("Duplicate annotation criterion ID")

    completed = work["status"] == "completed"
    if require_completed and not completed:
        raise DecompositionDevAnnotationError("Annotation work is not completed")
    if completed and not work["completion_attestation"][
        "human_authored_without_model_output"
    ]:
        raise DecompositionDevAnnotationError(
            "Completed annotation requires the explicit human-authorship attestation"
        )

    allowed_fields = _catalog_fields(catalog)
    seen_condition_ids: set[str] = set()
    expression_validator = _expression_validator()
    for item in work["items"]:
        criterion_id = item["criterion_id"]
        expression = item["expression"]
        if completed and expression is None:
            raise DecompositionDevAnnotationError(
                f"Completed annotation is missing a tree: {criterion_id}"
            )
        if expression is None:
            continue
        errors = sorted(
            expression_validator.iter_errors(expression),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            locations = []
            for error in errors:
                path = ".".join(str(part) for part in error.absolute_path)
                locations.append(f"{path or '<root>'}: {error.message}")
            raise DecompositionDevAnnotationError(
                f"Invalid expression for {criterion_id}: " + "; ".join(locations)
            )
        source = expected[criterion_id]
        try:
            _validate_expression(
                expression,
                source["source_id"],
                source["source_text"],
                allowed_fields,
                seen_condition_ids,
            )
            _validate_condition_id_order(expression, criterion_id)
        except DecompositionAnnotationError as error:
            raise DecompositionDevAnnotationError(str(error)) from error


def start_work(package: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    if package["status"] != "draft_unannotated":
        raise DecompositionDevAnnotationError("Source package is not unannotated")
    document = {
        "annotation_work_version": WORK_VERSION,
        "status": "draft",
        "source_package_id": package["package_id"],
        "source_package_sha256": package["package_sha256"],
        "annotator_id": "owner",
        "annotation_mode": "single_annotator",
        "completion_attestation": {
            "human_authored_without_model_output": False,
            "test_source_not_inspected": True,
        },
        "items": [
            {"criterion_id": item["criterion_id"], "expression": None}
            for item in package["items"]
        ],
    }
    result = _rehash(document)
    validate_work(package, catalog, result)
    return result


def set_expression(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    criterion_id: str,
    expression: Dict[str, Any] | None,
) -> Dict[str, Any]:
    validate_work(package, catalog, work)
    if work["status"] != "draft":
        raise DecompositionDevAnnotationError("Completed annotation is immutable")
    result = copy.deepcopy(work)
    matches = [item for item in result["items"] if item["criterion_id"] == criterion_id]
    if len(matches) != 1:
        raise DecompositionDevAnnotationError("Unknown or duplicate criterion ID")
    matches[0]["expression"] = copy.deepcopy(expression)
    result = _rehash(result)
    validate_work(package, catalog, result)
    return result


def progress(work: Mapping[str, Any]) -> Dict[str, Any]:
    completed_ids = [
        item["criterion_id"] for item in work["items"] if item["expression"] is not None
    ]
    pending_ids = [
        item["criterion_id"] for item in work["items"] if item["expression"] is None
    ]
    return {
        "status": work["status"],
        "total": len(work["items"]),
        "completed": len(completed_ids),
        "remaining": len(pending_ids),
        "next_criterion_id": pending_ids[0] if pending_ids else None,
    }


def finalize_work(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    *,
    human_authorship_attested: bool,
    test_source_not_inspected_attested: bool,
) -> Dict[str, Any]:
    validate_work(package, catalog, work)
    if not human_authorship_attested:
        raise DecompositionDevAnnotationError(
            "Finalization requires explicit human-authorship attestation"
        )
    if not test_source_not_inspected_attested:
        raise DecompositionDevAnnotationError(
            "Finalization requires explicit locked-test non-inspection attestation"
        )
    result = copy.deepcopy(work)
    result["status"] = "completed"
    result["completion_attestation"]["human_authored_without_model_output"] = True
    result = _rehash(result)
    validate_work(package, catalog, result, require_completed=True)
    return result


def write_new_private_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def replace_private_json(path: Path, document: Mapping[str, Any]) -> None:
    if not path.is_file() or path.is_symlink():
        raise DecompositionDevAnnotationError(
            "Mutable annotation path must be an existing regular file"
        )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DecompositionDevAnnotationError(f"{path} must contain one JSON object")
    return value


def item_view(
    package: Mapping[str, Any], issue_log: Mapping[str, Any], criterion_id: str
) -> Dict[str, Any]:
    items = [item for item in package["items"] if item["criterion_id"] == criterion_id]
    if len(items) != 1:
        raise DecompositionDevAnnotationError("Unknown or duplicate criterion ID")
    issues = [issue for issue in issue_log["issues"] if issue["criterion_id"] == criterion_id]
    return {
        "criterion_id": criterion_id,
        "criterion_type": items[0]["criterion_type"],
        "source_id": items[0]["source_id"],
        "source_text": items[0]["source_text"],
        "resolution_flags": items[0]["resolution_flags"],
        "approved_issue": issues[0] if issues else None,
    }


def catalog_view(catalog: Mapping[str, Any], query: str | None = None) -> Sequence[Dict[str, Any]]:
    needle = (query or "").casefold().strip()
    entries = []
    for entry in catalog["entries"]:
        searchable = " ".join(
            [entry["field_id"], entry["definition"], *entry["aliases"]]
        ).casefold()
        if not needle or needle in searchable:
            entries.append(copy.deepcopy(entry))
    return entries
