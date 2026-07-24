import argparse
import json
from pathlib import Path
from typing import Dict, Optional, Sequence

from .capacity import validate_capacity_plan
from .ingestion.snapshots import (
    build_benchmark_trial_snapshot,
    build_live_benchmark_trial_snapshot,
    validate_trial_snapshot,
)
from .ingestion.trial_selection import (
    ReproducibleTrialSelection,
    TrialFilterPolicy,
)
from .ingestion.trials import ClinicalTrialsClient, load_json


def _query_parameter(value: str) -> tuple:
    if "=" not in value:
        raise argparse.ArgumentTypeError("query parameters must use KEY=VALUE")
    key, parameter_value = value.split("=", 1)
    if not key or not parameter_value:
        raise argparse.ArgumentTypeError("query parameter key/value cannot be empty")
    if key == "pageToken":
        raise argparse.ArgumentTypeError("pageToken is transient")
    return key, parameter_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify an immutable ClinicalTrials.gov trial snapshot. "
            "Live API access is used only while building, never while loading "
            "evaluation inputs."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--disease-domain", required=True)
    build.add_argument("--selection-rationale", required=True)
    build.add_argument("--query-condition", required=True)
    build.add_argument(
        "--query-param",
        action="append",
        type=_query_parameter,
        metavar="KEY=VALUE",
    )
    build.add_argument("--study-type", action="append", required=True)
    build.add_argument("--overall-status", action="append", required=True)
    build.add_argument("--first-posted-from", required=True)
    build.add_argument("--first-posted-to", required=True)
    build.add_argument("--capacity-plan", type=Path, required=True)
    build.add_argument("--page-size", type=int, default=1000)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--search-response-json", type=Path)
    build.add_argument("--version-json", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--snapshot-dir", type=Path, required=True)
    return parser


def _selection(args: argparse.Namespace) -> ReproducibleTrialSelection:
    pairs = args.query_param or ()
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("Duplicate --query-param keys are not allowed")
    query_parameters: Dict[str, str] = dict(pairs)
    reserved = {
        "query.cond",
        "filter.overallStatus",
        "format",
        "markupFormat",
        "countTotal",
        "pageSize",
        "sort",
    }
    overlap = reserved & set(query_parameters)
    if overlap:
        raise ValueError(
            "Reserved query parameters must use dedicated CLI arguments: "
            + ", ".join(sorted(overlap))
        )
    query_parameters.update(
        {
            "query.cond": args.query_condition,
            "filter.overallStatus": "|".join(sorted(set(args.overall_status))),
            "format": "json",
            "markupFormat": "markdown",
            "countTotal": "true",
            "pageSize": str(args.page_size),
        }
    )
    return ReproducibleTrialSelection(
        disease_domain=args.disease_domain,
        rationale=args.selection_rationale,
        query_parameters=query_parameters,
        filters=TrialFilterPolicy(
            study_types=tuple(args.study_type),
            overall_statuses=tuple(args.overall_status),
            require_eligibility_text=True,
            first_posted_from=args.first_posted_from,
            first_posted_to=args.first_posted_to,
        ),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify":
        manifest = validate_trial_snapshot(args.snapshot_dir)
        imported = sum(
            record["status"] == "imported" for record in manifest["records"]
        )
        print(
            f"Verified {manifest['snapshot_id']} with "
            f"{len(manifest['records'])} candidates and {imported} imports"
        )
        return 0

    selection = _selection(args)
    capacity_plan = json.loads(args.capacity_plan.read_text(encoding="utf-8"))
    validate_capacity_plan(capacity_plan)
    if not capacity_plan["snapshot_design_allowed"]:
        raise ValueError(
            "Capacity plan must be pilot-validated with a selected design"
        )
    if bool(args.search_response_json) != bool(args.version_json):
        raise ValueError(
            "--search-response-json and --version-json must be supplied together"
        )
    if args.search_response_json:
        response = load_json(args.search_response_json)
        version = load_json(args.version_json)
        studies = response.get("studies")
        if not isinstance(studies, list):
            raise ValueError("Offline search response must contain studies[]")
        reported_total = response.get("totalCount")
        if not isinstance(reported_total, int):
            raise ValueError("Offline search response must contain totalCount")
        pages_fetched = response.get("pagesFetched", 1)
        if not isinstance(pages_fetched, int) or pages_fetched < 1:
            raise ValueError("Offline pagesFetched must be a positive integer")
        manifest = build_benchmark_trial_snapshot(
            studies=studies,
            version_payload=version,
            registry_reported_total_count=reported_total,
            pages_fetched=pages_fetched,
            selection=selection,
            capacity_plan=capacity_plan,
            output_dir=args.output_dir,
        )
    else:
        manifest = build_live_benchmark_trial_snapshot(
            client=ClinicalTrialsClient(),
            selection=selection,
            capacity_plan=capacity_plan,
            output_dir=args.output_dir,
        )
    coverage = {
        status: sum(record["status"] == status for record in manifest["records"])
        for status in ("imported", "skipped", "failed")
    }
    print(
        f"Built {manifest['snapshot_id']} at {args.output_dir}: "
        f"{coverage['imported']} imported, {coverage['skipped']} skipped, "
        f"{coverage['failed']} failed from "
        f"{manifest['search']['registry_reported_total_count']} registry hits"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
