"""P8 reader-input ablation: the frozen v1 long-context prompt over different inputs.

Every arm sends the v1 system prompt, user message layout, output schema and
decoding parameters unchanged (reused from ``apixaban_structured_llm``); only
the evidence handed to the model and the request grouping differ:

* ``A``  every evidence chunk, 23 questions in one request (v1 replay; the
  same-runtime baseline every other arm is compared with);
* ``B3``/``B5`` 23 questions in one request over the patient's top-m chunks,
  ranked by each chunk's best reciprocal-rank-fusion score across the frozen
  per-question selections;
* ``C``  one request per question over that question's frozen top-k chunks;
* ``D``  one request per question over every evidence chunk (the control that
  separates per-question prompting from retrieval).

Retrieval is never recomputed here: arms B and C read the sealed P3 RRF
retrieval document (evidence ids, ranks and scores only) and pin it. Runtime
identity is probed and recorded per contract exactly as E1 does.
"""

from __future__ import annotations

import copy
import json
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence

from .apixaban_contract import load_question_catalog
from .apixaban_structured_llm import (
    StructuredOutputError,
    build_messages,
    detect_hardware,
    load_long_context_contract,
    parse_structured_output,
    structured_output_schema,
)
from .p8_e0 import evaluate_rows
from .p8_e1 import (
    BUDGET_POLICY,
    GROUPED_PRECHECK_MIN_CHARS_PER_TOKEN,
    GROUPED_TIMEOUT_SECONDS,
    PERCENTILE_SOURCE,
    RETRY_POLICY,
    RUN_PARAMETERS,
    TransportFailure,
    _percentile,
)
from .p8_safety import (
    P8Error,
    check_pin,
    check_seal,
    make_pin,
    read_development_artifact,
    require_development_ready,
    seal,
)


READER_VERSION = "1.2.0"
# Round-1 error-driven prompt patches (owner approved P1-P4 on 2026-09-19). Each
# patch appends exactly one sentence to the frozen v1 system prompt; nothing
# else in the prompt changes, so a run's difference from its baseline arm is
# attributable to that sentence. P3 is an input policy (arm "H"), not text.
PROMPT_PATCHES: dict[str, dict[str, str]] = {
    "P1": {"targets": "boolean_false_positive",
           "sentence": ("Present requires that the note records the condition as this patient's "
                        "own diagnosis or history; mentions inside risk scores, differential "
                        "diagnoses, family history, medication indications, screening plans or "
                        "negated statements do not count and fall under the explicit-negation rule.")},
    "P2": {"targets": "numeric_hallucination",
           "sentence": ("A numeric answer must be a number that appears verbatim in the evidence "
                        "for that specific lab or score; never calculate, derive or convert one "
                        "(including CHADS2); when several appear, apply the question's minimum or "
                        "maximum rule.")},
}
# P4: fictional demonstrations appended after the system prompt. Names, values
# and phrasing are invented and describe no real patient; they show the same
# boundaries as P1/P2 by example instead of by rule.
SYNTHETIC_EXAMPLES: list[str] = [
    "Note says: \"Family history: mother with type 2 diabetes. Patient denies diabetes.\" "
    "Diabetes question: absent (explicit denial; family history does not count).",
    "Note says: \"Metformin listed among home medications; no diagnosis of diabetes documented.\" "
    "Diabetes question: unknown (a medication alone is neither explicit support nor negation).",
    "Note says: \"CHA2DS2-VASc calculated for stroke risk; no history of stroke or TIA.\" "
    "Prior stroke question: absent (a risk score mentioning stroke is not a stroke history).",
    "Note says: \"Labs: Hgb 9.8, Plt 210, Cr 1.4.\" Lowest hemoglobin question: present, 9.8, "
    "citing that lab line.",
    "Note says: \"Atrial fibrillation on the problem list; no CHADS2 score recorded.\" "
    "CHADS2 question: unknown (never compute a score that is not written in the note).",
]
SYNTHETIC_EXAMPLES_HEADER = "Fictional examples (invented, not about the current patient):"


