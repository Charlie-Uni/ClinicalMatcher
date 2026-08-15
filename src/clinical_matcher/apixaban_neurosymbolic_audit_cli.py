import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_neurosymbolic_audit import run_neurosymbolic_readiness_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit real restricted Apixaban fact outputs at the supported "
            "neuro-symbolic boundary."
        )
    )
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data",
        action="store_true",
        help="Confirm that every input and output remains in the authorized local environment.",
    )
    parser.add_argument(
        "--acknowledge-locked-test-use",
        action="store_true",
        help="Separately authorize an audit when the prediction set names the locked test split.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data:
        raise SystemExit("Restricted-data acknowledgement is required")
    import json

    split_name = json.loads(
        args.predictions.read_text(encoding="utf-8")
    ).get("split_name")
    if split_name == "test" and not args.acknowledge_locked_test_use:
        raise SystemExit("Locked-test acknowledgement is required")
    output = run_neurosymbolic_readiness_audit(
        prediction_path=args.predictions,
        staging_corpus_path=args.staging_corpus,
        frozen_split_path=args.frozen_split,
        output_path=args.output,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
