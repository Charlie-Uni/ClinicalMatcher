"""Frozen, gold-independent evaluation for public criterion decomposition."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator, FormatChecker

from .decomposition_annotation import (
    validate_annotation,
    validate_concept_catalog,
)
from .decomposition_benchmark import (
    PROTOCOL_VERSION,
    validate_decomposition_selection_document,
)
from .evaluation import BootstrapInterval, clustered_bootstrap
from .fixture import parse_expression
from .models import Criterion, CriterionSource, CriterionType
from .validation import load_schema, validate_document


EVALUATION_VERSION = "1.0.0"
NORMALIZATION_VERSION = "decomposition-normalization/1.0.0"
MATCHING_VERSION = "decomposition-atom-matching/1.0.0"
REPORT_SCHEMA = "schemas/decomposition-evaluation-report-1.0.0.schema.json"
_PREDICTION_KEYS = frozenset({"nct_id", "criterion_id", "expression"})


class DecompositionEvaluationError(ValueError):
    """Raised when evaluator inputs or outputs break the frozen contract."""


@dataclass(frozen=True)
class AtomOccurrence:
    identity: str
    field: str
    polarity: str
    span_start: int
    span_end: int
    condition_id: str


@dataclass(frozen=True)
class _ScoredItem:
    nct_id: str
    criterion_id: str
    status: str
    schema_valid: int
    verifier_load: int
    tree_exact: int
    topology_exact: int
    gold_atoms: int
    predicted_atoms: int
    matched_atoms: int
    concept_gold: int
    concept_predicted: int
    concept_matched: int
    span_exact: int
    span_iou_sum: float
    equivalence_review_queued: int


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _self_hash(document: Mapping[str, Any]) -> str:
    payload = dict(document)
    payload.pop("report_id", None)
    payload.pop("report_sha256", None)
    return _canonical_hash(payload)


def _prediction_expression_validator() -> Draft202012Validator:
    core = load_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/expression",
        "$defs": core["$defs"],
    }
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


_EXPRESSION_VALIDATOR = _prediction_expression_validator()


def _walk_atoms(expression: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    if expression["expression_type"] == "atom":
        yield expression["atom"]
        return
    for child in expression["children"]:
        yield from _walk_atoms(child)


def _schema_errors(expression: Any) -> List[str]:
    errors = sorted(
        _EXPRESSION_VALIDATOR.iter_errors(expression),
        key=lambda item: list(item.path),
    )
    return [error.message for error in errors]


def _canonical_number(value: Any) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise DecompositionEvaluationError("Numeric atom value is invalid") from error
    if not number.is_finite():
        raise DecompositionEvaluationError("Numeric atom value must be finite")
    if number == 0:
        return "0"
    return str(number.normalize())


def _canonical_value(expected: Mapping[str, Any]) -> Dict[str, Any]:
    value_type = expected["value_type"]
    value = expected["value"]
    if value_type == "number":
        value = _canonical_number(value)
    return {"value_type": value_type, "value": value}


def _atom_identity(atom: Mapping[str, Any], negated: bool) -> Dict[str, Any]:
    window = atom.get("time_window")
    canonical_window = None
    if window is not None:
        canonical_window = {
            "days": window["days"],
            "direction": window["direction"],
            "relative_to": window["relative_to"],
        }
    return {
        "field": atom["field"],
        "operator": atom["operator"],
        "expected": _canonical_value(atom["expected"]),
        "unit": atom["expected"].get("unit"),
        "time_window": canonical_window,
        "fact_selection": atom["fact_selection"],
        "polarity": "negated" if negated else "positive",
    }


def _normalize_expression(
    expression: Mapping[str, Any],
    negated: bool = False,
) -> Tuple[Dict[str, Any], Tuple[AtomOccurrence, ...]]:
    expression_type = expression["expression_type"]
    if expression_type == "atom":
        atom = expression["atom"]
        identity = _atom_identity(atom, negated)
        span = atom["provenance"]["source_span"]
        occurrence = AtomOccurrence(
            identity=_canonical_json(identity),
            field=atom["field"],
            polarity=identity["polarity"],
            span_start=span["start"],
            span_end=span["end"],
            condition_id=atom["condition_id"],
        )
        return (
            {"expression_type": "atom", "identity": identity},
            (occurrence,),
        )
    if expression_type == "not":
        return _normalize_expression(expression["children"][0], not negated)

    output_type = expression_type
    if negated:
        output_type = "any" if expression_type == "all" else "all"
    normalized_children: List[Dict[str, Any]] = []
    occurrences: List[AtomOccurrence] = []
    for child in expression["children"]:
        normalized_child, child_occurrences = _normalize_expression(
            child, negated
        )
        if normalized_child["expression_type"] == output_type:
            normalized_children.extend(normalized_child["children"])
        else:
            normalized_children.append(normalized_child)
        occurrences.extend(child_occurrences)
    normalized_children.sort(key=lambda child: _canonical_json(child).encode("utf-8"))
    return (
        {"expression_type": output_type, "children": normalized_children},
        tuple(occurrences),
    )


def normalize_decomposition_expression(
    expression: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the frozen comparison tree without rewriting the input artifact."""
    errors = _schema_errors(expression)
    if errors:
        raise DecompositionEvaluationError(
            "Expression is not schema-valid: " + "; ".join(errors)
        )
    normalized, _ = _normalize_expression(expression)
    return normalized


