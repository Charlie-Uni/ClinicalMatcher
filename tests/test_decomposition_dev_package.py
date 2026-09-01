import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.decomposition_dev_package import (
    DecompositionDevPackageError,
    load_annotation_guide_1_1,
    load_selected_dev_records,
    validate_dev_catalog,
    validate_dev_package,
    validate_issue_log,
    verify_all,
    write_artifacts_once,
)
from clinical_matcher.validation import DocumentValidationError


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "benchmarks/decomposition/af_decomposition_selection_1.2.0.json"
DEV_ROOT = ROOT / "benchmarks/decomposition/dev_sources_1.2.0"
CATALOG = ROOT / "benchmarks/decomposition/dev_concept_catalog_1.1.0.json"
ISSUES = ROOT / "benchmarks/decomposition/dev_annotation_issue_log_1.0.0.json"
PACKAGE = ROOT / "benchmarks/decomposition/dev_single_annotator_package_1.0.0.json"


class DecompositionDevPackageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = json.loads(SELECTION.read_text(encoding="utf-8"))
        cls.records, cls.snapshot = load_selected_dev_records(
            cls.selection, DEV_ROOT
        )
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.issues = json.loads(ISSUES.read_text(encoding="utf-8"))
        cls.package = json.loads(PACKAGE.read_text(encoding="utf-8"))

    def test_frozen_guide_is_self_hashed(self):
        guide = load_annotation_guide_1_1()
        self.assertEqual("decomposition-guide/1.1.0", guide["guide_version"])
        self.assertEqual(
            "decomposition-benchmark-protocol/1.2.0",
            guide["protocol_version"],
        )

    def test_all_three_frozen_artifacts_verify(self):
        catalog, issues, package = verify_all(
            selection_path=SELECTION,
            dev_source_root=DEV_ROOT,
            catalog_path=CATALOG,
            issue_path=ISSUES,
            package_path=PACKAGE,
        )
        self.assertEqual(85, len(catalog["entries"]))
        self.assertEqual(8, len(issues["issues"]))
        self.assertEqual(40, len(package["items"]))
        self.assertTrue(all(item["expression"] is None for item in package["items"]))

    def test_ungrounded_alias_fails_closed(self):
        catalog = copy.deepcopy(self.catalog)
        catalog["entries"][0]["aliases"].append("not in the frozen dev corpus")
        with self.assertRaises(DecompositionDevPackageError):
            validate_dev_catalog(
                self.selection, self.snapshot, self.records, catalog
            )

    def test_owner_resolution_and_flags_are_immutable(self):
        issues = copy.deepcopy(self.issues)
        issues["issues"][0]["approved_resolution"] = "changed after approval"
        with self.assertRaises(DecompositionDevPackageError):
            validate_issue_log(
                self.selection, self.snapshot, self.records, issues
            )
        flags = {
            item["criterion_id"]: item["resolution_flags"]
            for item in self.issues["issues"]
        }
        self.assertEqual(
            ["incomplete_source_condition"],
            flags["nct07430956-inclusion-918c40f64dad790a"],
        )
        self.assertEqual(
            ["source_span_contamination"],
            flags["nct07715929-exclusion-6fcd645b1ca00b43"],
        )

    def test_generated_package_cannot_contain_annotation(self):
        package = copy.deepcopy(self.package)
        package["items"][0]["expression"] = {"expression_type": "atom"}
        with self.assertRaises(DocumentValidationError):
            validate_dev_package(
                self.selection,
                self.snapshot,
                self.records,
                self.catalog,
                self.issues,
                package,
            )

    def test_write_once_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text("existing", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_artifacts_once(((path, {"new": True}),))
            self.assertEqual("existing", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
