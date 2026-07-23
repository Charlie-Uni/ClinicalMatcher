import argparse
from pathlib import Path
from typing import Dict, Optional, Sequence

from .ingestion.snapshots import (
    TrialSelection,
    build_live_trial_snapshot,
    build_trial_snapshot,
    validate_trial_snapshot,
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
    build.add_argument(
        "--query-param",
        action="append",
        type=_query_parameter,
        required=True,
        metavar="KEY=VALUE",
    )
    build.add_argument("--sort", required=True)
    build.add_argument("--page-size", type=int, default=100)
    build.add_argument("--max-studies", type=int)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--search-response-json", type=Path)
    build.add_argument("--version-json", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--snapshot-dir", type=Path, required=True)
    return parser


def _selection(args: argparse.Namespace) -> TrialSelection:
    query_parameters: Dict[str, str] = dict(args.query_param)
    query_parameters.update(
        {
            "format": "json",
            "markupFormat": "markdown",
            "countTotal": "true",
            "pageSize": str(args.page_size),
            "sort": args.sort,
        }
    )
    return TrialSelection(
        disease_domain=args.disease_domain,
        rationale=args.selection_rationale,
        query_parameters=query_parameters,
        max_studies=args.max_studies,
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
        if args.max_studies is not None:
            studies = studies[: args.max_studies]
        reported_total = response.get("totalCount")
        search_metadata = {
            "reported_total_count": (
                reported_total if isinstance(reported_total, int) else None
            ),
            "pages_fetched": 1,
            "selection_truncated": (
                isinstance(reported_total, int)
                and len(studies) < reported_total
            ),
        }
        manifest = build_trial_snapshot(
            studies=studies,
            version_payload=version,
            selection=selection,
            output_dir=args.output_dir,
            search_metadata=search_metadata,
        )
    else:
        manifest = build_live_trial_snapshot(
            client=ClinicalTrialsClient(),
            selection=selection,
            output_dir=args.output_dir,
        )
    coverage = {
        status: sum(record["status"] == status for record in manifest["records"])
        for status in ("imported", "skipped", "failed")
    }
    print(
        f"Built {manifest['snapshot_id']} at {args.output_dir}: "
        f"{coverage['imported']} imported, {coverage['skipped']} skipped, "
        f"{coverage['failed']} failed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
