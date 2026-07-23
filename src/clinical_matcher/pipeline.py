import operator
from datetime import timedelta
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

from .models import (
    AtomicCondition,
    AtomicDecision,
    ComparisonOperator,
    ConditionExpression,
    Criterion,
    CriterionDecision,
    CriterionType,
    Decision,
    ExpressionType,
    Fact,
    FactSelection,
    Patient,
    TimeDirection,
    Trial,
    TrialMatch,
    TruthValue,
    TypedValue,
    ValueType,
)


COMPARATORS: Dict[ComparisonOperator, Callable[[Any, Any], bool]] = {
    ComparisonOperator.EQ: operator.eq,
    ComparisonOperator.NE: operator.ne,
    ComparisonOperator.GT: operator.gt,
    ComparisonOperator.GE: operator.ge,
    ComparisonOperator.LT: operator.lt,
    ComparisonOperator.LE: operator.le,
}


def _in_time_window(patient: Patient, fact: Fact, atom: AtomicCondition) -> bool:
    window = atom.time_window
    if window is None:
        return True
    if fact.observed_at is None:
        return False

    if window.direction is TimeDirection.PAST:
        start = patient.index_date - timedelta(days=window.days)
        return start <= fact.observed_at <= patient.index_date

    end = patient.index_date + timedelta(days=window.days)
    return patient.index_date <= fact.observed_at <= end


def _compatible(
    observed: TypedValue,
    expected: TypedValue,
    comparison_operator: ComparisonOperator,
) -> Tuple[bool, str]:
    if observed.value_type is not expected.value_type:
        return False, (
            f"type mismatch: {observed.value_type.value} vs "
            f"{expected.value_type.value}"
        )
    if observed.value_type is ValueType.NUMBER and observed.unit != expected.unit:
        return False, f"unit mismatch: {observed.unit!r} vs {expected.unit!r}"
    if (
        observed.value_type in {ValueType.BOOLEAN, ValueType.STRING}
        and comparison_operator
        not in {ComparisonOperator.EQ, ComparisonOperator.NE}
    ):
        return False, (
            f"operator {comparison_operator.value} is invalid for "
            f"{observed.value_type.value}"
        )
    return True, ""


def _evaluate_atom(patient: Patient, atom: AtomicCondition) -> AtomicDecision:
    facts = [
        fact
        for fact in patient.facts
        if fact.field == atom.field and _in_time_window(patient, fact, atom)
    ]
    if not facts:
        return AtomicDecision(
            condition_id=atom.condition_id,
            truth_value=TruthValue.UNKNOWN,
            evidence_ids=(),
            reason=f"No in-window fact for {atom.field}.",
        )

    if atom.fact_selection is FactSelection.LATEST:
        dated_facts = [fact for fact in facts if fact.observed_at is not None]
        if not dated_facts:
            return AtomicDecision(
                condition_id=atom.condition_id,
                truth_value=TruthValue.UNKNOWN,
                evidence_ids=(),
                reason=f"No dated fact available for latest({atom.field}).",
            )
        facts = [
            max(
                dated_facts,
                key=lambda fact: fact.observed_at or patient.index_date,
            )
        ]

    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id for fact in facts for evidence_id in fact.evidence_ids
        )
    )
    outcomes = []
    incompatibilities = []
    for fact in facts:
        compatible, issue = _compatible(
            fact.value, atom.expected, atom.operator
        )
        if not compatible:
            incompatibilities.append(issue)
            continue
        outcomes.append(
            COMPARATORS[atom.operator](fact.value.value, atom.expected.value)
        )

    if atom.fact_selection is FactSelection.ALL:
        if False in outcomes:
            truth_value = TruthValue.FALSE
        elif outcomes and not incompatibilities:
            truth_value = TruthValue.TRUE
        else:
            truth_value = TruthValue.UNKNOWN
    elif any(outcomes):
        truth_value = TruthValue.TRUE
    elif outcomes and not incompatibilities:
        truth_value = TruthValue.FALSE
    else:
        truth_value = TruthValue.UNKNOWN

    reason = (
        f"{len(facts)} fact(s) evaluated with {atom.fact_selection.value} "
        f"selection for {atom.field}; "
        f"condition {atom.operator.value} {atom.expected.value!r} "
        f"resolved to {truth_value.value}."
    )
    if incompatibilities:
        reason += " " + "; ".join(sorted(set(incompatibilities))) + "."
    return AtomicDecision(
        condition_id=atom.condition_id,
        truth_value=truth_value,
        evidence_ids=evidence_ids,
        reason=reason,
    )


def _combine_all(values: Sequence[TruthValue]) -> TruthValue:
    if TruthValue.FALSE in values:
        return TruthValue.FALSE
    if all(value is TruthValue.TRUE for value in values):
        return TruthValue.TRUE
    return TruthValue.UNKNOWN


def _combine_any(values: Sequence[TruthValue]) -> TruthValue:
    if TruthValue.TRUE in values:
        return TruthValue.TRUE
    if all(value is TruthValue.FALSE for value in values):
        return TruthValue.FALSE
    return TruthValue.UNKNOWN