def patched_system_prompt(system: str, patches: Sequence[str], examples: bool) -> str:
    """Append approved sentences and, optionally, the fictional examples; never edit v1 text."""
    for patch in patches:
        if patch not in PROMPT_PATCHES:
            raise P8Error("Undeclared prompt patch")
    if len(set(patches)) != len(patches):
        raise P8Error("Repeated prompt patch")
    text = system
    for patch in patches:
        text += " " + PROMPT_PATCHES[patch]["sentence"]
    if examples:
        text += "\n\n" + SYNTHETIC_EXAMPLES_HEADER + "\n" + "\n".join("- " + item for item in SYNTHETIC_EXAMPLES)
    return text
VALIDATION_PATIENTS = 15
# E2 candidate models (owner decision 2026-09-18). Everything else stays frozen:
# the v1 prompt, schema, decoding and input policy. Qwen3 models default to a
# "thinking" mode that must be off for grammar-constrained JSON, so the request
# carries `think: false` for them and the contract records it. The manifest
# digest is probed from the local registry at freeze time and verified before
# every request batch, as the parent model's digest is.
E2_MODELS: dict[str, dict[str, Any]] = {
    "qwen3:14b": {"family": "Qwen3", "provider": "Alibaba Qwen", "parameter_count": "14B",
                  "think": False,
                  "license": {"name": "Apache-2.0", "open_weight": True, "osi_open_source": True}},
    "qwen3:30b-a3b": {"family": "Qwen3", "provider": "Alibaba Qwen",
                      "parameter_count": "30B-A3B (mixture of experts, 3B active)", "think": False,
                      "license": {"name": "Apache-2.0", "open_weight": True, "osi_open_source": True}},
    "qwen3:32b": {"family": "Qwen3", "provider": "Alibaba Qwen", "parameter_count": "32B",
                  "think": False,
                  "license": {"name": "Apache-2.0", "open_weight": True, "osi_open_source": True}},
}
ARMS: dict[str, dict[str, Any]] = {
    "A": {"input_policy": "all-complete-evidence-batched", "batched": True, "top": None,
          "retrieval_required": False,
          "description": "v1 long-context replay: every chunk, 23 questions in one request"},
    "B3": {"input_policy": "patient-top-m-rrf-batched", "batched": True, "top": 3,
           "retrieval_required": True,
           "description": "23 questions in one request over the patient's 3 best-scoring chunks"},
    "B5": {"input_policy": "patient-top-m-rrf-batched", "batched": True, "top": 5,
           "retrieval_required": True,
           "description": "23 questions in one request over the patient's 5 best-scoring chunks"},
    "C": {"input_policy": "per-question-top-k-rrf", "batched": False, "top": 3,
          "retrieval_required": True,
          "description": "one request per question over that question's frozen top-3 chunks"},
    "D": {"input_policy": "all-complete-evidence-per-question", "batched": False, "top": None,
          "retrieval_required": False,
          "description": "one request per question over every chunk (control for C)"},
    # P3: boolean questions read the patient's top-3 chunks (as B3), numeric
    # questions read every chunk; two batched requests per patient.
    "H": {"input_policy": "hybrid-boolean-top3-numeric-full", "batched": True, "top": 3,
          "retrieval_required": True, "groups": "by_question_type",
          "description": "boolean questions over the patient's 3 best-scoring chunks, numeric questions over every chunk"},
}


def question_ids() -> list[str]:
    return [item["question_id"] for item in load_question_catalog()["questions"]]


def request_groups(arm: str) -> list[list[str]]:
    questions = load_question_catalog()["questions"]
    ids = [q["question_id"] for q in questions]
    if ARMS[arm].get("groups") == "by_question_type":
        return [[q["question_id"] for q in questions if q["question_type"] == "boolean"],
                [q["question_id"] for q in questions if q["question_type"] == "numeric"]]
    return [ids] if ARMS[arm]["batched"] else [[qid] for qid in ids]


def catalog_subset(ids: Sequence[str]) -> dict:
    catalog = load_question_catalog()
    wanted = set(ids)
    questions = [q for q in catalog["questions"] if q["question_id"] in wanted]
    if len(questions) != len(ids):
        raise P8Error("Request names an unknown question")
    return {**catalog, "questions": questions}


