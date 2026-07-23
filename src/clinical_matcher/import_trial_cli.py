import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .ingestion.trials import (
    ClinicalTrialsClient,
    load_json,
    normalize_study,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import public ClinicalTrials.gov eligibility criteria with "
            "versioned provenance and source spans."
        )
    )
    parser.add_argument("--nct-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--study-json", type=Path)
    parser.add_argument("--version-json", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if bool(args.study_json) != bool(args.version_json):
        raise ValueError(
            "--study-json and --version-json must be supplied together"
        )
    if args.study_json:
        study = load_json(args.study_json)
        version = load_json(args.version_json)
    else:
        study, version = ClinicalTrialsClient().fetch(args.nct_id)

    document = normalize_study(study, version)
    if document["nct_id"] != args.nct_id.upper():
        raise ValueError("Fetched study NCT ID does not match --nct-id")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Imported {document['nct_id']} record "
        f"{document['source_record_version']} with "
        f"{len(document['criteria'])} criteria to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
