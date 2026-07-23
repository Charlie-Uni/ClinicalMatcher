import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.ingestion.patients import (
    assert_restricted_local_path,
    regenerate_normalized_patient_source,
    write_regenerated_patient_source,
)


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


class PatientIngestionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.source = {
            "patient_source_version": "1.0.0",
            "source": {
                "dataset_id": "synthetic-restricted-shaped-source",
                "dataset_version": "synthetic-v1",
                "access_policy": "restricted",
                "terms_url": "https://example.invalid/restricted-terms",
            },
            "patients": fixture["patients"],
        }

    def test_local_regeneration_validates_and_hashes_without_row_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "output.json"
            input_path.write_text(
                json.dumps(self.source),
                encoding="utf-8",
            )
            source, manifest = regenerate_normalized_patient_source(
                input_path=input_path,
                generated_at="2026-07-23T15:00:00Z",
                code_commit="e" * 40,
            )
            written, manifest_path = write_regenerated_patient_source(
                source,
                manifest,
                output_path,
            )
            self.assertEqual(2, manifest["patient_count"])
            self.assertTrue(written.exists())
            self.assertTrue(manifest_path.exists())
            serialized_manifest = json.dumps(manifest)
            self.assertNotIn("synthetic-patient-a", serialized_manifest)
            self.assertNotIn("Synthetic adult", serialized_manifest)
            with self.assertRaises(FileExistsError):
                write_regenerated_patient_source(
                    source,
                    manifest,
                    output_path,
                )

    def test_unknown_evidence_reference_is_rejected(self) -> None:
        source = copy.deepcopy(self.source)
        source["patients"][0]["facts"][0]["evidence_ids"] = ["missing"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown evidence IDs"):
                regenerate_normalized_patient_source(
                    input_path=path,
                    generated_at="2026-07-23T15:00:00Z",
                    code_commit="e" * 40,
                )

    def test_duplicate_patient_ids_are_rejected(self) -> None:
        source = copy.deepcopy(self.source)
        source["patients"][1]["patient_id"] = source["patients"][0][
            "patient_id"
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be unique"):
                regenerate_normalized_patient_source(
                    input_path=path,
                    generated_at="2026-07-23T15:00:00Z",
                    code_commit="e" * 40,
                )

    def test_repository_restricted_paths_must_be_ignored(self) -> None:
        with self.assertRaisesRegex(ValueError, "gitignore"):
            assert_restricted_local_path(Path("unignored-private.json"))
        assert_restricted_local_path(
            Path("artifacts/private/normalized-patients.json")
        )


if __name__ == "__main__":
    unittest.main()
