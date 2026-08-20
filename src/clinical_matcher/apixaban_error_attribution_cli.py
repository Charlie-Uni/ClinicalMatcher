import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_error_attribution import run_error_attribution


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attribute observable errors in restricted Apixaban predictions."
    )
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acknowledge-restricted-data", action="store_true")
    parser.add_argument("--acknowledge-locked-test-use", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data:
        raise SystemExit("Restricted-data acknowledgement is required")
    split_name = json.loads(
        args.predictions.read_text(encoding="utf-8")
    ).get("split_name")
    if split_name == "test" and not args.acknowledge_locked_test_use:
        raise SystemExit("Locked-test acknowledgement is required")
    path = run_error_attribution(
        prediction_path=args.predictions,
        benchmark_path=args.benchmark,
        staging_corpus_path=args.staging_corpus,
        frozen_split_path=args.frozen_split,
        output_path=args.output,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
