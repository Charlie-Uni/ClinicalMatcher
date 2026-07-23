from typing import Any, Dict, Optional, Sequence

from .splits import (
    SemanticNearDuplicate,
    SplitManifest,
    canonical_sha256,
)
from .validation import validate_document


SEMANTIC_AUDIT_VERSION = "1.0.0"
SEMANTIC_AUDIT_SCHEMA_RESOURCE = (
    "schemas/semantic-scan-summary-1.0.0.schema.json"
)
SUPPORTED_SEARCH_METHODS = ("exhaustive_cosine", "ann_candidates")


def build_semantic_scan_summary(
    manifest: SplitManifest,
    dimension: str,
    pairs: Sequence[SemanticNearDuplicate],
    embedding_model_id: str,
    embedding_model_revision: str,
    pooling: str,
    vectors_normalized: bool,
    search_method: str,
    candidate_pairs_evaluated: int,
    candidate_recall_estimate: Optional[float] = None,
) -> Dict[str, Any]:
    if dimension not in manifest.isolated_dimensions:
        raise ValueError(
            f"{dimension} is not an isolated dimension in this manifest"
        )
    if search_method not in SUPPORTED_SEARCH_METHODS:
        raise ValueError(f"Unsupported search method: {search_method}")
    if not all((embedding_model_id, embedding_model_revision, pooling)):
        raise ValueError("Embedding model, revision, and pooling are required")
    if candidate_pairs_evaluated < 0:
        raise ValueError("candidate_pairs_evaluated must be non-negative")
    if candidate_recall_estimate is not None and not (
        0.0 <= candidate_recall_estimate <= 1.0
    ):
        raise ValueError(
            "candidate_recall_estimate must be between 0 and 1"
        )

    split_names = tuple(manifest.splits)
    split_members = {
        name: set(manifest.splits[name].entity_ids.get(dimension, ()))
        for name in split_names
    }
    expected_cross_split_pairs = sum(
        len(split_members[left_name]) * len(split_members[right_name])
        for left_index, left_name in enumerate(split_names)
        for right_name in split_names[left_index + 1 :]
    )
    if candidate_pairs_evaluated > expected_cross_split_pairs:
        raise ValueError(
            "candidate_pairs_evaluated exceeds all possible cross-split pairs"
        )
    if len(pairs) > candidate_pairs_evaluated:
        raise ValueError(
            "Retained near-duplicate pairs exceed evaluated candidates"
        )
    if search_method == "exhaustive_cosine":
        if candidate_pairs_evaluated != expected_cross_split_pairs:
            raise ValueError(
                "Exhaustive scan must evaluate every cross-split pair: "
                f"expected {expected_cross_split_pairs}, got "
                f"{candidate_pairs_evaluated}"
            )
        if candidate_recall_estimate is not None:
            raise ValueError(
                "Exhaustive scan does not accept a candidate recall estimate"
            )
        exhaustive = True
    else:
        if candidate_recall_estimate is None:
            raise ValueError(
                "ANN scans require a measured candidate recall estimate"
            )
        exhaustive = False

    memberships = {
        entity_id: split_name
        for split_name, ids in split_members.items()
        for entity_id in ids
    }
    cross_split_pairs = 0
    serialized_pairs = []
    seen_pairs = set()
    for pair in pairs:
        if pair.dimension != dimension:
            raise ValueError("Semantic pair dimension does not match scan")
        if pair.similarity < manifest.semantic_similarity_threshold:
            raise ValueError(
                "Semantic pair file must retain only pairs at or above "
                "the manifest threshold"
            )
        unknown = {
            entity_id
            for entity_id in (pair.left_id, pair.right_id)
            if entity_id not in memberships
        }
        if unknown:
            raise ValueError(
                f"Semantic scan contains unknown IDs: {sorted(unknown)}"
            )
        if memberships[pair.left_id] == memberships[pair.right_id]:
            raise ValueError(
                "Semantic audit pair files must contain cross-split pairs only"
            )
        pair_key = tuple(sorted((pair.left_id, pair.right_id)))
        if pair_key in seen_pairs:
            raise ValueError("Semantic pair file contains a duplicate pair")
        seen_pairs.add(pair_key)
        cross_split_pairs += 1
        serialized_pairs.append(
            {
                "dimension": pair.dimension,
                "left_id": pair.left_id,
                "right_id": pair.right_id,
                "similarity": pair.similarity,
            }
        )

    limitations = []
    if not exhaustive:
        limitations.append(
            "ANN candidate generation can miss near-duplicate pairs; "
            "interpret the assertion at the measured candidate recall."
        )
    limitations.append(
        "Aggregate counts validate scan execution, not the clinical "
        "appropriateness of the embedding model or threshold."
    )
    limitations.append(
        "Detailed pair IDs and all real-data derivatives remain local to "
        "the authorized environment."
    )
    summary = {
        "summary_version": SEMANTIC_AUDIT_VERSION,
        "dataset_sha256": manifest.dataset_sha256,
        "split_manifest_sha256": manifest.manifest_sha256,
        "dimension": dimension,
        "threshold": manifest.semantic_similarity_threshold,
        "embedding": {
            "model_id": embedding_model_id,
            "model_revision": embedding_model_revision,
            "pooling": pooling,
            "vectors_normalized": vectors_normalized,
        },
        "search": {
            "method": search_method,
            "exhaustive": exhaustive,
            "entity_count": len(memberships),
            "expected_cross_split_pairs": expected_cross_split_pairs,
            "candidate_pairs_evaluated": candidate_pairs_evaluated,
            "candidate_recall_estimate": candidate_recall_estimate,
        },
        "results": {
            "retained_pairs_at_or_above_threshold": len(pairs),
            "cross_split_pairs_at_or_above_threshold": cross_split_pairs,
            "detailed_pair_payload_sha256": canonical_sha256(
                sorted(
                    serialized_pairs,
                    key=lambda item: (
                        item["dimension"],
                        item["left_id"],
                        item["right_id"],
                        item["similarity"],
                    ),
                )
            ),
            "leakage_assertion_passed": cross_split_pairs == 0,
        },
        "limitations": limitations,
        "disclosure_note": (
            "Text-free aggregate derived from restricted-data processing. "
            "Export still requires the applicable data-governance review."
        ),
    }
    validate_document(summary, SEMANTIC_AUDIT_SCHEMA_RESOURCE)
    return summary
