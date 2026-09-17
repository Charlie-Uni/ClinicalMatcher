import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p8_e1 import (
    RUN_PARAMETERS,
    build_e1_contract,
    load_ablation_matrix,
    run_e1,
    run_pilot,
    run_request,
    v2_dual_review_decision,
)
from clinical_matcher.p8_prompt import example_plan, review_proposal, select_examples
from clinical_matcher.p8_safety import P8Error, make_pin, seal
from tests.test_p8_safety import access_fixture


QUESTIONS = load_question_catalog()["questions"]


def _partition(manifest, partition, kind, rows):
    return seal({"p8_partition_version": "1.0.0", "partition": partition, "kind": kind,
                 "source_split_pin": manifest["source_split_pin"],
                 "reservation_pin": manifest["reservation_pin"], "rows": rows})


def _register(manifest, root, artifact_id, document):
    payload = (json.dumps(document, indent=2) + "\n").encode()
    path = root / f"{artifact_id}.json"
    path.write_bytes(payload)
    path.chmod(0o600)
    unsealed = {k: v for k, v in manifest.items() if k != "self_sha256"}
    unsealed["artifacts"] = manifest["artifacts"] + [{
        "artifact_id": artifact_id, "partition": document["partition"],
        "kind": document["kind"], "scope": "preisolated_partition_only",
        "path": str(path), "file_pin": make_pin(payload, "file"),
        "content_pin": make_pin(document, "content")}]
    return seal(unsealed)


def _unknown_grid(patient_ids):
    return [{"patient_id": pid, "question_id": q["question_id"],
             "question_type": q["question_type"], "fact_status": "unknown",
             "value": None, "unit": None, "evidence_ids": []}
            for pid in patient_ids for q in QUESTIONS]


def _answer_payload(question_ids):
    answers = []
    by_id = {q["question_id"]: q for q in QUESTIONS}
    for qid in question_ids:
        answers.append({"question_id": qid, "question_type": by_id[qid]["question_type"],
                        "supporting_quote": None, "fact_status": "unknown",
                        "value": None, "unit": None, "evidence_ids": []})
    return json.dumps({"assessments": answers})


class FakeClient:
    def __init__(self, *, fail_first=0, invalid_content=False, prompt_tokens=1200):
        self.calls, self.unloads = [], 0
        self.fail_first, self.invalid_content = fail_first, invalid_content
        self.prompt_tokens = prompt_tokens

    def chat(self, payload):
        if payload.get("messages") == []:
            self.unloads += 1
            return {"message": {"content": ""}}
        self.calls.append(copy.deepcopy(payload))
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError("synthetic transport failure")
        question_ids = [q["question_id"] for q in
                        json.loads(payload["messages"][1]["content"].split("\n", 1)[1])["questions"]]
        content = "not json" if self.invalid_content else _answer_payload(question_ids)
        return {"message": {"content": content}, "prompt_eval_count": self.prompt_tokens,
                "eval_count": 40, "load_duration": 5, "total_duration": 10}


