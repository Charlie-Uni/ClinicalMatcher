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


class DecompositionMethod(str, Enum):
    HUMAN = "human"
    RULE = "rule"
    LLM = "llm"


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
class SourceSpan:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("SourceSpan must satisfy 0 <= start < end")


@dataclass(frozen=True)
class CriterionSource:
    source_id: str
    source_text: str
    section: CriterionType
    document_version: str

    def __post_init__(self) -> None:
        if not all(
            (self.source_id, self.source_text, self.document_version)
        ):
            raise ValueError("Criterion source fields must be non-empty")


@dataclass(frozen=True)
class AtomProvenance:
    source_id: str
    source_span: SourceSpan
    method: DecompositionMethod
    model_id: Optional[str] = None
    prompt_version: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("Atom provenance source_id must be non-empty")
        if self.method is DecompositionMethod.LLM and not (
            self.model_id and self.prompt_version
        ):
            raise ValueError(
                "LLM decomposition requires model_id and prompt_version"
            )
        if self.method is not DecompositionMethod.LLM and (
            self.model_id or self.prompt_version
        ):
            raise ValueError(
                "model_id/prompt_version are reserved for LLM decomposition"
            )


@dataclass(frozen=True)
class AtomicCondition:
    condition_id: str
    field: str
    operator: ComparisonOperator
    expected: TypedValue
    fact_selection: FactSelection
    provenance: AtomProvenance
    time_window: Optional[TimeWindow] = None

    def __post_init__(self) -> None:
        if not self.condition_id or not self.field:
            raise ValueError("Atomic condition ID and field must be non-empty")


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
    source: CriterionSource
    expression: ConditionExpression
    hard: bool = False
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.criterion_id or not self.description:
            raise ValueError("Criterion ID and description must be non-empty")
        if self.weight <= 0:
            raise ValueError("Criterion.weight must be positive")
        if self.source.section is not self.criterion_type:
            raise ValueError("Criterion source section must match criterion type")

        def atoms(expression: ConditionExpression) -> Tuple[AtomicCondition, ...]:
            if expression.expression_type is ExpressionType.ATOM:
                if expression.atom is None:
                    raise ValueError("Validated ATOM unexpectedly has no atom")
                return (expression.atom,)
            return tuple(
                atom
                for child in expression.children
                for atom in atoms(child)
            )

        for atom in atoms(self.expression):
            if atom.provenance.source_id != self.source.source_id:
                raise ValueError(
                    "Atomic provenance must reference its criterion source"
                )
            if atom.provenance.source_span.end > len(self.source.source_text):
                raise ValueError("Atomic provenance span exceeds source text")


@dataclass(frozen=True)
class Trial:
    trial_id: str
    title: str
    criteria: Tuple[Criterion, ...]

    def __post_init__(self) -> None:
        if not self.trial_id or not self.title:
            raise ValueError("Trial ID and title must be non-empty")
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
        source_ids = [criterion.source.source_id for criterion in self.criteria]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Trial criterion source IDs must be unique")


@dataclass(frozen=True)
class AtomicDecision:
    condition_id: str
    truth_value: TruthValue
    evidence_ids: Tuple[str, ...]
    reason: str
    negated: bool = False
    issues: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionDecision:
    criterion_id: str
    criterion_type: CriterionType
    decision: Decision
    evidence_ids: Tuple[str, ...]
    atomic_decisions: Tuple[AtomicDecision, ...]
    atomic_coverage: float
    issues: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class TrialMatch:
    patient_id: str
    trial_id: str
    decision: Decision
    eligibility_score: Optional[float]
    coverage: float
    atomic_coverage: float
    abstained: bool
    abstention_reasons: Tuple[str, ...]
    data_quality_issues: Tuple[str, ...]
    criterion_decisions: Tuple[CriterionDecision, ...]
