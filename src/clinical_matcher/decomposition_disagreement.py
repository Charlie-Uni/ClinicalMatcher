"""Deterministic P5D.6 analysis of the frozen dev decomposition run.

This module explains observable disagreements without rescoring the frozen
P5D.3 primary metrics or treating assisted silver as independent gold.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .decomposition_llm import (
    load_decomposition_llm_contract,
    load_frozen_dev_inputs,
    validate_comparison_report,
    validate_prediction_artifact,
)
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = "resources/decomposition-disagreement-contract-1.0.0.json"
REPORT_SCHEMA = "schemas/decomposition-disagreement-report-1.0.0.schema.json"
REPORT_VERSION = "1.0.0"
COMPONENTS = (
    "field",
    "polarity",
    "operator",
    "value_type",
    "value",
    "unit",
    "time_window",
    "fact_selection",
)
PRIMARY_CATEGORIES = (
    "runtime_error",
    "schema_invalid_output_budget_reached",
    "schema_invalid_other",
    "semantic_invalid_boolean_operator",
    "semantic_invalid_negation_encoding",
    "semantic_invalid_string_operator",
    "semantic_invalid_other",
    "valid_atom_count_mismatch",
    "valid_field_mismatch",
    "valid_polarity_mismatch",
    "valid_operator_mismatch",
    "valid_value_type_mismatch",
    "valid_value_mismatch",
    "valid_unit_mismatch",
    "valid_time_window_mismatch",
    "valid_fact_selection_mismatch",
    "valid_structure_or_source_span_mismatch",
)
_SEMANTIC_REASON_CATEGORIES = {
    "boolean atom requires == or !=": "semantic_invalid_boolean_operator",
    "Boolean atoms must state a positive fact with expected=true; represent negation with NOT": "semantic_invalid_negation_encoding",
    "string atom requires == or !=": "semantic_invalid_string_operator",
}


class DecompositionDisagreementError(ValueError):
    """Raised when the frozen P5D.6 analysis contract is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _self_hash(document: Mapping[str, Any], id_field: str, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(id_field, None)
    payload.pop(hash_field, None)
    return canonical_sha256(payload)


def load_disagreement_contract() -> Dict[str, Any]:
    document = json.loads(
        files("clinical_matcher").joinpath(CONTRACT_RESOURCE).read_text(encoding="utf-8")
    )
    validate_disagreement_contract(document)
    return document


def validate_disagreement_contract(document: Mapping[str, Any]) -> None:
    required = {
        "approved_on",
        "contract_id",
        "contract_sha256",
        "contract_version",
        "disclosures",
        "primary_attribution",
        "protocol_version",
        "retention",
        "source_run",
        "split",
        "status",
    }
    if set(document) != required:
        raise DecompositionDisagreementError("P5D.6 contract is incomplete")
    if document["contract_version"] != "decomposition-disagreement-analysis/1.0.0":
        raise DecompositionDisagreementError("Unsupported P5D.6 contract version")
    expected = _self_hash(document, "contract_id", "contract_sha256")
    if document["contract_sha256"] != expected or document["contract_id"] != (
        f"decomposition-disagreement-contract-{expected[:16]}"
    ):
        raise DecompositionDisagreementError("P5D.6 contract identity mismatch")
    if document["status"] != "frozen_owner_approved" or document["split"] != "dev":
        raise DecompositionDisagreementError("Only the frozen dev analysis is allowed")
    policy = document["primary_attribution"]
    if tuple(policy["precedence"]) != PRIMARY_CATEGORIES:
        raise DecompositionDisagreementError("Primary failure precedence changed")
    if not policy["mutually_exclusive"] or policy["causal_proof_claimed"]:
        raise DecompositionDisagreementError("Primary attribution boundary changed")
    if policy["component_diagnostics_change_primary_metrics"]:
        raise DecompositionDisagreementError("Component diagnostics cannot change metrics")
    disclosures = document["disclosures"]
    if (
        disclosures["owner_accepted_unchanged"],
        disclosures["owner_review_notes"],
    ) != (40, 0):
        raise DecompositionDisagreementError("Owner-review disclosure changed")
    if disclosures["test_source_inspected"] or disclosures["decomposition_accuracy_claimed"]:
        raise DecompositionDisagreementError("Test or accuracy claim is forbidden")
    retention = document["retention"]
    if retention["test_entry_gate_met"] or retention["locked_test_unlocked"]:
        raise DecompositionDisagreementError("The locked test must remain closed")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _canonical_value(value: Any, value_type: str) -> Any:
    if value_type != "number":
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise DecompositionDisagreementError("Invalid numeric atom value") from error
    if not number.is_finite():
        raise DecompositionDisagreementError("Numeric atom value must be finite")
    return "0" if number == 0 else str(number.normalize())


def _walk_atom_components(
    expression: Mapping[str, Any], negated: bool = False
) -> Iterable[Dict[str, Any]]:
    expression_type = expression["expression_type"]
    if expression_type == "atom":
        atom = expression["atom"]
        expected = atom["expected"]
        yield {
            "field": atom["field"],
            "polarity": "negated" if negated else "positive",
            "operator": atom["operator"],
            "value_type": expected["value_type"],
            "value": _canonical_value(expected["value"], expected["value_type"]),
            "unit": expected.get("unit"),
            "time_window": atom.get("time_window"),
            "fact_selection": atom["fact_selection"],
        }
        return
    if expression_type == "not":
        yield from _walk_atom_components(expression["children"][0], not negated)
        return
    for child in expression["children"]:
        yield from _walk_atom_components(child, negated)


def _component_counter(atoms: Sequence[Mapping[str, Any]], component: str) -> Counter[str]:
    return Counter(_canonical_json(atom[component]) for atom in atoms)


def classify_primary_failure(
    prediction: Mapping[str, Any],
    reference_expression: Mapping[str, Any],
    *,
    output_token_limit: int,
) -> str:
    """Assign exactly one observable category using the frozen precedence."""

    status = prediction["output_status"]
    if status == "runtime_error":
        return "runtime_error"
    if status == "schema_invalid":
        if (
            prediction.get("failure_reason") == "invalid_json"
            and prediction.get("output_tokens", 0) >= output_token_limit
        ):
            return "schema_invalid_output_budget_reached"
        return "schema_invalid_other"
    if status == "semantic_invalid":
        return _SEMANTIC_REASON_CATEGORIES.get(
            prediction.get("failure_reason"), "semantic_invalid_other"
        )
    if status != "valid" or prediction.get("expression") is None:
        raise DecompositionDisagreementError("Prediction status is unsupported")

    reference_atoms = list(_walk_atom_components(reference_expression))
    predicted_atoms = list(_walk_atom_components(prediction["expression"]))
    if len(reference_atoms) != len(predicted_atoms):
        return "valid_atom_count_mismatch"
    for component in COMPONENTS:
        if _component_counter(reference_atoms, component) != _component_counter(
            predicted_atoms, component
        ):
            return f"valid_{component}_mismatch"
    return "valid_structure_or_source_span_mismatch"


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def build_component_diagnostics(
    predictions: Sequence[Mapping[str, Any]],
    references: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Compare marginal component multisets for valid outputs only."""

    totals = {component: [0, 0, 0] for component in COMPONENTS}
    valid_count = 0
    for prediction in predictions:
        if prediction["output_status"] != "valid":
            continue
        valid_count += 1
        reference_atoms = list(
            _walk_atom_components(references[prediction["criterion_id"]])
        )
        predicted_atoms = list(_walk_atom_components(prediction["expression"]))
        for component in COMPONENTS:
            reference_counter = _component_counter(reference_atoms, component)
            predicted_counter = _component_counter(predicted_atoms, component)
            totals[component][0] += len(reference_atoms)
            totals[component][1] += len(predicted_atoms)
            totals[component][2] += sum(
                (reference_counter & predicted_counter).values()
            )
    dimensions = {}
    for component, (reference_count, predicted_count, matched_count) in totals.items():
        precision = _ratio(matched_count, predicted_count)
        recall = _ratio(matched_count, reference_count)
        dimensions[component] = {
            "reference_count": reference_count,
            "predicted_count": predicted_count,
            "matched_count": matched_count,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    return {
        "scope": "semantic_valid_outputs_only",
        "criteria": valid_count,
        "nonexclusive": True,
        "marginal_multiset_only": True,
        "dimensions": dimensions,
    }


def _load_source_run(
    root: Path, source_dir: Path, contract: Mapping[str, Any]
) -> Dict[str, Any]:
    source_run = contract["source_run"]
    paths = {
        name: source_dir / binding["filename"] for name, binding in source_run.items()
    }
    for name, path in paths.items():
        if _file_sha256(path) != source_run[name]["file_sha256"]:
            raise DecompositionDisagreementError(f"Frozen source hash mismatch: {name}")
    predictions = json.loads(paths["predictions"].read_text(encoding="utf-8"))
    comparison = json.loads(paths["comparison_report"].read_text(encoding="utf-8"))
    llm_contract = load_decomposition_llm_contract()
    inputs = load_frozen_dev_inputs(root, llm_contract)
    validate_prediction_artifact(predictions, inputs)
    validate_comparison_report(comparison)
    if (
        predictions["prediction_id"] != source_run["predictions"]["id"]
        or predictions["prediction_sha256"]
        != source_run["predictions"]["content_sha256"]
        or comparison["report_id"] != source_run["comparison_report"]["id"]
        or comparison["report_sha256"]
        != source_run["comparison_report"]["content_sha256"]
    ):
        raise DecompositionDisagreementError("Frozen source identity mismatch")
    if (
        comparison["prediction_id"] != predictions["prediction_id"]
        or comparison["prediction_sha256"] != predictions["prediction_sha256"]
        or comparison["silver_manifest_id"]
        != inputs["silver_manifest"]["manifest_id"]
        or comparison["silver_manifest_sha256"]
        != inputs["silver_manifest"]["manifest_sha256"]
    ):
        raise DecompositionDisagreementError("P5D.5 artifact chain is broken")
    return {
        "paths": paths,
        "predictions": predictions,
        "comparison": comparison,
        "llm_contract": llm_contract,
        "inputs": inputs,
    }


def build_disagreement_report(
    root: Path,
    source_dir: Path,
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    contract = load_disagreement_contract()
    source = _load_source_run(root, source_dir, contract)
    predictions = source["predictions"]
    comparison = source["comparison"]
    inputs = source["inputs"]
    references = {
        item["criterion_id"]: item["reviewed_expression"]
        for item in inputs["assisted_silver"]["items"]
    }
    comparison_rows = {
        item["criterion_id"]: item for item in comparison["items"]
    }
    if comparison["overall_metrics"]["matched_atoms"] != 0:
        raise DecompositionDisagreementError(
            "The approved attribution universe requires zero exact atom matches"
        )
    items = []
    category_counts: Counter[str] = Counter()
    for prediction in predictions["predictions"]:
        criterion_id = prediction["criterion_id"]
        row = comparison_rows[criterion_id]
        category = classify_primary_failure(
            prediction,
            references[criterion_id],
            output_token_limit=contract["primary_attribution"][
                "schema_output_token_limit"
            ],
        )
        category_counts[category] += 1
        items.append(
            {
                "nct_id": prediction["nct_id"],
                "criterion_id": criterion_id,
                "information_asymmetry": row["information_asymmetry"],
                "output_status": prediction["output_status"],
                "primary_failure_category": category,
                "component_disagreement_types": row["disagreement_types"],
            }
        )
    file_resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    contract_file_sha = hashlib.sha256(file_resource.read_bytes()).hexdigest()
    input_contract = source["llm_contract"]["inputs"]
    status_counts = Counter(item["output_status"] for item in predictions["predictions"])
    reason_counts = Counter(
        item["failure_reason"]
        for item in predictions["predictions"]
        if item["failure_reason"] is not None
    )
    provenance_markers = (
        "provenance",
        "source_id",
        "source span",
        "decomposition method",
    )
    explicit_provenance_failures = sum(
        count
        for reason, count in reason_counts.items()
        if any(marker in reason.lower() for marker in provenance_markers)
    )
    report: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "report_id": "decomposition-disagreement-dev-0000000000000000",
        "report_sha256": "0" * 64,
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "protocol_version": contract["protocol_version"],
        "split": "dev",
        "source_artifacts": {
            "analysis_contract": {
                "id": contract["contract_id"],
                "content_sha256": contract["contract_sha256"],
                "file_sha256": contract_file_sha,
            },
            "predictions": {
                "id": predictions["prediction_id"],
                "content_sha256": predictions["prediction_sha256"],
                "file_sha256": contract["source_run"]["predictions"]["file_sha256"],
            },
            "comparison_report": {
                "id": comparison["report_id"],
                "content_sha256": comparison["report_sha256"],
                "file_sha256": contract["source_run"]["comparison_report"]["file_sha256"],
            },
            "assisted_silver": {
                "id": inputs["assisted_silver"]["work_id"],
                "content_sha256": inputs["assisted_silver"]["work_sha256"],
                "file_sha256": input_contract["assisted_silver"]["file_sha256"],
            },
            "silver_manifest": {
                "id": inputs["silver_manifest"]["manifest_id"],
                "content_sha256": inputs["silver_manifest"]["manifest_sha256"],
                "file_sha256": input_contract["silver_manifest"]["file_sha256"],
            },
        },
        "model_roles": copy.deepcopy(comparison["model_roles"]),
        "owner_review_outcome": copy.deepcopy(comparison["owner_review_outcome"]),
        "scope_statement": contract["retention"]["scope_statement"],
        "claim_boundaries": copy.deepcopy(comparison["claim_boundaries"]),
        "retention_decision": {
            key: copy.deepcopy(contract["retention"][key])
            for key in (
                "decision",
                "test_entry_gate_met",
                "locked_test_unlocked",
                "restart_conditions",
            )
        },
        "primary_attribution_policy": {
            key: copy.deepcopy(contract["primary_attribution"][key])
            for key in (
                "policy_version",
                "universe",
                "precedence",
                "mutually_exclusive",
                "causal_proof_claimed",
                "component_diagnostics_change_primary_metrics",
            )
        },
        "population": {
            "criteria": comparison["overall_metrics"]["criteria"],
            "exact_atom_matches": comparison["overall_metrics"]["matched_atoms"],
            "schema_valid_outputs": comparison["overall_metrics"]["schema_valid_outputs"],
            "semantic_valid_outputs": comparison["overall_metrics"]["semantic_valid_outputs"],
        },
        "primary_category_counts": {
            category: category_counts[category] for category in PRIMARY_CATEGORIES
        },
        "status_counts": dict(sorted(status_counts.items())),
        "failure_reason_counts": [
            {"reason": reason, "count": reason_counts[reason]}
            for reason in sorted(reason_counts)
        ],
        "component_diagnostics": build_component_diagnostics(
            predictions["predictions"], references
        ),
        "span_diagnostic": {
            "identity_matched_atoms": 0,
            "status": "not_evaluable_without_identity_matched_atoms",
            "zero_value_interpretation": "The reported zero span score has a zero matched-identity denominator; it does not show that every predicted span was wrong.",
        },
        "provenance_diagnostic": {
            "schema_valid_outputs_checked": comparison["overall_metrics"][
                "schema_valid_outputs"
            ],
            "explicit_provenance_failure_count": explicit_provenance_failures,
            "scope_limitation": "Only explicit recorded first-failure reasons and fully valid outputs are observable; this is not proof that every rejected expression had correct provenance.",
        },
        "information_asymmetry": {
            "item_count": comparison["information_asymmetry"]["item_count"],
            "semantic_valid_outputs": comparison["information_asymmetry"]["metrics"][
                "semantic_valid_outputs"
            ],
            "operator_topology_exact_rate": comparison["information_asymmetry"][
                "metrics"
            ]["operator_topology_exact_rate"],
            "atom_micro_f1": comparison["information_asymmetry"]["metrics"][
                "atom_micro_f1"
            ],
            "causal_effect_claimed": False,
        },
        "performance": copy.deepcopy(predictions["performance"]),
        "items": items,
        "limitations": [
            "The reference is Codex-drafted and was accepted unchanged by one owner; it is not independent human gold.",
            "The 40/40 accepted-unchanged and zero-note review distribution is a disclosed rubber-stamp risk.",
            "This was one zero-shot run under initial prompt v1.0.0; no prompt-iteration ceiling was measured.",
            "Primary categories are mutually exclusive observable diagnostics selected by precedence, not causal explanations.",
            "Component overlaps are marginal multiset diagnostics and do not award partial primary-metric credit.",
            "The AF-only dev result does not establish clinical, disease-general, or model-family capability.",
            "Eight assisted-reference items include owner resolutions hidden from the evaluated model; subgroup differences are not causal estimates.",
            "The locked test was not inspected or executed.",
        ],
    }
    digest = _self_hash(report, "report_id", "report_sha256")
    report["report_id"] = f"decomposition-disagreement-dev-{digest[:16]}"
    report["report_sha256"] = digest
    validate_disagreement_report(report)
    return report


def validate_disagreement_report(report: Mapping[str, Any]) -> None:
    validate_document(dict(report), REPORT_SCHEMA)
    expected = _self_hash(report, "report_id", "report_sha256")
    if report["report_sha256"] != expected or report["report_id"] != (
        f"decomposition-disagreement-dev-{expected[:16]}"
    ):
        raise DecompositionDisagreementError("P5D.6 report identity mismatch")
    policy = report["primary_attribution_policy"]
    if tuple(policy["precedence"]) != PRIMARY_CATEGORIES:
        raise DecompositionDisagreementError("P5D.6 report precedence changed")
    counts = report["primary_category_counts"]
    if tuple(counts) != PRIMARY_CATEGORIES:
        raise DecompositionDisagreementError("P5D.6 category keys or order changed")
    observed = Counter(item["primary_failure_category"] for item in report["items"])
    if counts != {category: observed[category] for category in PRIMARY_CATEGORIES}:
        raise DecompositionDisagreementError("Primary categories do not match items")
    if (
        sum(counts.values()) != report["population"]["criteria"]
        or report["population"]["criteria"] != 40
    ):
        raise DecompositionDisagreementError("Primary categories do not reconcile to 40")
    status_counts = dict(sorted(Counter(item["output_status"] for item in report["items"]).items()))
    if report["status_counts"] != status_counts:
        raise DecompositionDisagreementError("Output statuses do not reconcile")
    for metric in report["component_diagnostics"]["dimensions"].values():
        precision = _ratio(metric["matched_count"], metric["predicted_count"])
        recall = _ratio(metric["matched_count"], metric["reference_count"])
        if (
            metric["precision"] != precision
            or metric["recall"] != recall
            or metric["f1"] != _f1(precision, recall)
        ):
            raise DecompositionDisagreementError("Component metric does not reconcile")
    if report["retention_decision"]["locked_test_unlocked"]:
        raise DecompositionDisagreementError("Locked test cannot be unlocked")


def render_disagreement_markdown(report: Mapping[str, Any]) -> str:
    validate_disagreement_report(report)
    lines = [
        "# Initial-prompt decomposition disagreement analysis (dev)",
        "",
        "This is descriptive agreement with Codex-drafted, owner-accepted assisted silver; it is not accuracy against independent human gold.",
        "",
        f"- Scope: {report['scope_statement']}",
        "- Owner review outcome: 40/40 accepted unchanged, 0 edited, 0 review notes",
        f"- Reference draft model: `{report['model_roles']['reference_draft_model_id']}`",
        f"- Evaluated model: `{report['model_roles']['evaluated_model_id']}`",
        "- Decision: retain as an initial-prompt dev-only negative baseline; test entry gate not met and locked test remains closed",
        "",
        "## Frozen descriptive result",
        "",
        f"- Criteria / schema-valid / semantic-valid: {report['population']['criteria']} / {report['population']['schema_valid_outputs']} / {report['population']['semantic_valid_outputs']}",
        f"- Exact atom matches: {report['population']['exact_atom_matches']}",
        "",
        "## Mutually exclusive primary attribution",
        "",
        "Every dev item belongs to exactly one category; counts sum to 40. These are observable diagnostics, not causal proof.",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| `{category}` | {count} |"
        for category, count in report["primary_category_counts"].items()
        if count
    )
    lines.extend(
        [
            "",
            "## Non-primary component overlap",
            "",
            "These marginal multiset diagnostics use only the 26 semantic-valid outputs. They may overlap and do not change the zero exact-atom primary result.",
            "",
            "| Component | Matched / predicted / reference | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for component, metric in report["component_diagnostics"]["dimensions"].items():
        lines.append(
            f"| `{component}` | {metric['matched_count']} / {metric['predicted_count']} / {metric['reference_count']} | {metric['precision']:.4f} | {metric['recall']:.4f} | {metric['f1']:.4f} |"
        )
    performance = report["performance"]
    asymmetry = report["information_asymmetry"]
    lines.extend(
        [
            "",
            "## Interpretation boundaries",
            "",
            f"- Span alignment is `{report['span_diagnostic']['status']}`: {report['span_diagnostic']['zero_value_interpretation']}",
            f"- The eight-item information-asymmetry subgroup had semantic-valid count {asymmetry['semantic_valid_outputs']} and topology agreement {asymmetry['operator_topology_exact_rate']:.4f}; no causal effect is claimed.",
            f"- Runtime: mean {performance['latency_seconds_mean']:.3f} seconds/item, P95 {performance['latency_seconds_p95']:.3f} seconds/item, total {performance['total_duration_seconds']:.3f} seconds on {performance['hardware'].get('chip', 'recorded hardware')}.",
            "- A stronger prompt, a larger-model contract, or truly independent decomposition gold would require a new, separately versioned decision; none changes this retained run.",
            "",
            "The unanimous no-note owner-review distribution remains disclosed as a rubber-stamp risk. The assisted silver is observation-locked and was not revised after model disagreements were observed.",
            "",
        ]
    )
    return "\n".join(lines)


def publish_disagreement_package(
    root: Path,
    source_dir: Path,
    output_dir: Path,
) -> Dict[str, Path]:
    """Verify, copy, and analyze the frozen run without overwriting outputs."""

    contract = load_disagreement_contract()
    source = _load_source_run(root, source_dir, contract)
    report = build_disagreement_report(root, source_dir)
    markdown = render_disagreement_markdown(report)
    targets = {
        "predictions": output_dir / "predictions.json",
        "comparison_report": output_dir / "comparison-report.json",
        "comparison_markdown": output_dir / "comparison-report.md",
        "disagreement_report": output_dir / "disagreement-analysis.json",
        "disagreement_markdown": output_dir / "disagreement-analysis.md",
    }
    if output_dir.exists() or any(path.exists() for path in targets.values()):
        raise FileExistsError("Refusing to overwrite a decomposition result package")
    output_dir.mkdir(parents=True)
    written = []
    try:
        for name in ("predictions", "comparison_report", "comparison_markdown"):
            target = targets[name]
            with target.open("xb") as handle:
                handle.write(source["paths"][name].read_bytes())
            written.append(target)
        for target, value in (
            (targets["disagreement_report"], json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"),
            (targets["disagreement_markdown"], markdown),
        ):
            with target.open("x", encoding="utf-8") as handle:
                handle.write(value)
            written.append(target)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        output_dir.rmdir()
        raise
    return targets
