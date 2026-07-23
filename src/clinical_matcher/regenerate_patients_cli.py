import argparse
from pathlib import Path
from typing import Optional, Sequence

from .ingestion.patients import (
    regenerate_normalized_patient_source,
    write_regenerated_patient_source,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and regenerate a normalized restricted patient source "
            "entirely in the local environment."
        )
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only",
        action="store_true",
        help=(
            "Required acknowledgement that input/output stay in the "
            "authorized local environment."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing normalized output and manifest.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )
    if args.input.resolve() == args.output.resolve():
        raise ValueError(
            "Input and output must differ so the source remains preserved"
        )
    source, manifest = regenerate_normalized_patient_source(
        input_path=args.input,
    )
    output_path, manifest_path = write_regenerated_patient_source(
        source,
        manifest,
        args.output,
        overwrite=args.overwrite,
    )
    print(
        f"Validated and regenerated {manifest['patient_count']} patient(s) "
        f"to local restricted output {output_path}"
    )
    print(f"Wrote aggregate regeneration manifest {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
