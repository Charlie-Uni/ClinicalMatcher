import math
from dataclasses import dataclass
from typing import Protocol, Tuple


@dataclass(frozen=True)
class RankedEvidence:
    patient_id: str
    evidence_id: str
    score: float
    rank: int

    def __post_init__(self) -> None:
        if not self.patient_id or not self.evidence_id:
            raise ValueError("Ranked evidence identifiers cannot be empty")
        if not math.isfinite(self.score) or self.score < 0:
            raise ValueError("Ranked evidence score must be finite and non-negative")
        if self.rank < 1:
            raise ValueError("Rank must be positive")


class EvidenceRetriever(Protocol):
    def rank(self, patient_id: str, query: str) -> Tuple[RankedEvidence, ...]:
        ...

    def retrieve(
        self, patient_id: str, query: str, k: int
    ) -> Tuple[RankedEvidence, ...]:
        ...
