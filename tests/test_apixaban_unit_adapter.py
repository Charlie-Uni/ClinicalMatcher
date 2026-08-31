import copy
import unittest

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_unit_adapter import (
    NON_INTEGER_REASON,
    OUT_OF_RANGE_REASON,
    UNEXPECTED_UNIT_REASON,
    ApixabanUnitAdapterError,
    adapt_fact_rows,
    load_unit_adapter_contract,
    validate_unit_adapter_contract,
)
from clinical_matcher.splits import canonical_sha256


def one_patient_rows():
    rows = []
    for question in load_question_catalog()["questions"]:
        if question["question_type"] == "numeric":
            status = "unknown"
            value = None
        else:
            status = "absent"
            value = False
        rows.append(
            {
                "patient_id": "patient-000000000000000000000000",
                "question_id": question["question_id"],
                "question_type": question["question_type"],
                "fact_status": status,
                "value": value,
                "unit": None,
            }
        )
    return rows


def set_numeric(rows, question_id, value, *, unit=None):
    for row in rows:
        if row["question_id"] == question_id:
            row.update(
                {
                    "fact_status": "present",
                    "value": value,
                    "unit": unit,
                }
            )
            return
    raise AssertionError(f"Unknown synthetic question: {question_id}")


class ApixabanUnitAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_unit_adapter_contract()
        cls.entries = {
            entry["question_id"]: entry for entry in cls.contract["entries"]
        }

    def test_contract_is_owner_approved_and_self_authenticating(self):
        self.assertEqual(
            "owner_approved_frozen_pre_validation",
            self.contract["contract_status"],
        )
        self.assertTrue(self.contract["owner_review"]["approved"])
        self.assertTrue(
            self.contract["validation_authorization"]["authorized"]
        )
        self.assertFalse(
            self.contract["validation_authorization"]["locked_test_authorized"]
        )
        unsigned = dict(self.contract)
        recorded = unsigned.pop("contract_sha256")
        self.assertEqual(recorded, canonical_sha256(unsigned))

    def test_contract_rejects_rehashed_semantic_changes(self):
        changed = copy.deepcopy(self.contract)
        changed["entries"][0]["maximum_inclusive"] = 7
        unsigned = dict(changed)
        unsigned.pop("contract_sha256")
        changed["contract_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(
            ApixabanUnitAdapterError, "entry changed"
        ):
            validate_unit_adapter_contract(changed)

    def test_chads2_uses_mathematical_integer_not_storage_type(self):
        question_id = "apixaban-q-e6783d58af7c09d2"
        integral = one_patient_rows()
        set_numeric(integral, question_id, 3.0)
        adapted, _ = adapt_fact_rows(
            integral, source_name="released_gold"
        )
        row = next(item for item in adapted if item["question_id"] == question_id)
        self.assertEqual("present", row["fact_status"])
        self.assertEqual(3.0, row["value"])

        fractional = one_patient_rows()
        set_numeric(fractional, question_id, 3.2)
        adapted, report = adapt_fact_rows(
            fractional, source_name="released_gold"
        )
        row = next(item for item in adapted if item["question_id"] == question_id)
        self.assertEqual("unknown", row["fact_status"])
        self.assertEqual(NON_INTEGER_REASON, row["adapter_reason"])
        diagnostic = next(
            item for item in report["per_question"]
            if item["question_id"] == question_id
        )
        self.assertEqual(1, diagnostic["integer_violation_count"])
        self.assertEqual(0, diagnostic["out_of_range_count"])

    def test_all_inclusive_boundaries_pass_and_outside_values_abstain(self):
        for question_id, entry in self.entries.items():
            for value in (
                entry["minimum_inclusive"],
                entry["maximum_inclusive"],
            ):
                with self.subTest(question_id=question_id, value=value):
                    rows = one_patient_rows()
                    set_numeric(rows, question_id, value)
                    adapted, _ = adapt_fact_rows(
                        rows, source_name="released_gold"
                    )
                    row = next(
                        item for item in adapted
                        if item["question_id"] == question_id
                    )
                    self.assertEqual("present", row["fact_status"])
                    self.assertEqual(entry["assumed_unit"], row["unit"])

            outside = float(entry["maximum_inclusive"]) + 1
            rows = one_patient_rows()
            set_numeric(rows, question_id, outside)
            adapted, report = adapt_fact_rows(
                rows, source_name="model_predictions"
            )
            row = next(
                item for item in adapted if item["question_id"] == question_id
            )
            self.assertEqual("unknown", row["fact_status"])
            self.assertIsNone(row["value"])
            self.assertIsNone(row["unit"])
            self.assertEqual(OUT_OF_RANGE_REASON, row["adapter_reason"])
            diagnostic = next(
                item for item in report["per_question"]
                if item["question_id"] == question_id
            )
            self.assertEqual(1, diagnostic["out_of_range_count"])
            self.assertEqual(
                1.0, diagnostic["out_of_range_fraction_of_known_inputs"]
            )
            self.assertEqual(
                1.0, diagnostic["out_of_range_fraction_of_all_rows"]
            )

    def test_no_unit_conversion_or_guessing_occurs(self):
        hemoglobin_id = "apixaban-q-a69f4c14589e7f29"
        rows = one_patient_rows()
        set_numeric(rows, hemoglobin_id, 100)
        adapted, _ = adapt_fact_rows(rows, source_name="released_gold")
        row = next(
            item for item in adapted if item["question_id"] == hemoglobin_id
        )
        self.assertEqual("unknown", row["fact_status"])
        self.assertEqual(OUT_OF_RANGE_REASON, row["adapter_reason"])

        rows = one_patient_rows()
        set_numeric(rows, hemoglobin_id, 6.2)
        adapted, _ = adapt_fact_rows(rows, source_name="released_gold")
        row = next(
            item for item in adapted if item["question_id"] == hemoglobin_id
        )
        self.assertEqual("present", row["fact_status"])
        self.assertEqual("g/dL", row["unit"])

    def test_unexpected_source_unit_abstains_and_is_reported(self):
        creatinine_id = "apixaban-q-b920477ded648b17"
        rows = one_patient_rows()
        set_numeric(rows, creatinine_id, 1.1, unit="mg/dL")
        adapted, report = adapt_fact_rows(
            rows, source_name="model_predictions"
        )
        row = next(
            item for item in adapted if item["question_id"] == creatinine_id
        )
        self.assertEqual("unknown", row["fact_status"])
        self.assertEqual(UNEXPECTED_UNIT_REASON, row["adapter_reason"])
        diagnostic = next(
            item for item in report["per_question"]
            if item["question_id"] == creatinine_id
        )
        self.assertEqual(1, diagnostic["unexpected_source_unit_count"])

    def test_report_has_no_patient_identifiers_and_reconciles(self):
        rows = one_patient_rows()
        adapted, report = adapt_fact_rows(rows, source_name="released_gold")
        self.assertEqual(23, len(adapted))
        self.assertEqual(23, report["row_count"])
        self.assertEqual(8, report["numeric_row_count"])
        self.assertEqual(8, len(report["per_question"]))
        self.assertNotIn("patient_id", repr(report))
        for item in report["per_question"]:
            self.assertEqual(
                item["total_count"],
                item["known_input_count"] + item["source_unknown_count"],
            )
            self.assertEqual(0, item["out_of_range_count"])
            self.assertEqual(0.0, item["out_of_range_fraction_of_all_rows"])

    def test_source_name_is_closed(self):
        with self.assertRaisesRegex(
            ApixabanUnitAdapterError, "source name"
        ):
            adapt_fact_rows(one_patient_rows(), source_name="path/to/file")


if __name__ == "__main__":
    unittest.main()
