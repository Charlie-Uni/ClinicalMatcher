import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .gold_readiness import (
    GoldAuditCounts,
    assert_benchmark_ready,
    build_gold_readiness_report,
)
from .ingestion.snapshots import validate_trial_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a PHI-free aggregate gold readiness report for a frozen "
            "trial snapshot."
        )
    )
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--gold-source-description", required=True)
    parser.add_argument("--patient-count", type=int, default=0)
    parser.add_argument("--trial-count", type=int, default=0)
    parser.add_argument("--expected-patient-trial-pairs", type=int, default=0)
    parser.add_argument(
        "--adjudicated-patient-trial-pairs", type=int, default=0
    )
    parser.add_argument("--expected-criterion-units", type=int, default=0)
    parser.add_argument("--adjudicated-criterion-units", type=int, default=0)
    parser.add_argument("--minimum-annotators-per-unit", type=int, default=0)
    parser.add_argument("--unresolved-adjudications", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit with an error unless the aggregate gold gate passes.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        raise ValueError(f"Refusing to overwrite existing report: {args.output}")
    manifest = validate_trial_snapshot(args.snapshot_dir)
    counts = GoldAuditCounts(
        patient_count=args.patient_count,
        trial_count=args.trial_count,
        expected_patient_trial_pairs=args.expected_patient_trial_pairs,
        adjudicated_patient_trial_pairs=(
            args.adjudicated_patient_trial_pairs
        ),
        expected_criterion_units=args.expected_criterion_units,
        adjudicated_criterion_units=args.adjudicated_criterion_units,
        minimum_annotators_per_unit=args.minimum_annotators_per_unit,
        unresolved_adjudications=args.unresolved_adjudications,
    )
    report = build_gold_readiness_report(
        snapshot_manifest=manifest,
        counts=counts,
        gold_source_description=args.gold_source_description,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.require_ready:
        assert_benchmark_ready(report)
    print(
        f"Gold status for {report['snapshot_id']}: {report['status']} "
        f"({len(report['blocking_gaps'])} blocking gaps)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
