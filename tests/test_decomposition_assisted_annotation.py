import copy
import unittest
from pathlib import Path

from clinical_matcher.decomposition_assisted_annotation import (
    DecompositionAssistedAnnotationError,
    assisted_progress,
    finalize_assisted_work,
    load_assisted_decision,
    review_assisted_draft,
    set_assisted_draft_batch,
    start_assisted_work,
    validate_assisted_work,
)
from clinical_matcher.decomposition_dev_package import verify_all


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "benchmarks/decomposition/af_decomposition_selection_1.2.0.json"
DEV_ROOT = ROOT / "benchmarks/decomposition/dev_sources_1.2.0"
CATALOG = ROOT / "benchmarks/decomposition/dev_concept_catalog_1.1.0.json"
ISSUES = ROOT / "benchmarks/decomposition/dev_annotation_issue_log_1.0.0.json"
PACKAGE = ROOT / "benchmarks/decomposition/dev_single_annotator_package_1.0.0.json"


class DecompositionAssistedAnnotationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.issue_log, cls.package = verify_all(
            selection_path=SELECTION,
            dev_source_root=DEV_ROOT,
            catalog_path=CATALOG,
            issue_path=ISSUES,
            package_path=PACKAGE,
        )
        cls.decision = load_assisted_decision()

    def expression_for(self, criterion_id, *, field=None, method="llm"):
        source = next(
            item for item in self.package["items"] if item["criterion_id"] == criterion_id
        )
        generator = self.decision["draft_generator"]
        provenance = {
            "source_id": source["source_id"],
            "source_span": {"start": 0, "end": 1},
            "method": method,
        }
        if method == "llm":
            provenance.update(
                model_id=generator["model_id"],
                prompt_version=generator["prompt_version"],
            )
        return {
            "expression_type": "atom",
            "atom": {
                "condition_id": f"{criterion_id}:a01",
                "field": field or self.catalog["entries"][0]["field_id"],
                "operator": "==",
                "expected": {"value_type": "boolean", "value": True},
                "fact_selection": "any",
                "provenance": provenance,
            },
        }

    def batch_for(self, work, *, method="llm", field=None):
        return {
            "drafts": [
                {
                    "criterion_id": item["criterion_id"],
                    "expression": self.expression_for(
                        item["criterion_id"], field=field, method=method
                    ),
                }
                for item in work["items"]
            ]
        }

    def test_start_is_disclosed_silver_and_keeps_old_route_unexecuted(self):
        work = start_assisted_work(self.package, self.catalog)
        validate_assisted_work(self.package, self.catalog, work)
        self.assertEqual("llm_assisted_owner_review", work["annotation_mode"])
        self.assertEqual("llm_assisted_owner_reviewed_silver", work["reference_label"])
        self.assertFalse(work["completion_attestation"]["independent_gold_claimed"])
        self.assertFalse(work["completion_attestation"]["grpo_semantic_oracle_claimed"])
        self.assertEqual(0, assisted_progress(work)["drafted"])
        self.assertEqual(0, self.decision["superseded_route"]["saved_expression_count"])
        self.assertEqual(
            "decomposition-llm-assisted-decision/1.1.0",
            self.decision["decision_version"],
        )
        self.assertFalse(self.decision["workflow_sequence"]["partial_batch_allowed"])

    def test_complete_batch_then_unchanged_owner_review(self):
        work = start_assisted_work(self.package, self.catalog)
        criterion_id = work["items"][0]["criterion_id"]
        batch = self.batch_for(work)
        draft = batch["drafts"][0]["expression"]
        work = set_assisted_draft_batch(self.package, self.catalog, work, batch)
        self.assertEqual(40, assisted_progress(work)["drafted"])
        self.assertEqual("owner_review", assisted_progress(work)["next_action"])
        work = review_assisted_draft(
            self.package,
            self.catalog,
            work,
            criterion_id,
            "accepted_unchanged",
        )
        self.assertEqual(1, assisted_progress(work)["reviewed"])
        self.assertEqual(draft, work["items"][0]["reviewed_expression"])

    def test_partial_or_repeated_batch_is_rejected(self):
        work = start_assisted_work(self.package, self.catalog)
        partial = self.batch_for(work)
        partial["drafts"].pop()
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(self.package, self.catalog, work, partial)
        self.assertEqual(0, assisted_progress(work)["drafted"])
        complete = self.batch_for(work)
        work = set_assisted_draft_batch(self.package, self.catalog, work, complete)
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(self.package, self.catalog, work, complete)

    def test_batch_shape_order_and_membership_are_fail_closed(self):
        work = start_assisted_work(self.package, self.catalog)
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(self.package, self.catalog, work, [])
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(
                self.package, self.catalog, work, {"drafts": [], "extra": True}
            )
        reversed_batch = self.batch_for(work)
        reversed_batch["drafts"].reverse()
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(
                self.package, self.catalog, work, reversed_batch
            )
        duplicated = self.batch_for(work)
        duplicated["drafts"][-1] = copy.deepcopy(duplicated["drafts"][0])
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(self.package, self.catalog, work, duplicated)
        self.assertEqual(0, assisted_progress(work)["drafted"])

    def test_review_is_blocked_before_complete_batch(self):
        work = start_assisted_work(self.package, self.catalog)
        criterion_id = work["items"][0]["criterion_id"]
        with self.assertRaises(DecompositionAssistedAnnotationError):
            review_assisted_draft(
                self.package,
                self.catalog,
                work,
                criterion_id,
                "accepted_unchanged",
            )

    def test_edited_review_requires_real_change_and_note(self):
        work = start_assisted_work(self.package, self.catalog)
        criterion_id = work["items"][0]["criterion_id"]
        batch = self.batch_for(work)
        draft = batch["drafts"][0]["expression"]
        work = set_assisted_draft_batch(self.package, self.catalog, work, batch)
        with self.assertRaises(DecompositionAssistedAnnotationError):
            review_assisted_draft(
                self.package,
                self.catalog,
                work,
                criterion_id,
                "accepted_with_edits",
                edited_expression=draft,
                note="claimed edit",
            )
        edited = copy.deepcopy(draft)
        edited["atom"]["fact_selection"] = "latest"
        work = review_assisted_draft(
            self.package,
            self.catalog,
            work,
            criterion_id,
            "accepted_with_edits",
            edited_expression=edited,
            note="Changed fact-selection policy after owner review.",
        )
        self.assertEqual("accepted_with_edits", work["items"][0]["review_status"])

    def test_human_provenance_and_unknown_field_are_rejected(self):
        work = start_assisted_work(self.package, self.catalog)
        invalid = self.batch_for(work, method="human")
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(self.package, self.catalog, work, invalid)
        invalid = self.batch_for(work, field="invented_field")
        with self.assertRaises(DecompositionAssistedAnnotationError):
            set_assisted_draft_batch(self.package, self.catalog, work, invalid)

    def test_finalization_requires_all_owner_reviews_and_disclosure(self):
        work = start_assisted_work(self.package, self.catalog)
        with self.assertRaises(DecompositionAssistedAnnotationError):
            finalize_assisted_work(
                self.package,
                self.catalog,
                work,
                assistance_disclosed=True,
                every_item_reviewed=True,
                test_source_not_inspected=True,
            )
        work = set_assisted_draft_batch(
            self.package, self.catalog, work, self.batch_for(work)
        )
        for item in list(work["items"]):
            criterion_id = item["criterion_id"]
            work = review_assisted_draft(
                self.package,
                self.catalog,
                work,
                criterion_id,
                "accepted_unchanged",
            )
        completed = finalize_assisted_work(
            self.package,
            self.catalog,
            work,
            assistance_disclosed=True,
            every_item_reviewed=True,
            test_source_not_inspected=True,
        )
        self.assertEqual("completed", completed["status"])
        self.assertTrue(completed["completion_attestation"]["llm_assistance_disclosed"])
        self.assertFalse(completed["completion_attestation"]["independent_gold_claimed"])


if __name__ == "__main__":
    unittest.main()
