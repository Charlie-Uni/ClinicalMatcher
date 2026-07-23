from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple


class CriterionType(str, Enum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"


class Decision(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    field: str
    value: Any
    text: str


@dataclass(frozen=True)
class Patient:
    patient_id: str
    facts: Dict[str, Any]
    evidence: Tuple[Evidence, ...]


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    criterion_type: CriterionType
    field: str
    operator: str
    value: Any
    description: str
    hard: bool = False


@dataclass(frozen=True)
class Trial:
    trial_id: str
    title: str
    criteria: Tuple[Criterion, ...]


@dataclass(frozen=True)
class CriterionDecision:
    criterion_id: str
    criterion_type: CriterionType
    decision: Decision
    evidence_ids: Tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class TrialMatch:
    patient_id: str
    trial_id: str
    decision: Decision
    score: float
    criterion_decisions: Tuple[CriterionDecision, ...]
