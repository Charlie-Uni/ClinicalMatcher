import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.gold_readiness import (
    BenchmarkNotReadyError,
    GoldAuditCounts,
    assert_benchmark_ready,
    build_gold_readiness_report,
)
from clinical_matcher.ingestion.snapshots import (
    TrialSelection,
    build_trial_snapshot,
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


if __name__ == "__main__":
    unittest.main()
