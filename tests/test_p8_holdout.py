"""P8.5 producer/consumer and one-shot failure tests; fictional inputs only."""

import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p8_holdout import (ARMS, PUBLIC_FIELDS, _ConsumedPartitionReader,
    check_plan, freeze_plan, public_projection, retrieve, run_batch)
from clinical_matcher.p8_reader import build_reader_contract
from clinical_matcher.p8_safety import (P8Error, check_pin, check_seal,
    consume_holdout_once, make_pin, read_metadata, seal)
from tests.test_apixaban_dense import FakeMedCPTEncoder
from tests.test_p8_e1 import _partition, _register, _unknown_grid
from tests.test_p8_reader import FakeReaderClient
from tests.test_p8_safety import access_fixture


def batch_fixture(root: Path):
    """Also used for the live local-runtime replay; contains no real record."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    manifest = access_fixture(root)
    manifest["artifacts"] = [a for a in manifest["artifacts"] if a["partition"] != "secondary_holdout"]
    manifest = seal(manifest)
    ids = manifest["populations"]["secondary_holdout"]
    patients = []
    for pid in ids:
        text = "Fictional patient: atrial fibrillation documented. Hemoglobin 11.2. No diabetes."
        patients.append({"patient_id": pid, "evidence": [{"evidence_id": f"{pid}-e1",
            "source_id": "fictional-note", "section": None, "text": text,
            "source_span": {"start": 0, "end": len(text)}}]})
    manifest = _register(manifest, root, "secondary_holdout.evidence",
        _partition(manifest, "secondary_holdout", "evidence", patients))
    manifest = _register(manifest, root, "secondary_holdout.gold",
        _partition(manifest, "secondary_holdout", "gold", _unknown_grid(ids)))
    retrieval = {"run_sha256": "a" * 64, "results": [
        {"patient_id": pid, "question_id": q["question_id"], "selected_evidence": [
            {"evidence_id": f"{pid}-e1", "rank": 1, "score": 1.0}]}
        for pid in ids for q in load_question_catalog()["questions"]]}
    contract = build_reader_contract(manifest, arm="H", retrieval=retrieval,
        runtime_identity={"engine_version": "0.34.4", "hardware": {}, "accelerator": "unavailable"},
        synthetic=True)
    preflight = seal({k: True for k in ("full_tests_passed", "synthetic_batch_passed",
        "live_synthetic_replay_passed", "public_data_guard_passed", "installed_source_verified")})
    plan = freeze_plan(manifest, contract, output_root=root / "output", preflight=preflight,
                       dependencies=[], source_commit="synthetic", synthetic=True)
    authorization = {"owner_explicitly_authorized": True,
                     "owner_decision_record_id": "synthetic-owner-instruction",
                     "final_plan_pin": make_pin(plan, "self", self_field="self_sha256")}
    return manifest, plan, authorization


class P8HoldoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "lifetime-state"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in ("p8_safety", "p8_holdout"):
            self.stack.enter_context(patch(f"clinical_matcher.{module}.holdout_state_root", return_value=self.state))
        self.manifest, self.plan, self.authorization = batch_fixture(self.root / "fixture")
        self.client = FakeReaderClient()
        self.encoder = FakeMedCPTEncoder({})

    def run_batch(self, **kwargs):
        return run_batch(self.plan, self.manifest, self.authorization,
                         client=self.client, encoder=self.encoder, synthetic=True, **kwargs)

    def test_full_batch_reads_evidence_then_gold_once_after_persisted_raw(self):
        from clinical_matcher.p8_holdout import _read_private_bytes
        reads = []
        def guarded(path):
            reads.append(path.name)
            if "gold" in path.name:
                self.assertTrue((Path(self.plan["output_root"]) / "raw-complete.json").exists())
            self.assertTrue((self.state / "secondary-holdout-consumed.json").exists())
            return _read_private_bytes(path)
        with patch("clinical_matcher.p8_holdout._read_private_bytes", side_effect=guarded):
            result = self.run_batch()
        self.assertEqual(["secondary_holdout.evidence.json", "secondary_holdout.gold.json"], reads)
        self.assertEqual(set(ARMS), set(result["views"]))
        for arm in result["views"].values():
            for fields in arm.values():
                self.assertEqual(PUBLIC_FIELDS, set(fields))
        output = Path(self.plan["output_root"])
        final = read_metadata(output / "batch-complete.json")
        check_seal(final)
        for item in final["artifacts"]:
            check_pin(item["file_pin"], (output / item["path"]).read_bytes(), kind="file")
        raw = read_metadata(output / "raw-complete.json")
        for key in ("raw", "safety"):
            self.assertEqual(len(self.manifest["populations"]["secondary_holdout"]) * 23,
                             len(raw[key]["H.V1"]))

    def test_repeated_batch_new_output_and_new_plan_cannot_reset_slot(self):
        self.run_batch()
        changed = seal(dict(self.plan, output_root=str(self.root / "another-output")))
        auth = dict(self.authorization, final_plan_pin=make_pin(changed, "self", self_field="self_sha256"))
        with patch("clinical_matcher.p8_holdout._read_private_bytes") as read:
            with self.assertRaisesRegex(P8Error, "already consumed"):
                run_batch(changed, self.manifest, auth, client=self.client, encoder=self.encoder, synthetic=True)
            read.assert_not_called()

    def test_tampered_final_plan_and_authorization_fail_before_exposure(self):
        changed = copy.deepcopy(self.plan)
        changed["arms"] = ["unapproved"]
        with patch("clinical_matcher.p8_holdout._read_private_bytes") as read:
            with self.assertRaises(P8Error):
                run_batch(changed, self.manifest, self.authorization,
                          client=self.client, encoder=self.encoder, synthetic=True)
            auth = dict(self.authorization, owner_explicitly_authorized=False)
            with self.assertRaises(P8Error):
                run_batch(self.plan, self.manifest, auth,
                          client=self.client, encoder=self.encoder, synthetic=True)
            read.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_changed_code_pin_rejected_before_exposure(self):
        changed = copy.deepcopy(self.plan)
        changed["implementation"][0]["file_pin"]["value"] = "0" * 64
        changed = seal(changed)
        auth = dict(self.authorization, final_plan_pin=make_pin(changed, "self", self_field="self_sha256"))
        with self.assertRaises(P8Error):
            run_batch(changed, self.manifest, auth, client=self.client, encoder=self.encoder, synthetic=True)
        self.assertFalse(self.state.exists())

    def test_partition_content_mismatch_is_terminal_after_consumption(self):
        artifact = next(a for a in self.plan["protected_artifacts"] if a["kind"] == "evidence")
        Path(artifact["path"]).write_text('{}\n')
        with self.assertRaises(P8Error):
            self.run_batch()
        self.assertTrue((self.state / "secondary-holdout-consumed.json").exists())
        failure = read_metadata(Path(self.plan["output_root"]) / "terminal-failure.json")
        self.assertTrue(failure["consumed"])
        self.assertEqual("read_evidence", failure["stage"])
        with self.assertRaises(P8Error):
            self.run_batch()

    def test_mid_inference_failure_preserves_slot_raw_and_closed_gold(self):
        from clinical_matcher.p8_holdout import run_request
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Synthetic secret text must not enter failure metadata")
            return run_request(*args, **kwargs)
        with patch("clinical_matcher.p8_holdout.run_request", side_effect=fail_second):
            with self.assertRaises(P8Error):
                self.run_batch()
        output = Path(self.plan["output_root"])
        failure_text = (output / "terminal-failure.json").read_text()
        self.assertNotIn("secret text", failure_text)
        self.assertFalse((output / "full-report.json").exists())
        self.assertTrue((output / "requests" / "000-0.json").exists())
        self.assertTrue((self.state / "secondary-holdout-consumed.json").exists())

    def test_consumed_reader_requires_durable_marker_and_cannot_open_gold_first(self):
        event = consume_holdout_once(authorization=self.authorization, access_manifest=self.manifest,
                                    final_plan=self.plan, synthetic=True)
        reader = _ConsumedPartitionReader(self.plan, self.manifest, event)
        with self.assertRaises(P8Error):
            reader.read("gold")
        reader.read("evidence")
        with self.assertRaises(P8Error):
            reader.read("evidence")
        fake = seal(dict(event, status="fabricated"))
        with self.assertRaises(P8Error):
            _ConsumedPartitionReader(self.plan, self.manifest, fake)

    def test_invalid_request_is_measured_abstention_and_does_not_restart_batch(self):
        self.client = FakeReaderClient(invalid_content=True)
        result = self.run_batch()
        self.assertEqual(23 * len(self.manifest["populations"]["secondary_holdout"]),
                         result["views"]["H"]["raw"]["unknown_count"])
        self.assertEqual(2 * len(self.manifest["populations"]["secondary_holdout"]), len(self.client.calls))

    def test_synthetic_manifest_cannot_enter_real_batch(self):
        with self.assertRaises(P8Error):
            run_batch(self.plan, self.manifest, self.authorization, client=self.client, encoder=self.encoder)
        self.assertFalse(self.state.exists())

    def test_partial_marker_and_concurrent_batches_cannot_create_another_exposure(self):
        def attempt(index):
            plan = seal(dict(self.plan, output_root=str(self.root / f"concurrent-{index}")))
            auth = dict(self.authorization, final_plan_pin=make_pin(plan, "self", self_field="self_sha256"))
            try:
                run_batch(plan, self.manifest, auth, client=FakeReaderClient(),
                          encoder=FakeMedCPTEncoder({}), synthetic=True)
                return True
            except P8Error:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(1, sum(pool.map(attempt, range(2))))
        # A new output root must not make even a partial marker repairable.
        (self.state / "secondary-holdout-consumed.json").write_bytes(b"{")
        with patch("clinical_matcher.p8_holdout._read_private_bytes") as read:
            with self.assertRaises(P8Error):
                self.run_batch()
            read.assert_not_called()

    def test_retrieval_is_patient_local_and_does_not_take_gold(self):
        import inspect
        self.assertNotIn("gold", inspect.signature(retrieve).parameters)
        artifact = next(a for a in self.plan["protected_artifacts"] if a["kind"] == "evidence")
        patients = json.loads(Path(artifact["path"]).read_text())["rows"]
        selection = retrieve(patients, self.encoder)
        for (pid, qid), rows in selection.items():
            self.assertTrue(all(eid == f"{pid}-e1" for eid, score in rows))

    def test_zero_score_lexical_candidates_do_not_receive_an_rrf_vote(self):
        from clinical_matcher.retrieval.base import RankedEvidence
        artifact = next(a for a in self.plan["protected_artifacts"] if a["kind"] == "evidence")
        patients = json.loads(Path(artifact["path"]).read_text())["rows"]
        def zero_rank(_retriever, pid, _query):
            return (RankedEvidence(pid, f"{pid}-e1", 0.0, 1),)
        with patch("clinical_matcher.p8_holdout.BM25PatientRetriever.rank", zero_rank):
            result = retrieve(patients, self.encoder)
        for rows in result.values():
            self.assertAlmostEqual(1 / 61, rows[0][1])


if __name__ == "__main__":
    unittest.main()
