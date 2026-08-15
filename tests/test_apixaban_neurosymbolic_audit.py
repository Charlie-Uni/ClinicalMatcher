import copy
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_neurosymbolic_audit import (
    ApixabanNeurosymbolicAuditError,
    build_neurosymbolic_readiness_report,
    validate_neurosymbolic_audit_report,
)
from clinical_matcher.apixaban_neurosymbolic_audit_cli import main


PATIENT_ID = "patient-0123456789abcdef01234567"
SOURCE_ID = "note-0123456789abcdef01234567"
EVIDENCE_ID = "evidence-0123456789abcdef01234567-000"


def staging_corpus(catalog):
    questions = []
    for index, item in enumerate(catalog["questions"], start=2):
        boolean = item["question_type"] == "boolean"
        questions.append(
            {
                "criterion_id": item["question_id"],
                "source_criterion_label": item["source_criterion_label"],
                "question_type": item["question_type"],
                "question": item["source_question"],
                "answer_status": "answered",
                "answer_value": True if boolean else 1,
                "not_specified": False,
                "source_row_number": index,
            }
        )
    return {
        "apixaban_corpus_version": "1.0.0",
        "source": {
            "dataset_id": "MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions",
            "dataset_version": "1.0.0",
            "access_policy": "credentialed",
            "license_id": "PhysioNet Restricted Health Data License 1.5.0",
            "terms_url": "https://example.invalid/restricted",
            "source_csv_sha256": "a" * 64,
        },
        "adapter": {
            "name": "mimic-iv-ext-apixaban-csv",
            "version": "1.0.0",
            "pseudonymization": "HMAC-SHA256",
            "evidence_chunk_max_characters": 256,
        },
        "patients": [
            {
                "patient_id": PATIENT_ID,
                "source_id": SOURCE_ID,
                "index_date": None,
                "index_date_status": "unavailable_in_source",
                "evidence": [
                    {
                        "evidence_id": EVIDENCE_ID,
                        "source_id": SOURCE_ID,
                        "source_span": {"start": 0, "end": 24},
                        "text": "Synthetic evidence only.",
                    }
                ],
                "legacy_questions": questions,
            }
        ],
    }


def prediction_set(catalog):
    predictions = []
    for item in catalog["questions"]:
        boolean = item["question_type"] == "boolean"
        predictions.append(
            {
                "patient_id": PATIENT_ID,
                "question_id": item["question_id"],
                "question_type": item["question_type"],
                "fact_status": "present",
                "value": True if boolean else 1,
                "unit": None,
                "abstained": False,
                "abstention_reason": None,
                "evidence_ids": [EVIDENCE_ID],
                "trace_ids": ["synthetic.model"],
            }
        )
    return {
        "prediction_set_version": "1.2.0",
        "benchmark_sha256": "b" * 64,
        "split_manifest_sha256": "c" * 64,
        "split_name": "validation",
        "model_id": "synthetic-model",
        "prompt_version": "synthetic-prompt",
        "inference_config_sha256": "d" * 64,
        "generated_at": "2026-01-01T00:00:00Z",
        "code_commit": "e" * 40,
        "predictions": predictions,
    }


class ApixabanNeurosymbolicAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()

    def report(self, predictions=None, staging=None):
        return build_neurosymbolic_readiness_report(
            prediction_set=predictions or prediction_set(self.catalog),
            staging_corpus=staging or staging_corpus(self.catalog),
            expected_patient_ids=[PATIENT_ID],
            prediction_set_sha256="1" * 64,
            staging_corpus_sha256="2" * 64,
            split_manifest_sha256="3" * 64,
            generated_at="2026-01-01T00:00:00Z",
            code_commit="4" * 40,
        )

    def test_supported_checks_pass_but_eligibility_stays_not_evaluable(self):
        report = self.report()

        self.assertTrue(report["release_gate"]["fact_integrity_checks_pass"])
        self.assertFalse(report["release_gate"]["p4_2_complete"])
        self.assertEqual(0, report["review_required_count"])
        self.assertIsNone(
            report["model_verifier_conflicts"]["conflict_rate"]
        )
        for name in ("time", "negation", "criterion_polarity"):
            check = report["checks"][name]
            self.assertEqual(0, check["evaluable_count"])
            self.assertEqual(23, check["not_evaluable_count"])

    def test_unit_mismatch_is_review_required_not_silently_converted(self):
        predictions = prediction_set(self.catalog)
        numeric = next(
            item
            for item in predictions["predictions"]
            if item["question_type"] == "numeric"
        )
        numeric["unit"] = "invented-unit"

        report = self.report(predictions=predictions)

        self.assertEqual(1, report["checks"]["unit_contract"]["fail_count"])
        self.assertEqual(1, report["review_required_count"])
        self.assertFalse(report["release_gate"]["fact_integrity_checks_pass"])

    def test_cross_patient_or_unknown_evidence_is_review_required(self):
        predictions = prediction_set(self.catalog)
        predictions["predictions"][0]["evidence_ids"] = [
            "evidence-ffffffffffffffffffffffff-000"
        ]

        report = self.report(predictions=predictions)

        self.assertEqual(1, report["checks"]["evidence_link"]["fail_count"])
        self.assertEqual(1, report["review_required_count"])

    def test_known_fact_without_evidence_is_review_required(self):
        predictions = prediction_set(self.catalog)
        predictions["predictions"][0]["evidence_ids"] = []

        report = self.report(predictions=predictions)

        self.assertEqual(1, report["checks"]["missingness"]["fail_count"])
        self.assertEqual(1, report["review_required_count"])

    def test_invalid_numeric_shape_fails_before_audit(self):
        predictions = prediction_set(self.catalog)
        numeric = next(
            item
            for item in predictions["predictions"]
            if item["question_type"] == "numeric"
        )
        numeric["value"] = "not-a-number"

        with self.assertRaises(ValueError):
            self.report(predictions=predictions)

    def test_incomplete_patient_question_grid_is_rejected(self):
        predictions = prediction_set(self.catalog)
        predictions["predictions"].pop()

        with self.assertRaisesRegex(
            ApixabanNeurosymbolicAuditError, "exact split"
        ):
            self.report(predictions=predictions)

    def test_report_validator_rejects_false_zero_conflict_claim(self):
        report = self.report()
        report["model_verifier_conflicts"]["conflict_rate"] = 0.0

        with self.assertRaisesRegex(
            ApixabanNeurosymbolicAuditError, "Conflict rate"
        ):
            validate_neurosymbolic_audit_report(report)

    def test_cli_requires_restricted_and_locked_test_acknowledgements(self):
        with self.assertRaisesRegex(
            SystemExit, "Restricted-data acknowledgement"
        ):
            main(
                [
                    "--predictions",
                    "missing.json",
                    "--staging-corpus",
                    "missing.json",
                    "--frozen-split",
                    "missing.json",
                    "--output",
                    "missing.json",
                ]
            )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "predictions.json"
            path.write_text('{"split_name":"test"}', encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(
                SystemExit, "Locked-test acknowledgement"
            ):
                main(
                    [
                        "--predictions",
                        str(path),
                        "--staging-corpus",
                        "missing.json",
                        "--frozen-split",
                        "missing.json",
                        "--output",
                        "missing.json",
                        "--acknowledge-restricted-data",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
