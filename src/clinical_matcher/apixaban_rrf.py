import json
import math
import statistics
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import file_sha256
from .apixaban_bm25 import validate_bm25_run
from .apixaban_contract import load_question_catalog
from .apixaban_dense import (
    load_dense_contract,
    validate_dense_index_manifest,
    validate_dense_run,
)
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
from .retrieval.base import RankedEvidence
from .retrieval.bm25 import BM25PatientRetriever
from .retrieval.dense import (
    DenseEncoder,
    DensePatientRetriever,
    MedCPTEncoder,
    deserialize_float32_vectors,
)
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = "resources/apixaban-rrf-contract-1.0.0.json"
RUN_SCHEMA = "schemas/apixaban-rrf-run-1.0.0.schema.json"
RUN_VERSION = "1.0.0"
PREDICTION_SET_VERSION = "1.2.0"
MODEL_ID = "clinicalmatcher-bm25-medcpt-rrf-deterministic@1.0.0"
PROMPT_VERSION = "not-applicable:source-question-rrf+reviewed-rules@1.0.0"


class ApixabanRRFError(ValueError):
    """Raised when the frozen BM25 plus MedCPT RRF contract is violated."""


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


def load_rrf_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_rrf_contract(document)
    return document


def validate_rrf_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_id",
        "development_splits",
        "test_labels_used",
        "question_catalog_sha256",
        "components",
        "fusion",
        "selection",
        "downstream",
        "reranker",
    }
    if set(document) != required:
        raise ApixabanRRFError("RRF contract is incomplete")
    if document["contract_version"] != "1.0.0":
        raise ApixabanRRFError("Unsupported RRF contract")
    if document["contract_id"] != "apixaban-bm25-medcpt-rrf-v1":
        raise ApixabanRRFError("Unexpected RRF contract ID")
    if document["development_splits"] != ["train", "validation"]:
        raise ApixabanRRFError("RRF development splits changed")
    if document["test_labels_used"] is not False:
        raise ApixabanRRFError("Test labels are forbidden")
    if document["question_catalog_sha256"] != load_question_catalog()[
        "catalog_sha256"
    ]:
        raise ApixabanRRFError("RRF question catalog hash mismatch")
    if document["components"] != {
        "bm25_contract_id": "apixaban-patient-bm25-v1",
        "bm25_input_depth": "all_strictly_positive_score_patient_candidates",
        "dense_contract_id": "apixaban-medcpt-dense-v1",
        "dense_input_depth": "all_patient_candidates",
        "same_split_query_and_evidence_index_required": True,
    }:
        raise ApixabanRRFError("RRF component contract changed")
    if document["fusion"] != {
        "method": "reciprocal_rank_fusion",
        "rank_constant": 60,
        "formula": "sum(1/(60+rank))",
        "component_weights": {"bm25": 1.0, "dense": 1.0},
        "score_normalization": "none_rank_only",
        "parameter_search_used": False,
    }:
        raise ApixabanRRFError("RRF formula contract changed")
    if document["selection"] != {
        "top_k": 3,
        "tie_break": ["source_span.start", "evidence_id"],
        "exposure_budget": "same_as_bm25_and_dense_v1",
    }:
        raise ApixabanRRFError("RRF selection contract changed")
    if document["reranker"] != {
        "included": False,
        "decision": "deferred_until_rrf_validation_is_measured",
    }:
        raise ApixabanRRFError("RRF reranker boundary changed")


