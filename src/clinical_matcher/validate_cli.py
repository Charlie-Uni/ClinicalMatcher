import argparse
from pathlib import Path
from typing import Optional, Sequence

from .fixture import load_fixture


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate ClinicalMatcher JSON Schema and semantic links."
    )
    parser.add_argument("document", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    fixture = load_fixture(args.document)
    print(
        f"Valid ClinicalMatcher {fixture.schema_version} document: "
        f"{len(fixture.patients)} patient(s), {len(fixture.trials)} trial(s)."
    )
    return 0
