"""P8.3 review-stage prompt, example selection and response boundary.

This module has no network client or inference command. Public resources and
synthetic inputs may be used before review. Real example reads require a
source-bound, persisted, dual-reviewed plan through the P8 partition registry.
Approving that plan does not authorize holdout access or a model run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from importlib.resources import files
from pathlib import Path

from .apixaban_contract import known_fact_allows_empty_evidence, load_question_catalog
from .apixaban_deterministic import extract_question_prediction, load_deterministic_rule_set
from .apixaban_structured_llm import load_long_context_contract
from .p8_e0 import indexed_grid, validate_typed_row
from .p8_safety import (
    P8Error, _strict_json, canonical_bytes, check_pin, check_seal, make_pin,
    read_development_artifact, read_metadata, require_development_ready, seal,
)


RESOURCE = "resources/p8-prompt-v2-review-1.0.0.json"
ANSWER_FIELDS = {"question_id", "question_type", "supporting_quote", "fact_status",
                 "value", "unit", "evidence_ids"}


def _resource() -> dict:
    return json.loads(files("clinical_matcher").joinpath(RESOURCE).read_text(encoding="utf-8"))


def review_proposal() -> dict:
    """Self-sealed review artifact; a digest is not an approval or run freeze."""
    resource = _resource()
    return seal({
        "p8_prompt_review_version": "1.0.0", "status": "pending_dual_review",
        "inference_authorized": False, "proposal": resource,
        "resource_file_pin": make_pin(files("clinical_matcher").joinpath(RESOURCE).read_bytes(), "file"),
        "catalog_pin": make_pin(load_question_catalog(), "self", self_field="catalog_sha256"),
        "support_rule_pin": make_pin(load_deterministic_rule_set(), "content"),
        "parent_model_contract_pin": make_pin(load_long_context_contract(), "content"),
    })


def validate_proposal(proposal: dict) -> None:
    check_seal(proposal)
    if proposal != review_proposal():
        raise P8Error("Prompt proposal differs from the reviewed resource")


def _questions(question_ids: list[str]) -> list[dict]:
    catalog = load_question_catalog()["questions"]
    selected = [q for q in catalog if q["question_id"] in question_ids]
    if not question_ids or [q["question_id"] for q in selected] != question_ids:
        raise P8Error("Questions must be unique catalog entries in catalog order")
    return selected


def question_groups(mode: str) -> list[list[str]]:
    sizes = {"v2": [1] * 23, "v2b": [5, 5, 5, 4, 4], "v2-a4": [1] * 23,
             "v2-a24": [23]}
    if mode not in sizes:
        raise P8Error("Undeclared prompt mode")
    ids = [q["question_id"] for q in load_question_catalog()["questions"]]
    groups, offset = [], 0
    for size in sizes[mode]:
        groups.append(ids[offset:offset + size])
        offset += size
    return groups


def _group(question_ids: list[str], mode: str) -> list[dict]:
    if question_ids not in question_groups(mode):
        raise P8Error("Request differs from the predeclared question grouping")
    return _questions(question_ids)


def _evidence(patient: dict) -> dict[str, str]:
    if not isinstance(patient.get("patient_id"), str) or not patient["patient_id"]:
        raise P8Error("Missing current patient identity")
    result = {}
    for chunk in patient["evidence"]:
        eid, text = chunk.get("evidence_id"), chunk.get("text")
        if not isinstance(eid, str) or not eid or eid.startswith("demo:") or eid in result:
            raise P8Error("Invalid or duplicate current evidence identity")
        if not isinstance(text, str):
            raise P8Error("Current evidence text must be a string")
        result[eid] = text
    return result


OUTPUT_SCHEMA_VERSION = "2.0.1-flat-variants"
# Modes whose variants remove factor F4 (the known-answer quote hard constraint).
QUOTE_OPTIONAL_MODES = ("v2-a4", "v2-a24")
# The batched a24 request fixes each array position to one question through
# `prefixItems`, so the grammar itself enforces exactly one answer per question
# in catalog order (live synthetic probe 2026-09-17: honoured by Ollama 0.34.0;
# `items: false` is rejected by its converter and is therefore not emitted).
POSITIONAL_SCHEMA_MODES = ("v2-a24",)


def output_schema_version(mode: str) -> str:
    return "2.0.2-flat-variants-positional" if mode in POSITIONAL_SCHEMA_MODES else OUTPUT_SCHEMA_VERSION


def _flat_variant(q: dict, status: str, value: dict, *, quote_required: bool,
                  minimum_citations: int, evidence_ids: list[str] | None) -> dict:
    """One complete, self-contained object per (question, status).

    Grammar converters do not propagate an outer `required` list through
    `oneOf` refinements (attempt #1 emitted two-field objects for every
    request), so every constraint lives inside each flat variant.
    """
    limit = _resource()["quote_max_characters"]
    quote = ({"type": "string", "minLength": 1, "maxLength": limit} if quote_required
             else {"type": ["string", "null"], "minLength": 1, "maxLength": limit})
    citations = {"type": "array", "minItems": minimum_citations,
                 "items": ({"type": "string", "enum": list(evidence_ids)} if evidence_ids
                           else {"type": "string", "minLength": 1})}
    return {
        "type": "object", "additionalProperties": False,
        "required": sorted(ANSWER_FIELDS),
        "properties": {
            "question_id": {"const": q["question_id"]},
            "question_type": {"const": q["question_type"]},
            "supporting_quote": quote,
            "fact_status": {"const": status},
            "value": value,
            "unit": {"type": "null"},
            "evidence_ids": citations,
        },
    }


def output_schema(question_ids: list[str], *, mode: str = "v2",
                  evidence_ids: list[str] | None = None) -> dict:
    """Flat complete variants; numeric questions have no absent variant at all."""
    questions = _group(question_ids, mode)
    # Ablation variants removing factor F4 make quotes optional for every
    # status while citation requirements are unchanged.
    quotes = mode not in QUOTE_OPTIONAL_MODES
    per_question = []
    for q in questions:
        default = q["source_criterion_label"] == "med_decisions"
        variants = [_flat_variant(q, "unknown", {"type": "null"}, quote_required=False,
                                  minimum_citations=0, evidence_ids=evidence_ids)]
        if q["question_type"] == "boolean":
            variants.append(_flat_variant(q, "present", {"const": True}, quote_required=quotes,
                                          minimum_citations=1, evidence_ids=evidence_ids))
            variants.append(_flat_variant(q, "absent", {"const": False},
                                          quote_required=quotes and not default,
                                          minimum_citations=0 if default else 1,
                                          evidence_ids=evidence_ids))
        else:
            variants.append(_flat_variant(q, "present", {"type": "number"}, quote_required=quotes,
                                          minimum_citations=1, evidence_ids=evidence_ids))
        per_question.append(variants)
    assessments = {"type": "array", "minItems": len(questions), "maxItems": len(questions)}
    schema = {"type": "object", "additionalProperties": False, "required": ["assessments"],
              "properties": {"assessments": assessments}}
    if mode in POSITIONAL_SCHEMA_MODES:
        assessments["prefixItems"] = [{"oneOf": variants} for variants in per_question]
        _compact_shared_subschemas(schema, per_question)
    else:
        assessments["items"] = {"oneOf": [v for variants in per_question for v in variants]}
    return schema


def _compact_shared_subschemas(schema: dict, per_question: list[list[dict]]) -> None:
    """Move the repeated citation and quote sub-schemas into `$defs`.

    A 23-question positional schema repeats the evidence-id enum in every
    variant (about 35 KB, roughly 10k prompt tokens, in the live synthetic
    replay). `$ref` keeps the grammar identical while the embedded schema and
    the prompt shrink; Ollama 0.34.0 honoured `$defs`/`$ref` in the same probe.
    """
    defs: dict = {}
    def shared(name: str, value: dict) -> dict:
        existing = defs.setdefault(name, value)
        if existing != value:
            raise P8Error("Conflicting shared sub-schema")
        return {"$ref": f"#/$defs/{name}"}
    for variants in per_question:
        for variant in variants:
            props = variant["properties"]
            citations = props["evidence_ids"]
            props["evidence_ids"] = shared(f"citations_min{citations['minItems']}", citations)
            quote = props["supporting_quote"]
            kind = "optional" if isinstance(quote["type"], list) else "required"
            props["supporting_quote"] = shared(f"quote_{kind}", quote)
    schema["$defs"] = dict(sorted(defs.items()))


QUOTE_MATCH_POLICY = "whitespace-nfkc-normalized-verbatim/1.0.0"


def _normalize_quote_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def quote_matches(quote: str, chunk_text: str) -> bool:
    """Verbatim in content: every non-whitespace character in order.

    Runs of whitespace and line breaks are formatting artifacts of the source
    note that the model does not reproduce (attempt #2 pilot: 22/23 real
    requests failed only this check, 9 of them differing solely in
    whitespace). Case, punctuation and wording must still match exactly.
    """
    return _normalize_quote_text(quote) in _normalize_quote_text(chunk_text)


def _check_answer(answer: dict, question: dict, evidence: dict[str, str],
                  *, quote_optional: bool = False) -> bool:
    """Return True when the quote (if any) was verified verbatim."""
    if not isinstance(answer, dict) or set(answer) != ANSWER_FIELDS:
        raise P8Error("Unexpected response fields")
    validate_typed_row(answer, question)
    citations = answer["evidence_ids"]
    if (not isinstance(citations, list) or any(not isinstance(e, str) for e in citations)
            or len(citations) != len(set(citations)) or not set(citations).issubset(evidence)):
        raise P8Error("Invalid current-patient citations")
    quote = answer["supporting_quote"]
    verified = quote is not None
    if quote is not None:
        if (not isinstance(quote, str) or not quote.strip()
                or len(quote) > _resource()["quote_max_characters"]
                or not any(quote_matches(quote, evidence[e]) for e in citations)):
            if not quote_optional:
                raise P8Error("Quote is not a bounded verbatim cited-current-evidence span")
            verified = False  # a4: an unverified quote never invalidates the typed answer
    if answer["fact_status"] != "unknown":
        default = known_fact_allows_empty_evidence(question, answer) and not citations and quote is None
        if not default and (not citations or (quote is None and not quote_optional)):
            raise P8Error("Known answer lacks a quoted current-patient citation")
    return verified


def parse_response(payload: str, *, patient: dict, question_ids: list[str],
                   mode: str = "v2", unverified: set | None = None) -> list[dict]:
    """Strict response acceptance only; no semantic inference, repair or retry.

    Under ablation variant v2-a4 a non-verbatim quote is nulled mechanically
    and its question id is added to `unverified`; the typed answer stands.
    """
    questions, evidence = _group(question_ids, mode), _evidence(patient)
    if not isinstance(payload, str):
        raise P8Error("Model content must be a JSON string")
    document = _strict_json(payload.encode("utf-8"))
    if set(document) != {"assessments"} or not isinstance(document["assessments"], list):
        raise P8Error("Unexpected model response envelope")
    rows = document["assessments"]
    if len(rows) != len(questions) or any(not isinstance(row, dict) for row in rows):
        raise P8Error("Incomplete model response group")
    by_id = {}
    for row in rows:
        qid = row.get("question_id")
        if not isinstance(qid, str) or qid not in question_ids or qid in by_id:
            raise P8Error("Wrong or repeated response question")
        by_id[qid] = row
    optional = mode in QUOTE_OPTIONAL_MODES
    for q in questions:
        if not _check_answer(by_id[q["question_id"]], q, evidence, quote_optional=optional):
            by_id[q["question_id"]]["supporting_quote"] = None
            if unverified is not None:
                unverified.add(q["question_id"])
    return [copy.deepcopy(by_id[q["question_id"]]) for q in questions]


def project_response(payload: str, *, patient: dict, question_ids: list[str],
                     mode: str = "v2") -> tuple[list[dict], str]:
    """Discard quote mechanically; any malformed response abstains for the group."""
    questions = _group(question_ids, mode)
    _evidence(patient)  # Bad caller input is a preflight failure, not a model failure.
    unverified: set = set()
    try:
        answers = parse_response(payload, patient=patient, question_ids=question_ids, mode=mode,
                                 unverified=unverified)
        outcome = "accepted"
    except (P8Error, UnicodeError, OverflowError, RecursionError):
        outcome = "invalid_output"
        answers = [{"question_id": q["question_id"], "question_type": q["question_type"],
                    "fact_status": "unknown", "value": None, "unit": None,
                    "evidence_ids": [], "supporting_quote": None} for q in questions]
    projected = []
    for answer in answers:
        row = copy.deepcopy(answer)
        del row["supporting_quote"]
        unknown = row["fact_status"] == "unknown"
        trace = [f"p8.{mode}.{outcome}"]
        if row["question_id"] in unverified:
            trace.append(f"p8.{mode}.quote_unverified")
        row.update(patient_id=patient["patient_id"], abstained=unknown,
                   abstention_reason=(outcome if outcome == "invalid_output" else "model_unknown") if unknown else None,
                   trace_ids=trace)
        projected.append(row)
    return projected, outcome


def _selection_rank(source_pin: dict, question_id: str, patient_id: str) -> str:
    policy = _resource()["examples"]
    return hashlib.sha256(canonical_bytes([policy["algorithm_version"], policy["salt"],
        source_pin["value"], question_id, patient_id])).hexdigest()


def _typed(answer: dict) -> tuple:
    return answer["fact_status"], answer["value"], answer["unit"]


def _spans(text: str) -> list[tuple[int, int]]:
    # Same boundaries as rules 1.0.0, but keep original offsets/whitespace.
    boundaries, start = [], 0
    for match in re.finditer(r"(?:[\r\n]+|(?<=[.!?;])\s+)", text):
        if text[start:match.start()].strip():
            boundaries.append((start, match.start()))
        start = match.end()
    if text[start:].strip():
        boundaries.append((start, len(text)))
    return boundaries


def _candidate(patient: dict, label: dict, question: dict, rule: dict, rules: dict) -> tuple[dict | None, str]:
    evidence = _evidence(patient)
    if label["fact_status"] == "unknown":
        return None, "source_unknown"
    citations = label.get("evidence_ids")
    if (not isinstance(citations, list) or not citations
            or any(not isinstance(e, str) for e in citations) or not set(citations).issubset(evidence)):
        return None, "source_without_local_citation"
    full = extract_question_prediction(patient, question, rule, rules)
    if _typed(full) != _typed(label) or not set(full["evidence_ids"]) & set(citations):
        return None, "full_evidence_rule_disagrees"
    policy = _resource()["examples"]
    for eid, text in evidence.items():
        if eid not in citations or eid not in full["evidence_ids"]:
            continue
        spans = _spans(text)
        for index, (start, end) in enumerate(spans):
            quote = text[start:end]
            if len(quote) > _resource()["quote_max_characters"]:
                continue
            left = spans[max(0, index - 1)][0]
            right = spans[min(len(spans) - 1, index + 1)][1]
            excerpt = text[left:right]
            if len(excerpt) > policy["excerpt_max_characters"]:
                continue  # Never shorten a too-long context window to make it eligible.
            supported = True
            for value in (excerpt, quote):
                result = extract_question_prediction({"patient_id": patient["patient_id"],
                    "evidence": [{"evidence_id": eid, "text": value}]}, question, rule, rules)
                if _typed(result) != _typed(label) or not result["evidence_ids"]:
                    supported = False
            if supported:
                return {"source_patient_id": patient["patient_id"], "source_evidence_id": eid,
                        "excerpt_start": left, "excerpt_end": right, "quote_start": start,
                        "quote_end": end, "excerpt": excerpt, "supporting_quote": quote,
                        "answer": {key: label[key] for key in ("question_id", "question_type", "fact_status", "value", "unit")}}, "eligible"
    return None, "no_bounded_agreeing_source_window"


def select_examples(evidence_partition: dict, gold_partition: dict) -> dict:
    """Pure selector for already-authorized train-fit partitions; never a loader.

    Caller must use select_registered_examples for real data. This equality
    screen is not clinical adjudication. The answer is copied from source gold.
    """
    for doc, kind in ((evidence_partition, "evidence"), (gold_partition, "gold")):
        check_seal(doc)
        if doc.get("partition") != "train_fit" or doc.get("kind") != kind:
            raise P8Error("Examples require train-fit evidence and gold partitions")
    for field in ("source_split_pin", "reservation_pin"):
        if evidence_partition[field] != gold_partition[field]:
            raise P8Error("Example sources have different split or reservation identities")
    patients = {row["patient_id"]: row for row in evidence_partition["rows"]}
    if not patients or len(patients) != len(evidence_partition["rows"]):
        raise P8Error("Duplicate or empty example population")
    labels = indexed_grid(gold_partition["rows"], list(patients))
    sources = {"evidence": make_pin(evidence_partition, "self", self_field="self_sha256"),
               "gold": make_pin(gold_partition, "self", self_field="self_sha256")}
    source_pin = make_pin(sources, "content")
    rules = load_deterministic_rule_set()
    by_label = {r["source_criterion_label"]: r for r in rules["rules"]}
    examples, audit = {}, []
    for q in load_question_catalog()["questions"]:
        qid, chosen = q["question_id"], []
        ranked = sorted(patients, key=lambda pid: (_selection_rank(source_pin, qid, pid), pid))
        for pid in ranked:
            candidate, reason = _candidate(patients[pid], labels[(pid, qid)], q,
                                           by_label[q["source_criterion_label"]], rules)
            selected = candidate is not None and len(chosen) < _resource()["examples"]["maximum_per_question"]
            audit.append({"patient_id": pid, "question_id": qid, "eligibility": reason,
                          "selected": selected, "selection_reason": "selected" if selected else
                          ("eligible_beyond_limit" if candidate is not None else "ineligible")})
            if selected:
                chosen.append(candidate)
        examples[qid] = chosen
    return seal({"p8_example_set_version": "1.0.0", "partition": "train_fit",
                 "proposal_pin": make_pin(review_proposal(), "self", self_field="self_sha256"),
                 "source_pins": sources, "ranking_source_pin": source_pin,
                 "examples": examples, "selection_audit": audit})


def example_plan(proposal: dict, manifest: dict, decision: dict, *, synthetic: bool = False) -> dict:
    """Metadata-only plan; must be persisted before select_registered_examples."""
    validate_proposal(proposal)
    require_development_ready(manifest, synthetic=synthetic)
    check_seal(decision)
    check_pin(decision["proposal_pin"], proposal, kind="self")
    if (decision.get("owner_approved") is not True or decision.get("proposer_approved") is not True
            or decision.get("scope") != "v2_prompt_and_example_protocol"
            or not isinstance(decision.get("review_record"), str) or not decision["review_record"].strip()):
        raise P8Error("Both roles must approve the exact v2 text and example protocol")
    artifacts = {}
    for aid, kind in (("train_fit.evidence", "evidence"), ("train_fit.gold", "gold")):
        found = [a for a in manifest["artifacts"] if a["artifact_id"] == aid]
        if len(found) != 1 or found[0]["partition"] != "train_fit" or found[0]["kind"] != kind:
            raise P8Error("Missing registered train-fit example source")
        artifacts[aid] = copy.deepcopy(found[0])
    return seal({"p8_example_plan_version": "1.0.0", "proposal": proposal, "decision": decision,
                 "access_pin": make_pin(manifest, "self", self_field="self_sha256"),
                 "sources": artifacts, "inference_authorized": False})


def select_registered_examples(plan_path: Path, manifest: dict, *, synthetic: bool = False) -> dict:
    plan = read_metadata(plan_path)  # Metadata only, never a user-selected clinical path.
    check_seal(plan)
    expected = example_plan(plan["proposal"], manifest, plan["decision"], synthetic=synthetic)
    if plan != expected:
        raise P8Error("Persisted example plan does not match approved inputs")
    evidence = read_development_artifact(manifest, "train_fit.evidence", purpose="examples", synthetic=synthetic)
    gold = read_development_artifact(manifest, "train_fit.gold", purpose="examples", synthetic=synthetic)
    return select_examples(evidence, gold)


def build_messages(patient: dict, question_ids: list[str], example_set: dict, *, mode: str = "v2") -> list[dict]:
    """Complete notes first. No previous request/answer parameter exists."""
    questions = _group(question_ids, mode)
    _evidence(patient)
    check_seal(example_set)
    check_pin(example_set["proposal_pin"], review_proposal(), kind="self")
    if example_set.get("partition") != "train_fit":
        raise P8Error("Demonstrations must be from train-fit")
    demonstrations = []
    for q in questions:
        entries = example_set["examples"][q["question_id"]]
        if len(entries) > _resource()["examples"]["maximum_per_question"]:
            raise P8Error("Too many demonstrations")
        seen = set()
        for index, entry in enumerate(entries):
            pid = entry["source_patient_id"]
            if pid == patient["patient_id"] or pid in seen:
                raise P8Error("Repeated or current-patient demonstration")
            seen.add(pid)
            eid = f"demo:{q['question_id']}:{index + 1}"
            answer = {**entry["answer"], "supporting_quote": entry["supporting_quote"], "evidence_ids": [eid]}
            _check_answer(answer, q, {eid: entry["excerpt"]})
            demonstrations.append({"question_id": q["question_id"],
                                   "evidence": [{"evidence_id": eid, "text": entry["excerpt"]}], "answer": answer})
    resource = _resource()
    system = resource["system_prompt"]
    if mode in ("v2b", "v2-a24"):
        for old, new in (("exactly one supplied question", "each supplied question independently"),
                         ("for the supplied question", "for all supplied questions")):
            if system.count(old) != 1:
                raise P8Error("grouped prompt anchor phrase not found exactly once")
            system = system.replace(old, new)
    if mode in QUOTE_OPTIONAL_MODES:
        pattern = (r"Otherwise,\s+a\s+known\s+answer\s+requires\s+a\s+quote\s+of\s+1\s+to\s+400\s+"
                   r"characters\s+from\s+a\s+cited\s+current\s+evidence\s+chunk\.")
        system, count = re.subn(pattern, "A supporting_quote is optional; use null whenever you "
                                "cannot copy an exact span.", system)
        if count != 1:
            raise P8Error("a4 prompt anchor sentence not found exactly once")
    # One user message retains a literal common prefix through all note bytes.
    notes = json.dumps({"current_patient_evidence": patient["evidence"]}, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":"), allow_nan=False)
    current_ids = [item["evidence_id"] for item in patient["evidence"]]
    suffix = json.dumps({"demonstrations_not_current_patient_evidence": demonstrations,
                        "questions": questions,
                        "output_schema": output_schema(question_ids, mode=mode,
                                                       evidence_ids=current_ids)},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return [{"role": "system", "content": system},
            {"role": "user", "content": notes + "\n" + suffix}]


def timing_mode(patient_seconds: list[float], *, validation_patients: int = 15) -> dict:
    """Pilot includes all 23 slots and retry overhead, no accuracy argument."""
    if (len(patient_seconds) != 23 or validation_patients != 15
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in patient_seconds)):
        raise P8Error("Pilot must cover all 23 requests with finite nonnegative elapsed times")
    estimate = validation_patients * sum(patient_seconds)
    return {"estimated_validation_seconds": estimate, "mode": "v2b" if estimate > 10800 else "v2"}
