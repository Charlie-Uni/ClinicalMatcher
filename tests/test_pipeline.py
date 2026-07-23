import unittest
from pathlib import Path

from clinical_matcher.fixture import load_fixture
from clinical_matcher.models import Criterion, CriterionType, Decision, Patient
from clinical_matcher.pipeline import evaluate_criterion, match_patient


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.patients, cls.trials, cls.expected = load_fixture(FIXTURE)

    def test_expected_rankings(self) -> None:
        for patient in self.patients:
            actual = [item.trial_id for item in match_patient(patient, self.trials)]
            self.assertEqual(self.expected[patient.patient_id], actual)

    def test_exclusion_polarity(self) -> None:
        patient = self.patients[0]
        criterion = Criterion(
            criterion_id="exclude-diabetes",
            criterion_type=CriterionType.EXCLUSION,
            field="diabetes",
            operator="==",
            value=True,
            description="Diabetes excludes participation",
        )
        decision = evaluate_criterion(patient, criterion)
        self.assertEqual(Decision.INELIGIBLE, decision.decision)
        self.assertEqual(("a-diabetes",), decision.evidence_ids)

    def test_missing_fact_abstains(self) -> None:
        patient = Patient(patient_id="missing", facts={}, evidence=())
        criterion = Criterion(
            criterion_id="requires-lab",
            criterion_type=CriterionType.INCLUSION,
            field="missing_lab",
            operator=">=",
            value=1,
            description="A fictional lab is required",
        )
        decision = evaluate_criterion(patient, criterion)
        self.assertEqual(Decision.UNKNOWN, decision.decision)
        self.assertEqual((), decision.evidence_ids)

    def test_unsupported_operator_fails_closed(self) -> None:
        criterion = Criterion(
            criterion_id="invalid",
            criterion_type=CriterionType.INCLUSION,
            field="age_years",
            operator="approximately",
            value=50,
            description="Invalid test criterion",
        )
        with self.assertRaises(ValueError):
            evaluate_criterion(self.patients[0], criterion)


if __name__ == "__main__":
    unittest.main()
