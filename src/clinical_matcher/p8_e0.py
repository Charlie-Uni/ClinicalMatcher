"""Frozen raw-only E0 arbitration and P1.5 metrics over isolated inputs."""

from __future__ import annotations

import copy
import json
import math
import platform
from importlib.resources import files
from importlib.metadata import version
from pathlib import Path
from typing import Any, Mapping, Sequence

from .apixaban_abstention import abstention_policy, _reason_for, _project_prediction
from .apixaban_contract import load_question_catalog, question_index, known_fact_allows_empty_evidence
from .apixaban_deterministic import load_deterministic_rule_set
from .apixaban_evaluation import (
    FactEvaluationRecord, exact_source_tolerance_policy, mixed_fact_metrics,
    mixed_fact_bootstrap, validate_prediction_set,
)
from .apixaban_structured_llm import load_structured_llm_contract, load_long_context_contract
from .p8_safety import (EventLedger, P8Error, canonical_bytes, check_pin, check_seal,
                        make_pin, now, read_development_artifact,
                        private_directory, require_development_ready, seal, write_private)
from .splits import canonical_sha256
from .validation import validate_document


ARMS = ("rules", "structured", "long_context")
CANDIDATES = tuple(f"{arm}.A{i}" for arm in ("long_context", "structured") for i in range(1, 5))
POLICY = {
    "input_view": "raw", "rule_set_version": "1.0.0",
    "rule_prediction_schema_version": "1.1.0",
    "raw_config_fields": {"rules": "rule_set_sha256", "structured": "inference_config_sha256",
                          "long_context": "inference_config_sha256"},
    "llm_sources": ["long_context", "structured"], "candidates": list(CANDIDATES),
    "equivalent_typed_policies": [["A2", "A3", "A4"]],
    "rule_available": "known_with_patient_local_citations_or_med_decisions_default",
    "llm_available": "raw_known_without_preprojection",
    "both_unknown": "keep_llm_row", "agreement_source": {"boolean": "llm", "numeric": "rules"},
    "primary_metric": "raw_typed_exact_match", "incumbent": "long_context",
    "tie_order": ["long_context", *CANDIDATES],
    "safety_view": "apply_p4_3_1_1_0_after_raw_winner_fixed",
    "invalid_model_output_retry": False,
    "bootstrap_samples": 1000, "bootstrap_seed": 17,
    "claim": "development_diagnostic",
}


def policy_contract() -> dict:
    return seal({"p8_e0_contract_version": "1.0.0", "policy": POLICY,
                 "catalog_pin": make_pin(load_question_catalog(), "self", self_field="catalog_sha256"),
                 "p4_3_policy_pin": make_pin(abstention_policy(), "content"),
                 "evaluation_policy_pin": make_pin(exact_source_tolerance_policy(), "content"),
                 "raw_configuration_pins": {
                     "rules": make_pin(load_deterministic_rule_set(), "content"),
                     "structured": make_pin(load_structured_llm_contract(), "content"),
                     "long_context": make_pin(load_long_context_contract(), "content"),
                 }})


def validate_policy(contract: dict) -> None:
    validate_document(contract, "schemas/p8-e0-contract-1.0.0.schema.json")
    check_seal(contract)
    if contract != policy_contract():
        raise P8Error("E0 policy differs from the predeclared 1.0.0 candidate set")


def load_policy() -> dict:
    resource = files("clinical_matcher").joinpath("resources/p8-e0-contract-1.0.0.json")
    contract = json.loads(resource.read_text(encoding="utf-8"))
    validate_policy(contract)
    return contract


def validate_typed_row(row: Mapping[str, Any], question: Mapping[str, Any]) -> None:
    if row.get("question_id") != question["question_id"] or row.get("question_type") != question["question_type"]:
        raise P8Error("Typed row question identity mismatch")
    status, value = row.get("fact_status"), row.get("value")
    if row.get("unit") is not None:
        raise P8Error("P8 typed unit must be null")
    if status == "unknown":
        valid = value is None
    elif question["question_type"] == "boolean":
        valid = (status == "present" and value is True) or (status == "absent" and value is False)
    else:
        valid = status == "present" and type(value) in (int, float) and math.isfinite(value)
    if not valid:
        raise P8Error("Illegal P1.1 typed answer")


