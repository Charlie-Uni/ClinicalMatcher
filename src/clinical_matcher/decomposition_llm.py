"""Pinned local-Llama dev comparison against disclosed assisted silver."""

from __future__ import annotations

import copy
import hashlib
import json
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator, FormatChecker

from .apixaban_structured_llm import (
    ApixabanStructuredLLMError,
    OllamaLoopbackClient,
    detect_hardware,
    verify_local_runtime,
)
from .decomposition_assisted_annotation import (
    DecompositionAssistedAnnotationError,
    validate_assisted_work,
    validate_llm_expression_for_item,
)
from .decomposition_evaluation import compare_decomposition_expressions
from .evaluation import clustered_bootstrap
from .splits import canonical_sha256, current_git_commit
from .validation import load_schema, validate_document


CONTRACT_RESOURCE = "resources/decomposition-llama-dev-contract-1.0.0.json"
SILVER_MANIFEST_SCHEMA = (
    "schemas/decomposition-assisted-silver-manifest-1.0.0.schema.json"
)
PREDICTION_SCHEMA = "schemas/decomposition-llama-predictions-1.0.0.schema.json"
REPORT_SCHEMA = "schemas/decomposition-llama-comparison-report-1.0.0.schema.json"
PREDICTION_VERSION = "1.0.0"
REPORT_VERSION = "1.0.0"


class DecompositionLLMError(ValueError):
    """Raised when the frozen dev-only local-model contract is violated."""


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


def model_id(contract: Mapping[str, Any]) -> str:
    return contract["reporting"]["evaluated_model_id"]


def load_decomposition_llm_contract() -> Dict[str, Any]:
    document = json.loads(
        files("clinical_matcher").joinpath(CONTRACT_RESOURCE).read_text(encoding="utf-8")
    )
    validate_decomposition_llm_contract(document)
    return document


def validate_decomposition_llm_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version", "protocol_version", "contract_id", "contract_sha256",
        "status", "approved_on", "split", "inputs", "input_policy", "model",
        "runtime", "decoding", "prompt", "output_contract", "reporting",
        "post_observation_lock",
    }
    if set(document) != required:
        raise DecompositionLLMError("Decomposition LLM contract is incomplete")
    if document["contract_version"] != "decomposition-llama-dev-comparison/1.0.0":
        raise DecompositionLLMError("Unsupported decomposition LLM contract")
    if document["protocol_version"] != "decomposition-benchmark-protocol/1.3.0":
        raise DecompositionLLMError("Decomposition protocol 1.3.0 is required")
    expected = _self_hash(document, "contract_id", "contract_sha256")
    if document["contract_sha256"] != expected or document["contract_id"] != (
        f"decomposition-llama-dev-contract-{expected[:16]}"
    ):
        raise DecompositionLLMError("Decomposition LLM contract identity mismatch")
    if document["status"] != "frozen_owner_approved" or document["split"] != "dev":
        raise DecompositionLLMError("Only the frozen dev contract is executable")
    policy = document["input_policy"]
    forbidden = (
        "assisted_silver_included", "owner_review_included",
        "item_specific_issue_resolution_included", "restricted_data_allowed",
    )
    if any(policy[field] is not False for field in forbidden):
        raise DecompositionLLMError("Reference, item resolution, and restricted inputs are forbidden")
    if policy["few_shot_examples"] != 0 or policy["one_criterion_per_request"] is not True:
        raise DecompositionLLMError("The frozen run is zero-shot and itemwise")
    if policy["common_guide_sections"] != [
        "criterion_semantics", "expression_rules", "atom_rules", "time_rules",
        "condition_id_rules", "ambiguity_resolution_rules",
    ]:
        raise DecompositionLLMError("Common guide-section selection changed")
    runtime = document["runtime"]
    if runtime["endpoint"] != "http://127.0.0.1:11434" or runtime[
        "network_policy"
    ] != "loopback_only_no_cloud_fallback":
        raise DecompositionLLMError("Only pinned loopback Ollama is permitted")
    decoding = document["decoding"]
    if decoding != {
        "temperature": 0, "seed": 17, "num_ctx": 16384,
        "num_predict": 4096, "stream": False, "keep_alive": "5m",
    }:
        raise DecompositionLLMError("Frozen decoding settings changed")
    reporting = document["reporting"]
    if (
        reporting["owner_review_total"],
        reporting["owner_accepted_unchanged"],
        reporting["owner_accepted_with_edits"],
        reporting["owner_review_notes"],
    ) != (40, 40, 0, 0):
        raise DecompositionLLMError("Owner-review distribution disclosure changed")
    if reporting["information_asymmetry_item_count"] != 8:
        raise DecompositionLLMError("Information-asymmetry subgroup changed")
    if reporting["test_source_inspected"] is not False:
        raise DecompositionLLMError("Test-source inspection is forbidden")
    if reporting["decomposition_accuracy_claimed"] or reporting[
        "independent_gold_claimed"
    ]:
        raise DecompositionLLMError("Accuracy or independent-gold claims are forbidden")
    lock = document["post_observation_lock"]
    if set(lock.values()) != {True}:
        raise DecompositionLLMError("Post-observation silver lock is mandatory")