def reciprocal_rank_fusion(
    bm25_ranking: Sequence[RankedEvidence],
    dense_ranking: Sequence[RankedEvidence],
    source_starts: Mapping[str, int],
    *,
    rank_constant: int = 60,
    top_k: int = 3,
) -> Tuple[Dict[str, Any], ...]:
    if rank_constant < 1 or top_k < 1:
        raise ValueError("RRF rank constant and top_k must be positive")
    bm25_ranks = {item.evidence_id: item.rank for item in bm25_ranking}
    dense_ranks = {item.evidence_id: item.rank for item in dense_ranking}
    if len(bm25_ranks) != len(bm25_ranking):
        raise ValueError("RRF BM25 ranking contains duplicate evidence")
    if len(dense_ranks) != len(dense_ranking):
        raise ValueError("RRF dense ranking contains duplicate evidence")
    if [item.rank for item in bm25_ranking] != list(
        range(1, len(bm25_ranking) + 1)
    ):
        raise ValueError("RRF BM25 ranks must be ordered and contiguous")
    if [item.rank for item in dense_ranking] != list(
        range(1, len(dense_ranking) + 1)
    ):
        raise ValueError("RRF dense ranks must be ordered and contiguous")
    if set(bm25_ranks) - set(dense_ranks):
        raise ValueError("RRF BM25 candidates are absent from the dense ranking")
    if set(dense_ranks) != set(source_starts):
        raise ValueError("RRF dense ranking and patient candidates differ")
    fused = []
    for evidence_id, dense_rank in dense_ranks.items():
        bm25_rank = bm25_ranks.get(evidence_id)
        score = 1.0 / (rank_constant + dense_rank)
        if bm25_rank is not None:
            score += 1.0 / (rank_constant + bm25_rank)
        fused.append(
            (score, source_starts[evidence_id], evidence_id, bm25_rank, dense_rank)
        )
    fused.sort(key=lambda item: (-item[0], item[1], item[2]))
    return tuple(
        {
            "evidence_id": evidence_id,
            "score": score,
            "rank": rank,
            "bm25_rank": bm25_rank,
            "dense_rank": dense_rank,
        }
        for rank, (score, _, evidence_id, bm25_rank, dense_rank) in enumerate(
            fused[:top_k], start=1
        )
    )


def _component_documents(
    bm25_run: Mapping[str, Any],
    dense_run: Mapping[str, Any],
    dense_index: Mapping[str, Any],
    evidence_manifest: Mapping[str, Any],
    split_name: str,
) -> None:
    validate_bm25_run(bm25_run)
    validate_dense_run(dense_run)
    validate_dense_index_manifest(dense_index)
    common = (
        "benchmark_sha256",
        "split_manifest_sha256",
        "split_name",
        "evidence_index_manifest_sha256",
        "evidence_index_id",
        "question_catalog_sha256",
    )
    for field in common:
        if bm25_run["provenance"][field] != dense_run["provenance"][field]:
            raise ApixabanRRFError(f"RRF component provenance differs: {field}")
    if bm25_run["provenance"]["split_name"] != split_name:
        raise ApixabanRRFError("RRF component split differs from requested split")
    if dense_run["provenance"]["dense_index_manifest_sha256"] != dense_index[
        "manifest_sha256"
    ]:
        raise ApixabanRRFError("RRF dense run and index manifest differ")
    if dense_run["provenance"]["dense_index_id"] != dense_index["index"][
        "index_id"
    ]:
        raise ApixabanRRFError("RRF dense run and index identity differ")
    if bm25_run["provenance"]["evidence_index_manifest_sha256"] != (
        evidence_manifest["manifest_sha256"]
    ):
        raise ApixabanRRFError("RRF evidence index differs from component runs")
    for field in (
        "benchmark_sha256",
        "split_manifest_sha256",
        "split_name",
        "evidence_index_manifest_sha256",
        "evidence_index_id",
    ):
        if dense_index["provenance"][field] != dense_run["provenance"][field]:
            raise ApixabanRRFError(
                f"RRF dense index provenance differs from dense run: {field}"
            )
    bm25_keys = {
        (item["patient_id"], item["question_id"])
        for item in bm25_run["results"]
    }
    dense_keys = {
        (item["patient_id"], item["question_id"])
        for item in dense_run["results"]
    }
    if bm25_keys != dense_keys:
        raise ApixabanRRFError("RRF component query grids differ")


