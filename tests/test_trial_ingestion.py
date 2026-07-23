import copy
import json
import unittest
from pathlib import Path

from clinical_matcher.ingestion.trials import (
    TrialImportError,
    normalize_study,
    parse_eligibility_criteria,
)


STUDY_FIXTURE = Path(
    "fixtures/synthetic/clinicaltrials_api_study.json"
)
VERSION_FIXTURE = Path(
    "fixtures/synthetic/clinicaltrials_api_version.json"
)
COMMIT = "c" * 40
RETRIEVED_AT = "2026-07-23T14:00:00Z"


class TrialIngestionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.study = json.loads(STUDY_FIXTURE.read_text(encoding="utf-8"))
        cls.version = json.loads(
            VERSION_FIXTURE.read_text(encoding="utf-8")
        )

    def normalize(self, study=None):
        return normalize_study(
            study or self.study,
            self.version,
            retrieved_at=RETRIEVED_AT,
            importer_code_commit=COMMIT,
        )

    def test_normalizes_public_protocol_with_exact_source_spans(self) -> None:
        document = self.normalize()
        self.assertEqual("1.0.0", document["protocol_version"])
        self.assertEqual("NCT99999999", document["nct_id"])
        self.assertEqual("2.0.5", document["api_version"])
        self.assertEqual("2026-07-22", document["registry_snapshot_date"])
        self.assertEqual(4, len(document["criteria"]))
        self.assertEqual(
            ["inclusion", "inclusion", "exclusion", "exclusion"],
            [item["criterion_type"] for item in document["criteria"]],
        )
        raw = document["eligibility_text"]
        for criterion in document["criteria"]:
            span = criterion["source_span"]
            self.assertEqual(
                criterion["source_text"],
                raw[span["start"] : span["end"]],
            )
            self.assertEqual(document["source_id"], criterion["source_id"])
        self.assertEqual(
            "eGFR >50 mL/min/1.73m2 measured within 30 days",
            document["criteria"][1]["normalized_text"],
        )

    def test_ids_are_stable_across_retrieval_times(self) -> None:
        first = self.normalize()
        second = normalize_study(
            self.study,
            self.version,
            retrieved_at="2026-07-24T14:00:00Z",
            importer_code_commit=COMMIT,
        )
        self.assertEqual(
            [item["criterion_id"] for item in first["criteria"]],
            [item["criterion_id"] for item in second["criteria"]],
        )
        self.assertEqual(first["source_id"], second["source_id"])

    def test_record_version_changes_when_eligibility_text_changes(self) -> None:
        study = copy.deepcopy(self.study)
        study["protocolSection"]["eligibilityModule"][
            "eligibilityCriteria"
        ] += "\n3. Synthetic new exclusion"
        changed = self.normalize(study)
        original = self.normalize()
        self.assertNotEqual(
            original["source_record_version"],
            changed["source_record_version"],
        )

    def test_missing_polarity_headings_fail_instead_of_guessing(self) -> None:
        with self.assertRaisesRegex(TrialImportError, "polarity"):
            parse_eligibility_criteria(
                nct_id="NCT99999999",
                source_id="synthetic-source",
                eligibility_text="Age 18 years or older",
            )

    def test_paragraph_sections_are_preserved_conservatively(self) -> None:
        text = (
            "Inclusion Criteria:\n\nAdults with the synthetic condition.\n\n"
            "Exclusion Criteria:\n\nActive synthetic contraindication."
        )
        criteria = parse_eligibility_criteria(
            nct_id="NCT99999999",
            source_id="synthetic-source",
            eligibility_text=text,
        )
        self.assertEqual(2, len(criteria))
        self.assertEqual(
            "Adults with the synthetic condition.",
            criteria[0].source_text,
        )

    def test_bold_headings_and_windows_line_endings_are_supported(self) -> None:
        text = (
            "**Inclusion Criteria:**\r\n\r\n* Synthetic inclusion\r\n\r\n"
            "**Exclusion Criteria:**\r\n\r\n* Synthetic exclusion"
        )
        criteria = parse_eligibility_criteria(
            nct_id="NCT99999999",
            source_id="synthetic-source",
            eligibility_text=text,
        )
        self.assertEqual(2, len(criteria))
        self.assertEqual("Synthetic inclusion", criteria[0].source_text)


if __name__ == "__main__":
    unittest.main()
