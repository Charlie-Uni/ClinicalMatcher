import copy
import csv
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.ingestion.apixaban import (
    ApixabanImportError,
    build_apixaban_staging_corpus,
    generate_pseudonym_key,
    write_apixaban_staging_corpus,
)


HEADERS = (
    "text",
    "note_id",
    "hadm_id",
    "criterion",
    "question_type",
    "question",
    "answer",
    "not_specified",
)


class ApixabanIngestionTest(unittest.TestCase):
    def source_files(self, root: Path):
        source = root / "annotated_apixaban_combined.csv"
        note_a = (
            "Synthetic admission note A.\n\n"
            "The fictional history contains evidence for testing."
        )
        note_b = (
            "Synthetic admission note B.\n\n"
            "This text is independently authored and contains no clinical data."
        )
        rows = [
            (
                note_a,
                "synthetic-note-a",
                "synthetic-admission-a",
                "criterion-a",
                "yes",
                "Is the fictional condition present?",
                "Yes",
                "0",
            ),
            (
                note_a,
                "synthetic-note-a",
                "synthetic-admission-a",
                "criterion-b",
                "numeric",
                "What is the fictional numeric value?",
                "1.2",
                "0",
            ),
            (
                note_b,
                "synthetic-note-b",
                "synthetic-admission-b",
                "criterion-a",
                "yes",
                "Is the fictional condition present?",
                "",
                "1",
            ),
            (
                note_b,
                "synthetic-note-b",
                "synthetic-admission-b",
                "criterion-b",
                "numeric",
                "What is the fictional numeric value?",
                "",
                "0",
            ),
        ]
        with source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADERS)
            writer.writerows(rows)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        checksums = root / "SHA256SUMS.txt"
        checksums.write_text(
            f"{checksum} annotated_apixaban_combined.csv\n",
            encoding="utf-8",
        )
        license_path = root / "LICENSE.txt"
        license_path.write_text(
            "The PhysioNet Restricted Health Data License 1.5.0\n",
            encoding="utf-8",
        )
        key_path = root / "pseudonym.key"
        key_path.write_bytes(b"k" * 32)
        return source, checksums, license_path, key_path, rows

    def build(self, root: Path, key_path: Path = None):
        source, checksums, license_path, default_key, rows = (
            self.source_files(root)
        )
        corpus, id_map, manifest = build_apixaban_staging_corpus(
            source_csv=source,
            checksum_path=checksums,
            license_path=license_path,
            pseudonym_key_path=key_path or default_key,
            pseudonym_key_id="synthetic-key-v1",
            terms_url="https://example.invalid/restricted-license",
            evidence_chunk_max_characters=256,
            generated_at="2026-07-24T10:00:00Z",
            code_commit="a" * 40,
            required_source_sha256=None,
        )
        return corpus, id_map, manifest, rows

    def test_builds_pseudonymized_staging_corpus_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus, id_map, manifest, rows = self.build(Path(directory))
        self.assertEqual(2, manifest["counts"]["patient_count"])
        self.assertEqual(2, manifest["counts"]["criterion_count"])
        self.assertEqual(2, manifest["counts"]["answered_label_count"])
        self.assertEqual(
            1,
            manifest["counts"]["not_specified_label_count"],
        )
        self.assertEqual(
            1,
            manifest["counts"]["source_anomaly_label_count"],
        )
        self.assertFalse(manifest["quality"]["runtime_patient_source_ready"])
        self.assertTrue(
            manifest["quality"]["complete_patient_criterion_grid"]
        )
        corpus_text = json.dumps(corpus)
        for raw_identifier in (
            "synthetic-note-a",
            "synthetic-note-b",
            "synthetic-admission-a",
            "synthetic-admission-b",
        ):
            self.assertNotIn(raw_identifier, corpus_text)
        self.assertIn("synthetic-note-a", json.dumps(id_map))
        self.assertIsNone(corpus["patients"][0]["index_date"])
        statuses = {
            question["answer_status"]
            for patient in corpus["patients"]
            for question in patient["legacy_questions"]
        }
        self.assertEqual(
            {"answered", "not_specified", "source_anomaly"},
            statuses,
        )

    def test_evidence_spans_preserve_exact_synthetic_note_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            corpus, id_map, _, rows = self.build(Path(directory))
        notes_by_raw_id = {
            "synthetic-note-a": rows[0][0],
            "synthetic-note-b": rows[2][0],
        }
        raw_by_patient = {
            record["patient_id"]: record["note_id"]
            for record in id_map["records"]
        }
        for patient in corpus["patients"]:
            original = notes_by_raw_id[raw_by_patient[patient["patient_id"]]]
            for evidence in patient["evidence"]:
                span = evidence["source_span"]
                self.assertEqual(
                    original[span["start"]:span["end"]],
                    evidence["text"],
                )

    def test_same_key_is_stable_and_different_key_changes_ids(self) -> None:
        with tempfile.TemporaryDirectory() as first_directory:
            first, _, _, _ = self.build(Path(first_directory))
        with tempfile.TemporaryDirectory() as second_directory:
            second, _, _, _ = self.build(Path(second_directory))
        with tempfile.TemporaryDirectory() as third_directory:
            root = Path(third_directory)
            different_key = root / "different.key"
            different_key.write_bytes(b"z" * 32)
            third, _, _, _ = self.build(root, key_path=different_key)
        first_ids = [item["patient_id"] for item in first["patients"]]
        second_ids = [item["patient_id"] for item in second["patients"]]
        third_ids = [item["patient_id"] for item in third["patients"]]
        self.assertEqual(first_ids, second_ids)
        self.assertNotEqual(first_ids, third_ids)

    def test_checksum_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, checksums, license_path, key_path, _ = (
                self.source_files(root)
            )
            checksums.write_text(
                f"{'0' * 64} {source.name}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ApixabanImportError, "checksum"):
                build_apixaban_staging_corpus(
                    source,
                    checksums,
                    license_path,
                    key_path,
                    "synthetic-key-v1",
                    "https://example.invalid/restricted-license",
                    required_source_sha256=None,
                    code_commit="a" * 40,
                )

    def test_question_definition_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, checksums, license_path, key_path, _ = (
                self.source_files(root)
            )
            with source.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[-1]["question"] = "A different question definition?"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows(rows)
            checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            checksums.write_text(
                f"{checksum} {source.name}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ApixabanImportError,
                "definitions differ",
            ):
                build_apixaban_staging_corpus(
                    source,
                    checksums,
                    license_path,
                    key_path,
                    "synthetic-key-v1",
                    "https://example.invalid/restricted-license",
                    required_source_sha256=None,
                    code_commit="a" * 40,
                )

    def test_writes_separate_owner_only_id_map_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus, id_map, manifest, _ = self.build(root)
            output = root / "corpus.json"
            corpus_path, id_map_path, manifest_path = (
                write_apixaban_staging_corpus(
                    corpus,
                    id_map,
                    manifest,
                    output,
                )
            )
            for path in (corpus_path, id_map_path, manifest_path):
                self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                write_apixaban_staging_corpus(
                    corpus,
                    id_map,
                    manifest,
                    output,
                )

    def test_key_generation_is_owner_only_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pseudonym.key"
            generate_pseudonym_key(path)
            self.assertEqual(32, len(path.read_bytes()))
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                generate_pseudonym_key(path)

    def test_manifest_hash_detects_output_mutation_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus, id_map, manifest, _ = self.build(root)
            mutated = copy.deepcopy(corpus)
            mutated["patients"][0]["evidence"][0]["text"] += "mutation"
            with self.assertRaisesRegex(ApixabanImportError, "hash"):
                write_apixaban_staging_corpus(
                    mutated,
                    id_map,
                    manifest,
                    root / "corpus.json",
                )


if __name__ == "__main__":
    unittest.main()
