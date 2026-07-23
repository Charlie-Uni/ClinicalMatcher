import operator
from typing import Any, Callable, Dict, Iterable, List

from .models import (
    Criterion,
    CriterionDecision,
    CriterionType,
    Decision,
    Patient,
    Trial,
    TrialMatch,
)


COMPARATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


def evaluate_criterion(patient: Patient, criterion: Criterion) -> CriterionDecision:
    """Evaluate one atomic criterion without inventing missing facts."""
    if criterion.operator not in COMPARATORS:
        raise ValueError(f"Unsupported operator: {criterion.operator}")

    if criterion.field not in patient.facts or patient.facts[criterion.field] is None:
        return CriterionDecision(
            criterion_id=criterion.criterion_id,
            criterion_type=criterion.criterion_type,
            decision=Decision.UNKNOWN,
            evidence_ids=(),
            reason=f"Missing fact: {criterion.field}",
        )

    observed = patient.facts[criterion.field]
    condition_holds = COMPARATORS[criterion.operator](observed, criterion.value)
    if criterion.criterion_type is CriterionType.INCLUSION:
        decision = Decision.ELIGIBLE if condition_holds else Decision.INELIGIBLE
    else:
        decision = Decision.INELIGIBLE if condition_holds else Decision.ELIGIBLE

    evidence_ids = tuple(
        item.evidence_id for item in patient.evidence if item.field == criterion.field
    )
    return CriterionDecision(
        criterion_id=criterion.criterion_id,
        criterion_type=criterion.criterion_type,
        decision=decision,
        evidence_ids=evidence_ids,
        reason=(
            f"Observed {criterion.field}={observed!r}; "
            f"condition {criterion.operator} {criterion.value!r} "
            f"{'holds' if condition_holds else 'does not hold'}."
        ),
    )


def _aggregate(patient: Patient, trial: Trial) -> TrialMatch:
    decisions = tuple(evaluate_criterion(patient, item) for item in trial.criteria)
    hard_ineligible = any(
        item.hard and decision.decision is Decision.INELIGIBLE
        for item, decision in zip(trial.criteria, decisions)
    )
    values = {
        Decision.ELIGIBLE: 1.0,
        Decision.UNKNOWN: 0.5,
        Decision.INELIGIBLE: 0.0,
    }
    score = sum(values[item.decision] for item in decisions) / len(decisions)
    if hard_ineligible:
        score = 0.0

    if hard_ineligible or any(
        item.decision is Decision.INELIGIBLE for item in decisions
    ):
        overall = Decision.INELIGIBLE
    elif any(item.decision is Decision.UNKNOWN for item in decisions):
        overall = Decision.UNKNOWN
    else:
        overall = Decision.ELIGIBLE

    return TrialMatch(
        patient_id=patient.patient_id,
        trial_id=trial.trial_id,
        decision=overall,
        score=round(score, 6),
        criterion_decisions=decisions,
    )


def match_patient(patient: Patient, trials: Iterable[Trial]) -> List[TrialMatch]:
    """Return deterministic trial rankings with stable tie-breaking."""
    matches = [_aggregate(patient, trial) for trial in trials]
    return sorted(matches, key=lambda item: (-item.score, item.trial_id))
