import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .semantic_audit import (
    SUPPORTED_SEARCH_METHODS,
    build_semantic_scan_summary,
)
from .splits import SemanticNearDuplicate, load_split_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a text-free aggregate audit of a local semantic "
            "near-duplicate scan."
        )
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--semantic-pairs", type=Path, required=True)
    parser.add_argument("--dimension", required=True)
    parser.add_argument("--embedding-model-id", required=True)
    parser.add_argument("--embedding-model-revision", required=True)
    parser.add_argument("--pooling", required=True)
    parser.add_argument("--vectors-normalized", action="store_true")
    parser.add_argument(
        "--search-method",
        choices=SUPPORTED_SEARCH_METHODS,
        required=True,
    )
    parser.add_argument("--candidate-pairs-evaluated", type=int, required=True)
    parser.add_argument("--candidate-recall-estimate", type=float)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_split_manifest(args.manifest)
    raw_pairs = json.loads(
        args.semantic_pairs.read_text(encoding="utf-8")
    )
    if not isinstance(raw_pairs, list):
        raise ValueError("Semantic pair file must contain a JSON array")
    pairs = tuple(SemanticNearDuplicate(**item) for item in raw_pairs)
    summary = build_semantic_scan_summary(
        manifest=manifest,
        dimension=args.dimension,
        pairs=pairs,
        embedding_model_id=args.embedding_model_id,
        embedding_model_revision=args.embedding_model_revision,
        pooling=args.pooling,
        vectors_normalized=args.vectors_normalized,
        search_method=args.search_method,
        candidate_pairs_evaluated=args.candidate_pairs_evaluated,
        candidate_recall_estimate=args.candidate_recall_estimate,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Semantic scan audit "
        f"{'passed' if summary['results']['leakage_assertion_passed'] else 'failed'}; "
        f"aggregate summary written to {args.output}"
    )
    return 0 if summary["results"]["leakage_assertion_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
