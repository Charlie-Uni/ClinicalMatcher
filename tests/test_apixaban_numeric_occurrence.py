import copy
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_numeric_occurrence import (
    ApixabanNumericOccurrenceError,
    build_numeric_occurrence_report,
    contains_exact_numeric_token,
    load_numeric_occurrence_contract,
    validate_numeric_occurrence_report,
    write_numeric_occurrence_report,
)
from clinical_matcher.apixaban_numeric_occurrence_cli import main
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_evidence_index import _frozen_inputs


class NumericTokenTests(unittest.TestCase):
    def test_decimal_token_matching_is_exact_and_boundary_aware(self):
        self.assertTrue(contains_exact_numeric_token(["platelets 1,234.50"], 1234.5))
        self.assertTrue(contains_exact_numeric_token(["creatinine .8 mg/dL"], 0.8))
        self.assertFalse(contains_exact_numeric_token(["value 10.20"], 10.2 + 0.1))
        self.assertFalse(contains_exact_numeric_token(["code A55B"], 55))
        self.assertFalse(contains_exact_numeric_token(["value 155"], 55))


class NumericOccurrenceReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen, cls.inputs = _frozen_inputs()
        cls.benchmark = cls.inputs[0]
        cls.corpus = cls.inputs[2]

    def _report(self):
        corpus = copy.deepcopy(self.corpus)
        validation_ids = set(
            self.frozen["splits"]["validation"]["patient_ids"]
        )
        assessments_by_patient = {}
        for assessment in self.benchmark["assessments"]:
            if (
                assessment["patient_id"] in validation_ids
                and assessment["question_type"] == "numeric"
                and assessment["fact_status"] == "present"
            ):
                assessments_by_patient.setdefault(
                    assessment["patient_id"], []
                ).append(assessment["value"])
        evidence_by_patient = {}
        for patient in corpus["patients"]:
            if patient["patient_id"] not in validation_ids:
                continue
            values = " ".join(str(value) for value in assessments_by_patient.get(
                patient["patient_id"], []
            ))
            text = f"Synthetic numeric values {values}."
            evidence = patient["evidence"][0]
            evidence["text"] = text
            evidence["source_span"] = {"start": 0, "end": len(text)}
            evidence_by_patient[patient["patient_id"]] = evidence["evidence_id"]
        catalog = load_question_catalog()
        results = []
        for patient_id in sorted(validation_ids):
            for question in catalog["questions"]:
                results.append(
                    {
                        "patient_id": patient_id,
                        "question_id": question["question_id"],
                        "selected_evidence": [
                            {"evidence_id": evidence_by_patient[patient_id]}
                        ],
                    }
                )
        runs = {
            name: {"run_sha256": character * 64, "results": copy.deepcopy(results)}
            for name, character in (
                ("bm25", "a"),
                ("medcpt_dense", "b"),
                ("rrf60", "c"),
            )
        }
        return build_numeric_occurrence_report(
            self.benchmark,
            self.frozen,
            corpus,
            runs,
            benchmark_sha256="d" * 64,
            generated_at="2026-08-15T00:00:00Z",
            code_commit="e" * 40,
        )

    def test_contract_and_report_keep_the_signal_explicitly_weak(self):
        contract = load_numeric_occurrence_contract()
        self.assertEqual("validation", contract["split_name"])
        self.assertFalse(contract["test_labels_used"])
        self.assertFalse(contract["interpretation"]["evidence_relevance_metric"])
        report = self._report()
        self.assertEqual(46, report["population"]["split_assessment_count"])
        self.assertGreater(report["population"]["evaluable_count"], 0)
        self.assertTrue(report["population"]["reconciliation_passed"])
        for metrics in report["retrievers"].values():
            self.assertEqual(
                metrics["evaluable_count"], metrics["occurrence_at_1_count"]
            )
            self.assertEqual(1.0, metrics["occurrence_at_3_rate"])

    def test_report_validator_rejects_metric_inflation(self):
        report = self._report()
        report["retrievers"]["bm25"]["occurrence_at_1_count"] += 1
        unsigned = dict(report)
        unsigned.pop("report_sha256")
        report["report_sha256"] = canonical_sha256(unsigned)
        with self.assertRaisesRegex(
            ApixabanNumericOccurrenceError, "counts are impossible"
        ):
            validate_numeric_occurrence_report(report)

    def test_writer_is_owner_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "numeric-occurrence.json"
            written = write_numeric_occurrence_report(self._report(), output)
            self.assertEqual(0, os.stat(written).st_mode & 0o077)
            with self.assertRaises(FileExistsError):
                write_numeric_occurrence_report(self._report(), output)

    def test_cli_requires_restricted_data_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "local-only"):
            main(
                [
                    "--benchmark", "missing-benchmark.json",
                    "--frozen-split", "missing-split.json",
                    "--staging-corpus", "missing-corpus.json",
                    "--bm25-run", "missing-bm25.json",
                    "--dense-run", "missing-dense.json",
                    "--rrf-run", "missing-rrf.json",
                    "--output", "missing-output.json",
                ]
            )


if __name__ == "__main__":
    unittest.main()
