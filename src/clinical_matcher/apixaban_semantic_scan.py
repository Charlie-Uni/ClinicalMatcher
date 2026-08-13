import json
import math
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import file_sha256
from .apixaban_split import (
    ApixabanSplitError,
    load_apixaban_split_manifest,
    patient_content_sha256,
    split_manifest_view,
    write_private_json,
)
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .semantic_audit import build_semantic_scan_summary
from .splits import SemanticNearDuplicate


DEFAULT_MODEL_ID = "NeuML/pubmedbert-base-embeddings"
DEFAULT_MODEL_REVISION = "b79526d6ef3645e0df4530322e266f24c829f5ef"
PATIENT_POOLING = "model_native_then_l2_normalized_chunk_mean"
MAX_SEQUENCE_LENGTH = 512


def _normalize(vector: Sequence[float]) -> Tuple[float, ...]:
    values = tuple(float(value) for value in vector)
    if not values:
        raise ApixabanSplitError("Embedding vectors must not be empty")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0.0:
        raise ApixabanSplitError(
            "Embedding vectors must be finite and have non-zero norm"
        )
    return tuple(value / norm for value in values)


def _mean_normalized(vectors: Sequence[Sequence[float]]) -> Tuple[float, ...]:
    normalized = tuple(_normalize(vector) for vector in vectors)
    if not normalized:
        raise ApixabanSplitError("Every patient must have evidence text")
    dimensions = {len(vector) for vector in normalized}
    if len(dimensions) != 1:
        raise ApixabanSplitError("All embedding vectors must have equal length")
    mean = tuple(
        sum(vector[index] for vector in normalized) / len(normalized)
        for index in range(len(normalized[0]))
    )
    return _normalize(mean)


def _membership(document: Mapping[str, Any]) -> Dict[str, str]:
    return {
        patient_id: split_name
        for split_name, partition in document["splits"].items()
        for patient_id in partition["patient_ids"]
    }


def validate_scan_inputs(
    split_document: Mapping[str, Any],
    staging_document: Mapping[str, Any],
    staging_path: Path,
) -> None:
    validate_apixaban_staging_corpus(dict(staging_document))
    expected_hash = split_document["dataset"]["staging_corpus_sha256"]
    if file_sha256(staging_path) != expected_hash:
        raise ApixabanSplitError(
            "Staging corpus does not match the split candidate hash"
        )

    membership = _membership(split_document)
    patients = {
        patient["patient_id"]: patient
        for patient in staging_document["patients"]
    }
    if set(membership) != set(patients):
        raise ApixabanSplitError(
            "Staging patient membership does not match the split candidate"
        )
    for split_name, partition in split_document["splits"].items():
        for patient_id in partition["patient_ids"]:
            actual = patient_content_sha256(patients[patient_id])
            expected = partition["patient_content_sha256"][patient_id]
            if actual != expected:
                raise ApixabanSplitError(
                    "Patient content fingerprint mismatch for split "
                    f"{split_name}"
                )


def patient_chunk_texts(
    staging_document: Mapping[str, Any],
) -> Tuple[Tuple[str, ...], Dict[str, Tuple[int, int]]]:
    texts: List[str] = []
    spans: Dict[str, Tuple[int, int]] = {}
    for patient in sorted(
        staging_document["patients"], key=lambda item: item["patient_id"]
    ):
        start = len(texts)
        texts.extend(evidence["text"] for evidence in patient["evidence"])
        end = len(texts)
        if start == end:
            raise ApixabanSplitError("Every patient must have evidence text")
        spans[patient["patient_id"]] = (start, end)
    return tuple(texts), spans


def pool_patient_embeddings(
    chunk_embeddings: Sequence[Sequence[float]],
    spans: Mapping[str, Tuple[int, int]],
) -> Dict[str, Tuple[float, ...]]:
    if any(end > len(chunk_embeddings) for _, end in spans.values()):
        raise ApixabanSplitError("Encoder returned too few chunk embeddings")
    if spans and max(end for _, end in spans.values()) != len(chunk_embeddings):
        raise ApixabanSplitError("Encoder returned an unexpected vector count")
    return {
        patient_id: _mean_normalized(chunk_embeddings[start:end])
        for patient_id, (start, end) in spans.items()
    }


