import copy
import math
import os
import struct
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_dense import (
    ApixabanDenseError,
    load_dense_contract,
    run_dense_baseline,
    validate_dense_contract,
    validate_dense_index_manifest,
    validate_dense_run,
    write_dense_run,
)
from clinical_matcher.apixaban_dense_cli import main
from clinical_matcher.apixaban_evidence_index import (
    build_evidence_index_manifest_from_paths,
    write_evidence_index_manifest,
)
from clinical_matcher.retrieval.dense import (
    DensePatientRetriever,
    deserialize_float32_vectors,
    serialize_float32_vectors,
)
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_evidence_index import _frozen_inputs, _write_private


DIMENSION = 768


def _basis(index: int, value: float = 1.0):
    vector = [0.0] * DIMENSION
    vector[index] = value
    return vector


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


class FakeMedCPTEncoder:
    def __init__(self, contract):
        self.contract = contract

    @staticmethod
    def _encode(text):
        vector = [0.0] * DIMENSION
        lowered = text.casefold()
        vector[0] = 2.0 if "renal" in lowered else 0.25
        vector[1] = 2.0 if "heart" in lowered else 0.25
        vector[2] = 1.0 + (len(text) % 7) / 10
        return vector

    def encode_documents(self, texts):
        return [self._encode(text) for text in texts]

    def encode_queries(self, texts):
        return [self._encode(text) for text in texts]


class DenseRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.patient_a = "patient-" + "a" * 24
        self.patient_b = "patient-" + "b" * 24
        self.records = [
            _record(self.patient_a, "001", 0, "renal function"),
            _record(self.patient_a, "002", 20, "heart rhythm"),
            _record(self.patient_b, "001", 0, "other patient renal"),
        ]

    def test_exact_dot_product_ranking_and_patient_isolation(self):
        vectors = [_basis(0, 2), _basis(1, 2), _basis(0, 10)]
        retriever = DensePatientRetriever(self.records, vectors)
        ranked = retriever.retrieve_vector(self.patient_a, _basis(0), 2)
        self.assertEqual(
            f"evidence-{'a' * 24}-001", ranked[0].evidence_id
        )
        self.assertTrue(all(item.patient_id == self.patient_a for item in ranked))
        self.assertNotIn(
            f"evidence-{'b' * 24}-001",
            {item.evidence_id for item in ranked},
        )

    def test_negative_scores_are_valid_and_ties_use_source_order(self):
        vectors = [_basis(0, -1), _basis(0, -1), _basis(0, 1)]
        retriever = DensePatientRetriever(self.records, vectors)
        ranked = retriever.rank_vector(self.patient_a, _basis(0))
        self.assertEqual([-1.0, -1.0], [item.score for item in ranked])
        self.assertEqual(
            [f"evidence-{'a' * 24}-001", f"evidence-{'a' * 24}-002"],
            [item.evidence_id for item in ranked],
        )

    def test_common_string_interface_and_fail_closed_validation(self):
        vectors = [_basis(0), _basis(1), _basis(0)]
        encoder = lambda texts: [_basis(0) for _ in texts]
        retriever = DensePatientRetriever(
            self.records, vectors, query_encoder=encoder
        )
        self.assertEqual(2, len(retriever.retrieve(self.patient_a, "renal", 2)))
        with self.assertRaisesRegex(KeyError, "Unknown dense patient"):
            retriever.retrieve("patient-" + "f" * 24, "renal", 2)
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            DensePatientRetriever(self.records, vectors[:2])
        broken = copy.deepcopy(vectors)
        broken[0][0] = math.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            DensePatientRetriever(self.records, broken)

    def test_float32_serialization_is_deterministic_little_endian(self):
        payload = serialize_float32_vectors([[1.0, -2.0], [0.5, 4.0]])
        self.assertEqual(16, len(payload))
        self.assertEqual((1.0, -2.0, 0.5, 4.0), struct.unpack("<4f", payload))
        self.assertEqual(
            payload,
            serialize_float32_vectors([[1.0, -2.0], [0.5, 4.0]]),
        )
        self.assertEqual(
            ((1.0, -2.0), (0.5, 4.0)),
            deserialize_float32_vectors(payload, dimension=2, count=2),
        )
        with self.assertRaisesRegex(ValueError, "byte count"):
            deserialize_float32_vectors(payload[:-1], dimension=2, count=2)


class ApixabanDenseRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen, cls.inputs = _frozen_inputs()
        cls.corpus = cls.inputs[2]

    def test_contract_pins_one_public_domain_local_only_dual_encoder(self):
        contract = load_dense_contract()
        self.assertEqual(
            "public_domain_us_government_work", contract["license"]["status"]
        )
        self.assertTrue(contract["runtime"]["local_files_only"])
        self.assertFalse(contract["runtime"]["trust_remote_code"])
        self.assertFalse(contract["test_labels_used"])
        self.assertEqual("source_question_only", contract["query_input"]["source"])
        self.assertFalse(contract["query_input"]["answer_text_used"])
        self.assertEqual(768, contract["representation"]["dimension"])
        changed = copy.deepcopy(contract)
        changed["query_input"]["answer_text_used"] = True
        with self.assertRaisesRegex(ApixabanDenseError, "query construction"):
            validate_dense_contract(changed)

    def _run(self, root: Path):
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
        return run_dense_baseline(
            split_path,
            corpus_path,
            evidence_path,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
            encoder_factory=FakeMedCPTEncoder,
        )

    def test_end_to_end_index_run_and_outputs_are_bound_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, vectors, run, predictions = self._run(root)
            self.assertEqual(2, manifest["counts"]["vector_count"])
            self.assertEqual(2 * DIMENSION * 4, len(vectors))
            self.assertEqual(46, run["counts"]["query_count"])
            self.assertEqual(46, len(predictions["predictions"]))
            self.assertFalse(
                run["evaluation_boundary"]["retrieval_relevance_metrics_reported"]
            )
            paths = write_dense_run(
                manifest, vectors, run, predictions, root / "output"
            )
            self.assertEqual(4, len(paths))
            self.assertTrue(all(os.stat(path).st_mode & 0o077 == 0 for path in paths))
            with self.assertRaises(FileExistsError):
                write_dense_run(manifest, vectors, run, predictions, root / "output")

    def test_fake_rebuild_has_stable_vector_hash_and_index_identity(self):
        with tempfile.TemporaryDirectory() as left_directory:
            with tempfile.TemporaryDirectory() as right_directory:
                left = self._run(Path(left_directory))
                right = self._run(Path(right_directory))
                self.assertEqual(left[1], right[1])
                self.assertEqual(
                    left[0]["index"]["vector_file_sha256"],
                    right[0]["index"]["vector_file_sha256"],
                )
                self.assertEqual(
                    left[0]["index"]["index_id"], right[0]["index"]["index_id"]
                )

    def test_semantic_validators_reject_vector_and_grid_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, vectors, run, _ = self._run(Path(directory))
            corrupted = bytes([vectors[0] ^ 1]) + vectors[1:]
            with self.assertRaisesRegex(ApixabanDenseError, "file hash mismatch"):
                validate_dense_index_manifest(manifest, corrupted)

            removed = run["results"].pop()
            run["counts"]["query_count"] -= 1
            run["counts"]["selected_document_count"] -= len(
                removed["selected_evidence"]
            )
            run["counts"]["candidate_document_comparisons"] -= removed[
                "candidate_count"
            ]
            unsigned = dict(run)
            unsigned.pop("run_sha256")
            run["run_sha256"] = canonical_sha256(unsigned)
            with self.assertRaisesRegex(ApixabanDenseError, "grid is incomplete"):
                validate_dense_run(run)

    def test_cli_requires_local_ack_and_separate_locked_test_ack(self):
        common = [
            "--frozen-split", "missing-split.json",
            "--staging-corpus", "missing-corpus.json",
            "--evidence-index-manifest", "missing-index.json",
            "--output-dir", "missing-output",
        ]
        with self.assertRaisesRegex(ValueError, "local-only"):
            main([*common, "--split", "validation"])
        with self.assertRaisesRegex(ValueError, "Locked test dense retrieval"):
            main(
                [
                    *common,
                    "--split", "test",
                    "--acknowledge-restricted-data-local-only",
                ]
            )


if __name__ == "__main__":
    unittest.main()
