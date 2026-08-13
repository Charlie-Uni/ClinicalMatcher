import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_structured_llm import (
    run_structured_llm_baseline,
    write_structured_llm_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the pinned local Llama 3.1 structured-output baseline against "
            "a frozen restricted Apixaban split."
        )
    )
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument(
        "--split", choices=("train", "validation", "test"), required=True
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-locked-test-inference", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError("--acknowledge-restricted-data-local-only is required")
    if args.split == "test" and not args.acknowledge_locked_test_inference:
        raise ValueError(
            "Locked test inference requires explicit acknowledgement; do not "
            "evaluate or inspect test results during model selection"
        )

    def progress(index: int, total: int) -> None:
        print(f"Completed local structured request {index}/{total}", flush=True)

    predictions, report = run_structured_llm_baseline(
        frozen_split_path=args.frozen_split,
        staging_corpus_path=args.staging_corpus,
        split_name=args.split,
        progress=progress,
    )
    prediction_path, report_path = write_structured_llm_run(
        predictions, report, args.output_dir
    )
    print(f"Wrote restricted predictions: {prediction_path}")
    print(f"Wrote restricted aggregate run report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
