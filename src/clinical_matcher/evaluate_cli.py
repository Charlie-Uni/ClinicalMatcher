import argparse
from pathlib import Path
from typing import Optional, Sequence

from .evaluation_runner import evaluate_fixture_run
from .reporting import write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a ClinicalMatcher split and write JSON/Markdown."
    )
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("-k", type=int, default=2)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument(
        "--model-id",
        action="append",
        dest="model_ids",
    )
    parser.add_argument(
        "--prompt-version",
        action="append",
        dest="prompt_versions",
    )
    parser.add_argument(
        "--index-fingerprint",
        default="not-applicable:no-index",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = evaluate_fixture_run(
        fixture_path=args.fixture,
        manifest_path=args.manifest,
        split_name=args.split,
        k=args.k,
        bootstrap_samples=args.bootstrap_samples,
        model_ids=args.model_ids
        or ("deterministic-neuro-symbolic-baseline@1",),
        prompt_versions=args.prompt_versions
        or ("not-applicable:deterministic-baseline",),
        index_fingerprint=args.index_fingerprint,
    )
    json_path, markdown_path = write_report(report, args.output_dir)
    print(f"Wrote machine-readable report: {json_path}")
    print(f"Wrote human-readable report: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