def _evaluate_expression(
    patient: Patient, expression: ConditionExpression
) -> Tuple[TruthValue, Tuple[AtomicDecision, ...]]:
    if expression.expression_type is ExpressionType.ATOM:
        if expression.atom is None:
            raise ValueError("Validated ATOM unexpectedly has no atom")
        atomic_decision = _evaluate_atom(patient, expression.atom)
        return atomic_decision.truth_value, (atomic_decision,)

    child_results = [
        _evaluate_expression(patient, child) for child in expression.children
    ]
    child_values = [item[0] for item in child_results]
    atomic_decisions = tuple(
        atomic for _, decisions in child_results for atomic in decisions
    )

    if expression.expression_type is ExpressionType.ALL:
        return _combine_all(child_values), atomic_decisions
    if expression.expression_type is ExpressionType.ANY:
        return _combine_any(child_values), atomic_decisions

    child_value = child_values[0]
    inverted = {
        TruthValue.TRUE: TruthValue.FALSE,
        TruthValue.FALSE: TruthValue.TRUE,
        TruthValue.UNKNOWN: TruthValue.UNKNOWN,
    }[child_value]
    return inverted, atomic_decisions


def evaluate_criterion(patient: Patient, criterion: Criterion) -> CriterionDecision:
    """Evaluate a protocol criterion without inventing missing facts."""
    truth_value, atomic_decisions = _evaluate_expression(
        patient, criterion.expression
    )
    if truth_value is TruthValue.UNKNOWN:
        decision = Decision.UNKNOWN
    elif criterion.criterion_type is CriterionType.INCLUSION:
        decision = (
            Decision.ELIGIBLE
            if truth_value is TruthValue.TRUE
            else Decision.INELIGIBLE
        )
    else:
        decision = (
            Decision.INELIGIBLE
            if truth_value is TruthValue.TRUE
            else Decision.ELIGIBLE
        )

    evidence_ids = tuple(
        dict.fromkeys(
            evidence_id
            for atomic in atomic_decisions
            for evidence_id in atomic.evidence_ids
        )
    )
    return CriterionDecision(
        criterion_id=criterion.criterion_id,
        criterion_type=criterion.criterion_type,
        decision=decision,
        evidence_ids=evidence_ids,
        atomic_decisions=atomic_decisions,
        reason=(
            f"{criterion.criterion_type.value} expression resolved to "
            f"{truth_value.value}; criterion decision is {decision.value}."
        ),
    )


def _aggregate(patient: Patient, trial: Trial) -> TrialMatch:
    decisions = tuple(evaluate_criterion(patient, item) for item in trial.criteria)
    paired = tuple(zip(trial.criteria, decisions))
    hard_ineligible = [
        decision
        for criterion, decision in paired
        if criterion.hard and decision.decision is Decision.INELIGIBLE
    ]
    hard_unknown = [
        decision
        for criterion, decision in paired
        if criterion.hard and decision.decision is Decision.UNKNOWN
    ]

    if hard_ineligible:
        overall = Decision.INELIGIBLE
    elif hard_unknown or all(
        decision.decision is Decision.UNKNOWN for _, decision in paired
    ):
        overall = Decision.UNKNOWN
    else:
        overall = Decision.ELIGIBLE

    known = [
        (criterion, decision)
        for criterion, decision in paired
        if decision.decision is not Decision.UNKNOWN
    ]
    total_weight = sum(criterion.weight for criterion, _ in paired)
    known_weight = sum(criterion.weight for criterion, _ in known)
    eligible_weight = sum(
        criterion.weight
        for criterion, decision in known
        if decision.decision is Decision.ELIGIBLE
    )
    eligibility_score = (
        round(eligible_weight / known_weight, 6) if known_weight else None
    )
    coverage = round(known_weight / total_weight, 6)
    abstention_reasons = tuple(
        f"Hard criterion {decision.criterion_id} is unknown."
        for decision in hard_unknown
    )
    if overall is Decision.UNKNOWN and not abstention_reasons:
        abstention_reasons = ("No criterion could be resolved.",)

    return TrialMatch(
        patient_id=patient.patient_id,
        trial_id=trial.trial_id,
        decision=overall,
        eligibility_score=eligibility_score,
        coverage=coverage,
        abstained=overall is Decision.UNKNOWN,
        abstention_reasons=abstention_reasons,
        criterion_decisions=decisions,
    )


def _ranking_key(match: TrialMatch) -> Tuple[float, float, float, str]:
    decision_priority = {
        Decision.ELIGIBLE: 2.0,
        Decision.UNKNOWN: 1.0,
        Decision.INELIGIBLE: 0.0,
    }[match.decision]
    score = match.eligibility_score if match.eligibility_score is not None else -1.0
    return (-decision_priority, -score, -match.coverage, match.trial_id)


def match_patient(patient: Patient, trials: Iterable[Trial]) -> List[TrialMatch]:
    """Rank eligibility, score, and coverage separately with stable ties."""
    matches = [_aggregate(patient, trial) for trial in trials]
    return sorted(matches, key=_ranking_key)