def rule_available(rule: Mapping[str, Any], question: Mapping[str, Any],
                   evidence_ids: set[str]) -> bool:
    if rule["fact_status"] == "unknown":
        return False
    cited = set(rule["evidence_ids"])
    return cited.issubset(evidence_ids) and (bool(cited) or known_fact_allows_empty_evidence(question, rule))


def arbitrate_row(llm: dict, rule: dict, *, question: dict,
                  evidence_ids: set[str], policy_id: str) -> tuple[dict, str, str]:
    """No gold argument. Return one whole source row; never merge citations."""
    if policy_id not in {"A1", "A2", "A3", "A4"}:
        raise P8Error("Undeclared arbitration policy")
    for row in (llm, rule):
        validate_typed_row(row, question)
    if llm["patient_id"] != rule["patient_id"]:
        raise P8Error("Cross-patient arbitration is forbidden")
    available = rule_available(rule, question, evidence_ids)
    llm_known = llm["fact_status"] != "unknown"
    # A2-A4 are intentional aliases AFTER rule availability is resolved.
    if available and (not llm_known or (policy_id != "A1" and question["question_type"] == "numeric")):
        reason = "llm_abstained_rule_fallback" if not llm_known else "numeric_rule_priority"
        return copy.deepcopy(rule), "rules", reason
    reason = "raw_llm_priority" if llm_known else "neither_available_keep_llm_unknown"
    return copy.deepcopy(llm), "llm", reason


def indexed_grid(rows: Sequence[dict], patient_ids: Sequence[str]) -> dict:
    questions = question_index()
    expected = {(pid, qid) for pid in patient_ids for qid in questions}
    result = {}
    for row in rows:
        key = (row.get("patient_id"), row.get("question_id"))
        if key not in expected or key in result:
            raise P8Error("Duplicate or out-of-population row")
        validate_typed_row(row, questions[key[1]])
        result[key] = row
    if set(result) != expected:
        raise P8Error("Incomplete patient-question grid")
    return result


def evaluate_rows(rows: Sequence[dict], gold: Sequence[dict], patient_ids: Sequence[str],
                  *, bootstrap: bool = True) -> dict:
    predicted = indexed_grid(rows, patient_ids)
    labels = indexed_grid(gold, patient_ids)
    records = tuple(FactEvaluationRecord(
        patient_id=pid, question_id=qid, question_type=labels[(pid, qid)]["question_type"],
        gold_status=labels[(pid, qid)]["fact_status"], predicted_status=predicted[(pid, qid)]["fact_status"],
        gold_value=labels[(pid, qid)]["value"], predicted_value=predicted[(pid, qid)]["value"],
        gold_unit=None, predicted_unit=predicted[(pid, qid)]["unit"], tolerance=0.0,
    ) for pid, qid in sorted(labels))
    return {"metrics": mixed_fact_metrics(records),
            "bootstrap": mixed_fact_bootstrap(records, POLICY["bootstrap_samples"], POLICY["bootstrap_seed"]) if bootstrap else None,
            "unknown_count": sum(row["fact_status"] == "unknown" for row in rows)}


