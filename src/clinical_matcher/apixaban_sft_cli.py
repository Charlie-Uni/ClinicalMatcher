import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .apixaban_sft import build_apixaban_sft_export, write_apixaban_sft_export
from .apixaban_sft_length import load_frozen_apixaban_sft_tokenizer
from .ingestion.patients import assert_restricted_local_path


def _load_private_json(path: Path) -> Dict[str, Any]:
    assert_restricted_local_path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError(f"Restricted SFT input is not owner-only: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Restricted SFT input must be a JSON object: {path}")
    return document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export owner-only canonical, MLX, and MedicalGPT-compatible "
            "Apixaban SFT JSONL files"
        )
    )
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--calibration-reservation", type=Path, required=True)
    parser.add_argument("--input-plan", type=Path, required=True)
    parser.add_argument("--accepted-d-silver", type=Path, required=True)
    parser.add_argument("--accepted-e-silver", type=Path)
    parser.add_argument("--tokenizer-directory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--i-understand-restricted-data-stays-local",
        action="store_true",
        help="Required acknowledgement; this command reads and writes restricted data",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.i_understand_restricted_data_stays_local:
        raise ValueError(
            "Refusing SFT export without explicit acknowledgement that "
            "restricted data and derived artifacts stay local"
        )
    assert_restricted_local_path(args.output_dir)
    e_silver = (
        _load_private_json(args.accepted_e_silver)
        if args.accepted_e_silver
        else None
    )
    result = build_apixaban_sft_export(
        _load_private_json(args.staging_corpus),
        _load_private_json(args.benchmark),
        _load_private_json(args.frozen_split),
        _load_private_json(args.calibration_reservation),
        _load_private_json(args.input_plan),
        _load_private_json(args.accepted_d_silver),
        e_silver,
        tokenizer=load_frozen_apixaban_sft_tokenizer(args.tokenizer_directory),
        generation_command="clinical-matcher-export-apixaban-sft",
    )
    paths = write_apixaban_sft_export(*result, args.output_dir)
    manifest = result[-1]
    print(
        "Exported "
        f"{manifest['counts']['included_row_count']} owner-only SFT rows; "
        f"excluded {manifest['counts']['excluded_known_without_silver_count']} "
        "known rows without accepted silver."
    )
    print("Local outputs: " + ", ".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
