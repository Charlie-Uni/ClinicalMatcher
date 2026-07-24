import copy
import unittest

from clinical_matcher.capacity import (
    CapacityAssumptions,
    build_capacity_plan,
    validate_capacity_plan,
)


class CapacityPlanTest(unittest.TestCase):
    def assumptions(self, estimate_source="validated_pilot_summary"):
        return CapacityAssumptions(
            annotator_count=2,
            hours_per_annotator=10,
            required_annotations_per_unit=2,
            minutes_per_annotation=15,
            expected_adjudication_rate=0.2,
            minutes_per_adjudication=10,
            reserve_fraction=0.2,
            estimate_source=estimate_source,
            pilot_unit_count=(
                8
                if estimate_source
                in {"pilot_measurement", "validated_pilot_summary"}
                else 0
            ),
            pilot_summary_sha256=(
                "b" * 64
                if estimate_source == "validated_pilot_summary"
                else None
            ),
        )

    def test_reverse_plans_trial_patient_options_from_person_time(self) -> None:
        plan = build_capacity_plan(
            assumptions=self.assumptions(),
            minimum_trials=2,
            maximum_trials=5,
            minimum_patients_per_trial=5,
            selected_trial_count=3,
            generated_at="2026-07-23T18:00:00Z",
            code_commit="f" * 40,
        )
        self.assertEqual(30, plan["capacity"]["maximum_patient_trial_units"])
        self.assertEqual(
            [
                (2, 15),
                (3, 10),
                (4, 7),
                (5, 6),
            ],
            [
                (item["trial_count"], item["patient_count"])
                for item in plan["feasible_designs"]
            ],
        )
        self.assertEqual(30, plan["selected_design"]["patient_trial_units"])
        self.assertTrue(plan["snapshot_design_allowed"])
        validate_capacity_plan(plan)

    def test_unpiloted_assumptions_cannot_authorize_snapshot_design(self) -> None:
        plan = build_capacity_plan(
            assumptions=self.assumptions("planning_assumption"),
            minimum_trials=2,
            maximum_trials=3,
            minimum_patients_per_trial=5,
            selected_trial_count=3,
            code_commit="f" * 40,
        )
        self.assertEqual("provisional", plan["status"])
        self.assertFalse(plan["snapshot_design_allowed"])

    def test_hash_detects_capacity_plan_mutation(self) -> None:
        plan = build_capacity_plan(
            assumptions=self.assumptions(),
            minimum_trials=2,
            maximum_trials=3,
            minimum_patients_per_trial=5,
            selected_trial_count=3,
            code_commit="f" * 40,
        )
        mutated = copy.deepcopy(plan)
        mutated["assumptions"]["hours_per_annotator"] = 20
        with self.assertRaisesRegex(ValueError, "hash"):
            validate_capacity_plan(mutated)

    def test_pilot_source_requires_completed_units(self) -> None:
        assumptions = self.assumptions()
        assumptions = CapacityAssumptions(
            **{**assumptions.__dict__, "pilot_unit_count": 0}
        )
        with self.assertRaisesRegex(ValueError, "pilot"):
            assumptions.validate()

    def test_unvalidated_pilot_measurement_stays_provisional(self) -> None:
        plan = build_capacity_plan(
            assumptions=self.assumptions("pilot_measurement"),
            minimum_trials=2,
            maximum_trials=3,
            minimum_patients_per_trial=5,
            selected_trial_count=3,
            code_commit="f" * 40,
        )
        self.assertFalse(plan["snapshot_design_allowed"])

    def test_validated_source_requires_summary_hash(self) -> None:
        assumptions = self.assumptions()
        assumptions = CapacityAssumptions(
            **{**assumptions.__dict__, "pilot_summary_sha256": None}
        )
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            assumptions.validate()


if __name__ == "__main__":
    unittest.main()