class P8E1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        root.chmod(0o700)
        manifest = access_fixture(root)
        populations = manifest["populations"]
        self.validation_ids = sorted(populations["validation"])
        evidence_rows = [{"patient_id": pid, "evidence": [
            {"evidence_id": f"{pid}-e1", "text": "Synthetic note line one. Second line."}]}
            for pid in self.validation_ids]
        manifest = _register(manifest, root, "validation.evidence",
                             _partition(manifest, "validation", "evidence", evidence_rows))
        manifest = _register(manifest, root, "validation.gold",
                             _partition(manifest, "validation", "gold",
                                        _unknown_grid(self.validation_ids)))
        train_ids = sorted(populations["train_fit"])
        evidence = _partition(manifest, "train_fit", "evidence",
                              [{"patient_id": pid, "evidence": []} for pid in train_ids])
        gold = _partition(manifest, "train_fit", "gold", _unknown_grid(train_ids))
        manifest = _register(manifest, root, "train_fit.evidence", evidence)
        manifest = _register(manifest, root, "train_fit.gold", gold)
        self.manifest = manifest
        self.example_set = select_examples(evidence, gold)
        self.decision = v2_dual_review_decision("synthetic dual review record")
        self.contract = build_e1_contract(manifest, self.example_set, self.decision,
                                          mode="v2", synthetic=True,
                                          runtime_identity={"engine_version": "test"})
        self.patient = {"patient_id": self.validation_ids[0],
                        "evidence": [{"evidence_id": "p-e1", "text": "Synthetic note."}]}
        self.qid = QUESTIONS[0]["question_id"]

    def test_decision_and_plan_require_both_approvals(self):
        plan = example_plan(review_proposal(), self.manifest, self.decision, synthetic=True)
        self.assertFalse(plan["inference_authorized"])
        tampered = {k: v for k, v in self.decision.items() if k != "self_sha256"}
        tampered["owner_approved"] = False
        with self.assertRaises(P8Error):
            example_plan(review_proposal(), self.manifest, seal(tampered), synthetic=True)
        with self.assertRaises(P8Error):
            v2_dual_review_decision("  ")

    def test_contract_binds_ablation_matrix_and_rejects_foreign_decision(self):
        matrix = load_ablation_matrix()
        self.assertEqual({"F1", "F2", "F3", "F4"}, set(matrix["factors"]))
        foreign = {k: v for k, v in self.decision.items() if k != "self_sha256"}
        foreign["scope"] = "something_else"
        with self.assertRaises(P8Error):
            build_e1_contract(self.manifest, self.example_set, seal(foreign),
                              mode="v2", synthetic=True)

    def test_accepted_request_and_no_retry_on_model_content(self):
        client = FakeClient(invalid_content=True)
        result = run_request(client, self.contract, self.patient, [self.qid],
                             self.example_set, sleeper=lambda s: None)
        self.assertEqual("invalid_output", result["log"]["outcome"])
        self.assertEqual(1, len(client.calls))
        self.assertEqual(0, result["log"]["retries"])
        self.assertTrue(all(r["fact_status"] == "unknown" for r in result["rows"]))

    def test_transport_failure_retried_once_with_fixed_wait(self):
        waits = []
        client = FakeClient(fail_first=1)
        result = run_request(client, self.contract, self.patient, [self.qid],
                             self.example_set, sleeper=waits.append)
        self.assertEqual("accepted", result["log"]["outcome"])
        self.assertEqual([5], waits)
        self.assertEqual(2, len(client.calls))
        exhausted = FakeClient(fail_first=5)
        result = run_request(exhausted, self.contract, self.patient, [self.qid],
                             self.example_set, sleeper=lambda s: None)
        self.assertEqual("transport_failure", result["log"]["outcome"])
        self.assertEqual(2, len(exhausted.calls))

    def test_precheck_over_budget_sends_nothing(self):
        huge = {"patient_id": "zz-huge", "evidence": [
            {"evidence_id": "big-1", "text": "x" * 70000}]}
        client = FakeClient()
        result = run_request(client, self.contract, huge, [self.qid],
                             self.example_set, sleeper=lambda s: None)
        self.assertEqual("context_over_budget", result["log"]["outcome"])
        self.assertEqual(0, len(client.calls))

    def test_postcheck_over_budget_abstains(self):
        client = FakeClient(prompt_tokens=RUN_PARAMETERS["num_ctx"])
        result = run_request(client, self.contract, self.patient, [self.qid],
                             self.example_set, sleeper=lambda s: None)
        self.assertEqual("context_over_budget", result["log"]["outcome"])

    def test_pilot_covers_all_slots_after_unload_and_decides_mode(self):
        client = FakeClient()
        pilot = run_pilot(client, self.contract, self.manifest, self.example_set,
                          synthetic=True, sleeper=lambda s: None)
        self.assertEqual(1, client.unloads)
        self.assertEqual(23, len(pilot["slot_logs"]))
        self.assertTrue(pilot["slot_logs"][0]["cold_start"])
        self.assertEqual("v2", pilot["timing_decision"]["mode"])

    def test_run_reuses_pilot_and_reports_complete_grid(self):
        client = FakeClient()
        pilot = run_pilot(client, self.contract, self.manifest, self.example_set,
                          synthetic=True, sleeper=lambda s: None)
        before = len(client.calls)
        run = run_e1(client, self.contract, self.manifest, self.example_set, pilot,
                     synthetic=True, sleeper=lambda s: None)
        expected_new = (len(self.validation_ids) - 1) * 23
        self.assertEqual(before + expected_new, len(client.calls))
        self.assertEqual(len(self.validation_ids) * 23, len(run["rows"]))
        self.assertEqual(1.0, run["evaluation"]["metrics"]["typed_exact_match"])
        self.assertEqual({"accepted": len(self.validation_ids) * 23},
                         run["request_outcomes"])
        self.assertGreaterEqual(run["latency_seconds_p95"], run["latency_seconds_p50"])

    def test_open_runtime_checks_probed_engine_and_parent_digest(self):
        from unittest.mock import patch
        from clinical_matcher.p8_e1 import open_runtime
        parent = self.contract["parent_model_contract"]

        class Probe:
            def __init__(self, version_value, digest):
                self._version, self._digest = version_value, digest

            def version(self):
                return self._version

            def tags(self):
                return {"models": [{"name": parent["model"]["ollama_model_name"],
                                    "digest": self._digest}]}

        good = Probe("test", parent["model"]["ollama_manifest_sha256"])
        with patch("clinical_matcher.p8_e1.OllamaLoopbackClient", lambda *a, **k: good):
            self.assertIs(good, open_runtime(self.contract))
        drifted = Probe("other-version", parent["model"]["ollama_manifest_sha256"])
        with patch("clinical_matcher.p8_e1.OllamaLoopbackClient", lambda *a, **k: drifted):
            with self.assertRaises(P8Error):
                open_runtime(self.contract)
        swapped = Probe("test", "sha256:different")
        with patch("clinical_matcher.p8_e1.OllamaLoopbackClient", lambda *a, **k: swapped):
            with self.assertRaises(P8Error):
                open_runtime(self.contract)

    def test_output_schema_variants_are_flat_and_numeric_never_absent(self):
        from clinical_matcher.p8_prompt import output_schema
        by_type = {q["question_type"]: q for q in QUESTIONS}
        for kind, q in by_type.items():
            schema = output_schema([q["question_id"]], evidence_ids=["e-1", "e-2"])
            variants = schema["properties"]["assessments"]["items"]["oneOf"]
            statuses = sorted(v["properties"]["fact_status"]["const"] for v in variants)
            for variant in variants:
                self.assertEqual(sorted(variant["required"]), sorted(variant["properties"]))
                self.assertNotIn("oneOf", variant)
                self.assertEqual(["e-1", "e-2"],
                                 variant["properties"]["evidence_ids"]["items"]["enum"])
                if variant["properties"]["fact_status"]["const"] == "present":
                    self.assertEqual("string", variant["properties"]["supporting_quote"]["type"])
            if kind == "numeric":
                self.assertEqual(["present", "unknown"], statuses)
            else:
                self.assertEqual(["absent", "present", "unknown"], statuses)

    def test_zero_duration_requests_still_count_toward_latency_percentiles(self):
        from unittest.mock import patch
        client = FakeClient()
        # Freeze the clock so every request measures exactly 0.0 seconds.
        with patch("clinical_matcher.p8_e1.time.monotonic", return_value=100.0):
            pilot = run_pilot(client, self.contract, self.manifest, self.example_set,
                              synthetic=True, sleeper=lambda s: None)
            run = run_e1(client, self.contract, self.manifest, self.example_set, pilot,
                         synthetic=True, sleeper=lambda s: None)
        self.assertEqual(0.0, run["latency_seconds_p50"])
        self.assertEqual(0.0, run["latency_seconds_p95"])

    def test_quote_matching_tolerates_whitespace_only_and_nothing_else(self):
        from clinical_matcher.p8_prompt import quote_matches
        chunk = "HISTORY:\nThe patient has a history of\natrial  fibrillation diagnosed in 2019.\nNo prior stroke."
        self.assertTrue(quote_matches("atrial fibrillation diagnosed in 2019.", chunk))
        self.assertTrue(quote_matches("history of atrial fibrillation", chunk))
        self.assertFalse(quote_matches("Atrial fibrillation diagnosed in 2019.", chunk))  # case
        self.assertFalse(quote_matches("atrial fibrillation, diagnosed in 2019.", chunk))  # punctuation
        self.assertFalse(quote_matches("AF diagnosed in 2019.", chunk))  # paraphrase
        self.assertEqual("whitespace-nfkc-normalized-verbatim/1.0.0",
                         self.contract["quote_match_policy"])

    def test_full_run_refuses_pilot_without_any_accepted_response(self):
        client = FakeClient(invalid_content=True)
        pilot = run_pilot(client, self.contract, self.manifest, self.example_set,
                          synthetic=True, sleeper=lambda s: None)
        with self.assertRaises(P8Error):
            run_e1(client, self.contract, self.manifest, self.example_set, pilot,
                   synthetic=True, sleeper=lambda s: None)

    def test_run_rejects_mode_mismatch_with_pilot(self):
        client = FakeClient()
        pilot = run_pilot(client, self.contract, self.manifest, self.example_set,
                          synthetic=True, sleeper=lambda s: None)
        grouped = build_e1_contract(self.manifest, self.example_set, self.decision,
                                    mode="v2b", synthetic=True,
                                    runtime_identity={"engine_version": "test"})
        with self.assertRaises(P8Error):
            run_e1(client, grouped, self.manifest, self.example_set, pilot,
                   synthetic=True, sleeper=lambda s: None)


