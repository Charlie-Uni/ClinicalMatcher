from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Optional, Tuple


class CriterionType(str, Enum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"


class Decision(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class TruthValue(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ValueType(str, Enum):
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING = "string"
    DATE = "date"


class ComparisonOperator(str, Enum):
    EQ = "=="
    NE = "!="
    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="


class ExpressionType(str, Enum):
    ATOM = "atom"
    ALL = "all"
    ANY = "any"
    NOT = "not"


class TimeDirection(str, Enum):
    PAST = "past"
    FUTURE = "future"


class FactSelection(str, Enum):
    ANY = "any"
    ALL = "all"
    LATEST = "latest"


@dataclass(frozen=True)
class TypedValue:
    value_type: ValueType
    value: Any
    unit: Optional[str] = None

    def __post_init__(self) -> None:
        valid = {
            ValueType.NUMBER: (
                isinstance(self.value, (int, float))
                and not isinstance(self.value, bool)
            ),
            ValueType.BOOLEAN: isinstance(self.value, bool),
            ValueType.STRING: isinstance(self.value, str),
            ValueType.DATE: isinstance(self.value, date),
        }[self.value_type]
        if not valid:
            raise TypeError(
                f"{self.value!r} does not match {self.value_type.value}"
            )
        if self.value_type is not ValueType.NUMBER and self.unit is not None:
            raise ValueError("Only numeric values may declare a unit")


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.source_id or not self.text:
            raise ValueError("Evidence IDs, source IDs, and text must be non-empty")


@dataclass(frozen=True)
class Fact:
    fact_id: str
    field: str
    value: TypedValue
    evidence_ids: Tuple[str, ...]
    observed_at: Optional[date] = None

    def __post_init__(self) -> None:
        if not self.evidence_ids:
            raise ValueError("Every fact must link to at least one evidence ID")


@dataclass(frozen=True)
class Patient:
    patient_id: str
    index_date: date
    facts: Tuple[Fact, ...]
    evidence: Tuple[Evidence, ...]

    def __post_init__(self) -> None:
        fact_ids = [fact.fact_id for fact in self.facts]
        evidence_ids = [item.evidence_id for item in self.evidence]
        known_evidence_ids = set(evidence_ids)
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Patient fact IDs must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Patient evidence IDs must be unique")
        missing = {
            evidence_id
            for fact in self.facts
            for evidence_id in fact.evidence_ids
            if evidence_id not in known_evidence_ids
        }
        if missing:
            raise ValueError(f"Facts reference unknown evidence IDs: {sorted(missing)}")


@dataclass(frozen=True)
class TimeWindow:
    days: int
    direction: TimeDirection
    relative_to: str = "index_date"

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ValueError("TimeWindow.days must be positive")
        if self.relative_to != "index_date":
            raise ValueError("Only index_date-relative windows are supported")


@dataclass(frozen=True)
class AtomicCondition:
    condition_id: str
    field: str
    operator: ComparisonOperator
    expected: TypedValue
    fact_selection: FactSelection
    time_window: Optional[TimeWindow] = None


@dataclass(frozen=True)
class ConditionExpression:
    expression_type: ExpressionType
    atom: Optional[AtomicCondition] = None
    children: Tuple["ConditionExpression", ...] = ()

    def __post_init__(self) -> None:
        if self.expression_type is ExpressionType.ATOM:
            if self.atom is None or self.children:
                raise ValueError("ATOM requires one atom and no children")
        elif self.expression_type is ExpressionType.NOT:
            if self.atom is not None or len(self.children) != 1:
                raise ValueError("NOT requires exactly one child")
        elif self.atom is not None or not self.children:
            raise ValueError("ALL/ANY require children and no atom")


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    criterion_type: CriterionType
    description: str
    expression: ConditionExpression
    hard: bool = False
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.weight <= 0:
            raise ValueError("Criterion.weight must be positive")


@dataclass(frozen=True)
class Trial:
    trial_id: str
    title: str
    criteria: Tuple[Criterion, ...]

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValueError("Trial must contain at least one criterion")
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Trial criterion IDs must be unique")

        def condition_ids(expression: ConditionExpression) -> Tuple[str, ...]:
            if expression.expression_type is ExpressionType.ATOM:
                if expression.atom is None:
                    raise ValueError("Validated ATOM unexpectedly has no atom")
                return (expression.atom.condition_id,)
            return tuple(
                condition_id
                for child in expression.children
                for condition_id in condition_ids(child)
            )

        atom_ids = [
            condition_id
            for criterion in self.criteria
            for condition_id in condition_ids(criterion.expression)
        ]
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("Trial atomic condition IDs must be unique")


@dataclass(frozen=True)
class AtomicDecision:
    condition_id: str
    truth_value: TruthValue
    evidence_ids: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CriterionDecision:
    criterion_id: str
    criterion_type: CriterionType
    decision: Decision
    evidence_ids: Tuple[str, ...]
    atomic_decisions: Tuple[AtomicDecision, ...]
    reason: str


@dataclass(frozen=True)
class TrialMatch:
    patient_id: str
    trial_id: str
    decision: Decision
    eligibility_score: Optional[float]
    coverage: float
    abstained: bool
    abstention_reasons: Tuple[str, ...]
    criterion_decisions: Tuple[CriterionDecision, ...]
