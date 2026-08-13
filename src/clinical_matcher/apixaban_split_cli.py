import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_split import (
    ApixabanSplitError,
    build_apixaban_split_candidate_from_paths,
    freeze_apixaban_split,
    load_apixaban_split_manifest,
    split_manifest_view,
    write_apixaban_split_document,
    write_private_json,
)
from .apixaban_semantic_scan import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    run_apixaban_semantic_scan,
)
from .ingestion.patients import assert_restricted_local_path
from .semantic_audit import build_semantic_scan_summary
from .splits import SemanticNearDuplicate, assert_no_split_leakage


def _acknowledged(args: argparse.Namespace) -> None:
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build, audit, verify, or freeze a restricted Apixaban "
            "patient-grouped split."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    candidate = commands.add_parser("candidate")
    candidate.add_argument("--benchmark", type=Path, required=True)
    candidate.add_argument("--benchmark-manifest", type=Path, required=True)
    candidate.add_argument("--staging-corpus", type=Path, required=True)
    candidate.add_argument("--import-manifest", type=Path, required=True)
    candidate.add_argument("--id-map", type=Path, required=True)
    candidate.add_argument("--quality-report", type=Path, required=True)
    candidate.add_argument("--semantic-pairs", type=Path, action="append")
    candidate.add_argument("--semantic-summary", type=Path, action="append")
    candidate.add_argument(
        "--semantic-source-candidate", type=Path, action="append"
    )
    candidate.add_argument("--train-fraction", type=float, required=True)
    candidate.add_argument("--validation-fraction", type=float, required=True)
    candidate.add_argument("--test-fraction", type=float, required=True)
    candidate.add_argument("--seed", type=int, required=True)
    candidate.add_argument(
        "--semantic-similarity-threshold", type=float, default=0.95
    )
    candidate.add_argument("--output", type=Path, required=True)
    candidate.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )

    audit = commands.add_parser("audit-semantic")
    audit.add_argument("--manifest", type=Path, required=True)
    audit.add_argument("--semantic-pairs", type=Path, required=True)
    audit.add_argument("--embedding-model-id", required=True)
    audit.add_argument("--embedding-model-revision", required=True)
    audit.add_argument("--pooling", required=True)
    audit.add_argument("--vectors-normalized", action="store_true")
    audit.add_argument(
        "--search-method",
        choices=("exhaustive_cosine", "ann_candidates"),
        required=True,
    )
    audit.add_argument("--candidate-pairs-evaluated", type=int, required=True)
    audit.add_argument("--candidate-recall-estimate", type=float)
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )

    scan = commands.add_parser("scan-semantic")
    scan.add_argument("--manifest", type=Path, required=True)
    scan.add_argument("--staging-corpus", type=Path, required=True)
    scan.add_argument("--semantic-pairs-output", type=Path, required=True)
    scan.add_argument("--summary-output", type=Path, required=True)
    scan.add_argument("--embedding-model-id", default=DEFAULT_MODEL_ID)
    scan.add_argument(
        "--embedding-model-revision", default=DEFAULT_MODEL_REVISION
    )
    scan.add_argument("--batch-size", type=int, default=16)
    scan.add_argument("--device")
    scan.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )

    freeze = commands.add_parser("freeze")
    freeze.add_argument("--candidate", type=Path, required=True)
    freeze.add_argument("--semantic-summary", type=Path, required=True)
    freeze.add_argument("--decision-reference", required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )

    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _acknowledged(args)
    if args.command == "candidate":
        document = build_apixaban_split_candidate_from_paths(
            args.benchmark,
            args.benchmark_manifest,
            args.staging_corpus,
            args.import_manifest,
            args.id_map,
            args.quality_report,
            semantic_pairs_path=args.semantic_pairs,
            semantic_summary_path=args.semantic_summary,
            semantic_source_candidate_path=args.semantic_source_candidate,
            fractions={
                "train": args.train_fraction,
                "validation": args.validation_fraction,
                "test": args.test_fraction,
            },
            seed=args.seed,
            semantic_similarity_threshold=(
                args.semantic_similarity_threshold
            ),
        )
        write_apixaban_split_document(document, args.output)
        counts = document["policy"]["target_patient_counts"]
        print(
            "Built restricted Apixaban split candidate: "
            f"train={counts['train']}, validation={counts['validation']}, "
            f"test={counts['test']}; semantic_scan_status=pending."
        )
        print(f"Candidate manifest SHA-256: {document['manifest_sha256']}")
        print(f"Local output: {args.output}")
        return 0

    if args.command == "audit-semantic":
        document = load_apixaban_split_manifest(args.manifest)
        assert_restricted_local_path(args.semantic_pairs)
        if args.semantic_pairs.stat().st_mode & 0o077:
            raise ApixabanSplitError(
                "Semantic pair file must be owner-only"
            )
        raw_pairs = json.loads(args.semantic_pairs.read_text(encoding="utf-8"))
        if not isinstance(raw_pairs, list):
            raise ValueError("Semantic pair file must contain a JSON array")
        pairs = tuple(SemanticNearDuplicate(**item) for item in raw_pairs)
        view = split_manifest_view(document)
        summary = build_semantic_scan_summary(
            manifest=view,
            dimension="patient",
            pairs=pairs,
            embedding_model_id=args.embedding_model_id,
            embedding_model_revision=args.embedding_model_revision,
            pooling=args.pooling,
            vectors_normalized=args.vectors_normalized,
            search_method=args.search_method,
            candidate_pairs_evaluated=args.candidate_pairs_evaluated,
            candidate_recall_estimate=args.candidate_recall_estimate,
        )
        write_private_json(summary, args.output)
        print(
            "Semantic split audit completed: "
            f"leakage_assertion_passed="
            f"{summary['results']['leakage_assertion_passed']}."
        )
        print(f"Local aggregate output: {args.output}")
        return 0

    if args.command == "scan-semantic":
        summary = run_apixaban_semantic_scan(
            split_path=args.manifest,
            staging_path=args.staging_corpus,
            pair_output_path=args.semantic_pairs_output,
            summary_output_path=args.summary_output,
            model_id=args.embedding_model_id,
            model_revision=args.embedding_model_revision,
            batch_size=args.batch_size,
            device=args.device,
        )
        results = summary["results"]
        print(
            "Exhaustive local semantic scan completed: "
            f"evaluated={summary['search']['candidate_pairs_evaluated']}, "
            f"retained={results['retained_pairs_at_or_above_threshold']}, "
            f"leakage_assertion_passed="
            f"{results['leakage_assertion_passed']}."
        )
        print(f"Restricted pair output: {args.semantic_pairs_output}")
        print(f"Text-free local summary: {args.summary_output}")
        return 0 if results["leakage_assertion_passed"] else 2

    if args.command == "freeze":
        candidate = load_apixaban_split_manifest(args.candidate)
        assert_restricted_local_path(args.semantic_summary)
        if args.semantic_summary.stat().st_mode & 0o077:
            raise ApixabanSplitError(
                "Semantic summary file must be owner-only"
            )
        summary = json.loads(args.semantic_summary.read_text(encoding="utf-8"))
        frozen = freeze_apixaban_split(
            candidate, summary, args.decision_reference
        )
        write_apixaban_split_document(frozen, args.output)
        print(
            "Frozen Apixaban split with locked test membership: "
            f"{frozen['manifest_sha256']}."
        )
        print(f"Local output: {args.output}")
        return 0

    document = load_apixaban_split_manifest(args.manifest)
    view = split_manifest_view(document)
    assert_no_split_leakage(view)
    counts = document["policy"]["target_patient_counts"]
    print(
        f"Verified {document['status']} Apixaban split: "
        f"train={counts['train']}, validation={counts['validation']}, "
        f"test={counts['test']}; semantic_scan_status="
        f"{document['isolation']['semantic_scan_status']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
