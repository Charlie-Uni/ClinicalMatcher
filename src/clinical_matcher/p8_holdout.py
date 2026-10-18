"""P8.5 single-exposure batch for the validation-selected H + V1 candidate.

Preparation reads metadata and public model/code files only. The batch consumes
the project-wide slot before opening either sealed partition. Gold is opened
only after rules, H, V1 and their safety projections have been persisted.
There is no resume, replacement population, or post-exposure repair command.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from .apixaban_bm25 import load_bm25_contract
from .apixaban_abstention import abstention_policy
from .apixaban_contract import load_question_catalog, question_index
from .apixaban_dense import load_dense_contract
from .apixaban_deterministic import extract_patient_predictions, load_deterministic_rule_set
from .apixaban_evaluation import exact_source_tolerance_policy
from .apixaban_rrf import load_rrf_contract, reciprocal_rank_fusion
from .apixaban_structured_llm import OllamaLoopbackClient, load_long_context_contract
from .evaluation import clustered_bootstrap
from .p8_e0 import evaluate_rows, indexed_grid, project_safety_rows
from .p8_reader import (probe_runtime_identity, request_groups, run_request,
                        unload_model, verify_model_digest)
from .p8_round3 import arbitrate, policy_document
from .p8_safety import (EventLedger, P8Error, _no_symlinks, _read_private_bytes,
                        _strict_json, check_pin, check_seal, consume_holdout_once,
                        holdout_state_root, make_pin, private_directory,
                        read_metadata, require_development_ready, seal,
                        write_private)
from .retrieval.bm25 import BM25PatientRetriever
from .retrieval.dense import DensePatientRetriever, MedCPTEncoder
from .validation import validate_document


VERSION = "1.0.0"
SELECTED_READER_SELF_SHA256 = "4337ae51193f07a387233bdfaf20af4a0634449b83116b61ce2e665b0bb09ae4"
ARMS = ["rules", "H", "H.V1"]
PUBLIC_FIELDS = {"typed_exact_match", "boolean_macro_f1", "numeric_status_macro_f1",
                 "unknown_count", "request_latency_p50_seconds", "request_latency_p95_seconds"}
FAILURE_POLICY = {
    "exposure": "consume_before_any_partition_read_or_byte_hash",
    "batch_retry": False, "resume": False, "replacement_holdout": False,
    "invalid_response": "whole_request_abstains_no_manual_repair",
    "transport_retry": "unchanged_frozen_reader_request_policy",
    "infrastructure_failure": "terminal_even_without_metrics",
    "gold_access": "after_all_raw_predictions_and_safety_projections_are_persisted",
}
STATISTICS = {"samples": 1000, "seed": 17, "confidence": 0.95,
              "unit": "patient_cluster_all_23_questions", "interval": "percentile",
              "paired_comparisons": ["H.V1_minus_H.raw", "H.V1_minus_H.safety"],
              "zero_denominator": "existing_P1.5_zero_division_policy",
              "historical_v1_control": False, "improvement_claim": False}


def policy_pins() -> dict:
    return {name: make_pin(document, "content") for name, document in {
        "catalog": load_question_catalog(), "rules": load_deterministic_rule_set(),
        "bm25": load_bm25_contract(), "dense": load_dense_contract(),
        "rrf": load_rrf_contract(), "V1": policy_document(),
        "evaluation": exact_source_tolerance_policy(), "P4.3": abstention_policy(),
    }.items()}


def implementation_inventory(root: Path | None = None) -> list[dict]:
    root = root or Path(__file__).parent
    return [{"path": str(p.relative_to(root)), "file_pin": make_pin(p.read_bytes(), "file")}
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in {".py", ".json"}]


def _file_pin(path: Path) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    pin = make_pin(b"", "file")
    pin["value"] = digest
    return pin


def public_file_inventory(paths: list[Path]) -> list[dict]:
    """Public runtime/encoder files only, never a clinical input hash preflight."""
    result = []
    vault = holdout_state_root().absolute()
    for path in sorted({p.resolve() for p in paths}):
        if path == vault or vault in path.parents:
            raise P8Error("Protected files cannot enter a public dependency inventory")
        result.append({"path": str(path), "file_pin": _file_pin(path)})
    return result


def environment_versions() -> dict:
    return {name: version(name) for name in ("torch", "transformers", "numpy", "tokenizers",
                                              "jsonschema", "huggingface-hub", "safetensors")}


def validate_candidate(contract: dict) -> None:
    check_seal(contract)
    if (contract.get("arm") != "H" or contract.get("prompt_patches")
            or contract.get("synthetic_examples") not in (False, None)
            or contract["runtime_identity"].get("engine_version") != "0.34.4"
            or contract["effective_model"]["source"] != "parent"):
        raise P8Error("P8.5 is restricted to the selected, unpatched local H candidate")
    parent = contract["parent_model_contract"]
    frozen = load_long_context_contract()
    if (parent["model"] != frozen["model"] or parent["decoding"] != frozen["decoding"]
            or contract["prompt_version"] != frozen["prompt_version"]
            or contract["input_policy"] != "hybrid-boolean-top3-numeric-full"
            or contract["effective_model"]["ollama_manifest_sha256"]
            != frozen["model"]["ollama_manifest_sha256"]):
        raise P8Error("Selected model, original prompt or input grouping changed")
    for key, value in parent["decoding"].items():
        if contract["parameters"].get(key) != value:
            raise P8Error("Selected candidate decoding changed")


def _holdout_artifacts(manifest: dict, *, synthetic: bool) -> list[dict]:
    artifacts = [a for a in manifest["artifacts"] if a["partition"] == "secondary_holdout"]
    if len(artifacts) != 2 or {a["kind"] for a in artifacts} != {"evidence", "gold"}:
        raise P8Error("Exactly two original sealed holdout artifacts are required")
    for artifact in artifacts:
        path = Path(artifact["path"])
        _no_symlinks(path)
        if not synthetic and path.parent != holdout_state_root() / "sealed-partitions":
            raise P8Error("Holdout artifact is outside the original fixed vault")
        info = path.stat()  # Metadata only. Do not hash or open protected content here.
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1):
            raise P8Error("Holdout input metadata is not owner-only and regular")
    return copy.deepcopy(sorted(artifacts, key=lambda a: a["kind"]))


def freeze_plan(manifest: dict, contract: dict, *, output_root: Path,
                preflight: dict, dependencies: list[dict], source_commit: str,
                endpoint: str = "http://127.0.0.1:11435", synthetic: bool = False) -> dict:
    require_development_ready(manifest, synthetic=synthetic)
    validate_candidate(contract)
    if not synthetic and contract["self_sha256"] != SELECTED_READER_SELF_SHA256:
        raise P8Error("The selected sealed H replicate contract is required")
    check_pin(contract["access_pin"], manifest, kind="self")
    check_seal(preflight)
    required_checks = ("full_tests_passed", "synthetic_batch_passed", "live_synthetic_replay_passed",
                       "public_data_guard_passed", "installed_source_verified")
    if not all(preflight.get(name) is True for name in required_checks):
        raise P8Error("Exposure prerequisites have not all passed")
    if not synthetic and (preflight.get("ci_commit") != source_commit
                          or preflight.get("ci_conclusion") != "success"):
        raise P8Error("Hosted CI must pass on the exact execution commit")
    if not synthetic and not dependencies:
        raise P8Error("Public runtime and model files must be pinned")
    OllamaLoopbackClient(endpoint)  # Reject non-loopback/proxy/cloud endpoints before any read.
    history_names = ("P8_EXECUTION_STATUS.md", "P8_E1_IMPLEMENTATION_REVIEW.md",
                     "P8_RAG_READER_ABLATION_PROPOSAL.md", "P8_ERROR_DRIVEN_PROMPT_LOOP.md")
    history = [{"path": str(Path.cwd() / "docs" / name),
                "file_pin": _file_pin(Path.cwd() / "docs" / name)} for name in history_names]
    return seal({
        "p8_holdout_plan_version": VERSION, "stage": "final_frozen", "synthetic": synthetic,
        "partition": "secondary_holdout", "arms": list(ARMS), "selected_candidate": "H.V1",
        "access_manifest_pin": make_pin(manifest, "self", self_field="self_sha256"),
        "reader_contract": copy.deepcopy(contract),
        "reader_contract_pin": make_pin(contract, "self", self_field="self_sha256"),
        "protected_artifacts": _holdout_artifacts(manifest, synthetic=synthetic),
        "policy_pins": policy_pins(), "implementation": implementation_inventory(),
        "dependencies": copy.deepcopy(dependencies), "source_commit": source_commit,
        "source_repository": str(Path.cwd().resolve()),
        "environment": environment_versions() if not synthetic else {},
        "preflight": copy.deepcopy(preflight),
        "development_history": {"documents": history,
                                "scope": "E0, E1, reader/E2, and all three prompt rounds",
                                "frozen_documentary_record_no_new_validation_run": True},
        "endpoint": endpoint,
        "endpoint_deviation": "isolated_loopback_port_only; model_requests_unchanged",
        "failure_policy": dict(FAILURE_POLICY), "statistics": dict(STATISTICS),
        "output_root": str(output_root.absolute()),
        "validation_reference": {"raw_typed_exact_match": 265 / 345,
                                 "safety_typed_exact_match": 234 / 345,
                                 "interpretation": "development_selected_not_an_unbiased_control"},
        "public_fields": sorted(PUBLIC_FIELDS),
    })


def check_plan(plan: dict, manifest: dict, *, synthetic: bool = False) -> None:
    check_seal(plan)
    require_development_ready(manifest, synthetic=synthetic)
    if (plan.get("p8_holdout_plan_version") != VERSION or plan.get("synthetic") != synthetic
            or plan.get("stage") != "final_frozen" or plan.get("partition") != "secondary_holdout"
            or plan.get("arms") != ARMS or plan.get("selected_candidate") != "H.V1"
            or plan.get("failure_policy") != FAILURE_POLICY or plan.get("statistics") != STATISTICS
            or plan.get("public_fields") != sorted(PUBLIC_FIELDS)):
        raise P8Error("Final batch identity or policy changed")
    check_pin(plan["access_manifest_pin"], manifest, kind="self")
    check_pin(plan["reader_contract_pin"], plan["reader_contract"], kind="self")
    validate_candidate(plan["reader_contract"])
    if not synthetic and plan["reader_contract"]["self_sha256"] != SELECTED_READER_SELF_SHA256:
        raise P8Error("The selected sealed H replicate contract changed")
    if plan["protected_artifacts"] != _holdout_artifacts(manifest, synthetic=synthetic):
        raise P8Error("Registered holdout inputs changed")
    if plan["implementation"] != implementation_inventory() or plan["policy_pins"] != policy_pins():
        raise P8Error("Execution source or frozen policy differs from the final plan")
    if not synthetic:
        repo = Path(plan["source_repository"])
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        if (head != plan["source_commit"]
                or implementation_inventory(repo / "src" / "clinical_matcher") != plan["implementation"]):
            raise P8Error("Worktree, installed source and execution commit must agree")
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "src"], cwd=repo).returncode
        untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", "src"],
                                            cwd=repo, text=True)
        if dirty or untracked.strip():
            raise P8Error("Execution source must be committed before exposure")
        if plan["environment"] != environment_versions():
            raise P8Error("Execution environment changed")
        for item in plan["dependencies"]:
            if item["file_pin"] != _file_pin(Path(item["path"])):
                raise P8Error("Public runtime or model file changed")
        for item in plan["development_history"]["documents"]:
            if item["file_pin"] != _file_pin(Path(item["path"])):
                raise P8Error("Frozen development history changed")


class _ConsumedPartitionReader:
    """One in-process capability, created only by the just-consumed batch."""

    def __init__(self, plan: dict, manifest: dict, event: dict):
        check_seal(event)
        persisted = read_metadata(holdout_state_root() / "secondary-holdout-consumed.json")
        if event != persisted or event.get("status") != "consumed_before_content_access":
            raise P8Error("A durable original consumption event is required")
        check_pin(event["final_plan_pin"], plan, kind="self")
        if event["reservation_pin"] != manifest["reservation_pin"]:
            raise P8Error("Exposure reservation mismatch")
        self.plan, self.manifest, self.read_kinds = plan, manifest, set()
        self.raw_persisted = False

    def read(self, kind: str) -> dict:
        if kind not in {"evidence", "gold"} or kind in self.read_kinds:
            raise P8Error("Partition read is not part of the original batch")
        if kind == "gold" and not self.raw_persisted:
            raise P8Error("Gold remains closed until raw artifacts are persisted")
        artifact = next(a for a in self.plan["protected_artifacts"] if a["kind"] == kind)
        self.read_kinds.add(kind)  # A failed read must not be retried in this session.
        payload = _read_private_bytes(Path(artifact["path"]))
        check_pin(artifact["file_pin"], payload, kind="file")
        result = _strict_json(payload)
        check_pin(artifact["content_pin"], result, kind="content")
        check_seal(result)
        validate_document(result, "schemas/p8-partition-artifact-1.0.0.schema.json")
        if (result["partition"] != "secondary_holdout" or result["kind"] != kind
                or result["reservation_pin"] != self.manifest["reservation_pin"]
                or result["source_split_pin"] != self.manifest["source_split_pin"]
                or {r["patient_id"] for r in result["rows"]}
                != set(self.manifest["populations"]["secondary_holdout"])):
            raise P8Error("Sealed partition identity or population mismatch")
        return result


def retrieve(patients: list[dict], encoder) -> dict:
    """Use unchanged P3 patient-local scoring; never accept a gold argument."""
    catalog = load_question_catalog()
    records = [{**evidence, "patient_id": p["patient_id"]}
               for p in patients for evidence in p["evidence"]]
    bm25_contract = load_bm25_contract()
    bm25 = BM25PatientRetriever(records, k1=bm25_contract["bm25"]["k1"],
                                b=bm25_contract["bm25"]["b"])
    dense = DensePatientRetriever(records, encoder.encode_documents([r["text"] for r in records]))
    vectors = encoder.encode_queries([q["source_question"] for q in catalog["questions"]])
    result = {}
    for patient in patients:
        pid = patient["patient_id"]
        starts = {e["evidence_id"]: e["source_span"]["start"] for e in patient["evidence"]}
        for question, vector in zip(catalog["questions"], vectors):
            lexical = tuple(r for r in bm25.rank(pid, question["source_question"]) if r.score > 0)
            semantic = dense.rank_vector(pid, vector)
            fused = reciprocal_rank_fusion(lexical, semantic, starts, rank_constant=60, top_k=3)
            result[(pid, question["question_id"])] = [(e["evidence_id"], e["score"]) for e in fused]
    return result


def _latencies(logs: list[dict]) -> tuple[float | None, float | None]:
    from .p8_e1 import _percentile
    values = [r["wall_seconds"] for r in logs if not r.get("cold_start")
              and r["outcome"] != "context_over_budget"]
    return ((_percentile(values, .5), _percentile(values, .95)) if values else (None, None))


def public_projection(evaluations: dict, logs: list[dict]) -> dict:
    p50, p95 = _latencies(logs)
    views = {}
    for arm, results in evaluations.items():
        views[arm] = {}
        for view, result in results.items():
            metrics = result["metrics"]
            fields = {"typed_exact_match": metrics["typed_exact_match"],
                      "boolean_macro_f1": metrics["boolean"]["macro_f1"],
                      "numeric_status_macro_f1": metrics["numeric_status"]["macro_f1"],
                      "unknown_count": result["unknown_count"],
                      "request_latency_p50_seconds": p50 if arm != "rules" else None,
                      "request_latency_p95_seconds": p95 if arm != "rules" else None}
            if set(fields) != PUBLIC_FIELDS:
                raise P8Error("Public result must contain exactly the six allowed fields")
            views[arm][view] = fields
    return {"partition": "secondary_holdout", "exposure": "single",
            "candidate": "H.V1", "interpretation": "absolute_fact_extraction_results_only",
            "views": views}


def paired_difference(before: list[dict], after: list[dict], gold: list[dict], ids: list[str]) -> dict:
    labels, left, right = indexed_grid(gold, ids), indexed_grid(before, ids), indexed_grid(after, ids)
    def correct(row, label):
        return row["fact_status"] == label["fact_status"] and row["value"] == label["value"]
    rows = [(pid, int(correct(right[(pid, qid)], label)) - int(correct(left[(pid, qid)], label)))
            for (pid, qid), label in labels.items()]
    interval = clustered_bootstrap(rows, lambda r: r[0], lambda rs: sum(r[1] for r in rs) / len(rs),
                                   samples=STATISTICS["samples"], seed=STATISTICS["seed"])
    return asdict(interval)


def run_batch(plan: dict, manifest: dict, authorization: dict, *, client,
              encoder, synthetic: bool = False) -> dict:
    """The only batch entry. All pre-exposure checks precede slot consumption."""
    check_plan(plan, manifest, synthetic=synthetic)
    check_pin(authorization["final_plan_pin"], plan, kind="self")
    if not authorization.get("owner_explicitly_authorized") or not authorization.get("owner_decision_record_id"):
        raise P8Error("Explicit owner execution instruction is required")
    if holdout_state_root().joinpath("secondary-holdout-consumed.json").exists():
        raise P8Error("Project lifetime holdout exposure already consumed")
    contract = plan["reader_contract"]
    if not synthetic:
        if probe_runtime_identity(client) != contract["runtime_identity"]:
            raise P8Error("Runtime differs from the validation-selected runtime")
        verify_model_digest(client, contract["effective_model"])
    # Exercise both encoder paths before exposing the holdout. No protected data.
    encoder.encode_documents(["Fictional preflight hemoglobin 11.2."])
    encoder.encode_queries(["What is the hemoglobin?"])
    output = Path(plan["output_root"])
    if output.exists():
        raise P8Error("Batch output must be new; resumption is forbidden")
    _no_symlinks(output.absolute())
    private_directory(output)
    write_private(plan, output / "final-plan.json")
    write_private(authorization, output / "owner-authorization.json")
    ledger = EventLedger(output / "events")
    config_pin = make_pin(plan, "self", self_field="self_sha256")
    ledger.append(event="attempt_started", attempt_id="p8-5-single-batch", config_pin=config_pin,
                  details={"stage": "pre_exposure", "arms": ARMS})
    stage, consumed = "consume", False
    batch_started = time.monotonic()
    stage_seconds = {}
    try:
        event = consume_holdout_once(authorization=authorization, access_manifest=manifest,
                                    final_plan=plan, synthetic=synthetic)
        consumed = True
        reader = _ConsumedPartitionReader(plan, manifest, event)
        stage = "read_evidence"
        evidence_doc = reader.read("evidence")
        patients = sorted(evidence_doc["rows"], key=lambda p: p["patient_id"])
        ids = [p["patient_id"] for p in patients]
        evidence = {p["patient_id"]: {e["evidence_id"] for e in p["evidence"]} for p in patients}
        stage = "retrieval"
        stage_started = time.monotonic()
        selection = retrieve(patients, encoder)
        stage_seconds["retrieval"] = time.monotonic() - stage_started
        write_private(seal({"selections": [
            {"patient_id": pid, "question_id": qid, "evidence": selection[(pid, qid)]}
            for pid, qid in sorted(selection)]}), output / "retrieval.json")
        stage = "raw_inference"
        stage_started = time.monotonic()
        catalog, questions = load_question_catalog(), question_index()
        rules, model, logs, patient_times = [], [], [], []
        unload_model(client, contract)
        for pindex, patient in enumerate(patients):
            started = time.monotonic()
            extracted = extract_patient_predictions(patient, catalog, load_deterministic_rule_set())
            rules.extend(extracted)
            patient_rows = []
            for gindex, group in enumerate(request_groups("H")):
                result = run_request(client, contract, patient, group, selection)
                result["log"]["cold_start"] = pindex == 0 and gindex == 0
                write_private(seal(result), output / "requests" / f"{pindex:03d}-{gindex}.json")
                logs.append(result["log"])
                patient_rows.extend(result["rows"])
            model.extend(patient_rows)
            patient_times.append({"patient_id": patient["patient_id"],
                                  "wall_seconds": time.monotonic() - started})
            write_private(seal({"rules": extracted, "H": patient_rows}),
                          output / "raw-patients" / f"{pindex:03d}.json")
        stage_seconds["raw_inference_and_rules"] = time.monotonic() - stage_started
        stage = "arbitration"
        left, right = indexed_grid(model, ids), indexed_grid(rules, ids)
        winner, provenance = [], []
        for key in sorted(left):
            row, source, reason = arbitrate(left[key], right[key], question=questions[key[1]],
                                            evidence_ids=evidence[key[0]], policy_id="V1")
            winner.append(row)
            provenance.append({"patient_id": key[0], "question_id": key[1],
                               "source": source, "reason": reason})
        raw = {"rules": rules, "H": model, "H.V1": winner}
        safety = {arm: project_safety_rows(rows, evidence) for arm, rows in raw.items()}
        write_private(seal({"partition": "secondary_holdout", "raw": raw, "safety": safety,
                            "provenance": provenance, "request_logs": logs,
                            "patient_times": patient_times}), output / "raw-complete.json")
        reader.raw_persisted = True
        ledger.append(event="decision_recorded", attempt_id="p8-5-single-batch", config_pin=config_pin,
                      details={"stage": "all_raw_and_safety_persisted_before_gold"})
        stage = "read_gold"
        gold = reader.read("gold")["rows"]
        stage = "evaluate_once"
        stage_started = time.monotonic()
        evaluations = {arm: {"raw": evaluate_rows(raw[arm], gold, ids, bootstrap=not synthetic),
                             "safety": evaluate_rows(safety[arm], gold, ids, bootstrap=not synthetic)}
                       for arm in ARMS}
        paired = ({view: paired_difference(source["H"], source["H.V1"], gold, ids)
                   for view, source in (("raw", raw), ("safety", safety))} if not synthetic else {})
        stage_seconds["evaluation"] = time.monotonic() - stage_started
        report = seal({"p8_holdout_report_version": VERSION, "partition": "secondary_holdout",
                       "final_plan_pin": config_pin, "population": len(ids), "answer_rows": len(gold),
                       "evaluations": evaluations, "paired_V1_minus_H": paired,
                       "latency_excludes_first_cold_request": True,
                       "latency_shared_by_H_and_H_V1": True,
                       "stage_wall_seconds": stage_seconds,
                       "batch_wall_seconds_before_report_seal": time.monotonic() - batch_started,
                       "compute_cost": {"execution": "local", "rental": "not_applicable",
                                        "external_inference_api": "not_applicable"},
                       "validation_reference": plan["validation_reference"],
                       "development_history": plan["development_history"],
                       "no_historical_v1_control_no_improvement_claim": True})
        stage = "seal_report"
        write_private(report, output / "full-report.json")
        projection = public_projection(evaluations, logs)
        write_private(projection, output / "public-projection.json")
        ledger.append(event="completed", attempt_id="p8-5-single-batch", config_pin=config_pin,
                      details={"report_pin": make_pin(report, "self", self_field="self_sha256")})
        inventory = [{"path": str(p.relative_to(output)), "file_pin": make_pin(p.read_bytes(), "file")}
                     for p in sorted(output.rglob("*.json"))]
        write_private(seal({"status": "completed", "final_plan_pin": config_pin,
                            "exposure_pin": make_pin(event, "self", self_field="self_sha256"),
                            "artifacts": inventory}), output / "batch-complete.json")
        return projection
    except Exception as error:
        # Never stringify exceptions: schema errors can include clinical values.
        consumed = consumed or (holdout_state_root() / "secondary-holdout-consumed.json").exists()
        failure = seal({"status": "terminal_failure" if consumed else "pre_exposure_failure",
                        "stage": stage, "error_type": type(error).__name__, "consumed": consumed})
        write_private(failure, output / "terminal-failure.json")
        ledger.append(event="failed", attempt_id="p8-5-single-batch", config_pin=config_pin,
                      details=failure)
        raise P8Error("P8.5 batch stopped; consult the private terminal record") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="P8.5 original single-exposure holdout batch")
    parser.add_argument("--final-plan", type=Path, required=True)
    parser.add_argument("--access-manifest", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--acknowledge-restricted-data-local-only", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        plan, manifest = read_metadata(args.final_plan), read_metadata(args.access_manifest)
        check_plan(plan, manifest)
        client = OllamaLoopbackClient(plan["endpoint"],
                                     timeout_seconds=plan["reader_contract"]["parameters"]["timeout_seconds"])
        encoder = MedCPTEncoder(load_dense_contract())
        result = run_batch(plan, manifest, read_metadata(args.authorization), client=client, encoder=encoder)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print("P8.5 rejected or stopped. No clinical content is displayed; inspect private metadata.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