def build_arbitrated_predictions(llm: dict, rules: dict, *, policy_id: str,
                                 evidence: dict, patient_ids: Sequence[str],
                                 llm_arm: str) -> dict:
    if llm_arm not in {"structured", "long_context"}:
        raise P8Error("Undeclared E0 source arm")
    validate_prediction_set(llm)
    validate_prediction_set(rules)
    left = indexed_grid(llm["predictions"], patient_ids)
    right = indexed_grid(rules["predictions"], patient_ids)
    questions = question_index()
    output, provenance = [], []
    for key in sorted(left):
        chosen, source, reason = arbitrate_row(left[key], right[key], question=questions[key[1]],
                                              evidence_ids=evidence[key[0]], policy_id=policy_id)
        source_arm = llm_arm if source == "llm" else "rules"
        output.append(chosen)
        provenance.append({"patient_id": key[0], "question_id": key[1],
                           "source_arm": source_arm, "reason": reason,
                           "source_row_pin": make_pin(chosen, "content")})
    result = seal({"p8_arbitration_version": "1.0.0", "partition": "validation", "view": "raw",
                   "candidate_id": f"{llm_arm}.{policy_id}", "policy_pin": make_pin(policy_contract(), "self", self_field="self_sha256"),
                   "source_pins": {"llm": make_pin(llm, "content"), "rules": make_pin(rules, "content")},
                   "rows": output, "provenance": provenance})
    validate_arbitrated_predictions(result, llm=llm, rules=rules, evidence=evidence,
                                     patient_ids=patient_ids, reproduce=False)
    return result


def validate_arbitrated_predictions(document: dict, *, llm: dict, rules: dict,
                                     evidence: dict, patient_ids: Sequence[str],
                                     reproduce: bool = True) -> None:
    validate_document(document, "schemas/p8-arbitrated-predictions-1.0.0.schema.json")
    check_seal(document)
    check_pin(document["source_pins"]["llm"], llm, kind="content")
    check_pin(document["source_pins"]["rules"], rules, kind="content")
    indexed_grid(document["rows"], patient_ids)
    if reproduce:
        arm, policy_id = document["candidate_id"].split(".")
        expected = build_arbitrated_predictions(llm, rules, policy_id=policy_id, evidence=evidence,
                                                patient_ids=patient_ids, llm_arm=arm)
        if document != expected:
            raise P8Error("Arbitration provenance or source selection does not reproduce")


def select_raw_winner(scores: Mapping[str, float]) -> str:
    if set(scores) != {"long_context", *CANDIDATES}:
        raise P8Error("Winner selection requires the complete frozen candidate set")
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values()):
        raise P8Error("Invalid selection metric")
    return max(POLICY["tie_order"], key=lambda name: scores[name])


def project_safety_rows(rows: Sequence[dict], evidence: dict) -> list[dict]:
    """P4.3 1.1.0 pure row projection; no benchmark/staging loader or gold."""
    questions = question_index()
    return [_project_prediction(row, _reason_for(row, questions[row["question_id"]],
                                                 evidence[row["patient_id"]], False)) for row in rows]


def compare_to_incumbent(rows: Sequence[dict], incumbent: Sequence[dict],
                         gold: Sequence[dict], patient_ids: Sequence[str]) -> dict:
    labels = indexed_grid(gold, patient_ids)
    before, after = indexed_grid(incumbent, patient_ids), indexed_grid(rows, patient_ids)
    def correct(candidate, label):
        return (candidate["fact_status"] == label["fact_status"]
                and candidate["value"] == label["value"] and candidate["unit"] is None)
    return {
        "corrected_count": sum(not correct(before[key], labels[key]) and correct(after[key], labels[key]) for key in labels),
        "introduced_error_count": sum(correct(before[key], labels[key]) and not correct(after[key], labels[key]) for key in labels),
        "decision_changed_count": sum(any(before[key][field] != after[key][field] for field in ("fact_status", "value", "unit")) for key in labels),
    }


