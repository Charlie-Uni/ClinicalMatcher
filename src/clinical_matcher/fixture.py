import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .models import (
    AtomicCondition,
    AtomProvenance,
    ComparisonOperator,
    ConditionExpression,
    Criterion,
    CriterionSource,
    CriterionType,
    Decision,
    DecompositionMethod,
    Evidence,
    ExpressionType,
    Fact,
    FactSelection,
    Patient,
    SourceSpan,
    TimeDirection,
    TimeWindow,
    Trial,
    TypedValue,
    ValueType,
)
from .validation import validate_document


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class CriterionAnnotation:
    annotator_id: str
    decision: Decision
    evidence_ids: Tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        if not self.annotator_id or not self.rationale:
            raise ValueError("Criterion annotation identity and rationale are required")


@dataclass(frozen=True)
class CriterionAdjudication:
    adjudicator_id: str
    decision: Decision
    evidence_ids: Tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        if not self.adjudicator_id or not self.rationale:
            raise ValueError(
                "Criterion adjudication identity and rationale are required"
            )


@dataclass(frozen=True)
class GoldCriterionJudgment:
    annotations: Tuple[CriterionAnnotation, ...]
    adjudication: CriterionAdjudication

    def __post_init__(self) -> None:
        annotator_ids = [item.annotator_id for item in self.annotations]
        if len(self.annotations) < 2 or len(annotator_ids) != len(set(annotator_ids)):
            raise ValueError(
                "Criterion gold requires at least two unique annotators"
            )

    @property
    def decision(self) -> Decision:
        return self.adjudication.decision

    @property
    def evidence_ids(self) -> Tuple[str, ...]:
        return self.adjudication.evidence_ids


@dataclass(frozen=True)
class TrialAnnotation:
    annotator_id: str
    decision: Decision
    relevance_grade: int
    rationale: str

    def __post_init__(self) -> None:
        if not self.annotator_id or not self.rationale:
            raise ValueError("Trial annotation identity and rationale are required")
        if not 0 <= self.relevance_grade <= 3:
            raise ValueError("Gold relevance_grade must be between 0 and 3")


@dataclass(frozen=True)
class TrialAdjudication:
    adjudicator_id: str
    decision: Decision
    relevance_grade: int
    rationale: str

    def __post_init__(self) -> None:
        if not self.adjudicator_id or not self.rationale:
            raise ValueError("Trial adjudication identity and rationale are required")
        if not 0 <= self.relevance_grade <= 3:
            raise ValueError("Gold relevance_grade must be between 0 and 3")


@dataclass(frozen=True)
class GoldTrialJudgment:
    annotations: Tuple[TrialAnnotation, ...]
    adjudication: TrialAdjudication

    def __post_init__(self) -> None:
        annotator_ids = [item.annotator_id for item in self.annotations]
        if len(self.annotations) < 2 or len(annotator_ids) != len(set(annotator_ids)):
            raise ValueError("Trial gold requires at least two unique annotators")
        grades = [
            item.relevance_grade for item in self.annotations
        ] + [self.adjudication.relevance_grade]
        if any(not 0 <= grade <= 3 for grade in grades):
            raise ValueError("Gold relevance_grade must be between 0 and 3")

    @property
    def decision(self) -> Decision:
        return self.adjudication.decision

    @property
    def relevance_grade(self) -> int:
        return self.adjudication.relevance_grade


@dataclass(frozen=True)
class SyntheticFixture:
    schema_version: str
    patients: Tuple[Patient, ...]
    trials: Tuple[Trial, ...]
    gold_criteria: Mapping[Tuple[str, str, str], GoldCriterionJudgment]
    gold_trials: Mapping[Tuple[str, str], GoldTrialJudgment]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema version: {self.schema_version}"
            )
        patient_ids = [patient.patient_id for patient in self.patients]
        trial_ids = [trial.trial_id for trial in self.trials]
        if len(patient_ids) != len(set(patient_ids)):
            raise ValueError("Fixture patient IDs must be unique")
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("Fixture trial IDs must be unique")
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
            evidence_sets = [
                annotation.evidence_ids for annotation in judgment.annotations
            ] + [judgment.adjudication.evidence_ids]
            for evidence_ids in evidence_sets:
                missing = set(evidence_ids) - patient_evidence[patient_id]
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
                provenance=AtomProvenance(
                    source_id=atom_raw["provenance"]["source_id"],
                    source_span=SourceSpan(
                        start=atom_raw["provenance"]["source_span"]["start"],
                        end=atom_raw["provenance"]["source_span"]["end"],
                    ),
                    method=DecompositionMethod(
                        atom_raw["provenance"]["method"]
                    ),
                    model_id=atom_raw["provenance"].get("model_id"),
                    prompt_version=atom_raw["provenance"].get(
                        "prompt_version"
                    ),
                ),
                time_window=time_window,
            ),
        )
    return ConditionExpression(
        expression_type=expression_type,
        children=tuple(_expression(child) for child in raw["children"]),
    )


def parse_patients(
    raw_patients: List[Dict[str, Any]],
) -> Tuple[Patient, ...]:
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
                    source=CriterionSource(
                        source_id=criterion["source"]["source_id"],
                        source_text=criterion["source"]["source_text"],
                        section=CriterionType(
                            criterion["source"]["section"]
                        ),
                        document_version=criterion["source"][
                            "document_version"
                        ],
                    ),
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
    validate_document(raw)
    gold_criteria = {}
    for patient_id, trials in raw["gold"]["criteria"].items():
        for trial_id, criteria in trials.items():
            for criterion_id, judgment in criteria.items():
                key = (patient_id, trial_id, criterion_id)
                gold_criteria[key] = GoldCriterionJudgment(
                    annotations=tuple(
                        CriterionAnnotation(
                            annotator_id=annotation["annotator_id"],
                            decision=Decision(annotation["decision"]),
                            evidence_ids=tuple(annotation["evidence_ids"]),
                            rationale=annotation["rationale"],
                        )
                        for annotation in judgment["annotations"]
                    ),
                    adjudication=CriterionAdjudication(
                        adjudicator_id=judgment["adjudication"][
                            "adjudicator_id"
                        ],
                        decision=Decision(
                            judgment["adjudication"]["decision"]
                        ),
                        evidence_ids=tuple(
                            judgment["adjudication"]["evidence_ids"]
                        ),
                        rationale=judgment["adjudication"]["rationale"],
                    )
                )

    gold_trials = {}
    for patient_id, trials in raw["gold"]["trials"].items():
        for trial_id, judgment in trials.items():
            gold_trials[(patient_id, trial_id)] = GoldTrialJudgment(
                annotations=tuple(
                    TrialAnnotation(
                        annotator_id=annotation["annotator_id"],
                        decision=Decision(annotation["decision"]),
                        relevance_grade=annotation["relevance_grade"],
                        rationale=annotation["rationale"],
                    )
                    for annotation in judgment["annotations"]
                ),
                adjudication=TrialAdjudication(
                    adjudicator_id=judgment["adjudication"][
                        "adjudicator_id"
                    ],
                    decision=Decision(
                        judgment["adjudication"]["decision"]
                    ),
                    relevance_grade=judgment["adjudication"][
                        "relevance_grade"
                    ],
                    rationale=judgment["adjudication"]["rationale"],
                ),
            )

    return SyntheticFixture(
        schema_version=raw["schema_version"],
        patients=parse_patients(raw["patients"]),
        trials=_trials(raw["trials"]),
        gold_criteria=gold_criteria,
        gold_trials=gold_trials,
    )
