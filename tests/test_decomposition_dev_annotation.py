import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.decomposition_dev_annotation import (
    DecompositionDevAnnotationError,
    catalog_view,
    finalize_work,
    item_view,
    progress,
    set_expression,
    start_work,
    validate_work,
    write_new_private_json,
)
from clinical_matcher.decomposition_dev_package import verify_all


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "benchmarks/decomposition/af_decomposition_selection_1.2.0.json"
DEV_ROOT = ROOT / "benchmarks/decomposition/dev_sources_1.2.0"
CATALOG = ROOT / "benchmarks/decomposition/dev_concept_catalog_1.1.0.json"
ISSUES = ROOT / "benchmarks/decomposition/dev_annotation_issue_log_1.0.0.json"
PACKAGE = ROOT / "benchmarks/decomposition/dev_single_annotator_package_1.0.0.json"


class DecompositionDevAnnotationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.issue_log, cls.package = verify_all(
            selection_path=SELECTION,
            dev_source_root=DEV_ROOT,
            catalog_path=CATALOG,
            issue_path=ISSUES,
            package_path=PACKAGE,
        )

    def expression_for(self, criterion_id, *, field=None, span=None):
        source = next(
            item
            for item in self.package["items"]
            if item["criterion_id"] == criterion_id
        )
        return {
            "expression_type": "atom",
            "atom": {
                "condition_id": f"{criterion_id}:a01",
                "field": field or self.catalog["entries"][0]["field_id"],
                "operator": "==",
                "expected": {"value_type": "boolean", "value": True},
                "fact_selection": "any",
                "provenance": {
                    "source_id": source["source_id"],
                    "source_span": span or {"start": 0, "end": 1},
                    "method": "human",
                },
            },
        }

    def test_start_is_empty_and_bound_to_package(self):
        work = start_work(self.package, self.catalog)
        validate_work(self.package, self.catalog, work)
        self.assertEqual(40, progress(work)["remaining"])
        self.assertEqual(self.package["package_id"], work["source_package_id"])
        self.assertFalse(
            work["completion_attestation"]["human_authored_without_model_output"]
        )

    def test_one_expression_updates_progress_and_revalidates(self):
        work = start_work(self.package, self.catalog)
        criterion_id = work["items"][0]["criterion_id"]
        updated = set_expression(
            self.package,
            self.catalog,
            work,
            criterion_id,
            self.expression_for(criterion_id),
        )
        validate_work(self.package, self.catalog, updated)
        self.assertEqual(1, progress(updated)["completed"])
        self.assertNotEqual(work["work_sha256"], updated["work_sha256"])

    def test_invalid_field_and_span_fail_closed(self):
        work = start_work(self.package, self.catalog)
        criterion_id = work["items"][0]["criterion_id"]
        with self.assertRaises(DecompositionDevAnnotationError):
            set_expression(
                self.package,
                self.catalog,
                work,
                criterion_id,
                self.expression_for(criterion_id, field="invented_private_field"),
            )
        with self.assertRaises(DecompositionDevAnnotationError):
            set_expression(
                self.package,
                self.catalog,
                work,
                criterion_id,
                self.expression_for(criterion_id, span={"start": 0, "end": 99999}),
            )

    def test_incomplete_work_cannot_finalize(self):
        work = start_work(self.package, self.catalog)
        with self.assertRaises(DecompositionDevAnnotationError):
            finalize_work(
                self.package,
                self.catalog,
                work,
                human_authorship_attested=True,
                test_source_not_inspected_attested=True,
            )

    def test_completed_work_is_immutable(self):
        work = start_work(self.package, self.catalog)
        for item in work["items"]:
            work = set_expression(
                self.package,
                self.catalog,
                work,
                item["criterion_id"],
                self.expression_for(item["criterion_id"]),
            )
        with self.assertRaises(DecompositionDevAnnotationError):
            finalize_work(
                self.package,
                self.catalog,
                work,
                human_authorship_attested=False,
                test_source_not_inspected_attested=True,
            )
        completed = finalize_work(
            self.package,
            self.catalog,
            work,
            human_authorship_attested=True,
            test_source_not_inspected_attested=True,
        )
        validate_work(
            self.package, self.catalog, completed, require_completed=True
        )
        self.assertEqual(0, progress(completed)["remaining"])
        with self.assertRaises(DecompositionDevAnnotationError):
            set_expression(
                self.package,
                self.catalog,
                completed,
                completed["items"][0]["criterion_id"],
                None,
            )

    def test_output_is_private_and_refuses_overwrite(self):
        work = start_work(self.package, self.catalog)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "work.json"
            write_new_private_json(output, work)
            self.assertEqual(0o600, os.stat(output).st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                write_new_private_json(output, work)

    def test_views_only_return_selected_dev_and_frozen_catalog(self):
        work = start_work(self.package, self.catalog)
        criterion_id = progress(work)["next_criterion_id"]
        view = item_view(self.package, self.issue_log, criterion_id)
        self.assertEqual(criterion_id, view["criterion_id"])
        self.assertIn("source_text", view)
        matches = catalog_view(self.catalog, "age")
        self.assertTrue(matches)
        self.assertTrue(all("field_id" in item for item in matches))


if __name__ == "__main__":
    unittest.main()