def _topology(tree: Mapping[str, Any]) -> Dict[str, Any]:
    if tree["expression_type"] == "atom":
        return {"expression_type": "atom"}
    children = [_topology(child) for child in tree["children"]]
    children.sort(key=lambda child: _canonical_json(child).encode("utf-8"))
    return {
        "expression_type": tree["expression_type"],
        "children": children,
    }


def _semantic_prediction_error(
    expression: Mapping[str, Any],
    item: Mapping[str, Any],
    allowed_fields: frozenset[str],
    model_id: str,
    prompt_version: str,
    duplicated_condition_ids: frozenset[str],
) -> Optional[str]:
    for atom in _walk_atoms(expression):
        if atom["condition_id"] in duplicated_condition_ids:
            return "duplicate_condition_id"
        if atom["field"] not in allowed_fields:
            return "field_outside_frozen_catalog"
        provenance = atom["provenance"]
        if provenance["method"] != "llm":
            return "invalid_decomposition_method"
        if (
            provenance.get("model_id") != model_id
            or provenance.get("prompt_version") != prompt_version
        ):
            return "prediction_provenance_mismatch"
        if provenance["source_id"] != item["source_id"]:
            return "source_id_mismatch"
        span = provenance["source_span"]
        if (
            span["end"] > len(item["source_text"])
            or not item["source_text"][span["start"] : span["end"]].strip()
        ):
            return "invalid_source_span"
        expected = atom["expected"]
        if expected["value_type"] == "number" and not math.isfinite(
            expected["value"]
        ):
            return "nonfinite_numeric_value"
        if expected["value_type"] in {"boolean", "string"} and atom[
            "operator"
        ] not in {"==", "!="}:
            return "typed_operator_mismatch"
    try:
        source = CriterionSource(
            source_id=item["source_id"],
            source_text=item["source_text"],
            section=CriterionType(item["criterion_type"]),
            document_version=item["source_record_version"],
        )
        Criterion(
            criterion_id=item["criterion_id"],
            criterion_type=CriterionType(item["criterion_type"]),
            description=item["source_text"],
            source=source,
            expression=parse_expression(dict(expression)),
        )
    except (KeyError, TypeError, ValueError) as error:
        return f"typed_verifier_load_failed:{type(error).__name__}"
    return None