def cross_split_semantic_pairs(
    split_document: Mapping[str, Any],
    patient_embeddings: Mapping[str, Sequence[float]],
) -> Tuple[Tuple[SemanticNearDuplicate, ...], int]:
    membership = _membership(split_document)
    if set(patient_embeddings) != set(membership):
        raise ApixabanSplitError(
            "Embedding membership does not match the split candidate"
        )
    normalized = {
        patient_id: _normalize(vector)
        for patient_id, vector in patient_embeddings.items()
    }
    dimensions = {len(vector) for vector in normalized.values()}
    if len(dimensions) != 1:
        raise ApixabanSplitError("All patient vectors must have equal length")
    threshold = split_document["policy"]["semantic_similarity_threshold"]
    pairs: List[SemanticNearDuplicate] = []
    evaluated = 0
    for left_id, right_id in combinations(sorted(normalized), 2):
        if membership[left_id] == membership[right_id]:
            continue
        evaluated += 1
        similarity = sum(
            left * right
            for left, right in zip(normalized[left_id], normalized[right_id])
        )
        if similarity >= threshold:
            pairs.append(
                SemanticNearDuplicate(
                    dimension="patient",
                    left_id=left_id,
                    right_id=right_id,
                    similarity=max(-1.0, min(1.0, similarity)),
                )
            )
    return tuple(pairs), evaluated


def run_apixaban_semantic_scan(
    split_path: Path,
    staging_path: Path,
    pair_output_path: Path,
    summary_output_path: Path,
    model_id: str = DEFAULT_MODEL_ID,
    model_revision: str = DEFAULT_MODEL_REVISION,
    batch_size: int = 16,
    device: Optional[str] = None,
    encoder_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    for path in (
        split_path,
        staging_path,
        pair_output_path,
        summary_output_path,
    ):
        assert_restricted_local_path(path)
    if pair_output_path == summary_output_path:
        raise ApixabanSplitError("Pair and summary outputs must be different")
    if pair_output_path.exists() or summary_output_path.exists():
        raise FileExistsError("Refusing to overwrite semantic scan output")
    if batch_size < 1:
        raise ApixabanSplitError("batch_size must be positive")

    split_document = load_apixaban_split_manifest(split_path)
    if split_document["status"] != "candidate":
        raise ApixabanSplitError("Semantic scanning requires a candidate split")
    if staging_path.stat().st_mode & 0o077:
        raise ApixabanSplitError("Staging corpus must be owner-only")
    staging_document = json.loads(staging_path.read_text(encoding="utf-8"))
    validate_scan_inputs(split_document, staging_document, staging_path)
    texts, spans = patient_chunk_texts(staging_document)

    if encoder_factory is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Semantic scan dependencies are missing. Install with "
                "`pip install -e '.[semantic-scan]'`."
            ) from error
        encoder_factory = SentenceTransformer
    encoder = encoder_factory(
        model_id,
        revision=model_revision,
        device=device,
        trust_remote_code=False,
    )
    encoder.max_seq_length = MAX_SEQUENCE_LENGTH
    encoded = encoder.encode(
        list(texts),
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    chunk_embeddings = (
        encoded.tolist() if hasattr(encoded, "tolist") else encoded
    )
    patient_embeddings = pool_patient_embeddings(chunk_embeddings, spans)
    pairs, evaluated = cross_split_semantic_pairs(
        split_document, patient_embeddings
    )
    pair_payload = [
        {
            "dimension": pair.dimension,
            "left_id": pair.left_id,
            "right_id": pair.right_id,
            "similarity": pair.similarity,
        }
        for pair in pairs
    ]
    summary = build_semantic_scan_summary(
        manifest=split_manifest_view(split_document),
        dimension="patient",
        pairs=pairs,
        embedding_model_id=model_id,
        embedding_model_revision=model_revision,
        pooling=f"{PATIENT_POOLING}_maxseq{MAX_SEQUENCE_LENGTH}",
        vectors_normalized=True,
        search_method="exhaustive_cosine",
        candidate_pairs_evaluated=evaluated,
    )
    write_private_json(pair_payload, pair_output_path)
    try:
        write_private_json(summary, summary_output_path)
    except Exception:
        pair_output_path.unlink(missing_ok=True)
        raise
    return summary
