"""Frozen P7 locked-test batch contract and non-interactive execution helpers.

Importing or validating this module never opens a locked-test artifact. The
runner checks the explicit P7.1/P7.2 authorization state before resolving any
restricted input path.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .apixaban_abstention import abstention_policy, validate_abstention_outputs
from .apixaban_benchmark import file_sha256
from .apixaban_contract import load_question_catalog, question_index
from .apixaban_deterministic import load_deterministic_rule_set
from .apixaban_error_attribution import (
    ERROR_CATEGORIES,
    _attribute_row,
    validate_error_attribution_report,
)
from .apixaban_evaluation import EVALUATION_REPORT_SCHEMA, validate_prediction_set
from .apixaban_single_trial_evaluation import (
    validate_single_trial_report,
    validate_single_trial_trace,
)
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .apixaban_structured_llm import (
    load_long_context_contract,
    load_structured_llm_contract,
)
from .apixaban_split import write_private_json
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256
from .validation import validate_document


CONTRACT_VERSION = "1.0.0"
CONTRACT_RESOURCE = "resources/p7-locked-test-batch-contract-1.0.0.json"
CONTRACT_SCHEMA = "schemas/p7-locked-test-batch-contract-1.0.0.schema.json"
LATENCY_TRACE_SCHEMA = "schemas/p7-request-latency-trace-1.0.0.schema.json"
PUBLIC_CANDIDATE_SCHEMA = "schemas/p7-public-release-candidate-1.0.0.schema.json"
STATE_EVENT_SCHEMA = "schemas/p7-batch-state-event-1.0.0.schema.json"
CASE_PACKAGE_SCHEMA = "schemas/p7-representative-case-package-1.0.0.schema.json"
BATCH_MANIFEST_SCHEMA = "schemas/p7-locked-test-batch-manifest-1.0.0.schema.json"

BASE_ARM_IDS = (
    "rules_1_0_0",
    "llama31_structured_1_0_0",
    "llama31_long_context_1_0_0",
)
VIEW_IDS = tuple(
    view
    for arm_id in BASE_ARM_IDS
    for view in (f"{arm_id}.raw", f"{arm_id}.p4_3")
)
PUBLIC_ALLOWLIST = (
    "typed_exact_match",
    "boolean_macro_f1",
    "numeric_status_macro_f1",
    "abstained_or_unknown_count",
    "latency_seconds_p50",
    "latency_seconds_p95",
)
P4_3_POLICY_SHA256 = (
    "0946bb21ec4ab8e693c3abc15d6625ae09ead4acbd3700ae42e484b043f36fa8"
)
EMPTY_CONFLICT_KEYS_SHA256 = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)
VALIDATION_PROJECTION_PINS = (
    {
        "arm_id": "rules_1_0_0",
        "prediction_sha256": (
            "5f3975632eb7a74ec1542cde9ec6ff1af062ed54b8c3a2459102651761c5ce88"
        ),
        "inference_config_sha256": (
            "01162d132746a18ca5e6ecea1ebcb5b6a50a0c7acb015fd7fdfde7f8725ee447"
        ),
        "report_sha256": (
            "c7c77fd69129889ef715d8bf5e71629d0f25b4486d2f9445b7242cdd54403402"
        ),
        "code_commit": "d8346fc2bf40e2b7011863083bb94d3dd4e15316",
        "storage": "owner_only_outside_repository",
    },
    {
        "arm_id": "llama31_structured_1_0_0",
        "prediction_sha256": (
            "499f6ec444b3b82204ce1826713b3ebd0d365b9f417f2937f3814a8ecf606fc5"
        ),
        "inference_config_sha256": (
            "ab4716f4fd8c3dcc533853ad4528317a2579c8941fe621841ebf9157c82dc04c"
        ),
        "report_sha256": (
            "0479f6581e09aeb2703396b62e6728fc67b46b311cbc2608655c3b3c786e91dd"
        ),
        "code_commit": "d8346fc2bf40e2b7011863083bb94d3dd4e15316",
        "storage": "owner_only_outside_repository",
    },
    {
        "arm_id": "llama31_long_context_1_0_0",
        "prediction_sha256": (
            "cdbe3f8a028616b42ad4020fe6bbf23a54d1030091ac72362e863759dc5a3dd9"
        ),
        "inference_config_sha256": (
            "d70d4fc37ee97848039664b0400f1f194cb2752796d70901ddfcf0afc3683a79"
        ),
        "report_sha256": (
            "5586de9e9e06413801ff79fd6ff269f9b803a832cb47f080f0917744d544b827"
        ),
        "code_commit": "d8346fc2bf40e2b7011863083bb94d3dd4e15316",
        "storage": "owner_only_outside_repository",
    },
)


class P7LockedTestError(ValueError):
    """Raised when the P7 frozen single-batch contract is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any], field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _expected_config_hashes() -> Dict[str, str]:
    return {
        "rules_1_0_0": canonical_sha256(load_deterministic_rule_set()),
        "llama31_structured_1_0_0": canonical_sha256(
            load_structured_llm_contract()
        ),
        "llama31_long_context_1_0_0": canonical_sha256(
            load_long_context_contract()
        ),
    }


