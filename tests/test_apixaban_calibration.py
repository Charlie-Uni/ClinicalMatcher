import copy
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_calibration import (
    ApixabanCalibrationError,
    build_apixaban_calibration_reservation,
    patient_selection_digest,
    validate_apixaban_calibration_reservation,
    write_apixaban_calibration_reservation,
)
from clinical_matcher.apixaban_calibration_cli import main
from clinical_matcher.apixaban_split import (
    freeze_apixaban_split,
    split_manifest_view,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_split import build_candidate


def frozen_split():
    candidate, _ = build_candidate()
    view = split_manifest_view(candidate)
    sizes = [
        len(view.splits[name].entity_ids["patient"])
        for name in ("train", "validation", "test")
    ]
    cross_pairs = (
        sizes[0] * sizes[1]
        + sizes[0] * sizes[2]
        + sizes[1] * sizes[2]
    )
    summary = build_semantic_scan_summary(
        manifest=view,
        dimension="patient",
        pairs=(),
        embedding_model_id="synthetic-encoder",
        embedding_model_revision="synthetic-v1",
        pooling="mean",
        vectors_normalized=True,
        search_method="exhaustive_cosine",
        candidate_pairs_evaluated=cross_pairs,
    )
    return freeze_apixaban_split(
        candidate, summary, "Synthetic calibration reservation test"
    )


def build_reservation(split=None):
    return build_apixaban_calibration_reservation(
        split or frozen_split(),
        calibration_patient_count=1,
        generated_at="2026-08-21T05:00:00Z",
        code_commit="a" * 40,
        generation_command="synthetic calibration reservation test",
    )


def self_hash(document):
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


class ApixabanCalibrationReservationTests(unittest.TestCase):
    def test_selection_is_deterministic_and_uses_train_only(self):
        split = frozen_split()
        first = build_reservation(split)
        second = build_reservation(split)
        self.assertEqual(first, second)

        source_train = split["splits"]["train"]["patient_ids"]
        expected = min(
            source_train,
            key=lambda patient_id: (
                patient_selection_digest(
                    patient_id,
                    staging_corpus_sha256=split["dataset"][
                        "staging_corpus_sha256"
                    ],
                    split_manifest_sha256=split["manifest_sha256"],
                ),
                patient_id,
            ),
        )
        self.assertEqual(
            [expected],
            first["partitions"]["calibration_only"]["patient_ids"],
        )
        selected = {
            patient_id
            for partition in first["partitions"].values()
            for patient_id in partition["patient_ids"]
        }
        self.assertEqual(set(source_train), selected)
        self.assertTrue(
            selected.isdisjoint(split["splits"]["validation"]["patient_ids"])
        )
        self.assertTrue(
            selected.isdisjoint(split["splits"]["test"]["patient_ids"])
        )

    def test_candidate_split_and_invalid_counts_fail_closed(self):
        candidate, _ = build_candidate()
        with self.assertRaisesRegex(
            ApixabanCalibrationError, "requires a frozen"
        ):
            build_reservation(candidate)
        split = frozen_split()
        for count in (0, split["splits"]["train"]["patient_count"]):
            with self.subTest(count=count), self.assertRaisesRegex(
                ApixabanCalibrationError, "leave at least one train-fit"
            ):
                build_apixaban_calibration_reservation(
                    split,
                    calibration_patient_count=count,
                    generated_at="2026-08-21T05:00:00Z",
                    code_commit="a" * 40,
                    generation_command="synthetic calibration test",
                )

    def test_source_bound_validation_rejects_membership_tampering(self):
        split = frozen_split()
        reservation = build_reservation(split)
        tampered = copy.deepcopy(reservation)
        replacement = split["splits"]["validation"]["patient_ids"][0]
        calibration = tampered["partitions"]["calibration_only"]
        calibration["patient_ids"] = [replacement]
        calibration["patient_ids_sha256"] = canonical_sha256([replacement])
        tampered["manifest_sha256"] = self_hash(tampered)
        with self.assertRaisesRegex(
            ApixabanCalibrationError, "deterministic selection"
        ):
            validate_apixaban_calibration_reservation(tampered, split)

    def test_policy_count_and_content_hash_are_recomputed(self):
        reservation = build_reservation()
        wrong_count = copy.deepcopy(reservation)
        wrong_count["policy"]["calibration_patient_count"] = 2
        wrong_count["manifest_sha256"] = self_hash(wrong_count)
        with self.assertRaisesRegex(
            ApixabanCalibrationError, "does not match the frozen policy"
        ):
            validate_apixaban_calibration_reservation(wrong_count)

        wrong_hash = copy.deepcopy(reservation)
        wrong_hash["partitions"]["train_fit"]["patient_ids_sha256"] = "0" * 64
        wrong_hash["manifest_sha256"] = self_hash(wrong_hash)
        with self.assertRaisesRegex(
            ApixabanCalibrationError, "patient-list hash mismatch"
        ):
            validate_apixaban_calibration_reservation(wrong_hash)

    def test_writer_is_owner_only_and_refuses_overwrite(self):
        reservation = build_reservation()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "calibration.json"
            write_apixaban_calibration_reservation(reservation, output)
            self.assertEqual(0o600, os.stat(output).st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                write_apixaban_calibration_reservation(reservation, output)

    def test_cli_requires_restricted_data_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            main(
                [
                    "--frozen-split",
                    "/restricted/split.json",
                    "--calibration-patient-count",
                    "1",
                    "--output",
                    "/restricted/calibration.json",
                ]
            )


if __name__ == "__main__":
    unittest.main()