def _span_score(
    gold: Sequence[AtomOccurrence],
    predicted: Sequence[AtomOccurrence],
) -> Tuple[int, float]:
    gold_by_identity: Dict[str, List[AtomOccurrence]] = {}
    predicted_by_identity: Dict[str, List[AtomOccurrence]] = {}
    for occurrence in gold:
        gold_by_identity.setdefault(occurrence.identity, []).append(occurrence)
    for occurrence in predicted:
        predicted_by_identity.setdefault(occurrence.identity, []).append(occurrence)
    exact = 0
    iou_sum = 0.0
    for identity in set(gold_by_identity) & set(predicted_by_identity):
        gold_items = sorted(
            gold_by_identity[identity],
            key=lambda item: (item.span_start, item.span_end, item.condition_id),
        )
        predicted_items = sorted(
            predicted_by_identity[identity],
            key=lambda item: (item.span_start, item.span_end, item.condition_id),
        )
        for gold_item, predicted_item in zip(gold_items, predicted_items):
            exact += int(
                gold_item.span_start == predicted_item.span_start
                and gold_item.span_end == predicted_item.span_end
            )
            intersection = max(
                0,
                min(gold_item.span_end, predicted_item.span_end)
                - max(gold_item.span_start, predicted_item.span_start),
            )
            union = max(gold_item.span_end, predicted_item.span_end) - min(
                gold_item.span_start, predicted_item.span_start
            )
            iou_sum += intersection / union
    return exact, iou_sum


def _concept_counter(atoms: Sequence[AtomOccurrence]) -> Counter[Tuple[str, str]]:
    return Counter((atom.field, atom.polarity) for atom in atoms)


def _score_valid_prediction(
    item: Mapping[str, Any],
    expression: Mapping[str, Any],
) -> _ScoredItem:
    gold_tree, gold_atoms = _normalize_expression(item["expression"])
    predicted_tree, predicted_atoms = _normalize_expression(expression)
    gold_counter = Counter(atom.identity for atom in gold_atoms)
    predicted_counter = Counter(atom.identity for atom in predicted_atoms)
    matched = sum((gold_counter & predicted_counter).values())
    gold_concepts = _concept_counter(gold_atoms)
    predicted_concepts = _concept_counter(predicted_atoms)
    concept_matched = sum((gold_concepts & predicted_concepts).values())
    span_exact, span_iou_sum = _span_score(gold_atoms, predicted_atoms)
    tree_exact = int(gold_tree == predicted_tree)
    return _ScoredItem(
        nct_id=item["nct_id"],
        criterion_id=item["criterion_id"],
        status="valid",
        schema_valid=1,
        verifier_load=1,
        tree_exact=tree_exact,
        topology_exact=int(_topology(gold_tree) == _topology(predicted_tree)),
        gold_atoms=len(gold_atoms),
        predicted_atoms=len(predicted_atoms),
        matched_atoms=matched,
        concept_gold=len(gold_atoms),
        concept_predicted=len(predicted_atoms),
        concept_matched=concept_matched,
        span_exact=span_exact,
        span_iou_sum=span_iou_sum,
        equivalence_review_queued=int(
            not tree_exact and gold_counter == predicted_counter
        ),
    )


def _score_failure(
    item: Mapping[str, Any],
    status: str,
    schema_valid: int,
) -> _ScoredItem:
    _, gold_atoms = _normalize_expression(item["expression"])
    return _ScoredItem(
        nct_id=item["nct_id"],
        criterion_id=item["criterion_id"],
        status=status,
        schema_valid=schema_valid,
        verifier_load=0,
        tree_exact=0,
        topology_exact=0,
        gold_atoms=len(gold_atoms),
        predicted_atoms=0,
        matched_atoms=0,
        concept_gold=len(gold_atoms),
        concept_predicted=0,
        concept_matched=0,
        span_exact=0,
        span_iou_sum=0.0,
        equivalence_review_queued=0,
    )


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return _safe_ratio(2.0 * precision * recall, precision + recall)


