import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_rrf import run_rrf_fusion, write_rrf_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run frozen full-candidate BM25 plus MedCPT RRF fusion."
    )
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--evidence-index-manifest", type=Path, required=True)
    parser.add_argument("--bm25-run", type=Path, required=True)
    parser.add_argument("--dense-run", type=Path, required=True)
    parser.add_argument("--dense-index-manifest", type=Path, required=True)
    parser.add_argument("--dense-vectors", type=Path, required=True)
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
            "Locked test RRF retrieval requires explicit acknowledgement; "
            "do not inspect or tune against test results"
        )

    def progress(index: int, total: int) -> None:
        if index == total or index % 23 == 0:
            print(f"Completed RRF queries {index}/{total}", flush=True)

    run, predictions = run_rrf_fusion(
        args.frozen_split,
        args.staging_corpus,
        args.evidence_index_manifest,
        args.bm25_run,
        args.dense_run,
        args.dense_index_manifest,
        args.dense_vectors,
        args.split,
        progress=progress,
    )
    retrieval_path, prediction_path = write_rrf_run(
        run, predictions, args.output_dir
    )
    print(f"Wrote restricted RRF retrievals: {retrieval_path}")
    print(f"Wrote restricted downstream predictions: {prediction_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
