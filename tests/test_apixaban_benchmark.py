import copy
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from clinical_matcher.apixaban_benchmark import (
    ApixabanBenchmarkError,
    build_apixaban_benchmark,
    serialized_document_sha256,
    validate_apixaban_benchmark,
    verify_apixaban_benchmark_files,
    write_apixaban_benchmark,
)
from clinical_matcher.apixaban_benchmark_cli import main
from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.splits import canonical_sha256


SYNTHETIC_SOURCE_HASH = "b" * 64
SYNTHETIC_COUNTS = {
    "patient_count": 2,
    "question_count": 23,
    "assessment_count": 46,
    "answered_source_count": 44,
    "not_specified_source_count": 1,
    "source_anomaly_count": 1,
}


def _manifest_hash(document):
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def synthetic_inputs():
    catalog = load_question_catalog()
    patients = []
    row_number = 2
    for patient_index in range(2):
        token = f"{patient_index + 1:024x}"
        questions = []
        for question_index, question in enumerate(catalog["questions"]):
            if patient_index == 1 and question_index == 0:
                answer_status = "not_specified"
                answer_value = None
                not_specified = True
            elif patient_index == 1 and question_index == 1:
                answer_status = "source_anomaly"
                answer_value = None
                not_specified = False
            else:
                answer_status = "answered"
                answer_value = (
                    patient_index == 0
                    if question["question_type"] == "boolean"
                    else float(patient_index + question_index + 1)
                )
                not_specified = False
            questions.append(
                {
                    "criterion_id": question["question_id"],
                    "source_criterion_label": question[
                        "source_criterion_label"
                    ],
                    "question_type": question["question_type"],
                    "question": question["source_question"],
                    "answer_status": answer_status,
                    "answer_value": answer_value,
                    "not_specified": not_specified,
                    "source_row_number": row_number,
                }
            )
            row_number += 1
        patients.append(
            {
                "patient_id": f"patient-{token}",
                "source_id": f"note-{token}",
                "index_date": None,
                "index_date_status": "unavailable_in_source",
                "evidence": [
                    {
                        "evidence_id": f"evidence-{token}-001",
                        "source_id": f"note-{token}",
                        "source_span": {"start": 0, "end": 31},
                        "text": "Synthetic non-clinical fixture.",
                    }
                ],
                "legacy_questions": sorted(
                    questions, key=lambda item: item["criterion_id"]
                ),
            }
        )
    corpus = {
        "apixaban_corpus_version": "1.0.0",
        "source": {
            "dataset_id": "MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions",
            "dataset_version": "1.0.0",
            "access_policy": "credentialed",
            "license_id": "PhysioNet Restricted Health Data License 1.5.0",
            "terms_url": "https://example.invalid/restricted-license",
            "source_csv_sha256": SYNTHETIC_SOURCE_HASH,
        },
        "adapter": {
            "name": "mimic-iv-ext-apixaban-csv",
            "version": "1.0.0",
            "pseudonymization": "HMAC-SHA256",
            "evidence_chunk_max_characters": 256,
        },
        "patients": patients,
    }
    manifest = {
        "apixaban_import_manifest_version": "1.0.0",
        "manifest_sha256": "pending",
        "generated_at": "2026-08-13T00:00:00Z",
        "code_commit": "a" * 40,
        "source": {
            "dataset_id": "MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions",
            "dataset_version": "1.0.0",
            "source_csv_sha256": SYNTHETIC_SOURCE_HASH,
            "checksum_manifest_sha256": "c" * 64,
            "license_sha256": "d" * 64,
            "official_checksum_verified": True,
        },
        "adapter": {
            "name": "mimic-iv-ext-apixaban-csv",
            "version": "1.0.0",
            "evidence_chunk_max_characters": 256,
        },
        "pseudonymization": {
            "algorithm": "HMAC-SHA256",
            "key_id": "synthetic-key-v1",
            "raw_ids_in_corpus": False,
            "raw_ids_in_separate_id_map": True,
        },
        "outputs": {
            "corpus_sha256": serialized_document_sha256(corpus),
            "id_map_sha256": "e" * 64,
        },
        "counts": {
            "source_row_count": 46,
            "patient_count": 2,
            "criterion_count": 23,
            "evidence_chunk_count": 2,
            "answered_label_count": 44,
            "not_specified_label_count": 1,
            "source_anomaly_label_count": 1,
            "index_date_unavailable_patient_count": 2,
        },
        "quality": {
            "complete_patient_criterion_grid": True,
            "runtime_patient_source_ready": False,
            "runtime_blocker": "Synthetic fixture has no index dates.",
        },
        "modifications": ["Synthetic test input."],
        "disclosure_note": "Synthetic test input with no patient data.",
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    return corpus, manifest


def build_synthetic():
    corpus, import_manifest = synthetic_inputs()
    return build_apixaban_benchmark(
        corpus,
        import_manifest,
        generated_at="2026-08-13T01:00:00Z",
        code_commit="f" * 40,
        required_source_sha256=None,
        required_counts=SYNTHETIC_COUNTS,
    )


class ApixabanBenchmarkTest(unittest.TestCase):
    def test_builds_complete_fact_grid_without_copying_note_text(self):
        benchmark, manifest = build_synthetic()
        self.assertEqual(2, len(benchmark["patient_ids"]))
        self.assertEqual(46, len(benchmark["assessments"]))
        self.assertEqual(SYNTHETIC_COUNTS, {
            key: manifest["counts"][key] for key in SYNTHETIC_COUNTS
        })
        serialized = json.dumps(benchmark)
        self.assertNotIn("Synthetic non-clinical fixture", serialized)
        self.assertNotIn("source_id", serialized)
        self.assertNotIn("source_row_number", serialized)
        self.assertNotIn("eligible", serialized)

    def test_preserves_unknown_and_source_anomaly_without_guessing(self):
        benchmark, manifest = build_synthetic()
        reasons = [
            item["abstention_reason"]
            for item in benchmark["assessments"]
            if item["abstained"]
        ]
        self.assertEqual(
            ["source_anomaly", "source_not_specified"], sorted(reasons)
        )
        self.assertEqual(2, manifest["counts"]["unknown_count"])
        self.assertTrue(
            all(
                item["value"] is None
                for item in benchmark["assessments"]
                if item["abstained"]
            )
        )

    def test_repeat_generation_has_stable_content_hash(self):
        first, first_manifest = build_synthetic()
        second, second_manifest = build_synthetic()
        self.assertEqual(first, second)
        self.assertEqual(
            serialized_document_sha256(first),
            serialized_document_sha256(second),
        )
        self.assertEqual(
            first_manifest["output"]["benchmark_sha256"],
            second_manifest["output"]["benchmark_sha256"],
        )

    def test_duplicate_or_missing_patient_question_pair_is_rejected(self):
        benchmark, _ = build_synthetic()
        duplicated = copy.deepcopy(benchmark)
        duplicated["assessments"].append(
            copy.deepcopy(duplicated["assessments"][0])
        )
        duplicated["assessments"].sort(
            key=lambda item: (item["patient_id"], item["question_id"])
        )
        with self.assertRaisesRegex(
            ApixabanBenchmarkError, "Assessment IDs must be unique"
        ):
            validate_apixaban_benchmark(
                duplicated,
                required_source_sha256=None,
                required_counts=None,
            )

    def test_staging_hash_mismatch_is_rejected(self):
        corpus, import_manifest = synthetic_inputs()
        evidence = corpus["patients"][0]["evidence"][0]
        evidence["text"] += " changed"
        evidence["source_span"]["end"] += len(" changed")
        with self.assertRaisesRegex(
            ApixabanBenchmarkError, "does not match its import manifest"
        ):
            build_apixaban_benchmark(
                corpus,
                import_manifest,
                required_source_sha256=None,
                required_counts=SYNTHETIC_COUNTS,
            )

    def test_writes_owner_only_files_refuses_overwrite_and_verifies(self):
        benchmark, manifest = build_synthetic()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            benchmark_path, manifest_path = write_apixaban_benchmark(
                benchmark,
                manifest,
                output,
                required_source_sha256=None,
                required_counts=SYNTHETIC_COUNTS,
            )
            for path in (benchmark_path, manifest_path):
                self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            counts = verify_apixaban_benchmark_files(
                benchmark_path,
                manifest_path,
                required_source_sha256=None,
                required_counts=SYNTHETIC_COUNTS,
            )
            self.assertEqual(46, counts["assessment_count"])
            with self.assertRaises(FileExistsError):
                write_apixaban_benchmark(
                    benchmark,
                    manifest,
                    output,
                    required_source_sha256=None,
                    required_counts=SYNTHETIC_COUNTS,
                )

    def test_cli_requires_explicit_restricted_data_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            main(
                [
                    "verify",
                    "--benchmark",
                    "/tmp/nonexistent-benchmark.json",
                ]
            )

    def test_cli_verifies_official_contract_only(self):
        benchmark, manifest = build_synthetic()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            benchmark_path, manifest_path = write_apixaban_benchmark(
                benchmark,
                manifest,
                output,
                required_source_sha256=None,
                required_counts=SYNTHETIC_COUNTS,
            )
            with self.assertRaisesRegex(
                ApixabanBenchmarkError, "pinned official source"
            ):
                with redirect_stdout(StringIO()):
                    main(
                        [
                            "verify",
                            "--benchmark",
                            str(benchmark_path),
                            "--manifest",
                            str(manifest_path),
                            "--acknowledge-restricted-data-local-only",
                        ]
                    )


if __name__ == "__main__":
    unittest.main()