def _aggregate(records: Sequence[_ScoredItem]) -> Dict[str, Any]:
    criterion_count = len(records)
    gold_atoms = sum(record.gold_atoms for record in records)
    predicted_atoms = sum(record.predicted_atoms for record in records)
    matched_atoms = sum(record.matched_atoms for record in records)
    atom_precision = _safe_ratio(matched_atoms, predicted_atoms)
    atom_recall = _safe_ratio(matched_atoms, gold_atoms)
    per_item_f1 = []
    for record in records:
        precision = _safe_ratio(record.matched_atoms, record.predicted_atoms)
        recall = _safe_ratio(record.matched_atoms, record.gold_atoms)
        per_item_f1.append(_f1(precision, recall))
    concept_gold = sum(record.concept_gold for record in records)
    concept_predicted = sum(record.concept_predicted for record in records)
    concept_matched = sum(record.concept_matched for record in records)
    concept_precision = _safe_ratio(concept_matched, concept_predicted)
    concept_recall = _safe_ratio(concept_matched, concept_gold)
    return {
        "criterion_count": criterion_count,
        "gold_atom_count": gold_atoms,
        "predicted_atom_count": predicted_atoms,
        "matched_atom_count": matched_atoms,
        "span_matched_atom_count": matched_atoms,
        "normalized_tree_exact_count": sum(r.tree_exact for r in records),
        "normalized_tree_exact_rate": _safe_ratio(
            sum(r.tree_exact for r in records), criterion_count
        ),
        "operator_topology_exact_count": sum(r.topology_exact for r in records),
        "operator_topology_exact_rate": _safe_ratio(
            sum(r.topology_exact for r in records), criterion_count
        ),
        "atom_micro_precision": atom_precision,
        "atom_micro_recall": atom_recall,
        "atom_micro_f1": _f1(atom_precision, atom_recall),
        "atom_macro_f1": _safe_ratio(sum(per_item_f1), criterion_count),
        "span_exact_count": sum(r.span_exact for r in records),
        "span_exact_rate": _safe_ratio(
            sum(r.span_exact for r in records), matched_atoms
        ),
        "span_mean_iou": _safe_ratio(
            sum(r.span_iou_sum for r in records), matched_atoms
        ),
        "schema_valid_count": sum(r.schema_valid for r in records),
        "schema_valid_rate": _safe_ratio(
            sum(r.schema_valid for r in records), criterion_count
        ),
        "verifier_load_count": sum(r.verifier_load for r in records),
        "verifier_load_rate": _safe_ratio(
            sum(r.verifier_load for r in records), criterion_count
        ),
        "concept_micro_precision": concept_precision,
        "concept_micro_recall": concept_recall,
        "concept_micro_f1": _f1(concept_precision, concept_recall),
        "equivalence_review_queued_count": sum(
            r.equivalence_review_queued for r in records
        ),
    }


def _interval_dict(interval: BootstrapInterval) -> Dict[str, Any]:
    return {
        "estimate": interval.estimate,
        "lower": interval.lower,
        "upper": interval.upper,
        "confidence": interval.confidence,
        "samples": interval.samples,
        "cluster_count": interval.cluster_count,
    }


def _bootstrap_intervals(
    records: Sequence[_ScoredItem],
    samples: int,
    confidence: float,
    seed: int,
) -> Dict[str, Dict[str, Any]]:
    metric_names = (
        "normalized_tree_exact_rate",
        "operator_topology_exact_rate",
        "atom_micro_precision",
        "atom_micro_recall",
        "atom_micro_f1",
        "atom_macro_f1",
        "span_exact_rate",
        "span_mean_iou",
    )
    return {
        name: _interval_dict(
            clustered_bootstrap(
                records,
                cluster_key=lambda record: record.nct_id,
                statistic=lambda sampled, metric=name: _aggregate(sampled)[metric],
                samples=samples,
                confidence=confidence,
                seed=seed,
            )
        )
        for name in metric_names
    }


def _validated_prediction_map(
    predictions: Sequence[Mapping[str, Any]],
    expected_keys: frozenset[Tuple[str, str]],
) -> Dict[Tuple[str, str], Any]:
    result: Dict[Tuple[str, str], Any] = {}
    for record in predictions:
        if not isinstance(record, Mapping) or set(record) != _PREDICTION_KEYS:
            raise DecompositionEvaluationError(
                "Each prediction must contain only nct_id, criterion_id, expression"
            )
        if not isinstance(record["nct_id"], str) or not isinstance(
            record["criterion_id"], str
        ):
            raise DecompositionEvaluationError(
                "Prediction nct_id and criterion_id must be strings"
            )
        key = (record["nct_id"], record["criterion_id"])
        if key not in expected_keys:
            raise DecompositionEvaluationError(
                f"Prediction references an unselected criterion: {key}"
            )
        if key in result:
            raise DecompositionEvaluationError(f"Duplicate prediction: {key}")
        result[key] = record["expression"]
    return result


