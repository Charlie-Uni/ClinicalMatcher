import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.apixaban_benchmark import file_sha256
from clinical_matcher.apixaban_abstention import apply_deterministic_abstention
from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p7_locked_test import (
    BASE_ARM_IDS,
    VIEW_IDS,
    P7LockedTestError,
    build_public_release_candidate,
    build_representative_case_package,
    build_request_latency_trace,
    load_p7_contract,
    validate_batch_manifest,
    validate_p4_3_parent_derivation,
    validate_p7_contract,
)
from clinical_matcher.p7_locked_test_cli import build_parser
from clinical_matcher.p7_locked_test_runner import _execute_locked_test_batch
from clinical_matcher.splits import canonical_sha256

from tests.test_apixaban_error_attribution import benchmark_from_predictions
from tests.test_apixaban_abstention import gold_from_predictions
from tests.test_apixaban_neurosymbolic_audit import (
    PATIENT_ID,
    prediction_set,
    staging_corpus,
)


def _rehash_contract(document):
    unsigned = dict(document)
    unsigned.pop("contract_sha256", None)
    document["contract_sha256"] = canonical_sha256(unsigned)
    return document


def _write_private(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
    return path


def _fake_raw_phase(**kwargs):
    attempt_directory = kwargs["attempt_directory"]
    base_artifacts = []
    for arm_id in BASE_ARM_IDS:
        directory = attempt_directory / arm_id
        prediction_path = _write_private(directory / "predictions.json", "{}\n")
        latency_path = _write_private(directory / "request-latency.json", "{}\n")
        run_report_path = None
        if arm_id != BASE_ARM_IDS[0]:
            run_report_path = _write_private(directory / "run-report.json", "{}\n")
        base_artifacts.append(
            {
                "arm_id": arm_id,
                "prediction_path": prediction_path,
                "run_report_path": run_report_path,
                "latency_trace_path": latency_path,
            }
        )
    return {
        "predictions": {},
        "prediction_paths": {},
        "latency_traces": {},
        "base_artifacts": base_artifacts,
    }


def _fake_gold_phase(**kwargs):
    directory = kwargs["attempt_directory"] / "gold"
    view_artifacts = []
    for view_id in VIEW_IDS:
        view_directory = directory / view_id
        prediction_path = _write_private(view_directory / "predictions.json", "{}\n")
        p1_5_path = _write_private(view_directory / "p1-5.json", "{}\n")
        p4_5_path = _write_private(view_directory / "p4-5.json", "{}\n")
        p4_3_path = None
        if view_id.endswith(".p4_3"):
            p4_3_path = _write_private(view_directory / "p4-3.json", "{}\n")
        view_artifacts.append(
            {
                "view_id": view_id,
                "prediction_path": prediction_path,
                "p1_5_report_path": p1_5_path,
                "p4_5_report_path": p4_5_path,
                "p4_3_report_path": p4_3_path,
            }
        )
    p4_7_paths = tuple(
        _write_private(directory / f"p4-7.{suffix}", "synthetic\n")
        for suffix in ("report.json", "trace.json", "summary.md")
    )
    case_path = _write_private(directory / "cases.json", "{}\n")
    public_path = _write_private(directory / "public.json", "{}\n")
    return {
        "view_artifacts": view_artifacts,
        "p4_7_paths": p4_7_paths,
        "case_path": case_path,
        "public_path": public_path,
    }


class P7LockedTestTests(unittest.TestCase):
    def setUp(self):
        self.repository_root = Path(__file__).resolve().parents[1]

    def authorized_contract(self, inputs):
        contract = copy.deepcopy(load_p7_contract())
        contract["contract_status"] = (
            "owner_approved_frozen_p7_1_and_p7_2_authorized"
        )
        contract["authorization"] = {
            "p7_1_frozen": True,
            "p7_2_authorized": True,
            "locked_test_access_allowed": True,
        }
        contract["implementation"].update(
            {
                "pin_status": "complete",
                "code_commit": "0" * 40,
                "files": [
                    {
                        "path": "pyproject.toml",
                        "sha256": file_sha256(
                            self.repository_root / "pyproject.toml"
                        ),
                    }
                ],
            }
        )
        for field, path in inputs.items():
            contract["dataset"][field] = file_sha256(path)
        return _rehash_contract(contract)

    def input_paths(self, root):
        fields = {
            "split_file_sha256": "split.json",
            "staging_corpus_sha256": "staging.json",
            "benchmark_sha256": "benchmark.json",
            "benchmark_manifest_sha256": "benchmark-manifest.json",
            "mentor_results_sha256": "mentor.json",
            "mentor_candidate_csv_sha256": "mentor.csv",
            "id_map_sha256": "id-map.json",
        }
        inputs = {}
        for index, (field, name) in enumerate(fields.items()):
            inputs[field] = _write_private(root / "inputs" / name, f"{index}\n")
        return inputs

    def execute(self, root, contract, inputs, **kwargs):
        return _execute_locked_test_batch(
            repository_root=self.repository_root,
            output_root=root / "output",
            frozen_split_path=inputs["split_file_sha256"],
            staging_corpus_path=inputs["staging_corpus_sha256"],
            benchmark_path=inputs["benchmark_sha256"],
            benchmark_manifest_path=inputs["benchmark_manifest_sha256"],
            mentor_results_path=inputs["mentor_results_sha256"],
            mentor_candidate_csv_path=inputs["mentor_candidate_csv_sha256"],
            id_map_path=inputs["id_map_sha256"],
            clients=None,
            contract=contract,
            raw_phase=kwargs.get("raw_phase", _fake_raw_phase),
            gold_phase=kwargs.get("gold_phase", _fake_gold_phase),
            environment_check=lambda _contract, _root: None,
            allow_pre_gold_retry=kwargs.get("allow_pre_gold_retry", False),
        )

    def test_unauthorized_contract_blocks_before_any_path_is_resolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            absent = root / "must-not-be-created"
            contract = load_p7_contract()
            contract["contract_status"] = (
                "implementation_complete_owner_review_required_not_executable"
            )
            contract["authorization"] = {
                "p7_1_frozen": False,
                "p7_2_authorized": False,
                "locked_test_access_allowed": False,
            }
            contract = _rehash_contract(contract)
            with self.assertRaisesRegex(P7LockedTestError, "not authorized"):
                _execute_locked_test_batch(
                    repository_root=absent,
                    output_root=absent,
                    frozen_split_path=absent,
                    staging_corpus_path=absent,
                    benchmark_path=absent,
                    benchmark_manifest_path=absent,
                    mentor_results_path=absent,
                    mentor_candidate_csv_path=absent,
                    id_map_path=absent,
                    clients=None,
                    contract=contract,
                    raw_phase=_fake_raw_phase,
                    gold_phase=_fake_gold_phase,
                    environment_check=lambda _contract, _root: None,
                    allow_pre_gold_retry=False,
                )
            self.assertFalse(absent.exists())

    def test_successful_synthetic_state_machine_has_one_frozen_sequence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.input_paths(root)
            contract = self.authorized_contract(inputs)
            manifest_path = self.execute(root, contract, inputs)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            validate_batch_manifest(manifest, contract=contract)
            self.assertEqual(
                [
                    "attempt_started",
                    "raw_complete",
                    "gold_phase_started",
                    "batch_complete",
                ],
                [item["event"] for item in manifest["events"]],
            )
            with self.assertRaisesRegex(P7LockedTestError, "rerun.*forbidden"):
                self.execute(root, contract, inputs)

            raw_path = root / "output" / manifest["base_arms"][0]["prediction_path"]
            raw_path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(P7LockedTestError, "artifact hash mismatch"):
                validate_batch_manifest(
                    manifest, contract=contract, artifact_root=root / "output"
                )

    def test_only_one_explicit_pre_gold_retry_is_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.input_paths(root)
            contract = self.authorized_contract(inputs)

            def fail_raw(**kwargs):
                del kwargs
                raise RuntimeError("synthetic pre-gold failure")

            with self.assertRaisesRegex(P7LockedTestError, "pre-gold phase failed"):
                self.execute(root, contract, inputs, raw_phase=fail_raw)
            with self.assertRaisesRegex(P7LockedTestError, "explicit approval"):
                self.execute(root, contract, inputs)
            manifest_path = self.execute(
                root, contract, inputs, allow_pre_gold_retry=True
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(2, manifest["attempt"])
            self.assertEqual(6, len(manifest["events"]))

    def test_post_gold_failure_is_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = self.input_paths(root)
            contract = self.authorized_contract(inputs)

            def fail_gold(**kwargs):
                del kwargs
                raise RuntimeError("synthetic post-gold failure")

            with self.assertRaisesRegex(P7LockedTestError, "terminally"):
                self.execute(root, contract, inputs, gold_phase=fail_gold)
            events = sorted((root / "output" / "events").glob("*.json"))
            self.assertEqual(
                "terminal_failed",
                json.loads(events[-1].read_text(encoding="utf-8"))["event"],
            )
            with self.assertRaisesRegex(P7LockedTestError, "rerun.*forbidden"):
                self.execute(root, contract, inputs)

    def test_latency_trace_and_public_allowlist_are_exact(self):
        requests = [
            {
                "request_index": index,
                "patient_id": f"patient-{index:024x}",
                "latency_seconds": float(index),
                "prompt_tokens": None,
                "output_tokens": None,
            }
            for index in range(1, 4)
        ]
        traces = {
            arm_id: build_request_latency_trace(
                arm_id=arm_id, split_name="validation", requests=requests
            )
            for arm_id in BASE_ARM_IDS
        }
        reports = {
            view_id: {
                "metrics": {
                    "typed_exact_match": 0.5,
                    "boolean": {"macro_f1": 0.4},
                    "numeric_status": {"macro_f1": 0.3},
                }
            }
            for view_id in VIEW_IDS
        }
        predictions = {
            view_id: {
                "predictions": [
                    {"fact_status": "present", "abstained": True},
                    {"fact_status": "unknown", "abstained": True},
                ]
            }
            for view_id in VIEW_IDS
        }
        contract = load_p7_contract()
        candidate = build_public_release_candidate(
            contract=contract,
            evaluation_reports=reports,
            predictions=predictions,
            latency_traces=traces,
        )
        self.assertEqual(2, candidate["views"][0]["abstained_or_unknown_count"])
        self.assertEqual(2.0, candidate["base_arm_latency"][0]["latency_seconds_p50"])
        self.assertEqual(3.0, candidate["base_arm_latency"][0]["latency_seconds_p95"])
        tampered = copy.deepcopy(candidate)
        tampered["views"][0]["per_question"] = {}
        with self.assertRaises(ValueError):
            from clinical_matcher.p7_locked_test import validate_public_release_candidate

            validate_public_release_candidate(tampered, contract)

    def test_representative_cases_are_deterministic_and_owner_only(self):
        catalog = load_question_catalog()
        staging = staging_corpus(catalog)
        predictions = prediction_set(catalog)
        benchmark = benchmark_from_predictions(catalog, copy.deepcopy(predictions))
        predictions["predictions"][0].update(
            {
                "fact_status": "unknown",
                "value": None,
                "abstained": True,
                "abstention_reason": "synthetic_unknown",
            }
        )
        first = build_representative_case_package(
            contract=load_p7_contract(),
            view_id="llama31_long_context_1_0_0.p4_3",
            split_name="validation",
            prediction_set=predictions,
            benchmark=benchmark,
            staging_corpus=staging,
            expected_patient_ids=[PATIENT_ID],
        )
        second = build_representative_case_package(
            contract=load_p7_contract(),
            view_id="llama31_long_context_1_0_0.p4_3",
            split_name="validation",
            prediction_set=predictions,
            benchmark=benchmark,
            staging_corpus=staging,
            expected_patient_ids=[PATIENT_ID],
        )
        self.assertEqual(first, second)
        self.assertTrue(first["owner_only"])
        self.assertEqual("abstention_on_gold_known", first["cases"][0]["category"])

    def test_p4_3_parent_derivation_is_recomputed(self):
        catalog = load_question_catalog()
        predictions = prediction_set(catalog)
        projection, report = apply_deterministic_abstention(
            prediction_set=predictions,
            staging_corpus=staging_corpus(catalog),
            expected_patient_ids=[PATIENT_ID],
            gold_by_key=gold_from_predictions(predictions),
            source_prediction_sha256="1" * 64,
            split_manifest_sha256="2" * 64,
            staging_corpus_sha256="3" * 64,
            generated_at="2026-01-01T00:00:00Z",
            code_commit="4" * 40,
        )
        validate_p4_3_parent_derivation(
            raw_prediction_sha256="1" * 64,
            projected_prediction=projection,
            projection_report=report,
        )
        with self.assertRaisesRegex(P7LockedTestError, "config derivation mismatch"):
            validate_p4_3_parent_derivation(
                raw_prediction_sha256="9" * 64,
                projected_prediction=projection,
                projection_report=report,
            )

    def test_cli_has_no_split_or_arm_selection(self):
        destinations = {action.dest for action in build_parser()._actions}
        self.assertNotIn("split", destinations)
        self.assertNotIn("arm", destinations)

    def test_contract_rejects_changed_view_order(self):
        contract = copy.deepcopy(load_p7_contract())
        contract["execution"]["view_order"].reverse()
        _rehash_contract(contract)
        with self.assertRaises(ValueError):
            validate_p7_contract(contract)


if __name__ == "__main__":
    unittest.main()
