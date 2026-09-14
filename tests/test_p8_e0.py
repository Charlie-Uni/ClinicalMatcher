import copy
import json
import tempfile
import unittest
from itertools import product
from pathlib import Path
from unittest.mock import patch

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.apixaban_evaluation import records_from_documents, mixed_fact_metrics, exact_source_tolerance_policy
from clinical_matcher.p8_e0 import (
    CANDIDATES, arbitrate_row, build_arbitrated_predictions, evaluate_rows,
    freeze_e0_run, indexed_grid, policy_contract, project_safety_rows,
    run_e0, select_raw_winner, validate_arbitrated_predictions, compare_to_incumbent,
)
from clinical_matcher.p8_safety import EventLedger, P8Error, make_pin, seal
from tests.test_apixaban_neurosymbolic_audit import prediction_set, PATIENT_ID, EVIDENCE_ID
from tests.test_p8_safety import access_fixture


def row(question, value, citations=True):
    status = "unknown" if value is None else ("absent" if value is False else "present")
    return {"patient_id": PATIENT_ID, "question_id": question["question_id"],
            "question_type": question["question_type"], "fact_status": status,
            "value": value, "unit": None, "abstained": value is None,
            "abstention_reason": "synthetic_missing" if value is None else None,
            "evidence_ids": [EVIDENCE_ID] if citations else [], "trace_ids": ["synthetic.model"]}


