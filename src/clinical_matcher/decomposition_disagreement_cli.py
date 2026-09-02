import argparse
from pathlib import Path
from typing import Optional, Sequence

from .decomposition_disagreement import publish_disagreement_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish the frozen dev-only decomposition disagreement package."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-retained-dev-negative-result",
        action="store_true",
        help="Acknowledge the narrow dev-only retention and locked-test boundary.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_retained_dev_negative_result:
        raise ValueError("--acknowledge-retained-dev-negative-result is required")
    outputs = publish_disagreement_package(
        args.repo_root.resolve(),
        args.source_dir.resolve(),
        args.output_dir.resolve(),
    )
    for name, path in outputs.items():
        print(f"Wrote {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
