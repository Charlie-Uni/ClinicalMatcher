import argparse
import shlex
import sys
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_calibration import (
    build_apixaban_calibration_reservation_from_path,
    write_apixaban_calibration_reservation,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reserve a deterministic calibration-only patient subset from a "
            "frozen restricted Apixaban train split."
        )
    )
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument(
        "--calibration-patient-count", type=int, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )
    command_argv = list(
        sys.argv
        if argv is None
        else ["clinical-matcher-reserve-apixaban-calibration", *argv]
    )
    document = build_apixaban_calibration_reservation_from_path(
        args.frozen_split,
        calibration_patient_count=args.calibration_patient_count,
        generation_command=shlex.join(command_argv),
    )
    write_apixaban_calibration_reservation(document, args.output)
    print(
        "Reserved restricted Apixaban training patients: "
        f"train_fit={document['partitions']['train_fit']['patient_count']}, "
        f"calibration_only={document['partitions']['calibration_only']['patient_count']}."
    )
    print(f"Reservation manifest SHA-256: {document['manifest_sha256']}")
    print(f"Owner-only local output: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
