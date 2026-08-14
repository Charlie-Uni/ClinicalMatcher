import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_bm25 import (
    ApixabanBM25Error,
    load_bm25_contract,
    run_bm25_baseline,
    validate_bm25_contract,
    validate_bm25_run,
    write_bm25_run,
)
from clinical_matcher.apixaban_bm25_cli import main
from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_evidence_index import (
    build_evidence_index_manifest_from_paths,
    write_evidence_index_manifest,
)
from clinical_matcher.retrieval.bm25 import BM25PatientRetriever, tokenize
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_evidence_index import _frozen_inputs, _write_private


def _record(patient: str, suffix: str, start: int, text: str):
    token = patient.removeprefix("patient-")
    return {
        "patient_id": patient,
        "evidence_id": f"evidence-{token}-{suffix}",
        "source_id": f"note-{token}",
        "source_span": {"start": start, "end": start + len(text)},
        "section": None,
        "text": text,
    }


class BM25RetrieverTests(unittest.TestCase):
    def setUp(self):
        self.patient_a = "patient-" + "a" * 24
        self.patient_b = "patient-" + "b" * 24
        self.records = [
            _record(self.patient_a, "001", 0, "Renal function stable"),
            _record(self.patient_a, "002", 30, "Renal renal failure"),
            _record(self.patient_a, "003", 60, "Heart rhythm normal"),
            _record(self.patient_b, "001", 0, "Renal failure in other patient"),
        ]

    def test_tokenizer_is_frozen_unicode_casefold_without_stemming(self):
        self.assertEqual(
            ("egfr", "60", "ml/min", "cha2ds2-vasc"),
            tokenize("eGFR 60 mL/min, CHA2DS2-VASc"),
        )

    def test_controlled_ranking_is_deterministic_and_patient_local(self):
        retriever = BM25PatientRetriever(self.records)
        first = retriever.retrieve(self.patient_a, "renal failure", 3)
        second = retriever.retrieve(self.patient_a, "renal failure", 3)
        self.assertEqual(first, second)
        self.assertEqual(
            f"evidence-{'a' * 24}-002", first[0].evidence_id
        )
        self.assertTrue(all(item.patient_id == self.patient_a for item in first))
        self.assertNotIn(
            f"evidence-{'b' * 24}-001",
            {item.evidence_id for item in first},
        )

    def test_zero_matches_are_not_selected_and_ties_use_source_order(self):
        retriever = BM25PatientRetriever(self.records)
        ranked = retriever.rank(self.patient_a, "unseen-token")
        self.assertEqual(
            [f"evidence-{'a' * 24}-{index:03d}" for index in range(1, 4)],
            [item.evidence_id for item in ranked],
        )
        self.assertEqual((), retriever.retrieve(self.patient_a, "unseen-token", 3))
        with self.assertRaisesRegex(KeyError, "Unknown BM25 patient"):
            retriever.retrieve("patient-" + "f" * 24, "renal", 3)

    def test_parameters_and_input_validation_are_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "globally unique"):
            BM25PatientRetriever([self.records[0], self.records[0]])
        with self.assertRaisesRegex(ValueError, "positive"):
            BM25PatientRetriever(self.records, k1=0)
        with self.assertRaisesRegex(ValueError, "between zero and one"):
            BM25PatientRetriever(self.records, b=2)
        with self.assertRaisesRegex(ValueError, "positive"):
            BM25PatientRetriever(self.records).retrieve(self.patient_a, "renal", 0)


