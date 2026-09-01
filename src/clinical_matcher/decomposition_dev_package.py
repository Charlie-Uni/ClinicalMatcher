"""Freeze and validate the remediated development annotation inputs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .decomposition_annotation import (
    DecompositionAnnotationError,
    load_concept_catalog_rules,
)
from .decomposition_benchmark import criterion_complexity, normalize_detection_text
from .decomposition_gold import validate_single_annotator_decision
from .decomposition_test_remediation import (
    load_remediation_contract,
    validate_dev_source_snapshot_document,
    validate_remediated_selection_document,
)
from .validation import validate_document


PROTOCOL_VERSION = "decomposition-benchmark-protocol/1.2.0"
CATALOG_VERSION = "1.1.0"
ISSUE_LOG_VERSION = "1.0.0"
PACKAGE_VERSION = "1.0.0"
CATALOG_SCHEMA = "schemas/decomposition-concept-catalog-1.1.0.schema.json"
ISSUE_LOG_SCHEMA = "schemas/decomposition-annotation-issue-log-1.0.0.schema.json"
PACKAGE_SCHEMA = "schemas/decomposition-dev-annotation-package-1.0.0.schema.json"
GUIDE_RESOURCE = "resources/decomposition-annotation-guide-1.1.0.json"
GUIDE_SCHEMA = "schemas/decomposition-annotation-guide-1.1.0.schema.json"
DECISION_RESOURCE = "resources/decomposition-single-annotator-decision-1.0.0.json"
EXPECTED_CATALOG_DRAFT_SHA256 = (
    "4091c7af21315482ca35ee75728691db64d9cd676911adbff478f1cbbf9617c3"
)
EXPECTED_ISSUE_DRAFT_SHA256 = (
    "b05b5e6b126a5f74a61f2e3a310a977cf8d56496562a2c23d7d9873a6b6ee238"
)

APPROVED_RESOLUTIONS: Mapping[str, Tuple[str, Tuple[str, ...]]] = {
    "nct02932007-exclusion-04393439bf6f2032": (
        "Encode the schema-representable structure using shared catalog concepts "
        "and the investigator-assessed interference condition; record unsupported "
        "serious, active, or other modifiers as modifier_loss. No criterion-specific "
        "field may be added.",
        (),
    ),
    "nct02932007-inclusion-9fa662d9d6bd53f7": (
        "Encode the two source-defined operational alternatives as ANY(A, B). Do "
        "not create an additional parent-label atom.",
        (),
    ),
    "nct02932007-inclusion-ce4fe9d7f97194cb": (
        "Do not map planned wear duration to time_window. Encode the representable "
        "monitoring concept and record the planned-duration distinction as known loss.",
        (),
    ),
    "nct06528262-exclusion-08dc731ff1a6ea43": (
        "Encode the explicitly stated 2-3 year interval as ALL of lower-bound >= "
        "730 days and upper-bound <= 1095 days under the frozen 365-day year "
        "approximation. Preserve the source ambiguity in this log; do not choose "
        "one boundary.",
        (),
    ),
    "nct06751459-exclusion-6e586bcff5ca30ea": (
        "Keep the frozen criterion as one prediction unit and encode the six "
        "numbered alternatives in one expression tree; do not split or replace "
        "the selected item.",
        (),
    ),
    "nct06847932-exclusion-bf5951810876b3fd": (
        "Use nearest syntactic attachment as the single-annotator default and "
        "encode pacing rhythm with its nearest clause. Preserve the alternative "
        "attachment in this issue record.",
        (),
    ),
    "nct07430956-inclusion-918c40f64dad790a": (
        "Keep the selected text, encode only the complete condition before the "
        "dangling OR, and attach resolution flag incomplete_source_condition. Do "
        "not replace or complete the source.",
        ("incomplete_source_condition",),
    ),
    "nct07715929-exclusion-6fcd645b1ca00b43": (
        "Keep the immutable source span, emit no atom for the included heading, "
        "and attach resolution flag source_span_contamination. Do not re-cut the span.",
        ("source_span_contamination",),
    ),
}


class DecompositionDevPackageError(ValueError):
    """Raised when a dev-only annotation input breaks its frozen contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _self_hash(document: Mapping[str, Any], id_field: str, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(id_field, None)
    payload.pop(hash_field, None)
    return _canonical_hash(payload)


def _load_packaged_json(resource: str) -> Dict[str, Any]:
    document = json.loads(
        files("clinical_matcher").joinpath(resource).read_text(encoding="utf-8")
    )
    if not isinstance(document, dict):
        raise DecompositionDevPackageError(f"Packaged resource is not an object: {resource}")
    return document


def load_annotation_guide_1_1() -> Dict[str, Any]:
    guide = _load_packaged_json(GUIDE_RESOURCE)
    validate_document(guide, GUIDE_SCHEMA)
    digest = _self_hash(guide, "guide_id", "guide_sha256")
    if guide["guide_sha256"] != digest or guide["guide_id"] != (
        f"decomposition-guide-{digest[:16]}"
    ):
        raise DecompositionDevPackageError("Annotation guide identity mismatch")
    return guide


def _source_snapshot_binding(
    selection: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> Dict[str, str]:
    binding = selection["dev_source_snapshot"]
    expected = {
        "root": binding["root"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
    }
    if binding["snapshot_id"] != snapshot["snapshot_id"] or binding[
        "snapshot_sha256"
    ] != snapshot["snapshot_sha256"]:
        raise DecompositionDevPackageError(
            "Selection and dev-source snapshot bindings disagree"
        )
    return expected


def load_selected_dev_records(
    selection: Dict[str, Any], dev_source_root: Path
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Reconstruct only the selected dev records and verify every frozen hash."""
    contract = load_remediation_contract("1.1.0")
    validate_remediated_selection_document(selection, contract=contract)
    if selection["protocol_version"] != PROTOCOL_VERSION:
        raise DecompositionDevPackageError("Selection protocol version mismatch")
    if selection["dev_source_snapshot"]["root"] != contract["storage"][
        "dev_source_root"
    ]:
        raise DecompositionDevPackageError("Unexpected dev-source root binding")

    manifest_path = dev_source_root / selection["dev_source_snapshot"][
        "manifest_path"
    ]
    snapshot = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_dev_source_snapshot_document(snapshot)
    _source_snapshot_binding(selection, snapshot)
    if snapshot["contract_id"] != contract["contract_id"] or snapshot[
        "contract_sha256"
    ] != contract["contract_sha256"]:
        raise DecompositionDevPackageError("Dev snapshot contract binding mismatch")

    protocol_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    snapshot_trials = {record["nct_id"]: record for record in snapshot["records"]}
    for source_record in snapshot["records"]:
        for relative_path, hash_field in (
            (source_record["protocol_path"], "protocol_file_sha256"),
            (source_record["source_study_path"], "source_study_file_sha256"),
        ):
            path = dev_source_root / relative_path
            if _file_hash(path) != source_record[hash_field]:
                raise DecompositionDevPackageError(
                    f"Dev snapshot file hash mismatch: {relative_path}"
                )
        protocol_path = dev_source_root / source_record["protocol_path"]
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        if _canonical_hash(protocol) != source_record["protocol_sha256"]:
            raise DecompositionDevPackageError("Protocol canonical hash mismatch")
        if protocol["nct_id"] != source_record["nct_id"]:
            raise DecompositionDevPackageError("Protocol NCT ID mismatch")
        if protocol["eligibility_sha256"] != source_record["eligibility_sha256"]:
            raise DecompositionDevPackageError("Eligibility hash binding mismatch")
        source_study = json.loads(
            (dev_source_root / source_record["source_study_path"]).read_text(
                encoding="utf-8"
            )
        )
        if _canonical_hash(source_study) != source_record["source_study_sha256"]:
            raise DecompositionDevPackageError("Source-study canonical hash mismatch")
        for criterion in protocol["criteria"]:
            key = (protocol["nct_id"], criterion["criterion_id"])
            if key in protocol_index:
                raise DecompositionDevPackageError("Duplicate dev criterion identity")
            span = criterion["source_span"]
            if protocol["eligibility_text"][span["start"] : span["end"]] != criterion[
                "source_text"
            ]:
                raise DecompositionDevPackageError(
                    "Criterion span does not reproduce dev source text"
                )
            protocol_index[key] = {
                "nct_id": protocol["nct_id"],
                "criterion_id": criterion["criterion_id"],
                "criterion_type": criterion["criterion_type"],
                "source_id": criterion["source_id"],
                "source_record_version": protocol["source_record_version"],
                "protocol_sha256": source_record["protocol_sha256"],
                "eligibility_sha256": protocol["eligibility_sha256"],
                "source_span": dict(span),
                "source_text": criterion["source_text"],
                "normalized_text": normalize_detection_text(criterion["source_text"]),
                "complexity": criterion_complexity(criterion["source_text"]),
            }

    selected: List[Dict[str, Any]] = []
    seen = set()
    for metadata in selection["dev_records"]:
        key = (metadata["nct_id"], metadata["criterion_id"])
        if key in seen or key not in protocol_index:
            raise DecompositionDevPackageError("Selected dev criterion identity mismatch")
        seen.add(key)
        record = protocol_index[key]
        trial = snapshot_trials[record["nct_id"]]
        expected = {
            "criterion_type": record["criterion_type"],
            "source_record_version": record["source_record_version"],
            "protocol_sha256": record["protocol_sha256"],
            "eligibility_sha256": record["eligibility_sha256"],
            "source_text_sha256": hashlib.sha256(
                record["source_text"].encode("utf-8")
            ).hexdigest(),
            "normalized_text_sha256": hashlib.sha256(
                record["normalized_text"].encode("utf-8")
            ).hexdigest(),
            "source_span_length": record["source_span"]["end"]
            - record["source_span"]["start"],
            "complexity": record["complexity"],
        }
        if any(metadata[field] != value for field, value in expected.items()):
            raise DecompositionDevPackageError(
                f"Selected dev metadata mismatch: {metadata['criterion_id']}"
            )
        if trial["protocol_sha256"] != record["protocol_sha256"]:
            raise DecompositionDevPackageError("Snapshot trial binding mismatch")
        selected.append(record)
    if len(selected) != 40:
        raise DecompositionDevPackageError("Exactly 40 selected dev records are required")
    return sorted(selected, key=lambda item: (item["nct_id"], item["criterion_id"])), snapshot


def _validate_alias_grounding(
    catalog: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> None:
    field_ids = [entry["field_id"] for entry in catalog["entries"]]
    if len(field_ids) != len(set(field_ids)):
        raise DecompositionDevPackageError("Concept field IDs must be unique")
    corpus = "\n".join(record["normalized_text"] for record in records)
    aliases: Dict[str, str] = {}
    for entry in catalog["entries"]:
        for alias in entry["aliases"]:
            normalized = normalize_detection_text(alias)
            if not normalized:
                raise DecompositionDevPackageError("Concept alias normalizes to empty")
            if normalized in aliases:
                raise DecompositionDevPackageError(
                    f"Normalized alias is duplicated: {alias!r}"
                )
            aliases[normalized] = entry["field_id"]
            if normalized not in corpus:
                raise DecompositionDevPackageError(
                    f"Concept alias is not grounded in dev source text: {alias!r}"
                )


def validate_dev_catalog(
    selection: Dict[str, Any], snapshot: Dict[str, Any], records: Sequence[Dict[str, Any]], catalog: Dict[str, Any]
) -> None:
    validate_document(catalog, CATALOG_SCHEMA)
    digest = _self_hash(catalog, "concept_catalog_id", "concept_catalog_sha256")
    if catalog["concept_catalog_sha256"] != digest or catalog[
        "concept_catalog_id"
    ] != f"decomposition-catalog-dev-{digest[:16]}":
        raise DecompositionDevPackageError("Dev catalog identity mismatch")
    if catalog["selection_manifest_id"] != selection["selection_manifest_id"] or catalog[
        "selection_manifest_sha256"
    ] != selection["selection_manifest_sha256"]:
        raise DecompositionDevPackageError("Dev catalog selection binding mismatch")
    if catalog["source_snapshot"] != _source_snapshot_binding(selection, snapshot):
        raise DecompositionDevPackageError("Dev catalog snapshot binding mismatch")
    if catalog["supersedes_draft_sha256"] != EXPECTED_CATALOG_DRAFT_SHA256:
        raise DecompositionDevPackageError("Dev catalog draft provenance mismatch")
    rules = load_concept_catalog_rules()
    if (
        catalog["construction_rules_version"] != rules["rules_version"]
        or catalog["construction_rules_sha256"] != rules["rules_sha256"]
    ):
        raise DecompositionDevPackageError("Dev catalog construction-rules mismatch")
    _validate_alias_grounding(catalog, records)


def finalize_dev_catalog(
    selection: Dict[str, Any], snapshot: Dict[str, Any], records: Sequence[Dict[str, Any]], draft: Dict[str, Any], draft_sha256: str
) -> Dict[str, Any]:
    if draft_sha256 != EXPECTED_CATALOG_DRAFT_SHA256:
        raise DecompositionDevPackageError("Catalog draft content hash changed")
    rules = load_concept_catalog_rules()
    if draft.get("split") != "dev" or draft.get("entries") is None:
        raise DecompositionDevPackageError("Catalog draft is not a dev catalog")
    supplied_rules = (
        draft.get("construction_rules_version"),
        draft.get("construction_rules_sha256"),
    )
    if supplied_rules != (rules["rules_version"], rules["rules_sha256"]):
        raise DecompositionDevPackageError("Catalog draft rules binding changed")
    document = {
        "concept_catalog_version": CATALOG_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "source_snapshot": _source_snapshot_binding(selection, snapshot),
        "split": "dev",
        "construction_rules_version": rules["rules_version"],
        "construction_rules_sha256": rules["rules_sha256"],
        "supersedes_draft_sha256": draft_sha256,
        "entries": draft["entries"],
    }
    digest = _self_hash(document, "concept_catalog_id", "concept_catalog_sha256")
    document["concept_catalog_id"] = f"decomposition-catalog-dev-{digest[:16]}"
    document["concept_catalog_sha256"] = digest
    validate_dev_catalog(selection, snapshot, records, document)
    return document


def validate_issue_log(
    selection: Dict[str, Any], snapshot: Dict[str, Any], records: Sequence[Dict[str, Any]], issue_log: Dict[str, Any]
) -> None:
    validate_document(issue_log, ISSUE_LOG_SCHEMA)
    digest = _self_hash(issue_log, "issue_log_id", "issue_log_sha256")
    if issue_log["issue_log_sha256"] != digest or issue_log[
        "issue_log_id"
    ] != f"decomposition-issue-log-dev-{digest[:16]}":
        raise DecompositionDevPackageError("Dev issue-log identity mismatch")
    if issue_log["selection_manifest_id"] != selection["selection_manifest_id"] or issue_log[
        "selection_manifest_sha256"
    ] != selection["selection_manifest_sha256"]:
        raise DecompositionDevPackageError("Dev issue-log selection binding mismatch")
    if issue_log["source_snapshot"] != _source_snapshot_binding(selection, snapshot):
        raise DecompositionDevPackageError("Dev issue-log snapshot binding mismatch")
    if issue_log["supersedes_draft_sha256"] != EXPECTED_ISSUE_DRAFT_SHA256:
        raise DecompositionDevPackageError("Dev issue-log draft provenance mismatch")
    guide = load_annotation_guide_1_1()
    if (
        issue_log["annotation_guide_version"] != guide["guide_version"]
        or issue_log["annotation_guide_sha256"] != guide["guide_sha256"]
    ):
        raise DecompositionDevPackageError("Dev issue-log guide binding mismatch")
    selected_ids = {record["criterion_id"] for record in records}
    issue_ids = [issue["criterion_id"] for issue in issue_log["issues"]]
    if set(issue_ids) != set(APPROVED_RESOLUTIONS) or len(issue_ids) != len(set(issue_ids)):
        raise DecompositionDevPackageError("Dev issue-log criterion set mismatch")
    if not set(issue_ids).issubset(selected_ids):
        raise DecompositionDevPackageError("Issue log references an unselected criterion")
    for issue in issue_log["issues"]:
        resolution, flags = APPROVED_RESOLUTIONS[issue["criterion_id"]]
        if issue["approved_resolution"] != resolution or tuple(issue["resolution_flags"]) != flags:
            raise DecompositionDevPackageError("Owner-approved issue resolution changed")


def finalize_issue_log(
    selection: Dict[str, Any], snapshot: Dict[str, Any], records: Sequence[Dict[str, Any]], draft: Dict[str, Any], draft_sha256: str
) -> Dict[str, Any]:
    if draft_sha256 != EXPECTED_ISSUE_DRAFT_SHA256:
        raise DecompositionDevPackageError("Issue-log draft content hash changed")
    if draft.get("artifact_status") != "draft_owner_review_required_not_executable":
        raise DecompositionDevPackageError("Unexpected issue-log draft status")
    if draft.get("test_source_inspected") is not False or draft.get(
        "model_output_inspected"
    ) is not False:
        raise DecompositionDevPackageError("Issue-log independence attestations changed")
    draft_ids = [issue.get("criterion_id") for issue in draft.get("issues", [])]
    if set(draft_ids) != set(APPROVED_RESOLUTIONS) or len(draft_ids) != 8:
        raise DecompositionDevPackageError("Issue-log draft criterion set changed")
    guide = load_annotation_guide_1_1()
    issues = []
    for draft_issue in draft["issues"]:
        resolution, flags = APPROVED_RESOLUTIONS[draft_issue["criterion_id"]]
        issues.append(
            {
                "criterion_id": draft_issue["criterion_id"],
                "issue_type": draft_issue["issue_type"],
                "exact_issue": draft_issue["exact_issue"],
                "approved_resolution": resolution,
                "resolution_flags": list(flags),
            }
        )
    document = {
        "issue_log_version": ISSUE_LOG_VERSION,
        "artifact_status": "frozen_owner_approved",
        "protocol_version": PROTOCOL_VERSION,
        "annotation_guide_version": guide["guide_version"],
        "annotation_guide_sha256": guide["guide_sha256"],
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "source_snapshot": _source_snapshot_binding(selection, snapshot),
        "split": "dev",
        "supersedes_draft_sha256": draft_sha256,
        "test_source_inspected": False,
        "model_output_inspected": False,
        "issues": issues,
    }
    digest = _self_hash(document, "issue_log_id", "issue_log_sha256")
    document["issue_log_id"] = f"decomposition-issue-log-dev-{digest[:16]}"
    document["issue_log_sha256"] = digest
    validate_issue_log(selection, snapshot, records, document)
    return document


def validate_dev_package(
    selection: Dict[str, Any], snapshot: Dict[str, Any], records: Sequence[Dict[str, Any]], catalog: Dict[str, Any], issue_log: Dict[str, Any], package: Dict[str, Any]
) -> None:
    validate_document(package, PACKAGE_SCHEMA)
    digest = _self_hash(package, "package_id", "package_sha256")
    if package["package_sha256"] != digest or package[
        "package_id"
    ] != f"decomposition-dev-package-{digest[:16]}":
        raise DecompositionDevPackageError("Dev annotation-package identity mismatch")
    if package["selection_manifest_id"] != selection["selection_manifest_id"] or package[
        "selection_manifest_sha256"
    ] != selection["selection_manifest_sha256"]:
        raise DecompositionDevPackageError("Dev package selection binding mismatch")
    if package["source_snapshot"] != _source_snapshot_binding(selection, snapshot):
        raise DecompositionDevPackageError("Dev package snapshot binding mismatch")
    if package["concept_catalog_id"] != catalog["concept_catalog_id"] or package[
        "concept_catalog_sha256"
    ] != catalog["concept_catalog_sha256"]:
        raise DecompositionDevPackageError("Dev package catalog binding mismatch")
    if package["issue_log_id"] != issue_log["issue_log_id"] or package[
        "issue_log_sha256"
    ] != issue_log["issue_log_sha256"]:
        raise DecompositionDevPackageError("Dev package issue-log binding mismatch")
    guide = load_annotation_guide_1_1()
    if package["annotation_guide_version"] != guide["guide_version"] or package[
        "annotation_guide_sha256"
    ] != guide["guide_sha256"]:
        raise DecompositionDevPackageError("Dev package guide binding mismatch")
    decision = _load_packaged_json(DECISION_RESOURCE)
    validate_single_annotator_decision(decision)
    if package["single_annotator_decision"] != {
        "decision_id": decision["decision_id"],
        "decision_sha256": decision["decision_sha256"],
    }:
        raise DecompositionDevPackageError("Dev package staffing-decision mismatch")
    expected_records = {(record["nct_id"], record["criterion_id"]): record for record in records}
    issue_flags = {
        issue["criterion_id"]: issue["resolution_flags"] for issue in issue_log["issues"]
    }
    observed_keys = []
    for item in package["items"]:
        key = (item["nct_id"], item["criterion_id"])
        observed_keys.append(key)
        source = expected_records.get(key)
        if source is None:
            raise DecompositionDevPackageError("Dev package contains an unselected item")
        for field in (
            "criterion_type", "source_id", "source_record_version",
            "protocol_sha256", "eligibility_sha256", "source_span", "source_text",
        ):
            if item[field] != source[field]:
                raise DecompositionDevPackageError("Dev package source binding mismatch")
        if item["resolution_flags"] != issue_flags.get(item["criterion_id"], []):
            raise DecompositionDevPackageError("Dev package resolution flags mismatch")
        if item["expression"] is not None:
            raise DecompositionDevPackageError("Generated dev package must be unannotated")
    if len(observed_keys) != len(set(observed_keys)) or set(observed_keys) != set(expected_records):
        raise DecompositionDevPackageError("Dev package criterion set mismatch")


def build_dev_package(
    selection: Dict[str, Any], snapshot: Dict[str, Any], records: Sequence[Dict[str, Any]], catalog: Dict[str, Any], issue_log: Dict[str, Any]
) -> Dict[str, Any]:
    guide = load_annotation_guide_1_1()
    decision = _load_packaged_json(DECISION_RESOURCE)
    validate_single_annotator_decision(decision)
    flags = {issue["criterion_id"]: issue["resolution_flags"] for issue in issue_log["issues"]}
    items = []
    for record in records:
        items.append(
            {
                **{key: record[key] for key in (
                    "nct_id", "criterion_id", "criterion_type", "source_id",
                    "source_record_version", "protocol_sha256", "eligibility_sha256",
                    "source_span", "source_text",
                )},
                "resolution_flags": flags.get(record["criterion_id"], []),
                "expression": None,
            }
        )
    document = {
        "package_version": PACKAGE_VERSION,
        "status": "draft_unannotated",
        "protocol_version": PROTOCOL_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "source_snapshot": _source_snapshot_binding(selection, snapshot),
        "concept_catalog_id": catalog["concept_catalog_id"],
        "concept_catalog_sha256": catalog["concept_catalog_sha256"],
        "annotation_guide_version": guide["guide_version"],
        "annotation_guide_sha256": guide["guide_sha256"],
        "issue_log_id": issue_log["issue_log_id"],
        "issue_log_sha256": issue_log["issue_log_sha256"],
        "single_annotator_decision": {
            "decision_id": decision["decision_id"],
            "decision_sha256": decision["decision_sha256"],
        },
        "split": "dev",
        "annotation_mode": "single_annotator",
        "annotator_id": "owner",
        "gold_label": "single_annotator_reference_gold",
        "independence_attestation": {
            "model_outputs_not_viewed": True,
            "test_source_not_inspected": True,
        },
        "items": items,
    }
    digest = _self_hash(document, "package_id", "package_sha256")
    document["package_id"] = f"decomposition-dev-package-{digest[:16]}"
    document["package_sha256"] = digest
    validate_dev_package(selection, snapshot, records, catalog, issue_log, document)
    return document


def _json_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_artifacts_once(outputs: Sequence[Tuple[Path, Mapping[str, Any]]]) -> None:
    if any(path.exists() for path, _ in outputs):
        raise FileExistsError("Refusing to overwrite frozen dev annotation artifacts")
    temporary: List[Tuple[Path, Path]] = []
    created: List[Path] = []
    try:
        for output, document in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
            temp = Path(name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_json_bytes(document))
            temporary.append((temp, output))
        for temp, output in temporary:
            temp.replace(output)
            created.append(output)
    except Exception:
        for output in created:
            output.unlink(missing_ok=True)
        raise
    finally:
        for temp, _ in temporary:
            temp.unlink(missing_ok=True)


def build_all(
    *, selection_path: Path, dev_source_root: Path, catalog_draft_path: Path,
    issue_draft_path: Path, catalog_output: Path, issue_output: Path,
    package_output: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    records, snapshot = load_selected_dev_records(selection, dev_source_root)
    catalog_draft = json.loads(catalog_draft_path.read_text(encoding="utf-8"))
    issue_draft = json.loads(issue_draft_path.read_text(encoding="utf-8"))
    catalog = finalize_dev_catalog(
        selection, snapshot, records, catalog_draft, _file_hash(catalog_draft_path)
    )
    issue_log = finalize_issue_log(
        selection, snapshot, records, issue_draft, _file_hash(issue_draft_path)
    )
    package = build_dev_package(selection, snapshot, records, catalog, issue_log)
    write_artifacts_once(
        ((catalog_output, catalog), (issue_output, issue_log), (package_output, package))
    )
    return catalog, issue_log, package


def verify_all(
    *, selection_path: Path, dev_source_root: Path, catalog_path: Path,
    issue_path: Path, package_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    records, snapshot = load_selected_dev_records(selection, dev_source_root)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    issue_log = json.loads(issue_path.read_text(encoding="utf-8"))
    package = json.loads(package_path.read_text(encoding="utf-8"))
    validate_dev_catalog(selection, snapshot, records, catalog)
    validate_issue_log(selection, snapshot, records, issue_log)
    validate_dev_package(selection, snapshot, records, catalog, issue_log, package)
    return catalog, issue_log, package