def load_retrieval_selection(document: Mapping[str, Any]) -> dict:
    """Index a sealed P3 RRF retrieval document: (patient, question) -> ranked ids."""
    results = document.get("results")
    if not isinstance(results, list) or not results or not isinstance(document.get("run_sha256"), str):
        raise P8Error("Retrieval document must carry results and a run hash")
    index: dict = {}
    for item in results:
        key = (item["patient_id"], item["question_id"])
        if key in index:
            raise P8Error("Retrieval document repeats a patient-question pair")
        ranked = sorted(item["selected_evidence"], key=lambda entry: entry["rank"])
        if [entry["rank"] for entry in ranked] != list(range(1, len(ranked) + 1)):
            raise P8Error("Retrieval ranks must be 1..n without gaps")
        index[key] = [(entry["evidence_id"], float(entry["score"])) for entry in ranked]
    return index


def select_evidence(arm: str, patient: Mapping[str, Any], ids: Sequence[str],
                    retrieval_index: Mapping | None) -> list[dict]:
    """Evidence in note order under the arm's input policy; never reads labels."""
    spec = ARMS[arm]
    evidence = list(patient["evidence"])
    if not evidence:
        raise P8Error("Patient has no evidence chunks")
    if spec["top"] is None:
        return evidence
    if spec.get("groups") == "by_question_type":
        types = {q["question_type"] for q in catalog_subset(ids)["questions"]}
        if types == {"numeric"}:
            return evidence  # numeric questions read every chunk
        if types != {"boolean"}:
            raise P8Error("Hybrid arm requests must be single-type groups")
    if retrieval_index is None:
        raise P8Error("This arm requires the frozen retrieval selection")
    position = {item["evidence_id"]: number for number, item in enumerate(evidence)}
    pid = patient["patient_id"]
    if spec["batched"]:
        best: dict[str, float] = {}
        for qid in question_ids():
            for evidence_id, score in retrieval_index[(pid, qid)]:
                if evidence_id not in position:
                    raise P8Error("Retrieval selection cites an unknown evidence id")
                best[evidence_id] = max(best.get(evidence_id, float("-inf")), score)
        ranked = sorted(best, key=lambda evidence_id: (-best[evidence_id], position[evidence_id]))
        keep = set(ranked[:spec["top"]])
    else:
        keep = set()
        for qid in ids:
            for evidence_id, _score in retrieval_index[(pid, qid)][:spec["top"]]:
                if evidence_id not in position:
                    raise P8Error("Retrieval selection cites an unknown evidence id")
                keep.add(evidence_id)
    selected = [item for item in evidence if item["evidence_id"] in keep]
    if not selected:
        raise P8Error("Input policy selected no evidence")
    return selected


def _accelerator() -> str:
    try:
        probe = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                "--format=csv,noheader"], check=False, capture_output=True,
                               text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    line = probe.stdout.strip().splitlines()
    return line[0].strip() if probe.returncode == 0 and line else "unavailable"


def probe_runtime_identity(client) -> dict:
    return {"engine_version": client.version(), "hardware": detect_hardware(),
            "accelerator": _accelerator()}


def effective_model_for(parent: Mapping[str, Any], model: str | None,
                        model_digest: str | None) -> dict:
    """The model a reader contract actually sends requests to."""
    if model is None:
        if model_digest is not None:
            raise P8Error("A model digest requires an E2 model override")
        return {"source": "parent", "ollama_model_name": parent["model"]["ollama_model_name"],
                "ollama_manifest_sha256": parent["model"]["ollama_manifest_sha256"],
                "family": parent["model"]["family"], "provider": parent["model"]["provider"],
                "parameter_count": parent["model"]["parameter_count"], "think": None,
                "license": copy.deepcopy(parent["license"])}
    if model not in E2_MODELS:
        raise P8Error("Undeclared E2 model")
    if not isinstance(model_digest, str) or len(model_digest) < 16:
        raise P8Error("An E2 model override requires the probed manifest digest")
    spec = E2_MODELS[model]
    return {"source": "e2_override", "ollama_model_name": model,
            "ollama_manifest_sha256": model_digest, "family": spec["family"],
            "provider": spec["provider"], "parameter_count": spec["parameter_count"],
            "think": spec["think"], "license": copy.deepcopy(spec["license"])}


