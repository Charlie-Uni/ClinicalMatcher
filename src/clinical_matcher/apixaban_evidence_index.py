import json
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import file_sha256
from .apixaban_split import (
    load_apixaban_split_manifest,
    patient_content_sha256,
    validate_apixaban_split_manifest,
    write_private_json,
)
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = (
    "resources/apixaban-evidence-chunk-contract-1.0.0.json"
)
MANIFEST_VERSION = "1.0.0"
MANIFEST_SCHEMA = (
    "schemas/apixaban-evidence-index-manifest-1.0.0.schema.json"
)
SPLIT_NAMES = ("train", "validation", "test")


class ApixabanEvidenceIndexError(ValueError):
    """Raised when the frozen evidence-index input contract is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _manifest_hash(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def load_evidence_chunk_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_evidence_chunk_contract(document)
    return document


def validate_evidence_chunk_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_id",
        "source_corpus_version",
        "input_projection",
        "chunking",
        "index",
        "privacy",
    }
    if set(document) != required:
        raise ApixabanEvidenceIndexError("Evidence chunk contract is incomplete")
    if document["contract_version"] != "1.0.0":
        raise ApixabanEvidenceIndexError("Unsupported evidence chunk contract")
    if document["contract_id"] != "apixaban-preserved-evidence-chunks-v1":
        raise ApixabanEvidenceIndexError("Unexpected evidence chunk contract ID")
    projection = document["input_projection"]
    expected_allowed = {
        "patients[].patient_id",
        "patients[].source_id",
        "patients[].evidence[].evidence_id",
        "patients[].evidence[].source_id",
        "patients[].evidence[].source_span.start",
        "patients[].evidence[].source_span.end",
        "patients[].evidence[].text",
    }
    if set(projection["allowed_fields"]) != expected_allowed:
        raise ApixabanEvidenceIndexError(
            "Evidence input projection changed allowed fields"
        )
    if projection["answer_labels_used"] is not False:
        raise ApixabanEvidenceIndexError("Answer labels are forbidden")
    if projection["queries_used"] is not False:
        raise ApixabanEvidenceIndexError("Queries are forbidden during chunking")
    forbidden = set(projection["forbidden_fields"])
    if forbidden != {
        "patients[].legacy_questions",
        "benchmark.assessments",
        "prediction_sets",
        "test_labels",
    }:
        raise ApixabanEvidenceIndexError("Label fields must be explicitly forbidden")
    chunking = document["chunking"]
    if chunking["strategy"] != "preserve_staging_chunks_without_rechunking":
        raise ApixabanEvidenceIndexError("P3.1 must preserve frozen staging chunks")
    if chunking["text_normalization"] != "none":
        raise ApixabanEvidenceIndexError("Evidence text normalization is forbidden")
    if chunking["overlap_characters"] != 0:
        raise ApixabanEvidenceIndexError("Frozen staging chunks cannot overlap")
    if chunking["ordering"] != ["patient_id", "source_span.start", "evidence_id"]:
        raise ApixabanEvidenceIndexError("Evidence ordering contract changed")
    if chunking["source_span_semantics"] != (
        "zero_based_half_open_exact_characters"
    ):
        raise ApixabanEvidenceIndexError("Source-span semantics changed")
    index = document["index"]
    if index["retrieval_scope"] != "within_patient_only":
        raise ApixabanEvidenceIndexError("Retrieval must be patient isolated")
    if document["privacy"]["public_text_allowed"] is not False:
        raise ApixabanEvidenceIndexError("Restricted evidence cannot be public")


def evidence_index_records(
    staging_corpus: Mapping[str, Any],
    patient_ids: Sequence[str],
) -> Tuple[Dict[str, Any], ...]:
    """Project only evidence fields; legacy answer fields are never accessed."""
    requested = set(patient_ids)
    if not requested:
        raise ApixabanEvidenceIndexError("Index split must contain patients")
    patients = {
        patient["patient_id"]: patient
        for patient in staging_corpus["patients"]
        if patient["patient_id"] in requested
    }
    if set(patients) != requested:
        raise ApixabanEvidenceIndexError("Index split patient membership is incomplete")

    records: List[Dict[str, Any]] = []
    evidence_ids = set()
    source_ids = set()
    declared_maximum = staging_corpus["adapter"][
        "evidence_chunk_max_characters"
    ]
    for patient_id in sorted(patients):
        patient = patients[patient_id]
        token = patient_id[len("patient-"):]
        expected_source_id = f"note-{token}"
        if patient["source_id"] != expected_source_id:
            raise ApixabanEvidenceIndexError("Patient and source HMAC tokens differ")
        if patient["source_id"] in source_ids:
            raise ApixabanEvidenceIndexError("A source cannot belong to two patients")
        source_ids.add(patient["source_id"])
        ordered = sorted(
            patient["evidence"],
            key=lambda item: (
                item["source_span"]["start"],
                item["evidence_id"],
            ),
        )
        if not ordered:
            raise ApixabanEvidenceIndexError("Every patient needs evidence chunks")
        expected_start = 0
        for ordinal, evidence in enumerate(ordered, start=1):
            expected_evidence_id = f"evidence-{token}-{ordinal:03d}"
            if evidence["evidence_id"] != expected_evidence_id:
                raise ApixabanEvidenceIndexError(
                    "Evidence ID is not bound to patient token and span order"
                )
            if evidence["evidence_id"] in evidence_ids:
                raise ApixabanEvidenceIndexError("Evidence IDs must be globally unique")
            evidence_ids.add(evidence["evidence_id"])
            if evidence["source_id"] != patient["source_id"]:
                raise ApixabanEvidenceIndexError(
                    "Evidence source does not match its patient"
                )
            span = evidence["source_span"]
            if span["start"] != expected_start or span["end"] <= span["start"]:
                raise ApixabanEvidenceIndexError(
                    "Evidence spans must start at zero and be contiguous"
                )
            if len(evidence["text"]) != span["end"] - span["start"]:
                raise ApixabanEvidenceIndexError(
                    "Evidence text does not reconstruct its exact source span"
                )
            if len(evidence["text"]) > declared_maximum:
                raise ApixabanEvidenceIndexError(
                    "Evidence chunk exceeds the source-declared maximum"
                )
            records.append(
                {
                    "patient_id": patient_id,
                    "source_id": patient["source_id"],
                    "evidence_id": evidence["evidence_id"],
                    "source_span": {
                        "start": span["start"],
                        "end": span["end"],
                    },
                    "section": None,
                    "text": evidence["text"],
                }
            )
            expected_start = span["end"]
    return tuple(records)


def build_evidence_index_manifest(
    split_manifest: Mapping[str, Any],
    staging_corpus: Mapping[str, Any],
    split_name: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    if split_name not in SPLIT_NAMES:
        raise ApixabanEvidenceIndexError("Unsupported split name")
    validate_apixaban_split_manifest(dict(split_manifest))
    if split_manifest["status"] != "frozen" or not split_manifest["freeze"][
        "test_locked"
    ]:
        raise ApixabanEvidenceIndexError("Evidence indexing requires a frozen split")
    if staging_corpus["apixaban_corpus_version"] != "1.0.0":
        raise ApixabanEvidenceIndexError("Unsupported staging corpus version")
    adapter = staging_corpus["adapter"]
    if (
        adapter["name"] != "mimic-iv-ext-apixaban-csv"
        or adapter["version"] != "1.0.0"
        or not isinstance(adapter["evidence_chunk_max_characters"], int)
        or isinstance(adapter["evidence_chunk_max_characters"], bool)
        or adapter["evidence_chunk_max_characters"] < 256
    ):
        raise ApixabanEvidenceIndexError("Unsupported staging evidence adapter")

    patient_ids = tuple(split_manifest["splits"][split_name]["patient_ids"])
    patients = {
        patient["patient_id"]: patient for patient in staging_corpus["patients"]
    }
    for patient_id in patient_ids:
        if patient_id not in patients:
            raise ApixabanEvidenceIndexError("Split patient is missing from staging")
        expected = split_manifest["splits"][split_name][
            "patient_content_sha256"
        ][patient_id]
        if patient_content_sha256(patients[patient_id]) != expected:
            raise ApixabanEvidenceIndexError("Split patient content hash mismatch")

    contract = load_evidence_chunk_contract()
    records = evidence_index_records(staging_corpus, patient_ids)
    contract_sha256 = canonical_sha256(contract)
    membership_sha256 = canonical_sha256(sorted(patient_ids))
    input_sha256 = canonical_sha256(records)
    ordered_ids_sha256 = canonical_sha256(
        [record["evidence_id"] for record in records]
    )
    index_seed = {
        "chunk_contract_sha256": contract_sha256,
        "split_name": split_name,
        "patient_membership_sha256": membership_sha256,
        "index_input_sha256": input_sha256,
    }
    index_id = f"apixaban-evidence-index-{canonical_sha256(index_seed)[:24]}"
    source_ids = {record["source_id"] for record in records}
    text_lengths = [len(record["text"]) for record in records]
    document: Dict[str, Any] = {
        "evidence_index_manifest_version": MANIFEST_VERSION,
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "source": {
            "staging_corpus_sha256": split_manifest["dataset"][
                "staging_corpus_sha256"
            ],
            "split_manifest_sha256": split_manifest["manifest_sha256"],
            "split_name": split_name,
            "corpus_version": staging_corpus["apixaban_corpus_version"],
            "adapter_name": adapter["name"],
            "adapter_version": adapter["version"],
            "declared_max_chunk_characters": adapter[
                "evidence_chunk_max_characters"
            ],
        },
        "contract": {
            "contract_id": contract["contract_id"],
            "contract_sha256": contract_sha256,
            "answer_labels_used": False,
            "queries_used": False,
        },
        "index": {
            "index_id": index_id,
            "index_input_sha256": input_sha256,
            "ordered_evidence_ids_sha256": ordered_ids_sha256,
            "patient_membership_sha256": membership_sha256,
            "retrieval_scope": "within_patient_only",
            "section_metadata_status": "unavailable_in_staging_corpus_1.0.0",
        },
        "counts": {
            "patient_count": len(patient_ids),
            "source_count": len(source_ids),
            "evidence_chunk_count": len(records),
            "evidence_character_count": sum(text_lengths),
            "maximum_observed_chunk_characters": max(text_lengths),
        },
        "validation": {
            "stable_evidence_ids": True,
            "global_evidence_ids_unique": True,
            "source_ids_match_patient": True,
            "spans_start_at_zero": True,
            "spans_contiguous_non_overlapping": True,
            "text_lengths_match_spans": True,
            "chunks_within_declared_maximum": True,
            "patient_isolation_enforced": True,
            "text_normalization_applied": False,
        },
        "limitations": [
            (
                "The source corpus exposes no reviewed section metadata, so "
                "section is explicitly unavailable rather than inferred."
            ),
            (
                "This manifest freezes index inputs but does not claim "
                "retrieval relevance or downstream effectiveness."
            ),
            (
                "MIMIC-derived chunk text and patient-level index artifacts "
                "remain restricted and local."
            ),
        ],
        "disclosure_note": (
            "Restricted aggregate manifest derived from MIMIC evidence chunks; "
            "keep local unless disclosure is separately approved."
        ),
    }
    document["manifest_sha256"] = _manifest_hash(document)
    validate_evidence_index_manifest(document)
    return document


def validate_evidence_index_manifest(document: Mapping[str, Any]) -> None:
    validate_document(dict(document), MANIFEST_SCHEMA)
    if _manifest_hash(document) != document["manifest_sha256"]:
        raise ApixabanEvidenceIndexError("Evidence index manifest hash mismatch")
    contract = load_evidence_chunk_contract()
    if document["contract"]["contract_sha256"] != canonical_sha256(contract):
        raise ApixabanEvidenceIndexError("Evidence chunk contract hash mismatch")
    index_seed = {
        "chunk_contract_sha256": document["contract"]["contract_sha256"],
        "split_name": document["source"]["split_name"],
        "patient_membership_sha256": document["index"][
            "patient_membership_sha256"
        ],
        "index_input_sha256": document["index"]["index_input_sha256"],
    }
    expected_id = f"apixaban-evidence-index-{canonical_sha256(index_seed)[:24]}"
    if document["index"]["index_id"] != expected_id:
        raise ApixabanEvidenceIndexError("Evidence index ID derivation mismatch")


def build_evidence_index_manifest_from_paths(
    split_path: Path,
    staging_path: Path,
    split_name: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    for path in (split_path, staging_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanEvidenceIndexError(
                f"Restricted index input is not owner-only: {path}"
            )
    split = load_apixaban_split_manifest(split_path)
    if file_sha256(staging_path) != split["dataset"]["staging_corpus_sha256"]:
        raise ApixabanEvidenceIndexError("Staging file hash mismatch")
    staging = json.loads(staging_path.read_text(encoding="utf-8"))
    return build_evidence_index_manifest(
        split,
        staging,
        split_name,
        generated_at=generated_at,
        code_commit=code_commit,
    )


def verify_evidence_index_manifest_from_paths(
    manifest_path: Path,
    split_path: Path,
    staging_path: Path,
) -> Dict[str, Any]:
    for path in (manifest_path, split_path, staging_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanEvidenceIndexError(
                f"Restricted index input is not owner-only: {path}"
            )
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_evidence_index_manifest(existing)
    split = load_apixaban_split_manifest(split_path)
    if file_sha256(staging_path) != split["dataset"]["staging_corpus_sha256"]:
        raise ApixabanEvidenceIndexError("Staging file hash mismatch")
    staging = json.loads(staging_path.read_text(encoding="utf-8"))
    rebuilt = build_evidence_index_manifest(
        split,
        staging,
        existing["source"]["split_name"],
        generated_at=existing["generated_at"],
        code_commit=existing["code_commit"],
    )
    if rebuilt != existing:
        raise ApixabanEvidenceIndexError(
            "Evidence index manifest does not reproduce from frozen inputs"
        )
    return existing


def write_evidence_index_manifest(
    document: Dict[str, Any], output_path: Path
) -> Path:
    validate_evidence_index_manifest(document)
    return write_private_json(document, output_path)