def _load_bound_json(root: Path, binding: Mapping[str, Any]) -> Dict[str, Any]:
    path = root / binding["path"]
    if _file_sha256(path) != binding["file_sha256"]:
        raise DecompositionLLMError(f"Frozen input file hash mismatch: {binding['path']}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DecompositionLLMError(f"Frozen input is not an object: {binding['path']}")
    return value


def _load_bound_resource(binding: Mapping[str, Any]) -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(binding["resource"])
    raw = resource.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["file_sha256"]:
        raise DecompositionLLMError("Frozen guide file hash mismatch")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise DecompositionLLMError("Frozen guide is not an object")
    return value


def load_frozen_dev_inputs(root: Path, contract: Mapping[str, Any]) -> Dict[str, Any]:
    inputs = contract["inputs"]
    values = {
        name: _load_bound_json(root, inputs[name])
        for name in (
            "selection", "source_package", "concept_catalog", "issue_log",
            "assisted_silver", "silver_manifest",
        )
    }
    values["annotation_guide"] = _load_bound_resource(inputs["annotation_guide"])
    identity_fields = {
        "selection": ("selection_manifest_id", "selection_manifest_sha256"),
        "source_package": ("package_id", "package_sha256"),
        "concept_catalog": ("concept_catalog_id", "concept_catalog_sha256"),
        "issue_log": ("issue_log_id", "issue_log_sha256"),
        "annotation_guide": ("guide_id", "guide_sha256"),
    }
    for name, (id_field, hash_field) in identity_fields.items():
        binding = inputs[name]
        if values[name][id_field] != binding["id"] or values[name][hash_field] != binding[
            "content_sha256"
        ]:
            raise DecompositionLLMError(f"Frozen input identity mismatch: {name}")
    silver = values["assisted_silver"]
    if silver["work_id"] != inputs["assisted_silver"]["work_id"] or silver[
        "work_sha256"
    ] != inputs["assisted_silver"]["work_sha256"]:
        raise DecompositionLLMError("Assisted silver identity mismatch")
    manifest = values["silver_manifest"]
    validate_document(manifest, SILVER_MANIFEST_SCHEMA)
    expected_manifest_hash = _self_hash(manifest, "manifest_id", "manifest_sha256")
    if manifest["manifest_sha256"] != expected_manifest_hash or manifest[
        "manifest_id"
    ] != inputs["silver_manifest"]["id"]:
        raise DecompositionLLMError("Assisted-silver manifest identity mismatch")
    if manifest["manifest_sha256"] != inputs["silver_manifest"]["content_sha256"]:
        raise DecompositionLLMError("Assisted-silver manifest binding mismatch")
    if manifest["silver_file_sha256"] != inputs["assisted_silver"]["file_sha256"]:
        raise DecompositionLLMError("Silver file cross-binding mismatch")
    validate_assisted_work(
        values["source_package"], values["concept_catalog"], silver,
        require_completed=True,
    )
    outcome = manifest["owner_review_outcome"]
    observed = Counter(item["review_status"] for item in silver["items"])
    notes = sum(item["owner_review_note"] is not None for item in silver["items"])
    if (
        observed["accepted_unchanged"] != outcome["accepted_unchanged"]
        or observed["accepted_with_edits"] != outcome["accepted_with_edits"]
        or notes != outcome["review_notes"]
    ):
        raise DecompositionLLMError("Owner-review disclosure does not match silver")
    issue_ids = sorted(item["criterion_id"] for item in values["issue_log"]["issues"])
    if issue_ids != sorted(manifest["information_asymmetry"]["criterion_ids"]):
        raise DecompositionLLMError("Information-asymmetry subgroup mismatch")
    return values


def item_bound_output_schema(
    contract: Mapping[str, Any], catalog: Mapping[str, Any], item: Mapping[str, Any]
) -> Dict[str, Any]:
    core = load_schema(contract["output_contract"]["core_schema_resource"])
    required_definitions = (
        "expression", "atom", "nonEmptyString", "typedValue", "timeWindow",
        "provenance", "sourceSpan",
    )
    definitions = {
        name: copy.deepcopy(core["$defs"][name]) for name in required_definitions
    }
    definitions["atom"]["properties"]["field"] = {
        "type": "string", "enum": [entry["field_id"] for entry in catalog["entries"]]
    }
    definitions["atom"]["properties"]["condition_id"] = {
        "type": "string", "pattern": f"^{item['criterion_id']}:a[0-9]{{2}}$"
    }
    source_length = len(item["source_text"])
    definitions["sourceSpan"]["properties"]["start"]["maximum"] = source_length - 1
    definitions["sourceSpan"]["properties"]["end"]["maximum"] = source_length
    definitions["provenance"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_id", "source_span", "method", "model_id", "prompt_version"],
        "properties": {
            "source_id": {"const": item["source_id"]},
            "source_span": {"$ref": "#/$defs/sourceSpan"},
            "method": {"const": "llm"},
            "model_id": {"const": model_id(contract)},
            "prompt_version": {"const": contract["prompt"]["prompt_version"]},
        },
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["expression"],
        "properties": {"expression": {"$ref": "#/$defs/expression"}},
        "$defs": definitions,
    }
    Draft202012Validator.check_schema(schema)
    return schema


def build_messages(
    contract: Mapping[str, Any], catalog: Mapping[str, Any], guide: Mapping[str, Any],
    item: Mapping[str, Any],
) -> list[Dict[str, str]]:
    sections = {
        name: guide[name] for name in contract["input_policy"]["common_guide_sections"]
    }
    user = {
        "criterion": {
            key: item[key]
            for key in ("criterion_id", "criterion_type", "source_id", "source_text")
        },
        "concept_catalog": catalog["entries"],
        "common_annotation_guide": sections,
        "required_atom_provenance": {
            "method": "llm",
            "model_id": model_id(contract),
            "prompt_version": contract["prompt"]["prompt_version"],
            "source_id": item["source_id"],
        },
    }
    return [
        {"role": "system", "content": contract["prompt"]["system"]},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
    ]


def parse_model_output(
    content: str, schema: Mapping[str, Any], *, item: Mapping[str, Any],
    catalog: Mapping[str, Any], contract: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str, Optional[str]]:
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return None, "schema_invalid", "invalid_json"
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        return None, "schema_invalid", "output_schema_violation"
    expression = document["expression"]
    try:
        validate_llm_expression_for_item(
            expression,
            criterion_id=item["criterion_id"],
            source=item,
            catalog=catalog,
            model_id=model_id(contract),
            prompt_version=contract["prompt"]["prompt_version"],
        )
    except DecompositionAssistedAnnotationError as error:
        return expression, "semantic_invalid", str(error).split(":", 1)[0]
    return expression, "valid", None


def _comparison_row(
    item: Mapping[str, Any], reference: Mapping[str, Any], prediction: Mapping[str, Any],
    asymmetry_ids: set[str],
) -> Dict[str, Any]:
    base = {
        "nct_id": item["nct_id"],
        "criterion_id": item["criterion_id"],
        "information_asymmetry": item["criterion_id"] in asymmetry_ids,
        "output_status": prediction["output_status"],
        "failure_reason": prediction["failure_reason"],
    }
    if prediction["output_status"] != "valid":
        return {
            **base, "normalized_tree_exact": False, "operator_topology_exact": False,
            "reference_atoms": _count_atoms(reference), "predicted_atoms": 0,
            "matched_atoms": 0, "atom_f1": 0.0, "span_exact": 0,
            "span_iou_sum": 0.0, "equivalence_review_queued": False,
            "disagreement_types": [prediction["output_status"]],
        }
    compared = compare_decomposition_expressions(reference, prediction["expression"])
    return {
        **base,
        "normalized_tree_exact": compared["normalized_tree_exact"],
        "operator_topology_exact": compared["operator_topology_exact"],
        "reference_atoms": compared["left_atoms"],
        "predicted_atoms": compared["right_atoms"],
        "matched_atoms": compared["matched_atoms"],
        "atom_f1": compared["atom_f1"],
        "span_exact": compared["span_exact"],
        "span_iou_sum": compared["span_iou_sum"],
        "equivalence_review_queued": compared["equivalence_review_queued"],
        "disagreement_types": compared["disagreement_types"],
    }


def _count_atoms(expression: Mapping[str, Any]) -> int:
    if expression["expression_type"] == "atom":
        return 1
    return sum(_count_atoms(child) for child in expression["children"])


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    reference_atoms = sum(row["reference_atoms"] for row in rows)
    predicted_atoms = sum(row["predicted_atoms"] for row in rows)
    matched_atoms = sum(row["matched_atoms"] for row in rows)
    precision = _ratio(matched_atoms, predicted_atoms)
    recall = _ratio(matched_atoms, reference_atoms)
    return {
        "criteria": len(rows),
        "schema_valid_outputs": sum(
            row["output_status"] in {"valid", "semantic_invalid"} for row in rows
        ),
        "schema_valid_rate": _ratio(
            sum(row["output_status"] in {"valid", "semantic_invalid"} for row in rows),
            len(rows),
        ),
        "semantic_valid_outputs": sum(row["output_status"] == "valid" for row in rows),
        "semantic_valid_rate": _ratio(
            sum(row["output_status"] == "valid" for row in rows), len(rows)
        ),
        "normalized_tree_exact_rate": _ratio(sum(row["normalized_tree_exact"] for row in rows), len(rows)),
        "operator_topology_exact_rate": _ratio(sum(row["operator_topology_exact"] for row in rows), len(rows)),
        "reference_atoms": reference_atoms,
        "predicted_atoms": predicted_atoms,
        "matched_atoms": matched_atoms,
        "atom_micro_precision": precision,
        "atom_micro_recall": recall,
        "atom_micro_f1": _ratio(2 * precision * recall, precision + recall),
        "atom_macro_f1": _ratio(sum(row["atom_f1"] for row in rows), len(rows)),
        "span_exact_rate": _ratio(sum(row["span_exact"] for row in rows), matched_atoms),
        "span_mean_iou": _ratio(sum(row["span_iou_sum"] for row in rows), matched_atoms),
    }


def _interval(rows: Sequence[Mapping[str, Any]], metric: str) -> Dict[str, Any]:
    interval = clustered_bootstrap(
        rows,
        cluster_key=lambda row: row["nct_id"],
        statistic=lambda sampled: _metrics(sampled)[metric],
        samples=1000,
        confidence=0.95,
        seed=17,
    )
    return {
        "estimate": interval.estimate, "lower": interval.lower,
        "upper": interval.upper, "confidence": interval.confidence,
        "samples": interval.samples, "cluster_count": interval.cluster_count,
    }


def build_comparison_report(
    predictions: Mapping[str, Any], inputs: Mapping[str, Any],
    contract: Mapping[str, Any], generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    package_items = {item["criterion_id"]: item for item in inputs["source_package"]["items"]}
    reference_items = {
        item["criterion_id"]: item["reviewed_expression"]
        for item in inputs["assisted_silver"]["items"]
    }
    prediction_items = {item["criterion_id"]: item for item in predictions["predictions"]}
    asymmetry_ids = set(inputs["silver_manifest"]["information_asymmetry"]["criterion_ids"])
    rows = [
        _comparison_row(
            item, reference_items[item["criterion_id"]], prediction_items[item["criterion_id"]],
            asymmetry_ids,
        )
        for item in inputs["source_package"]["items"]
    ]
    asymmetry = [row for row in rows if row["information_asymmetry"]]
    symmetric = [row for row in rows if not row["information_asymmetry"]]
    overall = _metrics(rows)
    report: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "report_id": "decomposition-llama-comparison-dev-0000000000000000",
        "report_sha256": "0" * 64,
        "generated_at": generated_at or _now(),
        "protocol_version": contract["protocol_version"],
        "split": "dev",
        "comparison_label": contract["reporting"]["comparison_label"],
        "contract_id": contract["contract_id"],
        "contract_sha256": contract["contract_sha256"],
        "prediction_id": predictions["prediction_id"],
        "prediction_sha256": predictions["prediction_sha256"],
        "silver_manifest_id": inputs["silver_manifest"]["manifest_id"],
        "silver_manifest_sha256": inputs["silver_manifest"]["manifest_sha256"],
        "model_roles": {
            "reference_draft_model_id": contract["reporting"]["reference_draft_model_id"],
            "evaluated_model_id": model_id(contract),
            "relationship": "Llama compared with Codex-drafted, owner-accepted assisted silver; not accuracy against human gold",
        },
        "owner_review_outcome": copy.deepcopy(inputs["silver_manifest"]["owner_review_outcome"]),
        "claim_boundaries": copy.deepcopy(inputs["silver_manifest"]["claim_boundaries"]),
        "information_asymmetry": {
            "item_count": len(asymmetry),
            "evaluated_model_received_item_resolutions": False,
            "metrics": _metrics(asymmetry),
        },
        "no_item_resolution_subgroup": {"item_count": len(symmetric), "metrics": _metrics(symmetric)},
        "overall_metrics": overall,
        "failure_counts": dict(sorted(Counter(row["output_status"] for row in rows if row["output_status"] != "valid").items())),
        "bootstrap": {
            "cluster_key": "nct_id", "samples": 1000, "seed": 17, "confidence": 0.95,
            "intervals": {
                metric: _interval(rows, metric)
                for metric in ("atom_micro_f1", "operator_topology_exact_rate", "span_mean_iou")
            },
        },
        "items": rows,
        "limitations": [
            "The reference was drafted by a conversational Codex model and accepted unchanged by one owner; it is not independent gold.",
            "The 40/40 accepted-unchanged and zero-note distribution is a rubber-stamp risk.",
            "Eight reference items embed owner resolutions hidden from the evaluated model, creating an information asymmetry.",
            "This dev-only AF-domain comparison is descriptive and does not establish clinical or disease-general accuracy.",
        ],
    }
    digest = _self_hash(report, "report_id", "report_sha256")
    report["report_id"] = f"decomposition-llama-comparison-dev-{digest[:16]}"
    report["report_sha256"] = digest
    validate_comparison_report(report)
    return report


def validate_prediction_artifact(
    prediction: Mapping[str, Any], inputs: Optional[Mapping[str, Any]] = None
) -> None:
    validate_document(dict(prediction), PREDICTION_SCHEMA)
    expected = _self_hash(prediction, "prediction_id", "prediction_sha256")
    if prediction["prediction_sha256"] != expected or prediction["prediction_id"] != (
        f"decomposition-llama-predictions-dev-{expected[:16]}"
    ):
        raise DecompositionLLMError("Prediction artifact identity mismatch")
    records = prediction["predictions"]
    keys = [(item["nct_id"], item["criterion_id"]) for item in records]
    if len(keys) != len(set(keys)):
        raise DecompositionLLMError("Prediction criterion identities must be unique")
    for item in records:
        valid = item["output_status"] == "valid"
        if valid != (item["expression"] is not None and item["failure_reason"] is None):
            raise DecompositionLLMError("Prediction status, expression, and failure reason disagree")
        if item["output_status"] in {"schema_invalid", "runtime_error"} and item[
            "expression"
        ] is not None:
            raise DecompositionLLMError("Unusable raw output cannot masquerade as an expression")
    if inputs is not None:
        expected_keys = [
            (item["nct_id"], item["criterion_id"])
            for item in inputs["source_package"]["items"]
        ]
        if keys != expected_keys:
            raise DecompositionLLMError("Prediction order or membership changed")


def validate_comparison_report(report: Mapping[str, Any]) -> None:
    validate_document(dict(report), REPORT_SCHEMA)
    expected = _self_hash(report, "report_id", "report_sha256")
    if report["report_sha256"] != expected or report["report_id"] != (
        f"decomposition-llama-comparison-dev-{expected[:16]}"
    ):
        raise DecompositionLLMError("Comparison report identity mismatch")
    rows = report["items"]
    keys = [(item["nct_id"], item["criterion_id"]) for item in rows]
    if len(keys) != len(set(keys)):
        raise DecompositionLLMError("Comparison item identities must be unique")
    observed_asymmetry = [item for item in rows if item["information_asymmetry"]]
    observed_symmetric = [item for item in rows if not item["information_asymmetry"]]
    checks = (
        (report["overall_metrics"], _metrics(rows)),
        (report["information_asymmetry"]["metrics"], _metrics(observed_asymmetry)),
        (report["no_item_resolution_subgroup"]["metrics"], _metrics(observed_symmetric)),
    )
    for observed, recalculated in checks:
        for name, expected_value in recalculated.items():
            if observed[name] != expected_value:
                raise DecompositionLLMError(f"Comparison metric does not reconcile: {name}")
    if report["information_asymmetry"]["item_count"] != len(observed_asymmetry):
        raise DecompositionLLMError("Information-asymmetry count mismatch")
    if report["no_item_resolution_subgroup"]["item_count"] != len(observed_symmetric):
        raise DecompositionLLMError("No-resolution subgroup count mismatch")
    expected_failures = dict(sorted(Counter(
        item["output_status"] for item in rows if item["output_status"] != "valid"
    ).items()))
    if report["failure_counts"] != expected_failures:
        raise DecompositionLLMError("Comparison failure counts do not reconcile")
    for name, interval in report["bootstrap"]["intervals"].items():
        if interval["estimate"] != report["overall_metrics"][name]:
            raise DecompositionLLMError(f"Bootstrap estimate mismatch: {name}")


def run_decomposition_llm_dev(
    root: Path,
    *,
    client: Optional[Any] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    hardware: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    contract = load_decomposition_llm_contract()
    inputs = load_frozen_dev_inputs(root, contract)
    core_path = files("clinical_matcher").joinpath(contract["output_contract"]["core_schema_resource"])
    if hashlib.sha256(core_path.read_bytes()).hexdigest() != contract[
        "output_contract"
    ]["core_schema_file_sha256"]:
        raise DecompositionLLMError("Core output schema hash mismatch")
    resolved_client = client or OllamaLoopbackClient(contract["runtime"]["endpoint"])
    verify_local_runtime(resolved_client, contract)
    catalog = inputs["concept_catalog"]
    guide = inputs["annotation_guide"]
    records = []
    latencies = []
    for index, item in enumerate(inputs["source_package"]["items"], start=1):
        schema = item_bound_output_schema(contract, catalog, item)
        payload = {
            "model": contract["model"]["ollama_model_name"],
            "messages": build_messages(contract, catalog, guide, item),
            "stream": False,
            "format": schema,
            "options": {
                key: contract["decoding"][key]
                for key in ("temperature", "seed", "num_ctx", "num_predict")
            },
            "keep_alive": contract["decoding"]["keep_alive"],
        }
        started = time.monotonic()
        try:
            response = resolved_client.chat(payload)
            content = response.get("message", {}).get("content")
            if not isinstance(content, str):
                expression, status, reason = None, "schema_invalid", "missing_message_content"
            else:
                expression, status, reason = parse_model_output(
                    content, schema, item=item, catalog=catalog, contract=contract
                )
        except (ApixabanStructuredLLMError, OSError, TimeoutError) as error:
            expression, status, reason = None, "runtime_error", type(error).__name__
            response = {}
        latency = time.monotonic() - started
        latencies.append(latency)
        records.append(
            {
                "nct_id": item["nct_id"], "criterion_id": item["criterion_id"],
                "expression": expression, "output_status": status,
                "failure_reason": reason, "latency_seconds": latency,
                "prompt_tokens": int(response.get("prompt_eval_count", 0) or 0),
                "output_tokens": int(response.get("eval_count", 0) or 0),
            }
        )
        if progress:
            progress(index, len(inputs["source_package"]["items"]))
    timestamp = generated_at or _now()
    prediction: Dict[str, Any] = {
        "prediction_version": PREDICTION_VERSION,
        "prediction_id": "decomposition-llama-predictions-dev-0000000000000000",
        "prediction_sha256": "0" * 64,
        "generated_at": timestamp,
        "code_commit": code_commit or current_git_commit(),
        "protocol_version": contract["protocol_version"],
        "split": "dev",
        "contract_id": contract["contract_id"],
        "contract_sha256": contract["contract_sha256"],
        "model_id": model_id(contract),
        "prompt_version": contract["prompt"]["prompt_version"],
        "selection_manifest_sha256": inputs["selection"]["selection_manifest_sha256"],
        "concept_catalog_sha256": inputs["concept_catalog"]["concept_catalog_sha256"],
        "source_package_sha256": inputs["source_package"]["package_sha256"],
        "predictions": records,
        "performance": {
            "request_count": len(records),
            "total_duration_seconds": sum(latencies),
            "latency_seconds_mean": statistics.fmean(latencies),
            "latency_seconds_p95": sorted(latencies)[max(0, round((len(latencies) - 1) * 0.95))],
            "prompt_tokens": sum(item["prompt_tokens"] for item in records),
            "output_tokens": sum(item["output_tokens"] for item in records),
            "hardware": dict(hardware or detect_hardware()),
        },
    }
    digest = _self_hash(prediction, "prediction_id", "prediction_sha256")
    prediction["prediction_id"] = f"decomposition-llama-predictions-dev-{digest[:16]}"
    prediction["prediction_sha256"] = digest
    validate_prediction_artifact(prediction, inputs)
    report = build_comparison_report(prediction, inputs, contract, generated_at=timestamp)
    return prediction, report


def write_decomposition_llm_run(
    prediction: Dict[str, Any], report: Dict[str, Any], output_dir: Path
) -> Tuple[Path, Path]:
    validate_prediction_artifact(prediction)
    validate_comparison_report(report)
    prediction_path = output_dir / "predictions.json"
    report_path = output_dir / "comparison-report.json"
    if prediction_path.exists() or report_path.exists():
        raise FileExistsError("Refusing to overwrite decomposition-model output")
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for path, value in ((prediction_path, prediction), (report_path, report)):
            with path.open("x", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return prediction_path, report_path


def render_comparison_markdown(report: Mapping[str, Any]) -> str:
    validate_comparison_report(report)
    metrics = report["overall_metrics"]
    asymmetry = report["information_asymmetry"]["metrics"]
    return "\n".join(
        [
            "# Local Llama decomposition comparison (dev)", "",
            "This is descriptive agreement with Codex-drafted, owner-accepted assisted silver; it is not accuracy against independent human gold.", "",
            f"- Reference draft model: `{report['model_roles']['reference_draft_model_id']}`",
            f"- Evaluated model: `{report['model_roles']['evaluated_model_id']}`",
            "- Owner review outcome: 40/40 accepted unchanged, 0 edited, 0 review notes",
            f"- Criteria / schema-valid / semantic-valid: {metrics['criteria']} / {metrics['schema_valid_outputs']} / {metrics['semantic_valid_outputs']}", "",
            "## Descriptive agreement", "",
            f"- Atom micro P/R/F1: {metrics['atom_micro_precision']:.4f} / {metrics['atom_micro_recall']:.4f} / {metrics['atom_micro_f1']:.4f}",
            f"- Normalized-tree / operator-topology exact: {metrics['normalized_tree_exact_rate']:.4f} / {metrics['operator_topology_exact_rate']:.4f}",
            f"- Span exact / mean IoU: {metrics['span_exact_rate']:.4f} / {metrics['span_mean_iou']:.4f}", "",
            "## Information-asymmetry subgroup", "",
            "Eight reference items contain owner resolutions hidden from the evaluated model; disagreement is not attributable solely to model capability.",
            f"- Items: {report['information_asymmetry']['item_count']}",
            f"- Atom micro F1: {asymmetry['atom_micro_f1']:.4f}",
            f"- Operator-topology exact: {asymmetry['operator_topology_exact_rate']:.4f}",
            f"- Span mean IoU: {asymmetry['span_mean_iou']:.4f}", "",
            "The unanimous no-note owner-review distribution is disclosed as a rubber-stamp risk. Silver is observation-locked and cannot be changed after this comparison.", "",
        ]
    )
