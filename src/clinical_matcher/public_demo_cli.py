import argparse
import json
from pathlib import Path
import sys
from typing import Optional, Sequence

from .public_demo import (
    PublicDemoError,
    load_and_build_public_demo,
    render_public_demo_markdown,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline, CPU-only ClinicalMatcher synthetic demo."
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/synthetic/trial_matching.json"),
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = load_and_build_public_demo(args.fixture)
    except PublicDemoError:
        print(
            "Synthetic demo refused the input; no report was emitted. "
            "Use the validated public synthetic fixture.",
            file=sys.stderr,
        )
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_public_demo_markdown(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
