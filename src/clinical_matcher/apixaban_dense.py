import hashlib
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .apixaban_contract import load_question_catalog
from .apixaban_deterministic import (
    extract_question_prediction,
    load_deterministic_rule_set,
)
from .apixaban_evaluation import validate_prediction_set
from .apixaban_evidence_index import (
    evidence_index_records,
    verify_evidence_index_manifest_from_paths,
)
from .apixaban_split import load_apixaban_split_manifest, write_private_json
from .ingestion.patients import assert_restricted_local_path
from .retrieval.dense import (
    DenseEncoder,
    DensePatientRetriever,
    MedCPTEncoder,
    serialize_float32_vectors,
)
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = "resources/apixaban-dense-contract-1.0.0.json"
INDEX_SCHEMA = "schemas/apixaban-dense-index-1.0.0.schema.json"
RUN_SCHEMA = "schemas/apixaban-dense-run-1.0.0.schema.json"
INDEX_VERSION = "1.0.0"
RUN_VERSION = "1.0.0"
PREDICTION_SET_VERSION = "1.2.0"
MODEL_ID = "clinicalmatcher-medcpt-dense-deterministic@1.0.0"
PROMPT_VERSION = "not-applicable:source-question-medcpt+reviewed-rules@1.0.0"


class ApixabanDenseError(ValueError):
    """Raised when the frozen MedCPT dense-retrieval contract is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any], field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(
        0,
        min(len(ordered) - 1, int(round((len(ordered) - 1) * probability))),
    )
    return ordered[index]


def load_dense_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_dense_contract(document)
    return document


def validate_dense_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_id",
        "development_splits",
        "test_labels_used",
        "question_catalog_sha256",
        "evidence_chunk_contract_id",
        "license",
        "models",
        "query_input",
        "document_input",
        "representation",
        "similarity",
        "selection",
        "runtime",
        "downstream",
        "limitations",
    }
    if set(document) != required:
        raise ApixabanDenseError("Dense contract is incomplete")
    if document["contract_version"] != "1.0.0":
        raise ApixabanDenseError("Unsupported dense contract")
    if document["contract_id"] != "apixaban-medcpt-dense-v1":
        raise ApixabanDenseError("Unexpected dense contract ID")
    if document["development_splits"] != ["train", "validation"]:
        raise ApixabanDenseError("Dense development splits changed")
    if document["test_labels_used"] is not False:
        raise ApixabanDenseError("Test labels are forbidden")
    if document["question_catalog_sha256"] != load_question_catalog()[
        "catalog_sha256"
    ]:
        raise ApixabanDenseError("Dense question catalog hash mismatch")
    if document["evidence_chunk_contract_id"] != (
        "apixaban-preserved-evidence-chunks-v1"
    ):
        raise ApixabanDenseError("Dense evidence-chunk contract changed")
    if document["license"] != {
        "status": "public_domain_us_government_work",
        "source": "pinned_huggingface_model_license_files",
        "commercial_use_restriction": False,
    }:
        raise ApixabanDenseError("Dense model license contract changed")
    if document["models"] != {
        "query_encoder": {
            "model_id": "ncbi/MedCPT-Query-Encoder",
            "revision": "d83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc",
        },
        "document_encoder": {
            "model_id": "ncbi/MedCPT-Article-Encoder",
            "revision": "d05a736da4bb84ee4057b7f7999485be6ed85465",
        },
    }:
        raise ApixabanDenseError("Dense model identity changed")
    if document["query_input"] != {
        "source": "source_question_only",
        "answer_text_used": False,
        "fact_field_used": False,
        "manual_expansion_used": False,
        "max_length": 64,
        "truncation": True,
        "padding": "batch_longest",
    }:
        raise ApixabanDenseError("Dense query construction contract changed")
    if document["document_input"] != {
        "source": "evidence_text_only",
        "format": "empty_title_plus_evidence_text_pair",
        "empty_title": True,
        "max_length": 512,
        "truncation": True,
        "padding": "batch_longest",
        "text_normalization": "none",
    }:
        raise ApixabanDenseError("Dense document-input contract changed")
    if document["representation"] != {
        "pooling": "last_hidden_state_cls",
        "dtype": "float32",
        "dimension": 768,
        "l2_normalized": False,
    }:
        raise ApixabanDenseError("Dense representation contract changed")
    if document["similarity"] != {
        "function": "exact_dot_product",
        "scope": "within_patient_only",
    }:
        raise ApixabanDenseError("Dense similarity contract changed")
    if document["selection"] != {
        "top_k": 3,
        "tie_break": ["source_span.start", "evidence_id"],
        "top_k_rationale": "same_predeclared_exposure_budget_as_bm25_v1",
    }:
        raise ApixabanDenseError("Dense selection contract changed")
    if document["runtime"] != {
        "transformers_version": "4.43.0",
        "torch_version": "2.2.2",
        "device": "cpu",
        "batch_size": 8,
        "local_files_only": True,
        "trust_remote_code": False,
        "deterministic_algorithms": True,
    }:
        raise ApixabanDenseError("Dense runtime contract changed")
    if document["downstream"] != {
        "extractor": "clinicalmatcher-deterministic-extractor@1.0.0",
        "purpose": "answer_metric_diagnostic_not_evidence_relevance_gold",
    }:
        raise ApixabanDenseError("Dense downstream diagnostic contract changed")
    if document["limitations"] != [
        "trained_on_pubmed_search_logs_not_clinical_notes",
        "empty_title_adapts_article_encoder_to_note_chunks",
        "no_independent_evidence_id_gold",
    ]:
        raise ApixabanDenseError("Dense limitations contract changed")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_dense_index_manifest(
    document: Mapping[str, Any], vector_bytes: Optional[bytes] = None
) -> None:
    validate_document(dict(document), INDEX_SCHEMA)
    if _self_hash(document, "manifest_sha256") != document["manifest_sha256"]:
        raise ApixabanDenseError("Dense index manifest hash mismatch")
    contract = load_dense_contract()
    if document["provenance"]["dense_contract_sha256"] != canonical_sha256(
        contract
    ):
        raise ApixabanDenseError("Dense index contract hash mismatch")
    counts = document["counts"]
    evidence_ids = document["index"]["ordered_evidence_ids"]
    if counts["document_count"] != len(evidence_ids):
        raise ApixabanDenseError("Dense index document count does not reconcile")
    if counts["vector_count"] != counts["document_count"]:
        raise ApixabanDenseError("Dense vector/document counts differ")
    patient_tokens = {
        evidence_id.removeprefix("evidence-").rsplit("-", 1)[0]
        for evidence_id in evidence_ids
    }
    if counts["patient_count"] != len(patient_tokens):
        raise ApixabanDenseError("Dense index patient count does not reconcile")
    expected_bytes = counts["vector_count"] * document["model"]["dimension"] * 4
    if document["index"]["byte_count"] != expected_bytes:
        raise ApixabanDenseError("Dense index byte count does not reconcile")
    index_identity = canonical_sha256(
        {
            "contract_sha256": document["provenance"][
                "dense_contract_sha256"
            ],
            "evidence_index_id": document["provenance"]["evidence_index_id"],
            "vector_file_sha256": document["index"]["vector_file_sha256"],
            "ordered_evidence_ids": evidence_ids,
        }
    )
    if document["index"]["index_id"] != (
        f"apixaban-medcpt-index-{index_identity[:24]}"
    ):
        raise ApixabanDenseError("Dense index identity does not reconcile")
    if vector_bytes is not None:
        if len(vector_bytes) != expected_bytes:
            raise ApixabanDenseError("Dense vector file length mismatch")
        if _sha256_bytes(vector_bytes) != document["index"][
            "vector_file_sha256"
        ]:
            raise ApixabanDenseError("Dense vector file hash mismatch")


def validate_dense_run(
    document: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    validate_document(dict(document), RUN_SCHEMA)
    if _self_hash(document, "run_sha256") != document["run_sha256"]:
        raise ApixabanDenseError("Dense run hash mismatch")
    resolved = dict(catalog or load_question_catalog())
    contract = load_dense_contract()
    provenance = document["provenance"]
    if provenance["dense_contract_sha256"] != canonical_sha256(contract):
        raise ApixabanDenseError("Dense run contract hash mismatch")
    if provenance["question_catalog_sha256"] != resolved["catalog_sha256"]:
        raise ApixabanDenseError("Dense run question catalog mismatch")
    question_hashes = {
        question["question_id"]: canonical_sha256(question["source_question"])
        for question in resolved["questions"]
    }
    seen = set()
    patients = set()
    questions_by_patient: Dict[str, set] = {}
    candidates_by_patient: Dict[str, set] = {}
    selected_count = 0
    candidate_comparisons = 0
    for result in document["results"]:
        key = (result["patient_id"], result["question_id"])
        if key in seen:
            raise ApixabanDenseError("Duplicate patient-question dense retrieval")
        seen.add(key)
        patients.add(result["patient_id"])
        questions_by_patient.setdefault(result["patient_id"], set()).add(
            result["question_id"]
        )
        candidates_by_patient.setdefault(result["patient_id"], set()).add(
            result["candidate_count"]
        )
        if result["query_sha256"] != question_hashes.get(result["question_id"]):
            raise ApixabanDenseError("Dense query is not the frozen source question")
        selected = result["selected_evidence"]
        if [item["rank"] for item in selected] != list(
            range(1, len(selected) + 1)
        ):
            raise ApixabanDenseError("Selected dense ranks must be contiguous")
        if len({item["evidence_id"] for item in selected}) != len(selected):
            raise ApixabanDenseError("Selected dense evidence IDs must be unique")
        if any(not math.isfinite(item["score"]) for item in selected):
            raise ApixabanDenseError("Dense scores must be finite")
        if any(
            left["score"] < right["score"]
            for left, right in zip(selected, selected[1:])
        ):
            raise ApixabanDenseError("Selected dense scores are not ranked")
        patient_token = result["patient_id"].removeprefix("patient-")
        if any(
            not item["evidence_id"].startswith(f"evidence-{patient_token}-")
            for item in selected
        ):
            raise ApixabanDenseError("Dense selected evidence crossed patients")
        selected_count += len(selected)
        candidate_comparisons += result["candidate_count"]
    counts = document["counts"]
    expected_questions = set(question_hashes)
    if len(seen) != counts["query_count"]:
        raise ApixabanDenseError("Dense query count does not reconcile")
    if len(patients) != counts["patient_count"]:
        raise ApixabanDenseError("Dense patient count does not reconcile")
    if len(expected_questions) != counts["question_count"]:
        raise ApixabanDenseError("Dense question count does not reconcile")
    if counts["query_count"] != counts["patient_count"] * counts["question_count"]:
        raise ApixabanDenseError("Dense patient-question grid is incomplete")
    if any(values != expected_questions for values in questions_by_patient.values()):
        raise ApixabanDenseError("Dense patient-question coverage is incomplete")
    if selected_count != counts["selected_document_count"]:
        raise ApixabanDenseError("Dense selected-document count does not reconcile")
    if candidate_comparisons != counts["candidate_document_comparisons"]:
        raise ApixabanDenseError("Dense candidate count does not reconcile")
    if any(len(values) != 1 for values in candidates_by_patient.values()):
        raise ApixabanDenseError("Dense patient candidate counts changed by query")
    document_count = sum(next(iter(values)) for values in candidates_by_patient.values())
    if document_count != counts["document_count"]:
        raise ApixabanDenseError("Dense document count does not reconcile")


def run_dense_baseline(
    frozen_split_path: Path,
    staging_corpus_path: Path,
    evidence_index_manifest_path: Path,
    split_name: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    encoder_factory: Optional[Callable[[Mapping[str, Any]], DenseEncoder]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Dict[str, Any], bytes, Dict[str, Any], Dict[str, Any]]:
    if split_name not in {"train", "validation", "test"}:
        raise ApixabanDenseError("Unsupported split name")
    for path in (
        frozen_split_path,
        staging_corpus_path,
        evidence_index_manifest_path,
    ):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanDenseError(f"Restricted dense input is not owner-only: {path}")
    evidence_manifest = verify_evidence_index_manifest_from_paths(
        evidence_index_manifest_path,
        frozen_split_path,
        staging_corpus_path,
    )
    if evidence_manifest["source"]["split_name"] != split_name:
        raise ApixabanDenseError("Evidence index manifest split mismatch")
    split = load_apixaban_split_manifest(frozen_split_path)
    staging = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    catalog = load_question_catalog()
    contract = load_dense_contract()
    if split["dataset"]["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanDenseError("Frozen split question catalog mismatch")
    patient_ids = tuple(split["splits"][split_name]["patient_ids"])
    records = evidence_index_records(staging, patient_ids)
    record_by_id = {record["evidence_id"]: record for record in records}
    documents_per_patient: Dict[str, int] = {}
    for record in records:
        documents_per_patient[record["patient_id"]] = (
            documents_per_patient.get(record["patient_id"], 0) + 1
        )

    load_started = time.perf_counter()
    encoder = (encoder_factory or MedCPTEncoder)(contract)
    model_load_ms = (time.perf_counter() - load_started) * 1000
    document_started = time.perf_counter()
    document_vectors = encoder.encode_documents(
        [record["text"] for record in records]
    )
    document_encoding_ms = (time.perf_counter() - document_started) * 1000
    dimension = contract["representation"]["dimension"]
    if len(document_vectors) != len(records):
        raise ApixabanDenseError("Dense encoder returned wrong document count")
    if any(
        len(vector) != dimension
        or any(not math.isfinite(float(value)) for value in vector)
        for vector in document_vectors
    ):
        raise ApixabanDenseError("Dense document vectors violate the contract")
    vector_bytes = serialize_float32_vectors(document_vectors)
    vector_sha256 = _sha256_bytes(vector_bytes)
    retriever = DensePatientRetriever(records, document_vectors)
    if set(retriever.patient_ids) != set(patient_ids):
        raise ApixabanDenseError("Dense index patient membership mismatch")

    questions = catalog["questions"]
    query_started = time.perf_counter()
    query_vectors = encoder.encode_queries(
        [question["source_question"] for question in questions]
    )
    query_encoding_ms = (time.perf_counter() - query_started) * 1000
    if len(query_vectors) != len(questions):
        raise ApixabanDenseError("Dense encoder returned wrong query count")
    if any(
        len(vector) != dimension
        or any(not math.isfinite(float(value)) for value in vector)
        for vector in query_vectors
    ):
        raise ApixabanDenseError("Dense query vectors violate the contract")
    query_by_id = {
        question["question_id"]: vector
        for question, vector in zip(questions, query_vectors)
    }

    runtime_commit = code_commit or current_git_commit()
    timestamp = generated_at or _now()
    contract_sha256 = canonical_sha256(contract)
    index_identity = canonical_sha256(
        {
            "contract_sha256": contract_sha256,
            "evidence_index_id": evidence_manifest["index"]["index_id"],
            "vector_file_sha256": vector_sha256,
            "ordered_evidence_ids": [record["evidence_id"] for record in records],
        }
    )
    index_manifest: Dict[str, Any] = {
        "dense_index_version": INDEX_VERSION,
        "manifest_sha256": "pending",
        "generated_at": timestamp,
        "code_commit": runtime_commit,
        "provenance": {
            "benchmark_sha256": split["dataset"]["benchmark_sha256"],
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": split_name,
            "evidence_index_manifest_sha256": evidence_manifest[
                "manifest_sha256"
            ],
            "evidence_index_id": evidence_manifest["index"]["index_id"],
            "dense_contract_sha256": contract_sha256,
        },
        "model": {
            "query_model_id": contract["models"]["query_encoder"]["model_id"],
            "query_revision": contract["models"]["query_encoder"]["revision"],
            "document_model_id": contract["models"]["document_encoder"][
                "model_id"
            ],
            "document_revision": contract["models"]["document_encoder"][
                "revision"
            ],
            "pooling": contract["representation"]["pooling"],
            "dtype": "float32_little_endian",
            "dimension": dimension,
            "l2_normalized": contract["representation"]["l2_normalized"],
            "similarity": contract["similarity"]["function"],
            "device": contract["runtime"]["device"],
        },
        "index": {
            "index_id": f"apixaban-medcpt-index-{index_identity[:24]}",
            "vector_file": "vectors.f32",
            "vector_file_sha256": vector_sha256,
            "byte_count": len(vector_bytes),
            "ordered_evidence_ids": [
                record["evidence_id"] for record in records
            ],
        },
        "counts": {
            "patient_count": len(patient_ids),
            "document_count": len(records),
            "vector_count": len(document_vectors),
        },
        "disclosure_note": (
            "Restricted dense index derived from MIMIC evidence. Keep the "
            "manifest and vectors local; do not publish either artifact."
        ),
    }
    index_manifest["manifest_sha256"] = _self_hash(
        index_manifest, "manifest_sha256"
    )
    validate_dense_index_manifest(index_manifest, vector_bytes)

    rule_set = load_deterministic_rule_set()
    rule_by_label = {
        rule["source_criterion_label"]: rule for rule in rule_set["rules"]
    }
    top_k = contract["selection"]["top_k"]
    results: List[Dict[str, Any]] = []
    predictions: List[Dict[str, Any]] = []
    latencies: List[float] = []
    selected_characters = 0
    candidate_comparisons = 0
    total_queries = len(patient_ids) * len(questions)
    completed = 0
    for patient_id in sorted(patient_ids):
        for question in questions:
            started = time.perf_counter()
            selected = retriever.retrieve_vector(
                patient_id, query_by_id[question["question_id"]], top_k
            )
            latencies.append((time.perf_counter() - started) * 1000)
            candidate_count = documents_per_patient[patient_id]
            candidate_comparisons += candidate_count
            selected_records = [record_by_id[item.evidence_id] for item in selected]
            selected_characters += sum(len(item["text"]) for item in selected_records)
            results.append(
                {
                    "patient_id": patient_id,
                    "question_id": question["question_id"],
                    "query_sha256": canonical_sha256(question["source_question"]),
                    "candidate_count": candidate_count,
                    "selected_evidence": [
                        {
                            "evidence_id": item.evidence_id,
                            "score": item.score,
                            "rank": item.rank,
                        }
                        for item in selected
                    ],
                }
            )
            prediction = extract_question_prediction(
                {"patient_id": patient_id, "evidence": selected_records},
                question,
                rule_by_label[question["source_criterion_label"]],
                rule_set,
            )
            rule_ids = prediction.pop("rule_ids")
            prediction["trace_ids"] = [
                "retrieval.dense.medcpt.patient_local.top3",
                *rule_ids,
            ]
            predictions.append(prediction)
            completed += 1
            if progress:
                progress(completed, total_queries)

    inference_configuration = {
        "dense_contract_sha256": contract_sha256,
        "dense_index_id": index_manifest["index"]["index_id"],
        "deterministic_rule_set_sha256": canonical_sha256(rule_set),
    }
    prediction_set = {
        "prediction_set_version": PREDICTION_SET_VERSION,
        "benchmark_sha256": split["dataset"]["benchmark_sha256"],
        "split_manifest_sha256": split["manifest_sha256"],
        "split_name": split_name,
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "inference_config_sha256": canonical_sha256(inference_configuration),
        "generated_at": timestamp,
        "code_commit": runtime_commit,
        "predictions": predictions,
    }
    validate_prediction_set(prediction_set, catalog)
    unique_characters = sum(len(record["text"]) for record in records)
    full_question_characters = unique_characters * len(questions)
    retrieval_total_ms = sum(latencies)
    run: Dict[str, Any] = {
        "dense_run_version": RUN_VERSION,
        "run_sha256": "pending",
        "generated_at": timestamp,
        "code_commit": runtime_commit,
        "provenance": {
            "benchmark_sha256": split["dataset"]["benchmark_sha256"],
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": split_name,
            "evidence_index_manifest_sha256": evidence_manifest[
                "manifest_sha256"
            ],
            "evidence_index_id": evidence_manifest["index"]["index_id"],
            "dense_contract_sha256": contract_sha256,
            "dense_index_manifest_sha256": index_manifest["manifest_sha256"],
            "dense_index_id": index_manifest["index"]["index_id"],
            "question_catalog_sha256": catalog["catalog_sha256"],
            "prediction_set_content_sha256": canonical_sha256(prediction_set),
        },
        "configuration": {
            "contract_id": contract["contract_id"],
            "query_source": contract["query_input"]["source"],
            "document_format": contract["document_input"]["format"],
            "pooling": contract["representation"]["pooling"],
            "dimension": dimension,
            "l2_normalized": contract["representation"]["l2_normalized"],
            "similarity": contract["similarity"]["function"],
            "top_k": top_k,
            "tie_break": contract["selection"]["tie_break"],
            "retrieval_scope": contract["similarity"]["scope"],
        },
        "counts": {
            "patient_count": len(patient_ids),
            "question_count": len(questions),
            "query_count": len(results),
            "document_count": len(records),
            "selected_document_count": sum(
                len(item["selected_evidence"]) for item in results
            ),
            "candidate_document_comparisons": candidate_comparisons,
        },
        "performance": {
            "model_load_ms": model_load_ms,
            "document_encoding_ms": document_encoding_ms,
            "query_encoding_ms": query_encoding_ms,
            "retrieval_total_ms": retrieval_total_ms,
            "retrieval_mean_ms": statistics.fmean(latencies),
            "retrieval_p50_ms": _percentile(latencies, 0.50),
            "retrieval_p95_ms": _percentile(latencies, 0.95),
            "vector_index_size_bytes": len(vector_bytes),
            "unique_evidence_characters": unique_characters,
            "full_question_context_characters": full_question_characters,
            "selected_evidence_characters": selected_characters,
            "selected_character_proportion": (
                selected_characters / full_question_characters
            ),
        },
        "evaluation_boundary": {
            "independent_evidence_gold_available": False,
            "retrieval_relevance_metrics_reported": False,
            "downstream_answer_metrics_supported": True,
            "interpretation": (
                "The release has no independent evidence-ID gold. Controlled "
                "ranking tests, index integrity, resource statistics, and "
                "separately reported downstream answer metrics are diagnostic."
            ),
        },
        "results": results,
        "disclosure_note": (
            "Restricted dense output derived from MIMIC evidence. Keep local; "
            "do not publish vectors, patient IDs, rankings, or scores."
        ),
    }
    run["run_sha256"] = _self_hash(run, "run_sha256")
    validate_dense_run(run, catalog)
    return index_manifest, vector_bytes, run, prediction_set


def _write_private_bytes(payload: bytes, output_path: Path) -> Path:
    descriptor = os.open(
        output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def write_dense_run(
    index_manifest: Dict[str, Any],
    vector_bytes: bytes,
    run: Dict[str, Any],
    prediction_set: Dict[str, Any],
    output_directory: Path,
) -> Tuple[Path, Path, Path, Path]:
    assert_restricted_local_path(output_directory)
    validate_dense_index_manifest(index_manifest, vector_bytes)
    validate_dense_run(run)
    validate_prediction_set(prediction_set)
    if run["provenance"]["dense_index_manifest_sha256"] != index_manifest[
        "manifest_sha256"
    ]:
        raise ApixabanDenseError("Dense run and index manifest differ")
    if run["provenance"]["dense_index_id"] != index_manifest["index"][
        "index_id"
    ]:
        raise ApixabanDenseError("Dense run and index identity differ")
    if run["provenance"]["prediction_set_content_sha256"] != canonical_sha256(
        prediction_set
    ):
        raise ApixabanDenseError("Dense run and prediction set differ")
    selected_by_key = {
        (item["patient_id"], item["question_id"]): {
            evidence["evidence_id"] for evidence in item["selected_evidence"]
        }
        for item in run["results"]
    }
    prediction_keys = {
        (item["patient_id"], item["question_id"])
        for item in prediction_set["predictions"]
    }
    if set(selected_by_key) != prediction_keys:
        raise ApixabanDenseError("Dense retrieval and prediction grids differ")
    if any(
        not set(prediction["evidence_ids"]).issubset(
            selected_by_key[(prediction["patient_id"], prediction["question_id"])]
        )
        for prediction in prediction_set["predictions"]
    ):
        raise ApixabanDenseError("Dense prediction cites unselected evidence")
    expected_inference_configuration = {
        "dense_contract_sha256": run["provenance"]["dense_contract_sha256"],
        "dense_index_id": run["provenance"]["dense_index_id"],
        "deterministic_rule_set_sha256": canonical_sha256(
            load_deterministic_rule_set()
        ),
    }
    if prediction_set["inference_config_sha256"] != canonical_sha256(
        expected_inference_configuration
    ):
        raise ApixabanDenseError("Dense prediction inference configuration differs")
    for prediction_field, run_field in (
        ("benchmark_sha256", "benchmark_sha256"),
        ("split_manifest_sha256", "split_manifest_sha256"),
        ("split_name", "split_name"),
    ):
        if prediction_set[prediction_field] != run["provenance"][run_field]:
            raise ApixabanDenseError("Dense run and prediction provenance differ")
    paths = (
        output_directory / "vectors.f32",
        output_directory / "index-manifest.json",
        output_directory / "retrieval.json",
        output_directory / "predictions.json",
    )
    if any(path.exists() for path in paths):
        raise FileExistsError("Refusing to overwrite dense output")
    output_directory.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    try:
        written.append(_write_private_bytes(vector_bytes, paths[0]))
        written.append(write_private_json(index_manifest, paths[1]))
        written.append(write_private_json(run, paths[2]))
        written.append(write_private_json(prediction_set, paths[3]))
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return paths
