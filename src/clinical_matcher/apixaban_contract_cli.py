import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_contract import (
    load_question_catalog,
    validate_fact_assessment,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen Apixaban note-grounded fact contract and "
            "an optional fact-assessment JSON document."
        )
    )
    parser.add_argument(
        "assessment",
        type=Path,
        nargs="?",
        help="Optional fact-assessment JSON document to validate.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = load_question_catalog()
    counts = {"boolean": 0, "numeric": 0}
    for question in catalog["questions"]:
        counts[question["question_type"]] += 1
    print(
        "Valid Apixaban question catalog "
        f"{catalog['catalog_version']}: {len(catalog['questions'])} questions "
        f"({counts['boolean']} boolean, {counts['numeric']} numeric)."
    )
    if args.assessment is not None:
        document = json.loads(args.assessment.read_text(encoding="utf-8"))
        validate_fact_assessment(document, catalog)
        print(
            "Valid Apixaban fact assessment "
            f"{document['fact_assessment_version']}: "
            f"{document['assessment_id']}."
        )
    return 0
