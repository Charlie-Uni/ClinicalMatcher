import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_evaluation import (
    evaluate_apixaban_predictions,
    write_apixaban_evaluation_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate restricted Apixaban boolean, unknown, and numeric "
            "fact predictions without conflating answer types."
        )
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument(
        "--split", choices=("train", "validation", "test"), required=True
    )
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-locked-test-evaluation", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )
    if args.split == "test" and not args.acknowledge_locked_test_evaluation:
        raise ValueError(
            "Locked test evaluation requires explicit acknowledgement; do "
            "not use test results for model or threshold selection"
        )
    report = evaluate_apixaban_predictions(
        benchmark_path=args.benchmark,
        split_path=args.frozen_split,
        prediction_path=args.predictions,
        split_name=args.split,
        bootstrap_samples=args.bootstrap_samples,
    )
    json_path, markdown_path = write_apixaban_evaluation_report(
        report, args.output_dir
    )
    print(f"Wrote restricted machine-readable report: {json_path}")
    print(f"Wrote restricted human-readable report: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