def evaluate_decomposition(
    selection: Dict[str, Any],
    catalog: Dict[str, Any],
    gold_annotation: Dict[str, Any],
    predictions: Sequence[Mapping[str, Any]],
    *,
    model_id: str,
    prompt_version: str,
    bootstrap_samples: int = 1000,
    bootstrap_confidence: float = 0.95,
    bootstrap_seed: int = 17,
) -> Dict[str, Any]:
    """Evaluate raw public predictions without repairing invalid output."""
    validate_decomposition_selection_document(selection)
    validate_concept_catalog(selection, catalog)
    validate_annotation(selection, catalog, gold_annotation, require_completed=True)
    if not model_id.strip() or not prompt_version.strip():
        raise DecompositionEvaluationError(
            "model_id and prompt_version must be non-empty"
        )
    if bootstrap_samples <= 0 or not 0.0 < bootstrap_confidence < 1.0:
        raise DecompositionEvaluationError("Invalid bootstrap configuration")

    prediction_records = tuple(predictions)

    gold_items = sorted(
        gold_annotation["items"],
        key=lambda item: (item["nct_id"], item["criterion_id"]),
    )
    expected_keys = frozenset(
        (item["nct_id"], item["criterion_id"]) for item in gold_items
    )
    prediction_map = _validated_prediction_map(prediction_records, expected_keys)
    allowed_fields = frozenset(entry["field_id"] for entry in catalog["entries"])

    condition_id_counts: Counter[str] = Counter()
    for expression in prediction_map.values():
        if expression is not None and not _schema_errors(expression):
            condition_id_counts.update(
                atom["condition_id"] for atom in _walk_atoms(expression)
            )
    duplicated_condition_ids = frozenset(
        condition_id
        for condition_id, count in condition_id_counts.items()
        if count > 1
    )

    scored: List[_ScoredItem] = []
    failure_counts: Counter[str] = Counter()
    queued_items: List[Dict[str, str]] = []
    for item in gold_items:
        key = (item["nct_id"], item["criterion_id"])
        if key not in prediction_map or prediction_map[key] is None:
            scored_item = _score_failure(item, "missing", 0)
        else:
            expression = prediction_map[key]
            errors = _schema_errors(expression)
            if errors:
                scored_item = _score_failure(item, "schema_invalid", 0)
            else:
                semantic_error = _semantic_prediction_error(
                    expression,
                    item,
                    allowed_fields,
                    model_id,
                    prompt_version,
                    duplicated_condition_ids,
                )
                if semantic_error is not None:
                    scored_item = _score_failure(item, semantic_error, 1)
                else:
                    scored_item = _score_valid_prediction(item, expression)
        scored.append(scored_item)
        if scored_item.status != "valid":
            failure_counts[scored_item.status] += 1
        if scored_item.equivalence_review_queued:
            queued_items.append(
                {"nct_id": item["nct_id"], "criterion_id": item["criterion_id"]}
            )

    metrics = _aggregate(scored)
    canonical_predictions = sorted(
        (dict(record) for record in prediction_records),
        key=lambda record: (record["nct_id"], record["criterion_id"]),
    )
    prediction_payload_sha256 = _canonical_hash(canonical_predictions)
    report: Dict[str, Any] = {
        "decomposition_evaluation_version": EVALUATION_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "matching_version": MATCHING_VERSION,
        "selection_manifest_id": selection["selection_manifest_id"],
        "selection_manifest_sha256": selection["selection_manifest_sha256"],
        "concept_catalog_id": catalog["concept_catalog_id"],
        "concept_catalog_sha256": catalog["concept_catalog_sha256"],
        "gold_annotation_id": gold_annotation["annotation_id"],
        "gold_annotation_sha256": gold_annotation["annotation_sha256"],
        "split": gold_annotation["split"],
        "model_id": model_id,
        "prompt_version": prompt_version,
        "prediction_payload_sha256": prediction_payload_sha256,
        "denominators": {
            "criteria": metrics["criterion_count"],
            "gold_atoms": metrics["gold_atom_count"],
            "predicted_atoms": metrics["predicted_atom_count"],
            "identity_matched_atoms": metrics["matched_atom_count"],
            "span_scored_atoms": metrics["span_matched_atom_count"],
        },
        "metrics": {
            key: value
            for key, value in metrics.items()
            if key
            not in {
                "criterion_count",
                "gold_atom_count",
                "predicted_atom_count",
                "matched_atom_count",
                "span_matched_atom_count",
            }
        },
        "failure_counts": dict(sorted(failure_counts.items())),
        "equivalence_review": {
            "queued_count": len(queued_items),
            "reviewed_equivalent_count": 0,
            "reviewed_not_equivalent_count": 0,
            "reviewed_uncertain_count": 0,
            "queued_items": queued_items,
            "affects_primary_metrics": False,
        },
        "bootstrap": {
            "cluster_key": "nct_id",
            "trial_count": len({record.nct_id for record in scored}),
            "samples": bootstrap_samples,
            "seed": bootstrap_seed,
            "confidence": bootstrap_confidence,
            "intervals": _bootstrap_intervals(
                scored,
                bootstrap_samples,
                bootstrap_confidence,
                bootstrap_seed,
            ),
        },
        "items": [
            {
                "nct_id": record.nct_id,
                "criterion_id": record.criterion_id,
                "status": record.status,
                "schema_valid": bool(record.schema_valid),
                "verifier_load": bool(record.verifier_load),
                "normalized_tree_exact": bool(record.tree_exact),
                "operator_topology_exact": bool(record.topology_exact),
                "gold_atoms": record.gold_atoms,
                "predicted_atoms": record.predicted_atoms,
                "matched_atoms": record.matched_atoms,
                "concept_gold": record.concept_gold,
                "concept_predicted": record.concept_predicted,
                "concept_matched": record.concept_matched,
                "span_exact": record.span_exact,
                "span_iou_sum": record.span_iou_sum,
                "equivalence_review_queued": bool(
                    record.equivalence_review_queued
                ),
            }
            for record in scored
        ],
    }
    digest = _self_hash(report)
    report["report_id"] = f"decomposition-evaluation-{report['split']}-{digest[:16]}"
    report["report_sha256"] = digest
    validate_decomposition_evaluation_report(report)
    return report


