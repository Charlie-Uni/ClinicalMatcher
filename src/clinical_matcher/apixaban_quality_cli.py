import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_quality import (
    build_apixaban_quality_reports_from_paths,
    verify_apixaban_quality_report_files,
    write_apixaban_quality_reports,
)


def _acknowledged(args: argparse.Namespace) -> None:
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify restricted and disclosure-controlled Apixaban "
            "benchmark quality reports."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--benchmark", type=Path, required=True)
    build.add_argument("--benchmark-manifest", type=Path, required=True)
    build.add_argument("--restricted-output", type=Path, required=True)
    build.add_argument("--public-output", type=Path)
    build.add_argument("--minimum-cell-size", type=int, required=True)
    build.add_argument("--governance-approval-reference")
    build.add_argument(
        "--acknowledge-governance-approved-threshold", action="store_true"
    )
    build.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )

    verify = commands.add_parser("verify")
    verify.add_argument("--restricted-report", type=Path, required=True)
    verify.add_argument("--public-report", type=Path, required=True)
    verify.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _acknowledged(args)
    if args.command == "build":
        reference = args.governance_approval_reference
        approved_ack = args.acknowledge_governance_approved_threshold
        if bool(reference) != approved_ack:
            raise ValueError(
                "A governance approval reference and explicit threshold "
                "acknowledgement must be supplied together"
            )
        restricted, public = build_apixaban_quality_reports_from_paths(
            args.benchmark,
            args.benchmark_manifest,
            minimum_cell_size=args.minimum_cell_size,
            governance_approval_reference=reference,
        )
        restricted_path, public_path = write_apixaban_quality_reports(
            restricted,
            public,
            args.restricted_output,
            args.public_output,
        )
        totals = restricted["totals"]
        control = public["disclosure_control"]
        print(
            "Built Apixaban quality reports: "
            f"{totals['patient_count']} patients, "
            f"{totals['question_count']} questions, "
            f"{totals['assessment_count']} assessments, "
            f"{totals['source_anomaly_count']} preserved anomalies."
        )
        print(
            "Public projection: "
            f"{control['suppressed_cell_count']} suppressed cells; "
            f"governance_status={control['governance_status']}."
        )
        print(f"Local outputs: {restricted_path}, {public_path}")
        return 0

    restricted, public = verify_apixaban_quality_report_files(
        args.restricted_report, args.public_report
    )
    print(
        "Verified Apixaban quality reports: "
        f"{restricted['totals']['assessment_count']} assessments; "
        "public governance_status="
        f"{public['disclosure_control']['governance_status']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
