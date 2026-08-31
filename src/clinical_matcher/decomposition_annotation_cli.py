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
from .decomposition_gold import (
    build_adjudicated_gold,
    build_adjudication_template,
    build_single_annotator_gold,
    finalize_adjudication,
    validate_adjudication,
    validate_gold,
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

    def require_catalog_and_annotations(command: argparse.ArgumentParser) -> None:
        require_source(command)
        command.add_argument("--catalog", type=Path, required=True)
        command.add_argument(
            "--annotation",
            type=Path,
            action="append",
            required=True,
            help="Repeat once per completed source annotation.",
        )

    adjudication_template = commands.add_parser("adjudication-template")
    require_catalog_and_annotations(adjudication_template)
    adjudication_template.add_argument(
        "--adjudicator-id", action="append", required=True
    )
    adjudication_template.add_argument("--output", type=Path, required=True)

    adjudication_finalize = commands.add_parser("finalize-adjudication")
    require_catalog_and_annotations(adjudication_finalize)
    adjudication_finalize.add_argument("--input", type=Path, required=True)
    adjudication_finalize.add_argument("--output", type=Path, required=True)

    adjudication_validate = commands.add_parser("validate-adjudication")
    require_catalog_and_annotations(adjudication_validate)
    adjudication_validate.add_argument(
        "--adjudication", type=Path, required=True
    )
    adjudication_validate.add_argument("--allow-draft", action="store_true")

    gold_finalize = commands.add_parser("finalize-gold")
    require_catalog_and_annotations(gold_finalize)
    gold_finalize.add_argument("--adjudication", type=Path)
    gold_finalize.add_argument("--downgrade-decision-version")
    gold_finalize.add_argument("--downgrade-decision-sha256")
    gold_finalize.add_argument("--output", type=Path, required=True)

    gold_validate = commands.add_parser("validate-gold")
    require_catalog_and_annotations(gold_validate)
    gold_validate.add_argument("--gold", type=Path, required=True)
    gold_validate.add_argument("--adjudication", type=Path)
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
    if args.command in {
        "adjudication-template",
        "finalize-adjudication",
        "validate-adjudication",
        "finalize-gold",
        "validate-gold",
    }:
        annotations = [_read(path) for path in args.annotation]
        if args.command == "adjudication-template":
            document = build_adjudication_template(
                selection,
                catalog,
                annotations,
                args.adjudicator_id,
            )
            write_new_json(args.output, document)
            print(f"Created draft adjudication {document['adjudication_id']}.")
            return 0
        if args.command == "finalize-adjudication":
            document = finalize_adjudication(
                selection,
                catalog,
                annotations,
                _read(args.input),
            )
            write_new_json(args.output, document)
            print(f"Finalized adjudication {document['adjudication_id']}.")
            return 0
        if args.command == "validate-adjudication":
            document = _read(args.adjudication)
            validate_adjudication(
                selection,
                catalog,
                annotations,
                document,
                require_completed=not args.allow_draft,
            )
            print(f"Valid adjudication {document['adjudication_id']}.")
            return 0
        adjudication = (
            _read(args.adjudication) if args.adjudication is not None else None
        )
        if args.command == "finalize-gold":
            if adjudication is not None:
                if (
                    args.downgrade_decision_version is not None
                    or args.downgrade_decision_sha256 is not None
                ):
                    raise ValueError(
                        "Dual adjudicated gold cannot take downgrade-decision flags"
                    )
                document = build_adjudicated_gold(
                    selection, catalog, annotations, adjudication
                )
            else:
                if len(annotations) != 1:
                    raise ValueError(
                        "Single-annotator gold requires exactly one annotation"
                    )
                document = build_single_annotator_gold(
                    selection,
                    catalog,
                    annotations[0],
                    downgrade_decision_version=(
                        args.downgrade_decision_version or ""
                    ),
                    downgrade_decision_sha256=(
                        args.downgrade_decision_sha256 or ""
                    ),
                )
            write_new_json(args.output, document)
            print(f"Finalized decomposition gold {document['gold_id']}.")
            return 0
        document = _read(args.gold)
        validate_gold(
            selection,
            catalog,
            annotations,
            document,
            adjudication=adjudication,
        )
        print(f"Valid decomposition gold {document['gold_id']}.")
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