class P8E0TruthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()
        cls.boolean = next(q for q in cls.catalog["questions"] if q["source_criterion_label"] == "afib")
        cls.numeric = next(q for q in cls.catalog["questions"] if q["question_type"] == "numeric")
        cls.med = next(q for q in cls.catalog["questions"] if q["source_criterion_label"] == "med_decisions")

    def test_truth_table_including_unusable_rule_and_aliases(self):
        cases = 0
        for question, values in [(self.boolean, [True, False, None]), (self.med, [True, False, None]),
                                  (self.numeric, [12, 24, None])]:
            for left_value, right_value, cited in product(values, values, [False, True]):
                left, right = row(question, left_value, False), row(question, right_value, cited)
                available = right_value is not None and (cited or (question == self.med and right_value is False))
                results = {}
                for policy in ["A1", "A2", "A3", "A4"]:
                    actual, source, _ = arbitrate_row(left, right, question=question,
                                                     evidence_ids={EVIDENCE_ID}, policy_id=policy)
                    expected_rule = available and (left_value is None or (policy != "A1" and question == self.numeric))
                    self.assertEqual(right if expected_rule else left, actual)
                    self.assertEqual("rules" if expected_rule else "llm", source)
                    results[policy] = actual
                self.assertEqual(results["A2"], results["A3"])
                self.assertEqual(results["A3"], results["A4"])
                cases += 1
        self.assertEqual(54, cases)

    def test_raw_llm_unsupported_answer_retained_until_safety_projection(self):
        left, right = row(self.boolean, True, False), row(self.boolean, False)
        result, source, _ = arbitrate_row(left, right, question=self.boolean,
                                         evidence_ids={EVIDENCE_ID}, policy_id="A4")
        self.assertEqual("llm", source)
        self.assertEqual("present", result["fact_status"])
        safety = project_safety_rows([result], {PATIENT_ID: {EVIDENCE_ID}})
        self.assertEqual("unknown", safety[0]["fact_status"])
        self.assertEqual("present", result["fact_status"])

    def test_wrong_patient_citation_not_usable_numeric_rule(self):
        left, right = row(self.numeric, 12), row(self.numeric, 24)
        right["evidence_ids"] = ["evidence-aaaaaaaaaaaaaaaaaaaaaaaa-000"]
        result, source, _ = arbitrate_row(left, right, question=self.numeric,
                                         evidence_ids={EVIDENCE_ID}, policy_id="A2")
        self.assertEqual(left, result)
        self.assertEqual("llm", source)

    def test_reject_cross_patient_illegal_numeric_and_undeclared_policy(self):
        for value in [False, float("inf"), float("nan")]:
            with self.subTest(value=value), self.assertRaises(P8Error):
                arbitrate_row(row(self.numeric, value), row(self.numeric, 12), question=self.numeric,
                              evidence_ids={EVIDENCE_ID}, policy_id="A1")
        right = row(self.numeric, 12)
        right["patient_id"] = "patient-aaaaaaaaaaaaaaaaaaaaaaaa"
        with self.assertRaises(P8Error):
            arbitrate_row(row(self.numeric, 12), right, question=self.numeric, evidence_ids=set(), policy_id="A1")
        with self.assertRaises(P8Error):
            arbitrate_row(row(self.numeric, 12), row(self.numeric, 12), question=self.numeric,
                          evidence_ids=set(), policy_id="A5")

    def test_complete_grid_and_existing_p1_5_kernel_equivalence(self):
        predictions = prediction_set(self.catalog)
        gold = copy.deepcopy(predictions["predictions"])
        changed = predictions["predictions"][0]
        changed.update(fact_status="unknown", value=None, abstained=True, abstention_reason="synthetic_missing")
        records = records_from_documents({"assessments": gold}, {"splits": {"validation": {"patient_ids": [PATIENT_ID]}}},
                                         predictions, exact_source_tolerance_policy(), "validation")
        actual = evaluate_rows(predictions["predictions"], gold, [PATIENT_ID], bootstrap=False)
        self.assertEqual(mixed_fact_metrics(records), actual["metrics"])
        for bad in [gold[:-1], gold + [gold[0]]]:
            with self.assertRaises(P8Error):
                indexed_grid(bad, [PATIENT_ID])

    def test_provenance_reproduces_and_tampering_fails_even_when_resealed(self):
        llm = prediction_set(self.catalog)
        rules = copy.deepcopy(llm)
        evidence = {PATIENT_ID: {EVIDENCE_ID}}
        result = build_arbitrated_predictions(llm, rules, policy_id="A4", evidence=evidence,
                                               patient_ids=[PATIENT_ID], llm_arm="long_context")
        validate_arbitrated_predictions(result, llm=llm, rules=rules, evidence=evidence, patient_ids=[PATIENT_ID])
        result["provenance"][0]["source_arm"] = "structured"
        with self.assertRaises(P8Error):
            validate_arbitrated_predictions(seal(result), llm=llm, rules=rules, evidence=evidence, patient_ids=[PATIENT_ID])

    def test_ties_keep_incumbent_then_frozen_candidate_order(self):
        scores = {key: .5 for key in ("long_context", *CANDIDATES)}
        self.assertEqual("long_context", select_raw_winner(scores))
        scores["long_context.A2"] = scores["long_context.A3"] = .6
        self.assertEqual("long_context.A2", select_raw_winner(scores))
        scores.pop("structured.A4")
        with self.assertRaises(P8Error):
            select_raw_winner(scores)

    def test_repaired_and_introduced_errors_are_both_reported(self):
        gold = prediction_set(self.catalog)["predictions"]
        before = copy.deepcopy(gold)
        before[0].update(fact_status="unknown", value=None, abstained=True, abstention_reason="synthetic_missing")
        after = copy.deepcopy(gold)
        numeric = next(item for item in after if item["question_type"] == "numeric")
        numeric["value"] = 24
        self.assertEqual({"corrected_count": 1, "introduced_error_count": 1, "decision_changed_count": 2},
                         compare_to_incumbent(after, before, gold, [PATIENT_ID]))


