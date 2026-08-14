import json
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
from .retrieval.bm25 import BM25PatientRetriever, TOKEN_PATTERN
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = "resources/apixaban-bm25-contract-1.0.0.json"
RUN_SCHEMA = "schemas/apixaban-bm25-run-1.0.0.schema.json"
RUN_VERSION = "1.0.0"
PREDICTION_SET_VERSION = "1.2.0"
MODEL_ID = "clinicalmatcher-bm25-deterministic@1.0.0"
PROMPT_VERSION = "not-applicable:source-question-bm25+reviewed-rules@1.0.0"


class ApixabanBM25Error(ValueError):
    """Raised when the frozen patient-local BM25 contract is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("run_sha256", None)
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


def load_bm25_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_bm25_contract(document)
    return document


def validate_bm25_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_id",
        "development_splits",
        "test_labels_used",
        "question_catalog_sha256",
        "evidence_chunk_contract_id",
        "query",
        "tokenizer",
        "bm25",
        "selection",
        "downstream",
    }
    if set(document) != required:
        raise ApixabanBM25Error("BM25 contract is incomplete")
    if document["contract_version"] != "1.0.0":
        raise ApixabanBM25Error("Unsupported BM25 contract")
    if document["contract_id"] != "apixaban-patient-bm25-v1":
        raise ApixabanBM25Error("Unexpected BM25 contract ID")
    if document["development_splits"] != ["train", "validation"]:
        raise ApixabanBM25Error("BM25 development splits changed")
    if document["test_labels_used"] is not False:
        raise ApixabanBM25Error("Test labels are forbidden")
    if document["evidence_chunk_contract_id"] != (
        "apixaban-preserved-evidence-chunks-v1"
    ):
        raise ApixabanBM25Error("BM25 evidence-chunk contract changed")
    catalog = load_question_catalog()
    if document["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanBM25Error("BM25 question catalog hash mismatch")
    query = document["query"]
    if query != {
        "source": "source_question_only",
        "answer_text_used": False,
        "fact_field_used": False,
        "manual_expansion_used": False,
    }:
        raise ApixabanBM25Error("BM25 query construction contract changed")
    tokenizer = document["tokenizer"]
    if (
        tokenizer["implementation"] != "python_re_unicode_v1"
        or tokenizer["pattern"] != TOKEN_PATTERN.pattern
        or tokenizer["case_normalization"] != "unicode_casefold"
        or tokenizer["stopwords"] != "none"
        or tokenizer["stemming"] != "none"
        or tokenizer["text_normalization_beyond_tokenization"] != "none"
    ):
        raise ApixabanBM25Error("BM25 tokenizer contract changed")
    bm25 = document["bm25"]
    if bm25 != {
        "variant": "BM25Okapi_positive_idf",
        "k1": 1.2,
        "b": 0.75,
        "idf": "log(1+(N-df+0.5)/(df+0.5))",
        "document_frequency_scope": "within_patient",
    }:
        raise ApixabanBM25Error("BM25 scoring contract changed")
    selection = document["selection"]
    if selection != {
        "top_k": 3,
        "minimum_score_exclusive": 0.0,
        "tie_break": ["source_span.start", "evidence_id"],
        "retrieval_scope": "within_patient_only",
        "top_k_rationale": (
            "predeclared_maximum_approximately_6000_source_characters_per_question"
        ),
    }:
        raise ApixabanBM25Error("BM25 selection contract changed")
    if document["downstream"] != {
        "extractor": "clinicalmatcher-deterministic-extractor@1.0.0",
        "purpose": "answer_metric_diagnostic_not_evidence_relevance_gold",
    }:
        raise ApixabanBM25Error("BM25 downstream diagnostic contract changed")


def validate_bm25_run(
    document: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    validate_document(dict(document), RUN_SCHEMA)
    if _self_hash(document) != document["run_sha256"]:
        raise ApixabanBM25Error("BM25 run hash mismatch")
    resolved = dict(catalog or load_question_catalog())
    contract = load_bm25_contract()
    if document["provenance"]["bm25_contract_sha256"] != canonical_sha256(
        contract
    ):
        raise ApixabanBM25Error("BM25 run contract hash mismatch")
    if document["provenance"]["question_catalog_sha256"] != resolved[
        "catalog_sha256"
    ]:
        raise ApixabanBM25Error("BM25 run question catalog mismatch")
    question_hashes = {
        question["question_id"]: canonical_sha256(question["source_question"])
        for question in resolved["questions"]
    }
    seen = set()
    selected_count = 0
    positive_queries = 0
    candidate_comparisons = 0
    patients = set()
    questions_by_patient: Dict[str, set] = {}
    candidate_counts_by_patient: Dict[str, set] = {}
    for result in document["results"]:
        key = (result["patient_id"], result["question_id"])
        if key in seen:
            raise ApixabanBM25Error("Duplicate patient-question retrieval")
        seen.add(key)
        patients.add(result["patient_id"])
        questions_by_patient.setdefault(result["patient_id"], set()).add(
            result["question_id"]
        )
        candidate_counts_by_patient.setdefault(result["patient_id"], set()).add(
            result["candidate_count"]
        )
        if result["query_sha256"] != question_hashes.get(result["question_id"]):
            raise ApixabanBM25Error("BM25 query is not the frozen source question")
        selected = result["selected_evidence"]
        if [item["rank"] for item in selected] != list(
            range(1, len(selected) + 1)
        ):
            raise ApixabanBM25Error("Selected BM25 ranks must be contiguous")
        if len({item["evidence_id"] for item in selected}) != len(selected):
            raise ApixabanBM25Error("Selected evidence IDs must be unique")
        patient_token = result["patient_id"].removeprefix("patient-")
        if any(
            not item["evidence_id"].startswith(f"evidence-{patient_token}-")
            for item in selected
        ):
            raise ApixabanBM25Error("BM25 selected evidence crossed patients")
        if any(
            left["score"] < right["score"]
            for left, right in zip(selected, selected[1:])
        ):
            raise ApixabanBM25Error("Selected BM25 scores are not ranked")
        selected_count += len(selected)
        positive_queries += int(bool(selected))
        candidate_comparisons += result["candidate_count"]
    counts = document["counts"]
    if len(seen) != counts["query_count"]:
        raise ApixabanBM25Error("BM25 query count does not reconcile")
    if selected_count != counts["selected_document_count"]:
        raise ApixabanBM25Error("BM25 selected-document count does not reconcile")
    if positive_queries != counts["queries_with_positive_match"]:
        raise ApixabanBM25Error("BM25 positive-query count does not reconcile")
    if counts["queries_without_positive_match"] != (
        counts["query_count"] - positive_queries
    ):
        raise ApixabanBM25Error("BM25 zero-match count does not reconcile")
    if len(patients) != counts["patient_count"]:
        raise ApixabanBM25Error("BM25 patient count does not reconcile")
    expected_question_ids = set(question_hashes)
    if len(expected_question_ids) != counts["question_count"]:
        raise ApixabanBM25Error("BM25 question count does not reconcile")
    if counts["query_count"] != (
        counts["patient_count"] * counts["question_count"]
    ):
        raise ApixabanBM25Error("BM25 patient-question grid is incomplete")
    if any(
        question_ids != expected_question_ids
        for question_ids in questions_by_patient.values()
    ):
        raise ApixabanBM25Error("BM25 patient-question coverage is incomplete")
    if candidate_comparisons != counts["candidate_document_comparisons"]:
        raise ApixabanBM25Error("BM25 candidate count does not reconcile")
    if any(len(values) != 1 for values in candidate_counts_by_patient.values()):
        raise ApixabanBM25Error("BM25 patient candidate counts changed by query")
    unique_document_count = sum(
        next(iter(values)) for values in candidate_counts_by_patient.values()
    )
    if unique_document_count != counts["document_count"]:
        raise ApixabanBM25Error("BM25 document count does not reconcile")


def run_bm25_baseline(
    frozen_split_path: Path,
    staging_corpus_path: Path,
    evidence_index_manifest_path: Path,
    split_name: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if split_name not in {"train", "validation", "test"}:
        raise ApixabanBM25Error("Unsupported split name")
    for path in (
        frozen_split_path,
        staging_corpus_path,
        evidence_index_manifest_path,
    ):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanBM25Error(f"Restricted BM25 input is not owner-only: {path}")
    index_manifest = verify_evidence_index_manifest_from_paths(
        evidence_index_manifest_path,
        frozen_split_path,
        staging_corpus_path,
    )
    if index_manifest["source"]["split_name"] != split_name:
        raise ApixabanBM25Error("Evidence index manifest split mismatch")
    split = load_apixaban_split_manifest(frozen_split_path)
    staging = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    catalog = load_question_catalog()
    contract = load_bm25_contract()
    if split["dataset"]["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanBM25Error("Frozen split question catalog mismatch")
    patient_ids = tuple(split["splits"][split_name]["patient_ids"])
    records = evidence_index_records(staging, patient_ids)
    record_by_id = {record["evidence_id"]: record for record in records}
    documents_per_patient: Dict[str, int] = {}
    for record in records:
        documents_per_patient[record["patient_id"]] = (
            documents_per_patient.get(record["patient_id"], 0) + 1
        )

    index_started = time.perf_counter()
    retriever = BM25PatientRetriever(
        records,
        k1=contract["bm25"]["k1"],
        b=contract["bm25"]["b"],
    )
    index_build_ms = (time.perf_counter() - index_started) * 1000
    if set(retriever.patient_ids) != set(patient_ids):
        raise ApixabanBM25Error("BM25 index patient membership mismatch")

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
    total_queries = len(patient_ids) * len(catalog["questions"])
    completed = 0
    for patient_id in sorted(patient_ids):
        for question in catalog["questions"]:
            query = question["source_question"]
            started = time.perf_counter()
            selected = retriever.retrieve(patient_id, query, top_k)
            latencies.append((time.perf_counter() - started) * 1000)
            candidate_comparisons += documents_per_patient[patient_id]
            selected_records = [record_by_id[item.evidence_id] for item in selected]
            selected_characters += sum(len(item["text"]) for item in selected_records)
            results.append(
                {
                    "patient_id": patient_id,
                    "question_id": question["question_id"],
                    "query_sha256": canonical_sha256(query),
                    "candidate_count": documents_per_patient[patient_id],
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
                "retrieval.bm25.patient_local.top3"
                if selected
                else "retrieval.bm25.no_positive_match",
                *rule_ids,
            ]
            predictions.append(prediction)
            completed += 1
            if progress:
                progress(completed, total_queries)

    runtime_commit = code_commit or current_git_commit()
    timestamp = generated_at or _now()
    inference_configuration = {
        "bm25_contract_sha256": canonical_sha256(contract),
        "evidence_index_id": index_manifest["index"]["index_id"],
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
    positive_queries = sum(bool(item["selected_evidence"]) for item in results)
    unique_characters = sum(len(record["text"]) for record in records)
    full_question_characters = unique_characters * len(catalog["questions"])
    retrieval_total_ms = sum(latencies)
    run: Dict[str, Any] = {
        "bm25_run_version": RUN_VERSION,
        "run_sha256": "pending",
        "generated_at": timestamp,
        "code_commit": runtime_commit,
        "provenance": {
            "benchmark_sha256": split["dataset"]["benchmark_sha256"],
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": split_name,
            "evidence_index_manifest_sha256": index_manifest["manifest_sha256"],
            "evidence_index_id": index_manifest["index"]["index_id"],
            "question_catalog_sha256": catalog["catalog_sha256"],
            "bm25_contract_sha256": canonical_sha256(contract),
            "prediction_set_content_sha256": canonical_sha256(prediction_set),
        },
        "configuration": {
            "contract_id": contract["contract_id"],
            "query_source": contract["query"]["source"],
            "tokenizer": contract["tokenizer"]["implementation"],
            "k1": contract["bm25"]["k1"],
            "b": contract["bm25"]["b"],
            "idf": contract["bm25"]["idf"],
            "top_k": top_k,
            "minimum_score_exclusive": contract["selection"][
                "minimum_score_exclusive"
            ],
            "tie_break": contract["selection"]["tie_break"],
            "retrieval_scope": contract["selection"]["retrieval_scope"],
        },
        "counts": {
            "patient_count": len(patient_ids),
            "question_count": len(catalog["questions"]),
            "query_count": len(results),
            "document_count": len(records),
            "queries_with_positive_match": positive_queries,
            "queries_without_positive_match": len(results) - positive_queries,
            "selected_document_count": sum(
                len(item["selected_evidence"]) for item in results
            ),
            "candidate_document_comparisons": candidate_comparisons,
        },
        "performance": {
            "index_build_ms": index_build_ms,
            "retrieval_total_ms": retrieval_total_ms,
            "retrieval_mean_ms": statistics.fmean(latencies),
            "retrieval_p50_ms": _percentile(latencies, 0.50),
            "retrieval_p95_ms": _percentile(latencies, 0.95),
            "deterministic_index_size_bytes": (
                retriever.deterministic_index_size_bytes()
            ),
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
                "The release has no independent evidence-ID gold. Ranking "
                "tests are synthetic and real-data evaluation is limited to "
                "resource statistics plus separately reported downstream "
                "answer metrics."
            ),
        },
        "results": results,
        "disclosure_note": (
            "Restricted BM25 output derived from MIMIC evidence. Keep local; "
            "do not publish patient-level IDs, rankings, or scores."
        ),
    }
    run["run_sha256"] = _self_hash(run)
    validate_bm25_run(run, catalog)
    return run, prediction_set


def write_bm25_run(
    run: Dict[str, Any],
    prediction_set: Dict[str, Any],
    output_directory: Path,
) -> Tuple[Path, Path]:
    assert_restricted_local_path(output_directory)
    validate_bm25_run(run)
    validate_prediction_set(prediction_set)
    if run["provenance"]["prediction_set_content_sha256"] != canonical_sha256(
        prediction_set
    ):
        raise ApixabanBM25Error("BM25 run and prediction set differ")
    retrieval_keys = {
        (item["patient_id"], item["question_id"]) for item in run["results"]
    }
    prediction_keys = {
        (item["patient_id"], item["question_id"])
        for item in prediction_set["predictions"]
    }
    if retrieval_keys != prediction_keys:
        raise ApixabanBM25Error("BM25 retrieval and prediction grids differ")
    selected_by_key = {
        (item["patient_id"], item["question_id"]): {
            evidence["evidence_id"] for evidence in item["selected_evidence"]
        }
        for item in run["results"]
    }
    if any(
        not set(prediction["evidence_ids"]).issubset(
            selected_by_key[(prediction["patient_id"], prediction["question_id"])]
        )
        for prediction in prediction_set["predictions"]
    ):
        raise ApixabanBM25Error("BM25 prediction cites unselected evidence")
    expected_inference_configuration = {
        "bm25_contract_sha256": run["provenance"]["bm25_contract_sha256"],
        "evidence_index_id": run["provenance"]["evidence_index_id"],
        "deterministic_rule_set_sha256": canonical_sha256(
            load_deterministic_rule_set()
        ),
    }
    if prediction_set["inference_config_sha256"] != canonical_sha256(
        expected_inference_configuration
    ):
        raise ApixabanBM25Error("BM25 prediction inference configuration differs")
    for prediction_field, run_field in (
        ("benchmark_sha256", "benchmark_sha256"),
        ("split_manifest_sha256", "split_manifest_sha256"),
        ("split_name", "split_name"),
    ):
        if prediction_set[prediction_field] != run["provenance"][run_field]:
            raise ApixabanBM25Error("BM25 run and prediction provenance differ")
    retrieval_path = output_directory / "retrieval.json"
    prediction_path = output_directory / "predictions.json"
    if retrieval_path.exists() or prediction_path.exists():
        raise FileExistsError("Refusing to overwrite BM25 output")
    output_directory.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    try:
        written.append(write_private_json(run, retrieval_path))
        written.append(write_private_json(prediction_set, prediction_path))
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return retrieval_path, prediction_path