def public_e0_summary(report: dict) -> dict:
    """Allowlist-only aggregate projection; never expose row/interval diagnostics."""
    check_seal(report)
    if (report.get("claim") != "development_diagnostic" or report.get("partition") != "validation"
            or set(report["results"]) != {*ARMS, *CANDIDATES}
            or report["raw_winner"] not in {"long_context", *CANDIDATES}):
        raise P8Error("Unexpected E0 report for public projection")
    def project(name, view, result):
        metrics = result["metrics"]
        return {"candidate": name, "view": view,
                "typed_exact_match": metrics["typed_exact_match"],
                "boolean_macro_f1": metrics["boolean"]["macro_f1"],
                "numeric_status_macro_f1": metrics["numeric_status"]["macro_f1"],
                "unknown_count": result["unknown_count"]}
    rows = [project(name, "raw", report["results"][name]) for name in (*ARMS, *CANDIDATES)]
    rows.append(project(report["raw_winner"], "p4_3_1_1_0", report["winner_safety_result"]))
    return {"claim": "development_diagnostic", "partition": "validation",
            "winner": report["raw_winner"], "rows": rows,
            "latency": "not_applicable_no_new_inference",
            "equivalent_typed_policies": [["A2", "A3", "A4"]]}


def freeze_e0_run(manifest: dict, input_ids: Mapping[str, str], *, synthetic: bool = False) -> dict:
    require_development_ready(manifest, synthetic=synthetic)
    load_policy()
    if set(input_ids) != {*ARMS, "evidence", "gold"} or len(set(input_ids.values())) != 5:
        raise P8Error("E0 requires five distinct predeclared isolated inputs")
    by_id = {a["artifact_id"]: a for a in manifest["artifacts"]}
    for role, key in input_ids.items():
        artifact = by_id.get(key)
        kind = "raw_predictions" if role in ARMS else role
        if not artifact or artifact["partition"] != "validation" or artifact["kind"] != kind:
            raise P8Error("E0 input registry role or partition mismatch")
    source = Path(__file__).parent
    # All package code/resources are public inputs. No benchmarks, data or P7
    # run artifacts are opened. Exact bytes make uncommitted-code drift visible.
    implementation = [{"path": str(path.relative_to(source)), "file_pin": make_pin(path.read_bytes(), "file")}
                      for path in sorted(source.rglob("*")) if path.suffix in {".py", ".json"}]
    return seal({"p8_e0_run_version": "1.0.0", "synthetic": synthetic,
                 "access_manifest_pin": make_pin(manifest, "self", self_field="self_sha256"),
                 "policy_pin": make_pin(policy_contract(), "self", self_field="self_sha256"),
                 "input_ids": dict(input_ids), "implementation": implementation,
                 "evaluation_runtime": {
                     "python_version": platform.python_version(),
                     "system": platform.system(), "machine": platform.machine(),
                     "dependency_versions": {name: version(name) for name in
                                             ("jsonschema", "attrs", "referencing", "jsonschema-specifications", "rpds-py")},
                 },
                 "bootstrap": True, "inference_requests": 0})


def validate_e0_run(run: dict, manifest: dict, *, synthetic: bool = False) -> None:
    check_seal(run)
    if run != freeze_e0_run(manifest, run["input_ids"], synthetic=synthetic):
        raise P8Error("Frozen E0 implementation, inputs or configuration changed")


