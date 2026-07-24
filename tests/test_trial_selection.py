import copy
import json
import unittest
from pathlib import Path

from clinical_matcher.capacity import CapacityAssumptions, build_capacity_plan
from clinical_matcher.ingestion.trial_selection import (
    ReproducibleTrialSelection,
    TrialFilterPolicy,
    TrialSelectionError,
    select_trials,
    validate_selection_audit,
)


SEARCH_FIXTURE = Path(
    "fixtures/synthetic/clinicaltrials_api_search_response.json"
)


def capacity_plan():
    return build_capacity_plan(
        assumptions=CapacityAssumptions(
            annotator_count=2,
            hours_per_annotator=10,
            required_annotations_per_unit=2,
            minutes_per_annotation=15,
            expected_adjudication_rate=0.2,
            minutes_per_adjudication=10,
            reserve_fraction=0.2,
            estimate_source="validated_pilot_summary",
            pilot_unit_count=8,
            pilot_summary_sha256="b" * 64,
        ),
        minimum_trials=2,
        maximum_trials=3,
        minimum_patients_per_trial=5,
        selected_trial_count=2,
        generated_at="2026-07-23T18:00:00Z",
        code_commit="a" * 40,
    )


def policy():
    return ReproducibleTrialSelection(
        disease_domain="atrial_fibrillation",
        rationale=(
            "All registry AF hits are explicitly filtered, then sampled by "
            "seeded NCT hash within annotation capacity."
        ),
        query_parameters={
            "query.cond": "Atrial Fibrillation",
            "filter.overallStatus": "RECRUITING|NOT_YET_RECRUITING",
            "format": "json",
            "markupFormat": "markdown",
            "countTotal": "true",
            "pageSize": "1000",
        },
        filters=TrialFilterPolicy(
            study_types=("INTERVENTIONAL",),
            overall_statuses=("RECRUITING", "NOT_YET_RECRUITING"),
            require_eligibility_text=True,
            first_posted_from="2024-01-01",
            first_posted_to="2025-12-31",
        ),
    )


class TrialSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        response = json.loads(SEARCH_FIXTURE.read_text(encoding="utf-8"))
        cls.base_studies = response["studies"]

    def candidates(self):
        studies = copy.deepcopy(self.base_studies)
        extra = copy.deepcopy(studies[0])
        extra["protocolSection"]["identificationModule"]["nctId"] = "NCT99999996"
        extra["protocolSection"]["identificationModule"][
            "briefTitle"
        ] = "Synthetic Additional Interventional AF Study"
        extra["protocolSection"]["statusModule"][
            "lastUpdatePostDateStruct"
        ]["date"] = "2024-02-01"
        studies.append(extra)
        return studies

    def test_audits_total_filter_and_capacity_bound_sample(self) -> None:
        studies = self.candidates()
        excluded = studies[2]["protocolSection"]
        excluded["statusModule"]["overallStatus"] = "COMPLETED"
        excluded["statusModule"]["studyFirstPostDateStruct"][
            "date"
        ] = "2020-01-01"
        excluded["eligibilityModule"].pop("eligibilityCriteria")
        selected, audit = select_trials(
            studies=studies,
            registry_reported_total_count=4,
            selection=policy(),
            capacity_plan=capacity_plan(),
        )
        self.assertEqual(2, len(selected))
        self.assertEqual(
            {
                "registry_reported_total_count": 4,
                "fetched_candidate_count": 4,
                "filter_passed_count": 3,
                "filter_excluded_count": 1,
                "eligible_not_sampled_count": 1,
                "selected_count": 2,
            },
            audit["flow"],
        )
        self.assertEqual(
            {
                "eligibility_text_missing": 1,
                "first_posted_date_outside_range": 1,
                "recruitment_status_not_allowed": 1,
                "study_type_not_allowed": 1,
            },
            audit["filter_exclusion_reason_counts"],
        )
        self.assertFalse(
            audit["selection"]["sampling"]["registry_order_used_for_sampling"]
        )
        self.assertEqual(
            capacity_plan()["plan_sha256"],
            audit["selection"]["capacity_binding"]["capacity_plan_sha256"],
        )
        validate_selection_audit(audit)

    def test_sample_is_independent_of_registry_and_recency_order(self) -> None:
        studies = self.candidates()
        first, _ = select_trials(
            studies=studies,
            registry_reported_total_count=4,
            selection=policy(),
            capacity_plan=capacity_plan(),
        )
        for index, study in enumerate(studies):
            study["protocolSection"]["statusModule"][
                "lastUpdatePostDateStruct"
            ]["date"] = f"2025-01-{index + 1:02d}"
        second, _ = select_trials(
            studies=list(reversed(studies)),
            registry_reported_total_count=4,
            selection=policy(),
            capacity_plan=capacity_plan(),
        )
        self.assertEqual(
            {
                item["protocolSection"]["identificationModule"]["nctId"]
                for item in first
            },
            {
                item["protocolSection"]["identificationModule"]["nctId"]
                for item in second
            },
        )

    def test_incomplete_registry_fetch_cannot_be_sampled(self) -> None:
        with self.assertRaisesRegex(TrialSelectionError, "All registry"):
            select_trials(
                studies=self.base_studies,
                registry_reported_total_count=807,
                selection=policy(),
                capacity_plan=capacity_plan(),
            )

    def test_provisional_capacity_cannot_select_trials(self) -> None:
        provisional = build_capacity_plan(
            assumptions=CapacityAssumptions(
                annotator_count=2,
                hours_per_annotator=10,
                required_annotations_per_unit=2,
                minutes_per_annotation=15,
                expected_adjudication_rate=0.2,
                minutes_per_adjudication=10,
                reserve_fraction=0.2,
                estimate_source="planning_assumption",
            ),
            minimum_trials=2,
            maximum_trials=2,
            minimum_patients_per_trial=5,
            selected_trial_count=2,
            code_commit="a" * 40,
        )
        with self.assertRaisesRegex(TrialSelectionError, "provisional"):
            select_trials(
                studies=self.base_studies,
                registry_reported_total_count=3,
                selection=policy(),
                capacity_plan=provisional,
            )


if __name__ == "__main__":
    unittest.main()
