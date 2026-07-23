import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .splits import SUPPORTED_STRATEGIES, generate_split_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a lineage-tracked ClinicalMatcher split manifest."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/synthetic/trial_matching.json"),
    )
    parser.add_argument("--strategy", choices=SUPPORTED_STRATEGIES, required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--test-fraction", type=float, default=0.5)
    parser.add_argument("--dataset-id", default="clinicalmatcher-synthetic-v1")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    document = generate_split_manifest(
        fixture_path=args.fixture,
        strategy=args.strategy,
        seed=args.seed,
        test_fraction=args.test_fraction,
        dataset_id=args.dataset_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.strategy} split manifest "
        f"{document['manifest_sha256']} to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
