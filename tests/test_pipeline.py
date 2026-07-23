import unittest
from datetime import date
from pathlib import Path
from typing import Optional

from clinical_matcher.evaluation import (
    evidence_recall_at_k,
    ndcg_at_k,
    reciprocal_rank,
)
from clinical_matcher.fixture import load_fixture
from clinical_matcher.models import (
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
from clinical_matcher.pipeline import evaluate_criterion, match_patient


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


def atom(
    condition_id: str,
    field: str,
    operator_: ComparisonOperator,
    value: object,
    value_type: ValueType,
    unit: Optional[str] = None,
    fact_selection: FactSelection = FactSelection.ANY,
) -> ConditionExpression:
    return ConditionExpression(
        expression_type=ExpressionType.ATOM,
        atom=AtomicCondition(
            condition_id=condition_id,
            field=field,
            operator=operator_,
            expected=TypedValue(value_type=value_type, value=value, unit=unit),
            fact_selection=fact_selection,
        ),
    )


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_fixture(FIXTURE)

    def test_predictions_match_independent_gold(self) -> None:
        for patient in self.fixture.patients:
            for match in match_patient(patient, self.fixture.trials):
                trial_gold = self.fixture.gold_trials[
                    (patient.patient_id, match.trial_id)
                ]
                self.assertEqual(trial_gold.decision, match.decision)
                for decision in match.criterion_decisions:
                    criterion_gold = self.fixture.gold_criteria[
                        (patient.patient_id, match.trial_id, decision.criterion_id)
                    ]
                    self.assertEqual(criterion_gold.decision, decision.decision)
                    self.assertEqual(
                        set(criterion_gold.evidence_ids),
                        set(decision.evidence_ids),
                    )

    def test_gold_ranking_ndcg(self) -> None:
        for patient in self.fixture.patients:
            ranked = [
                item.trial_id
                for item in match_patient(patient, self.fixture.trials)
            ]
            grades = {
                trial.trial_id: self.fixture.gold_trials[
                    (patient.patient_id, trial.trial_id)
                ].relevance_grade
                for trial in self.fixture.trials
            }
            self.assertEqual(1.0, ndcg_at_k(ranked, grades, k=2))

    def test_compound_range_expression(self) -> None:
        patient = self.fixture.patients[0]
        criterion = self.fixture.trials[0].criteria[1]
        decision = evaluate_criterion(patient, criterion)
        self.assertEqual(Decision.ELIGIBLE, decision.decision)
        self.assertEqual(2, len(decision.atomic_decisions))

    def test_soft_failure_lowers_score_without_exclusion(self) -> None:
        patient = self.fixture.patients[0]
        trial = self.fixture.trials[1]
        match = match_patient(patient, [trial])[0]
        self.assertEqual(Decision.ELIGIBLE, match.decision)
        self.assertAlmostEqual(2 / 3, match.eligibility_score, places=6)
        self.assertEqual(1.0, match.coverage)
        self.assertFalse(match.abstained)

    def test_missing_hard_fact_abstains_without_half_score(self) -> None:
        patient = Patient(
            patient_id="missing",
            index_date=date(2026, 1, 1),
            facts=(),
            evidence=(),
        )
        criterion = Criterion(
            criterion_id="requires-lab",
            criterion_type=CriterionType.INCLUSION,
            description="A fictional lab is required",
            hard=True,
            expression=atom(
                "requires-lab-atom",
                "missing_lab",
                ComparisonOperator.GE,
                1,
                ValueType.NUMBER,
                "unit",
            ),
        )
        trial = Trial(trial_id="missing-trial", title="Missing", criteria=(criterion,))
        match = match_patient(patient, [trial])[0]
        self.assertEqual(Decision.UNKNOWN, match.decision)
        self.assertIsNone(match.eligibility_score)
        self.assertEqual(0.0, match.coverage)
        self.assertTrue(match.abstained)

    def test_unit_mismatch_abstains(self) -> None:
        patient = Patient(
            patient_id="unit-mismatch",
            index_date=date(2026, 1, 1),
            facts=(
                Fact(
                    fact_id="creatinine-fact",
                    field="creatinine",
                    value=TypedValue(ValueType.NUMBER, 100, "umol/L"),
                    evidence_ids=("lab",),
                ),
            ),
            evidence=(
                Evidence(
                    evidence_id="lab",
                    source_id="synthetic-lab",
                    text="Synthetic creatinine measurement.",
                ),
            ),
        )
        criterion = Criterion(
            criterion_id="creatinine-limit",
            criterion_type=CriterionType.INCLUSION,
            description="Unit safety test",
            hard=True,
            expression=atom(
                "creatinine-max",
                "creatinine",
                ComparisonOperator.LE,
                1.5,
                ValueType.NUMBER,
                "mg/dL",
            ),
        )
        decision = evaluate_criterion(patient, criterion)
        self.assertEqual(Decision.UNKNOWN, decision.decision)
        self.assertIn("unit mismatch", decision.atomic_decisions[0].reason)

    def test_latest_fact_does_not_use_stale_normal_value(self) -> None:
        patient = Patient(
            patient_id="latest",
            index_date=date(2026, 1, 1),
            facts=(
                Fact(
                    fact_id="old",
                    field="egfr",
                    value=TypedValue(ValueType.NUMBER, 80, "unit"),
                    evidence_ids=("old-evidence",),
                    observed_at=date(2025, 12, 1),
                ),
                Fact(
                    fact_id="new",
                    field="egfr",
                    value=TypedValue(ValueType.NUMBER, 40, "unit"),
                    evidence_ids=("new-evidence",),
                    observed_at=date(2025, 12, 31),
                ),
            ),
            evidence=(
                Evidence(
                    evidence_id="old-evidence",
                    source_id="synthetic-old-lab",
                    text="Synthetic older eGFR measurement.",
                ),
                Evidence(
                    evidence_id="new-evidence",
                    source_id="synthetic-new-lab",
                    text="Synthetic newer eGFR measurement.",
                ),
            ),
        )
        criterion = Criterion(
            criterion_id="latest-egfr",
            criterion_type=CriterionType.INCLUSION,
            description="Latest eGFR must be at least 50",
            expression=atom(
                "latest-egfr-atom",
                "egfr",
                ComparisonOperator.GE,
                50,
                ValueType.NUMBER,
                "unit",
                FactSelection.LATEST,
            ),
        )
        decision = evaluate_criterion(patient, criterion)
        self.assertEqual(Decision.INELIGIBLE, decision.decision)
        self.assertEqual(("new-evidence",), decision.evidence_ids)

    def test_any_not_and_time_window_three_valued_logic(self) -> None:
        patient = self.fixture.patients[0]
        missing = atom(
            "missing",
            "missing_field",
            ComparisonOperator.EQ,
            True,
            ValueType.BOOLEAN,
        )
        diabetes = atom(
            "diabetes",
            "diabetes",
            ComparisonOperator.EQ,
            True,
            ValueType.BOOLEAN,
        )
        expression = ConditionExpression(
            expression_type=ExpressionType.NOT,
            children=(
                ConditionExpression(
                    expression_type=ExpressionType.ANY,
                    children=(missing, diabetes),
                ),
            ),
        )
        criterion = Criterion(
            criterion_id="not-any",
            criterion_type=CriterionType.INCLUSION,
            description="NOT(unknown OR diabetes)",
            expression=expression,
        )
        self.assertEqual(
            Decision.INELIGIBLE,
            evaluate_criterion(patient, criterion).decision,
        )

        outside_window = ConditionExpression(
            expression_type=ExpressionType.ATOM,
            atom=AtomicCondition(
                condition_id="outside-window",
                field="active_bleeding",
                operator=ComparisonOperator.EQ,
                expected=TypedValue(ValueType.BOOLEAN, True),
                fact_selection=FactSelection.ANY,
                time_window=TimeWindow(days=1, direction=TimeDirection.PAST),
            ),
        )
        exclusion = Criterion(
            criterion_id="recent-event",
            criterion_type=CriterionType.EXCLUSION,
            description="Event in the previous day",
            expression=outside_window,
            hard=True,
        )
        self.assertEqual(
            Decision.UNKNOWN,
            evaluate_criterion(patient, exclusion).decision,
        )

    def test_evidence_metrics_use_independent_relevance(self) -> None:
        retrieved = ["noise", "a-renal", "other"]
        relevant = {"a-renal"}
        self.assertEqual(0.0, evidence_recall_at_k(retrieved, relevant, k=1))
        self.assertEqual(1.0, evidence_recall_at_k(retrieved, relevant, k=2))
        self.assertEqual(0.5, reciprocal_rank(retrieved, relevant))

    def test_unsupported_expression_shape_fails(self) -> None:
        with self.assertRaises(ValueError):
            ConditionExpression(expression_type=ExpressionType.ALL)

    def test_declared_type_and_operator_are_enforced(self) -> None:
        with self.assertRaises(TypeError):
            TypedValue(ValueType.NUMBER, "not-a-number")

        patient = Patient(
            patient_id="typed",
            index_date=date(2026, 1, 1),
            facts=(
                Fact(
                    fact_id="status",
                    field="status",
                    value=TypedValue(ValueType.STRING, "stable"),
                    evidence_ids=("status-evidence",),
                ),
            ),
            evidence=(
                Evidence(
                    evidence_id="status-evidence",
                    source_id="synthetic-status",
                    text="Synthetic status value.",
                ),
            ),
        )
        criterion = Criterion(
            criterion_id="invalid-string-order",
            criterion_type=CriterionType.INCLUSION,
            description="Ordering strings is not permitted",
            expression=atom(
                "invalid-string-order-atom",
                "status",
                ComparisonOperator.GT,
                "baseline",
                ValueType.STRING,
            ),
        )
        decision = evaluate_criterion(patient, criterion)
        self.assertEqual(Decision.UNKNOWN, decision.decision)
        self.assertIn("invalid for string", decision.atomic_decisions[0].reason)


if __name__ == "__main__":
    unittest.main()