if __name__ == "__main__":
    unittest.main()


class P8E1AblationA4Tests(unittest.TestCase):
    """Variant 2.0.0-a4 removes only the quote hard constraint (factor F4)."""

    setUp = P8E1Tests.setUp  # reuse the fixture without re-running the base tests

    def _a4_contract(self):
        return build_e1_contract(self.manifest, self.example_set, self.decision,
                                 mode="v2-a4", synthetic=True,
                                 runtime_identity={"engine_version": "test"})

    def test_a4_groups_match_v2_and_schema_makes_quotes_optional(self):
        from clinical_matcher.p8_prompt import output_schema, question_groups
        self.assertEqual(question_groups("v2"), question_groups("v2-a4"))
        q = next(x for x in QUESTIONS if x["question_type"] == "boolean")
        for variant in output_schema([q["question_id"]], mode="v2-a4")["properties"]["assessments"]["items"]["oneOf"]:
            self.assertEqual(["string", "null"], variant["properties"]["supporting_quote"]["type"])
            if variant["properties"]["fact_status"]["const"] == "present":
                self.assertEqual(1, variant["properties"]["evidence_ids"]["minItems"])

    def test_a4_keeps_known_answers_without_or_with_unverified_quotes(self):
        from clinical_matcher.p8_prompt import parse_response, project_response, build_messages
        q = next(x for x in QUESTIONS if x["question_type"] == "boolean")
        patient = {"patient_id": self.validation_ids[0],
                   "evidence": [{"evidence_id": "p-e1", "text": "History of atrial fibrillation."}]}
        base = {"question_id": q["question_id"], "question_type": "boolean",
                "fact_status": "present", "value": True, "unit": None, "evidence_ids": ["p-e1"]}
        no_quote = json.dumps({"assessments": [dict(base, supporting_quote=None)]})
        paraphrase = json.dumps({"assessments": [dict(base, supporting_quote="AF history noted")]})
        with self.assertRaises(P8Error):
            parse_response(no_quote, patient=patient, question_ids=[q["question_id"]], mode="v2")
        rows = parse_response(no_quote, patient=patient, question_ids=[q["question_id"]], mode="v2-a4")
        self.assertEqual("present", rows[0]["fact_status"])
        projected, outcome = project_response(paraphrase, patient=patient,
                                              question_ids=[q["question_id"]], mode="v2-a4")
        self.assertEqual("accepted", outcome)
        self.assertEqual("present", projected[0]["fact_status"])
        self.assertIn("p8.v2-a4.quote_unverified", projected[0]["trace_ids"])
        system = build_messages(patient, [q["question_id"]], self.example_set, mode="v2-a4")[0]["content"]
        self.assertIn("supporting_quote is optional", system)
        self.assertNotIn("requires a quote", system)

    def test_a4_contract_records_variant_and_removed_factor(self):
        contract = self._a4_contract()
        self.assertEqual("2.0.0-a4", contract["ablation_variant"])
        self.assertEqual("F4", contract["removed_factor"])
        self.assertEqual("apixaban-23-facts-perq-2.0.0-a4", contract["prompt_version"])
        self.assertIsNone(self.contract["ablation_variant"])
