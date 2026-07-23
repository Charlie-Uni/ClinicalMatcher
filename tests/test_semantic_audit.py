import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import (
    SemanticNearDuplicate,
    generate_split_manifest,
    load_split_manifest,
)


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


class SemanticAuditTest(unittest.TestCase):
    def manifest(self):
        document = generate_split_manifest(
            fixture_path=FIXTURE,
            strategy="patient_holdout",
            seed=17,
            test_fraction=0.5,
            dataset_id="semantic-audit-test",
            code_commit="d" * 40,
            generated_at="2026-07-23T14:00:00Z",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return load_split_manifest(path)

    def test_exhaustive_summary_proves_cross_pair_coverage(self) -> None:
        manifest = self.manifest()
        summary = build_semantic_scan_summary(
            manifest=manifest,
            dimension="patient",
            pairs=(),
            embedding_model_id="synthetic-encoder",
            embedding_model_revision="revision-1",
            pooling="mean",
            vectors_normalized=True,
            search_method="exhaustive_cosine",
            candidate_pairs_evaluated=1,
        )
        self.assertTrue(summary["search"]["exhaustive"])
        self.assertEqual(
            1,
            summary["search"]["expected_cross_split_pairs"],
        )
        self.assertTrue(
            summary["results"]["leakage_assertion_passed"]
        )
        serialized = json.dumps(summary)
        self.assertNotIn("synthetic-patient", serialized)

    def test_exhaustive_claim_fails_when_not_all_pairs_were_scanned(self) -> None:
        manifest = self.manifest()
        with self.assertRaisesRegex(ValueError, "every cross-split pair"):
            build_semantic_scan_summary(
                manifest=manifest,
                dimension="patient",
                pairs=(),
                embedding_model_id="synthetic-encoder",
                embedding_model_revision="revision-1",
                pooling="mean",
                vectors_normalized=True,
                search_method="exhaustive_cosine",
                candidate_pairs_evaluated=0,
            )

    def test_ann_scan_requires_measured_candidate_recall(self) -> None:
        manifest = self.manifest()
        with self.assertRaisesRegex(ValueError, "candidate recall"):
            build_semantic_scan_summary(
                manifest=manifest,
                dimension="patient",
                pairs=(),
                embedding_model_id="synthetic-encoder",
                embedding_model_revision="revision-1",
                pooling="mean",
                vectors_normalized=True,
                search_method="ann_candidates",
                candidate_pairs_evaluated=1,
            )

    def test_summary_reports_cross_split_near_duplicate_without_ids(self) -> None:
        manifest = self.manifest()
        pair = SemanticNearDuplicate(
            dimension="patient",
            left_id=manifest.partition("train").entity_ids["patient"][0],
            right_id=manifest.partition("test").entity_ids["patient"][0],
            similarity=0.98,
        )
        summary = build_semantic_scan_summary(
            manifest=manifest,
            dimension="patient",
            pairs=(pair,),
            embedding_model_id="synthetic-encoder",
            embedding_model_revision="revision-1",
            pooling="mean",
            vectors_normalized=True,
            search_method="exhaustive_cosine",
            candidate_pairs_evaluated=1,
        )
        self.assertFalse(
            summary["results"]["leakage_assertion_passed"]
        )
        self.assertEqual(
            1,
            summary["results"][
                "cross_split_pairs_at_or_above_threshold"
            ],
        )
        self.assertNotIn(pair.left_id, json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