def validate_decomposition_evaluation_report(report: Dict[str, Any]) -> None:
    validate_document(report, REPORT_SCHEMA)
    expected_hash = _self_hash(report)
    if report["report_sha256"] != expected_hash:
        raise DecompositionEvaluationError("Evaluation report hash mismatch")
    expected_id = (
        f"decomposition-evaluation-{report['split']}-{expected_hash[:16]}"
    )
    if report["report_id"] != expected_id:
        raise DecompositionEvaluationError("Evaluation report ID mismatch")
    if report["denominators"]["criteria"] != len(report["items"]):
        raise DecompositionEvaluationError("Criterion denominator mismatch")
    if report["bootstrap"]["trial_count"] != len(
        {item["nct_id"] for item in report["items"]}
    ):
        raise DecompositionEvaluationError("Bootstrap trial count mismatch")
    items = report["items"]
    denominators = report["denominators"]
    metrics = report["metrics"]
    item_keys = [(item["nct_id"], item["criterion_id"]) for item in items]
    if len(item_keys) != len(set(item_keys)):
        raise DecompositionEvaluationError("Evaluation item identities must be unique")
    expected_denominators = {
        "criteria": len(items),
        "gold_atoms": sum(item["gold_atoms"] for item in items),
        "predicted_atoms": sum(item["predicted_atoms"] for item in items),
        "identity_matched_atoms": sum(item["matched_atoms"] for item in items),
        "span_scored_atoms": sum(item["matched_atoms"] for item in items),
    }
    if denominators != expected_denominators:
        raise DecompositionEvaluationError("Evaluation denominators do not reconcile")
    for item in items:
        if item["matched_atoms"] > min(item["gold_atoms"], item["predicted_atoms"]):
            raise DecompositionEvaluationError("Matched atom count exceeds an input")
        if item["concept_gold"] != item["gold_atoms"] or item[
            "concept_predicted"
        ] != item["predicted_atoms"]:
            raise DecompositionEvaluationError("Concept denominators do not reconcile")
        if item["concept_matched"] > min(
            item["concept_gold"], item["concept_predicted"]
        ):
            raise DecompositionEvaluationError("Matched concept count exceeds an input")
        if item["span_exact"] > item["matched_atoms"]:
            raise DecompositionEvaluationError("Exact span count exceeds matched atoms")
        if item["span_iou_sum"] > item["matched_atoms"]:
            raise DecompositionEvaluationError("Span IoU sum exceeds matched atoms")
        if item["status"] == "valid" and not (
            item["schema_valid"] and item["verifier_load"]
        ):
            raise DecompositionEvaluationError("Valid item fails a validity floor")
        if item["status"] != "valid" and item["verifier_load"]:
            raise DecompositionEvaluationError("Failed item cannot load in verifier")
        if item["status"] in {"missing", "schema_invalid"}:
            if item["schema_valid"]:
                raise DecompositionEvaluationError(
                    "Missing or schema-invalid item cannot be schema-valid"
                )
        elif not item["schema_valid"]:
            raise DecompositionEvaluationError(
                "Semantic failure requires a schema-valid expression"
            )
        if item["verifier_load"] and not item["schema_valid"]:
            raise DecompositionEvaluationError("Verifier load requires schema validity")
        if item["status"] != "valid" and any(
            (
                item["normalized_tree_exact"],
                item["operator_topology_exact"],
                item["predicted_atoms"],
                item["matched_atoms"],
                item["concept_predicted"],
                item["concept_matched"],
                item["span_exact"],
                item["span_iou_sum"],
                item["equivalence_review_queued"],
            )
        ):
            raise DecompositionEvaluationError(
                "Invalid or missing output received semantic credit"
            )

    expected_failure_counts = Counter(
        item["status"] for item in items if item["status"] != "valid"
    )
    if report["failure_counts"] != dict(sorted(expected_failure_counts.items())):
        raise DecompositionEvaluationError("Failure counts do not reconcile")
    count_checks = {
        "normalized_tree_exact_count": sum(
            item["normalized_tree_exact"] for item in items
        ),
        "operator_topology_exact_count": sum(
            item["operator_topology_exact"] for item in items
        ),
        "span_exact_count": sum(item["span_exact"] for item in items),
        "schema_valid_count": sum(item["schema_valid"] for item in items),
        "verifier_load_count": sum(item["verifier_load"] for item in items),
        "equivalence_review_queued_count": sum(
            item["equivalence_review_queued"] for item in items
        ),
    }
    for name, expected in count_checks.items():
        if metrics[name] != expected:
            raise DecompositionEvaluationError(
                f"Metric count does not reconcile: {name}"
            )
    rate_checks = {
        "normalized_tree_exact_rate": _safe_ratio(
            count_checks["normalized_tree_exact_count"], len(items)
        ),
        "operator_topology_exact_rate": _safe_ratio(
            count_checks["operator_topology_exact_count"], len(items)
        ),
        "span_exact_rate": _safe_ratio(
            count_checks["span_exact_count"],
            denominators["span_scored_atoms"],
        ),
        "span_mean_iou": _safe_ratio(
            sum(item["span_iou_sum"] for item in items),
            denominators["span_scored_atoms"],
        ),
        "schema_valid_rate": _safe_ratio(
            count_checks["schema_valid_count"], len(items)
        ),
        "verifier_load_rate": _safe_ratio(
            count_checks["verifier_load_count"], len(items)
        ),
    }
    atom_precision = _safe_ratio(
        denominators["identity_matched_atoms"], denominators["predicted_atoms"]
    )
    atom_recall = _safe_ratio(
        denominators["identity_matched_atoms"], denominators["gold_atoms"]
    )
    concept_gold = sum(item["concept_gold"] for item in items)
    concept_predicted = sum(item["concept_predicted"] for item in items)
    concept_matched = sum(item["concept_matched"] for item in items)
    concept_precision = _safe_ratio(concept_matched, concept_predicted)
    concept_recall = _safe_ratio(concept_matched, concept_gold)
    rate_checks.update(
        {
            "atom_micro_precision": atom_precision,
            "atom_micro_recall": atom_recall,
            "atom_micro_f1": _f1(atom_precision, atom_recall),
            "atom_macro_f1": _safe_ratio(
                sum(
                    _f1(
                        _safe_ratio(item["matched_atoms"], item["predicted_atoms"]),
                        _safe_ratio(item["matched_atoms"], item["gold_atoms"]),
                    )
                    for item in items
                ),
                len(items),
            ),
            "concept_micro_precision": concept_precision,
            "concept_micro_recall": concept_recall,
            "concept_micro_f1": _f1(concept_precision, concept_recall),
        }
    )
    for name, expected in rate_checks.items():
        if not math.isclose(metrics[name], expected, rel_tol=0.0, abs_tol=1e-12):
            raise DecompositionEvaluationError(
                f"Metric rate does not reconcile: {name}"
            )

    queued = {
        (item["nct_id"], item["criterion_id"])
        for item in items
        if item["equivalence_review_queued"]
    }
    reported_queued = {
        (item["nct_id"], item["criterion_id"])
        for item in report["equivalence_review"]["queued_items"]
    }
    if queued != reported_queued or report["equivalence_review"][
        "queued_count"
    ] != len(queued):
        raise DecompositionEvaluationError("Equivalence-review queue mismatch")
    bootstrap = report["bootstrap"]
    for name, interval in bootstrap["intervals"].items():
        if not math.isclose(
            interval["estimate"], metrics[name], rel_tol=0.0, abs_tol=1e-12
        ):
            raise DecompositionEvaluationError(
                f"Bootstrap estimate does not match metric: {name}"
            )
        if (
            interval["samples"] != bootstrap["samples"]
            or interval["confidence"] != bootstrap["confidence"]
            or interval["cluster_count"] != bootstrap["trial_count"]
        ):
            raise DecompositionEvaluationError(
                f"Bootstrap configuration mismatch: {name}"
            )
        if interval["lower"] > interval["upper"]:
            raise DecompositionEvaluationError(
                f"Bootstrap interval bounds are reversed: {name}"
            )


