import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_calibration import (
    build_apixaban_calibration_reservation,
)
from clinical_matcher.apixaban_contract import (
    known_fact_allows_empty_evidence,
    load_question_catalog,
    question_index,
)
from clinical_matcher.apixaban_sft import (
    ApixabanSFTError,
    build_apixaban_sft_export,
    validate_apixaban_sft_export_manifest,
    write_apixaban_sft_export,
)
from clinical_matcher.apixaban_sft_contract import (
    load_apixaban_sft_length_contract,
)
from clinical_matcher.apixaban_sft_cli import main
from clinical_matcher.apixaban_split import (
    freeze_apixaban_split,
    split_manifest_view,
)
from clinical_matcher.semantic_audit import build_semantic_scan_summary
from clinical_matcher.splits import canonical_sha256
from tests.test_apixaban_split import build_candidate


def _self_hash(document, field):
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


class FakeTokenizer:
    def __init__(self, target_tokens=None):
        self.target_tokens = target_tokens

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        prompt_tokens = 11 + sum(
            max(1, len(message["content"]) // 4) for message in messages[:2]
        )
        if len(messages) == 2:
            count = prompt_tokens
        else:
            target = self.target_tokens
            if target is None:
                target = max(1, len(messages[2]["content"]) // 4)
            count = prompt_tokens + target
        return list(range(count))


def _frozen_inputs():
    candidate, inputs = build_candidate()
    view = split_manifest_view(candidate)
    sizes = [
        len(view.splits[name].entity_ids["patient"])
        for name in ("train", "validation", "test")
    ]
    summary = build_semantic_scan_summary(
        manifest=view,
        dimension="patient",
        pairs=(),
        embedding_model_id="synthetic-encoder",
        embedding_model_revision="synthetic-v1",
        pooling="mean",
        vectors_normalized=True,
        search_method="exhaustive_cosine",
        candidate_pairs_evaluated=(
            sizes[0] * sizes[1]
            + sizes[0] * sizes[2]
            + sizes[1] * sizes[2]
        ),
    )
    frozen = freeze_apixaban_split(candidate, summary, "Synthetic SFT test")
    benchmark, _, corpus, *_ = inputs
    reservation = build_apixaban_calibration_reservation(
        frozen,
        calibration_patient_count=1,
        generated_at="2026-08-22T00:00:00Z",
        code_commit="a" * 40,
        generation_command="synthetic SFT calibration test",
    )
    return corpus, benchmark, frozen, reservation


def _input_plan(corpus, reservation):
    catalog = load_question_catalog()
    contract = load_apixaban_sft_length_contract()
    train_fit = reservation["partitions"]["train_fit"]["patient_ids"]
    patients = {item["patient_id"]: item for item in corpus["patients"]}
    document = {
        "input_plan_version": "1.1.0",
        "input_policy_id": contract["input_policy"]["input_policy_id"],
        "input_policy_sha256": "pending",
        "prompt_version": contract["prompt"]["prompt_version"],
        "system_instruction": contract["prompt"]["system_instruction"],
        "context": {
            "length_report_sha256": "f" * 64,
            "model_id": contract["model"]["model_id"],
            "model_revision": contract["model"]["revision"],
            "tokenizer_sha256": contract["tokenizer"]["files"]["tokenizer.json"],
            "tokenizer_config_sha256": contract["tokenizer"]["files"][
                "tokenizer_config.json"
            ],
            "chat_template_sha256": contract["tokenizer"]["chat_template_sha256"],
            "output_reserve_tokens": 512,
            "max_seq_len": 2048,
            "overflow_policy": "measured_failure_abstention_no_truncation",
        },
        "rows": [
            {
                "patient_id": patient_id,
                "question_id": question["question_id"],
                "evidence_ids": [
                    evidence["evidence_id"]
                    for evidence in patients[patient_id]["evidence"]
                ],
            }
            for patient_id in train_fit
            for question in catalog["questions"]
        ],
    }
    document["rows"].sort(
        key=lambda item: (item["patient_id"], item["question_id"])
    )
    document["input_policy_sha256"] = _self_hash(
        document, "input_policy_sha256"
    )
    return document


def _accepted_silver(benchmark, reservation, source="D", skip=()):
    catalog = load_question_catalog()
    questions = question_index(catalog)
    train_fit = set(
        reservation["partitions"]["train_fit"]["patient_ids"]
    )
    rows = []
    for assessment in benchmark["assessments"]:
        key = (assessment["patient_id"], assessment["question_id"])
        if assessment["patient_id"] not in train_fit or key in skip:
            continue
        question = questions[assessment["question_id"]]
        if assessment["fact_status"] == "unknown" or (
            known_fact_allows_empty_evidence(question, assessment)
        ):
            continue
        token = assessment["patient_id"].removeprefix("patient-")
        rows.append(
            {
                "patient_id": assessment["patient_id"],
                "question_id": assessment["question_id"],
                "fact_status": assessment["fact_status"],
                "value": assessment["value"],
                "unit": assessment["unit"],
                "evidence_ids": [f"evidence-{token}-001"],
            }
        )
    document = {
        "accepted_silver_version": "1.0.0",
        "artifact_sha256": "pending",
        "source": source,
        "source_artifact_sha256": ("d" if source == "D" else "e") * 64,
        "quality_audit_sha256": ("1" if source == "D" else "2") * 64,
        "audit_status": "passed_predeclared_thresholds",
        "rows": rows,
    }
    document["artifact_sha256"] = _self_hash(document, "artifact_sha256")
    return document


def _build_export(with_e=True, leave_uncovered=True):
    corpus, benchmark, split, reservation = _frozen_inputs()
    input_plan = _input_plan(corpus, reservation)
    citation_required = [
        (row["patient_id"], row["question_id"])
        for row in _accepted_silver(benchmark, reservation)["rows"]
    ]
    e_key = citation_required[0]
    uncovered_key = citation_required[1]
    skipped = {e_key}
    if leave_uncovered:
        skipped.add(uncovered_key)
    d_silver = _accepted_silver(benchmark, reservation, skip=skipped)
    e_silver = None
    if with_e:
        e_document = _accepted_silver(
            benchmark,
            reservation,
            source="E",
            skip=set(citation_required) - {e_key},
        )
        e_silver = e_document
    result = build_apixaban_sft_export(
        corpus,
        benchmark,
        split,
        reservation,
        input_plan,
        d_silver,
        e_silver,
        tokenizer=FakeTokenizer(),
        generated_at="2026-08-22T01:00:00Z",
        code_commit="b" * 40,
        generation_command="synthetic SFT export test",
    )
    return result, (corpus, benchmark, split, reservation, input_plan, d_silver)


class ApixabanSFTExportTests(unittest.TestCase):
    def test_builds_one_canonical_source_and_two_consistent_formats(self):
        (records, mlx_rows, medicalgpt_rows, manifest), inputs = _build_export()
        _, _, split, reservation, _, _ = inputs
        train_fit = set(
            reservation["partitions"]["train_fit"]["patient_ids"]
        )
        held_out = set(
            reservation["partitions"]["calibration_only"]["patient_ids"]
        ) | set(split["splits"]["validation"]["patient_ids"]) | set(
            split["splits"]["test"]["patient_ids"]
        )

        self.assertTrue(records)
        self.assertTrue(
            all(record["patient_id"] in train_fit for record in records)
        )
        self.assertTrue(
            {record["patient_id"] for record in records}.isdisjoint(held_out)
        )
        self.assertEqual(len(records), len(mlx_rows))
        self.assertEqual(len(records), len(medicalgpt_rows))
        for mlx, medicalgpt in zip(mlx_rows, medicalgpt_rows):
            self.assertEqual(
                ["system", "user", "assistant"],
                [message["role"] for message in mlx["messages"]],
            )
            self.assertEqual(
                ["system", "human", "gpt"],
                [message["from"] for message in medicalgpt["conversations"]],
            )
            self.assertEqual(
                [message["content"] for message in mlx["messages"]],
                [message["value"] for message in medicalgpt["conversations"]],
            )

        counts = manifest["counts"]
        self.assertEqual(46, counts["eligible_pair_count"])
        self.assertEqual(
            46,
            counts["included_row_count"]
            + counts["excluded_known_without_silver_count"],
        )
        self.assertEqual(1, counts["accepted_e_count"])
        self.assertEqual(1, counts["excluded_known_without_silver_count"])
        self.assertGreater(counts["unknown_empty_evidence_count"], 0)
        self.assertGreater(counts["default_absent_exception_count"], 0)
        self.assertFalse(manifest["restrictions"]["online_upload_permitted"])
        self.assertFalse(manifest["restrictions"]["silver_is_evidence_gold"])
        validate_apixaban_sft_export_manifest(manifest)

    def test_unknown_and_default_absent_rows_have_legal_empty_citations(self):
        (records, *_), _ = _build_export()
        empty_policies = {
            "gold_unknown_empty_evidence",
            "source_default_absent_empty_evidence",
        }
        selected = [
            row for row in records if row["supervision"]["row_policy"] in empty_policies
        ]
        self.assertTrue(selected)
        for row in selected:
            self.assertEqual([], row["target"]["evidence_ids"])
            self.assertIsNone(row["supervision"]["silver_source"])

    def test_export_rejects_target_longer_than_frozen_reserve(self):
        _, inputs = _build_export(with_e=False, leave_uncovered=False)
        corpus, benchmark, split, reservation, input_plan, d_silver = inputs
        with self.assertRaisesRegex(
            ValueError, "target exceeds the frozen output reserve"
        ):
            build_apixaban_sft_export(
                corpus,
                benchmark,
                split,
                reservation,
                input_plan,
                d_silver,
                tokenizer=FakeTokenizer(target_tokens=513),
                generation_command="synthetic target overflow test",
            )

    def test_accepted_silver_must_be_visible_and_match_gold(self):
        _, inputs = _build_export(with_e=False, leave_uncovered=False)
        corpus, benchmark, split, reservation, input_plan, d_silver = inputs
        cited = d_silver["rows"][0]
        plan = copy.deepcopy(input_plan)
        key = (cited["patient_id"], cited["question_id"])
        row = next(
            item
            for item in plan["rows"]
            if (item["patient_id"], item["question_id"]) == key
        )
        other = next(
            item
            for item in corpus["patients"]
            if item["patient_id"] != cited["patient_id"]
        )
        row["evidence_ids"] = [other["evidence"][0]["evidence_id"]]
        plan["input_policy_sha256"] = _self_hash(plan, "input_policy_sha256")
        with self.assertRaisesRegex(
            ApixabanSFTError, "every complete patient chunk"
        ):
            build_apixaban_sft_export(
                corpus,
                benchmark,
                split,
                reservation,
                plan,
                d_silver,
                tokenizer=FakeTokenizer(),
                generation_command="synthetic invalid visibility test",
            )

        wrong = copy.deepcopy(d_silver)
        wrong["rows"][0]["value"] = not wrong["rows"][0]["value"]
        wrong["artifact_sha256"] = _self_hash(wrong, "artifact_sha256")
        with self.assertRaisesRegex(ApixabanSFTError, "does not equal"):
            build_apixaban_sft_export(
                corpus,
                benchmark,
                split,
                reservation,
                input_plan,
                wrong,
                tokenizer=FakeTokenizer(),
                generation_command="synthetic invalid typed silver test",
            )

    def test_teacher_backoff_cannot_overlap_rule_silver(self):
        _, inputs = _build_export(with_e=False, leave_uncovered=False)
        corpus, benchmark, split, reservation, input_plan, d_silver = inputs
        overlap_key = (
            d_silver["rows"][0]["patient_id"],
            d_silver["rows"][0]["question_id"],
        )
        all_keys = {
            (row["patient_id"], row["question_id"])
            for row in _accepted_silver(benchmark, reservation, source="E")["rows"]
        }
        e_silver = _accepted_silver(
            benchmark,
            reservation,
            source="E",
            skip=all_keys - {overlap_key},
        )
        with self.assertRaisesRegex(ApixabanSFTError, "must not overlap"):
            build_apixaban_sft_export(
                corpus,
                benchmark,
                split,
                reservation,
                input_plan,
                d_silver,
                e_silver,
                tokenizer=FakeTokenizer(),
                generation_command="synthetic overlap test",
            )

    def test_writer_is_owner_only_hash_bound_and_refuses_overwrite(self):
        result, _ = _build_export()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sft"
            paths = write_apixaban_sft_export(*result, root)
            self.assertEqual(4, len(paths))
            self.assertTrue(
                all((os.stat(path).st_mode & 0o777) == 0o600 for path in paths)
            )
            manifest = result[-1]
            for filename, key in (
                ("canonical.jsonl", "canonical_jsonl_sha256"),
                ("train.jsonl", "mlx_jsonl_sha256"),
                ("medicalgpt.jsonl", "medicalgpt_jsonl_sha256"),
            ):
                payload = (root / filename).read_bytes()
                self.assertEqual(
                    manifest["outputs"][key], hashlib.sha256(payload).hexdigest()
                )
            with self.assertRaises(FileExistsError):
                write_apixaban_sft_export(*result, root)

    def test_input_plan_and_manifest_tampering_fail_closed(self):
        result, inputs = _build_export()
        corpus, benchmark, split, reservation, input_plan, d_silver = inputs
        tampered_plan = copy.deepcopy(input_plan)
        tampered_plan["rows"].pop()
        tampered_plan["input_policy_sha256"] = _self_hash(
            tampered_plan, "input_policy_sha256"
        )
        with self.assertRaisesRegex(ApixabanSFTError, "exactly the train-fit"):
            build_apixaban_sft_export(
                corpus,
                benchmark,
                split,
                reservation,
                tampered_plan,
                d_silver,
                tokenizer=FakeTokenizer(),
                generation_command="synthetic incomplete plan test",
            )
        manifest = copy.deepcopy(result[-1])
        manifest["counts"]["included_row_count"] += 1
        manifest["manifest_sha256"] = _self_hash(manifest, "manifest_sha256")
        with self.assertRaisesRegex(
            ApixabanSFTError, "differs from included rows|do not reconcile"
        ):
            validate_apixaban_sft_export_manifest(manifest)

        duplicated_question = copy.deepcopy(result[-1])
        duplicated_question["per_question"][1]["question_id"] = (
            duplicated_question["per_question"][0]["question_id"]
        )
        duplicated_question["manifest_sha256"] = _self_hash(
            duplicated_question, "manifest_sha256"
        )
        with self.assertRaisesRegex(ApixabanSFTError, "frozen catalog once"):
            validate_apixaban_sft_export_manifest(duplicated_question)

        records, mlx_rows, medicalgpt_rows, valid_manifest = result
        duplicate_records = list(records) + [copy.deepcopy(records[0])]
        duplicate_mlx = list(mlx_rows) + [copy.deepcopy(mlx_rows[0])]
        duplicate_medicalgpt = list(medicalgpt_rows) + [
            copy.deepcopy(medicalgpt_rows[0])
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ApixabanSFTError, "must be unique"):
                write_apixaban_sft_export(
                    duplicate_records,
                    duplicate_mlx,
                    duplicate_medicalgpt,
                    valid_manifest,
                    Path(directory) / "sft",
                )

    def test_cli_requires_restricted_data_acknowledgement(self):
        with self.assertRaisesRegex(ValueError, "explicit acknowledgement"):
            main(
                [
                    "--staging-corpus", "/restricted/staging.json",
                    "--benchmark", "/restricted/benchmark.json",
                    "--frozen-split", "/restricted/split.json",
                    "--calibration-reservation", "/restricted/calibration.json",
                    "--input-plan", "/restricted/input-plan.json",
                    "--accepted-d-silver", "/restricted/d-silver.json",
                    "--tokenizer-directory", "/restricted/tokenizer",
                    "--output-dir", "/restricted/sft",
                ]
            )


if __name__ == "__main__":
    unittest.main()