def build_reader_contract(manifest: dict, *, arm: str, retrieval: Mapping[str, Any] | None = None,
                          runtime_identity: Mapping[str, Any] | None = None,
                          model: str | None = None, model_digest: str | None = None,
                          patches: Sequence[str] = (), examples: bool = False,
                          synthetic: bool = False) -> dict:
    require_development_ready(manifest, synthetic=synthetic)
    if arm not in ARMS:
        raise P8Error("Undeclared reader arm")
    spec = ARMS[arm]
    parent = load_long_context_contract()
    effective_model = effective_model_for(parent, model, model_digest)
    patched_system_prompt("", patches, examples)  # validates the patch list
    prompt_version = parent["prompt_version"] + "".join("+" + p for p in patches) + ("+P4" if examples else "")
    retrieval_pin = None
    if spec["retrieval_required"]:
        if retrieval is None:
            raise P8Error("This arm requires the frozen retrieval selection")
        load_retrieval_selection(retrieval)
        retrieval_pin = make_pin(retrieval, "content")
    elif retrieval is not None:
        raise P8Error("This arm takes no retrieval input")
    parameters = dict(parent["decoding"])
    parameters["timeout_seconds"] = (GROUPED_TIMEOUT_SECONDS if spec["batched"]
                                     else RUN_PARAMETERS["timeout_seconds"])
    parameters["concurrency"] = 1
    budget = dict(BUDGET_POLICY)
    if spec["batched"]:
        budget["precheck_min_chars_per_token"] = GROUPED_PRECHECK_MIN_CHARS_PER_TOKEN
    identity = dict(runtime_identity or {})
    return seal({
        "p8_reader_contract_version": READER_VERSION,
        "arm": arm,
        "input_policy": spec["input_policy"],
        "batched": spec["batched"],
        "top": spec["top"],
        "description": spec["description"],
        "prompt_version": prompt_version,
        "prompt_patches": list(patches),
        "prompt_patch_sentences": {p: PROMPT_PATCHES[p]["sentence"] for p in patches},
        "synthetic_examples": bool(examples),
        "prompt_source": "clinical_matcher.apixaban_structured_llm.build_messages",
        "output_schema_source": "clinical_matcher.apixaban_structured_llm.structured_output_schema",
        "access_pin": make_pin(manifest, "self", self_field="self_sha256"),
        "catalog_pin": copy.deepcopy(manifest["catalog_pin"]),
        "effective_model": effective_model,
        "model_deviation_from_parent": effective_model["source"] != "parent",
        "retrieval_pin": retrieval_pin,
        "retrieval_run_sha256": retrieval.get("run_sha256") if retrieval else None,
        "parent_model_contract": {
            "contract_version": parent["contract_version"],
            "prompt_version": parent["prompt_version"],
            "model": copy.deepcopy(parent["model"]),
            "runtime": copy.deepcopy(parent["runtime"]),
            "license": copy.deepcopy(parent["license"]),
            "decoding": copy.deepcopy(parent["decoding"]),
            "input_policy": copy.deepcopy(parent["input_policy"]),
        },
        "parameters": parameters,
        "budget_policy": budget,
        "retry_policy": dict(RETRY_POLICY),
        "patient_order": "pseudonym_lexicographic",
        "percentile_source": PERCENTILE_SOURCE,
        "runtime_identity": identity,
        "engine_version_deviation_from_parent": (
            identity.get("engine_version") is not None
            and identity.get("engine_version") != parent["runtime"]["engine_version"]),
        "synthetic": synthetic,
    })


def chat_once(client, contract: dict, messages: list[dict], schema: dict) -> dict:
    """One request to the contract's effective model; E1's transport taxonomy."""
    parameters = contract["parameters"]
    model = contract["effective_model"]
    payload: dict[str, Any] = {
        "model": model["ollama_model_name"],
        "messages": messages,
        "stream": parameters["stream"],
        "format": schema,
        "keep_alive": parameters["keep_alive"],
        "options": {
            "temperature": parameters["temperature"],
            "seed": parameters["seed"],
            "num_ctx": parameters["num_ctx"],
            "num_predict": parameters["num_predict"],
        },
    }
    if model.get("think") is False:
        payload["think"] = False
    try:
        response = client.chat(payload)
    except Exception as error:  # noqa: BLE001 - transport taxonomy is frozen in E1.
        raise TransportFailure(str(error.__class__.__name__)) from error
    message = response.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise TransportFailure("missing_model_content")
    return response


