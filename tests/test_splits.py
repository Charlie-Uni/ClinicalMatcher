import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from clinical_matcher.splits import (
    SemanticNearDuplicate,
    SplitPartition,
    assert_dataset_matches,
    assert_no_split_leakage,
    generate_split_manifest,
    load_split_manifest,
    semantic_pairs_from_embeddings,
)


FIXTURE = Path("fixtures/synthetic/trial_matching.json")
COMMIT = "a" * 40
GENERATED_AT = "2026-07-23T12:00:00Z"


class SplitManifestTest(unittest.TestCase):
    def generate(self, strategy: str = "patient_holdout") -> dict:
        return generate_split_manifest(
            fixture_path=FIXTURE,
            strategy=strategy,
            seed=17,
            test_fraction=0.5,
            dataset_id="synthetic-test",
            code_commit=COMMIT,
            generated_at=GENERATED_AT,
            generation_command=f"test:{strategy}",
        )

    def load_document(self, document: dict):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return load_split_manifest(path)

    def test_manifest_is_deterministic_and_lineage_tracked(self) -> None:
        first = self.generate()
        second = self.generate()
        self.assertEqual(first, second)
        manifest = self.load_document(first)
        self.assertEqual(("patient",), manifest.isolated_dimensions)
        self.assertEqual(COMMIT, manifest.code_commit)
        self.assertEqual(64, len(manifest.dataset_sha256))
        assert_dataset_matches(manifest, FIXTURE)

    def test_trial_holdout_also_isolates_criteria(self) -> None:
        manifest = self.load_document(self.generate("trial_holdout"))
        self.assertEqual(
            ("trial", "criterion"),
            manifest.isolated_dimensions,
        )
        train = manifest.partition("train")
        test = manifest.partition("test")
        self.assertFalse(
            set(train.entity_ids["trial"]) & set(test.entity_ids["trial"])
        )
        self.assertFalse(
            set(train.entity_ids["criterion"])
            & set(test.entity_ids["criterion"])
        )

    def test_manifest_hash_detects_mutation(self) -> None:
        document = self.generate()
        document["seed"] = 18
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.load_document(document)

    def test_exact_id_overlap_is_an_executable_assertion(self) -> None:
        manifest = self.load_document(self.generate())
        train = manifest.partition("train")
        test = manifest.partition("test")
        repeated = train.entity_ids["patient"][0]
        test_ids = dict(test.entity_ids)
        test_ids["patient"] = test_ids["patient"] + (repeated,)
        bad = replace(
            manifest,
            splits={
                **manifest.splits,
                "test": SplitPartition(
                    entity_ids=test_ids,
                    content_sha256=test.content_sha256,
                ),
            },
        )
        with self.assertRaisesRegex(ValueError, "IDs cross"):
            assert_no_split_leakage(bad)

    def test_exact_content_duplicate_with_different_ids_is_rejected(self) -> None:
        manifest = self.load_document(self.generate())
        train = manifest.partition("train")
        test = manifest.partition("test")
        train_id = train.entity_ids["patient"][0]
        test_id = test.entity_ids["patient"][0]
        test_hashes = {
            dimension: dict(values)
            for dimension, values in test.content_sha256.items()
        }
        test_hashes["patient"][test_id] = train.content_sha256["patient"][
            train_id
        ]
        bad = replace(
            manifest,
            splits={
                **manifest.splits,
                "test": SplitPartition(
                    entity_ids=test.entity_ids,
                    content_sha256=test_hashes,
                ),
            },
        )
        with self.assertRaisesRegex(ValueError, "exact content duplicates"):
            assert_no_split_leakage(bad)

    def test_semantic_near_duplicate_crossing_split_is_rejected(self) -> None:
        manifest = self.load_document(self.generate())
        pair = SemanticNearDuplicate(
            dimension="patient",
            left_id=manifest.partition("train").entity_ids["patient"][0],
            right_id=manifest.partition("test").entity_ids["patient"][0],
            similarity=0.97,
        )
        with self.assertRaisesRegex(ValueError, "semantic near-duplicate"):
            assert_no_split_leakage(manifest, [pair])

    def test_embedding_cosine_scan_emits_only_pairs_over_threshold(self) -> None:
        pairs = semantic_pairs_from_embeddings(
            dimension="patient",
            embeddings={
                "patient-a": (1.0, 0.0),
                "patient-b": (0.99, 0.01),
                "patient-c": (0.0, 1.0),
            },
            threshold=0.95,
        )
        self.assertEqual(1, len(pairs))
        self.assertEqual("patient-a", pairs[0].left_id)
        self.assertEqual("patient-b", pairs[0].right_id)

    def test_generic_encounter_group_can_be_made_mandatory(self) -> None:
        manifest = self.load_document(self.generate())
        splits = {}
        for name, partition in manifest.splits.items():
            ids = dict(partition.entity_ids)
            hashes = {
                dimension: dict(values)
                for dimension, values in partition.content_sha256.items()
            }
            ids["encounter"] = ("synthetic-encounter-shared",)
            hashes["encounter"] = {
                "synthetic-encounter-shared": "b" * 64
            }
            splits[name] = SplitPartition(ids, hashes)
        bad = replace(
            manifest,
            isolated_dimensions=("patient", "encounter"),
            splits=splits,
        )
        with self.assertRaisesRegex(ValueError, "encounter: IDs cross"):
            assert_no_split_leakage(bad)

    def test_dataset_fingerprint_detects_wrong_source(self) -> None:
        manifest = self.load_document(self.generate())
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["fixture_notice"] += " Modified."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                assert_dataset_matches(manifest, path)


if __name__ == "__main__":
    unittest.main()
