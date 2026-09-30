"""P8 round 3: deterministic boolean arbitration over a sealed reader run.

No model is called. A sealed reader run (the round-2 selected base) is
combined row by row with the frozen deterministic-rule predictions under one
of two predeclared policies. Both follow the benchmark's protocol semantics
for boolean questions ("Does the note describe the patient as having X?": not
described means No), which is stricter on accuracy and weaker on safety than
the project's P4.3 abstention semantics. Closed-world absents therefore carry
no citation and become unknown in the P4.3 safety view; both views are always
evaluated and neither replaces the other. Numeric rows are never modified.
"""

from __future__ import annotations

import copy
from collections import Counter

from .apixaban_contract import question_index
from .p8_e0 import evaluate_rows, indexed_grid, project_safety_rows, rule_available
from .p8_safety import (
    P8Error,
    check_seal,
    make_pin,
    read_development_artifact,
    require_development_ready,
    seal,
)


ROUND3_VERSION = "1.0.0"
POLICIES = {
    "V1": ("Model rows stand, except that a boolean present answer without an available rule "
           "present answer becomes a closed-world absent."),
    "V2": ("Boolean rows come from the rules under a closed world (available rule present -> the "
           "rule row; otherwise absent); numeric rows come from the model unchanged."),
}


def policy_document() -> dict:
    return seal({
        "p8_round3_policy_version": ROUND3_VERSION,
        "policies": dict(POLICIES),
        "boolean_semantics": ("benchmark protocol view: a condition the note does not describe is "
                              "absent; closed-world absents carry no citation and are projected to "
                              "unknown by the P4.3 safety view"),
        "numeric_rows": "never modified",
        "model_calls": 0,
    })


def _closed_world_absent(model_row: dict, policy_id: str, reason: str) -> dict:
    return {"patient_id": model_row["patient_id"], "question_id": model_row["question_id"],
            "question_type": "boolean", "fact_status": "absent", "value": False, "unit": None,
            "evidence_ids": [], "abstained": False, "abstention_reason": None,
            "trace_ids": [f"p8.round3.{policy_id}.{reason}"]}


def arbitrate(model_row: dict, rule_row: dict, *, question: dict, evidence_ids: set[str],
              policy_id: str) -> tuple[dict, str, str]:
    """No gold argument. Returns (row, source, reason); numeric rows pass through."""
    if policy_id not in POLICIES:
        raise P8Error("Undeclared round-3 policy")
    if model_row["patient_id"] != rule_row["patient_id"] or model_row["question_id"] != rule_row["question_id"]:
        raise P8Error("Cross-row arbitration is forbidden")
    if question["question_type"] != "boolean":
        return copy.deepcopy(model_row), "model", "numeric_model_unchanged"
    rule_present = (rule_row["fact_status"] == "present"
                    and rule_available(rule_row, question, evidence_ids))
    if policy_id == "V1":
        if model_row["fact_status"] == "present" and not rule_present:
            return (_closed_world_absent(model_row, policy_id, "vetoed_present_without_rule_support"),
                    "policy", "vetoed_present_without_rule_support")
        return copy.deepcopy(model_row), "model", "model_boolean_kept"
    if rule_present:
        return copy.deepcopy(rule_row), "rules", "rule_present"
    return (_closed_world_absent(model_row, policy_id, "closed_world_absent"),
            "policy", "closed_world_absent")


def build_round3(manifest: dict, reader_run: dict, *, policy_id: str,
                 synthetic: bool = False) -> dict:
    require_development_ready(manifest, synthetic=synthetic)
    check_seal(reader_run)
    if policy_id not in POLICIES:
        raise P8Error("Undeclared round-3 policy")
    rules = read_development_artifact(manifest, "rules", purpose="evaluation", synthetic=synthetic)
    evidence_doc = read_development_artifact(manifest, "validation.evidence",
                                             purpose="evaluation", synthetic=synthetic)
    gold = read_development_artifact(manifest, "validation.gold", purpose="evaluation",
                                     synthetic=synthetic)
    evidence = {row["patient_id"]: {item["evidence_id"] for item in row["evidence"]}
                for row in evidence_doc["rows"]}
    patient_ids = sorted(evidence)
    model = indexed_grid(reader_run["rows"], patient_ids)
    rule_rows = indexed_grid(rules["predictions"], patient_ids)
    questions = question_index()
    combined, provenance = [], []
    sources: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for key in sorted(model):
        chosen, source, reason = arbitrate(model[key], rule_rows[key], question=questions[key[1]],
                                           evidence_ids=evidence[key[0]], policy_id=policy_id)
        if questions[key[1]]["question_type"] != "boolean" and chosen != model[key]:
            raise P8Error("Round 3 must not modify numeric rows")
        combined.append(chosen)
        sources[source] += 1
        reasons[reason] += 1
        provenance.append({"patient_id": key[0], "question_id": key[1], "source": source,
                           "reason": reason, "source_row_pin": make_pin(chosen, "content")})
    raw_evaluation = evaluate_rows(combined, gold["rows"], patient_ids, bootstrap=not synthetic)
    safety_rows = project_safety_rows(combined, evidence)
    safety_evaluation = evaluate_rows(safety_rows, gold["rows"], patient_ids,
                                      bootstrap=not synthetic)
    return seal({
        "p8_round3_version": ROUND3_VERSION,
        "partition": "validation",
        "policy_id": policy_id,
        "policy_pin": make_pin(policy_document(), "self", self_field="self_sha256"),
        "reader_run_pin": make_pin(reader_run, "self", self_field="self_sha256"),
        "rules_pin": make_pin(rules, "content"),
        "base": {"arm": reader_run.get("arm"), "prompt_version": reader_run.get("prompt_version"),
                 "typed_exact_match": reader_run["evaluation"]["metrics"].get("typed_exact_match"),
                 "unknown_count": reader_run["evaluation"].get("unknown_count")},
        "rows": combined,
        "safety_rows": safety_rows,
        "provenance": provenance,
        "source_counts": dict(sources),
        "reason_counts": dict(reasons),
        "evaluation_raw": raw_evaluation,
        "evaluation_safety": safety_evaluation,
    })


def aggregates(document: dict) -> dict:
    return {
        "policy": document["policy_id"],
        "base": document["base"],
        "raw_typed_exact_match": document["evaluation_raw"]["metrics"].get("typed_exact_match"),
        "safety_typed_exact_match": document["evaluation_safety"]["metrics"].get("typed_exact_match"),
        "raw_unknown": document["evaluation_raw"]["unknown_count"],
        "safety_unknown": document["evaluation_safety"]["unknown_count"],
        "source_counts": document["source_counts"],
        "reason_counts": document["reason_counts"],
    }
