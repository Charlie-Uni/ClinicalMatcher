import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .apixaban_sft_length import (
    build_apixaban_sft_length_report,
    load_frozen_apixaban_sft_tokenizer,
    write_apixaban_sft_length_outputs,
)
from .ingestion.patients import assert_restricted_local_path


def _load_private_json(path: Path) -> Dict[str, Any]:
    assert_restricted_local_path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError(f"Restricted length input is not owner-only: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Restricted length input must be a JSON object: {path}")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build an owner-only aggregate SFT length report and, when one "
            "approved tier fits every train-fit row, its bound input plan"
        )
    )
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--calibration-reservation", type=Path, required=True)
    parser.add_argument("--tokenizer-directory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--i-understand-restricted-data-stays-local",
        action="store_true",
        help="Required acknowledgement; inputs and derived aggregates stay local",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.i_understand_restricted_data_stays_local:
        raise ValueError(
            "Refusing length analysis without explicit acknowledgement that "
            "restricted data and derived artifacts stay local"
        )
    assert_restricted_local_path(args.output_dir)
    result = build_apixaban_sft_length_report(
        _load_private_json(args.staging_corpus),
        _load_private_json(args.frozen_split),
        _load_private_json(args.calibration_reservation),
        load_frozen_apixaban_sft_tokenizer(args.tokenizer_directory),
        generation_command="clinical-matcher-apixaban-sft-length",
    )
    paths = write_apixaban_sft_length_outputs(*result, args.output_dir)
    print(
        "Owner-only SFT length artifacts written locally: "
        + ", ".join(map(str, paths))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
