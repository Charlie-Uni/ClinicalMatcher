import copy
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from clinical_matcher.apixaban_contract import (
    ApixabanContractError,
    load_question_catalog,
    normalize_source_answer,
    validate_fact_assessment,
    validate_question_catalog,
    validate_source_question_definitions,
)
from clinical_matcher.apixaban_contract_cli import main
from clinical_matcher.validation import DocumentValidationError


class ApixabanContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_question_catalog()
        cls.questions = {
            item["source_criterion_label"]: item
            for item in cls.catalog["questions"]
        }

    def test_catalog_freezes_all_23_source_questions(self) -> None:
        self.assertEqual(23, len(self.catalog["questions"]))
        self.assertEqual(
            15,
            sum(
                item["question_type"] == "boolean"
                for item in self.catalog["questions"]
            ),
        )
        self.assertEqual(
            8,
            sum(
                item["question_type"] == "numeric"
                for item in self.catalog["questions"]
            ),
        )
        self.assertTrue(
            all(
                item["canonical_unit"] is None
                for item in self.catalog["questions"]
            )
        )
        self.assertEqual("maximum", self.questions["CREAT"]["aggregation"])
        self.assertEqual("minimum", self.questions["PLT"]["aggregation"])

    def test_catalog_hash_detects_reviewed_mapping_mutation(self) -> None:
        mutated = copy.deepcopy(self.catalog)
        mutated["questions"][0]["fact_field"] = "changed_field"
        with self.assertRaisesRegex(ApixabanContractError, "hash mismatch"):
            validate_question_catalog(mutated)

    def test_catalog_rejects_question_id_not_bound_to_source_text(self) -> None:
        mutated = copy.deepcopy(self.catalog)
        mutated["questions"][0]["source_question"] += " changed"
        unsigned = dict(mutated)
        unsigned.pop("catalog_sha256")
        from clinical_matcher.apixaban_contract import _canonical_sha256

        mutated["catalog_sha256"] = _canonical_sha256(unsigned)
        with self.assertRaisesRegex(ApixabanContractError, "Question ID"):
            validate_question_catalog(mutated)

    def test_official_source_definitions_must_match_catalog_exactly(self) -> None:
        definitions = {
            item["source_criterion_label"]: (
                item["question_type"],
                item["source_question"],
            )
            for item in self.catalog["questions"]
        }
        validate_source_question_definitions(definitions, self.catalog)
        changed = dict(definitions)
        changed["afib"] = ("boolean", "Changed wording")
        with self.assertRaisesRegex(ApixabanContractError, "changed=\['afib'\]"):
            validate_source_question_definitions(changed, self.catalog)

    def test_boolean_answers_round_trip_as_facts_not_eligibility(self) -> None:
        question_id = self.questions["bleeding"]["question_id"]
        present = normalize_source_answer(
            assessment_id="assessment-1",
            patient_id="patient-synthetic",
            question_id=question_id,
            answer_status="answered",
            answer_value=True,
            catalog=self.catalog,
        )
        absent = normalize_source_answer(
            assessment_id="assessment-2",
            patient_id="patient-synthetic",
            question_id=question_id,
            answer_status="answered",
            answer_value=False,
            catalog=self.catalog,
        )
        self.assertEqual("present", present["fact_status"])
        self.assertEqual("absent", absent["fact_status"])
        self.assertNotIn("eligible", json.dumps([present, absent]))

    def test_numeric_and_unknown_answers_round_trip(self) -> None:
        question_id = self.questions["CREAT"]["question_id"]
        numeric = normalize_source_answer(
            assessment_id="assessment-3",
            patient_id="patient-synthetic",
            question_id=question_id,
            answer_status="answered",
            answer_value=1.2,
            catalog=self.catalog,
        )
        unknown = normalize_source_answer(
            assessment_id="assessment-4",
            patient_id="patient-synthetic",
            question_id=question_id,
            answer_status="not_specified",
            answer_value=None,
            catalog=self.catalog,
        )
        anomaly = normalize_source_answer(
            assessment_id="assessment-5",
            patient_id="patient-synthetic",
            question_id=question_id,
            answer_status="source_anomaly",
            answer_value=None,
            catalog=self.catalog,
        )
        self.assertEqual(("present", 1.2, None), (
            numeric["fact_status"], numeric["value"], numeric["unit"]
        ))
        self.assertEqual("source_not_specified", unknown["abstention_reason"])
        self.assertEqual("source_anomaly", anomaly["abstention_reason"])

    def test_numeric_assessment_cannot_be_absent(self) -> None:
        document = normalize_source_answer(
            assessment_id="assessment-6",
            patient_id="patient-synthetic",
            question_id=self.questions["CREAT"]["question_id"],
            answer_status="answered",
            answer_value=1.2,
            catalog=self.catalog,
        )
        document["fact_status"] = "absent"
        document["value"] = False
        with self.assertRaises(DocumentValidationError):
            validate_fact_assessment(document, self.catalog)

    def test_assessment_rejects_invented_unit_and_wrong_question_type(self) -> None:
        document = normalize_source_answer(
            assessment_id="assessment-7",
            patient_id="patient-synthetic",
            question_id=self.questions["CREAT"]["question_id"],
            answer_status="answered",
            answer_value=1.2,
            catalog=self.catalog,
        )
        with_unit = copy.deepcopy(document)
        with_unit["unit"] = "mg/dL"
        with self.assertRaises(DocumentValidationError):
            validate_fact_assessment(with_unit, self.catalog)
        wrong_type = copy.deepcopy(document)
        wrong_type["question_type"] = "boolean"
        with self.assertRaises((DocumentValidationError, ApixabanContractError)):
            validate_fact_assessment(wrong_type, self.catalog)

    def test_unknown_requires_explicit_abstention(self) -> None:
        document = normalize_source_answer(
            assessment_id="assessment-8",
            patient_id="patient-synthetic",
            question_id=self.questions["afib"]["question_id"],
            answer_status="not_specified",
            answer_value=None,
            catalog=self.catalog,
        )
        document["abstained"] = False
        document["abstention_reason"] = None
        with self.assertRaises(DocumentValidationError):
            validate_fact_assessment(document, self.catalog)

    def test_cli_validates_catalog_and_assessment(self) -> None:
        document = normalize_source_answer(
            assessment_id="assessment-cli",
            patient_id="patient-synthetic",
            question_id=self.questions["afib"]["question_id"],
            answer_status="answered",
            answer_value=True,
            catalog=self.catalog,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assessment.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, main([str(path)]))
        self.assertIn("23 questions", output.getvalue())
        self.assertIn("assessment-cli", output.getvalue())


if __name__ == "__main__":
    unittest.main()
