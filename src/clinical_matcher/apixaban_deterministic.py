import json
import math
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .apixaban_contract import load_question_catalog
from .apixaban_evaluation import validate_prediction_set
from .apixaban_semantic_scan import validate_scan_inputs
from .apixaban_split import (
    ApixabanSplitError,
    load_apixaban_split_manifest,
    write_private_json,
)
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit


RULE_SET_VERSION = "1.0.0"
RULE_SET_RESOURCE = "resources/apixaban-deterministic-rules-1.0.0.json"
PREDICTION_SET_VERSION = "1.1.0"
MODEL_ID = "clinicalmatcher-deterministic-extractor@1.0.0"
PROMPT_VERSION = "not-applicable:reviewed-rules@1.0.0"
_SENTENCE_BOUNDARY = re.compile(r"(?:[\r\n]+|(?<=[.!?;])\s+)")
_NUMBER = r"(?<![\w.])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?!\w)"


class ApixabanDeterministicError(ValueError):
    """Raised when the deterministic extraction contract is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_deterministic_rule_set() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(RULE_SET_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_deterministic_rule_set(document)
    return document


def validate_deterministic_rule_set(
    document: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    resolved = dict(catalog or load_question_catalog())
    required = {
        "rule_set_version",
        "question_catalog_sha256",
        "development_splits",
        "test_labels_used",
        "method",
        "negation_cues",
        "uncertainty_cues",
        "rules",
    }
    if set(document) != required:
        raise ApixabanDeterministicError("Rule-set fields are incomplete")
    if document["rule_set_version"] != RULE_SET_VERSION:
        raise ApixabanDeterministicError("Unsupported deterministic rule set")
    if document["question_catalog_sha256"] != resolved["catalog_sha256"]:
        raise ApixabanDeterministicError("Rule-set catalog hash mismatch")
    if document["development_splits"] != ["train", "validation"]:
        raise ApixabanDeterministicError(
            "Rules must be developed on train/validation only"
        )
    if document["test_labels_used"] is not False:
        raise ApixabanDeterministicError(
            "Rules influenced by locked test labels are forbidden"
        )
    if document["method"] != "reviewed_question_semantics_and_lexical_rules":
        raise ApixabanDeterministicError("Unexpected rule derivation method")

    expected = {
        question["source_criterion_label"] for question in resolved["questions"]
    }
    rules = document["rules"]
    labels = [rule.get("source_criterion_label") for rule in rules]
    if set(labels) != expected or len(labels) != len(set(labels)):
        raise ApixabanDeterministicError(
            "Rules must cover every catalog question exactly once"
        )
    for cue in (*document["negation_cues"], *document["uncertainty_cues"]):
        _compile(cue)
    for rule in rules:
        allowed = {
            "source_criterion_label",
            "aliases",
            "required_context",
            "default_absent",
        }
        if not set(rule).issubset(allowed) or not rule.get("aliases"):
            raise ApixabanDeterministicError("Malformed lexical rule")
        if rule.get("default_absent", False) and (
            rule["source_criterion_label"] != "med_decisions"
        ):
            raise ApixabanDeterministicError(
                "Only the source-defined medical-decision rule may default absent"
            )
        for pattern in rule["aliases"]:
            _compile(pattern)
        if "required_context" in rule:
            _compile(rule["required_context"])


def _compile(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as error:
        raise ApixabanDeterministicError(
            f"Invalid rule regular expression: {pattern}"
        ) from error


def _segments(patient: Mapping[str, Any]) -> Iterable[Tuple[str, str]]:
    for evidence in patient["evidence"]:
        for segment in _SENTENCE_BOUNDARY.split(evidence["text"]):
            normalized = " ".join(segment.split())
            if normalized:
                yield evidence["evidence_id"], normalized


def _local_context(text: str, start: int, end: int) -> str:
    return text[max(0, start - 100) : min(len(text), end + 100)]


def _ordered_unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(values))


def _unknown_prediction(
    patient_id: str,
    question: Mapping[str, Any],
    reason: str,
    evidence_ids: Sequence[str] = (),
    rule_ids: Sequence[str] = (),
) -> Dict[str, Any]:
    return {
        "patient_id": patient_id,
        "question_id": question["question_id"],
        "question_type": question["question_type"],
        "fact_status": "unknown",
        "value": None,
        "unit": None,
        "abstained": True,
        "abstention_reason": reason,
        "evidence_ids": _ordered_unique(evidence_ids),
        "rule_ids": _ordered_unique(rule_ids),
    }


def _boolean_prediction(
    patient: Mapping[str, Any],
    question: Mapping[str, Any],
    rule: Mapping[str, Any],
    rule_set: Mapping[str, Any],
) -> Dict[str, Any]:
    aliases = tuple(_compile(pattern) for pattern in rule["aliases"])
    negations = tuple(_compile(pattern) for pattern in rule_set["negation_cues"])
    uncertainties = tuple(
        _compile(pattern) for pattern in rule_set["uncertainty_cues"]
    )
    required_context = (
        _compile(rule["required_context"])
        if rule.get("required_context")
        else None
    )
    findings: List[Tuple[str, str, str]] = []
    ambiguous_evidence: List[str] = []
    label = rule["source_criterion_label"].lower()
    for evidence_id, text in _segments(patient):
        for alias_index, alias in enumerate(aliases):
            for match in alias.finditer(text):
                context = _local_context(text, match.start(), match.end())
                rule_id = f"{label}.alias.{alias_index + 1}"
                if any(pattern.search(context) for pattern in uncertainties):
                    ambiguous_evidence.append(evidence_id)
                    continue
                if required_context and not required_context.search(context):
                    ambiguous_evidence.append(evidence_id)
                    continue
                prefix = text[max(0, match.start() - 80) : match.start()]
                suffix = text[match.end() : min(len(text), match.end() + 30)]
                polarity = (
                    "absent"
                    if any(pattern.search(prefix) for pattern in negations)
                    or re.search(r"\b(?:is|was|are|were)?\s*absent\b", suffix, re.I)
                    else "present"
                )
                findings.append((polarity, evidence_id, rule_id))

    polarities = {item[0] for item in findings}
    evidence_ids = [item[1] for item in findings] + ambiguous_evidence
    rule_ids = [item[2] for item in findings]
    if len(polarities) > 1:
        return _unknown_prediction(
            patient["patient_id"],
            question,
            "conflicting_positive_and_negative_mentions",
            evidence_ids,
            rule_ids,
        )
    if not findings:
        if rule.get("default_absent", False) and not ambiguous_evidence:
            return {
                "patient_id": patient["patient_id"],
                "question_id": question["question_id"],
                "question_type": "boolean",
                "fact_status": "absent",
                "value": False,
                "unit": None,
                "abstained": False,
                "abstention_reason": None,
                "evidence_ids": [],
                "rule_ids": [f"{label}.source_defined_default_absent"],
            }
        return _unknown_prediction(
            patient["patient_id"],
            question,
            "ambiguous_or_missing_required_context"
            if ambiguous_evidence
            else "no_lexical_match",
            ambiguous_evidence,
            (),
        )
    status = next(iter(polarities))
    return {
        "patient_id": patient["patient_id"],
        "question_id": question["question_id"],
        "question_type": "boolean",
        "fact_status": status,
        "value": status == "present",
        "unit": None,
        "abstained": False,
        "abstention_reason": None,
        "evidence_ids": _ordered_unique(evidence_ids),
        "rule_ids": _ordered_unique(rule_ids),
    }


def _numeric_mentions(
    patient: Mapping[str, Any], rule: Mapping[str, Any]
) -> List[Tuple[float, str, str]]:
    mentions: List[Tuple[float, str, str]] = []
    label = rule["source_criterion_label"].lower()
    for evidence_id, text in _segments(patient):
        for alias_index, alias_text in enumerate(rule["aliases"]):
            alias = f"(?:{alias_text})"
            patterns = (
                re.compile(
                    rf"{alias}(?:\s*(?:level|score|value|fraction))?"
                    rf"\s*(?:[:=]|is|of)?\s*({_NUMBER})",
                    re.IGNORECASE,
                ),
                re.compile(
                    rf"({_NUMBER})\s*(?:%|percent)?\s*{alias}",
                    re.IGNORECASE,
                ),
            )
            for direction, pattern in enumerate(patterns, start=1):
                for match in pattern.finditer(text):
                    value = float(match.group(1))
                    if math.isfinite(value):
                        mentions.append(
                            (
                                value,
                                evidence_id,
                                f"{label}.numeric.{alias_index + 1}.{direction}",
                            )
                        )
    return mentions


def _numeric_prediction(
    patient: Mapping[str, Any],
    question: Mapping[str, Any],
    rule: Mapping[str, Any],
) -> Dict[str, Any]:
    mentions = _numeric_mentions(patient, rule)
    if not mentions:
        return _unknown_prediction(
            patient["patient_id"], question, "no_numeric_lexical_match"
        )
    aggregation = question["aggregation"]
    selected = (
        min(item[0] for item in mentions)
        if aggregation == "minimum"
        else max(item[0] for item in mentions)
    )
    if question["source_criterion_label"] == "lvef" and selected >= 55:
        selected = 55.0
    selected_mentions = [item for item in mentions if item[0] == selected]
    if question["source_criterion_label"] == "lvef" and selected == 55:
        selected_mentions = [item for item in mentions if item[0] >= 55]
    return {
        "patient_id": patient["patient_id"],
        "question_id": question["question_id"],
        "question_type": "numeric",
        "fact_status": "present",
        "value": selected,
        "unit": None,
        "abstained": False,
        "abstention_reason": None,
        "evidence_ids": _ordered_unique(item[1] for item in selected_mentions),
        "rule_ids": _ordered_unique(item[2] for item in selected_mentions),
    }


def extract_patient_predictions(
    patient: Mapping[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
    rule_set: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    resolved_catalog = dict(catalog or load_question_catalog())
    resolved_rules = dict(rule_set or load_deterministic_rule_set())
    validate_deterministic_rule_set(resolved_rules, resolved_catalog)
    by_label = {
        rule["source_criterion_label"]: rule for rule in resolved_rules["rules"]
    }
    predictions = []
    for question in resolved_catalog["questions"]:
        predictions.append(
            extract_question_prediction(
                patient,
                question,
                by_label[question["source_criterion_label"]],
                resolved_rules,
            )
        )
    return predictions


def extract_question_prediction(
    patient: Mapping[str, Any],
    question: Mapping[str, Any],
    rule: Mapping[str, Any],
    rule_set: Mapping[str, Any],
) -> Dict[str, Any]:
    """Apply one reviewed fact rule to a caller-selected evidence subset."""
    if rule["source_criterion_label"] != question["source_criterion_label"]:
        raise ApixabanDeterministicError("Question and rule labels differ")
    if question["question_type"] == "boolean":
        return _boolean_prediction(patient, question, rule, rule_set)
    if question["question_type"] == "numeric":
        return _numeric_prediction(patient, question, rule)
    raise ApixabanDeterministicError("Unsupported question type")


def build_deterministic_prediction_set(
    frozen_split_path: Path,
    staging_corpus_path: Path,
    split_name: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    if split_name not in {"train", "validation", "test"}:
        raise ApixabanDeterministicError("Unsupported split name")
    for path in (frozen_split_path, staging_corpus_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanDeterministicError(
                f"Restricted baseline input is not owner-only: {path}"
            )
    split = load_apixaban_split_manifest(frozen_split_path)
    if split["status"] != "frozen" or not split["freeze"]["test_locked"]:
        raise ApixabanSplitError("Baseline requires the frozen benchmark split")
    staging = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    validate_scan_inputs(split, staging, staging_corpus_path)
    catalog = load_question_catalog()
    if split["dataset"]["question_catalog_sha256"] != catalog["catalog_sha256"]:
        raise ApixabanDeterministicError("Frozen split catalog hash mismatch")
    rule_set = load_deterministic_rule_set()
    patient_ids = set(split["splits"][split_name]["patient_ids"])
    selected = [
        patient for patient in staging["patients"]
        if patient["patient_id"] in patient_ids
    ]
    if {patient["patient_id"] for patient in selected} != patient_ids:
        raise ApixabanDeterministicError("Split patient membership is incomplete")
    predictions = [
        prediction
        for patient in sorted(selected, key=lambda item: item["patient_id"])
        for prediction in extract_patient_predictions(patient, catalog, rule_set)
    ]
    document = {
        "prediction_set_version": PREDICTION_SET_VERSION,
        "benchmark_sha256": split["dataset"]["benchmark_sha256"],
        "split_manifest_sha256": split["manifest_sha256"],
        "split_name": split_name,
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "rule_set_sha256": canonical_sha256(rule_set),
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "predictions": predictions,
    }
    validate_prediction_set(document, catalog)
    return document


def write_deterministic_prediction_set(
    document: Dict[str, Any], output_path: Path
) -> Path:
    validate_prediction_set(document)
    return write_private_json(document, output_path)
