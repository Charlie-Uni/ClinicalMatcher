import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_benchmark import (
    build_apixaban_benchmark,
    serialized_document_sha256,
)
from clinical_matcher.apixaban_quality import build_apixaban_quality_reports
from clinical_matcher.apixaban_split import (
    ApixabanSplitError,
    build_apixaban_split_candidate,
    freeze_apixaban_split,
    split_manifest_view,
    validate_apixaban_split_manifest,
    write_apixaban_split_document,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import SemanticNearDuplicate, canonical_sha256
from tests.test_apixaban_benchmark import synthetic_inputs


SPLIT_COUNTS = {
    "patient_count": 6,
    "question_count": 23,
    "assessment_count": 138,
    "answered_source_count": 132,
    "not_specified_source_count": 3,
    "source_anomaly_count": 3,
}
FRACTIONS = {"train": 0.5, "validation": 0.25, "test": 0.25}


def _self_hash(document, field):
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def split_inputs(exact_duplicate=False):
    base_corpus, base_manifest = synthetic_inputs()
    patients = []
    id_records = []
    row_number = 2
    for index in range(6):
        source = copy.deepcopy(base_corpus["patients"][index % 2])
        token = f"{index + 1:024x}"
        patient_id = f"patient-{token}"
        source_id = f"note-{token}"
        text_index = 0 if exact_duplicate and index == 1 else index
        text = f"Synthetic split fixture note {text_index}."
        source.update({"patient_id": patient_id, "source_id": source_id})
        source["evidence"] = [
            {
                "evidence_id": f"evidence-{token}-001",
                "source_id": source_id,
                "source_span": {"start": 0, "end": len(text)},
                "text": text,
            }
        ]
        for question in source["legacy_questions"]:
            question["source_row_number"] = row_number
            row_number += 1
        patients.append(source)
        id_records.append(
            {
                "patient_id": patient_id,
                "source_id": source_id,
                "note_id": f"synthetic-split-note-{index}",
                "hadm_id": f"synthetic-split-admission-{index}",
            }
        )
    corpus = copy.deepcopy(base_corpus)
    corpus["patients"] = patients
    id_map = {
        "apixaban_id_map_version": "1.0.0",
        "source_csv_sha256": corpus["source"]["source_csv_sha256"],
        "pseudonymization": {
            "algorithm": "HMAC-SHA256",
            "key_id": "synthetic-key-v1",
        },
        "records": id_records,
    }
    manifest = copy.deepcopy(base_manifest)
    manifest["outputs"] = {
        "corpus_sha256": serialized_document_sha256(corpus),
        "id_map_sha256": serialized_document_sha256(id_map),
    }
    manifest["counts"].update(
        {
            "source_row_count": 138,
            "patient_count": 6,
            "criterion_count": 23,
            "evidence_chunk_count": 6,
            "answered_label_count": 132,
            "not_specified_label_count": 3,
            "source_anomaly_label_count": 3,
            "index_date_unavailable_patient_count": 6,
        }
    )
    manifest["manifest_sha256"] = _self_hash(manifest, "manifest_sha256")
    benchmark, benchmark_manifest = build_apixaban_benchmark(
        corpus,
        manifest,
        generated_at="2026-08-13T05:00:00Z",
        code_commit="2" * 40,
        required_source_sha256=None,
        required_counts=SPLIT_COUNTS,
    )
    quality, _ = build_apixaban_quality_reports(
        benchmark,
        benchmark_manifest,
        minimum_cell_size=2,
        generated_at="2026-08-13T05:01:00Z",
        code_commit="3" * 40,
        required_source_sha256=None,
        required_counts=SPLIT_COUNTS,
    )
    return benchmark, benchmark_manifest, corpus, manifest, id_map, quality


def build_candidate(exact_duplicate=False):
    inputs = split_inputs(exact_duplicate=exact_duplicate)
    candidate = build_apixaban_split_candidate(
        *inputs,
        fractions=FRACTIONS,
        seed=17,
        semantic_similarity_threshold=0.95,
        generated_at="2026-08-13T05:02:00Z",
        code_commit="4" * 40,
        generation_command="synthetic split test",
        required_source_sha256=None,
        required_counts=SPLIT_COUNTS,
    )
    return candidate, inputs


class ApixabanSplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_candidate, cls.base_inputs = build_candidate()
        cls.duplicate_candidate, cls.duplicate_inputs = build_candidate(
            exact_duplicate=True
        )

    def test_candidate_is_deterministic_patient_grouped_and_complete(self):
        first = copy.deepcopy(self.base_candidate)
        second, _ = build_candidate()
        self.assertEqual(first, second)
        self.assertEqual(
            {"train": 3, "validation": 2, "test": 1},
            first["policy"]["target_patient_counts"],
        )
        memberships = [
            patient_id
            for partition in first["splits"].values()
            for patient_id in partition["patient_ids"]
        ]
        self.assertEqual(6, len(memberships))
        self.assertEqual(6, len(set(memberships)))
        self.assertEqual("candidate", first["status"])
        self.assertFalse(first["freeze"]["test_locked"])

    def test_raw_admission_and_note_ids_never_enter_manifest(self):
        candidate = self.base_candidate
        serialized = json.dumps(candidate)
        self.assertNotIn("synthetic-split-admission", serialized)
        self.assertNotIn("synthetic-split-note", serialized)

    def test_exact_note_duplicates_are_kept_in_one_split(self):
        candidate = self.duplicate_candidate
        inputs = self.duplicate_inputs
        first, second = inputs[0]["patient_ids"][:2]
        membership = {
            patient_id: split_name
            for split_name, partition in candidate["splits"].items()
            for patient_id in partition["patient_ids"]
        }
        self.assertEqual(membership[first], membership[second])
        self.assertEqual(
            1,
            candidate["isolation"][
                "multi_patient_exact_content_group_count"
            ],
        )

    def test_balance_report_reconciles_each_question(self):
        candidate = self.base_candidate
        self.assertEqual(23, len(candidate["balance"]["questions"]))
        for question in candidate["balance"]["questions"]:
            for group in ("fact_status_counts", "source_status_counts"):
                overall = question["overall"][group]
                summed = {
                    label: sum(
                        question["splits"][name][group][label]
                        for name in ("train", "validation", "test")
                    )
                    for label in overall
                }
                self.assertEqual(overall, summed)
        anomaly_by_split = {
            split_name: sum(
                question["splits"][split_name]["source_status_counts"][
                    "source_anomaly"
                ]
                for question in candidate["balance"]["questions"]
            )
            for split_name in ("train", "validation", "test")
        }
        self.assertEqual(
            {"train": 1, "validation": 1, "test": 1}, anomaly_by_split
        )

    def test_fraction_contract_rejects_implicit_or_invalid_design(self):
        inputs = self.base_inputs
        with self.assertRaisesRegex(ApixabanSplitError, "sum to 1"):
            build_apixaban_split_candidate(
                *inputs,
                fractions={"train": 0.7, "validation": 0.2, "test": 0.2},
                seed=17,
                required_source_sha256=None,
                required_counts=SPLIT_COUNTS,
            )

    def test_manifest_hash_and_cross_split_content_tampering_are_rejected(self):
        candidate = self.base_candidate
        mutated = copy.deepcopy(candidate)
        mutated["policy"]["seed"] = 18
        with self.assertRaisesRegex(ApixabanSplitError, "hash mismatch"):
            validate_apixaban_split_manifest(mutated)
        duplicate = copy.deepcopy(candidate)
        left = duplicate["splits"]["train"]["patient_ids"][0]
        right = duplicate["splits"]["test"]["patient_ids"][0]
        duplicate["splits"]["test"]["patient_content_sha256"][right] = (
            duplicate["splits"]["train"]["patient_content_sha256"][left]
        )
        duplicate["manifest_sha256"] = _self_hash(
            duplicate, "manifest_sha256"
        )
        with self.assertRaisesRegex(ApixabanSplitError, "duplicates cross"):
            validate_apixaban_split_manifest(duplicate)

    def test_freeze_requires_passing_bound_semantic_audit(self):
        candidate = self.base_candidate
        view = split_manifest_view(candidate)
        sizes = [
            len(view.splits[name].entity_ids["patient"])
            for name in ("train", "validation", "test")
        ]
        cross_pairs = sizes[0] * sizes[1] + sizes[0] * sizes[2] + sizes[1] * sizes[2]
        summary = build_semantic_scan_summary(
            manifest=view,
            dimension="patient",
            pairs=(),
            embedding_model_id="synthetic-encoder",
            embedding_model_revision="revision-1",
            pooling="mean",
            vectors_normalized=True,
            search_method="exhaustive_cosine",
            candidate_pairs_evaluated=cross_pairs,
        )
        frozen = freeze_apixaban_split(
            candidate, summary, "Synthetic reviewed decision SPLIT-001"
        )
        self.assertEqual("frozen", frozen["status"])
        self.assertTrue(frozen["freeze"]["test_locked"])
        self.assertEqual(candidate["splits"], frozen["splits"])
        self.assertEqual(
            candidate["manifest_sha256"],
            frozen["freeze"]["audited_candidate_manifest_sha256"],
        )

    def test_semantic_leakage_prevents_freeze(self):
        candidate = self.base_candidate
        view = split_manifest_view(candidate)
        left = view.splits["train"].entity_ids["patient"][0]
        right = view.splits["test"].entity_ids["patient"][0]
        sizes = [
            len(view.splits[name].entity_ids["patient"])
            for name in ("train", "validation", "test")
        ]
        summary = build_semantic_scan_summary(
            manifest=view,
            dimension="patient",
            pairs=(SemanticNearDuplicate("patient", left, right, 0.98),),
            embedding_model_id="synthetic-encoder",
            embedding_model_revision="revision-1",
            pooling="mean",
            vectors_normalized=True,
            search_method="exhaustive_cosine",
            candidate_pairs_evaluated=(
                sizes[0] * sizes[1]
                + sizes[0] * sizes[2]
                + sizes[1] * sizes[2]
            ),
        )
        with self.assertRaisesRegex(ApixabanSplitError, "did not pass"):
            freeze_apixaban_split(candidate, summary, "SPLIT-001")

    def test_writer_is_owner_only_and_refuses_overwrite(self):
        candidate = self.base_candidate
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            write_apixaban_split_document(candidate, path)
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                write_apixaban_split_document(candidate, path)


if __name__ == "__main__":
    unittest.main()
