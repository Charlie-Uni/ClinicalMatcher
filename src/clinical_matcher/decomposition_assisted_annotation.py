"""Versioned LLM-assisted, owner-reviewed decomposition silver workflow."""

from __future__ import annotations

import copy
import hashlib
import json
from importlib.resources import files
from typing import Any, Dict, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

from .decomposition_annotation import (
    DecompositionAnnotationError,
    _validate_condition_id_order,
    _validate_expression,
)
from .validation import load_schema, validate_document


WORK_VERSION = "1.0.0"
WORK_SCHEMA = "schemas/decomposition-dev-assisted-work-1.0.0.schema.json"
DECISION_RESOURCE = "resources/decomposition-llm-assisted-decision-1.1.0.json"
DECISION_SCHEMA = "schemas/decomposition-llm-assisted-decision-1.1.0.schema.json"


class DecompositionAssistedAnnotationError(ValueError):
    """Raised when assisted annotation violates its frozen disclosure contract."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _self_hash(document: Mapping[str, Any], id_field: str, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(id_field, None)
    payload.pop(hash_field, None)
    return _canonical_hash(payload)


def _rehash_work(document: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(document))
    result.pop("work_id", None)
    result.pop("work_sha256", None)
    digest = _self_hash(result, "work_id", "work_sha256")
    result["work_id"] = f"decomposition-dev-assisted-work-{digest[:16]}"
    result["work_sha256"] = digest
    return result


def load_assisted_decision() -> Dict[str, Any]:
    decision = json.loads(
        files("clinical_matcher").joinpath(DECISION_RESOURCE).read_text(encoding="utf-8")
    )
    validate_document(decision, DECISION_SCHEMA)
    expected = _self_hash(decision, "decision_id", "decision_sha256")
    if decision["decision_sha256"] != expected or decision["decision_id"] != (
        f"decomposition-llm-assisted-decision-{expected[:16]}"
    ):
        raise DecompositionAssistedAnnotationError(
            "LLM-assisted decision identity mismatch"
        )
    return decision


def _expression_validator() -> Draft202012Validator:
    core = load_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/expression",
        "$defs": core["$defs"],
    }
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


_EXPRESSION_VALIDATOR = _expression_validator()


def _walk_atoms(expression: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    if expression["expression_type"] == "atom":
        yield expression["atom"]
        return
    for child in expression["children"]:
        yield from _walk_atoms(child)


def _package_items(package: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {item["criterion_id"]: item for item in package["items"]}


def _catalog_fields(catalog: Mapping[str, Any]) -> set[str]:
    return {entry["field_id"] for entry in catalog["entries"]}


def _validate_one_expression(
    expression: Dict[str, Any],
    *,
    criterion_id: str,
    source: Mapping[str, Any],
    allowed_fields: set[str],
    seen_condition_ids: set[str],
    generator: Mapping[str, str],
) -> None:
    errors = sorted(
        _EXPRESSION_VALIDATOR.iter_errors(expression),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        locations = []
        for error in errors:
            path = ".".join(str(part) for part in error.absolute_path)
            locations.append(f"{path or '<root>'}: {error.message}")
        raise DecompositionAssistedAnnotationError(
            f"Invalid assisted expression for {criterion_id}: " + "; ".join(locations)
        )
    for atom in _walk_atoms(expression):
        provenance = atom["provenance"]
        if provenance.get("method") != "llm":
            raise DecompositionAssistedAnnotationError(
                "Assisted draft and reviewed atoms must retain method=llm"
            )
        if provenance.get("model_id") != generator["model_id"] or provenance.get(
            "prompt_version"
        ) != generator["prompt_version"]:
            raise DecompositionAssistedAnnotationError(
                "Assisted atom provenance does not match the frozen draft generator"
            )
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
        raise DecompositionAssistedAnnotationError(str(error)) from error


def validate_assisted_work(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    *,
    require_completed: bool = False,
) -> None:
    validate_document(work, WORK_SCHEMA)
    expected_hash = _self_hash(work, "work_id", "work_sha256")
    if work["work_sha256"] != expected_hash or work["work_id"] != (
        f"decomposition-dev-assisted-work-{expected_hash[:16]}"
    ):
        raise DecompositionAssistedAnnotationError("Assisted work identity mismatch")

    decision = load_assisted_decision()
    if work["decision_id"] != decision["decision_id"] or work[
        "decision_sha256"
    ] != decision["decision_sha256"]:
        raise DecompositionAssistedAnnotationError("Assisted decision binding mismatch")
    if work["draft_generator"] != {
        key: decision["draft_generator"][key]
        for key in ("model_id", "revision_status", "prompt_version")
    }:
        raise DecompositionAssistedAnnotationError("Draft generator binding mismatch")
    if work["source_package_id"] != package["package_id"] or work[
        "source_package_sha256"
    ] != package["package_sha256"]:
        raise DecompositionAssistedAnnotationError("Source package binding mismatch")

    expected = _package_items(package)
    observed_ids = [item["criterion_id"] for item in work["items"]]
    if observed_ids != list(expected) or len(observed_ids) != len(set(observed_ids)):
        raise DecompositionAssistedAnnotationError(
            "Assisted items must preserve the source package order and membership"
        )
    drafted_count = sum(
        item["draft_expression"] is not None for item in work["items"]
    )
    if drafted_count not in {0, len(work["items"])}:
        raise DecompositionAssistedAnnotationError(
            "Assisted work must contain either zero drafts or the complete draft batch"
        )
    if drafted_count == 0 and any(
        item["review_status"] != "pending" for item in work["items"]
    ):
        raise DecompositionAssistedAnnotationError(
            "Owner review cannot exist before the complete draft batch"
        )

    completed = work["status"] == "completed"
    if require_completed and not completed:
        raise DecompositionAssistedAnnotationError("Assisted work is not completed")
    if completed and not (
        work["completion_attestation"]["llm_assistance_disclosed"]
        and work["completion_attestation"]["owner_reviewed_every_item"]
    ):
        raise DecompositionAssistedAnnotationError(
            "Completed assisted work requires disclosure and owner-review attestations"
        )

    allowed_fields = _catalog_fields(catalog)
    seen_draft_ids: set[str] = set()
    seen_reviewed_ids: set[str] = set()
    for item in work["items"]:
        criterion_id = item["criterion_id"]
        source = expected[criterion_id]
        draft = item["draft_expression"]
        reviewed = item["reviewed_expression"]
        status = item["review_status"]
        note = item["owner_review_note"]
        if draft is not None:
            _validate_one_expression(
                draft,
                criterion_id=criterion_id,
                source=source,
                allowed_fields=allowed_fields,
                seen_condition_ids=seen_draft_ids,
                generator=work["draft_generator"],
            )
        if reviewed is not None:
            _validate_one_expression(
                reviewed,
                criterion_id=criterion_id,
                source=source,
                allowed_fields=allowed_fields,
                seen_condition_ids=seen_reviewed_ids,
                generator=work["draft_generator"],
            )
        if status == "pending":
            if reviewed is not None or note is not None:
                raise DecompositionAssistedAnnotationError(
                    "Pending assisted items cannot contain a reviewed expression or note"
                )
        elif status == "accepted_unchanged":
            if draft is None or reviewed != draft or note is not None:
                raise DecompositionAssistedAnnotationError(
                    "accepted_unchanged must copy the non-null draft and omit a note"
                )
        else:
            if draft is None or reviewed is None or reviewed == draft or not note:
                raise DecompositionAssistedAnnotationError(
                    "accepted_with_edits requires a changed expression and non-empty note"
                )
        if completed and status == "pending":
            raise DecompositionAssistedAnnotationError(
                f"Completed assisted work has an unreviewed item: {criterion_id}"
            )


def start_assisted_work(package: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    decision = load_assisted_decision()
    document = {
        "assisted_work_version": WORK_VERSION,
        "status": "draft",
        "source_package_id": package["package_id"],
        "source_package_sha256": package["package_sha256"],
        "decision_id": decision["decision_id"],
        "decision_sha256": decision["decision_sha256"],
        "annotation_mode": decision["annotation_mode"],
        "reference_label": decision["reference_label"],
        "draft_generator": {
            key: decision["draft_generator"][key]
            for key in ("model_id", "revision_status", "prompt_version")
        },
        "completion_attestation": {
            "llm_assistance_disclosed": False,
            "owner_reviewed_every_item": False,
            "independent_gold_claimed": False,
            "grpo_semantic_oracle_claimed": False,
            "test_source_not_inspected": True,
        },
        "items": [
            {
                "criterion_id": item["criterion_id"],
                "draft_expression": None,
                "review_status": "pending",
                "reviewed_expression": None,
                "owner_review_note": None,
            }
            for item in package["items"]
        ],
    }
    result = _rehash_work(document)
    validate_assisted_work(package, catalog, result)
    return result


def _find_item(work: Dict[str, Any], criterion_id: str) -> Dict[str, Any]:
    matches = [item for item in work["items"] if item["criterion_id"] == criterion_id]
    if len(matches) != 1:
        raise DecompositionAssistedAnnotationError("Unknown or duplicate criterion ID")
    return matches[0]


def set_assisted_draft_batch(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    draft_batch: Mapping[str, Any],
) -> Dict[str, Any]:
    validate_assisted_work(package, catalog, work)
    if work["status"] != "draft":
        raise DecompositionAssistedAnnotationError("Completed assisted work is immutable")
    if any(item["draft_expression"] is not None for item in work["items"]):
        raise DecompositionAssistedAnnotationError(
            "The all-or-none LLM draft batch may only be applied to fresh work"
        )
    if (
        not isinstance(draft_batch, Mapping)
        or set(draft_batch) != {"drafts"}
        or not isinstance(draft_batch["drafts"], list)
    ):
        raise DecompositionAssistedAnnotationError(
            "Draft batch must contain exactly one drafts array"
        )
    drafts = draft_batch["drafts"]
    expected_ids = [item["criterion_id"] for item in work["items"]]
    observed_ids: list[str] = []
    for index, draft in enumerate(drafts):
        if not isinstance(draft, dict) or set(draft) != {"criterion_id", "expression"}:
            raise DecompositionAssistedAnnotationError(
                f"Draft batch item {index} must contain criterion_id and expression"
            )
        if not isinstance(draft["criterion_id"], str) or not isinstance(
            draft["expression"], dict
        ):
            raise DecompositionAssistedAnnotationError(
                f"Draft batch item {index} has invalid field types"
            )
        observed_ids.append(draft["criterion_id"])
    if observed_ids != expected_ids or len(observed_ids) != len(set(observed_ids)):
        raise DecompositionAssistedAnnotationError(
            "Draft batch must cover every source item exactly once in package order"
        )

    result = copy.deepcopy(work)
    for item, draft in zip(result["items"], drafts):
        item["draft_expression"] = copy.deepcopy(draft["expression"])
    result = _rehash_work(result)
    validate_assisted_work(package, catalog, result)
    return result


def review_assisted_draft(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    criterion_id: str,
    decision: str,
    *,
    edited_expression: Dict[str, Any] | None = None,
    note: str | None = None,
) -> Dict[str, Any]:
    validate_assisted_work(package, catalog, work)
    if work["status"] != "draft":
        raise DecompositionAssistedAnnotationError("Completed assisted work is immutable")
    if any(item["draft_expression"] is None for item in work["items"]):
        raise DecompositionAssistedAnnotationError(
            "Owner review cannot begin until the complete LLM draft batch is frozen"
        )
    result = copy.deepcopy(work)
    item = _find_item(result, criterion_id)
    if item["draft_expression"] is None:
        raise DecompositionAssistedAnnotationError("Cannot review a missing LLM draft")
    if decision == "accepted_unchanged":
        if edited_expression is not None or note is not None:
            raise DecompositionAssistedAnnotationError(
                "accepted_unchanged does not accept an edited expression or note"
            )
        item["review_status"] = decision
        item["reviewed_expression"] = copy.deepcopy(item["draft_expression"])
        item["owner_review_note"] = None
    elif decision == "accepted_with_edits":
        if edited_expression is None or not (note or "").strip():
            raise DecompositionAssistedAnnotationError(
                "accepted_with_edits requires an expression and non-empty note"
            )
        item["review_status"] = decision
        item["reviewed_expression"] = copy.deepcopy(edited_expression)
        item["owner_review_note"] = note.strip()
    else:
        raise DecompositionAssistedAnnotationError("Unsupported owner review decision")
    result = _rehash_work(result)
    validate_assisted_work(package, catalog, result)
    return result


def assisted_progress(work: Mapping[str, Any]) -> Dict[str, Any]:
    drafted = [item for item in work["items"] if item["draft_expression"] is not None]
    reviewed = [item for item in work["items"] if item["review_status"] != "pending"]
    missing_drafts = [item for item in work["items"] if item["draft_expression"] is None]
    pending_reviews = [
        item
        for item in work["items"]
        if item["draft_expression"] is not None and item["review_status"] == "pending"
    ]
    return {
        "status": work["status"],
        "total": len(work["items"]),
        "drafted": len(drafted),
        "reviewed": len(reviewed),
        "remaining_drafts": len(missing_drafts),
        "remaining_reviews": len(work["items"]) - len(reviewed),
        "next_criterion_id": (
            missing_drafts[0]["criterion_id"]
            if missing_drafts
            else (pending_reviews[0]["criterion_id"] if pending_reviews else None)
        ),
        "next_action": (
            "llm_draft_batch"
            if missing_drafts
            else ("owner_review" if pending_reviews else None)
        ),
    }


def assisted_item_view(
    package: Mapping[str, Any],
    issue_log: Mapping[str, Any],
    work: Mapping[str, Any],
    criterion_id: str,
) -> Dict[str, Any]:
    source_matches = [
        item for item in package["items"] if item["criterion_id"] == criterion_id
    ]
    work_matches = [
        item for item in work["items"] if item["criterion_id"] == criterion_id
    ]
    if len(source_matches) != 1 or len(work_matches) != 1:
        raise DecompositionAssistedAnnotationError("Unknown or duplicate criterion ID")
    issues = [
        issue for issue in issue_log["issues"] if issue["criterion_id"] == criterion_id
    ]
    source = source_matches[0]
    item = work_matches[0]
    return {
        "criterion_id": criterion_id,
        "criterion_type": source["criterion_type"],
        "source_id": source["source_id"],
        "source_text": source["source_text"],
        "resolution_flags": source["resolution_flags"],
        "approved_issue": issues[0] if issues else None,
        "draft_expression": copy.deepcopy(item["draft_expression"]),
        "review_status": item["review_status"],
        "reviewed_expression": copy.deepcopy(item["reviewed_expression"]),
        "owner_review_note": item["owner_review_note"],
    }


def finalize_assisted_work(
    package: Dict[str, Any],
    catalog: Dict[str, Any],
    work: Dict[str, Any],
    *,
    assistance_disclosed: bool,
    every_item_reviewed: bool,
    test_source_not_inspected: bool,
) -> Dict[str, Any]:
    validate_assisted_work(package, catalog, work)
    if not assistance_disclosed or not every_item_reviewed:
        raise DecompositionAssistedAnnotationError(
            "Finalization requires LLM-disclosure and owner-review attestations"
        )
    if not test_source_not_inspected:
        raise DecompositionAssistedAnnotationError(
            "Finalization requires the locked-test non-inspection attestation"
        )
    if any(item["review_status"] == "pending" for item in work["items"]):
        raise DecompositionAssistedAnnotationError(
            "Every assisted item must be owner reviewed before finalization"
        )
    result = copy.deepcopy(work)
    result["status"] = "completed"
    result["completion_attestation"]["llm_assistance_disclosed"] = True
    result["completion_attestation"]["owner_reviewed_every_item"] = True
    result = _rehash_work(result)
    validate_assisted_work(package, catalog, result, require_completed=True)
    return result