class P8E0IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.manifest = access_fixture(self.root)
        self.catalog = load_question_catalog()
        self.ids = self.manifest["populations"]["validation"]
        evidence = [{"patient_id": pid, "evidence": [{"evidence_id": f"evidence-{pid[8:]}-000", "text": "Synthetic evidence."}]} for pid in self.ids]
        configs = policy_contract()["raw_configuration_pins"]
        self.inputs = {}
        for arm in ["rules", "structured", "long_context"]:
            source = prediction_set(self.catalog)
            source["split_manifest_sha256"] = self.manifest["source_split_pin"]["value"]
            source["inference_config_sha256"] = configs[arm]["value"]
            source["predictions"] = []
            for pid in self.ids:
                for question in self.catalog["questions"]:
                    answer = row(question, True if question["question_type"] == "boolean" else 12)
                    answer["patient_id"] = pid
                    answer["evidence_ids"] = [f"evidence-{pid[8:]}-000"]
                    source["predictions"].append(answer)
            if arm == "rules":
                source["prediction_set_version"] = "1.1.0"
                source["rule_set_sha256"] = source.pop("inference_config_sha256")
                for answer in source["predictions"]:
                    answer["rule_ids"] = answer.pop("trace_ids")
            self.inputs[arm] = source
        self.gold = copy.deepcopy(self.inputs["rules"]["predictions"])
        for role, rows in [("gold", self.gold), ("evidence", evidence)]:
            self.inputs[role] = seal({"p8_partition_version": "1.0.0", "partition": "validation", "kind": role,
                                     "source_split_pin": self.manifest["source_split_pin"],
                                     "reservation_pin": self.manifest["reservation_pin"], "rows": rows})
        self.manifest["artifacts"] = []
        self.register()

    def register(self):
        self.manifest["artifacts"] = []
        for role, document in self.inputs.items():
            path = self.root / f"e0-{role}.json"
            payload = (json.dumps(document) + "\n").encode()
            path.write_bytes(payload)
            path.chmod(0o600)
            self.manifest["artifacts"].append({"artifact_id": role, "partition": "validation",
                                               "kind": "raw_predictions" if role in ["rules", "structured", "long_context"] else role,
                                               "scope": "preisolated_partition_only", "path": str(path),
                                               "file_pin": make_pin(payload, "file"), "content_pin": make_pin(document, "content")})
        self.manifest = seal(self.manifest)

    def test_end_to_end_has_eight_candidates_and_selects_before_projection(self):
        config = freeze_e0_run(self.manifest, {key: key for key in self.inputs}, synthetic=True)
        ledger = EventLedger(self.root / "events")
        with patch("clinical_matcher.p8_e0.mixed_fact_bootstrap", return_value={}), patch("clinical_matcher.apixaban_structured_llm.OllamaLoopbackClient.chat", side_effect=AssertionError):
            report = run_e0(config, self.manifest, output_root=self.root / "output", ledger=ledger,
                            attempt_id="synthetic-e0", synthetic=True)
        self.assertEqual(11, len(report["results"]))
        self.assertEqual("long_context", report["raw_winner"])
        self.assertEqual(0, report["inference_requests"])
        self.assertIsNone(report["new_model_latency"])
        self.assertEqual(["attempt_started", "raw_winner_frozen", "completed"], [e["event"] for e in ledger.read()])
        with self.assertRaises(P8Error):
            run_e0(config, self.manifest, output_root=self.root / "output", ledger=ledger,
                   attempt_id="duplicate", synthetic=True)

    def test_projected_input_fails_and_failed_attempt_remains(self):
        self.inputs["long_context"]["inference_config_sha256"] = "0" * 64
        self.register()
        config = freeze_e0_run(self.manifest, {key: key for key in self.inputs}, synthetic=True)
        ledger = EventLedger(self.root / "events")
        with self.assertRaises(P8Error):
            run_e0(config, self.manifest, output_root=self.root / "output", ledger=ledger,
                   attempt_id="synthetic-failure", synthetic=True)
        self.assertEqual(["attempt_started", "failed"], [e["event"] for e in ledger.read()])

    def test_symlink_output_parent_rejected_before_private_inputs(self):
        config = freeze_e0_run(self.manifest, {key: key for key in self.inputs}, synthetic=True)
        ledger = EventLedger(self.root / "events")
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with patch("clinical_matcher.p8_safety._read_private_bytes") as read, self.assertRaises(P8Error):
            run_e0(config, self.manifest, output_root=alias / "output", ledger=ledger,
                   attempt_id="synthetic-output-rejection", synthetic=True)
        read.assert_not_called()