def run_e0(run: dict, manifest: dict, *, output_root: Path,
           ledger: EventLedger, attempt_id: str, synthetic: bool = False) -> dict:
    validate_e0_run(run, manifest, synthetic=synthetic)
    config_pin = make_pin(run, "self", self_field="self_sha256")
    # Reserve output before any input read. A repeated output never overwrites.
    output_root = output_root.absolute()
    if output_root.exists():
        raise P8Error("E0 output directory already exists")
    output_root.mkdir(mode=0o700, parents=True)
    private_directory(output_root)
    ledger.append(event="attempt_started", attempt_id=attempt_id, config_pin=config_pin,
                  details={"stage": "E0", "inference_requests": 0})
    operation = "read_registered_inputs"
    try:
        inputs = {role: read_development_artifact(manifest, key, purpose="evaluation", synthetic=synthetic)
                  for role, key in run["input_ids"].items()}
        populations = manifest["populations"]["validation"]
        evidence = {}
        seen_evidence = set()
        for patient in inputs["evidence"]["rows"]:
            pid = patient["patient_id"]
            if pid in evidence:
                raise P8Error("Duplicate evidence patient")
            ids = [item["evidence_id"] for item in patient["evidence"]]
            if len(ids) != len(set(ids)) or seen_evidence.intersection(ids):
                raise P8Error("Repeated or cross-patient evidence identity")
            evidence[pid] = set(ids)
            seen_evidence.update(ids)
        operation = "validate_frozen_raw_sources"
        source_configs = policy_contract()["raw_configuration_pins"]
        for arm in ARMS:
            prediction = inputs[arm]
            validate_prediction_set(prediction)
            expected_schema = "1.1.0" if arm == "rules" else "1.2.0"
            if prediction["prediction_set_version"] != expected_schema:
                raise P8Error("Raw prediction schema version differs from its source arm")
            config_field = POLICY["raw_config_fields"][arm]
            if prediction.get(config_field) != source_configs[arm]["value"]:
                raise P8Error("E0 requires the pinned raw configuration, never P4.3 input")
            if prediction["split_manifest_sha256"] != manifest["source_split_pin"]["value"]:
                raise P8Error("Raw prediction source split mismatch")
        if len({inputs[arm]["benchmark_sha256"] for arm in ARMS}) != 1:
            raise P8Error("Raw predictions do not share the same benchmark")
        gold = inputs["gold"]["rows"]
        operation = "build_predeclared_candidates"
        outputs = {arm: inputs[arm]["predictions"] for arm in ARMS}
        for candidate in CANDIDATES:
            arm, policy_id = candidate.split(".")
            document = build_arbitrated_predictions(inputs[arm], inputs["rules"], policy_id=policy_id,
                                                    evidence=evidence, patient_ids=populations, llm_arm=arm)
            write_private(document, output_root / f"{candidate}.json")
            outputs[candidate] = document["rows"]
        operation = "evaluate_predeclared_raw_grid"
        results = {name: {**evaluate_rows(rows, gold, populations),
                          "changes_vs_long_context_raw": compare_to_incumbent(rows, outputs["long_context"], gold, populations)}
                   for name, rows in outputs.items()}
        scores = {name: results[name]["metrics"]["typed_exact_match"] for name in ("long_context", *CANDIDATES)}
        winner = select_raw_winner(scores)
        selection = seal({"winner": winner, "scores": scores, "config_pin": config_pin,
                          "selection_view": "raw", "selected_before_safety_projection": True})
        write_private(selection, output_root / "raw-selection.json")
        ledger.append(event="raw_winner_frozen", attempt_id=attempt_id, config_pin=config_pin,
                      details={"selection_pin": make_pin(selection, "self", self_field="self_sha256")})
        operation = "project_fixed_raw_winner"
        safety_rows = project_safety_rows(outputs[winner], evidence)
        safety = seal({"view": "p4_3", "policy_pin": make_pin(abstention_policy(), "content"),
                       "parent_rows_pin": make_pin(outputs[winner], "content"), "rows": safety_rows})
        write_private(safety, output_root / "winner-p4-3.json")
        safety_result = evaluate_rows(safety_rows, gold, populations)
        report = seal({"p8_e0_report_version": "1.0.0", "claim": "development_diagnostic",
                       "partition": "validation", "row_count": len(gold), "patient_count": len(populations),
                       "config_pin": config_pin, "results": results, "raw_winner": winner,
                       "winner_safety_result": safety_result, "inference_requests": 0,
                       "new_model_latency": None, "equivalent_typed_policies": POLICY["equivalent_typed_policies"]})
        write_private(report, output_root / "report.json")
        ledger.append(event="completed", attempt_id=attempt_id, config_pin=config_pin,
                      details={"report_pin": make_pin(report, "self", self_field="self_sha256")})
        return report
    except Exception as error:
        ledger.append(event="failed", attempt_id=attempt_id, config_pin=config_pin,
                      details={"reason": "e0_failed_no_overwrite_no_inference_retry",
                               "operation": operation, "failure_type": type(error).__name__})
        raise P8Error("E0 failed; private partial artifacts and attempt history retained") from None