def validate_p7_contract(
    document: Dict[str, Any], *, repository_root: Optional[Path] = None
) -> None:
    validate_document(document, CONTRACT_SCHEMA)
    if document["contract_sha256"] != _self_hash(document, "contract_sha256"):
        raise P7LockedTestError("P7 contract self-hash mismatch")

    status = document["contract_status"]
    authorization = document["authorization"]
    expected_authorization = {
        "owner_approved_pre_implementation_not_executable": (False, False, False),
        "implementation_complete_owner_review_required_not_executable": (
            False,
            False,
            False,
        ),
        "owner_approved_frozen_p7_1_not_p7_2_authorized": (True, False, False),
        "owner_approved_frozen_p7_1_and_p7_2_authorized": (True, True, True),
    }[status]
    observed_authorization = (
        authorization["p7_1_frozen"],
        authorization["p7_2_authorized"],
        authorization["locked_test_access_allowed"],
    )
    if observed_authorization != expected_authorization:
        raise P7LockedTestError("P7 authorization state is inconsistent")

    execution = document["execution"]
    if tuple(execution["base_arm_order"]) != BASE_ARM_IDS:
        raise P7LockedTestError("P7 base-arm order changed")
    if tuple(execution["view_order"]) != VIEW_IDS:
        raise P7LockedTestError("P7 raw/projected view order changed")

    arms = document["base_arms"]
    if tuple(item["arm_id"] for item in arms) != BASE_ARM_IDS:
        raise P7LockedTestError("P7 base-arm definitions changed")
    expected_kinds = {
        "rules_1_0_0": "deterministic_rules",
        "llama31_structured_1_0_0": "local_structured_llm",
        "llama31_long_context_1_0_0": "local_long_context_llm",
    }
    expected_hashes = _expected_config_hashes()
    for arm in arms:
        arm_id = arm["arm_id"]
        if arm["kind"] != expected_kinds[arm_id]:
            raise P7LockedTestError(f"P7 arm kind changed: {arm_id}")
        if arm["config_sha256"] != expected_hashes[arm_id]:
            raise P7LockedTestError(f"P7 arm config hash changed: {arm_id}")

    projection = document["p4_3_projection"]
    if canonical_sha256(abstention_policy()) != P4_3_POLICY_SHA256:
        raise P7LockedTestError("Runtime P4.3 policy differs from the reviewed hash")
    if projection["policy_sha256"] != P4_3_POLICY_SHA256:
        raise P7LockedTestError("P7 contract P4.3 policy hash changed")
    if projection["verifier_conflict_keys_sha256"] != EMPTY_CONFLICT_KEYS_SHA256:
        raise P7LockedTestError("P7 verifier-conflict input is not frozen empty")
    if tuple(projection["validation_projections"]) != VALIDATION_PROJECTION_PINS:
        raise P7LockedTestError("P7 validation projection pins changed")

    evaluations = {item["evaluation_id"]: item for item in document["evaluations"]}
    if set(evaluations) != {
        "p1_5_mixed_facts_1_0_0",
        "p4_5_observable_attribution_1_1_0",
        "p4_7_three_axis_1_1_0",
    }:
        raise P7LockedTestError("P7 evaluation set changed")
    for evaluation_id in (
        "p1_5_mixed_facts_1_0_0",
        "p4_5_observable_attribution_1_1_0",
    ):
        if tuple(evaluations[evaluation_id]["views"]) != VIEW_IDS:
            raise P7LockedTestError(f"P7 six-view coverage changed: {evaluation_id}")
    if evaluations["p4_7_three_axis_1_1_0"]["views"] != [
        "llama31_long_context_1_0_0.p4_3"
    ]:
        raise P7LockedTestError("P4.7 must evaluate only the final projected view")

    if tuple(document["disclosure"]["whole_split_allowlist"]) != PUBLIC_ALLOWLIST:
        raise P7LockedTestError("P7 public whole-split allowlist changed")

    implementation = document["implementation"]
    if implementation["pin_status"] == "complete" and not implementation["files"]:
        raise P7LockedTestError("Complete P7 implementation pin is empty")

    if repository_root is not None:
        root = repository_root.resolve()
        for item in [*arms, *implementation["files"]]:
            relative = Path(item.get("resource_path", item.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise P7LockedTestError("P7 pinned path is not repository-relative")
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise P7LockedTestError("P7 pinned path escapes repository") from error
            expected = item.get("resource_file_sha256", item.get("sha256"))
            if file_sha256(path) != expected:
                raise P7LockedTestError(f"P7 pinned file hash mismatch: {relative}")


def load_p7_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_p7_contract(document)
    return document


def require_locked_test_authorization(document: Dict[str, Any]) -> None:
    """Fail before any restricted path is resolved or opened."""

    validate_p7_contract(document)
    if document["contract_status"] != (
        "owner_approved_frozen_p7_1_and_p7_2_authorized"
    ):
        raise P7LockedTestError("P7.2 locked-test execution is not authorized")
    if document["implementation"]["pin_status"] != "complete":
        raise P7LockedTestError("P7 implementation hashes are not frozen")


def _nearest_percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise P7LockedTestError("Latency trace must contain requests")
    index = max(
        0,
        min(len(ordered) - 1, int(round((len(ordered) - 1) * probability))),
    )
    return ordered[index]


def build_request_latency_trace(
    *, arm_id: str, split_name: str, requests: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    rows = [dict(item) for item in requests]
    latencies = [float(item["latency_seconds"]) for item in rows]
    trace: Dict[str, Any] = {
        "trace_version": "1.0.0",
        "trace_sha256": "pending",
        "arm_id": arm_id,
        "split_name": split_name,
        "request_count": len(rows),
        "requests": rows,
        "summary": {
            "latency_seconds_mean": statistics.fmean(latencies),
            "latency_seconds_p50": _nearest_percentile(latencies, 0.50),
            "latency_seconds_p95": _nearest_percentile(latencies, 0.95),
        },
        "owner_only": True,
    }
    trace["trace_sha256"] = _self_hash(trace, "trace_sha256")
    validate_request_latency_trace(trace)
    return trace


def validate_request_latency_trace(document: Dict[str, Any]) -> None:
    validate_document(document, LATENCY_TRACE_SCHEMA)
    if document["trace_sha256"] != _self_hash(document, "trace_sha256"):
        raise P7LockedTestError("P7 latency trace self-hash mismatch")
    rows = document["requests"]
    if document["request_count"] != len(rows):
        raise P7LockedTestError("P7 latency request count mismatch")
    if [item["request_index"] for item in rows] != list(range(1, len(rows) + 1)):
        raise P7LockedTestError("P7 latency request order is not contiguous")
    if len({item["patient_id"] for item in rows}) != len(rows):
        raise P7LockedTestError("P7 latency trace repeats a patient")
    latencies = [float(item["latency_seconds"]) for item in rows]
    expected = {
        "latency_seconds_mean": statistics.fmean(latencies),
        "latency_seconds_p50": _nearest_percentile(latencies, 0.50),
        "latency_seconds_p95": _nearest_percentile(latencies, 0.95),
    }
    if document["summary"] != expected:
        raise P7LockedTestError("P7 latency summary does not reproduce")


def write_request_latency_trace(document: Dict[str, Any], path: Path) -> Path:
    validate_request_latency_trace(document)
    return write_private_json(document, path)


def build_public_release_candidate(
    *,
    contract: Dict[str, Any],
    evaluation_reports: Mapping[str, Mapping[str, Any]],
    predictions: Mapping[str, Mapping[str, Any]],
    latency_traces: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    validate_p7_contract(contract)
    if tuple(evaluation_reports) != VIEW_IDS or tuple(predictions) != VIEW_IDS:
        raise P7LockedTestError("Public projection requires the exact six-view order")
    if tuple(latency_traces) != BASE_ARM_IDS:
        raise P7LockedTestError("Public projection requires the exact base-arm order")

    views = []
    for view_id in VIEW_IDS:
        report = evaluation_reports[view_id]
        prediction = predictions[view_id]
        metrics = report["metrics"]
        views.append(
            {
                "view_id": view_id,
                "typed_exact_match": metrics["typed_exact_match"],
                "boolean_macro_f1": metrics["boolean"]["macro_f1"],
                "numeric_status_macro_f1": metrics["numeric_status"]["macro_f1"],
                "abstained_or_unknown_count": sum(
                    item["fact_status"] == "unknown" or item["abstained"]
                    for item in prediction["predictions"]
                ),
            }
        )

    latency = []
    for arm_id in BASE_ARM_IDS:
        validate_request_latency_trace(dict(latency_traces[arm_id]))
        summary = latency_traces[arm_id]["summary"]
        latency.append(
            {
                "arm_id": arm_id,
                "latency_seconds_p50": summary["latency_seconds_p50"],
                "latency_seconds_p95": summary["latency_seconds_p95"],
            }
        )

    candidate: Dict[str, Any] = {
        "candidate_version": "1.0.0",
        "candidate_sha256": "pending",
        "status": "unreviewed_public_release_candidate",
        "contract_sha256": contract["contract_sha256"],
        "policy": {
            "policy_level": contract["disclosure"]["policy_level"],
            "institutional_approval_claimed": False,
            "allowlist": list(PUBLIC_ALLOWLIST),
            "selection_after_result_forbidden": True,
        },
        "views": views,
        "base_arm_latency": latency,
        "limitations": [
            "This is a project-level disclosure candidate, not institutional approval.",
            (
                "Per-question, per-class, confusion-matrix, patient-level, "
                "rule-level, unit, and P4.7 values remain owner-only."
            ),
            (
                "The benchmark evaluates released note-grounded facts, not "
                "clinical eligibility accuracy."
            ),
        ],
    }
    candidate["candidate_sha256"] = _self_hash(candidate, "candidate_sha256")
    validate_public_release_candidate(candidate, contract)
    return candidate


def validate_public_release_candidate(
    document: Dict[str, Any], contract: Optional[Dict[str, Any]] = None
) -> None:
    validate_document(document, PUBLIC_CANDIDATE_SCHEMA)
    if document["candidate_sha256"] != _self_hash(document, "candidate_sha256"):
        raise P7LockedTestError("P7 public candidate self-hash mismatch")
    if tuple(item["view_id"] for item in document["views"]) != VIEW_IDS:
        raise P7LockedTestError("P7 public candidate view order changed")
    if tuple(item["arm_id"] for item in document["base_arm_latency"]) != BASE_ARM_IDS:
        raise P7LockedTestError("P7 public candidate arm order changed")
    if tuple(document["policy"]["allowlist"]) != PUBLIC_ALLOWLIST:
        raise P7LockedTestError("P7 public candidate contains a changed allowlist")
    if contract is not None and document["contract_sha256"] != contract[
        "contract_sha256"
    ]:
        raise P7LockedTestError("P7 public candidate contract hash mismatch")


def build_state_event(
    *,
    contract_sha256: str,
    event: str,
    attempt: int,
    gold_backed_phase_started: bool,
    reason_code: Optional[str] = None,
    artifact_count: int = 0,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "event_version": "1.0.0",
        "event_sha256": "pending",
        "event": event,
        "contract_sha256": contract_sha256,
        "attempt": attempt,
        "created_at": created_at or _now(),
        "gold_backed_phase_started": gold_backed_phase_started,
        "details": {
            "reason_code": reason_code,
            "artifact_count": artifact_count,
        },
    }
    document["event_sha256"] = _self_hash(document, "event_sha256")
    validate_state_event(document)
    return document


def validate_state_event(document: Dict[str, Any]) -> None:
    validate_document(document, STATE_EVENT_SCHEMA)
    if document["event_sha256"] != _self_hash(document, "event_sha256"):
        raise P7LockedTestError("P7 state-event self-hash mismatch")
    event = document["event"]
    started = document["gold_backed_phase_started"]
    if event in {"attempt_started", "pre_gold_failed", "raw_complete"} and started:
        raise P7LockedTestError("Pre-gold P7 event claims gold exposure")
    if event in {"gold_phase_started", "terminal_failed", "batch_complete"} and not started:
        raise P7LockedTestError("Post-gold P7 event omits gold exposure")


def write_state_event(document: Dict[str, Any], path: Path) -> Path:
    validate_state_event(document)
    return write_private_json(document, path)


def build_representative_case_package(
    *,
    contract: Dict[str, Any],
    view_id: str,
    split_name: str,
    prediction_set: Dict[str, Any],
    benchmark: Dict[str, Any],
    staging_corpus: Dict[str, Any],
    expected_patient_ids: Sequence[str],
) -> Dict[str, Any]:
    """Select one owner-only case per observed category without human choice."""

    validate_p7_contract(contract)
    if view_id != "llama31_long_context_1_0_0.p4_3":
        raise P7LockedTestError("Representative review is frozen to the final view")
    validate_prediction_set(prediction_set)
    validate_apixaban_staging_corpus(staging_corpus)
    expected_patients = set(expected_patient_ids)
    predictions = {
        (item["patient_id"], item["question_id"]): item
        for item in prediction_set["predictions"]
    }
    gold = {
        (item["patient_id"], item["question_id"]): item
        for item in benchmark["assessments"]
        if item["patient_id"] in expected_patients
    }
    catalog = load_question_catalog()
    questions = question_index(catalog)
    expected_keys = {
        (patient_id, question_id)
        for patient_id in expected_patients
        for question_id in questions
    }
    if set(predictions) != expected_keys or set(gold) != expected_keys:
        raise P7LockedTestError("Representative review grid is incomplete")
    patients = {
        item["patient_id"]: item for item in staging_corpus["patients"]
    }
    if not expected_patients.issubset(patients):
        raise P7LockedTestError("Representative review patient evidence is missing")

    candidates: Dict[str, list[Dict[str, Any]]] = {
        category: [] for category in ERROR_CATEGORIES
    }
    salt = "clinicalmatcher-p7-p4-5-case-review-v1"
    for patient_id, question_id in sorted(expected_keys):
        prediction = predictions[(patient_id, question_id)]
        gold_item = gold[(patient_id, question_id)]
        question = questions[question_id]
        patient_evidence_ids = {
            item["evidence_id"] for item in patients[patient_id]["evidence"]
        }
        category = _attribute_row(
            gold=gold_item,
            prediction=prediction,
            question=question,
            canonical_unit=question["canonical_unit"],
            patient_evidence_ids=patient_evidence_ids,
        )
        if category is None:
            continue
        digest = hashlib.sha256(
            "\0".join(
                (
                    "lowest_sha256_per_observed_attribution_category/1.0.0",
                    salt,
                    contract["contract_sha256"],
                    view_id,
                    category,
                    patient_id,
                    question_id,
                )
            ).encode("utf-8")
        ).hexdigest()
        candidates[category].append(
            {
                "category": category,
                "sampling_digest": digest,
                "patient_id": patient_id,
                "question_id": question_id,
                "source_question": question["source_question"],
                "gold": {
                    "fact_status": gold_item["fact_status"],
                    "value": gold_item["value"],
                    "unit": gold_item["unit"],
                },
                "prediction": {
                    "fact_status": prediction["fact_status"],
                    "value": prediction["value"],
                    "unit": prediction["unit"],
                    "evidence_ids": list(prediction["evidence_ids"]),
                    "abstained": prediction["abstained"],
                    "abstention_reason": prediction["abstention_reason"],
                },
                "patient_evidence": [
                    {"evidence_id": item["evidence_id"], "text": item["text"]}
                    for item in patients[patient_id]["evidence"]
                ],
            }
        )
    cases = [
        min(candidates[category], key=lambda item: item["sampling_digest"])
        for category in ERROR_CATEGORIES
        if candidates[category]
    ]
    if not cases:
        raise P7LockedTestError("Representative review has no observed errors")
    package: Dict[str, Any] = {
        "package_version": "1.0.0",
        "package_sha256": "pending",
        "algorithm": "lowest_sha256_per_observed_attribution_category/1.0.0",
        "salt": salt,
        "contract_sha256": contract["contract_sha256"],
        "view_id": view_id,
        "split_name": split_name,
        "cases": cases,
        "review_status": "pending_owner_review_after_immutable_batch",
        "owner_only": True,
    }
    package["package_sha256"] = _self_hash(package, "package_sha256")
    validate_representative_case_package(package, contract)
    return package


def validate_representative_case_package(
    document: Dict[str, Any], contract: Optional[Dict[str, Any]] = None
) -> None:
    validate_document(document, CASE_PACKAGE_SCHEMA)
    if document["package_sha256"] != _self_hash(document, "package_sha256"):
        raise P7LockedTestError("Representative case package hash mismatch")
    categories = [item["category"] for item in document["cases"]]
    expected = [category for category in ERROR_CATEGORIES if category in categories]
    if categories != expected or len(categories) != len(set(categories)):
        raise P7LockedTestError("Representative cases are not in frozen category order")
    if contract is not None and document["contract_sha256"] != contract[
        "contract_sha256"
    ]:
        raise P7LockedTestError("Representative case package contract mismatch")


def _validated_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise P7LockedTestError("P7 manifest contains an unsafe artifact path")
    if path.as_posix() != value or value.startswith("./"):
        raise P7LockedTestError("P7 manifest path is not canonical POSIX form")
    return path


def validate_p4_3_parent_derivation(
    *,
    raw_prediction_sha256: str,
    projected_prediction: Dict[str, Any],
    projection_report: Dict[str, Any],
) -> None:
    validate_abstention_outputs(projected_prediction, projection_report)
    expected_config = canonical_sha256(
        {
            "policy": abstention_policy(),
            "source_prediction_sha256": raw_prediction_sha256,
            "verifier_conflict_status": "not_evaluable",
            "verifier_conflict_keys_sha256": EMPTY_CONFLICT_KEYS_SHA256,
        }
    )
    if projected_prediction["inference_config_sha256"] != expected_config:
        raise P7LockedTestError("P7 P4.3 config derivation mismatch")
    provenance = projection_report["provenance"]
    if provenance["source_prediction_sha256"] != raw_prediction_sha256:
        raise P7LockedTestError("P7 P4.3 parent hash mismatch")
    if provenance["projected_prediction_content_sha256"] != canonical_sha256(
        projected_prediction
    ):
        raise P7LockedTestError("P7 P4.3 projected content hash mismatch")


def validate_batch_manifest(
    document: Dict[str, Any],
    *,
    contract: Optional[Dict[str, Any]] = None,
    artifact_root: Optional[Path] = None,
) -> None:
    validate_document(document, BATCH_MANIFEST_SCHEMA)
    if document["manifest_sha256"] != _self_hash(document, "manifest_sha256"):
        raise P7LockedTestError("P7 batch manifest self-hash mismatch")
    if tuple(item["arm_id"] for item in document["base_arms"]) != BASE_ARM_IDS:
        raise P7LockedTestError("P7 batch manifest base-arm order changed")
    if tuple(item["view_id"] for item in document["views"]) != VIEW_IDS:
        raise P7LockedTestError("P7 batch manifest view order changed")
    if document["p4_7"]["view_id"] != "llama31_long_context_1_0_0.p4_3":
        raise P7LockedTestError("P7 batch manifest P4.7 view changed")

    raw_views = {f"{arm_id}.raw" for arm_id in BASE_ARM_IDS}
    for item in document["views"]:
        is_raw = item["view_id"] in raw_views
        if is_raw != (item["p4_3_report_path"] is None):
            raise P7LockedTestError("P7 raw/projected P4.3 lineage is inconsistent")
        if is_raw != (item["p4_3_report_sha256"] is None):
            raise P7LockedTestError("P7 raw/projected P4.3 hash is inconsistent")

    expected_event_names = (
        ("attempt_started", "raw_complete", "gold_phase_started", "batch_complete")
        if document["attempt"] == 1
        else (
            "attempt_started",
            "pre_gold_failed",
            "attempt_started",
            "raw_complete",
            "gold_phase_started",
            "batch_complete",
        )
    )
    events = document["events"]
    if tuple(item["event"] for item in events) != expected_event_names:
        raise P7LockedTestError("P7 batch event sequence is not the frozen sequence")
    expected_attempts = (1, 1, 1, 1) if document["attempt"] == 1 else (1, 1, 2, 2, 2, 2)
    if tuple(item["attempt"] for item in events) != expected_attempts:
        raise P7LockedTestError("P7 batch event attempt sequence is inconsistent")

    path_hash_pairs = []
    for arm in document["base_arms"]:
        path_hash_pairs.extend(
            (
                (arm["prediction_path"], arm["prediction_sha256"]),
                (arm["latency_trace_path"], arm["latency_trace_sha256"]),
            )
        )
        if arm["run_report_path"] is not None:
            path_hash_pairs.append(
                (arm["run_report_path"], arm["run_report_sha256"])
            )
    for view in document["views"]:
        path_hash_pairs.extend(
            (
                (view["prediction_path"], view["prediction_sha256"]),
                (view["p1_5_report_path"], view["p1_5_report_sha256"]),
                (view["p4_5_report_path"], view["p4_5_report_sha256"]),
            )
        )
        if view["p4_3_report_path"] is not None:
            path_hash_pairs.append(
                (view["p4_3_report_path"], view["p4_3_report_sha256"])
            )
    for name in ("report", "trace", "summary"):
        path_hash_pairs.append(
            (
                document["p4_7"][f"{name}_path"],
                document["p4_7"][f"{name}_sha256"],
            )
        )
    for key in ("representative_case_package", "public_candidate"):
        artifact = document[key]
        path_hash_pairs.append((artifact["path"], artifact["sha256"]))
    path_hash_pairs.extend((item["path"], item["sha256"]) for item in events)

    paths = [value for value, _ in path_hash_pairs]
    if len(paths) != len(set(paths)):
        raise P7LockedTestError("P7 batch manifest repeats an artifact path")
    for value in paths:
        _validated_relative_path(value)

    if contract is not None:
        validate_p7_contract(contract)
        if document["contract_sha256"] != contract["contract_sha256"]:
            raise P7LockedTestError("P7 batch manifest contract hash mismatch")
    if artifact_root is not None:
        root = artifact_root.resolve()
        for value, expected_sha256 in path_hash_pairs:
            path = (root / _validated_relative_path(value)).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise P7LockedTestError("P7 artifact escapes its root") from error
            if file_sha256(path) != expected_sha256:
                raise P7LockedTestError(f"P7 artifact hash mismatch: {value}")

        base_by_arm = {item["arm_id"]: item for item in document["base_arms"]}
        views_by_id = {item["view_id"]: item for item in document["views"]}
        for arm_id in BASE_ARM_IDS:
            raw = views_by_id[f"{arm_id}.raw"]
            projected = views_by_id[f"{arm_id}.p4_3"]
            base = base_by_arm[arm_id]
            if (
                raw["prediction_path"] != base["prediction_path"]
                or raw["prediction_sha256"] != base["prediction_sha256"]
            ):
                raise P7LockedTestError("P7 base arm and raw view are not identical")
            raw_sha256 = raw["prediction_sha256"]
            projected_document = json.loads(
                (root / projected["prediction_path"]).read_text(encoding="utf-8")
            )
            projection_report = json.loads(
                (root / projected["p4_3_report_path"]).read_text(encoding="utf-8")
            )
            validate_p4_3_parent_derivation(
                raw_prediction_sha256=raw_sha256,
                projected_prediction=projected_document,
                projection_report=projection_report,
            )

        for item in document["views"]:
            prediction = json.loads(
                (root / item["prediction_path"]).read_text(encoding="utf-8")
            )
            validate_prediction_set(prediction)
            p1_5 = json.loads(
                (root / item["p1_5_report_path"]).read_text(encoding="utf-8")
            )
            validate_document(p1_5, EVALUATION_REPORT_SCHEMA)
            if p1_5["provenance"]["prediction_set_sha256"] != item[
                "prediction_sha256"
            ]:
                raise P7LockedTestError("P7 P1.5 prediction binding mismatch")
            p4_5 = json.loads(
                (root / item["p4_5_report_path"]).read_text(encoding="utf-8")
            )
            validate_error_attribution_report(p4_5)
            if p4_5["provenance"]["prediction_set_sha256"] != item[
                "prediction_sha256"
            ]:
                raise P7LockedTestError("P7 P4.5 prediction binding mismatch")

        p4_7_report = json.loads(
            (root / document["p4_7"]["report_path"]).read_text(encoding="utf-8")
        )
        p4_7_trace = json.loads(
            (root / document["p4_7"]["trace_path"]).read_text(encoding="utf-8")
        )
        validate_single_trial_report(p4_7_report)
        validate_single_trial_trace(p4_7_trace)
        if p4_7_report["provenance"]["trace_sha256"] != p4_7_trace[
            "trace_sha256"
        ]:
            raise P7LockedTestError("P7 P4.7 trace binding mismatch")
        if p4_7_report["provenance"]["run_contract_sha256"] != document[
            "contract_sha256"
        ]:
            raise P7LockedTestError("P7 P4.7 contract binding mismatch")

        case_document = json.loads(
            (root / document["representative_case_package"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        validate_representative_case_package(case_document, contract)
        public_document = json.loads(
            (root / document["public_candidate"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        validate_public_release_candidate(public_document, contract)


def build_batch_manifest(
    *,
    contract: Dict[str, Any],
    attempt: int,
    base_arms: Sequence[Mapping[str, Any]],
    views: Sequence[Mapping[str, Any]],
    p4_7: Mapping[str, Any],
    representative_case_package: Mapping[str, str],
    public_candidate: Mapping[str, str],
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {
        "manifest_version": "1.0.0",
        "manifest_sha256": "pending",
        "status": "complete_immutable_owner_review_required",
        "contract_sha256": contract["contract_sha256"],
        "attempt": attempt,
        "split_name": "test",
        "patient_count": contract["execution"]["expected_patient_count"],
        "row_count_per_view": contract["execution"]["expected_row_count"],
        "base_arms": [dict(item) for item in base_arms],
        "views": [dict(item) for item in views],
        "p4_7": dict(p4_7),
        "representative_case_package": dict(representative_case_package),
        "public_candidate": dict(public_candidate),
        "events": [dict(item) for item in events],
        "only_locked_test_exposure": True,
        "reuse_for_all_p5_p7_reports": True,
        "owner_only": True,
    }
    manifest["manifest_sha256"] = _self_hash(manifest, "manifest_sha256")
    validate_batch_manifest(manifest, contract=contract)
    return manifest


def write_batch_manifest(document: Dict[str, Any], path: Path) -> Path:
    validate_batch_manifest(document)
    return write_private_json(document, path)


def assert_private_inputs(paths: Sequence[Path]) -> None:
    for path in paths:
        assert_restricted_local_path(path)
        if path.is_symlink() or not path.is_file():
            raise P7LockedTestError(f"Missing restricted P7 input: {path}")
        if path.stat().st_mode & 0o077:
            raise P7LockedTestError(f"Restricted P7 input is not owner-only: {path}")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_private_text(value: str, path: Path) -> Path:
    assert_restricted_local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
