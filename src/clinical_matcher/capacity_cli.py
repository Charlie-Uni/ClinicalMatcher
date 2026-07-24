import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .capacity import CapacityAssumptions, build_capacity_plan
from .pilot import validate_pilot_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reverse-plan patient-trial benchmark size from dual-annotation "
            "and adjudication capacity."
        )
    )
    parser.add_argument(
        "--pilot-summary",
        type=Path,
        help=(
            "Validated aggregate pilot summary. When supplied, timing and "
            "adjudication inputs are derived from it and cannot be overridden."
        ),
    )
    parser.add_argument("--annotator-count", type=int)
    parser.add_argument("--hours-per-annotator", type=float, required=True)
    parser.add_argument(
        "--required-annotations-per-unit",
        type=int,
        default=2,
    )
    parser.add_argument("--minutes-per-annotation", type=float)
    parser.add_argument(
        "--expected-adjudication-rate",
        type=float,
    )
    parser.add_argument(
        "--minutes-per-adjudication",
        type=float,
    )
    parser.add_argument("--reserve-fraction", type=float, default=0.2)
    parser.add_argument(
        "--estimate-source",
        choices=("planning_assumption", "pilot_measurement"),
    )
    parser.add_argument("--pilot-unit-count", type=int, default=0)
    parser.add_argument("--minimum-trials", type=int, default=2)
    parser.add_argument("--maximum-trials", type=int, required=True)
    parser.add_argument("--minimum-patients-per-trial", type=int, default=5)
    parser.add_argument("--selected-trial-count", type=int)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _manual_assumptions(args: argparse.Namespace) -> CapacityAssumptions:
    required = {
        "--annotator-count": args.annotator_count,
        "--minutes-per-annotation": args.minutes_per_annotation,
        "--expected-adjudication-rate": args.expected_adjudication_rate,
        "--minutes-per-adjudication": args.minutes_per_adjudication,
        "--estimate-source": args.estimate_source,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(
            "Manual planning requires: " + ", ".join(missing)
        )
    return CapacityAssumptions(
        annotator_count=args.annotator_count,
        hours_per_annotator=args.hours_per_annotator,
        required_annotations_per_unit=args.required_annotations_per_unit,
        minutes_per_annotation=args.minutes_per_annotation,
        expected_adjudication_rate=args.expected_adjudication_rate,
        minutes_per_adjudication=args.minutes_per_adjudication,
        reserve_fraction=args.reserve_fraction,
        estimate_source=args.estimate_source,
        pilot_unit_count=args.pilot_unit_count,
    )


def _summary_assumptions(args: argparse.Namespace) -> CapacityAssumptions:
    forbidden = {
        "--annotator-count": args.annotator_count,
        "--minutes-per-annotation": args.minutes_per_annotation,
        "--expected-adjudication-rate": args.expected_adjudication_rate,
        "--minutes-per-adjudication": args.minutes_per_adjudication,
        "--estimate-source": args.estimate_source,
    }
    overrides = [name for name, value in forbidden.items() if value is not None]
    if args.required_annotations_per_unit != 2:
        overrides.append("--required-annotations-per-unit")
    if args.pilot_unit_count:
        overrides.append("--pilot-unit-count")
    if overrides:
        raise ValueError(
            "Pilot-derived inputs cannot be overridden: "
            + ", ".join(overrides)
        )
    summary = json.loads(args.pilot_summary.read_text(encoding="utf-8"))
    validate_pilot_summary(summary)
    inputs = summary["capacity_inputs"]
    return CapacityAssumptions(
        annotator_count=inputs["annotator_count"],
        hours_per_annotator=args.hours_per_annotator,
        required_annotations_per_unit=inputs[
            "required_annotations_per_unit"
        ],
        minutes_per_annotation=inputs["minutes_per_annotation"],
        expected_adjudication_rate=inputs[
            "expected_adjudication_rate"
        ],
        minutes_per_adjudication=inputs["minutes_per_adjudication"],
        reserve_fraction=args.reserve_fraction,
        estimate_source="validated_pilot_summary",
        pilot_unit_count=inputs["pilot_unit_count"],
        pilot_summary_sha256=summary["summary_sha256"],
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output.exists():
        raise ValueError(f"Refusing to overwrite existing plan: {args.output}")
    assumptions = (
        _summary_assumptions(args)
        if args.pilot_summary is not None
        else _manual_assumptions(args)
    )
    plan = build_capacity_plan(
        assumptions=assumptions,
        minimum_trials=args.minimum_trials,
        maximum_trials=args.maximum_trials,
        minimum_patients_per_trial=args.minimum_patients_per_trial,
        selected_trial_count=args.selected_trial_count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Built {plan['plan_id']}: "
        f"{plan['capacity']['maximum_patient_trial_units']} units, "
        f"{len(plan['feasible_designs'])} feasible designs, "
        f"snapshot_design_allowed={plan['snapshot_design_allowed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
