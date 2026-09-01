"""CLI for freezing and verifying the remediated dev annotation inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .decomposition_dev_package import build_all, verify_all


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify the public dev-only decomposition package."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--selection", type=Path, required=True)
        child.add_argument("--dev-source-root", type=Path, required=True)
        child.add_argument("--catalog", type=Path, required=True)
        child.add_argument("--issue-log", type=Path, required=True)
        child.add_argument("--package", type=Path, required=True)
        if command == "build":
            child.add_argument("--catalog-draft", type=Path, required=True)
            child.add_argument("--issue-draft", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        catalog, issue_log, package = build_all(
            selection_path=args.selection,
            dev_source_root=args.dev_source_root,
            catalog_draft_path=args.catalog_draft,
            issue_draft_path=args.issue_draft,
            catalog_output=args.catalog,
            issue_output=args.issue_log,
            package_output=args.package,
        )
    else:
        catalog, issue_log, package = verify_all(
            selection_path=args.selection,
            dev_source_root=args.dev_source_root,
            catalog_path=args.catalog,
            issue_path=args.issue_log,
            package_path=args.package,
        )
    print(
        json.dumps(
            {
                "catalog_id": catalog["concept_catalog_id"],
                "issue_log_id": issue_log["issue_log_id"],
                "package_id": package["package_id"],
                "item_count": len(package["items"]),
                "issue_count": len(issue_log["issues"]),
                "annotation_status": package["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
