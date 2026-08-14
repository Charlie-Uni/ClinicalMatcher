import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_dense import run_dense_baseline, write_dense_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build and run the frozen local-only MedCPT dense baseline with "
            "a deterministic downstream fact diagnostic."
        )
    )
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--evidence-index-manifest", type=Path, required=True)
    parser.add_argument(
        "--split", choices=("train", "validation", "test"), required=True
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-locked-test-retrieval", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError("--acknowledge-restricted-data-local-only is required")
    if args.split == "test" and not args.acknowledge_locked_test_retrieval:
        raise ValueError(
            "Locked test dense retrieval requires explicit acknowledgement; "
            "do not inspect or tune against test results"
        )

    def progress(index: int, total: int) -> None:
        if index == total or index % 23 == 0:
            print(f"Completed dense queries {index}/{total}", flush=True)

    index_manifest, vectors, run, predictions = run_dense_baseline(
        args.frozen_split,
        args.staging_corpus,
        args.evidence_index_manifest,
        args.split,
        progress=progress,
    )
    vector_path, manifest_path, retrieval_path, prediction_path = write_dense_run(
        index_manifest,
        vectors,
        run,
        predictions,
        args.output_dir,
    )
    print(f"Wrote restricted dense vectors: {vector_path}")
    print(f"Wrote restricted dense index manifest: {manifest_path}")
    print(f"Wrote restricted dense retrievals: {retrieval_path}")
    print(f"Wrote restricted downstream predictions: {prediction_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
