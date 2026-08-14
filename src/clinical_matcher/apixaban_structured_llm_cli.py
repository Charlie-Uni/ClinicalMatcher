import argparse
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .apixaban_structured_llm import (
    run_structured_llm_baseline,
    write_structured_llm_run,
)


def build_parser(long_context: bool = False) -> argparse.ArgumentParser:
    baseline = "long-context" if long_context else "structured-output"
    parser = argparse.ArgumentParser(
        description=(
            f"Run the pinned local Llama 3.1 {baseline} baseline against "
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


def run_cli(
    argv: Optional[Sequence[str]] = None,
    contract: Optional[Mapping[str, Any]] = None,
    long_context: bool = False,
) -> int:
    args = build_parser(long_context=long_context).parse_args(argv)
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
        contract=contract,
    )
    prediction_path, report_path = write_structured_llm_run(
        predictions, report, args.output_dir
    )
    print(f"Wrote restricted predictions: {prediction_path}")
    print(f"Wrote restricted aggregate run report: {report_path}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
