import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_bm25 import run_bm25_baseline, write_bm25_run
from clinical_matcher.apixaban_dense import run_dense_baseline, write_dense_run
from clinical_matcher.apixaban_evidence_index import (
    build_evidence_index_manifest_from_paths,
    write_evidence_index_manifest,
)
from clinical_matcher.apixaban_rrf import (
    ApixabanRRFError,
    load_rrf_contract,
    reciprocal_rank_fusion,
    run_rrf_fusion,
    validate_rrf_run,
    write_rrf_run,
)
from clinical_matcher.apixaban_rrf_cli import main
from clinical_matcher.retrieval.base import RankedEvidence
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_dense import FakeMedCPTEncoder
from tests.test_apixaban_evidence_index import _frozen_inputs, _write_private


def _ranking(patient_id, evidence_ids):
    return tuple(
        RankedEvidence(patient_id, evidence_id, float(len(evidence_ids) - rank), rank)
        for rank, evidence_id in enumerate(evidence_ids, start=1)
    )


class ReciprocalRankFusionTests(unittest.TestCase):
    def setUp(self):
        self.patient_id = "patient-" + "a" * 24
        self.prefix = "evidence-" + "a" * 24 + "-"

    def test_equal_weight_formula_and_source_order_tie_break(self):
        a, b, c = (self.prefix + suffix for suffix in ("001", "002", "003"))
        selected = reciprocal_rank_fusion(
            _ranking(self.patient_id, [a, b]),
            _ranking(self.patient_id, [b, a, c]),
            {a: 20, b: 0, c: 40},
        )
        self.assertEqual([b, a, c], [item["evidence_id"] for item in selected])
        self.assertAlmostEqual(1 / 61 + 1 / 62, selected[0]["score"])
        self.assertEqual((2, 1), (selected[0]["bm25_rank"], selected[0]["dense_rank"]))
        self.assertIsNone(selected[2]["bm25_rank"])

    def test_full_rank_inputs_can_promote_moderate_cross_retriever_agreement(self):
        ids = [self.prefix + f"{index:03d}" for index in range(1, 11)]
        a, b, c, shared = ids[0], ids[1], ids[2], ids[3]
        dense_order = [ids[4], ids[5], ids[6], shared, ids[7], ids[8], ids[9], a, b, c]
        selected = reciprocal_rank_fusion(
            _ranking(self.patient_id, [a, b, c, shared]),
            _ranking(self.patient_id, dense_order),
            {evidence_id: index for index, evidence_id in enumerate(ids)},
            top_k=1,
        )
        self.assertEqual(shared, selected[0]["evidence_id"])
        self.assertEqual((4, 4), (selected[0]["bm25_rank"], selected[0]["dense_rank"]))

    def test_component_candidate_mismatch_fails_closed(self):
        a, b = (self.prefix + suffix for suffix in ("001", "002"))
        with self.assertRaisesRegex(ValueError, "absent from the dense"):
            reciprocal_rank_fusion(
                _ranking(self.patient_id, [a]),
                _ranking(self.patient_id, [b]),
                {b: 0},
            )

    def test_noncontiguous_component_ranks_fail_closed(self):
        a = self.prefix + "001"
        with self.assertRaisesRegex(ValueError, "ordered and contiguous"):
            reciprocal_rank_fusion(
                (RankedEvidence(self.patient_id, a, 1.0, 2),),
                _ranking(self.patient_id, [a]),
                {a: 0},
            )


class ApixabanRRFRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen, cls.inputs = _frozen_inputs()
        cls.corpus = cls.inputs[2]

    def _components(self, root: Path):
        split_path = root / "split.json"
        corpus_path = root / "corpus.json"
        evidence_path = root / "evidence-index.json"
        _write_private(split_path, self.frozen)
        _write_private(corpus_path, self.corpus)
        evidence = build_evidence_index_manifest_from_paths(
            split_path,
            corpus_path,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        write_evidence_index_manifest(evidence, evidence_path)
        bm25_run, bm25_predictions = run_bm25_baseline(
            split_path,
            corpus_path,
            evidence_path,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        bm25_paths = write_bm25_run(
            bm25_run, bm25_predictions, root / "bm25"
        )
        dense = run_dense_baseline(
            split_path,
            corpus_path,
            evidence_path,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
            encoder_factory=FakeMedCPTEncoder,
        )
        dense_paths = write_dense_run(*dense, root / "dense")
        return split_path, corpus_path, evidence_path, bm25_paths, dense_paths

    def _run(self, root: Path):
        split_path, corpus_path, evidence_path, bm25_paths, dense_paths = (
            self._components(root)
        )
        run, predictions = run_rrf_fusion(
            split_path,
            corpus_path,
            evidence_path,
            bm25_paths[0],
            dense_paths[2],
            dense_paths[1],
            dense_paths[0],
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
            encoder_factory=FakeMedCPTEncoder,
        )
        return run, predictions

    def test_contract_freezes_approved_formula_and_defers_reranker(self):
        contract = load_rrf_contract()
        self.assertEqual(60, contract["fusion"]["rank_constant"])
        self.assertEqual(
            {"bm25": 1.0, "dense": 1.0},
            contract["fusion"]["component_weights"],
        )
        self.assertFalse(contract["fusion"]["parameter_search_used"])
        self.assertFalse(contract["reranker"]["included"])

    def test_end_to_end_run_is_complete_restricted_and_label_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, predictions = self._run(root)
            self.assertEqual(46, run["counts"]["query_count"])
            self.assertEqual(46, len(predictions["predictions"]))
            self.assertTrue(
                all(
                    item["dense_candidate_count"] == item["candidate_count"]
                    for item in run["results"]
                )
            )
            self.assertFalse(run["configuration"]["reranker_included"])
            self.assertFalse(
                run["evaluation_boundary"]["retrieval_relevance_metrics_reported"]
            )
            serialized = json.dumps(run, sort_keys=True)
            self.assertNotIn("answer_status", serialized)
            paths = write_rrf_run(run, predictions, root / "rrf")
            self.assertTrue(all(os.stat(path).st_mode & 0o077 == 0 for path in paths))
            with self.assertRaises(FileExistsError):
                write_rrf_run(run, predictions, root / "rrf")

    def test_semantic_validator_recomputes_fusion_score(self):
        with tempfile.TemporaryDirectory() as directory:
            run, _ = self._run(Path(directory))
            run["results"][0]["selected_evidence"][0]["score"] += 0.001
            unsigned = dict(run)
            unsigned.pop("run_sha256")
            run["run_sha256"] = canonical_sha256(unsigned)
            with self.assertRaisesRegex(ApixabanRRFError, "does not match its ranks"):
                validate_rrf_run(run)

    def test_recomputed_ranking_must_match_cited_component_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_path, corpus_path, evidence_path, bm25_paths, dense_paths = (
                self._components(root)
            )
            bm25 = json.loads(bm25_paths[0].read_text(encoding="utf-8"))
            target = next(item for item in bm25["results"] if item["selected_evidence"])
            removed_count = len(target["selected_evidence"])
            target["selected_evidence"] = []
            bm25["counts"]["selected_document_count"] -= removed_count
            bm25["counts"]["queries_with_positive_match"] -= 1
            bm25["counts"]["queries_without_positive_match"] += 1
            unsigned = dict(bm25)
            unsigned.pop("run_sha256")
            bm25["run_sha256"] = canonical_sha256(unsigned)
            _write_private(root / "changed-bm25.json", bm25)
            with self.assertRaisesRegex(ApixabanRRFError, "Recomputed BM25"):
                run_rrf_fusion(
                    split_path,
                    corpus_path,
                    evidence_path,
                    root / "changed-bm25.json",
                    dense_paths[2],
                    dense_paths[1],
                    dense_paths[0],
                    "validation",
                    generated_at="2026-08-14T12:00:00Z",
                    code_commit="a" * 40,
                    encoder_factory=FakeMedCPTEncoder,
                )

    def test_cli_requires_local_ack_and_separate_locked_test_ack(self):
        common = [
            "--frozen-split", "missing-split.json",
            "--staging-corpus", "missing-corpus.json",
            "--evidence-index-manifest", "missing-evidence.json",
            "--bm25-run", "missing-bm25.json",
            "--dense-run", "missing-dense.json",
            "--dense-index-manifest", "missing-dense-index.json",
            "--dense-vectors", "missing-vectors.f32",
            "--output-dir", "missing-output",
        ]
        with self.assertRaisesRegex(ValueError, "local-only"):
            main([*common, "--split", "validation"])
        with self.assertRaisesRegex(ValueError, "Locked test RRF"):
            main(
                [
                    *common,
                    "--split", "test",
                    "--acknowledge-restricted-data-local-only",
                ]
            )


if __name__ == "__main__":
    unittest.main()
