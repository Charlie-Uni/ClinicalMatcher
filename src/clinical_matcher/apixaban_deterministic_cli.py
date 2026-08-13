import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_deterministic import (
    build_deterministic_prediction_set,
    write_deterministic_prediction_set,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a restricted, evidence-linked deterministic baseline "
            "prediction set for the frozen Apixaban benchmark."
        )
    )
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument(
        "--split", choices=("train", "validation", "test"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-locked-test-prediction", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )
    if args.split == "test" and not args.acknowledge_locked_test_prediction:
        raise ValueError(
            "Locked test prediction requires explicit acknowledgement; do "
            "not evaluate or inspect test results during development"
        )
    document = build_deterministic_prediction_set(
        frozen_split_path=args.frozen_split,
        staging_corpus_path=args.staging_corpus,
        split_name=args.split,
    )
    path = write_deterministic_prediction_set(document, args.output)
    print(f"Wrote restricted deterministic predictions: {path}")
    print(f"Prediction rows: {len(document['predictions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
