import copy
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_benchmark import (
    build_apixaban_benchmark,
    serialized_document_sha256,
)
from clinical_matcher.apixaban_calibration import build_apixaban_calibration_reservation
from clinical_matcher.apixaban_contract import (
    known_fact_allows_empty_evidence,
    load_question_catalog,
    question_index,
)
from clinical_matcher.apixaban_quality import build_apixaban_quality_reports
from clinical_matcher.apixaban_silver_audit import (
    ApixabanSilverAuditError,
    build_silver_audit_package,
    build_silver_quality_gate,
    finalize_silver_judgments,
    sampling_digest,
    validate_silver_judgments,
    write_silver_audit_package,
)
from clinical_matcher.apixaban_silver_audit_cli import main
from clinical_matcher.apixaban_split import (
    build_apixaban_split_candidate,
    freeze_apixaban_split,
    split_manifest_view,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_benchmark import synthetic_inputs


PATIENT_COUNT = 20
COUNTS = {
    "patient_count": PATIENT_COUNT,
    "question_count": 23,
    "assessment_count": PATIENT_COUNT * 23,
    "answered_source_count": PATIENT_COUNT * 22,
    "not_specified_source_count": PATIENT_COUNT // 2,
    "source_anomaly_count": PATIENT_COUNT // 2,
}


def _self_hash(document, field):
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _frozen_inputs():
    base_corpus, base_manifest = synthetic_inputs()
    patients = []
    id_records = []
    row_number = 2
    for index in range(PATIENT_COUNT):
        patient = copy.deepcopy(base_corpus["patients"][index % 2])
        token = f"{index + 1:024x}"
        patient_id = f"patient-{token}"
        source_id = f"note-{token}"
        text = f"Synthetic silver audit fixture note {index}."
        patient.update({"patient_id": patient_id, "source_id": source_id})
        patient["evidence"] = [
            {
                "evidence_id": f"evidence-{token}-001",
                "source_id": source_id,
                "source_span": {"start": 0, "end": len(text)},
                "text": text,
            }
        ]
        for question in patient["legacy_questions"]:
            question["source_row_number"] = row_number
            row_number += 1
        patients.append(patient)
        id_records.append(
            {
                "patient_id": patient_id,
                "source_id": source_id,
                "note_id": f"synthetic-audit-note-{index}",
                "hadm_id": f"synthetic-audit-admission-{index}",
            }
        )
    corpus = copy.deepcopy(base_corpus)
    corpus["patients"] = patients
    id_map = {
        "apixaban_id_map_version": "1.0.0",
        "source_csv_sha256": corpus["source"]["source_csv_sha256"],
        "pseudonymization": {"algorithm": "HMAC-SHA256", "key_id": "synthetic-key-v1"},
        "records": id_records,
    }
    manifest = copy.deepcopy(base_manifest)
    manifest["outputs"] = {
        "corpus_sha256": serialized_document_sha256(corpus),
        "id_map_sha256": serialized_document_sha256(id_map),
    }
    manifest["counts"].update(
        {
            "source_row_count": PATIENT_COUNT * 23,
            "patient_count": PATIENT_COUNT,
            "criterion_count": 23,
            "evidence_chunk_count": PATIENT_COUNT,
            "answered_label_count": PATIENT_COUNT * 22,
            "not_specified_label_count": PATIENT_COUNT // 2,
            "source_anomaly_label_count": PATIENT_COUNT // 2,
            "index_date_unavailable_patient_count": PATIENT_COUNT,
        }
    )
    manifest["manifest_sha256"] = _self_hash(manifest, "manifest_sha256")
    benchmark, benchmark_manifest = build_apixaban_benchmark(
        corpus,
        manifest,
        generated_at="2026-08-23T00:00:00Z",
        code_commit="1" * 40,
        required_source_sha256=None,
        required_counts=COUNTS,
    )
    quality, _ = build_apixaban_quality_reports(
        benchmark,
        benchmark_manifest,
        minimum_cell_size=2,
        generated_at="2026-08-23T00:01:00Z",
        code_commit="2" * 40,
        required_source_sha256=None,
        required_counts=COUNTS,
    )
    candidate = build_apixaban_split_candidate(
        benchmark,
        benchmark_manifest,
        corpus,
        manifest,
        id_map,
        quality,
        fractions={"train": 0.8, "validation": 0.1, "test": 0.1},
        seed=17,
        semantic_similarity_threshold=0.95,
        generated_at="2026-08-23T00:02:00Z",
        code_commit="3" * 40,
        generation_command="synthetic silver audit split",
        required_source_sha256=None,
        required_counts=COUNTS,
    )
    view = split_manifest_view(candidate)
    sizes = [
        len(view.splits[name].entity_ids["patient"])
        for name in ("train", "validation", "test")
    ]
    scan = build_semantic_scan_summary(
        manifest=view,
        dimension="patient",
        pairs=(),
        embedding_model_id="synthetic-encoder",
        embedding_model_revision="synthetic-v1",
        pooling="mean",
        vectors_normalized=True,
        search_method="exhaustive_cosine",
        candidate_pairs_evaluated=sizes[0] * sizes[1]
        + sizes[0] * sizes[2]
        + sizes[1] * sizes[2],
    )
    frozen = freeze_apixaban_split(candidate, scan, "Synthetic silver audit freeze")
    reservation = build_apixaban_calibration_reservation(
        frozen,
        calibration_patient_count=1,
        generated_at="2026-08-23T00:03:00Z",
        code_commit="4" * 40,
        generation_command="synthetic silver audit calibration",
    )
    return corpus, benchmark, frozen, reservation


def _input_plan(corpus, reservation):
    patients = {item["patient_id"]: item for item in corpus["patients"]}
    document = {
        "input_plan_version": "1.0.0",
        "input_policy_id": "synthetic-all-chunks-v1",
        "input_policy_sha256": "pending",
        "prompt_version": "synthetic-audit-prompt-v1",
        "system_instruction": "Return one grounded typed answer from synthetic evidence.",
        "rows": [
            {
                "patient_id": patient_id,
                "question_id": question["question_id"],
                "evidence_ids": [
                    item["evidence_id"] for item in patients[patient_id]["evidence"]
                ],
            }
            for patient_id in reservation["partitions"]["train_fit"]["patient_ids"]
            for question in load_question_catalog()["questions"]
        ],
    }
    document["rows"].sort(key=lambda row: (row["patient_id"], row["question_id"]))
    document["input_policy_sha256"] = _self_hash(document, "input_policy_sha256")
    return document


def _candidate(benchmark, reservation, source="D", include=None):
    questions = question_index()
    train_fit = set(reservation["partitions"]["train_fit"]["patient_ids"])
    rows = []
    for assessment in benchmark["assessments"]:
        key = (assessment["patient_id"], assessment["question_id"])
        if assessment["patient_id"] not in train_fit or (
            include is not None and key not in include
        ):
            continue
        question = questions[assessment["question_id"]]
        if assessment["fact_status"] == "unknown" or known_fact_allows_empty_evidence(
            question, assessment
        ):
            continue
        token = assessment["patient_id"].removeprefix("patient-")
        rows.append(
            {
                "patient_id": assessment["patient_id"],
                "question_id": assessment["question_id"],
                "question_type": assessment["question_type"],
                "fact_status": assessment["fact_status"],
                "value": assessment["value"],
                "unit": assessment["unit"],
                "evidence_ids": [f"evidence-{token}-001"],
                "provenance_ids": [f"synthetic-{source.lower()}-provenance"],
            }
        )
    document = {
        "apixaban_silver_candidate_version": "1.0.0",
        "artifact_sha256": "pending",
        "source": source,
        "source_artifact_sha256": ("d" if source == "D" else "e") * 64,
        "generation_counts": {
            "proposed_count": len(rows),
            "accepted_candidate_count": len(rows),
            "typed_disagreement_count": 0,
            "missing_evidence_count": 0,
            "invalid_ownership_count": 0,
            "student_invisibility_count": 0,
        },
        "rows": rows,
    }
    document["artifact_sha256"] = _self_hash(document, "artifact_sha256")
    return document


def _complete(pending, package, default="support"):
    filled = copy.deepcopy(pending)
    for row in filled["rows"]:
        row["judgment"] = default
        row["zero_tolerance_reconfirmed"] = {
            "cross_patient_citation": False,
            "student_invisible_citation": False,
        }
    return finalize_silver_judgments(
        filled, package, completed_at="2026-08-23T01:00:00Z"
    )


class ApixabanSilverAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus, cls.benchmark, cls.split, cls.reservation = _frozen_inputs()
        cls.input_plan = _input_plan(cls.corpus, cls.reservation)
        cls.d_candidate = _candidate(cls.benchmark, cls.reservation)

    def _package(self, candidate=None):
        return build_silver_audit_package(
            self.corpus,
            self.benchmark,
            self.split,
            self.reservation,
            self.input_plan,
            candidate or self.d_candidate,
            generated_at="2026-08-23T00:10:00Z",
            code_commit="5" * 40,
            generation_command="synthetic silver audit package",
        )

    def test_sampling_is_hash_bound_stratified_and_deterministic(self):
        first, pending = self._package()
        second, _ = self._package()
        self.assertEqual(first, second)
        self.assertGreater(first["population"]["candidate_count"], 100)
        self.assertEqual(100, first["population"]["sample_count"])
        self.assertFalse(first["population"]["all_candidates_reviewed"])
        self.assertEqual(100, sum(row["allocated_count"] for row in first["strata"]))
        self.assertTrue(
            all(
                row["allocated_count"] <= row["candidate_count"]
                for row in first["strata"]
            )
        )
        self.assertEqual(
            [row["sampling_digest"] for row in first["rows"]],
            sorted(row["sampling_digest"] for row in first["rows"]),
        )
        self.assertEqual(
            first["rows"][0]["sampling_digest"],
            sampling_digest(
                self.d_candidate["artifact_sha256"],
                first["rows"][0]["patient_id"],
                first["rows"][0]["question_id"],
            ),
        )
        self.assertTrue(all(row["judgment"] is None for row in pending["rows"]))
        with tempfile.TemporaryDirectory() as directory:
            paths = write_silver_audit_package(
                first, pending, Path(directory) / "audit"
            )
            self.assertTrue(
                all((os.stat(path).st_mode & 0o777) == 0o600 for path in paths)
            )
            with self.assertRaises(FileExistsError):
                write_silver_audit_package(first, pending, Path(directory) / "audit")

    def test_zero_tolerance_checks_cover_complete_candidate_set(self):
        invalid = copy.deepcopy(self.d_candidate)
        first, second = invalid["rows"][:2]
        if first["patient_id"] == second["patient_id"]:
            second = next(
                row
                for row in invalid["rows"]
                if row["patient_id"] != first["patient_id"]
            )
        first["evidence_ids"] = list(second["evidence_ids"])
        invalid["artifact_sha256"] = _self_hash(invalid, "artifact_sha256")
        with self.assertRaisesRegex(ApixabanSilverAuditError, "cross_patient_citation"):
            self._package(invalid)

    def test_judgments_are_complete_hash_bound_and_immutable(self):
        package, pending = self._package()
        completed = _complete(pending, package)
        validate_silver_judgments(completed, package)
        tampered = copy.deepcopy(completed)
        tampered["rows"][0]["source_question"] = "Changed after review"
        tampered["judgment_sha256"] = _self_hash(tampered, "judgment_sha256")
        with self.assertRaisesRegex(ApixabanSilverAuditError, "immutable"):
            validate_silver_judgments(tampered, package)

        altered_package = copy.deepcopy(package)
        altered_package["rows"][0]["cited_evidence"][0][
            "text"
        ] = "Synthetic text altered after package construction."
        altered_package["package_sha256"] = _self_hash(
            altered_package, "package_sha256"
        )
        altered_pending = copy.deepcopy(pending)
        altered_pending["audit_package_sha256"] = altered_package["package_sha256"]
        altered_pending["rows"][0]["cited_evidence"] = copy.deepcopy(
            altered_package["rows"][0]["cited_evidence"]
        )
        altered_judgments = _complete(altered_pending, altered_package)
        with self.assertRaisesRegex(ApixabanSilverAuditError, "citation text differs"):
            build_silver_quality_gate(
                self.corpus,
                self.benchmark,
                self.split,
                self.reservation,
                self.input_plan,
                self.d_candidate,
                altered_package,
                altered_judgments,
                code_commit="8" * 40,
                generation_command="synthetic altered package test",
            )

        incomplete = copy.deepcopy(pending)
        for row in incomplete["rows"]:
            row["zero_tolerance_reconfirmed"] = {
                "cross_patient_citation": False,
                "student_invisible_citation": False,
            }
        incomplete["rows"][0]["judgment"] = "support"
        with self.assertRaisesRegex(ApixabanSilverAuditError, "requires one judgment"):
            finalize_silver_judgments(incomplete, package)

    def test_gate_passes_and_removes_reviewed_failures_before_recount(self):
        package, pending = self._package()
        filled = copy.deepcopy(pending)
        for index, row in enumerate(filled["rows"]):
            row["judgment"] = "ambiguous" if index == 0 else "support"
            row["zero_tolerance_reconfirmed"] = {
                "cross_patient_citation": False,
                "student_invisible_citation": False,
            }
        judgments = finalize_silver_judgments(
            filled, package, completed_at="2026-08-23T01:00:00Z"
        )
        report, accepted_d, accepted_e = build_silver_quality_gate(
            self.corpus,
            self.benchmark,
            self.split,
            self.reservation,
            self.input_plan,
            self.d_candidate,
            package,
            judgments,
            generated_at="2026-08-23T01:01:00Z",
            code_commit="6" * 40,
            generation_command="synthetic silver quality gate",
        )
        self.assertEqual("passed_predeclared_thresholds", report["status"])
        self.assertIsNotNone(accepted_d)
        self.assertIsNone(accepted_e)
        self.assertEqual(len(self.d_candidate["rows"]) - 1, len(accepted_d["rows"]))
        self.assertEqual(
            report["quality_audit_sha256"], accepted_d["quality_audit_sha256"]
        )
        rejected_key = (
            filled["rows"][0]["patient_id"],
            filled["rows"][0]["question_id"],
        )
        self.assertNotIn(
            rejected_key,
            {(row["patient_id"], row["question_id"]) for row in accepted_d["rows"]},
        )

    def test_source_below_ninety_percent_fails_closed(self):
        package, pending = self._package()
        filled = copy.deepcopy(pending)
        for index, row in enumerate(filled["rows"]):
            row["judgment"] = "not_support" if index < 11 else "support"
            row["zero_tolerance_reconfirmed"] = {
                "cross_patient_citation": False,
                "student_invisible_citation": False,
            }
        judgments = finalize_silver_judgments(
            filled, package, completed_at="2026-08-23T01:00:00Z"
        )
        report, accepted_d, accepted_e = build_silver_quality_gate(
            self.corpus,
            self.benchmark,
            self.split,
            self.reservation,
            self.input_plan,
            self.d_candidate,
            package,
            judgments,
            code_commit="7" * 40,
            generation_command="synthetic failing silver gate",
        )
        self.assertEqual("failed_source_quality", report["status"])
        self.assertIsNone(accepted_d)
        self.assertIsNone(accepted_e)

    def test_teacher_backoff_is_audited_and_only_fills_uncovered_rows(self):
        by_question = {}
        for row in self.d_candidate["rows"]:
            by_question.setdefault(row["question_id"], []).append(
                (row["patient_id"], row["question_id"])
            )
        d_keys = {
            key
            for question_keys in by_question.values()
            for key in sorted(question_keys)[:3]
        }
        all_keys = {
            (row["patient_id"], row["question_id"]) for row in self.d_candidate["rows"]
        }
        d_candidate = _candidate(
            self.benchmark, self.reservation, source="D", include=d_keys
        )
        e_candidate = _candidate(
            self.benchmark,
            self.reservation,
            source="E",
            include=all_keys - d_keys,
        )
        d_package, d_pending = self._package(d_candidate)
        e_package, e_pending = self._package(e_candidate)
        d_judgments = _complete(d_pending, d_package)
        e_judgments = _complete(e_pending, e_package)

        d_report, d_output, _ = build_silver_quality_gate(
            self.corpus,
            self.benchmark,
            self.split,
            self.reservation,
            self.input_plan,
            d_candidate,
            d_package,
            d_judgments,
            generated_at="2026-08-23T01:02:00Z",
            code_commit="9" * 40,
            generation_command="synthetic D-only gate",
        )
        self.assertEqual("needs_e_backoff", d_report["status"])
        self.assertIsNone(d_output)

        report, accepted_d, accepted_e = build_silver_quality_gate(
            self.corpus,
            self.benchmark,
            self.split,
            self.reservation,
            self.input_plan,
            d_candidate,
            d_package,
            d_judgments,
            (e_candidate, e_package, e_judgments),
            generated_at="2026-08-23T01:03:00Z",
            code_commit="a" * 40,
            generation_command="synthetic D plus E gate",
        )
        self.assertEqual("passed_predeclared_thresholds", report["status"])
        self.assertEqual(len(d_candidate["rows"]), len(accepted_d["rows"]))
        self.assertEqual(len(e_candidate["rows"]), len(accepted_e["rows"]))
        self.assertFalse(
            {(row["patient_id"], row["question_id"]) for row in accepted_d["rows"]}
            & {(row["patient_id"], row["question_id"]) for row in accepted_e["rows"]}
        )

    def test_cli_requires_restricted_data_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "explicit acknowledgement"):
            main(
                [
                    "package",
                    "--staging-corpus",
                    "/restricted/staging.json",
                    "--benchmark",
                    "/restricted/benchmark.json",
                    "--frozen-split",
                    "/restricted/split.json",
                    "--calibration-reservation",
                    "/restricted/calibration.json",
                    "--input-plan",
                    "/restricted/input-plan.json",
                    "--candidate",
                    "/restricted/candidate.json",
                    "--output-dir",
                    "/restricted/audit",
                ]
            )


if __name__ == "__main__":
    unittest.main()
