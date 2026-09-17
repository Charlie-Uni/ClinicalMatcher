"""P8 E3: apply the E0-selected arbitration policy to an E1 prompt-v2 run.

Reuses the frozen E0 row-level policy (`arbitrate_row`) and the P4.3 1.1.0
projection without touching the sealed E0 contract or its declared arms.
Inputs arrive only through the P8.1 registry; stdout carries aggregates.
"""

from __future__ import annotations

from collections import Counter

from .apixaban_contract import question_index
from .p8_e0 import (
    arbitrate_row,
    evaluate_rows,
    indexed_grid,
    policy_contract,
    project_safety_rows,
)
from .p8_safety import (
    P8Error,
    check_seal,
    make_pin,
    read_development_artifact,
    require_development_ready,
    seal,
)


E3_VERSION = "1.0.0"
DEFAULT_POLICY = "A1"


def build_e3_combination(manifest: dict, e1_run: dict, *, policy_id: str = DEFAULT_POLICY,
                         synthetic: bool = False) -> dict:
    """Combine one sealed E1 run with the frozen rules arm under one policy."""
    require_development_ready(manifest, synthetic=synthetic)
    check_seal(e1_run)
    if policy_id != DEFAULT_POLICY:
        # E0 selected A1 on validation; any other policy is a new recorded decision.
        raise P8Error("E3 applies the E0-selected policy only")
    rules = read_development_artifact(manifest, "rules", purpose="evaluation",
                                      synthetic=synthetic)
    evidence_doc = read_development_artifact(manifest, "validation.evidence",
                                             purpose="evaluation", synthetic=synthetic)
    gold = read_development_artifact(manifest, "validation.gold", purpose="evaluation",
                                     synthetic=synthetic)
    evidence = {row["patient_id"]: {item["evidence_id"] for item in row["evidence"]}
                for row in evidence_doc["rows"]}
    patient_ids = sorted(evidence)
    llm = indexed_grid(e1_run["rows"], patient_ids)
    rule_rows = indexed_grid(rules["predictions"], patient_ids)
    questions = question_index()
    combined, provenance = [], []
    sources: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for key in sorted(llm):
        chosen, source, reason = arbitrate_row(llm[key], rule_rows[key],
                                              question=questions[key[1]],
                                              evidence_ids=evidence[key[0]],
                                              policy_id=policy_id)
        combined.append(chosen)
        sources[source] += 1
        reasons[reason] += 1
        provenance.append({"patient_id": key[0], "question_id": key[1],
                           "source_arm": "prompt_v2" if source == "llm" else "rules",
                           "reason": reason, "source_row_pin": make_pin(chosen, "content")})
    raw_evaluation = evaluate_rows(combined, gold["rows"], patient_ids, bootstrap=not synthetic)
    safety_rows = project_safety_rows(combined, evidence)
    safety_evaluation = evaluate_rows(safety_rows, gold["rows"], patient_ids,
                                      bootstrap=not synthetic)
    return seal({
        "p8_e3_version": E3_VERSION,
        "partition": "validation",
        "candidate_id": f"prompt_v2.{policy_id}",
        "policy_pin": make_pin(policy_contract(), "self", self_field="self_sha256"),
        "e1_run_pin": make_pin(e1_run, "self", self_field="self_sha256"),
        "rules_pin": make_pin(rules, "content"),
        "rows": combined,
        "safety_rows": safety_rows,
        "provenance": provenance,
        "source_counts": dict(sources),
        "reason_counts": dict(reasons),
        "evaluation_raw": raw_evaluation,
        "evaluation_safety": safety_evaluation,
        "e1_raw_reference": {
            "typed_exact_match": e1_run["evaluation"]["metrics"].get("typed_exact_match"),
            "unknown_count": e1_run["evaluation"].get("unknown_count"),
        },
    })


def aggregates(document: dict) -> dict:
    return {
        "candidate": document["candidate_id"],
        "raw_typed_exact_match": document["evaluation_raw"]["metrics"].get("typed_exact_match"),
        "safety_typed_exact_match": document["evaluation_safety"]["metrics"].get("typed_exact_match"),
        "raw_unknown": document["evaluation_raw"]["unknown_count"],
        "safety_unknown": document["evaluation_safety"]["unknown_count"],
        "source_counts": document["source_counts"],
        "e1_raw_reference": document["e1_raw_reference"],
    }
