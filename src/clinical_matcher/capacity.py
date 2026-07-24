import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .splits import current_git_commit
from .validation import validate_document


CAPACITY_PLAN_VERSION = "1.0.0"
CAPACITY_SCHEMA_RESOURCE = "schemas/annotation-capacity-plan-1.0.0.schema.json"


@dataclass(frozen=True)
class CapacityAssumptions:
    annotator_count: int
    hours_per_annotator: float
    required_annotations_per_unit: int
    minutes_per_annotation: float
    expected_adjudication_rate: float
    minutes_per_adjudication: float
    reserve_fraction: float
    estimate_source: str
    pilot_unit_count: int = 0

    def validate(self) -> None:
        if self.annotator_count < 2:
            raise ValueError("At least two annotators are required")
        if self.required_annotations_per_unit < 2:
            raise ValueError("Each unit requires at least two annotations")
        if self.required_annotations_per_unit > self.annotator_count:
            raise ValueError(
                "required_annotations_per_unit exceeds annotator_count"
            )
        if self.hours_per_annotator <= 0:
            raise ValueError("hours_per_annotator must be positive")
        if self.minutes_per_annotation <= 0:
            raise ValueError("minutes_per_annotation must be positive")
        if not 0 <= self.expected_adjudication_rate <= 1:
            raise ValueError("expected_adjudication_rate must be in [0, 1]")
        if self.minutes_per_adjudication < 0:
            raise ValueError("minutes_per_adjudication cannot be negative")
        if not 0 <= self.reserve_fraction < 1:
            raise ValueError("reserve_fraction must be in [0, 1)")
        if self.estimate_source not in {
            "planning_assumption",
            "pilot_measurement",
        }:
            raise ValueError("Unsupported estimate_source")
        if self.pilot_unit_count < 0:
            raise ValueError("pilot_unit_count cannot be negative")
        if self.estimate_source == "pilot_measurement" and self.pilot_unit_count < 1:
            raise ValueError(
                "pilot_measurement requires at least one completed pilot unit"
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _plan_hash_payload(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"plan_id", "plan_sha256", "generated_at"}
    }


def build_capacity_plan(
    assumptions: CapacityAssumptions,
    minimum_trials: int,
    maximum_trials: int,
    minimum_patients_per_trial: int,
    selected_trial_count: Optional[int] = None,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Reverse-plan benchmark size from double-annotation person-time."""
    assumptions.validate()
    if minimum_trials < 2:
        raise ValueError("minimum_trials must be at least 2")
    if maximum_trials < minimum_trials:
        raise ValueError("maximum_trials cannot be below minimum_trials")
    if minimum_patients_per_trial < 1:
        raise ValueError("minimum_patients_per_trial must be positive")
    if selected_trial_count is not None and not (
        minimum_trials <= selected_trial_count <= maximum_trials
    ):
        raise ValueError("selected_trial_count is outside the trial range")

    gross_person_minutes = (
        assumptions.annotator_count
        * assumptions.hours_per_annotator
        * 60.0
    )
    usable_person_minutes = gross_person_minutes * (
        1.0 - assumptions.reserve_fraction
    )
    annotation_person_minutes = (
        assumptions.required_annotations_per_unit
        * assumptions.minutes_per_annotation
    )
    adjudication_person_minutes = (
        assumptions.expected_adjudication_rate
        * assumptions.minutes_per_adjudication
    )
    expected_person_minutes_per_unit = (
        annotation_person_minutes + adjudication_person_minutes
    )
    max_units = math.floor(
        usable_person_minutes / expected_person_minutes_per_unit
    )
    options: List[Dict[str, Any]] = []
    for trial_count in range(minimum_trials, maximum_trials + 1):
        patient_count = max_units // trial_count
        if patient_count < minimum_patients_per_trial:
            continue
        units = trial_count * patient_count
        options.append(
            {
                "trial_count": trial_count,
                "patient_count": patient_count,
                "patient_trial_units": units,
                "unused_unit_capacity": max_units - units,
                "estimated_person_minutes": round(
                    units * expected_person_minutes_per_unit,
                    4,
                ),
            }
        )
    selected_design = None
    if selected_trial_count is not None:
        selected_design = next(
            (
                option
                for option in options
                if option["trial_count"] == selected_trial_count
            ),
            None,
        )
        if selected_design is None:
            raise ValueError(
                "Selected trial count cannot meet minimum patient coverage"
            )

    plan: Dict[str, Any] = {
        "capacity_plan_version": CAPACITY_PLAN_VERSION,
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "status": (
            "pilot_validated"
            if assumptions.estimate_source == "pilot_measurement"
            else "provisional"
        ),
        "assumptions": asdict(assumptions),
        "constraints": {
            "minimum_trials": minimum_trials,
            "maximum_trials": maximum_trials,
            "minimum_patients_per_trial": minimum_patients_per_trial,
        },
        "capacity": {
            "gross_person_minutes": round(gross_person_minutes, 4),
            "usable_person_minutes": round(usable_person_minutes, 4),
            "annotation_person_minutes_per_unit": round(
                annotation_person_minutes,
                4,
            ),
            "expected_adjudication_person_minutes_per_unit": round(
                adjudication_person_minutes,
                4,
            ),
            "expected_person_minutes_per_unit": round(
                expected_person_minutes_per_unit,
                4,
            ),
            "maximum_patient_trial_units": max_units,
        },
        "feasible_designs": options,
        "selected_design": selected_design,
        "snapshot_design_allowed": (
            assumptions.estimate_source == "pilot_measurement"
            and selected_design is not None
        ),
        "scope_note": (
            "One patient-trial unit must include the complete criterion-level "
            "eligibility and evidence annotation bundle used by the benchmark."
        ),
    }
    plan_sha256 = _canonical_hash(_plan_hash_payload(plan))
    plan["plan_sha256"] = plan_sha256
    plan["plan_id"] = f"capacity-{plan_sha256[:16]}"
    validate_document(plan, CAPACITY_SCHEMA_RESOURCE)
    return plan


def validate_capacity_plan(plan: Dict[str, Any]) -> None:
    validate_document(plan, CAPACITY_SCHEMA_RESOURCE)
    expected_hash = _canonical_hash(_plan_hash_payload(plan))
    if plan["plan_sha256"] != expected_hash:
        raise ValueError("Capacity plan hash does not match its contents")
    if plan["plan_id"] != f"capacity-{expected_hash[:16]}":
        raise ValueError("Capacity plan ID does not match its contents")
    if plan["snapshot_design_allowed"]:
        if plan["status"] != "pilot_validated" or plan["selected_design"] is None:
            raise ValueError("Capacity plan release flags are inconsistent")
    selected_trial_count = (
        plan["selected_design"]["trial_count"]
        if plan["selected_design"] is not None
        else None
    )
    recomputed = build_capacity_plan(
        assumptions=CapacityAssumptions(**plan["assumptions"]),
        minimum_trials=plan["constraints"]["minimum_trials"],
        maximum_trials=plan["constraints"]["maximum_trials"],
        minimum_patients_per_trial=plan["constraints"][
            "minimum_patients_per_trial"
        ],
        selected_trial_count=selected_trial_count,
        generated_at=plan["generated_at"],
        code_commit=plan["code_commit"],
    )
    if recomputed != plan:
        raise ValueError("Capacity plan calculations are inconsistent")
