"""P8.3/E1 runner: per-question v2 validation inference under a frozen contract.

The runner never prints clinical content; stdout carries aggregates only.
All clinical inputs arrive through the P8.1 registry API, never raw paths.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .apixaban_structured_llm import (
    OllamaLoopbackClient,
    load_long_context_contract,
)
from .p8_e0 import evaluate_rows
from .p8_prompt import (
    OUTPUT_SCHEMA_VERSION,
    QUOTE_MATCH_POLICY,
    build_messages,
    output_schema,
    project_response,
    question_groups,
    review_proposal,
    timing_mode,
    validate_proposal,
)
from .p8_safety import (
    P8Error,
    check_pin,
    check_seal,
    make_pin,
    read_development_artifact,
    read_metadata,
    require_development_ready,
    seal,
    write_private,
)


E1_RUN_VERSION = "1.0.2"
DECISION_SCOPE = "v2_prompt_and_example_protocol"
# Grouping families. The pilot's timing decision only says whether the
# per-question grouping is affordable ("v2") or the grouped fallback is needed
# ("v2b"); the mode the full run must use also carries the ablation variant.
PER_QUESTION_MODES = ("v2", "v2-a4")
GROUPED_MODES = ("v2b",)


def run_mode_for(contract_mode: str, timing_grouping: str) -> str:
    """Mode the full run must use given the pilot's affordability decision.

    A per-question contract keeps its own mode (including its ablation variant)
    while affordable and falls back to the grouped mode otherwise; there is no
    grouped ablation variant, so an over-budget variant pilot refuses the full
    run. A grouped contract is already the fallback and keeps its mode.
    """
    if contract_mode in GROUPED_MODES:
        return contract_mode
    if contract_mode not in PER_QUESTION_MODES:
        raise P8Error("Unknown E1 mode")
    return contract_mode if timing_grouping == "v2" else GROUPED_MODES[0]


RUN_PARAMETERS = {
    "temperature": 0,
    "seed": 17,
    "num_ctx": 32768,
    "num_predict": 4096,
    "keep_alive": "5m",
    "timeout_seconds": 600,
    "concurrency": 1,
    "stream": False,
}
BUDGET_POLICY = {
    # Conservative character-per-token floor for the pre-send estimate. English
    # clinical text runs ~3.5-4 chars/token; 2.0 deliberately over-estimates
    # tokens so a pass here cannot mask a real overflow, which the post-check
    # still catches from the runtime's own prompt_eval_count.
    "precheck_min_chars_per_token": 2.0,
    "over_budget_rule": "precheck_estimate_or_prompt_eval_count_plus_num_predict_exceeds_num_ctx",
}
RETRY_POLICY = {
    "transport_only": True,
    "maximum_retries": 1,
    "wait_seconds": 5,
    "model_content_never_retried": True,
}
PERCENTILE_SOURCE = "per_request_wall_seconds_excluding_flagged_cold_start"


class TransportFailure(RuntimeError):
    """A request that produced no model content at all."""


def v2_dual_review_decision(review_record: str) -> dict:
    if not isinstance(review_record, str) or not review_record.strip():
        raise P8Error("A dual-review decision requires a non-empty review record")
    return seal({
        "p8_v2_decision_version": "1.0.0",
        "scope": DECISION_SCOPE,
        "proposal_pin": make_pin(review_proposal(), "self", self_field="self_sha256"),
        "proposer_approved": True,
        "owner_approved": True,
        "review_record": review_record,
    })


def load_ablation_matrix() -> dict:
    from importlib.resources import files
    import json

    payload = json.loads(
        files("clinical_matcher.resources")
        .joinpath("p8-e1-ablation-matrix-1.0.0.json")
        .read_text(encoding="utf-8")
    )
    check_seal(payload)
    return payload


def build_e1_contract(manifest: dict, example_set: dict, decision: dict, *,
                      mode: str, synthetic: bool = False,
                      runtime_identity: Mapping[str, Any] | None = None) -> dict:
    """Freeze every E1 identity before any inference request is sent."""
    require_development_ready(manifest, synthetic=synthetic)
    check_seal(example_set)
    check_seal(decision)
    proposal = review_proposal()
    validate_proposal(proposal)
    check_pin(decision["proposal_pin"], proposal, kind="self")
    check_pin(example_set["proposal_pin"], proposal, kind="self")
    if decision.get("scope") != DECISION_SCOPE or not (
            decision.get("owner_approved") and decision.get("proposer_approved")):
        raise P8Error("E1 requires the approved dual-review decision")
    if mode not in {"v2", "v2b", "v2-a4"}:
        raise P8Error("Unknown E1 mode")
    parent = load_long_context_contract()
    contract = seal({
        "p8_e1_contract_version": E1_RUN_VERSION,
        "mode": mode,
        "prompt_version": {"v2": "apixaban-23-facts-perq-2.0.0",
                           "v2b": "apixaban-23-facts-grouped-2.0.0",
                           "v2-a4": "apixaban-23-facts-perq-2.0.0-a4"}[mode],
        "ablation_variant": "2.0.0-a4" if mode == "v2-a4" else None,
        "removed_factor": "F4" if mode == "v2-a4" else None,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "quote_match_policy": QUOTE_MATCH_POLICY,
        "proposal_pin": make_pin(proposal, "self", self_field="self_sha256"),
        "decision_pin": make_pin(decision, "self", self_field="self_sha256"),
        "example_set_pin": make_pin(example_set, "self", self_field="self_sha256"),
        "access_pin": make_pin(manifest, "self", self_field="self_sha256"),
        "ablation_matrix_pin": make_pin(load_ablation_matrix(), "self", self_field="self_sha256"),
        "parent_model_contract": {
            "contract_version": parent["contract_version"],
            "model": copy.deepcopy(parent["model"]),
            "runtime": copy.deepcopy(parent["runtime"]),
            "license": copy.deepcopy(parent["license"]),
        },
        "parameters": dict(RUN_PARAMETERS),
        "budget_policy": dict(BUDGET_POLICY),
        "retry_policy": dict(RETRY_POLICY),
        "patient_order": "pseudonym_lexicographic",
        "percentile_source": PERCENTILE_SOURCE,
        "runtime_identity": dict(runtime_identity or {}),
        "engine_version_deviation_from_parent": (
            (runtime_identity or {}).get("engine_version") is not None
            and (runtime_identity or {}).get("engine_version")
            != parent["runtime"]["engine_version"]),
        "synthetic": synthetic,
    })
    return contract


def _estimated_tokens(messages: Sequence[Mapping[str, str]]) -> int:
    characters = sum(len(item["content"]) for item in messages)
    return int(characters / BUDGET_POLICY["precheck_min_chars_per_token"]) + 1


def _abstained_rows(patient: dict, question_ids: list[str], mode: str, outcome: str) -> list[dict]:
    rows, _ = project_response("", patient=patient, question_ids=question_ids, mode=mode)
    for row in rows:
        row["abstention_reason"] = outcome
        row["trace_ids"] = [f"p8.{mode}.{outcome}"]
    return rows


def _chat_once(client, contract: dict, messages: list[dict], schema: dict) -> dict:
    payload = {
        "model": contract["parent_model_contract"]["model"]["ollama_model_name"],
        "messages": messages,
        "stream": RUN_PARAMETERS["stream"],
        "format": schema,
        "keep_alive": RUN_PARAMETERS["keep_alive"],
        "options": {
            "temperature": RUN_PARAMETERS["temperature"],
            "seed": RUN_PARAMETERS["seed"],
            "num_ctx": RUN_PARAMETERS["num_ctx"],
            "num_predict": RUN_PARAMETERS["num_predict"],
        },
    }
    try:
        response = client.chat(payload)
    except Exception as error:  # noqa: BLE001 - transport taxonomy is frozen below.
        raise TransportFailure(str(error.__class__.__name__)) from error
    message = response.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise TransportFailure("missing_model_content")
    return response


def run_request(client, contract: dict, patient: dict, question_ids: list[str],
                example_set: dict, *, sleeper: Callable[[float], None] = time.sleep) -> dict:
    """One frozen request slot; returns rows plus an aggregate-only log entry."""
    mode = contract["mode"]
    messages = build_messages(patient, question_ids, example_set, mode=mode)
    schema = output_schema(question_ids, mode=mode,
                           evidence_ids=[item["evidence_id"] for item in patient["evidence"]])
    estimate = _estimated_tokens(messages)
    limit = RUN_PARAMETERS["num_ctx"] - RUN_PARAMETERS["num_predict"]
    log: dict[str, Any] = {
        "patient_id": patient["patient_id"], "question_ids": list(question_ids),
        "estimated_prompt_tokens": estimate, "retries": 0,
    }
    if estimate > limit:
        log.update(outcome="context_over_budget", wall_seconds=0.0)
        return {"rows": _abstained_rows(patient, question_ids, mode, "context_over_budget"),
                "log": log}
    attempts = 0
    while True:
        attempts += 1
        started = time.monotonic()
        try:
            response = _chat_once(client, contract, messages, schema)
        except TransportFailure as failure:
            if attempts <= RETRY_POLICY["maximum_retries"]:
                log["retries"] += 1
                sleeper(RETRY_POLICY["wait_seconds"])
                continue
            log.update(outcome="transport_failure", wall_seconds=time.monotonic() - started,
                       transport_error=str(failure))
            return {"rows": _abstained_rows(patient, question_ids, mode, "transport_failure"),
                    "log": log}
        wall = time.monotonic() - started
        break
    prompt_tokens = response.get("prompt_eval_count")
    log.update(wall_seconds=wall,
               prompt_eval_count=prompt_tokens,
               eval_count=response.get("eval_count"),
               load_duration_ns=response.get("load_duration"),
               total_duration_ns=response.get("total_duration"))
    if (not isinstance(prompt_tokens, int)
            or prompt_tokens + RUN_PARAMETERS["num_predict"] > RUN_PARAMETERS["num_ctx"]):
        log["outcome"] = "context_over_budget"
        return {"rows": _abstained_rows(patient, question_ids, mode, "context_over_budget"),
                "log": log}
    rows, outcome = project_response(response["message"]["content"], patient=patient,
                                     question_ids=question_ids, mode=mode)
    log["outcome"] = outcome
    return {"rows": rows, "log": log}


def unload_model(client, contract: dict) -> None:
    """Explicit cold-start boundary before the timing pilot."""
    client.chat({"model": contract["parent_model_contract"]["model"]["ollama_model_name"],
                 "messages": [], "keep_alive": 0})


def run_pilot(client, contract: dict, manifest: dict, example_set: dict, *,
              synthetic: bool = False, sleeper: Callable[[float], None] = time.sleep) -> dict:
    """First patient by pseudonym order; all 23 slots; records the mode decision."""
    evidence = read_development_artifact(manifest, "validation.evidence",
                                         purpose="inference", synthetic=synthetic)
    patients = sorted(evidence["rows"], key=lambda row: row["patient_id"])
    pilot_patient = patients[0]
    unload_model(client, contract)
    groups = question_groups(contract["mode"])
    slots, requests = [], []
    for index, group in enumerate(groups):
        result = run_request(client, contract, pilot_patient, group, example_set,
                             sleeper=sleeper)
        result["log"]["cold_start"] = index == 0
        slots.append(result)
        requests.extend([result["log"]["wall_seconds"]] * len(group))
    if len(requests) != 23:
        raise P8Error("Pilot must cover all 23 question slots")
    decision = timing_mode(requests)
    decision["timing_grouping"] = decision["mode"]
    decision["mode"] = run_mode_for(contract["mode"], decision["timing_grouping"])
    return seal({
        "p8_e1_pilot_version": E1_RUN_VERSION,
        "contract_pin": make_pin(contract, "self", self_field="self_sha256"),
        "pilot_patient_id": pilot_patient["patient_id"],
        "slot_logs": [item["log"] for item in slots],
        "rows": [row for item in slots for row in item["rows"]],
        "timing_decision": decision,
    })


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise P8Error("Latency percentile requires at least one request")
    ordered = sorted(values)
    rank = max(1, -(-int(fraction * 100) * len(ordered) // 100))
    return ordered[rank - 1]


def run_e1(client, contract: dict, manifest: dict, example_set: dict, pilot: dict, *,
           synthetic: bool = False, sleeper: Callable[[float], None] = time.sleep) -> dict:
    """Complete validation run. Reuses immutable pilot requests for its patient."""
    require_development_ready(manifest, synthetic=synthetic)
    check_seal(contract)
    check_seal(pilot)
    check_pin(pilot["contract_pin"], contract, kind="self")
    if pilot["timing_decision"]["mode"] != contract["mode"]:
        raise P8Error("Contract mode differs from the pilot timing decision")
    if not any(log.get("outcome") == "accepted" for log in pilot["slot_logs"]):
        # Attempt #1 lesson: a pilot whose every response failed parsing must
        # stop the full run before another request is sent.
        raise P8Error("Pilot produced no accepted response; refusing the full run")
    evidence = read_development_artifact(manifest, "validation.evidence",
                                         purpose="inference", synthetic=synthetic)
    patients = sorted(evidence["rows"], key=lambda row: row["patient_id"])
    if patients[0]["patient_id"] != pilot["pilot_patient_id"]:
        raise P8Error("Pilot patient is not the first patient in frozen order")
    rows: list[dict] = list(copy.deepcopy(pilot["rows"]))
    logs: list[dict] = [dict(item, reused_pilot=True) for item in pilot["slot_logs"]]
    for patient in patients[1:]:
        for group in question_groups(contract["mode"]):
            result = run_request(client, contract, patient, group, example_set,
                                 sleeper=sleeper)
            result["log"]["cold_start"] = False
            result["log"]["reused_pilot"] = False
            rows.extend(result["rows"])
            logs.append(result["log"])
    gold = read_development_artifact(manifest, "validation.gold",
                                     purpose="evaluation", synthetic=synthetic)
    patient_ids = [patient["patient_id"] for patient in patients]
    evaluation = evaluate_rows(rows, gold["rows"], patient_ids, bootstrap=not synthetic)
    # Filter by outcome, never by the truthiness of the duration: a measured
    # 0.0-second request is a valid sample, and dropping it emptied the list
    # intermittently under fast synthetic clients (the tracked flake).
    latency = [log["wall_seconds"] for log in logs
               if not log.get("cold_start")
               and log.get("outcome") != "context_over_budget"
               and isinstance(log.get("wall_seconds"), (int, float))]
    outcomes: dict[str, int] = {}
    for log in logs:
        outcomes[log["outcome"]] = outcomes.get(log["outcome"], 0) + 1
    return seal({
        "p8_e1_run_version": E1_RUN_VERSION,
        "contract_pin": make_pin(contract, "self", self_field="self_sha256"),
        "pilot_pin": make_pin(pilot, "self", self_field="self_sha256"),
        "mode": contract["mode"],
        "rows": rows,
        "evaluation": evaluation,
        "request_outcomes": outcomes,
        "request_count": len(logs),
        "latency_seconds_p50": _percentile(latency, 0.50),
        "latency_seconds_p95": _percentile(latency, 0.95),
        "percentile_source": PERCENTILE_SOURCE,
    })


def open_runtime(contract: dict) -> OllamaLoopbackClient:
    """Verify the live runtime against the E1 contract's own probed identity.

    The engine version is pinned by this contract's probe (a recorded
    deviation field discloses any difference from the parent v1 contract);
    the model weights must still match the inherited parent digest exactly.
    """
    parent = contract["parent_model_contract"]
    client = OllamaLoopbackClient(parent["runtime"]["endpoint"],
                                  timeout_seconds=RUN_PARAMETERS["timeout_seconds"])
    expected = contract["runtime_identity"].get("engine_version")
    live = client.version()
    if not expected or live != expected:
        raise P8Error("Live engine version differs from the contract's probed identity")
    models = client.tags().get("models")
    if not isinstance(models, list):
        raise P8Error("Ollama model list is malformed")
    matching = [m for m in models if m.get("name") == parent["model"]["ollama_model_name"]]
    if len(matching) != 1 or matching[0].get("digest") != parent["model"]["ollama_manifest_sha256"]:
        raise P8Error("Pinned model manifest is missing or has changed")
    return client


def write_run(document: dict, output_directory: Path, name: str) -> Path:
    return write_private(document, output_directory / name)
