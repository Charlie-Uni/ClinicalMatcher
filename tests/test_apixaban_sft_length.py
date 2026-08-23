import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_sft_length import (
    ApixabanSFTLengthError,
    build_apixaban_sft_length_report,
    validate_apixaban_sft_length_report,
    write_apixaban_sft_length_outputs,
)
from clinical_matcher.apixaban_sft_length_cli import main
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_sft import FakeTokenizer, _frozen_inputs


def _self_hash(document, field):
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


class OversizePromptTokenizer(FakeTokenizer):
    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        return list(range(20_000))


class ApixabanSFTLengthTests(unittest.TestCase):
    def _build(self, tokenizer=None):
        corpus, _, split, reservation = _frozen_inputs()
        report, plan = build_apixaban_sft_length_report(
            corpus,
            split,
            reservation,
            tokenizer or FakeTokenizer(),
            generated_at="2026-08-23T00:00:00Z",
            code_commit="a" * 40,
            generation_command="synthetic SFT length test",
        )
        return report, plan, corpus, reservation

    def test_uses_full_train_fit_grid_and_selects_smallest_full_tier(self):
        report, plan, corpus, reservation = self._build()
        self.assertIsNotNone(plan)
        self.assertEqual(46, report["population"]["row_count"])
        self.assertEqual(2048, report["selection"]["selected_context_tier"])
        self.assertEqual(2048, plan["context"]["max_seq_len"])
        self.assertEqual(
            report["manifest_sha256"],
            plan["context"]["length_report_sha256"],
        )
        train_fit = set(reservation["partitions"]["train_fit"]["patient_ids"])
        patients = {item["patient_id"]: item for item in corpus["patients"]}
        self.assertEqual(
            train_fit,
            {row["patient_id"] for row in plan["rows"]},
        )
        for row in plan["rows"]:
            expected = [
                item["evidence_id"] for item in patients[row["patient_id"]]["evidence"]
            ]
            self.assertEqual(expected, row["evidence_ids"])
        serialized_report = json.dumps(report)
        for patient_id in train_fit:
            self.assertNotIn(patient_id, serialized_report)
        for patient in corpus["patients"]:
            for evidence in patient["evidence"]:
                self.assertNotIn(evidence["evidence_id"], serialized_report)

    def test_no_input_plan_is_emitted_when_no_approved_tier_fits(self):
        report, plan, _, _ = self._build(OversizePromptTokenizer())
        self.assertIsNone(plan)
        self.assertEqual("no_approved_tier_fits", report["selection"]["status"])
        self.assertIsNone(report["selection"]["selected_context_tier"])

    def test_tampered_selection_fails_closed(self):
        report, _, _, _ = self._build()
        tampered = copy.deepcopy(report)
        tampered["selection"]["selected_context_tier"] = 4096
        tampered["manifest_sha256"] = _self_hash(tampered, "manifest_sha256")
        with self.assertRaisesRegex(ApixabanSFTLengthError, "smallest full fit"):
            validate_apixaban_sft_length_report(tampered)

    def test_writer_is_owner_only_and_refuses_overwrite(self):
        report, plan, _, _ = self._build()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "length"
            paths = write_apixaban_sft_length_outputs(report, plan, root)
            self.assertEqual(2, len(paths))
            self.assertTrue(
                all((os.stat(path).st_mode & 0o777) == 0o600 for path in paths)
            )
            with self.assertRaises(FileExistsError):
                write_apixaban_sft_length_outputs(report, plan, root)

    def test_cli_requires_restricted_data_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "explicit acknowledgement"):
            main(
                [
                    "--staging-corpus", "/restricted/staging.json",
                    "--frozen-split", "/restricted/split.json",
                    "--calibration-reservation", "/restricted/calibration.json",
                    "--tokenizer-directory", "/restricted/tokenizer",
                    "--output-dir", "/restricted/output",
                ]
            )


if __name__ == "__main__":
    unittest.main()
