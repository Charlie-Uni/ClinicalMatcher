import copy
import unittest
from datetime import date

from clinical_matcher.apixaban_single_trial import (
    ApixabanSingleTrialError,
    RULE_IDS,
    build_intended_trial,
    load_intended_rule_contract,
    project_intended_class,
    project_mentor_reference_class,
    validate_intended_rule_contract,
)
from clinical_matcher.models import (
    Decision,
    Evidence,
    Fact,
    Patient,
    TypedValue,
    ValueType,
)
from clinical_matcher.pipeline import evaluate_criterion
from clinical_matcher.splits import canonical_sha256


PASSING_FACTS = {
    "atrial_fibrillation": (ValueType.BOOLEAN, True, None),
    "afib_ablation": (ValueType.BOOLEAN, False, None),
    "valvular_disease_requiring_surgery": (ValueType.BOOLEAN, False, None),
    "hemorrhagic_tendency_or_blood_dyscrasia": (ValueType.BOOLEAN, False, None),
    "peptic_ulcer_disease": (ValueType.BOOLEAN, False, None),
    "serious_bleeding_within_6_months": (ValueType.BOOLEAN, False, None),
    "chads2_score": (ValueType.NUMBER, 3, None),
    "left_ventricular_ejection_fraction": (ValueType.NUMBER, 50, "%"),
    "stroke_during_admission_or_within_last_month": (
        ValueType.BOOLEAN,
        False,
        None,
    ),
    "prior_stroke_or_tia": (ValueType.BOOLEAN, False, None),
    "heart_failure": (ValueType.BOOLEAN, False, None),
    "platelet_count": (ValueType.NUMBER, 100, "10^3/uL"),
    "total_bilirubin": (ValueType.NUMBER, 1.8, "mg/dL"),
    "aspartate_aminotransferase": (ValueType.NUMBER, 80, "U/L"),
    "serum_creatinine": (ValueType.NUMBER, 2.5, "mg/dL"),
    "hemoglobin": (ValueType.NUMBER, 10, "g/dL"),
    "major_depressive_disorder": (ValueType.BOOLEAN, False, None),
    "schizophrenia_or_schizoaffective_disorder": (
        ValueType.BOOLEAN,
        False,
        None,
    ),
    "bipolar_disorder": (ValueType.BOOLEAN, False, None),
    "unable_to_make_medical_decisions": (ValueType.BOOLEAN, False, None),
    "diabetes_mellitus": (ValueType.BOOLEAN, False, None),
    "treated_arterial_hypertension": (ValueType.BOOLEAN, False, None),
    "blood_glucose": (ValueType.NUMBER, 120, "mg/dL"),
}


def synthetic_patient(*, overrides=None, omitted=()) -> Patient:
    values = dict(PASSING_FACTS)
    values.update(overrides or {})
    for field in omitted:
        values.pop(field)
    evidence = []
    facts = []
    for index, (field, (value_type, value, unit)) in enumerate(values.items()):
        evidence_id = f"synthetic-evidence-{index}"
        evidence.append(
            Evidence(
                evidence_id=evidence_id,
                source_id="synthetic-note",
                text=f"Synthetic evidence for {field}.",
            )
        )
        facts.append(
            Fact(
                fact_id=f"synthetic-fact-{index}",
                field=field,
                value=TypedValue(value_type=value_type, value=value, unit=unit),
                evidence_ids=(evidence_id,),
            )
        )
    return Patient(
        patient_id="synthetic-patient",
        index_date=date(2026, 1, 1),
        facts=tuple(facts),
        evidence=tuple(evidence),
    )


class ApixabanSingleTrialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_intended_rule_contract()
        cls.trial = build_intended_trial(cls.contract)
        cls.criteria = {
            criterion.criterion_id: criterion for criterion in cls.trial.criteria
        }

    def decision(self, rule_id: str, patient: Patient) -> Decision:
        return evaluate_criterion(patient, self.criteria[rule_id]).decision

    def test_contract_freezes_provenance_and_five_hard_rules(self) -> None:
        provenance = self.contract["provenance"]
        semantics = self.contract["semantics"]
        self.assertEqual(
            "mentor_designated_rule_derived_project_ground_truth",
            provenance["mentor_ground_truth_role"],
        )
        self.assertFalse(provenance["independent_clinical_gold"])
        self.assertFalse(provenance["result_generator_available"])
        self.assertFalse(provenance["validation_labels_used"])
        self.assertFalse(provenance["locked_test_labels_used"])
        self.assertFalse(semantics["validation_run_authorized"])
        self.assertTrue(semantics["unit_assignment_is_mapping_assumption"])
        self.assertFalse(semantics["clinical_unit_safety_claim_allowed"])
        self.assertEqual(
            "one_normalized_fact_per_patient_question",
            semantics["input_fact_cardinality"],
        )
        self.assertEqual(RULE_IDS, tuple(self.criteria))
        self.assertTrue(all(criterion.hard for criterion in self.trial.criteria))
        self.assertTrue(
            all(
                criterion.source.source_id.startswith(
                    "apixaban-intended-rule-contract:"
                )
                for criterion in self.trial.criteria
            )
        )
        self.assertTrue(
            all(
                criterion.source.document_version
                == self.contract["contract_sha256"]
                for criterion in self.trial.criteria
            )
        )

    def test_hash_and_semantics_are_both_fail_closed(self) -> None:
        tampered = copy.deepcopy(self.contract)
        tampered["rules"][0]["description"] = "changed"
        with self.assertRaisesRegex(ApixabanSingleTrialError, "hash mismatch"):
            validate_intended_rule_contract(tampered)

        semantic_tamper = copy.deepcopy(self.contract)
        semantic_tamper["rules"][3]["expression"]["children"][3]["atom"][
            "expected"
        ]["value"] = True
        unsigned = dict(semantic_tamper)
        unsigned.pop("contract_sha256")
        semantic_tamper["contract_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(ApixabanSingleTrialError, "Intended atom changed"):
            validate_intended_rule_contract(semantic_tamper)

    def test_all_rule_boundaries_pass(self) -> None:
        patient = synthetic_patient()
        self.assertEqual(
            [Decision.ELIGIBLE] * 5,
            [self.decision(rule_id, patient) for rule_id in RULE_IDS],
        )

    def test_each_hard_rule_rejects_explicit_counterexamples(self) -> None:
        cases = (
            ("apixaban-rule-1", "atrial_fibrillation", ValueType.BOOLEAN, False, None),
            (
                "apixaban-rule-1",
                "serious_bleeding_within_6_months",
                ValueType.BOOLEAN,
                True,
                None,
            ),
            ("apixaban-rule-2", "chads2_score", ValueType.NUMBER, 4, None),
            (
                "apixaban-rule-2",
                "left_ventricular_ejection_fraction",
                ValueType.NUMBER,
                49,
                "%",
            ),
            ("apixaban-rule-2", "prior_stroke_or_tia", ValueType.BOOLEAN, True, None),
            ("apixaban-rule-3", "platelet_count", ValueType.NUMBER, 99, "10^3/uL"),
            ("apixaban-rule-3", "total_bilirubin", ValueType.NUMBER, 1.9, "mg/dL"),
            (
                "apixaban-rule-3",
                "aspartate_aminotransferase",
                ValueType.NUMBER,
                81,
                "U/L",
            ),
            ("apixaban-rule-3", "hemoglobin", ValueType.NUMBER, 9.9, "g/dL"),
            (
                "apixaban-rule-4",
                "major_depressive_disorder",
                ValueType.BOOLEAN,
                True,
                None,
            ),
            (
                "apixaban-rule-4",
                "schizophrenia_or_schizoaffective_disorder",
                ValueType.BOOLEAN,
                True,
                None,
            ),
            ("apixaban-rule-4", "bipolar_disorder", ValueType.BOOLEAN, True, None),
        )
        for rule_id, field, value_type, value, unit in cases:
            with self.subTest(rule_id=rule_id, field=field):
                patient = synthetic_patient(
                    overrides={field: (value_type, value, unit)}
                )
                self.assertIs(self.decision(rule_id, patient), Decision.INELIGIBLE)

    def test_rule_one_uses_frozen_or_branch(self) -> None:
        one_branch_passes = synthetic_patient(
            overrides={
                "afib_ablation": (ValueType.BOOLEAN, True, None),
                "valvular_disease_requiring_surgery": (
                    ValueType.BOOLEAN,
                    False,
                    None,
                ),
            }
        )
        both_branches_fail = synthetic_patient(
            overrides={
                "afib_ablation": (ValueType.BOOLEAN, True, None),
                "valvular_disease_requiring_surgery": (
                    ValueType.BOOLEAN,
                    True,
                    None,
                ),
            }
        )
        self.assertIs(
            self.decision("apixaban-rule-1", one_branch_passes),
            Decision.ELIGIBLE,
        )
        self.assertIs(
            self.decision("apixaban-rule-1", both_branches_fail),
            Decision.INELIGIBLE,
        )

    def test_rule_two_missing_value_abstains(self) -> None:
        patient = synthetic_patient(omitted=("left_ventricular_ejection_fraction",))
        self.assertIs(
            self.decision("apixaban-rule-2", patient),
            Decision.UNKNOWN,
        )

    def test_rule_three_requires_all_thresholds_and_exact_units(self) -> None:
        abnormal = synthetic_patient(
            overrides={
                "serum_creatinine": (ValueType.NUMBER, 2.6, "mg/dL"),
            }
        )
        incompatible_unit = synthetic_patient(
            overrides={
                "serum_creatinine": (ValueType.NUMBER, 230, "umol/L"),
            }
        )
        self.assertIs(
            self.decision("apixaban-rule-3", abnormal),
            Decision.INELIGIBLE,
        )
        self.assertIs(
            self.decision("apixaban-rule-3", incompatible_unit),
            Decision.UNKNOWN,
        )

    def test_rule_four_uses_incapacity_polarity(self) -> None:
        lacks_capacity = synthetic_patient(
            overrides={
                "unable_to_make_medical_decisions": (
                    ValueType.BOOLEAN,
                    True,
                    None,
                )
            }
        )
        self.assertIs(
            self.decision("apixaban-rule-4", lacks_capacity),
            Decision.INELIGIBLE,
        )

    def test_rule_five_accepts_either_frozen_branch(self) -> None:
        no_metabolic_risk = synthetic_patient()
        controlled_diabetes = synthetic_patient(
            overrides={
                "diabetes_mellitus": (ValueType.BOOLEAN, True, None),
                "treated_arterial_hypertension": (
                    ValueType.BOOLEAN,
                    True,
                    None,
                ),
                "blood_glucose": (ValueType.NUMBER, 180, "mg/dL"),
            }
        )
        uncontrolled_diabetes = synthetic_patient(
            overrides={
                "diabetes_mellitus": (ValueType.BOOLEAN, True, None),
                "blood_glucose": (ValueType.NUMBER, 181, "mg/dL"),
            }
        )
        missing_glucose = synthetic_patient(
            overrides={"diabetes_mellitus": (ValueType.BOOLEAN, True, None)},
            omitted=("blood_glucose",),
        )
        self.assertIs(
            self.decision("apixaban-rule-5", no_metabolic_risk),
            Decision.ELIGIBLE,
        )
        self.assertIs(
            self.decision("apixaban-rule-5", controlled_diabetes),
            Decision.ELIGIBLE,
        )
        self.assertIs(
            self.decision("apixaban-rule-5", uncontrolled_diabetes),
            Decision.INELIGIBLE,
        )
        self.assertIs(
            self.decision("apixaban-rule-5", missing_glucose),
            Decision.UNKNOWN,
        )

    def test_rule_decisions_project_to_four_outcomes(self) -> None:
        eligible = {rule_id: Decision.ELIGIBLE for rule_id in RULE_IDS}
        self.assertEqual("ideal", project_intended_class(eligible))

        semi = dict(eligible)
        semi[RULE_IDS[4]] = Decision.INELIGIBLE
        self.assertEqual("semi-ideal", project_intended_class(semi))

        excluded = dict(eligible)
        excluded[RULE_IDS[1]] = Decision.INELIGIBLE
        self.assertEqual("non-ideal", project_intended_class(excluded))

        unresolved = dict(eligible)
        unresolved[RULE_IDS[1]] = Decision.UNKNOWN
        self.assertEqual("unknown", project_intended_class(unresolved))

        with self.assertRaisesRegex(ApixabanSingleTrialError, "all five rules"):
            project_intended_class({RULE_IDS[0]: Decision.ELIGIBLE})

    def test_mentor_flags_project_by_frozen_precedence(self) -> None:
        self.assertEqual(
            "ideal",
            project_mentor_reference_class(
                ideal_candidate=True,
                semi_ideal_candidate=True,
            ),
        )
        self.assertEqual(
            "semi-ideal",
            project_mentor_reference_class(
                ideal_candidate=False,
                semi_ideal_candidate=True,
            ),
        )
        self.assertEqual(
            "non-ideal",
            project_mentor_reference_class(
                ideal_candidate=False,
                semi_ideal_candidate=False,
            ),
        )
        with self.assertRaisesRegex(ApixabanSingleTrialError, "subset"):
            project_mentor_reference_class(
                ideal_candidate=True,
                semi_ideal_candidate=False,
            )
        with self.assertRaisesRegex(ApixabanSingleTrialError, "booleans"):
            project_mentor_reference_class(
                ideal_candidate=1,
                semi_ideal_candidate=True,
            )


if __name__ == "__main__":
    unittest.main()
