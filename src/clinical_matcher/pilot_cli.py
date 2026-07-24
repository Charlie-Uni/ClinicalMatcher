import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .ingestion.patients import assert_restricted_local_path
from .pilot import (
    build_adjudication_template,
    build_annotation_template,
    build_pilot_summary,
    finalize_pilot_manifest,
    validate_adjudication,
    validate_annotation,
    validate_pilot_manifest,
    validate_pilot_summary,
)


def _read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: Dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _protect_restricted(
    manifest: Dict[str, Any],
    *paths: Path,
) -> None:
    if manifest.get("source", {}).get("access_policy") != "restricted_local":
        return
    for path in paths:
        assert_restricted_local_path(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create and validate independent patient-trial timing-pilot "
            "records without exporting row-level clinical data."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    finalize = commands.add_parser(
        "finalize-manifest",
        help="Hash and validate a pilot manifest specification.",
    )
    finalize.add_argument("--input", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)

    templates = commands.add_parser(
        "templates",
        help="Create one blinded draft file per assigned annotator.",
    )
    templates.add_argument("--manifest", type=Path, required=True)
    templates.add_argument("--output-dir", type=Path, required=True)

    adjudicate = commands.add_parser(
        "adjudication-template",
        help="Compare two completed annotations and create a draft.",
    )
    adjudicate.add_argument("--manifest", type=Path, required=True)
    adjudicate.add_argument(
        "--annotation",
        type=Path,
        action="append",
        required=True,
    )
    adjudicate.add_argument(
        "--adjudicator-id",
        action="append",
        required=True,
    )
    adjudicate.add_argument("--output", type=Path, required=True)

    summarize = commands.add_parser(
        "summarize",
        help="Emit an ID-free aggregate from completed local records.",
    )
    summarize.add_argument("--manifest", type=Path, required=True)
    summarize.add_argument(
        "--annotation",
        type=Path,
        action="append",
        required=True,
    )
    summarize.add_argument("--adjudication", type=Path, required=True)
    summarize.add_argument("--output", type=Path, required=True)

    validate = commands.add_parser(
        "validate-summary",
        help="Validate a PHI-free aggregate pilot summary.",
    )
    validate.add_argument("--summary", type=Path, required=True)
    return parser


def _require_two(paths: Sequence[Path]) -> None:
    if len(paths) != 2:
        raise ValueError("Exactly two --annotation files are required")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-summary":
        summary = _read(args.summary)
        validate_pilot_summary(summary)
        print(
            "Valid aggregate pilot summary: "
            f"{summary['counts']['patient_trial_unit_count']} unit(s)."
        )
        return 0

    source_path = (
        args.input if args.command == "finalize-manifest" else args.manifest
    )
    manifest = _read(source_path)
    if args.command == "finalize-manifest":
        _protect_restricted(manifest, args.input, args.output)
        finalized = finalize_pilot_manifest(manifest)
        _write(args.output, finalized)
        print(f"Finalized pilot manifest {finalized['manifest_sha256']}.")
        return 0

    validate_pilot_manifest(manifest)
    if args.command == "templates":
        output_paths = [
            args.output_dir / f"annotation-{index}.json"
            for index in range(1, 3)
        ]
        _protect_restricted(manifest, args.manifest, *output_paths)
        if any(path.exists() for path in output_paths):
            raise FileExistsError(
                "Refusing to overwrite an existing annotation template"
            )
        for annotator_id, path in zip(
            manifest["annotator_ids"],
            output_paths,
        ):
            _write(path, build_annotation_template(manifest, annotator_id))
        print(f"Created two blinded templates in {args.output_dir}.")
        return 0

    _require_two(args.annotation)
    annotations = [_read(path) for path in args.annotation]
    protected_paths = [args.manifest, *args.annotation]
    if args.command == "adjudication-template":
        protected_paths.append(args.output)
        _protect_restricted(manifest, *protected_paths)
        document = build_adjudication_template(
            manifest,
            annotations,
            args.adjudicator_id,
        )
        _write(args.output, document)
        print(f"Created adjudication template at {args.output}.")
        return 0

    _protect_restricted(
        manifest,
        *protected_paths,
        args.adjudication,
        args.output,
    )
    adjudication = _read(args.adjudication)
    for annotation in annotations:
        validate_annotation(manifest, annotation, require_completed=True)
    validate_adjudication(
        manifest,
        annotations,
        adjudication,
        require_completed=True,
    )
    summary = build_pilot_summary(manifest, annotations, adjudication)
    _write(args.output, summary)
    print(
        f"Built aggregate summary {summary['summary_sha256']} from "
        f"{summary['counts']['patient_trial_unit_count']} unit(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
