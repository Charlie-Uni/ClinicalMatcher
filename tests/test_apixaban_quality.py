import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_quality import (
    ApixabanQualityError,
    build_apixaban_quality_reports,
    validate_public_quality_report,
    validate_quality_report_pair,
    verify_apixaban_quality_report_files,
    write_apixaban_quality_reports,
)
from tests.test_apixaban_benchmark import (
    SYNTHETIC_COUNTS,
    build_synthetic,
)


def build_quality(minimum_cell_size=2, approval_reference=None):
    benchmark, manifest = build_synthetic()
    return build_apixaban_quality_reports(
        benchmark,
        manifest,
        minimum_cell_size=minimum_cell_size,
        governance_approval_reference=approval_reference,
        generated_at="2026-08-13T04:00:00Z",
        code_commit="1" * 40,
        required_source_sha256=None,
        required_counts=SYNTHETIC_COUNTS,
    )


class ApixabanQualityTest(unittest.TestCase):
    def test_restricted_report_reconciles_grid_and_preserves_anomaly(self):
        restricted, public = build_quality()
        totals = restricted["totals"]
        self.assertEqual(2, totals["patient_count"])
        self.assertEqual(23, totals["question_count"])
        self.assertEqual(46, totals["assessment_count"])
        self.assertEqual(0, totals["missing_patient_question_pair_count"])
        self.assertEqual(0, totals["duplicate_patient_question_pair_count"])
        self.assertEqual(2, totals["complete_patient_count"])
        self.assertEqual(1, totals["source_anomaly_count"])
        self.assertTrue(restricted["quality"]["source_anomalies_preserved"])
        self.assertEqual("pending_review", public["disclosure_control"][
            "governance_status"
        ])

    def test_numeric_ranges_are_local_and_never_removed_or_invented(self):
        restricted, public = build_quality()
        numeric_pairs = [
            (private, released)
            for private, released in zip(
                restricted["questions"], public["questions"]
            )
            if private["question_type"] == "numeric"
        ]
        self.assertEqual(8, len(numeric_pairs))
        for private, released in numeric_pairs:
            self.assertIsNotNone(private["numeric_summary"]["minimum"])
            self.assertIsNotNone(private["numeric_summary"]["maximum"])
            self.assertEqual(0, private["numeric_summary"][
                "removed_value_count"
            ])
            self.assertIsNone(released["numeric_summary"]["minimum"])
            self.assertIsNone(released["numeric_summary"]["maximum"])
            self.assertIsNone(released["numeric_summary"][
                "flagged_implausible_value_count"
            ])

    def test_positive_small_cells_and_complements_are_suppressed(self):
        _, public = build_quality(minimum_cell_size=2)
        source_totals = {
            name: public["totals"][name]
            for name in (
                "answered_source_count",
                "not_specified_source_count",
                "source_anomaly_count",
            )
        }
        self.assertTrue(source_totals["source_anomaly_count"]["suppressed"])
        self.assertGreaterEqual(
            sum(cell["suppressed"] for cell in source_totals.values()), 2
        )
        for question in public["questions"]:
            for group_name in ("fact_status_counts", "source_status_counts"):
                cells = question[group_name].values()
                visible = [
                    cell["value"]
                    for cell in cells
                    if not cell["suppressed"]
                ]
                self.assertTrue(
                    all(value == 0 or value >= 2 for value in visible)
                )

    def test_suppressed_unknown_count_also_suppresses_rate(self):
        _, public = build_quality(minimum_cell_size=2)
        affected = [
            question
            for question in public["questions"]
            if question["fact_status_counts"]["unknown"]["suppressed"]
        ]
        self.assertTrue(affected)
        for question in affected:
            self.assertTrue(question["unknown_rate"]["suppressed"])
            self.assertIsNone(question["unknown_rate"]["value"])

    def test_public_report_has_no_patient_or_benchmark_fingerprint(self):
        _, public = build_quality()
        serialized = json.dumps(public, sort_keys=True)
        for forbidden in (
            "patient_id",
            "assessment_id",
            "benchmark_sha256",
            "benchmark_manifest_sha256",
            "code_commit",
        ):
            self.assertNotIn(f'"{forbidden}"', serialized)

    def test_public_small_cell_tampering_is_rejected(self):
        _, public = build_quality()
        mutated = copy.deepcopy(public)
        cell = mutated["questions"][0]["fact_status_counts"]["present"]
        cell.update(
            {
                "value": 1,
                "suppressed": False,
                "suppression_reason": None,
            }
        )
        unsigned = dict(mutated)
        unsigned.pop("report_sha256")
        from clinical_matcher.splits import canonical_sha256

        mutated["report_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(
            ApixabanQualityError, "below threshold"
        ):
            validate_public_quality_report(mutated)

    def test_approval_requires_recorded_reference(self):
        _, pending = build_quality()
        self.assertFalse(pending["disclosure_control"]["release_authorized"])
        _, approved = build_quality(
            approval_reference="Synthetic governance approval TEST-001"
        )
        self.assertEqual(
            "approved", approved["disclosure_control"]["governance_status"]
        )
        self.assertTrue(approved["disclosure_control"]["release_authorized"])

    def test_writes_owner_only_refuses_overwrite_and_verifies(self):
        restricted, public = build_quality()
        with tempfile.TemporaryDirectory() as directory:
            private_path = Path(directory) / "quality.json"
            public_path = Path(directory) / "quality.public.json"
            written = write_apixaban_quality_reports(
                restricted,
                public,
                private_path,
                public_path,
                required_counts=SYNTHETIC_COUNTS,
            )
            self.assertEqual((private_path, public_path), written)
            for path in written:
                self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            verified, projection = verify_apixaban_quality_report_files(
                private_path,
                public_path,
                required_counts=SYNTHETIC_COUNTS,
            )
            self.assertEqual(46, verified["totals"]["assessment_count"])
            self.assertFalse(
                projection["disclosure_control"]["release_authorized"]
            )
            with self.assertRaises(FileExistsError):
                write_apixaban_quality_reports(
                    restricted,
                    public,
                    private_path,
                    public_path,
                    required_counts=SYNTHETIC_COUNTS,
                )

    def test_public_projection_cannot_change_visible_count(self):
        restricted, public = build_quality()
        mutated = copy.deepcopy(public)
        mutated["questions"][0]["assessment_count"] += 1
        unsigned = dict(mutated)
        unsigned.pop("report_sha256")
        from clinical_matcher.splits import canonical_sha256

        mutated["report_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(
            ApixabanQualityError, "metadata changed"
        ):
            validate_quality_report_pair(
                restricted, mutated, required_counts=SYNTHETIC_COUNTS
            )


if __name__ == "__main__":
    unittest.main()
