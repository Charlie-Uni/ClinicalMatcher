import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .decomposition_annotation import (
    build_annotation_template,
    finalize_annotation,
    finalize_concept_catalog,
    validate_annotation,
    validate_concept_catalog,
)
from .decomposition_benchmark import (
    validate_decomposition_selection,
    write_new_json,
)


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and validate public decomposition annotation files."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def require_source(command: argparse.ArgumentParser) -> None:
        command.add_argument("--snapshot-dir", type=Path, required=True)
        command.add_argument("--selection", type=Path, required=True)

    catalog = commands.add_parser("finalize-catalog")
    require_source(catalog)
    catalog.add_argument("--input", type=Path, required=True)
    catalog.add_argument("--output", type=Path, required=True)

    template = commands.add_parser("annotation-template")
    require_source(template)
    template.add_argument("--catalog", type=Path, required=True)
    template.add_argument("--annotator-id", required=True)
    template.add_argument(
        "--annotation-mode",
        choices=("dual_independent_with_adjudication", "single_annotator"),
        required=True,
    )
    template.add_argument("--guide-version", required=True)
    template.add_argument("--guide-sha256", required=True)
    template.add_argument("--output", type=Path, required=True)

    finalize = commands.add_parser("finalize-annotation")
    require_source(finalize)
    finalize.add_argument("--catalog", type=Path, required=True)
    finalize.add_argument("--input", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)

    verify_catalog = commands.add_parser("validate-catalog")
    require_source(verify_catalog)
    verify_catalog.add_argument("--catalog", type=Path, required=True)

    verify = commands.add_parser("validate-annotation")
    require_source(verify)
    verify.add_argument("--catalog", type=Path, required=True)
    verify.add_argument("--annotation", type=Path, required=True)
    verify.add_argument("--allow-draft", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    selection = _read(args.selection)
    validate_decomposition_selection(args.snapshot_dir, selection)
    if args.command == "finalize-catalog":
        catalog = finalize_concept_catalog(selection, _read(args.input))
        write_new_json(args.output, catalog)
        print(f"Finalized concept catalog {catalog['concept_catalog_id']}.")
        return 0
    catalog = _read(args.catalog)
    validate_concept_catalog(selection, catalog)
    if args.command == "validate-catalog":
        print(f"Valid concept catalog {catalog['concept_catalog_id']}.")
        return 0
    if args.command == "annotation-template":
        document = build_annotation_template(
            selection=selection,
            catalog=catalog,
            annotator_id=args.annotator_id,
            annotation_mode=args.annotation_mode,
            annotation_guide_version=args.guide_version,
            annotation_guide_sha256=args.guide_sha256,
        )
        write_new_json(args.output, document)
        print(f"Created draft annotation {document['annotation_id']}.")
        return 0
    if args.command == "finalize-annotation":
        document = finalize_annotation(
            selection,
            catalog,
            _read(args.input),
        )
        write_new_json(args.output, document)
        print(f"Finalized annotation {document['annotation_id']}.")
        return 0
    annotation = _read(args.annotation)
    validate_annotation(
        selection,
        catalog,
        annotation,
        require_completed=not args.allow_draft,
    )
    print(f"Valid annotation {annotation['annotation_id']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
