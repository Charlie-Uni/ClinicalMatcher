"""Reader-input ablation: v1 prompt over different evidence inputs (synthetic only)."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p8_reader import (ARMS, build_reader_contract, load_retrieval_selection,
                                        request_groups, run_pilot, run_reader, run_request,
                                        select_evidence)
from clinical_matcher.p8_safety import P8Error, seal
from tests.test_p8_e1 import _partition, _register, _unknown_grid, access_fixture

QUESTIONS = load_question_catalog()["questions"]
QIDS = [q["question_id"] for q in QUESTIONS]


def _answer_payload(question_ids):
    return json.dumps({"assessments": [
        {"question_id": qid, "fact_status": "unknown", "value": None, "unit": None, "evidence_ids": []}
        for qid in question_ids]})


class FakeReaderClient:
    def __init__(self, *, invalid_content=False, prompt_tokens=1500):
        self.calls, self.unloads = [], 0
        self.invalid_content, self.prompt_tokens = invalid_content, prompt_tokens

    def chat(self, payload):
        if payload.get("messages") == []:
            self.unloads += 1
            return {"message": {"content": ""}}
        self.calls.append(copy.deepcopy(payload))
        user = json.loads(payload["messages"][1]["content"])
        ids = [q["question_id"] for q in user["questions"]]
        content = "not json" if self.invalid_content else _answer_payload(ids)
        return {"message": {"content": content}, "prompt_eval_count": self.prompt_tokens,
                "eval_count": 30, "load_duration": 5, "total_duration": 10}


def _retrieval_document(patient_ids, chunk_ids):
    """Frozen top-3 per question: chunks 2, 1, 3 in that order; chunk 4 never selected."""
    results = []
    for pid in patient_ids:
        for qid in QIDS:
            picks = [chunk_ids[pid][1], chunk_ids[pid][0], chunk_ids[pid][2]]
            results.append({"patient_id": pid, "question_id": qid, "candidate_count": 4,
                            "selected_evidence": [
                                {"evidence_id": eid, "rank": rank, "score": 1.0 / rank,
                                 "bm25_rank": rank, "dense_rank": rank}
                                for rank, eid in enumerate(picks, start=1)]})
    return {"rrf_run_version": "1.0.0", "run_sha256": "f" * 64, "results": results}


class P8ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        root.chmod(0o700)
        manifest = access_fixture(root)
        self.validation_ids = sorted(manifest["populations"]["validation"])
        self.chunks = {pid: [f"{pid}-e{i}" for i in range(1, 5)] for pid in self.validation_ids}
        evidence_rows = [{"patient_id": pid, "evidence": [
            {"evidence_id": eid, "text": f"Synthetic chunk {i} for a synthetic patient."}
            for i, eid in enumerate(self.chunks[pid], start=1)]} for pid in self.validation_ids]
        manifest = _register(manifest, root, "validation.evidence",
                             _partition(manifest, "validation", "evidence", evidence_rows))
        manifest = _register(manifest, root, "validation.gold",
                             _partition(manifest, "validation", "gold",
                                        _unknown_grid(self.validation_ids)))
        self.manifest = manifest
        self.retrieval = _retrieval_document(self.validation_ids, self.chunks)
        self.patients = {row["patient_id"]: row for row in evidence_rows}
        self.first = self.patients[self.validation_ids[0]]

    def _contract(self, arm, retrieval="auto"):
        if retrieval == "auto":
            retrieval = self.retrieval if ARMS[arm]["retrieval_required"] else None
        return build_reader_contract(self.manifest, arm=arm, retrieval=retrieval, synthetic=True,
                                     runtime_identity={"engine_version": "test", "accelerator": "none"})

    def test_arm_a_sends_every_chunk_in_one_batched_request(self):
        client = FakeReaderClient()
        contract = self._contract("A")
        groups = request_groups("A")
        self.assertEqual([QIDS], groups)
        result = run_request(client, contract, self.first, groups[0], None, sleeper=lambda s: None)
        self.assertEqual("accepted", result["log"]["outcome"])
        self.assertEqual(23, len(result["rows"]))
        user = json.loads(client.calls[-1]["messages"][1]["content"])
        self.assertEqual(self.chunks[self.first["patient_id"]],
                         [item["evidence_id"] for item in user["note_evidence"]])
        self.assertEqual(23, len(user["questions"]))
        # v1 decoding and prompt are reused unchanged.
        self.assertEqual(4096, client.calls[-1]["options"]["num_predict"])
        self.assertIn("absent requires explicit negation", client.calls[-1]["messages"][0]["content"])
        self.assertEqual("apixaban-23-facts-structured-1.0.0", contract["prompt_version"])

    def test_arm_b3_keeps_three_best_chunks_in_note_order_and_b5_keeps_all_scored(self):
        pid = self.first["patient_id"]
        index = load_retrieval_selection(self.retrieval)
        b3 = select_evidence("B3", self.first, QIDS, index)
        self.assertEqual(self.chunks[pid][:3], [item["evidence_id"] for item in b3])
        b5 = select_evidence("B5", self.first, QIDS, index)
        # Chunk 4 never appears in any frozen selection, so it has no score and is dropped.
        self.assertEqual(self.chunks[pid][:3], [item["evidence_id"] for item in b5])
        contract = self._contract("B3")
        client = FakeReaderClient()
        result = run_request(client, contract, self.first, QIDS, index, sleeper=lambda s: None)
        self.assertEqual(3, result["log"]["evidence_chunks_input"])
        self.assertEqual(4, result["log"]["evidence_chunks_total"])

    def test_arm_c_uses_only_the_questions_frozen_top3_and_one_request_per_question(self):
        index = load_retrieval_selection(self.retrieval)
        contract = self._contract("C")
        self.assertEqual([[qid] for qid in QIDS], request_groups("C"))
        client = FakeReaderClient()
        result = run_request(client, contract, self.first, [QIDS[0]], index, sleeper=lambda s: None)
        user = json.loads(client.calls[-1]["messages"][1]["content"])
        self.assertEqual(1, len(user["questions"]))
        pid = self.first["patient_id"]
        self.assertEqual(self.chunks[pid][:3], [item["evidence_id"] for item in user["note_evidence"]])
        self.assertEqual(1, len(result["rows"]))
        enum = client.calls[-1]["format"]["properties"]["assessments"]["items"]["oneOf"][0]
        self.assertEqual(self.chunks[pid][:3], enum["properties"]["evidence_ids"]["items"]["enum"])

    def test_arm_d_is_per_question_over_every_chunk(self):
        contract = self._contract("D")
        client = FakeReaderClient()
        result = run_request(client, contract, self.first, [QIDS[3]], None, sleeper=lambda s: None)
        user = json.loads(client.calls[-1]["messages"][1]["content"])
        self.assertEqual(4, len(user["note_evidence"]))
        self.assertEqual(1, len(user["questions"]))
        self.assertEqual("accepted", result["log"]["outcome"])

    def test_contract_records_arm_pins_and_requires_retrieval_only_where_declared(self):
        contract = self._contract("C")
        self.assertEqual(("C", "per-question-top-k-rrf", False, 3),
                         (contract["arm"], contract["input_policy"], contract["batched"], contract["top"]))
        self.assertEqual("content", contract["retrieval_pin"]["kind"])
        self.assertEqual("f" * 64, contract["retrieval_run_sha256"])
        self.assertEqual(1800, self._contract("A")["parameters"]["timeout_seconds"])
        self.assertEqual(600, contract["parameters"]["timeout_seconds"])
        self.assertTrue(contract["engine_version_deviation_from_parent"])
        with self.assertRaises(P8Error):
            build_reader_contract(self.manifest, arm="B3", synthetic=True,
                                  runtime_identity={"engine_version": "test"})
        with self.assertRaises(P8Error):
            build_reader_contract(self.manifest, arm="A", retrieval=self.retrieval, synthetic=True,
                                  runtime_identity={"engine_version": "test"})
        with self.assertRaises(P8Error):
            build_reader_contract(self.manifest, arm="Z", synthetic=True,
                                  runtime_identity={"engine_version": "test"})

    def test_pilot_then_full_run_complete_the_grid_and_reuse_the_pilot(self):
        client = FakeReaderClient()
        contract = self._contract("A")
        pilot = run_pilot(client, contract, self.manifest, None, synthetic=True, sleeper=lambda s: None)
        self.assertEqual(1, client.unloads)
        self.assertEqual(1, len(pilot["slot_logs"]))
        self.assertEqual(1, pilot["timing_decision"]["requests_per_patient"])
        run = run_reader(client, contract, self.manifest, pilot, None, synthetic=True,
                         sleeper=lambda s: None)
        self.assertEqual(len(self.validation_ids), len(client.calls))
        self.assertEqual(len(self.validation_ids) * 23, len(run["rows"]))
        self.assertEqual(1.0, run["evaluation"]["metrics"]["typed_exact_match"])
        self.assertEqual({"accepted": len(self.validation_ids)}, run["request_outcomes"])
        self.assertEqual(4.0, run["evidence_chunks_input_mean"])
        # Arm C: 23 requests per patient with the retrieval document pinned.
        client = FakeReaderClient()
        contract = self._contract("C")
        pilot = run_pilot(client, contract, self.manifest, self.retrieval, synthetic=True,
                          sleeper=lambda s: None)
        self.assertEqual(23, pilot["timing_decision"]["requests_per_patient"])
        run = run_reader(client, contract, self.manifest, pilot, self.retrieval, synthetic=True,
                         sleeper=lambda s: None)
        self.assertEqual(len(self.validation_ids) * 23, len(client.calls))
        self.assertEqual(3.0, run["evidence_chunks_input_mean"])

    def test_tampered_retrieval_document_and_missing_coverage_are_refused(self):
        contract = self._contract("C")
        tampered = copy.deepcopy(self.retrieval)
        tampered["results"][0]["selected_evidence"][0]["score"] = 9.0
        with self.assertRaises(P8Error):
            run_pilot(FakeReaderClient(), contract, self.manifest, tampered, synthetic=True,
                      sleeper=lambda s: None)
        partial = copy.deepcopy(self.retrieval)
        partial["results"] = partial["results"][:-1]
        partial_contract = build_reader_contract(self.manifest, arm="C", retrieval=partial, synthetic=True,
                                                 runtime_identity={"engine_version": "test"})
        with self.assertRaises(P8Error):
            run_pilot(FakeReaderClient(), partial_contract, self.manifest, partial, synthetic=True,
                      sleeper=lambda s: None)

    def test_invalid_output_abstains_the_whole_request_and_blocks_the_full_run(self):
        client = FakeReaderClient(invalid_content=True)
        contract = self._contract("A")
        result = run_request(client, contract, self.first, QIDS, None, sleeper=lambda s: None)
        self.assertEqual("invalid_output", result["log"]["outcome"])
        self.assertTrue(all(row["fact_status"] == "unknown" and row["abstention_reason"] == "invalid_output"
                            for row in result["rows"]))
        pilot = run_pilot(client, contract, self.manifest, None, synthetic=True, sleeper=lambda s: None)
        with self.assertRaises(P8Error):
            run_reader(client, contract, self.manifest, pilot, None, synthetic=True, sleeper=lambda s: None)


class P8ReaderE2Tests(unittest.TestCase):
    """E2: the reader contract can swap the model and nothing else."""

    setUp = P8ReaderTests.setUp

    def test_default_contract_uses_the_parent_model_without_think(self):
        contract = build_reader_contract(self.manifest, arm="A", synthetic=True,
                                         runtime_identity={"engine_version": "test"})
        self.assertEqual("parent", contract["effective_model"]["source"])
        self.assertEqual("llama3.1:latest", contract["effective_model"]["ollama_model_name"])
        self.assertFalse(contract["model_deviation_from_parent"])
        client = FakeReaderClient()
        run_request(client, contract, self.first, QIDS, None, sleeper=lambda s: None)
        self.assertEqual("llama3.1:latest", client.calls[-1]["model"])
        self.assertNotIn("think", client.calls[-1])

    def test_e2_override_pins_digest_records_license_and_disables_thinking(self):
        contract = build_reader_contract(self.manifest, arm="A", synthetic=True,
                                         runtime_identity={"engine_version": "test"},
                                         model="qwen3:14b", model_digest="a" * 64)
        model = contract["effective_model"]
        self.assertEqual(("e2_override", "qwen3:14b", "a" * 64, False, "Apache-2.0"),
                         (model["source"], model["ollama_model_name"], model["ollama_manifest_sha256"],
                          model["think"], model["license"]["name"]))
        self.assertTrue(contract["model_deviation_from_parent"])
        # Prompt, schema and decoding are the frozen v1 ones.
        self.assertEqual("apixaban-23-facts-structured-1.0.0", contract["prompt_version"])
        self.assertEqual(4096, contract["parameters"]["num_predict"])
        client = FakeReaderClient()
        result = run_request(client, contract, self.first, QIDS, None, sleeper=lambda s: None)
        self.assertEqual("accepted", result["log"]["outcome"])
        self.assertEqual("qwen3:14b", client.calls[-1]["model"])
        self.assertIs(False, client.calls[-1]["think"])
        self.assertIn("absent requires explicit negation", client.calls[-1]["messages"][0]["content"])
        run = run_reader(client, contract, self.manifest,
                         run_pilot(client, contract, self.manifest, None, synthetic=True,
                                   sleeper=lambda s: None), None, synthetic=True, sleeper=lambda s: None)
        self.assertEqual("qwen3:14b", run["effective_model"]["ollama_model_name"])
        with self.assertRaises(P8Error):
            build_reader_contract(self.manifest, arm="A", synthetic=True,
                                  runtime_identity={"engine_version": "test"}, model="qwen3:14b")
        with self.assertRaises(P8Error):
            build_reader_contract(self.manifest, arm="A", synthetic=True,
                                  runtime_identity={"engine_version": "test"},
                                  model="mystery:latest", model_digest="a" * 64)

    def test_runtime_verifies_the_effective_model_digest(self):
        from unittest.mock import patch
        from clinical_matcher.p8_reader import open_reader_runtime
        contract = build_reader_contract(self.manifest, arm="A", synthetic=True,
                                         runtime_identity={"engine_version": "test"},
                                         model="qwen3:14b", model_digest="a" * 64)

        class Probe:
            def __init__(self, digest):
                self._digest = digest

            def version(self):
                return "test"

            def tags(self):
                return {"models": [{"name": "qwen3:14b", "digest": self._digest},
                                   {"name": "llama3.1:latest", "digest": "other"}]}

        good = Probe("a" * 64)
        with patch("clinical_matcher.apixaban_structured_llm.OllamaLoopbackClient", lambda *a, **k: good):
            self.assertIs(good, open_reader_runtime(contract))
        swapped = Probe("b" * 64)
        with patch("clinical_matcher.apixaban_structured_llm.OllamaLoopbackClient", lambda *a, **k: swapped):
            with self.assertRaises(P8Error):
                open_reader_runtime(contract)


if __name__ == "__main__":
    unittest.main()
