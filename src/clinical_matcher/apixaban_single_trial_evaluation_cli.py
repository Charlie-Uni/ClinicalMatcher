import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_single_trial_evaluation import (
    evaluate_single_trial_from_paths,
    write_single_trial_evaluation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the one owner-approved, validation-only Apixaban three-axis "
            "single-trial diagnostic. Locked test is not supported."
        )
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--model-predictions", type=Path, required=True)
    parser.add_argument("--mentor-screening-results", type=Path, required=True)
    parser.add_argument("--mentor-candidate-csv", type=Path, required=True)
    parser.add_argument("--id-map", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-single-predeclared-validation-run", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )
    if not args.acknowledge_single_predeclared_validation_run:
        raise ValueError(
            "--acknowledge-single-predeclared-validation-run is required"
        )
    report, trace = evaluate_single_trial_from_paths(
        benchmark_path=args.benchmark,
        split_path=args.frozen_split,
        prediction_path=args.model_predictions,
        mentor_results_path=args.mentor_screening_results,
        candidate_csv_path=args.mentor_candidate_csv,
        id_map_path=args.id_map,
    )
    report_path, trace_path, summary_path = write_single_trial_evaluation(
        report, trace, args.output_dir
    )
    print(f"Wrote owner-only three-axis report: {report_path}")
    print(f"Wrote owner-only row trace: {trace_path}")
    print(f"Wrote owner-only human-readable summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
