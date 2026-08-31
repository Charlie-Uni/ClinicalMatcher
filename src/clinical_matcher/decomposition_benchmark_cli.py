import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .decomposition_benchmark import (
    build_decomposition_selection,
    validate_decomposition_selection,
    write_new_json,
)
from .decomposition_overlap import (
    build_overlap_diagnostic_from_path,
    validate_overlap_diagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the frozen public ClinicalTrials.gov "
            "criteria-decomposition selection manifest."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--snapshot-dir", type=Path, required=True)
    select.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--snapshot-dir", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    diagnose = commands.add_parser("diagnose-overlap")
    diagnose.add_argument("--manifest", type=Path, required=True)
    diagnose.add_argument("--output", type=Path, required=True)
    verify_overlap = commands.add_parser("verify-overlap")
    verify_overlap.add_argument("--manifest", type=Path, required=True)
    verify_overlap.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "select":
        document = build_decomposition_selection(args.snapshot_dir)
        write_new_json(args.output, document)
        print(
            f"Wrote {document['selection_manifest_id']} with "
            f"{document['counts']['selected_count']} selected criteria."
        )
        return 0
    if args.command == "verify":
        document = json.loads(args.manifest.read_text(encoding="utf-8"))
        validate_decomposition_selection(args.snapshot_dir, document)
        print(
            f"Valid decomposition selection {document['selection_manifest_id']}."
        )
        return 0
    if args.command == "diagnose-overlap":
        document = build_overlap_diagnostic_from_path(args.manifest)
        write_new_json(args.output, document)
        print(
            f"Wrote {document['report_id']} after exhaustive comparison of "
            f"{document['counts']['cross_split_pairs_evaluated']} pairs."
        )
        return 0
    document = json.loads(args.report.read_text(encoding="utf-8"))
    validate_overlap_diagnostic(args.manifest, document)
    print(f"Valid overlap diagnostic {document['report_id']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