def unload_model(client, contract: dict) -> None:
    """Explicit cold-start boundary before the timing pilot (effective model)."""
    client.chat({"model": contract["effective_model"]["ollama_model_name"],
                 "messages": [], "keep_alive": 0})


def open_reader_runtime(contract: dict):
    """Verify engine version against the contract probe and the effective model digest."""
    from .apixaban_structured_llm import OllamaLoopbackClient
    parent = contract["parent_model_contract"]
    client = OllamaLoopbackClient(parent["runtime"]["endpoint"],
                                  timeout_seconds=contract["parameters"]["timeout_seconds"])
    expected = contract["runtime_identity"].get("engine_version")
    if not expected or client.version() != expected:
        raise P8Error("Live engine version differs from the contract's probed identity")
    verify_model_digest(client, contract["effective_model"])
    return client


def verify_model_digest(client, model: Mapping[str, Any]) -> None:
    models = client.tags().get("models")
    if not isinstance(models, list):
        raise P8Error("Ollama model list is malformed")
    matching = [item for item in models if item.get("name") == model["ollama_model_name"]]
    if len(matching) != 1 or matching[0].get("digest") != model["ollama_manifest_sha256"]:
        raise P8Error("Pinned model manifest is missing or has changed")


def probe_model_digest(client, model: str) -> str:
    models = client.tags().get("models")
    if not isinstance(models, list):
        raise P8Error("Ollama model list is malformed")
    matching = [item for item in models if item.get("name") == model]
    if len(matching) != 1 or not isinstance(matching[0].get("digest"), str):
        raise P8Error("Model is not present in the local registry exactly once")
    return matching[0]["digest"]


def _abstained_rows(arm: str, patient: Mapping[str, Any], ids: Sequence[str], outcome: str) -> list[dict]:
    catalog = catalog_subset(ids)
    return [{"patient_id": patient["patient_id"], "question_id": q["question_id"],
             "question_type": q["question_type"], "fact_status": "unknown", "value": None,
             "unit": None, "evidence_ids": [], "abstained": True, "abstention_reason": outcome,
             "trace_ids": [f"p8.reader.{arm}.{outcome}"]} for q in catalog["questions"]]


def _typed_rows(arm: str, patient: Mapping[str, Any], catalog: Mapping[str, Any],
                assessments: Sequence[Mapping[str, Any]]) -> list[dict]:
    rows = []
    for question, answer in zip(catalog["questions"], assessments):
        unknown = answer["fact_status"] == "unknown"
        rows.append({"patient_id": patient["patient_id"], "question_id": question["question_id"],
                     "question_type": question["question_type"], "fact_status": answer["fact_status"],
                     "value": answer["value"], "unit": answer["unit"],
                     "evidence_ids": list(answer["evidence_ids"]), "abstained": unknown,
                     "abstention_reason": "model_unknown" if unknown else None,
                     "trace_ids": [f"p8.reader.{arm}.accepted"]})
    return rows