class ApixabanBM25RunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen, cls.inputs = _frozen_inputs()
        cls.corpus = cls.inputs[2]

    def test_contract_forbids_answer_derived_queries_and_test_labels(self):
        contract = load_bm25_contract()
        self.assertEqual("source_question_only", contract["query"]["source"])
        self.assertFalse(contract["query"]["answer_text_used"])
        self.assertFalse(contract["query"]["fact_field_used"])
        self.assertFalse(contract["test_labels_used"])
        changed = copy.deepcopy(contract)
        changed["query"]["answer_text_used"] = True
        with self.assertRaisesRegex(ApixabanBM25Error, "query construction"):
            validate_bm25_contract(changed)

    def _run(self, root: Path):
        split_path = root / "split.json"
        corpus_path = root / "corpus.json"
        index_path = root / "evidence-index.json"
        _write_private(split_path, self.frozen)
        _write_private(corpus_path, self.corpus)
        index = build_evidence_index_manifest_from_paths(
            split_path,
            corpus_path,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        write_evidence_index_manifest(index, index_path)
        run, predictions = run_bm25_baseline(
            split_path,
            corpus_path,
            index_path,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        return run, predictions

    def test_end_to_end_run_is_complete_restricted_and_label_free(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, predictions = self._run(root)
            self.assertEqual(2, run["counts"]["patient_count"])
            self.assertEqual(23, run["counts"]["question_count"])
            self.assertEqual(46, run["counts"]["query_count"])
            self.assertFalse(
                run["evaluation_boundary"]["independent_evidence_gold_available"]
            )
            self.assertFalse(
                run["evaluation_boundary"]["retrieval_relevance_metrics_reported"]
            )
            serialized = json.dumps(run, sort_keys=True)
            for question in load_question_catalog()["questions"]:
                self.assertNotIn(question["source_question"], serialized)
            self.assertNotIn("answer_status", serialized)
            self.assertTrue(
                all(len(item["selected_evidence"]) <= 3 for item in run["results"])
            )
            self.assertEqual("1.2.0", predictions["prediction_set_version"])
            self.assertEqual(46, len(predictions["predictions"]))

            output = root / "output"
            retrieval_path, prediction_path = write_bm25_run(
                run, predictions, output
            )
            self.assertEqual(0, os.stat(retrieval_path).st_mode & 0o077)
            self.assertEqual(0, os.stat(prediction_path).st_mode & 0o077)
            with self.assertRaises(FileExistsError):
                write_bm25_run(run, predictions, output)

    def test_semantic_validation_rejects_incomplete_question_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            run, _ = self._run(Path(directory))
            removed = run["results"].pop()
            run["counts"]["query_count"] -= 1
            run["counts"]["candidate_document_comparisons"] -= removed[
                "candidate_count"
            ]
            run["counts"]["selected_document_count"] -= len(
                removed["selected_evidence"]
            )
            if removed["selected_evidence"]:
                run["counts"]["queries_with_positive_match"] -= 1
            else:
                run["counts"]["queries_without_positive_match"] -= 1
            unsigned = dict(run)
            unsigned.pop("run_sha256")
            run["run_sha256"] = canonical_sha256(unsigned)
            with self.assertRaisesRegex(ApixabanBM25Error, "grid is incomplete"):
                validate_bm25_run(run)

    def test_write_rejects_prediction_evidence_outside_retrieved_top_k(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, predictions = self._run(root)
            changed = copy.deepcopy(predictions)
            first = changed["predictions"][0]
            other_patient_evidence = next(
                item["selected_evidence"][0]["evidence_id"]
                for item in run["results"]
                if item["patient_id"] != first["patient_id"]
                and item["selected_evidence"]
            )
            first["evidence_ids"] = [other_patient_evidence]
            run["provenance"]["prediction_set_content_sha256"] = canonical_sha256(
                changed
            )
            unsigned = dict(run)
            unsigned.pop("run_sha256")
            run["run_sha256"] = canonical_sha256(unsigned)
            with self.assertRaisesRegex(
                ApixabanBM25Error, "cites unselected evidence"
            ):
                write_bm25_run(run, changed, root / "changed-output")

    def test_cli_requires_local_ack_and_separate_locked_test_ack(self):
        with self.assertRaisesRegex(ValueError, "local-only"):
            main(
                [
                    "--frozen-split", "missing-split.json",
                    "--staging-corpus", "missing-corpus.json",
                    "--evidence-index-manifest", "missing-index.json",
                    "--split", "validation",
                    "--output-dir", "missing-output",
                ]
            )
        with self.assertRaisesRegex(ValueError, "Locked test retrieval"):
            main(
                [
                    "--frozen-split", "missing-split.json",
                    "--staging-corpus", "missing-corpus.json",
                    "--evidence-index-manifest", "missing-index.json",
                    "--split", "test",
                    "--output-dir", "missing-output",
                    "--acknowledge-restricted-data-local-only",
                ]
            )


if __name__ == "__main__":
    unittest.main()
