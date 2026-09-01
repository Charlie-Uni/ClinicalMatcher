import argparse
from pathlib import Path
from typing import Optional, Sequence

from .decomposition_test_remediation import (
    build_remediated_selection,
    validate_remediated_selection,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the owner-approved headless P5D test-source "
            "replacement. Criterion text is never printed."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--predecessor", type=Path, required=True)
        command.add_argument("--source-audit", type=Path, required=True)
        command.add_argument("--source-snapshot-root", type=Path, required=True)
        command.add_argument("--dev-source-root", type=Path, required=True)
        command.add_argument("--test-source-root", type=Path, required=True)
        command.add_argument("--selection", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    common = {
        "predecessor_path": args.predecessor,
        "source_audit_path": args.source_audit,
        "source_snapshot_root": args.source_snapshot_root,
        "dev_source_root": args.dev_source_root,
        "test_source_root": args.test_source_root,
    }
    if args.command == "build":
        document = build_remediated_selection(
            **common,
            selection_output=args.selection,
        )
        print(
            f"Built {document['selection_manifest_id']}: preserved "
            f"{document['counts']['preserved_dev_count']} dev items, retired "
            f"{document['counts']['retired_test_count']} old test items, and "
            f"selected {document['counts']['replacement_test_count']} "
            "replacement test items. No criterion text was displayed."
        )
        return 0
    document = validate_remediated_selection(
        **common,
        selection_path=args.selection,
    )
    print(
        f"Verified {document['selection_manifest_id']} without displaying "
        "criterion text."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
