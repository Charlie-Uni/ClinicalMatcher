import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .models import (
    AtomicCondition,
    ComparisonOperator,
    ConditionExpression,
    Criterion,
    CriterionType,
    Decision,
    Evidence,
    ExpressionType,
    Fact,
    FactSelection,
    Patient,
    TimeDirection,
    TimeWindow,
    Trial,
    TypedValue,
    ValueType,
)


@dataclass(frozen=True)
class GoldCriterionJudgment:
    decision: Decision
    evidence_ids: Tuple[str, ...]


@dataclass(frozen=True)
class GoldTrialJudgment:
    decision: Decision
    relevance_grade: int

    def __post_init__(self) -> None:
        if not 0 <= self.relevance_grade <= 3:
            raise ValueError("Gold relevance_grade must be between 0 and 3")


@dataclass(frozen=True)
class SyntheticFixture:
    patients: Tuple[Patient, ...]
    trials: Tuple[Trial, ...]
    gold_criteria: Mapping[Tuple[str, str, str], GoldCriterionJudgment]
    gold_trials: Mapping[Tuple[str, str], GoldTrialJudgment]

    def __post_init__(self) -> None:
        expected_trials = {
            (patient.patient_id, trial.trial_id)
            for patient in self.patients
            for trial in self.trials
        }
        if set(self.gold_trials) != expected_trials:
            raise ValueError("Trial gold must cover every patient-trial pair exactly")

        expected_criteria = {
            (patient.patient_id, trial.trial_id, criterion.criterion_id)
            for patient in self.patients
            for trial in self.trials
            for criterion in trial.criteria
        }
        if set(self.gold_criteria) != expected_criteria:
            raise ValueError(
                "Criterion gold must cover every patient-trial-criterion tuple exactly"
            )

        patient_evidence = {
            patient.patient_id: {item.evidence_id for item in patient.evidence}
            for patient in self.patients
        }
        for (patient_id, _, _), judgment in self.gold_criteria.items():
            missing = set(judgment.evidence_ids) - patient_evidence[patient_id]
            if missing:
                raise ValueError(
                    f"Gold references unknown evidence IDs: {sorted(missing)}"
                )

def _typed_value(raw: Dict[str, Any]) -> TypedValue:
    value_type = ValueType(raw["value_type"])
    value = raw["value"]
    if value_type is ValueType.DATE:
        value = date.fromisoformat(value)
    return TypedValue(
        value_type=value_type,
        value=value,
        unit=raw.get("unit"),
    )


def _expression(raw: Dict[str, Any]) -> ConditionExpression:
    expression_type = ExpressionType(raw["expression_type"])
    if expression_type is ExpressionType.ATOM:
        atom_raw = raw["atom"]
        window_raw = atom_raw.get("time_window")
        time_window = (
            TimeWindow(
                days=window_raw["days"],
                direction=TimeDirection(window_raw["direction"]),
                relative_to=window_raw.get("relative_to", "index_date"),
            )
            if window_raw
            else None
        )
        return ConditionExpression(
            expression_type=expression_type,
            atom=AtomicCondition(
                condition_id=atom_raw["condition_id"],
                field=atom_raw["field"],
                operator=ComparisonOperator(atom_raw["operator"]),
                expected=_typed_value(atom_raw["expected"]),
                fact_selection=FactSelection(atom_raw["fact_selection"]),
                time_window=time_window,
            ),
        )
    return ConditionExpression(
        expression_type=expression_type,
        children=tuple(_expression(child) for child in raw["children"]),
    )


def _patients(raw_patients: List[Dict[str, Any]]) -> Tuple[Patient, ...]:
    return tuple(
        Patient(
            patient_id=item["patient_id"],
            index_date=date.fromisoformat(item["index_date"]),
            facts=tuple(
                Fact(
                    fact_id=fact["fact_id"],
                    field=fact["field"],
                    value=_typed_value(fact["value"]),
                    evidence_ids=tuple(fact["evidence_ids"]),
                    observed_at=(
                        date.fromisoformat(fact["observed_at"])
                        if fact.get("observed_at")
                        else None
                    ),
                )
                for fact in item["facts"]
            ),
            evidence=tuple(Evidence(**evidence) for evidence in item["evidence"]),
        )
        for item in raw_patients
    )


def _trials(raw_trials: List[Dict[str, Any]]) -> Tuple[Trial, ...]:
    return tuple(
        Trial(
            trial_id=item["trial_id"],
            title=item["title"],
            criteria=tuple(
                Criterion(
                    criterion_id=criterion["criterion_id"],
                    criterion_type=CriterionType(criterion["criterion_type"]),
                    description=criterion["description"],
                    expression=_expression(criterion["expression"]),
                    hard=criterion.get("hard", False),
                    weight=criterion.get("weight", 1.0),
                )
                for criterion in item["criteria"]
            ),
        )
        for item in raw_trials
    )


def load_fixture(path: Path) -> SyntheticFixture:
    raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    gold_criteria = {}
    for patient_id, trials in raw["gold"]["criteria"].items():
        for trial_id, criteria in trials.items():
            for criterion_id, judgment in criteria.items():
                gold_criteria[(patient_id, trial_id, criterion_id)] = (
                    GoldCriterionJudgment(
                        decision=Decision(judgment["decision"]),
                        evidence_ids=tuple(judgment["evidence_ids"]),
                    )
                )

    gold_trials = {}
    for patient_id, trials in raw["gold"]["trials"].items():
        for trial_id, judgment in trials.items():
            gold_trials[(patient_id, trial_id)] = GoldTrialJudgment(
                decision=Decision(judgment["decision"]),
                relevance_grade=judgment["relevance_grade"],
            )

    return SyntheticFixture(
        patients=_patients(raw["patients"]),
        trials=_trials(raw["trials"]),
        gold_criteria=gold_criteria,
        gold_trials=gold_trials,
    )