def run_request(client, contract: dict, patient: Mapping[str, Any], ids: Sequence[str],
                retrieval_index: Mapping | None, *,
                sleeper: Callable[[float], None] = time.sleep) -> dict:
    arm = contract["arm"]
    catalog = catalog_subset(ids)
    evidence = select_evidence(arm, patient, ids, retrieval_index)
    messages = build_messages(catalog, evidence)
    messages[0]["content"] = patched_system_prompt(messages[0]["content"], contract.get("prompt_patches", ()),
                                                   contract.get("synthetic_examples", False))
    schema = structured_output_schema(catalog, [item["evidence_id"] for item in evidence])
    characters = sum(len(item["content"]) for item in messages)
    estimate = int(characters / contract["budget_policy"]["precheck_min_chars_per_token"]) + 1
    parameters = contract["parameters"]
    limit = parameters["num_ctx"] - parameters["num_predict"]
    log: dict[str, Any] = {
        "patient_id": patient["patient_id"], "question_ids": list(ids),
        "evidence_chunks_input": len(evidence), "evidence_chunks_total": len(patient["evidence"]),
        "input_characters": sum(len(item["text"]) for item in evidence),
        "estimated_prompt_tokens": estimate, "retries": 0,
    }
    if estimate > limit:
        log.update(outcome="context_over_budget", wall_seconds=0.0)
        return {"rows": _abstained_rows(arm, patient, ids, "context_over_budget"), "log": log}
    attempts = 0
    while True:
        attempts += 1
        started = time.monotonic()
        try:
            response = chat_once(client, contract, messages, schema)
        except TransportFailure as failure:
            if attempts <= RETRY_POLICY["maximum_retries"]:
                log["retries"] += 1
                sleeper(RETRY_POLICY["wait_seconds"])
                continue
            log.update(outcome="transport_failure", wall_seconds=time.monotonic() - started,
                       transport_error=type(failure).__name__)
            return {"rows": _abstained_rows(arm, patient, ids, "transport_failure"), "log": log}
        wall = time.monotonic() - started
        break
    prompt_tokens = response.get("prompt_eval_count")
    log.update(wall_seconds=wall, prompt_eval_count=prompt_tokens,
               eval_count=response.get("eval_count"),
               load_duration_ns=response.get("load_duration"),
               total_duration_ns=response.get("total_duration"))
    if not isinstance(prompt_tokens, int) or prompt_tokens + parameters["num_predict"] > parameters["num_ctx"]:
        log["outcome"] = "context_over_budget"
        return {"rows": _abstained_rows(arm, patient, ids, "context_over_budget"), "log": log}
    try:
        content = response["message"]["content"]
        if not isinstance(content, str):
            raise StructuredOutputError("Model content must be a string")
        assessments = parse_structured_output(content, schema, catalog)
    except (StructuredOutputError, KeyError, TypeError, ValueError, RecursionError):
        # v1 policy: a malformed response abstains for the whole request.
        log["outcome"] = "invalid_output"
        return {"rows": _abstained_rows(arm, patient, ids, "invalid_output"), "log": log}
    log["outcome"] = "accepted"
    return {"rows": _typed_rows(arm, patient, catalog, assessments), "log": log}


def _retrieval_index_for(contract: dict, retrieval: Mapping[str, Any] | None) -> Mapping | None:
    if contract["retrieval_pin"] is None:
        if retrieval is not None:
            raise P8Error("This arm takes no retrieval input")
        return None
    if retrieval is None:
        raise P8Error("This arm requires the frozen retrieval selection")
    check_pin(contract["retrieval_pin"], retrieval, kind="content")
    return load_retrieval_selection(retrieval)


def _check_coverage(retrieval_index: Mapping | None, patients: Sequence[Mapping[str, Any]]) -> None:
    if retrieval_index is None:
        return
    missing = [(p["patient_id"], qid) for p in patients for qid in question_ids()
               if (p["patient_id"], qid) not in retrieval_index]
    if missing:
        raise P8Error("Retrieval selection does not cover every validation patient-question pair")


def run_pilot(client, contract: dict, manifest: dict, retrieval: Mapping[str, Any] | None = None, *,
              synthetic: bool = False, sleeper: Callable[[float], None] = time.sleep) -> dict:
    """First patient by pseudonym order, every request of the arm, after an explicit unload."""
    check_seal(contract)
    check_pin(contract["access_pin"], manifest, kind="self")
    retrieval_index = _retrieval_index_for(contract, retrieval)
    evidence = read_development_artifact(manifest, "validation.evidence",
                                         purpose="inference", synthetic=synthetic)
    patients = sorted(evidence["rows"], key=lambda row: row["patient_id"])
    _check_coverage(retrieval_index, patients)
    pilot_patient = patients[0]
    unload_model(client, contract)
    slots = []
    for index, group in enumerate(request_groups(contract["arm"])):
        result = run_request(client, contract, pilot_patient, group, retrieval_index, sleeper=sleeper)
        result["log"]["cold_start"] = index == 0
        slots.append(result)
    walls = [item["log"]["wall_seconds"] for item in slots]
    return seal({
        "p8_reader_pilot_version": READER_VERSION,
        "contract_pin": make_pin(contract, "self", self_field="self_sha256"),
        "arm": contract["arm"],
        "pilot_patient_id": pilot_patient["patient_id"],
        "slot_logs": [item["log"] for item in slots],
        "rows": [row for item in slots for row in item["rows"]],
        "timing_decision": {"estimated_validation_seconds": VALIDATION_PATIENTS * sum(walls),
                            "requests_per_patient": len(slots)},
    })