def validate_rrf_run(
    document: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    validate_document(dict(document), RUN_SCHEMA)
    if _self_hash(document) != document["run_sha256"]:
        raise ApixabanRRFError("RRF run hash mismatch")
    resolved = dict(catalog or load_question_catalog())
    contract = load_rrf_contract()
    provenance = document["provenance"]
    if provenance["rrf_contract_sha256"] != canonical_sha256(contract):
        raise ApixabanRRFError("RRF run contract hash mismatch")
    if provenance["question_catalog_sha256"] != resolved["catalog_sha256"]:
        raise ApixabanRRFError("RRF run question catalog mismatch")
    question_hashes = {
        question["question_id"]: canonical_sha256(question["source_question"])
        for question in resolved["questions"]
    }
    seen = set()
    patients = set()
    questions_by_patient: Dict[str, set] = {}
    totals = {
        "bm25_positive_candidate_count": 0,
        "dense_candidate_count": 0,
        "fused_unique_candidate_count": 0,
        "selected_document_count": 0,
        "selected_with_both_ranks_count": 0,
        "selected_with_dense_only_rank_count": 0,
    }
    documents_by_patient: Dict[str, set] = {}
    for result in document["results"]:
        key = (result["patient_id"], result["question_id"])
        if key in seen:
            raise ApixabanRRFError("Duplicate patient-question RRF result")
        seen.add(key)
        patient_id = result["patient_id"]
        patients.add(patient_id)
        questions_by_patient.setdefault(patient_id, set()).add(
            result["question_id"]
        )
        documents_by_patient.setdefault(patient_id, set()).add(
            result["candidate_count"]
        )
        if result["query_sha256"] != question_hashes.get(result["question_id"]):
            raise ApixabanRRFError("RRF query is not the frozen source question")
        if result["dense_candidate_count"] != result["candidate_count"]:
            raise ApixabanRRFError("RRF dense ranking is not complete")
        if result["fused_unique_candidate_count"] != result["candidate_count"]:
            raise ApixabanRRFError("RRF fused candidate union is incomplete")
        if result["bm25_positive_candidate_count"] > result["candidate_count"]:
            raise ApixabanRRFError("RRF BM25 candidate count is impossible")
        selected = result["selected_evidence"]
        selected_ids = [item["evidence_id"] for item in selected]
        if len(selected_ids) != len(set(selected_ids)):
            raise ApixabanRRFError("Selected RRF evidence IDs must be unique")
        if [item["rank"] for item in selected] != list(
            range(1, len(selected) + 1)
        ):
            raise ApixabanRRFError("Selected RRF ranks must be contiguous")
        if any(
            not math.isfinite(item["score"]) or item["score"] <= 0
            for item in selected
        ):
            raise ApixabanRRFError("RRF scores must be finite and positive")
        if any(
            left["score"] < right["score"]
            for left, right in zip(selected, selected[1:])
        ):
            raise ApixabanRRFError("Selected RRF scores are not ranked")
        for item in selected:
            if item["dense_rank"] > result["dense_candidate_count"]:
                raise ApixabanRRFError("Selected dense rank exceeds candidate count")
            if item["bm25_rank"] is not None and item["bm25_rank"] > result[
                "bm25_positive_candidate_count"
            ]:
                raise ApixabanRRFError("Selected BM25 rank exceeds positive depth")
            expected_score = 1.0 / (60 + item["dense_rank"])
            if item["bm25_rank"] is not None:
                expected_score += 1.0 / (60 + item["bm25_rank"])
            if not math.isclose(
                item["score"], expected_score, rel_tol=1e-12, abs_tol=1e-15
            ):
                raise ApixabanRRFError("Selected RRF score does not match its ranks")
        token = patient_id.removeprefix("patient-")
        if any(
            not item["evidence_id"].startswith(f"evidence-{token}-")
            for item in selected
        ):
            raise ApixabanRRFError("RRF selected evidence crossed patients")
        totals["bm25_positive_candidate_count"] += result[
            "bm25_positive_candidate_count"
        ]
        totals["dense_candidate_count"] += result["dense_candidate_count"]
        totals["fused_unique_candidate_count"] += result[
            "fused_unique_candidate_count"
        ]
        totals["selected_document_count"] += len(selected)
        totals["selected_with_both_ranks_count"] += sum(
            item["bm25_rank"] is not None for item in selected
        )
        totals["selected_with_dense_only_rank_count"] += sum(
            item["bm25_rank"] is None for item in selected
        )
    counts = document["counts"]
    expected_questions = set(question_hashes)
    if len(seen) != counts["query_count"]:
        raise ApixabanRRFError("RRF query count does not reconcile")
    if len(patients) != counts["patient_count"]:
        raise ApixabanRRFError("RRF patient count does not reconcile")
    if len(expected_questions) != counts["question_count"]:
        raise ApixabanRRFError("RRF question count does not reconcile")
    if counts["query_count"] != counts["patient_count"] * counts["question_count"]:
        raise ApixabanRRFError("RRF patient-question grid is incomplete")
    if any(values != expected_questions for values in questions_by_patient.values()):
        raise ApixabanRRFError("RRF patient-question coverage is incomplete")
    if any(len(values) != 1 for values in documents_by_patient.values()):
        raise ApixabanRRFError("RRF patient candidate count changed by query")
    document_count = sum(next(iter(values)) for values in documents_by_patient.values())
    if document_count != counts["document_count"]:
        raise ApixabanRRFError("RRF document count does not reconcile")
    for field, expected in totals.items():
        if counts[field] != expected:
            raise ApixabanRRFError(f"RRF aggregate does not reconcile: {field}")


def run_rrf_fusion(
    frozen_split_path: Path,
    staging_corpus_path: Path,
    evidence_index_manifest_path: Path,
    bm25_run_path: Path,
    dense_run_path: Path,
    dense_index_manifest_path: Path,
    dense_vectors_path: Path,
    split_name: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    encoder_factory: Optional[Callable[[Mapping[str, Any]], DenseEncoder]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if split_name not in {"train", "validation", "test"}:
        raise ApixabanRRFError("Unsupported split name")
    paths = (
        frozen_split_path,
        staging_corpus_path,
        evidence_index_manifest_path,
        bm25_run_path,
        dense_run_path,
        dense_index_manifest_path,
        dense_vectors_path,
    )
    for path in paths:
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanRRFError(f"Restricted RRF input is not owner-only: {path}")
    evidence_manifest = verify_evidence_index_manifest_from_paths(
        evidence_index_manifest_path,
        frozen_split_path,
        staging_corpus_path,
    )
    bm25_run = json.loads(bm25_run_path.read_text(encoding="utf-8"))
    dense_run = json.loads(dense_run_path.read_text(encoding="utf-8"))
    dense_index = json.loads(dense_index_manifest_path.read_text(encoding="utf-8"))
    vector_bytes = dense_vectors_path.read_bytes()
    validate_dense_index_manifest(dense_index, vector_bytes)
    _component_documents(
        bm25_run,
        dense_run,
        dense_index,
        evidence_manifest,
        split_name,
    )
    bm25_component_by_key = {
        (item["patient_id"], item["question_id"]): item
        for item in bm25_run["results"]
    }
    dense_component_by_key = {
        (item["patient_id"], item["question_id"]): item
        for item in dense_run["results"]
    }
    if file_sha256(dense_vectors_path) != dense_index["index"][
        "vector_file_sha256"
    ]:
        raise ApixabanRRFError("RRF dense vector file hash mismatch")

    split = load_apixaban_split_manifest(frozen_split_path)
    staging = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    catalog = load_question_catalog()
    contract = load_rrf_contract()
    if split["dataset"]["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanRRFError("Frozen split question catalog mismatch")
    if evidence_manifest["source"]["split_name"] != split_name:
        raise ApixabanRRFError("RRF evidence index split mismatch")
    expected_provenance = {
        "benchmark_sha256": split["dataset"]["benchmark_sha256"],
        "split_manifest_sha256": split["manifest_sha256"],
        "split_name": split_name,
        "question_catalog_sha256": catalog["catalog_sha256"],
    }
    for field, expected in expected_provenance.items():
        if bm25_run["provenance"][field] != expected:
            raise ApixabanRRFError(
                f"RRF component does not match frozen input: {field}"
            )
    patient_ids = tuple(split["splits"][split_name]["patient_ids"])
    records = evidence_index_records(staging, patient_ids)
    if [record["evidence_id"] for record in records] != dense_index["index"][
        "ordered_evidence_ids"
    ]:
        raise ApixabanRRFError("RRF records and dense vector ordering differ")
    dimension = dense_index["model"]["dimension"]
    vector_started = time.perf_counter()
    vectors = deserialize_float32_vectors(
        vector_bytes,
        dimension=dimension,
        count=dense_index["counts"]["vector_count"],
    )
    dense_vector_load_ms = (time.perf_counter() - vector_started) * 1000
    record_by_id = {record["evidence_id"]: record for record in records}
    source_starts_by_patient: Dict[str, Dict[str, int]] = {}
    documents_per_patient: Dict[str, int] = {}
    for record in records:
        patient_id = record["patient_id"]
        source_starts_by_patient.setdefault(patient_id, {})[
            record["evidence_id"]
        ] = record["source_span"]["start"]
        documents_per_patient[patient_id] = documents_per_patient.get(patient_id, 0) + 1
    bm25 = BM25PatientRetriever(records)
    dense = DensePatientRetriever(records, vectors)

    load_started = time.perf_counter()
    encoder = (encoder_factory or MedCPTEncoder)(load_dense_contract())
    model_load_ms = (time.perf_counter() - load_started) * 1000
    questions = catalog["questions"]
    query_started = time.perf_counter()
    query_vectors = encoder.encode_queries(
        [question["source_question"] for question in questions]
    )
    query_encoding_ms = (time.perf_counter() - query_started) * 1000
    if len(query_vectors) != len(questions) or any(
        len(vector) != dimension for vector in query_vectors
    ):
        raise ApixabanRRFError("RRF query vectors violate dense index shape")
    query_by_id = {
        question["question_id"]: vector
        for question, vector in zip(questions, query_vectors)
    }

    rule_set = load_deterministic_rule_set()
    rules = {rule["source_criterion_label"]: rule for rule in rule_set["rules"]}
    results: List[Dict[str, Any]] = []
    predictions: List[Dict[str, Any]] = []
    latencies: List[float] = []
    selected_characters = 0
    total_queries = len(patient_ids) * len(questions)
    completed = 0
    for patient_id in sorted(patient_ids):
        for question in questions:
            started = time.perf_counter()
            bm25_positive = tuple(
                item
                for item in bm25.rank(patient_id, question["source_question"])
                if item.score > 0
            )
            dense_all = dense.rank_vector(
                patient_id, query_by_id[question["question_id"]]
            )
            key = (patient_id, question["question_id"])
            expected_bm25 = [
                item["evidence_id"]
                for item in bm25_component_by_key[key]["selected_evidence"]
            ]
            if [item.evidence_id for item in bm25_positive[:3]] != expected_bm25:
                raise ApixabanRRFError(
                    "Recomputed BM25 ranking differs from the cited component run"
                )
            expected_dense = [
                item["evidence_id"]
                for item in dense_component_by_key[key]["selected_evidence"]
            ]
            if [item.evidence_id for item in dense_all[:3]] != expected_dense:
                raise ApixabanRRFError(
                    "Recomputed dense ranking differs from the cited component run"
                )
            selected = reciprocal_rank_fusion(
                bm25_positive,
                dense_all,
                source_starts_by_patient[patient_id],
                rank_constant=contract["fusion"]["rank_constant"],
                top_k=contract["selection"]["top_k"],
            )
            latencies.append((time.perf_counter() - started) * 1000)
            selected_records = [record_by_id[item["evidence_id"]] for item in selected]
            selected_characters += sum(len(item["text"]) for item in selected_records)
            candidate_count = documents_per_patient[patient_id]
            results.append(
                {
                    "patient_id": patient_id,
                    "question_id": question["question_id"],
                    "query_sha256": canonical_sha256(question["source_question"]),
                    "candidate_count": candidate_count,
                    "bm25_positive_candidate_count": len(bm25_positive),
                    "dense_candidate_count": len(dense_all),
                    "fused_unique_candidate_count": len(dense_all),
                    "selected_evidence": list(selected),
                }
            )
            prediction = extract_question_prediction(
                {"patient_id": patient_id, "evidence": selected_records},
                question,
                rules[question["source_criterion_label"]],
                rule_set,
            )
            rule_ids = prediction.pop("rule_ids")
            prediction["trace_ids"] = [
                "retrieval.fusion.bm25_medcpt_rrf60.patient_local.top3",
                *rule_ids,
            ]
            predictions.append(prediction)
            completed += 1
            if progress:
                progress(completed, total_queries)

    runtime_commit = code_commit or current_git_commit()
    timestamp = generated_at or _now()
    contract_sha256 = canonical_sha256(contract)
    inference_configuration = {
        "rrf_contract_sha256": contract_sha256,
        "bm25_run_sha256": bm25_run["run_sha256"],
        "dense_run_sha256": dense_run["run_sha256"],
        "dense_index_id": dense_index["index"]["index_id"],
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
    total_ms = sum(latencies)
    counts = {
        "patient_count": len(patient_ids),
        "question_count": len(questions),
        "query_count": len(results),
        "document_count": len(records),
        "bm25_positive_candidate_count": sum(
            item["bm25_positive_candidate_count"] for item in results
        ),
        "dense_candidate_count": sum(item["dense_candidate_count"] for item in results),
        "fused_unique_candidate_count": sum(
            item["fused_unique_candidate_count"] for item in results
        ),
        "selected_document_count": sum(
            len(item["selected_evidence"]) for item in results
        ),
        "selected_with_both_ranks_count": sum(
            evidence["bm25_rank"] is not None
            for item in results
            for evidence in item["selected_evidence"]
        ),
        "selected_with_dense_only_rank_count": sum(
            evidence["bm25_rank"] is None
            for item in results
            for evidence in item["selected_evidence"]
        ),
    }
    run: Dict[str, Any] = {
        "rrf_run_version": RUN_VERSION,
        "run_sha256": "pending",
        "generated_at": timestamp,
        "code_commit": runtime_commit,
        "provenance": {
            "benchmark_sha256": split["dataset"]["benchmark_sha256"],
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": split_name,
            "evidence_index_manifest_sha256": evidence_manifest["manifest_sha256"],
            "evidence_index_id": evidence_manifest["index"]["index_id"],
            "bm25_run_sha256": bm25_run["run_sha256"],
            "dense_run_sha256": dense_run["run_sha256"],
            "dense_index_manifest_sha256": dense_index["manifest_sha256"],
            "dense_index_id": dense_index["index"]["index_id"],
            "rrf_contract_sha256": contract_sha256,
            "question_catalog_sha256": catalog["catalog_sha256"],
            "prediction_set_content_sha256": canonical_sha256(prediction_set),
        },
        "configuration": {
            "contract_id": contract["contract_id"],
            "method": contract["fusion"]["method"],
            "rank_constant": contract["fusion"]["rank_constant"],
            "formula": contract["fusion"]["formula"],
            "component_weights": contract["fusion"]["component_weights"],
            "bm25_input_depth": contract["components"]["bm25_input_depth"],
            "dense_input_depth": contract["components"]["dense_input_depth"],
            "top_k": contract["selection"]["top_k"],
            "tie_break": contract["selection"]["tie_break"],
            "retrieval_scope": "within_patient_only",
            "reranker_included": contract["reranker"]["included"],
        },
        "counts": counts,
        "performance": {
            "model_load_ms": model_load_ms,
            "dense_vector_load_ms": dense_vector_load_ms,
            "query_encoding_ms": query_encoding_ms,
            "ranking_and_fusion_total_ms": total_ms,
            "ranking_and_fusion_mean_ms": statistics.fmean(latencies),
            "ranking_and_fusion_p50_ms": _percentile(latencies, 0.50),
            "ranking_and_fusion_p95_ms": _percentile(latencies, 0.95),
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
                "RRF parameters were fixed before validation and no reranker was "
                "run. The release has no independent evidence-ID relevance gold; "
                "resource and downstream answer metrics are diagnostic."
            ),
        },
        "results": results,
        "disclosure_note": (
            "Restricted fusion output derived from MIMIC evidence. Keep local; "
            "do not publish patient IDs, rankings, scores, or predictions."
        ),
    }
    run["run_sha256"] = _self_hash(run)
    validate_rrf_run(run, catalog)
    return run, prediction_set
def write_rrf_run(
    run: Dict[str, Any],
    prediction_set: Dict[str, Any],
    output_directory: Path,
) -> Tuple[Path, Path]:
    assert_restricted_local_path(output_directory)
    validate_rrf_run(run)
    validate_prediction_set(prediction_set)
    if run["provenance"]["prediction_set_content_sha256"] != canonical_sha256(
        prediction_set
    ):
        raise ApixabanRRFError("RRF run and prediction set differ")
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
        raise ApixabanRRFError("RRF retrieval and prediction grids differ")
    if any(
        not set(prediction["evidence_ids"]).issubset(
            selected_by_key[(prediction["patient_id"], prediction["question_id"])]
        )
        for prediction in prediction_set["predictions"]
    ):
        raise ApixabanRRFError("RRF prediction cites unselected evidence")
    expected = {
        "rrf_contract_sha256": run["provenance"]["rrf_contract_sha256"],
        "bm25_run_sha256": run["provenance"]["bm25_run_sha256"],
        "dense_run_sha256": run["provenance"]["dense_run_sha256"],
        "dense_index_id": run["provenance"]["dense_index_id"],
        "deterministic_rule_set_sha256": canonical_sha256(
            load_deterministic_rule_set()
        ),
    }
    if prediction_set["inference_config_sha256"] != canonical_sha256(expected):
        raise ApixabanRRFError("RRF prediction inference configuration differs")
    for prediction_field, run_field in (
        ("benchmark_sha256", "benchmark_sha256"),
        ("split_manifest_sha256", "split_manifest_sha256"),
        ("split_name", "split_name"),
    ):
        if prediction_set[prediction_field] != run["provenance"][run_field]:
            raise ApixabanRRFError("RRF run and prediction provenance differ")
    retrieval_path = output_directory / "retrieval.json"
    prediction_path = output_directory / "predictions.json"
    if retrieval_path.exists() or prediction_path.exists():
        raise FileExistsError("Refusing to overwrite RRF output")
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
