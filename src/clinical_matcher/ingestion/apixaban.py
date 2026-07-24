import csv
import hashlib
import hmac
import json
import math
import os
import re
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..splits import canonical_sha256, current_git_commit
from ..validation import validate_document
from .patients import assert_restricted_local_path


DATASET_ID = "MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions"
DATASET_VERSION = "1.0.0"
LICENSE_ID = "PhysioNet Restricted Health Data License 1.5.0"
OFFICIAL_TERMS_URL = (
    "https://physionet.org/content/"
    "mimic-iv-ext-apixaban-trial/view-dua/1.0.0/"
)
OFFICIAL_SOURCE_SHA256 = (
    "8e8083b0b5e3d038ad912a812be1bb8a53f8a59bc37a4c29d8a420cb4296e267"
)
CORPUS_SCHEMA = "schemas/apixaban-staging-corpus-1.0.0.schema.json"
ID_MAP_SCHEMA = "schemas/apixaban-id-map-1.0.0.schema.json"
MANIFEST_SCHEMA = "schemas/apixaban-import-manifest-1.0.0.schema.json"
EXPECTED_COLUMNS = (
    "text",
    "note_id",
    "hadm_id",
    "criterion",
    "question_type",
    "question",
    "answer",
    "not_specified",
)
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ApixabanImportError(ValueError):
    """Raised when a restricted Apixaban source cannot be imported safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _serialized(document: Dict[str, Any]) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _serialized_sha256(document: Dict[str, Any]) -> str:
    return hashlib.sha256(_serialized(document)).hexdigest()


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def generate_pseudonym_key(path: Path) -> None:
    assert_restricted_local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_file(path, secrets.token_bytes(32))


def _write_private_file(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite restricted file: {path}"
        ) from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_key(path: Path) -> bytes:
    assert_restricted_local_path(path)
    key = path.read_bytes()
    if len(key) < 32:
        raise ApixabanImportError(
            "Pseudonym key must contain at least 32 random bytes"
        )
    return key


def _read_expected_checksum(
    checksum_path: Path,
    source_name: str,
) -> str:
    for line in checksum_path.read_text(
        encoding="utf-8-sig",
    ).splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        checksum, filename = parts
        if filename.lstrip("*").strip() == source_name:
            if not re.fullmatch(r"[0-9a-f]{64}", checksum):
                break
            return checksum
    raise ApixabanImportError(
        f"No valid SHA-256 entry for {source_name} in {checksum_path}"
    )


def _criterion_id(
    label: str,
    question_type: str,
    question: str,
) -> str:
    digest = hashlib.sha256(
        f"{label}\0{question_type}\0{question}".encode("utf-8")
    ).hexdigest()
    return f"apixaban-q-{digest[:16]}"


def _pseudonym(
    key: bytes,
    note_id: str,
    admission_id: str,
) -> str:
    message = (
        f"{DATASET_ID}\0{DATASET_VERSION}\0{note_id}\0{admission_id}"
    ).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:24]


def _split_note(
    text: str,
    max_characters: int,
) -> List[Tuple[int, int]]:
    if max_characters < 256:
        raise ApixabanImportError(
            "evidence chunk size must be at least 256 characters"
        )
    if not text:
        raise ApixabanImportError("Clinical note text cannot be empty")
    spans = []
    start = 0
    while start < len(text):
        end = min(start + max_characters, len(text))
        if end < len(text):
            lower = start + max_characters // 2
            candidates = [
                text.rfind("\n\n", lower, end),
                text.rfind("\n", lower, end),
                text.rfind(" ", lower, end),
            ]
            split = max(candidates)
            if split > start:
                end = split + (2 if text[split: split + 2] == "\n\n" else 1)
        if end <= start:
            end = min(start + max_characters, len(text))
        if text[start:end].strip():
            spans.append((start, end))
        start = end
    if not spans:
        raise ApixabanImportError("Clinical note yielded no evidence chunks")
    return spans


def _normalize_question_type(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized in {"yes", "boolean"}:
        return "boolean"
    if normalized == "numeric":
        return "numeric"
    raise ApixabanImportError(f"Unsupported question_type: {raw!r}")


def _parse_answer(
    answer: str,
    question_type: str,
    not_specified: bool,
) -> Tuple[str, Optional[Any]]:
    stripped = answer.strip()
    if not stripped:
        return (
            ("not_specified" if not_specified else "source_anomaly"),
            None,
        )
    if not_specified:
        raise ApixabanImportError(
            "A non-empty answer cannot also be marked not_specified"
        )
    if question_type == "boolean":
        normalized = stripped.lower()
        if normalized not in {"yes", "no"}:
            raise ApixabanImportError(
                "Boolean answers must be exactly Yes or No"
            )
        return "answered", normalized == "yes"
    try:
        value = float(stripped)
    except ValueError as error:
        raise ApixabanImportError(
            "Numeric answer is not a finite number"
        ) from error
    if not math.isfinite(value):
        raise ApixabanImportError("Numeric answer is not finite")
    return "answered", value


def _validate_corpus_semantics(document: Dict[str, Any]) -> None:
    validate_document(document, CORPUS_SCHEMA)
    patient_ids = [item["patient_id"] for item in document["patients"]]
    source_ids = [item["source_id"] for item in document["patients"]]
    if len(patient_ids) != len(set(patient_ids)):
        raise ApixabanImportError("Corpus patient IDs must be unique")
    if len(source_ids) != len(set(source_ids)):
        raise ApixabanImportError("Corpus source IDs must be unique")
    expected_criteria = None
    source_row_numbers = []
    for patient in document["patients"]:
        evidence_ids = [
            item["evidence_id"] for item in patient["evidence"]
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ApixabanImportError("Evidence IDs must be unique per patient")
        criterion_ids = [
            item["criterion_id"]
            for item in patient["legacy_questions"]
        ]
        source_row_numbers.extend(
            item["source_row_number"]
            for item in patient["legacy_questions"]
        )
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ApixabanImportError(
                "Legacy criterion IDs must be unique per patient"
            )
        criterion_set = set(criterion_ids)
        if expected_criteria is None:
            expected_criteria = criterion_set
        elif criterion_set != expected_criteria:
            raise ApixabanImportError(
                "Every note must cover the same legacy criteria"
            )
        previous_end = 0
        for evidence in patient["evidence"]:
            if evidence["source_id"] != patient["source_id"]:
                raise ApixabanImportError(
                    "Evidence source ID does not match its patient note"
                )
            span = evidence["source_span"]
            if span["start"] < previous_end or span["end"] <= span["start"]:
                raise ApixabanImportError(
                    "Evidence spans must be ordered and non-overlapping"
                )
            if len(evidence["text"]) != span["end"] - span["start"]:
                raise ApixabanImportError(
                    "Evidence text length does not match source span"
                )
            previous_end = span["end"]
        for question in patient["legacy_questions"]:
            status = question["answer_status"]
            value = question["answer_value"]
            if status == "answered":
                expected_type = (
                    bool
                    if question["question_type"] == "boolean"
                    else (int, float)
                )
                if not isinstance(value, expected_type) or (
                    question["question_type"] == "numeric"
                    and isinstance(value, bool)
                ):
                    raise ApixabanImportError(
                        "Answered value does not match question type"
                    )
            elif value is not None:
                raise ApixabanImportError(
                    "Unresolved legacy answer must have a null value"
                )
            if question["not_specified"] != (
                status == "not_specified"
            ):
                raise ApixabanImportError(
                    "not_specified flag does not match answer status"
                )
    if len(source_row_numbers) != len(set(source_row_numbers)):
        raise ApixabanImportError("Source row numbers must be unique")


def _validate_id_map_semantics(document: Dict[str, Any]) -> None:
    validate_document(document, ID_MAP_SCHEMA)
    for field in ("patient_id", "source_id", "note_id", "hadm_id"):
        values = [item[field] for item in document["records"]]
        if len(values) != len(set(values)):
            raise ApixabanImportError(
                f"ID map {field} values must be unique"
            )


def build_apixaban_staging_corpus(
    source_csv: Path,
    checksum_path: Path,
    license_path: Path,
    pseudonym_key_path: Path,
    pseudonym_key_id: str,
    terms_url: str,
    evidence_chunk_max_characters: int = 2000,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    for path in (
        source_csv,
        checksum_path,
        license_path,
        pseudonym_key_path,
    ):
        assert_restricted_local_path(path)
    if not KEY_ID_PATTERN.fullmatch(pseudonym_key_id):
        raise ApixabanImportError(
            "pseudonym_key_id must use 1-64 letters, digits, dot, dash, "
            "or underscore"
        )
    key = _read_key(pseudonym_key_path)
    source_sha256 = _raw_sha256(source_csv)
    expected_sha256 = _read_expected_checksum(
        checksum_path,
        source_csv.name,
    )
    if source_sha256 != expected_sha256:
        raise ApixabanImportError(
            "Source CSV does not match the official checksum manifest"
        )
    if (
        required_source_sha256 is not None
        and source_sha256 != required_source_sha256
    ):
        raise ApixabanImportError(
            "Source CSV is not the pinned official dataset version 1.0.0"
        )
    license_text = license_path.read_text(
        encoding="utf-8-sig",
        errors="strict",
    )
    if not re.search(
        r"PhysioNet Restricted Health Data License\s+Version\s+1\.5\.0",
        license_text,
        re.IGNORECASE,
    ):
        raise ApixabanImportError(
            "License file does not identify the required restricted license"
        )

    rows: List[Dict[str, str]] = []
    with source_csv.open(
        "r",
        encoding="utf-8-sig",
        errors="strict",
        newline="",
    ) as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
            raise ApixabanImportError(
                "Source CSV columns do not match the official data dictionary"
            )
        rows = list(reader)
    if not rows:
        raise ApixabanImportError("Source CSV contains no rows")
    if required_source_sha256 is not None and len(rows) != 2300:
        raise ApixabanImportError(
            "Pinned official source must contain exactly 2300 rows"
        )

    grouped: Dict[str, List[Tuple[int, Dict[str, str]]]] = defaultdict(list)
    for row_number, row in enumerate(rows, start=2):
        if not row["note_id"] or not row["hadm_id"] or not row["criterion"]:
            raise ApixabanImportError(
                "Source identifiers and criterion labels cannot be empty"
            )
        grouped[row["note_id"]].append((row_number, row))

    patients = []
    id_records = []
    all_criterion_ids = set()
    anomaly_count = 0
    answered_count = 0
    not_specified_count = 0
    evidence_count = 0
    seen_tokens = set()
    reference_definitions: Optional[Dict[str, Tuple[str, str]]] = None
    for note_id in sorted(grouped):
        source_rows = grouped[note_id]
        admission_ids = {item[1]["hadm_id"] for item in source_rows}
        texts = {item[1]["text"] for item in source_rows}
        if len(admission_ids) != 1 or len(texts) != 1:
            raise ApixabanImportError(
                "Each note must map to one admission and one stable text"
            )
        admission_id = next(iter(admission_ids))
        text = next(iter(texts))
        token = _pseudonym(key, note_id, admission_id)
        if token in seen_tokens:
            raise ApixabanImportError("Pseudonym collision detected")
        seen_tokens.add(token)
        patient_id = f"patient-{token}"
        source_id = f"note-{token}"

        evidence = []
        for index, (start, end) in enumerate(
            _split_note(text, evidence_chunk_max_characters),
            start=1,
        ):
            evidence.append(
                {
                    "evidence_id": f"evidence-{token}-{index:03d}",
                    "source_id": source_id,
                    "source_span": {"start": start, "end": end},
                    "text": text[start:end],
                }
            )
        evidence_count += len(evidence)

        questions = []
        seen_criterion_labels = set()
        definitions: Dict[str, Tuple[str, str]] = {}
        for row_number, row in source_rows:
            label = row["criterion"]
            if label in seen_criterion_labels:
                raise ApixabanImportError(
                    "Duplicate note-criterion row in source CSV"
                )
            seen_criterion_labels.add(label)
            question_type = _normalize_question_type(row["question_type"])
            definitions[label] = (question_type, row["question"])
            try:
                not_specified = bool(int(row["not_specified"]))
            except ValueError as error:
                raise ApixabanImportError(
                    "not_specified must be encoded as 0 or 1"
                ) from error
            if row["not_specified"] not in {"0", "1"}:
                raise ApixabanImportError(
                    "not_specified must be encoded as 0 or 1"
                )
            answer_status, answer_value = _parse_answer(
                row["answer"],
                question_type,
                not_specified,
            )
            if answer_status == "answered":
                answered_count += 1
            elif answer_status == "not_specified":
                not_specified_count += 1
            else:
                anomaly_count += 1
            criterion_id = _criterion_id(
                label,
                question_type,
                row["question"],
            )
            all_criterion_ids.add(criterion_id)
            questions.append(
                {
                    "criterion_id": criterion_id,
                    "source_criterion_label": label,
                    "question_type": question_type,
                    "question": row["question"],
                    "answer_status": answer_status,
                    "answer_value": answer_value,
                    "not_specified": not_specified,
                    "source_row_number": row_number,
                }
            )
        if reference_definitions is None:
            reference_definitions = definitions
        elif definitions != reference_definitions:
            raise ApixabanImportError(
                "Question definitions differ across source notes"
            )
        patients.append(
            {
                "patient_id": patient_id,
                "source_id": source_id,
                "index_date": None,
                "index_date_status": "unavailable_in_source",
                "evidence": evidence,
                "legacy_questions": sorted(
                    questions,
                    key=lambda item: item["criterion_id"],
                ),
            }
        )
        id_records.append(
            {
                "patient_id": patient_id,
                "source_id": source_id,
                "note_id": note_id,
                "hadm_id": admission_id,
            }
        )
    if required_source_sha256 is not None and (
        len(patients) != 100 or len(all_criterion_ids) != 23
    ):
        raise ApixabanImportError(
            "Pinned official source must contain 100 notes and 23 criteria"
        )

    corpus: Dict[str, Any] = {
        "apixaban_corpus_version": "1.0.0",
        "source": {
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
            "access_policy": "credentialed",
            "license_id": LICENSE_ID,
            "terms_url": terms_url,
            "source_csv_sha256": source_sha256,
        },
        "adapter": {
            "name": "mimic-iv-ext-apixaban-csv",
            "version": "1.0.0",
            "pseudonymization": "HMAC-SHA256",
            "evidence_chunk_max_characters": evidence_chunk_max_characters,
        },
        "patients": sorted(
            patients,
            key=lambda item: item["patient_id"],
        ),
    }
    id_map: Dict[str, Any] = {
        "apixaban_id_map_version": "1.0.0",
        "source_csv_sha256": source_sha256,
        "pseudonymization": {
            "algorithm": "HMAC-SHA256",
            "key_id": pseudonym_key_id,
        },
        "records": sorted(
            id_records,
            key=lambda item: item["patient_id"],
        ),
    }
    _validate_corpus_semantics(corpus)
    _validate_id_map_semantics(id_map)
    corpus_sha256 = _serialized_sha256(corpus)
    id_map_sha256 = _serialized_sha256(id_map)
    manifest: Dict[str, Any] = {
        "apixaban_import_manifest_version": "1.0.0",
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "source": {
            "dataset_id": DATASET_ID,
            "dataset_version": DATASET_VERSION,
            "source_csv_sha256": source_sha256,
            "checksum_manifest_sha256": _raw_sha256(checksum_path),
            "license_sha256": _raw_sha256(license_path),
            "official_checksum_verified": True,
        },
        "adapter": {
            "name": "mimic-iv-ext-apixaban-csv",
            "version": "1.0.0",
            "evidence_chunk_max_characters": evidence_chunk_max_characters,
        },
        "pseudonymization": {
            "algorithm": "HMAC-SHA256",
            "key_id": pseudonym_key_id,
            "raw_ids_in_corpus": False,
            "raw_ids_in_separate_id_map": True,
        },
        "outputs": {
            "corpus_sha256": corpus_sha256,
            "id_map_sha256": id_map_sha256,
        },
        "counts": {
            "source_row_count": len(rows),
            "patient_count": len(patients),
            "criterion_count": len(all_criterion_ids),
            "evidence_chunk_count": evidence_count,
            "answered_label_count": answered_count,
            "not_specified_label_count": not_specified_count,
            "source_anomaly_label_count": anomaly_count,
            "index_date_unavailable_patient_count": len(patients),
        },
        "quality": {
            "complete_patient_criterion_grid": (
                len(rows) == len(patients) * len(all_criterion_ids)
            ),
            "runtime_patient_source_ready": False,
            "runtime_blocker": (
                "The released extension omits usable note/index dates; "
                "authorized MIMIC metadata must supply them before temporal "
                "eligibility evaluation."
            ),
        },
        "modifications": [
            "Verified the official source CSV SHA-256 before parsing.",
            "Deduplicated repeated note text across criterion rows.",
            "Replaced note and admission identifiers with keyed HMAC IDs.",
            "Stored the raw-ID crosswalk in a separate restricted ID map.",
            "Split exact note text into deterministic non-overlapping spans.",
            "Normalized Yes/No answers to booleans and numeric answers to "
            "finite numbers.",
            "Mapped empty answers to not_specified or source_anomaly without "
            "guessing a clinical value.",
            "Did not invent an index date missing from the released source.",
        ],
        "disclosure_note": (
            "The corpus and ID map contain restricted MIMIC derivatives and "
            "must remain local. This manifest contains aggregate counts and "
            "hashes only, but disclosure still requires governance review."
        ),
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    validate_document(manifest, MANIFEST_SCHEMA)
    return corpus, id_map, manifest


def write_apixaban_staging_corpus(
    corpus: Dict[str, Any],
    id_map: Dict[str, Any],
    manifest: Dict[str, Any],
    output_path: Path,
) -> Tuple[Path, Path, Path]:
    id_map_path = output_path.with_name(
        f"{output_path.stem}.id-map.json"
    )
    manifest_path = output_path.with_name(
        f"{output_path.stem}.import-manifest.json"
    )
    paths = (output_path, id_map_path, manifest_path)
    for path in paths:
        assert_restricted_local_path(path)
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite restricted output: "
            + ", ".join(str(path) for path in existing)
        )
    if _serialized_sha256(corpus) != manifest["outputs"]["corpus_sha256"]:
        raise ApixabanImportError("Corpus hash does not match manifest")
    if _serialized_sha256(id_map) != manifest["outputs"]["id_map_sha256"]:
        raise ApixabanImportError("ID map hash does not match manifest")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_file(output_path, _serialized(corpus))
    _write_private_file(id_map_path, _serialized(id_map))
    _write_private_file(manifest_path, _serialized(manifest))
    if _raw_sha256(output_path) != manifest["outputs"]["corpus_sha256"]:
        raise RuntimeError("Written corpus hash mismatch")
    if _raw_sha256(id_map_path) != manifest["outputs"]["id_map_sha256"]:
        raise RuntimeError("Written ID map hash mismatch")
    return output_path, id_map_path, manifest_path
