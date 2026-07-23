import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.evaluation_runner import evaluate_fixture_run
from clinical_matcher.reporting import render_markdown, write_report
from clinical_matcher.splits import generate_split_manifest


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


class ReportingTest(unittest.TestCase):
    def build_report(self, directory: Path):
        manifest_document = generate_split_manifest(
            fixture_path=FIXTURE,
            strategy="patient_holdout",
            seed=17,
            test_fraction=0.5,
            dataset_id="synthetic-report-test",
            code_commit="a" * 40,
            generated_at="2026-07-23T12:00:00Z",
            generation_command="test:report",
        )
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest_document),
            encoding="utf-8",
        )
        return evaluate_fixture_run(
            fixture_path=FIXTURE,
            manifest_path=manifest_path,
            split_name="test",
            k=2,
            bootstrap_samples=20,
            code_commit="b" * 40,
            generated_at="2026-07-23T13:00:00Z",
        )

    def test_report_records_provenance_metrics_and_cluster_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = self.build_report(Path(directory))
        self.assertEqual("1.0.0", report["report_version"])
        self.assertEqual("b" * 40, report["provenance"]["code_commit"])
        self.assertEqual(
            "patient",
            report["configuration"]["bootstrap_unit"],
        )
        self.assertEqual(
            1.0,
            report["metrics"]["decision"]["macro_f1"],
        )
        self.assertAlmostEqual(
            2 / 3,
            report["metrics"]["decision"]["macro_f1_all_classes"],
        )
        self.assertEqual(
            1,
            report["metrics"]["bootstrap"]["criterion_macro_f1"][
                "cluster_count"
            ],
        )
        self.assertEqual(
            1.0,
            report["metrics"]["retrieval"]["evidence_recall_at_2"],
        )
        self.assertEqual(
            1.0,
            report["metrics"]["ranking"]["trial_ndcg_at_2"],
        )

    def test_run_id_is_stable_and_json_markdown_are_both_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.build_report(root)
            second = self.build_report(root)
            self.assertEqual(first["run_id"], second["run_id"])
            json_path, markdown_path = write_report(
                first,
                root / "output",
            )
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(first["run_id"], loaded["run_id"])
            markdown = markdown_path.read_text(encoding="utf-8")
            self.assertIn("# ClinicalMatcher run report", markdown)
            self.assertIn("Bootstrap unit", markdown)
            self.assertIn("Error attribution", markdown)
            self.assertEqual(markdown, render_markdown(first))

    def test_reproducibility_identifiers_cannot_be_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_document = generate_split_manifest(
                fixture_path=FIXTURE,
                strategy="patient_holdout",
                seed=17,
                test_fraction=0.5,
                dataset_id="synthetic-report-test",
                code_commit="a" * 40,
                generated_at="2026-07-23T12:00:00Z",
            )
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest_document),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "required"):
                evaluate_fixture_run(
                    fixture_path=FIXTURE,
                    manifest_path=manifest_path,
                    split_name="test",
                    bootstrap_samples=10,
                    model_ids=(),
                )


if __name__ == "__main__":
    unittest.main()
