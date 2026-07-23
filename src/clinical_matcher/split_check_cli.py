import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .splits import (
    SemanticNearDuplicate,
    assert_no_split_leakage,
    load_split_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assert exact and semantic split isolation."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--semantic-pairs",
        type=Path,
        help=(
            "Optional local JSON array of dimension/left_id/right_id/"
            "similarity records produced by an embedding scan."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_split_manifest(args.manifest)
    pairs = ()
    if args.semantic_pairs:
        raw = json.loads(args.semantic_pairs.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Semantic pair file must contain a JSON array")
        pairs = tuple(SemanticNearDuplicate(**item) for item in raw)
    assert_no_split_leakage(manifest, pairs)
    print(
        f"Split isolation passed for {manifest.strategy}: "
        f"{', '.join(manifest.isolated_dimensions)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
