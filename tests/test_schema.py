import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.fixture import SCHEMA_VERSION, load_fixture
from clinical_matcher.validation import (
    DocumentValidationError,
    validate_document,
)


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


class SchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def assert_semantically_invalid(
        self, document: dict, message: str
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, message):
                load_fixture(path)

    def test_fixture_conforms_to_frozen_schema(self) -> None:
        validate_document(self.document)
        fixture = load_fixture(FIXTURE)
        self.assertEqual(SCHEMA_VERSION, fixture.schema_version)

    def test_unknown_schema_version_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["schema_version"] = "2.0.0"
        with self.assertRaises(DocumentValidationError):
            validate_document(document)

    def test_atom_without_provenance_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        atom = document["trials"][0]["criteria"][0]["expression"]["atom"]
        del atom["provenance"]
        with self.assertRaises(DocumentValidationError):
            validate_document(document)

    def test_llm_provenance_requires_model_and_prompt_versions(self) -> None:
        document = copy.deepcopy(self.document)
        provenance = document["trials"][0]["criteria"][0]["expression"][
            "atom"
        ]["provenance"]
        provenance["method"] = "llm"
        with self.assertRaises(DocumentValidationError):
            validate_document(document)

    def test_invalid_date_format_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        document["patients"][0]["index_date"] = "2026/01/01"
        with self.assertRaises(DocumentValidationError):
            validate_document(document)

    def test_source_span_outside_source_text_is_rejected(self) -> None:
        document = copy.deepcopy(self.document)
        criterion = document["trials"][0]["criteria"][0]
        criterion["expression"]["atom"]["provenance"]["source_span"][
            "end"
        ] = len(criterion["source"]["source_text"]) + 1
        self.assert_semantically_invalid(document, "span exceeds source text")

    def test_gold_annotation_cannot_reference_unknown_evidence(self) -> None:
        document = copy.deepcopy(self.document)
        judgment = document["gold"]["criteria"]["synthetic-patient-a"][
            "synthetic-trial-renal-high"
        ]["renal-high-age"]
        judgment["annotations"][0]["evidence_ids"] = ["unknown-evidence"]
        self.assert_semantically_invalid(document, "unknown evidence IDs")

    def test_gold_requires_unique_annotators(self) -> None:
        document = copy.deepcopy(self.document)
        judgment = document["gold"]["trials"]["synthetic-patient-a"][
            "synthetic-trial-renal-high"
        ]
        judgment["annotations"][1]["annotator_id"] = judgment["annotations"][
            0
        ]["annotator_id"]
        self.assert_semantically_invalid(
            document, "at least two unique annotators"
        )


if __name__ == "__main__":
    unittest.main()