def run_reader(client, contract: dict, manifest: dict, pilot: dict,
               retrieval: Mapping[str, Any] | None = None, *, synthetic: bool = False,
               sleeper: Callable[[float], None] = time.sleep) -> dict:
    """Complete validation run for one arm; reuses the immutable pilot requests."""
    require_development_ready(manifest, synthetic=synthetic)
    check_seal(contract)
    check_seal(pilot)
    check_pin(pilot["contract_pin"], contract, kind="self")
    if pilot["arm"] != contract["arm"]:
        raise P8Error("Pilot arm differs from the contract")
    if not any(log.get("outcome") == "accepted" for log in pilot["slot_logs"]):
        raise P8Error("Pilot produced no accepted response; refusing the full run")
    retrieval_index = _retrieval_index_for(contract, retrieval)
    evidence = read_development_artifact(manifest, "validation.evidence",
                                         purpose="inference", synthetic=synthetic)
    patients = sorted(evidence["rows"], key=lambda row: row["patient_id"])
    _check_coverage(retrieval_index, patients)
    if patients[0]["patient_id"] != pilot["pilot_patient_id"]:
        raise P8Error("Pilot patient is not the first patient in frozen order")
    rows: list[dict] = list(copy.deepcopy(pilot["rows"]))
    logs: list[dict] = [dict(item, reused_pilot=True) for item in pilot["slot_logs"]]
    for patient in patients[1:]:
        for group in request_groups(contract["arm"]):
            result = run_request(client, contract, patient, group, retrieval_index, sleeper=sleeper)
            result["log"]["cold_start"] = False
            result["log"]["reused_pilot"] = False
            rows.extend(result["rows"])
            logs.append(result["log"])
    gold = read_development_artifact(manifest, "validation.gold",
                                     purpose="evaluation", synthetic=synthetic)
    patient_ids = [patient["patient_id"] for patient in patients]
    evaluation = evaluate_rows(rows, gold["rows"], patient_ids, bootstrap=not synthetic)
    latency = [log["wall_seconds"] for log in logs
               if not log.get("cold_start") and log.get("outcome") != "context_over_budget"
               and isinstance(log.get("wall_seconds"), (int, float))]
    outcomes: dict[str, int] = {}
    for log in logs:
        outcomes[log["outcome"]] = outcomes.get(log["outcome"], 0) + 1
    return seal({
        "p8_reader_run_version": READER_VERSION,
        "contract_pin": make_pin(contract, "self", self_field="self_sha256"),
        "pilot_pin": make_pin(pilot, "self", self_field="self_sha256"),
        "arm": contract["arm"],
        "input_policy": contract["input_policy"],
        "prompt_version": contract["prompt_version"],
        "prompt_patches": list(contract.get("prompt_patches", ())),
        "synthetic_examples": bool(contract.get("synthetic_examples", False)),
        "effective_model": copy.deepcopy(contract["effective_model"]),
        "runtime_identity": copy.deepcopy(contract["runtime_identity"]),
        "rows": rows,
        "evaluation": evaluation,
        "request_outcomes": outcomes,
        "request_count": len(logs),
        "request_logs": logs,
        "evidence_chunks_input_mean": (sum(log["evidence_chunks_input"] for log in logs) / len(logs)),
        "latency_seconds_p50": _percentile(latency, 0.50),
        "latency_seconds_p95": _percentile(latency, 0.95),
        "percentile_source": PERCENTILE_SOURCE,
    })


def aggregates(run: dict) -> dict:
    return {"arm": run["arm"], "prompt": run["prompt_version"], "model": run["effective_model"]["ollama_model_name"],
            "typed_exact_match": run["evaluation"]["metrics"].get("typed_exact_match"),
            "unknown_count": run["evaluation"]["unknown_count"],
            "request_outcomes": run["request_outcomes"],
            "evidence_chunks_input_mean": round(run["evidence_chunks_input_mean"], 2),
            "latency_p50_s": round(run["latency_seconds_p50"], 3),
            "latency_p95_s": round(run["latency_seconds_p95"], 3)}
