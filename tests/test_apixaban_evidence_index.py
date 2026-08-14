import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_evidence_index import (
    ApixabanEvidenceIndexError,
    build_evidence_index_manifest,
    build_evidence_index_manifest_from_paths,
    evidence_index_records,
    load_evidence_chunk_contract,
    validate_evidence_chunk_contract,
    validate_evidence_index_manifest,
    verify_evidence_index_manifest_from_paths,
    write_evidence_index_manifest,
)
from clinical_matcher.apixaban_evidence_index_cli import main
from clinical_matcher.apixaban_split import (
    freeze_apixaban_split,
    split_manifest_view,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_split import build_candidate


def _write_private(path: Path, document) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _frozen_inputs():
    candidate, inputs = build_candidate()
    view = split_manifest_view(candidate)
    expected_pairs = sum(
        len(view.splits[left].entity_ids["patient"])
        * len(view.splits[right].entity_ids["patient"])
        for left, right in (
            ("train", "validation"),
            ("train", "test"),
            ("validation", "test"),
        )
    )
    summary = build_semantic_scan_summary(
        view,
        "patient",
        (),
        "synthetic-encoder",
        "synthetic-v1",
        "mean",
        True,
        "exhaustive_cosine",
        expected_pairs,
    )
    return freeze_apixaban_split(candidate, summary, "SYNTHETIC-P3.1"), inputs


class ApixabanEvidenceIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen, cls.inputs = _frozen_inputs()
        cls.corpus = cls.inputs[2]
        cls.contract = load_evidence_chunk_contract()

    def validation_ids(self):
        return self.frozen["splits"]["validation"]["patient_ids"]

    def test_contract_forbids_labels_queries_rechunking_and_public_text(self):
        projection = self.contract["input_projection"]
        self.assertFalse(projection["answer_labels_used"])
        self.assertFalse(projection["queries_used"])
        self.assertIn("patients[].legacy_questions", projection["forbidden_fields"])
        self.assertEqual(
            "preserve_staging_chunks_without_rechunking",
            self.contract["chunking"]["strategy"],
        )
        self.assertEqual("none", self.contract["chunking"]["text_normalization"])
        self.assertEqual(
            "within_patient_only", self.contract["index"]["retrieval_scope"]
        )
        self.assertFalse(self.contract["privacy"]["public_text_allowed"])
        changed = copy.deepcopy(self.contract)
        changed["input_projection"]["allowed_fields"].append(
            "patients[].legacy_questions"
        )
        with self.assertRaisesRegex(ApixabanEvidenceIndexError, "allowed fields"):
            validate_evidence_chunk_contract(changed)

    def test_projection_is_label_independent_and_preserves_exact_text(self):
        first = evidence_index_records(self.corpus, self.validation_ids())
        changed = copy.deepcopy(self.corpus)
        for patient in changed["patients"]:
            for question in patient["legacy_questions"]:
                question["answer_status"] = "source_anomaly"
                question["answer_value"] = None
                question["not_specified"] = False
        second = evidence_index_records(changed, self.validation_ids())
        self.assertEqual(first, second)
        first_manifest = build_evidence_index_manifest(
            self.frozen,
            self.corpus,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        changed_manifest = build_evidence_index_manifest(
            self.frozen,
            changed,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        self.assertEqual(first_manifest["index"], changed_manifest["index"])
        source_text = {
            evidence["evidence_id"]: evidence["text"]
            for patient in self.corpus["patients"]
            for evidence in patient["evidence"]
        }
        for record in first:
            self.assertEqual(source_text[record["evidence_id"]], record["text"])
            self.assertIsNone(record["section"])

    def test_projection_reconstructs_multiple_chunks_and_content_changes_hash(self):
        patient_id = self.validation_ids()[0]
        changed = copy.deepcopy(self.corpus)
        patient = next(
            item for item in changed["patients"] if item["patient_id"] == patient_id
        )
        original = patient["evidence"][0]["text"]
        token = patient_id[len("patient-"):]
        midpoint = len(original) // 2
        patient["evidence"] = [
            {
                "evidence_id": f"evidence-{token}-001",
                "source_id": patient["source_id"],
                "source_span": {"start": 0, "end": midpoint},
                "text": original[:midpoint],
            },
            {
                "evidence_id": f"evidence-{token}-002",
                "source_id": patient["source_id"],
                "source_span": {"start": midpoint, "end": len(original)},
                "text": original[midpoint:],
            },
        ]
        records = evidence_index_records(changed, [patient_id])
        self.assertEqual(original, "".join(record["text"] for record in records))
        mutated = copy.deepcopy(changed)
        target = next(
            item for item in mutated["patients"] if item["patient_id"] == patient_id
        )
        text = target["evidence"][0]["text"]
        target["evidence"][0]["text"] = ("X" if text[0] != "X" else "Y") + text[1:]
        mutated_records = evidence_index_records(mutated, [patient_id])
        self.assertNotEqual(
            canonical_sha256(records), canonical_sha256(mutated_records)
        )

    def test_rejects_span_gaps_and_cross_patient_source_ids(self):
        patient_id = self.validation_ids()[0]
        gap = copy.deepcopy(self.corpus)
        patient = next(
            item for item in gap["patients"] if item["patient_id"] == patient_id
        )
        patient["evidence"][0]["source_span"]["start"] = 1
        with self.assertRaisesRegex(ApixabanEvidenceIndexError, "contiguous"):
            evidence_index_records(gap, [patient_id])

        crossed = copy.deepcopy(self.corpus)
        patient = next(
            item for item in crossed["patients"] if item["patient_id"] == patient_id
        )
        patient["evidence"][0]["source_id"] = "note-ffffffffffffffffffffffff"
        with self.assertRaisesRegex(ApixabanEvidenceIndexError, "source"):
            evidence_index_records(crossed, [patient_id])

    def test_manifest_is_deterministic_hash_bound_and_section_explicit(self):
        first = build_evidence_index_manifest(
            self.frozen,
            self.corpus,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        second = build_evidence_index_manifest(
            self.frozen,
            self.corpus,
            "validation",
            generated_at="2026-08-14T12:00:00Z",
            code_commit="a" * 40,
        )
        self.assertEqual(first, second)
        self.assertEqual(2, first["counts"]["patient_count"])
        self.assertEqual(2, first["counts"]["evidence_chunk_count"])
        self.assertEqual(
            "unavailable_in_staging_corpus_1.0.0",
            first["index"]["section_metadata_status"],
        )
        self.assertTrue(first["validation"]["patient_isolation_enforced"])
        self.assertFalse(first["validation"]["text_normalization_applied"])
        tampered = copy.deepcopy(first)
        tampered["counts"]["evidence_chunk_count"] += 1
        with self.assertRaisesRegex(ApixabanEvidenceIndexError, "hash mismatch"):
            validate_evidence_index_manifest(tampered)

    def test_path_build_write_and_verify_are_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            split_path = root / "split.json"
            corpus_path = root / "corpus.json"
            output_path = root / "evidence-index-manifest.json"
            _write_private(split_path, self.frozen)
            _write_private(corpus_path, self.corpus)
            document = build_evidence_index_manifest_from_paths(
                split_path,
                corpus_path,
                "validation",
                generated_at="2026-08-14T12:00:00Z",
                code_commit="a" * 40,
            )
            write_evidence_index_manifest(document, output_path)
            self.assertEqual(0, os.stat(output_path).st_mode & 0o077)
            verified = verify_evidence_index_manifest_from_paths(
                output_path, split_path, corpus_path
            )
            self.assertEqual(document, verified)
            with self.assertRaises(FileExistsError):
                write_evidence_index_manifest(document, output_path)

    def test_cli_requires_local_ack_and_separate_test_ack(self):
        with self.assertRaisesRegex(ValueError, "local-only"):
            main(
                [
                    "build",
                    "--frozen-split", "missing-split.json",
                    "--staging-corpus", "missing-corpus.json",
                    "--split", "validation",
                    "--output", "missing-output.json",
                ]
            )
        with self.assertRaisesRegex(ValueError, "Locked test indexing"):
            main(
                [
                    "build",
                    "--frozen-split", "missing-split.json",
                    "--staging-corpus", "missing-corpus.json",
                    "--split", "test",
                    "--output", "missing-output.json",
                    "--acknowledge-restricted-data-local-only",
                ]
            )


if __name__ == "__main__":
    unittest.main()
