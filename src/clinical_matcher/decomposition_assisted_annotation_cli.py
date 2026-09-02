"""CLI for LLM-assisted, owner-reviewed public dev decomposition silver."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from .decomposition_assisted_annotation import (
    assisted_item_view,
    assisted_progress,
    finalize_assisted_work,
    review_assisted_draft,
    set_assisted_draft_batch,
    start_assisted_work,
    validate_assisted_work,
)
from .decomposition_dev_annotation import (
    catalog_view,
    read_object,
    replace_private_json,
    write_new_private_json,
)
from .decomposition_dev_package import verify_all


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create LLM-assisted, owner-reviewed decomposition silver."
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=Path("benchmarks/decomposition/af_decomposition_selection_1.2.0.json"),
    )
    parser.add_argument(
        "--dev-source-root",
        type=Path,
        default=Path("benchmarks/decomposition/dev_sources_1.2.0"),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("benchmarks/decomposition/dev_concept_catalog_1.1.0.json"),
    )
    parser.add_argument(
        "--issue-log",
        type=Path,
        default=Path("benchmarks/decomposition/dev_annotation_issue_log_1.0.0.json"),
    )
    parser.add_argument(
        "--package",
        type=Path,
        default=Path("benchmarks/decomposition/dev_single_annotator_package_1.0.0.json"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start")
    start.add_argument("--output", type=Path, required=True)

    for name in ("progress", "next", "show", "validate"):
        command = commands.add_parser(name)
        command.add_argument("--work", type=Path, required=True)
        if name == "show":
            command.add_argument("--criterion-id", required=True)

    catalog = commands.add_parser("catalog")
    catalog.add_argument("--query")

    draft = commands.add_parser("set-draft-batch")
    draft.add_argument("--work", type=Path, required=True)
    draft.add_argument("--drafts-json", type=Path, required=True)

    review = commands.add_parser("review")
    review.add_argument("--work", type=Path, required=True)
    review.add_argument("--criterion-id", required=True)
    review.add_argument(
        "--decision",
        choices=("accepted_unchanged", "accepted_with_edits"),
        required=True,
    )
    review.add_argument("--expression-json", type=Path)
    review.add_argument("--note")

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--work", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.add_argument("--attest-llm-assistance-disclosed", action="store_true", required=True)
    finalize.add_argument("--attest-owner-reviewed-every-item", action="store_true", required=True)
    finalize.add_argument("--attest-test-source-not-inspected", action="store_true", required=True)
    return parser


def _inputs(args: argparse.Namespace) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    catalog, issue_log, package = verify_all(
        selection_path=args.selection,
        dev_source_root=args.dev_source_root,
        catalog_path=args.catalog,
        issue_path=args.issue_log,
        package_path=args.package,
    )
    return catalog, issue_log, package


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    catalog, issue_log, package = _inputs(args)
    if args.command == "start":
        work = start_assisted_work(package, catalog)
        write_new_private_json(args.output, work)
        _print({**assisted_progress(work), "work_id": work["work_id"]})
        return 0
    if args.command == "catalog":
        _print({"entries": catalog_view(catalog, args.query)})
        return 0

    work = read_object(args.work)
    validate_assisted_work(package, catalog, work)
    if args.command == "progress":
        _print(assisted_progress(work))
        return 0
    if args.command in {"next", "show"}:
        criterion_id = (
            assisted_progress(work)["next_criterion_id"]
            if args.command == "next"
            else args.criterion_id
        )
        if criterion_id is None:
            _print({"status": "all_items_owner_reviewed"})
        else:
            _print(assisted_item_view(package, issue_log, work, criterion_id))
        return 0
    if args.command == "validate":
        _print({**assisted_progress(work), "valid": True, "work_id": work["work_id"]})
        return 0
    if args.command == "set-draft-batch":
        updated = set_assisted_draft_batch(
            package,
            catalog,
            work,
            read_object(args.drafts_json),
        )
    elif args.command == "review":
        edited = read_object(args.expression_json) if args.expression_json else None
        updated = review_assisted_draft(
            package,
            catalog,
            work,
            args.criterion_id,
            args.decision,
            edited_expression=edited,
            note=args.note,
        )
    else:
        completed = finalize_assisted_work(
            package,
            catalog,
            work,
            assistance_disclosed=args.attest_llm_assistance_disclosed,
            every_item_reviewed=args.attest_owner_reviewed_every_item,
            test_source_not_inspected=args.attest_test_source_not_inspected,
        )
        write_new_private_json(args.output, completed)
        _print({**assisted_progress(completed), "work_id": completed["work_id"]})
        return 0
    replace_private_json(args.work, updated)
    _print({**assisted_progress(updated), "work_id": updated["work_id"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
