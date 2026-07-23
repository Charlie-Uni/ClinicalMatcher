"""Core types and deterministic baseline for ClinicalMatcher."""

from .models import (
    Criterion,
    CriterionDecision,
    CriterionType,
    Decision,
    Evidence,
    Patient,
    Trial,
    TrialMatch,
)
from .pipeline import evaluate_criterion, match_patient

__all__ = [
    "Criterion",
    "CriterionDecision",
    "CriterionType",
    "Decision",
    "Evidence",
    "Patient",
    "Trial",
    "TrialMatch",
    "evaluate_criterion",
    "match_patient",
]
