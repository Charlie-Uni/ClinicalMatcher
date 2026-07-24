import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.capacity import CapacityAssumptions, build_capacity_plan
from clinical_matcher.gold_readiness import (
    BenchmarkNotReadyError,
    GoldAuditCounts,
    assert_benchmark_ready,
    build_gold_readiness_report,
)
from clinical_matcher.ingestion.snapshots import (
    TrialSelection,
    build_benchmark_trial_snapshot,
    build_trial_snapshot,
)
from clinical_matcher.ingestion.trial_selection import (
    ReproducibleTrialSelection,
    TrialFilterPolicy,
)


class GoldReadinessTest(unittest.TestCase):
    def setUp(self) -> None:
        response = json.loads(
            Path(
                "fixtures/synthetic/clinicaltrials_api_search_response.json"
            ).read_text(encoding="utf-8")
        )
        version = json.loads(
            Path(
                "fixtures/synthetic/clinicaltrials_api_version.json"
            ).read_text(encoding="utf-8")
        )
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.snapshot_dir = Path(self.temporary_directory.name) / "snapshot"
        self.manifest = build_trial_snapshot(
            studies=response["studies"],
            version_payload=version,
            selection=TrialSelection(
                disease_domain="atrial_fibrillation",
                rationale="Synthetic multi-trial selection",
                query_parameters={
                    "query.cond": "Atrial Fibrillation",
                    "sort": "LastUpdatePostDate:desc",
                },
            ),
            output_dir=self.snapshot_dir,
            created_at="2026-07-23T15:00:00Z",
            builder_code_commit="e" * 40,
        )
        capacity_plan = build_capacity_plan(
            assumptions=CapacityAssumptions(
                annotator_count=2,
                hours_per_annotator=10,
                required_annotations_per_unit=2,
                minutes_per_annotation=15,
                expected_adjudication_rate=0.2,
                minutes_per_adjudication=10,
                reserve_fraction=0.2,
                estimate_source="pilot_measurement",
                pilot_unit_count=8,
            ),
            minimum_trials=2,
            maximum_trials=2,
            minimum_patients_per_trial=5,
            selected_trial_count=2,
            code_commit="e" * 40,
        )
        self.benchmark_manifest = build_benchmark_trial_snapshot(
            studies=response["studies"],
            version_payload=version,
            registry_reported_total_count=3,
            pages_fetched=1,
            selection=ReproducibleTrialSelection(
                disease_domain="atrial_fibrillation",
                rationale="Synthetic capacity-bound benchmark",
                query_parameters={
                    "query.cond": "Atrial Fibrillation",
                    "filter.overallStatus": (
                        "RECRUITING|NOT_YET_RECRUITING"
                    ),
                },
                filters=TrialFilterPolicy(
                    study_types=("INTERVENTIONAL",),
                    overall_statuses=(
                        "RECRUITING",
                        "NOT_YET_RECRUITING",
                    ),
                    require_eligibility_text=True,
                    first_posted_from="2024-01-01",
                    first_posted_to="2025-12-31",
                ),
            ),
            capacity_plan=capacity_plan,
            output_dir=Path(self.temporary_directory.name)
            / "benchmark-snapshot",
            created_at="2026-07-23T15:00:00Z",
            builder_code_commit="e" * 40,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_patient_trial_gold_blocks_benchmark_claim(self) -> None:
        report = build_gold_readiness_report(
            snapshot_manifest=self.manifest,
            counts=GoldAuditCounts(),
            gold_source_description="No adjudicated restricted gold available",
            generated_at="2026-07-23T16:00:00Z",
        )
        self.assertEqual("not_ready", report["status"])
        self.assertFalse(report["benchmark_release_allowed"])
        self.assertIn("no_gold_patients", report["blocking_gaps"])
        with self.assertRaises(BenchmarkNotReadyError):
            assert_benchmark_ready(report)

    def test_complete_dual_adjudicated_gold_passes_gate(self) -> None:
        report = build_gold_readiness_report(
            snapshot_manifest=self.manifest,
            counts=GoldAuditCounts(
                patient_count=3,
                trial_count=2,
                expected_patient_trial_pairs=6,
                adjudicated_patient_trial_pairs=6,
                expected_criterion_units=15,
                adjudicated_criterion_units=15,
                minimum_annotators_per_unit=2,
                unresolved_adjudications=0,
            ),
            gold_source_description=(
                "Synthetic independently annotated and adjudicated gold"
            ),
            generated_at="2026-07-23T16:00:00Z",
            counts_provenance="validated_annotation_records",
        )
        self.assertEqual("ready", report["status"])
        self.assertTrue(report["benchmark_release_allowed"])
        self.assertEqual([], report["blocking_gaps"])
        assert_benchmark_ready(report)

    def test_partial_criterion_gold_is_not_ready(self) -> None:
        report = build_gold_readiness_report(
            snapshot_manifest=self.manifest,
            counts=GoldAuditCounts(
                patient_count=3,
                trial_count=2,
                expected_patient_trial_pairs=6,
                adjudicated_patient_trial_pairs=6,
                expected_criterion_units=15,
                adjudicated_criterion_units=14,
                minimum_annotators_per_unit=2,
            ),
            gold_source_description="Synthetic partial gold",
        )
        self.assertIn(
            "criterion_evidence_gold_incomplete",
            report["blocking_gaps"],
        )

    def test_manually_entered_complete_counts_cannot_unlock_release(self) -> None:
        report = build_gold_readiness_report(
            snapshot_manifest=self.manifest,
            counts=GoldAuditCounts(
                patient_count=3,
                trial_count=2,
                expected_patient_trial_pairs=6,
                adjudicated_patient_trial_pairs=6,
                expected_criterion_units=15,
                adjudicated_criterion_units=15,
                minimum_annotators_per_unit=2,
            ),
            gold_source_description="Manually entered aggregate counts",
        )
        self.assertFalse(report["benchmark_release_allowed"])
        self.assertIn(
            "gold_counts_not_derived_from_validated_records",
            report["blocking_gaps"],
        )

    def test_truncated_trial_selection_cannot_unlock_release(self) -> None:
        truncated = copy.deepcopy(self.manifest)
        truncated["search"]["selection_truncated"] = True
        report = build_gold_readiness_report(
            snapshot_manifest=truncated,
            counts=GoldAuditCounts(
                patient_count=3,
                trial_count=2,
                expected_patient_trial_pairs=6,
                adjudicated_patient_trial_pairs=6,
                expected_criterion_units=15,
                adjudicated_criterion_units=15,
                minimum_annotators_per_unit=2,
            ),
            gold_source_description="Synthetic validated gold",
            counts_provenance="validated_annotation_records",
        )
        self.assertIn("trial_selection_is_truncated", report["blocking_gaps"])

    def test_capacity_bound_gold_must_match_planned_grid(self) -> None:
        report = build_gold_readiness_report(
            snapshot_manifest=self.benchmark_manifest,
            counts=GoldAuditCounts(
                patient_count=14,
                trial_count=2,
                expected_patient_trial_pairs=28,
                adjudicated_patient_trial_pairs=28,
                expected_criterion_units=50,
                adjudicated_criterion_units=50,
                minimum_annotators_per_unit=2,
            ),
            gold_source_description="Synthetic validated gold",
            counts_provenance="validated_annotation_records",
        )
        self.assertIn(
            "gold_patient_count_differs_from_capacity_plan",
            report["blocking_gaps"],
        )
        self.assertIn(
            "gold_units_differ_from_capacity_plan",
            report["blocking_gaps"],
        )


if __name__ == "__main__":
    unittest.main()
