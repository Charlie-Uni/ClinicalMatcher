import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_numeric_occurrence import (
    evaluate_numeric_occurrence_from_paths,
    write_numeric_occurrence_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the validation-only weak numeric answer occurrence diagnostic."
        )
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--bm25-run", type=Path, required=True)
    parser.add_argument("--dense-run", type=Path, required=True)
    parser.add_argument("--rrf-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError("--acknowledge-restricted-data-local-only is required")
    report = evaluate_numeric_occurrence_from_paths(
        args.benchmark,
        args.frozen_split,
        args.staging_corpus,
        args.bm25_run,
        args.dense_run,
        args.rrf_run,
    )
    output = write_numeric_occurrence_report(report, args.output)
    print(f"Wrote restricted weak numeric occurrence report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
