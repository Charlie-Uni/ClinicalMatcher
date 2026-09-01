"""CLI for model-free owner annotation of the frozen dev package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from .decomposition_dev_annotation import (
    catalog_view,
    finalize_work,
    item_view,
    progress,
    read_object,
    replace_private_json,
    set_expression,
    start_work,
    validate_work,
    write_new_private_json,
)
from .decomposition_dev_package import verify_all


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manually annotate the frozen public dev decomposition package."
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
        default=Path(
            "benchmarks/decomposition/dev_single_annotator_package_1.0.0.json"
        ),
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

    update = commands.add_parser("set-expression")
    update.add_argument("--work", type=Path, required=True)
    update.add_argument("--criterion-id", required=True)
    update.add_argument("--expression-json", type=Path, required=True)

    clear = commands.add_parser("clear-expression")
    clear.add_argument("--work", type=Path, required=True)
    clear.add_argument("--criterion-id", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--work", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    finalize.add_argument(
        "--attest-human-no-model-output", action="store_true", required=True
    )
    finalize.add_argument(
        "--attest-test-source-not-inspected", action="store_true", required=True
    )
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
        work = start_work(package, catalog)
        write_new_private_json(args.output, work)
        _print({**progress(work), "work_id": work["work_id"]})
        return 0
    if args.command == "catalog":
        _print({"entries": catalog_view(catalog, args.query)})
        return 0

    work = read_object(args.work)
    validate_work(package, catalog, work)
    if args.command == "progress":
        _print(progress(work))
        return 0
    if args.command in {"next", "show"}:
        criterion_id = (
            progress(work)["next_criterion_id"]
            if args.command == "next"
            else args.criterion_id
        )
        if criterion_id is None:
            _print({"status": "all_items_have_expressions"})
        else:
            _print(item_view(package, issue_log, criterion_id))
        return 0
    if args.command == "validate":
        _print({**progress(work), "valid": True, "work_id": work["work_id"]})
        return 0
    if args.command == "set-expression":
        expression = read_object(args.expression_json)
        updated = set_expression(
            package, catalog, work, args.criterion_id, expression
        )
        replace_private_json(args.work, updated)
        _print({**progress(updated), "work_id": updated["work_id"]})
        return 0
    if args.command == "clear-expression":
        updated = set_expression(package, catalog, work, args.criterion_id, None)
        replace_private_json(args.work, updated)
        _print({**progress(updated), "work_id": updated["work_id"]})
        return 0
    completed = finalize_work(
        package,
        catalog,
        work,
        human_authorship_attested=args.attest_human_no_model_output,
        test_source_not_inspected_attested=args.attest_test_source_not_inspected,
    )
    write_new_private_json(args.output, completed)
    _print({**progress(completed), "work_id": completed["work_id"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
