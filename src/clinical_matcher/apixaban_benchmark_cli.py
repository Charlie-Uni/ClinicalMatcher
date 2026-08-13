import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_benchmark import (
    build_apixaban_benchmark_from_paths,
    verify_apixaban_benchmark_files,
    write_apixaban_benchmark,
)


def _acknowledged(args: argparse.Namespace) -> None:
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the restricted local Apixaban fact benchmark."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--staging-corpus", type=Path, required=True)
    build.add_argument("--import-manifest", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )

    verify = commands.add_parser("verify")
    verify.add_argument("--benchmark", type=Path, required=True)
    verify.add_argument("--manifest", type=Path)
    verify.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _acknowledged(args)
    if args.command == "build":
        benchmark, manifest = build_apixaban_benchmark_from_paths(
            args.staging_corpus, args.import_manifest
        )
        benchmark_path, manifest_path = write_apixaban_benchmark(
            benchmark, manifest, args.output
        )
        counts = manifest["counts"]
        print(
            "Built restricted Apixaban fact benchmark: "
            f"{counts['patient_count']} patients, "
            f"{counts['question_count']} questions, "
            f"{counts['assessment_count']} assessments, "
            f"{counts['source_anomaly_count']} preserved anomalies."
        )
        print(f"Benchmark SHA-256: {manifest['output']['benchmark_sha256']}")
        print(f"Local outputs: {benchmark_path}, {manifest_path}")
        return 0

    manifest_path = args.manifest or args.benchmark.with_name(
        f"{args.benchmark.stem}.manifest.json"
    )
    counts = verify_apixaban_benchmark_files(
        args.benchmark, manifest_path
    )
    print(
        "Verified restricted Apixaban fact benchmark: "
        f"{counts['patient_count']} patients, "
        f"{counts['question_count']} questions, "
        f"{counts['assessment_count']} assessments."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
