import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .apixaban_contract import (
    load_question_catalog,
    normalize_source_answer,
    validate_fact_assessment,
)
from .ingestion.apixaban import (
    OFFICIAL_SOURCE_SHA256,
    validate_apixaban_import_manifest,
    validate_apixaban_staging_corpus,
)
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


BENCHMARK_VERSION = "1.0.0"
MANIFEST_VERSION = "1.0.0"
BENCHMARK_SCHEMA = "schemas/apixaban-benchmark-1.0.0.schema.json"
MANIFEST_SCHEMA = (
    "schemas/apixaban-benchmark-manifest-1.0.0.schema.json"
)
EXPECTED_OFFICIAL_COUNTS = {
    "patient_count": 100,
    "question_count": 23,
    "assessment_count": 2300,
    "answered_source_count": 2033,
    "not_specified_source_count": 265,
    "source_anomaly_count": 2,
}


class ApixabanBenchmarkError(ValueError):
    """Raised when restricted benchmark provenance or semantics are invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _serialized(document: Dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def serialized_document_sha256(document: Dict[str, Any]) -> str:
    return hashlib.sha256(_serialized(document)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def _assessment_id(patient_id: str, question_id: str) -> str:
    digest = hashlib.sha256(
        f"{patient_id}\0{question_id}".encode("utf-8")
    ).hexdigest()
    return f"apixaban-a-{digest[:24]}"


def validate_apixaban_benchmark(
    document: Dict[str, Any],
    *,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Dict[str, int]:
    validate_document(document, BENCHMARK_SCHEMA)
    catalog = load_question_catalog()
    patient_ids = document["patient_ids"]
    assessments = document["assessments"]
    question_ids = {
        question["question_id"] for question in catalog["questions"]
    }

    if patient_ids != sorted(patient_ids):
        raise ApixabanBenchmarkError("Benchmark patient IDs must be sorted")
    if required_source_sha256 is not None and (
        document["source"]["source_csv_sha256"]
        != required_source_sha256
    ):
        raise ApixabanBenchmarkError(
            "Benchmark does not reference the pinned official source"
        )
    if document["contract"]["question_catalog_sha256"] != catalog[
        "catalog_sha256"
    ]:
        raise ApixabanBenchmarkError("Question catalog hash mismatch")

    assessment_ids = []
    pairs = []
    for assessment in assessments:
        validate_fact_assessment(assessment, catalog)
        if assessment["patient_id"] not in patient_ids:
            raise ApixabanBenchmarkError(
                "Assessment references a patient outside the benchmark"
            )
        if assessment["question_id"] not in question_ids:
            raise ApixabanBenchmarkError(
                "Assessment references a question outside the catalog"
            )
        expected_id = _assessment_id(
            assessment["patient_id"], assessment["question_id"]
        )
        if assessment["assessment_id"] != expected_id:
            raise ApixabanBenchmarkError(
                "Assessment ID is not bound to its patient-question pair"
            )
        assessment_ids.append(assessment["assessment_id"])
        pairs.append((assessment["patient_id"], assessment["question_id"]))

    if assessments != sorted(
        assessments,
        key=lambda item: (item["patient_id"], item["question_id"]),
    ):
        raise ApixabanBenchmarkError("Benchmark assessments must be sorted")
    if len(assessment_ids) != len(set(assessment_ids)):
        raise ApixabanBenchmarkError("Assessment IDs must be unique")
    if len(pairs) != len(set(pairs)):
        raise ApixabanBenchmarkError(
            "Patient-question assessment pairs must be unique"
        )
    expected_pairs = {
        (patient_id, question_id)
        for patient_id in patient_ids
        for question_id in question_ids
    }
    if set(pairs) != expected_pairs:
        raise ApixabanBenchmarkError(
            "Benchmark must contain the complete patient-question grid"
        )

    source_reasons = Counter(
        assessment["abstention_reason"] for assessment in assessments
    )
    type_counts = Counter(
        assessment["question_type"] for assessment in assessments
    )
    fact_counts = Counter(
        assessment["fact_status"] for assessment in assessments
    )
    counts = {
        "patient_count": len(patient_ids),
        "question_count": len(question_ids),
        "assessment_count": len(assessments),
        "boolean_assessment_count": type_counts["boolean"],
        "numeric_assessment_count": type_counts["numeric"],
        "present_count": fact_counts["present"],
        "absent_count": fact_counts["absent"],
        "unknown_count": fact_counts["unknown"],
        "answered_source_count": sum(
            not assessment["abstained"] for assessment in assessments
        ),
        "not_specified_source_count": source_reasons[
            "source_not_specified"
        ],
        "source_anomaly_count": source_reasons["source_anomaly"],
    }
    if required_counts is not None:
        for name, expected in required_counts.items():
            if counts[name] != expected:
                raise ApixabanBenchmarkError(
                    f"Official benchmark {name} must equal {expected}, "
                    f"found {counts[name]}"
                )
    return counts


def validate_apixaban_benchmark_manifest(
    document: Dict[str, Any],
) -> None:
    validate_document(document, MANIFEST_SCHEMA)
    if _manifest_hash(document) != document["manifest_sha256"]:
        raise ApixabanBenchmarkError("Benchmark manifest hash mismatch")


def build_apixaban_benchmark(
    staging_corpus: Dict[str, Any],
    import_manifest: Dict[str, Any],
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    validate_apixaban_staging_corpus(staging_corpus)
    validate_apixaban_import_manifest(import_manifest)
    corpus_sha256 = serialized_document_sha256(staging_corpus)
    if corpus_sha256 != import_manifest["outputs"]["corpus_sha256"]:
        raise ApixabanBenchmarkError(
            "Staging corpus content does not match its import manifest"
        )
    source = staging_corpus["source"]
    if source["source_csv_sha256"] != import_manifest["source"][
        "source_csv_sha256"
    ]:
        raise ApixabanBenchmarkError(
            "Staging corpus and import manifest source hashes differ"
        )
    if not import_manifest["quality"]["complete_patient_criterion_grid"]:
        raise ApixabanBenchmarkError(
            "Import manifest does not attest a complete source grid"
        )
    if required_source_sha256 is not None and (
        source["source_csv_sha256"] != required_source_sha256
    ):
        raise ApixabanBenchmarkError(
            "Staging corpus does not reference the pinned official source"
        )

    catalog = load_question_catalog()
    catalog_by_id = {
        question["question_id"]: question
        for question in catalog["questions"]
    }
    patient_ids = sorted(
        patient["patient_id"] for patient in staging_corpus["patients"]
    )
    assessments = []
    source_status_counts: Counter[str] = Counter()
    for patient in staging_corpus["patients"]:
        for source_answer in patient["legacy_questions"]:
            question_id = source_answer["criterion_id"]
            question = catalog_by_id.get(question_id)
            if question is None:
                raise ApixabanBenchmarkError(
                    "Staging corpus references a question outside catalog 1.0.0"
                )
            if (
                source_answer["source_criterion_label"]
                != question["source_criterion_label"]
                or source_answer["question_type"]
                != question["question_type"]
                or source_answer["question"] != question["source_question"]
            ):
                raise ApixabanBenchmarkError(
                    "Staging question definition differs from catalog 1.0.0"
                )
            source_status_counts[source_answer["answer_status"]] += 1
            assessments.append(
                normalize_source_answer(
                    assessment_id=_assessment_id(
                        patient["patient_id"], question_id
                    ),
                    patient_id=patient["patient_id"],
                    question_id=question_id,
                    answer_status=source_answer["answer_status"],
                    answer_value=source_answer["answer_value"],
                    catalog=catalog,
                )
            )
    assessments.sort(
        key=lambda item: (item["patient_id"], item["question_id"])
    )
    expected_import_counts = {
        "answered": import_manifest["counts"]["answered_label_count"],
        "not_specified": import_manifest["counts"][
            "not_specified_label_count"
        ],
        "source_anomaly": import_manifest["counts"][
            "source_anomaly_label_count"
        ],
    }
    if dict(source_status_counts) != {
        key: value for key, value in expected_import_counts.items() if value
    }:
        raise ApixabanBenchmarkError(
            "Staging label-status counts do not match the import manifest"
        )

    benchmark: Dict[str, Any] = {
        "apixaban_benchmark_version": BENCHMARK_VERSION,
        "source": {
            "dataset_id": source["dataset_id"],
            "dataset_version": source["dataset_version"],
            "source_csv_sha256": source["source_csv_sha256"],
            "staging_corpus_sha256": corpus_sha256,
            "import_manifest_sha256": import_manifest["manifest_sha256"],
        },
        "contract": {
            "question_catalog_version": catalog["catalog_version"],
            "question_catalog_sha256": catalog["catalog_sha256"],
            "fact_assessment_version": "1.0.0",
            "prediction_target": "note_grounded_fact_assessment",
            "patient_text_storage": "external_restricted_staging_corpus",
            "gold_evidence_status": "not_available_in_source",
        },
        "patient_ids": patient_ids,
        "assessments": assessments,
    }
    counts = validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=required_source_sha256,
        required_counts=required_counts,
    )
    benchmark_sha256 = serialized_document_sha256(benchmark)
    manifest: Dict[str, Any] = {
        "apixaban_benchmark_manifest_version": MANIFEST_VERSION,
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "source": {
            **benchmark["source"],
            "question_catalog_sha256": catalog["catalog_sha256"],
        },
        "adapter": {
            "name": "apixaban-staging-to-fact-benchmark",
            "version": "1.0.0",
        },
        "output": {
            "benchmark_sha256": benchmark_sha256,
            "restricted_local_only": True,
        },
        "counts": counts,
        "quality": {
            "complete_patient_question_grid": True,
            "unique_patient_question_pairs": True,
            "source_anomalies_preserved": True,
            "contains_note_text": False,
            "contains_raw_identifiers": False,
            "contains_eligibility_labels": False,
            "gold_evidence_available": False,
        },
        "modifications": [
            "Verified staging-corpus provenance against its import manifest.",
            "Mapped released answers through frozen fact contract 1.0.0.",
            "Preserved not-specified labels and source anomalies as unknown.",
            "Excluded note text, raw identifiers, source row numbers, and "
            "eligibility labels.",
            "Kept gold evidence unavailable because the release does not "
            "provide independent evidence links.",
        ],
        "disclosure_note": (
            "The benchmark contains pseudonymous patient-level labels derived "
            "from restricted MIMIC data and must remain local. This aggregate "
            "manifest contains no patient IDs or text, but public disclosure "
            "still requires governance review."
        ),
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    validate_apixaban_benchmark_manifest(manifest)
    return benchmark, manifest


def build_apixaban_benchmark_from_paths(
    staging_corpus_path: Path,
    import_manifest_path: Path,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for path in (staging_corpus_path, import_manifest_path):
        assert_restricted_local_path(path)
    staging_corpus = json.loads(
        staging_corpus_path.read_text(encoding="utf-8")
    )
    import_manifest = json.loads(
        import_manifest_path.read_text(encoding="utf-8")
    )
    validate_apixaban_import_manifest(import_manifest)
    if file_sha256(staging_corpus_path) != import_manifest["outputs"].get(
        "corpus_sha256"
    ):
        raise ApixabanBenchmarkError(
            "Staging corpus file hash does not match its import manifest"
        )
    return build_apixaban_benchmark(
        staging_corpus, import_manifest, **kwargs
    )


def _write_private_file(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
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


def write_apixaban_benchmark(
    benchmark: Dict[str, Any],
    manifest: Dict[str, Any],
    output_path: Path,
    *,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Tuple[Path, Path]:
    manifest_path = output_path.with_name(
        f"{output_path.stem}.manifest.json"
    )
    for path in (output_path, manifest_path):
        assert_restricted_local_path(path)
    existing = [path for path in (output_path, manifest_path) if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite restricted output: "
            + ", ".join(str(path) for path in existing)
        )
    validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=required_source_sha256,
        required_counts=required_counts,
    )
    validate_apixaban_benchmark_manifest(manifest)
    if serialized_document_sha256(benchmark) != manifest["output"][
        "benchmark_sha256"
    ]:
        raise ApixabanBenchmarkError("Benchmark hash does not match manifest")
    if manifest["source"] != {
        **benchmark["source"],
        "question_catalog_sha256": benchmark["contract"][
            "question_catalog_sha256"
        ],
    }:
        raise ApixabanBenchmarkError(
            "Benchmark provenance does not match manifest"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_file(output_path, _serialized(benchmark))
    _write_private_file(manifest_path, _serialized(manifest))
    if file_sha256(output_path) != manifest["output"]["benchmark_sha256"]:
        raise RuntimeError("Written benchmark hash mismatch")
    return output_path, manifest_path


def verify_apixaban_benchmark_files(
    benchmark_path: Path,
    manifest_path: Path,
    *,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Dict[str, int]:
    for path in (benchmark_path, manifest_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanBenchmarkError(
                f"Restricted benchmark file is not owner-only: {path}"
            )
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    counts = validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=required_source_sha256,
        required_counts=required_counts,
    )
    validate_apixaban_benchmark_manifest(manifest)
    if file_sha256(benchmark_path) != manifest["output"][
        "benchmark_sha256"
    ]:
        raise ApixabanBenchmarkError(
            "Benchmark file hash does not match manifest"
        )
    if counts != manifest["counts"]:
        raise ApixabanBenchmarkError(
            "Benchmark counts do not match aggregate manifest"
        )
    if manifest["source"] != {
        **benchmark["source"],
        "question_catalog_sha256": benchmark["contract"][
            "question_catalog_sha256"
        ],
    }:
        raise ApixabanBenchmarkError(
            "Benchmark provenance does not match aggregate manifest"
        )
    return counts
