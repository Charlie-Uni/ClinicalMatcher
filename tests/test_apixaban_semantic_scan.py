import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_semantic_scan import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    MAX_SEQUENCE_LENGTH,
    cross_split_semantic_pairs,
    pool_patient_embeddings,
    run_apixaban_semantic_scan,
)
from clinical_matcher.apixaban_split import ApixabanSplitError
from clinical_matcher.apixaban_split import (
    build_apixaban_split_candidate_from_paths,
    split_manifest_view,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import SemanticNearDuplicate
from tests.test_apixaban_split import build_candidate


def _write_private(path, document):
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


class _FakeEncoder:
    init_args = None
    instance = None

    def __init__(self, *args, **kwargs):
        type(self).init_args = (args, kwargs)
        type(self).instance = self

    def encode(self, texts, **kwargs):
        size = len(texts)
        return [
            [1.0 if index == dimension else 0.0 for dimension in range(size)]
            for index in range(size)
        ]


class ApixabanSemanticScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate, cls.inputs = build_candidate()
        cls.corpus = cls.inputs[2]

    def test_patient_pooling_normalizes_chunks_and_patient_vector(self):
        pooled = pool_patient_embeddings(
            [[3.0, 0.0], [0.0, 4.0]], {"patient-a": (0, 2)}
        )
        expected = 2 ** -0.5
        self.assertAlmostEqual(expected, pooled["patient-a"][0])
        self.assertAlmostEqual(expected, pooled["patient-a"][1])

    def test_cross_split_scan_counts_every_cross_pair_and_retains_hits(self):
        membership = {
            patient_id: split_name
            for split_name, partition in self.candidate["splits"].items()
            for patient_id in partition["patient_ids"]
        }
        train_id = next(
            patient_id
            for patient_id, split_name in membership.items()
            if split_name == "train"
        )
        test_id = next(
            patient_id
            for patient_id, split_name in membership.items()
            if split_name == "test"
        )
        vectors = {
            patient_id: [1.0, 0.0]
            if patient_id in (train_id, test_id)
            else [0.0, 1.0]
            for patient_id in membership
        }
        pairs, evaluated = cross_split_semantic_pairs(
            self.candidate, vectors
        )
        self.assertEqual(11, evaluated)
        self.assertTrue(
            any(
                {pair.left_id, pair.right_id} == {train_id, test_id}
                for pair in pairs
            )
        )
        self.assertTrue(
            all(
                membership[pair.left_id] != membership[pair.right_id]
                for pair in pairs
            )
        )

    def test_end_to_end_scan_uses_fixed_revision_and_writes_private_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_path = root / "split.json"
            corpus_path = root / "corpus.json"
            pairs_path = root / "pairs.json"
            summary_path = root / "summary.json"
            _write_private(split_path, self.candidate)
            _write_private(corpus_path, self.corpus)
            summary = run_apixaban_semantic_scan(
                split_path,
                corpus_path,
                pairs_path,
                summary_path,
                encoder_factory=_FakeEncoder,
            )
            self.assertEqual(11, summary["search"]["candidate_pairs_evaluated"])
            self.assertTrue(summary["search"]["exhaustive"])
            self.assertTrue(summary["results"]["leakage_assertion_passed"])
            self.assertEqual([], json.loads(pairs_path.read_text()))
            self.assertEqual(0, os.stat(pairs_path).st_mode & 0o077)
            self.assertEqual(0, os.stat(summary_path).st_mode & 0o077)
            args, kwargs = _FakeEncoder.init_args
            self.assertEqual((DEFAULT_MODEL_ID,), args)
            self.assertEqual(DEFAULT_MODEL_REVISION, kwargs["revision"])
            self.assertFalse(kwargs["trust_remote_code"])
            self.assertEqual(
                MAX_SEQUENCE_LENGTH, _FakeEncoder.instance.max_seq_length
            )

    def test_staging_hash_mismatch_fails_before_encoding(self):
        tampered = copy.deepcopy(self.corpus)
        evidence = tampered["patients"][0]["evidence"][0]
        evidence["text"] += " changed"
        evidence["source_span"]["end"] = len(evidence["text"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_path = root / "split.json"
            corpus_path = root / "corpus.json"
            _write_private(split_path, self.candidate)
            _write_private(corpus_path, tampered)
            with self.assertRaisesRegex(
                ApixabanSplitError, "does not match the split candidate hash"
            ):
                run_apixaban_semantic_scan(
                    split_path,
                    corpus_path,
                    root / "pairs.json",
                    root / "summary.json",
                    encoder_factory=_FakeEncoder,
                )

    def test_world_readable_staging_corpus_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_path = root / "split.json"
            corpus_path = root / "corpus.json"
            _write_private(split_path, self.candidate)
            _write_private(corpus_path, self.corpus)
            corpus_path.chmod(0o644)
            with self.assertRaisesRegex(
                ApixabanSplitError, "Staging corpus must be owner-only"
            ):
                run_apixaban_semantic_scan(
                    split_path,
                    corpus_path,
                    root / "pairs.json",
                    root / "summary.json",
                    encoder_factory=_FakeEncoder,
                )

    def test_failed_scan_pairs_can_be_bound_into_regrouped_candidate(self):
        train_id = self.candidate["splits"]["train"]["patient_ids"][0]
        test_id = self.candidate["splits"]["test"]["patient_ids"][0]
        pair = SemanticNearDuplicate("patient", train_id, test_id, 0.97)
        summary = build_semantic_scan_summary(
            split_manifest_view(self.candidate),
            "patient",
            [pair],
            DEFAULT_MODEL_ID,
            DEFAULT_MODEL_REVISION,
            "test_pooling",
            True,
            "exhaustive_cosine",
            11,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = (
                "benchmark",
                "benchmark-manifest",
                "corpus",
                "import-manifest",
                "id-map",
                "quality",
            )
            paths = [root / f"{name}.json" for name in names]
            for path, document in zip(paths, self.inputs):
                _write_private(path, document)
            source_path = root / "source-candidate.json"
            pair_path = root / "pairs.json"
            summary_path = root / "summary.json"
            _write_private(source_path, self.candidate)
            _write_private(
                pair_path,
                [
                    {
                        "dimension": pair.dimension,
                        "left_id": pair.left_id,
                        "right_id": pair.right_id,
                        "similarity": pair.similarity,
                    }
                ],
            )
            _write_private(summary_path, summary)
            regrouped = build_apixaban_split_candidate_from_paths(
                *paths,
                semantic_pairs_path=pair_path,
                semantic_summary_path=summary_path,
                semantic_source_candidate_path=source_path,
                fractions={"train": 0.5, "validation": 0.25, "test": 0.25},
                seed=17,
                semantic_similarity_threshold=0.95,
                generated_at="2026-08-13T07:00:00Z",
                code_commit="5" * 40,
                required_source_sha256=None,
                required_counts={
                    "patient_count": 6,
                    "question_count": 23,
                    "assessment_count": 138,
                    "answered_source_count": 132,
                    "not_specified_source_count": 3,
                    "source_anomaly_count": 3,
                },
            )
            membership = {
                patient_id: split_name
                for split_name, partition in regrouped["splits"].items()
                for patient_id in partition["patient_ids"]
            }
            self.assertEqual(membership[train_id], membership[test_id])
            self.assertIn(
                "semantic_near_duplicate",
                regrouped["policy"]["grouping_dimensions"],
            )
            self.assertEqual(
                1,
                regrouped["policy"]["semantic_grouping"][
                    "retained_pair_count"
                ],
            )


if __name__ == "__main__":
    unittest.main()