def render_decomposition_evaluation_markdown(report: Dict[str, Any]) -> str:
    """Render a concise public companion without changing metric semantics."""
    validate_decomposition_evaluation_report(report)
    metrics = report["metrics"]
    denominators = report["denominators"]
    lines = [
        "# Criteria decomposition evaluation",
        "",
        f"- Split: `{report['split']}`",
        f"- Model: `{report['model_id']}`",
        f"- Prompt: `{report['prompt_version']}`",
        f"- Criteria denominator: {denominators['criteria']}",
        f"- Gold / predicted / matched atoms: {denominators['gold_atoms']} / "
        f"{denominators['predicted_atoms']} / "
        f"{denominators['identity_matched_atoms']}",
        "",
        "## Metrics",
        "",
        f"- Normalized-tree exact: {metrics['normalized_tree_exact_rate']:.4f}",
        f"- Operator-topology exact: {metrics['operator_topology_exact_rate']:.4f}",
        f"- Atom micro P/R/F1: {metrics['atom_micro_precision']:.4f} / "
        f"{metrics['atom_micro_recall']:.4f} / {metrics['atom_micro_f1']:.4f}",
        f"- Atom macro F1: {metrics['atom_macro_f1']:.4f}",
        f"- Exact span / mean span IoU: {metrics['span_exact_rate']:.4f} / "
        f"{metrics['span_mean_iou']:.4f}",
        f"- Schema-valid / verifier-load: {metrics['schema_valid_rate']:.4f} / "
        f"{metrics['verifier_load_rate']:.4f}",
        "",
        "Invalid and missing predictions remain in the criterion denominator. "
        "Verifier load is a validity floor, not semantic correctness.",
        "",
    ]
    return "\n".join(lines)
