"""Frozen public AF trial source-pool selection for decomposition."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from importlib.resources import files
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from ..validation import validate_document
from .trial_selection import TrialSelectionError
from .trials import NCT_PATTERN


CONTRACT_RESOURCE = "resources/decomposition-source-pool-contract-1.0.0.json"
CONTRACT_SCHEMA = "schemas/decomposition-source-pool-contract-1.0.0.schema.json"
AUDIT_SCHEMA = "schemas/decomposition-source-selection-audit-1.0.0.schema.json"
AUDIT_VERSION = "decomposition-source-selection-audit/1.0.0"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _self_hash(document: Mapping[str, Any], id_field: str, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(id_field, None)
    payload.pop(hash_field, None)
    return _canonical_hash(payload)


def load_decomposition_source_pool_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    contract = json.loads(resource.read_text(encoding="utf-8"))
    validate_decomposition_source_pool_contract(contract)
    return contract


def validate_decomposition_source_pool_contract(contract: Dict[str, Any]) -> None:
    validate_document(contract, CONTRACT_SCHEMA)
    expected = _self_hash(contract, "contract_id", "contract_sha256")
    if contract["contract_sha256"] != expected:
        raise TrialSelectionError("Decomposition source-pool contract hash mismatch")
    if contract["contract_id"] != f"decomposition-source-pool-{expected[:16]}":
        raise TrialSelectionError("Decomposition source-pool contract ID mismatch")


def source_pool_selection_document(contract: Dict[str, Any]) -> Dict[str, Any]:
    validate_decomposition_source_pool_contract(contract)
    return {
        "disease_domain": contract["scope"]["disease_domain"],
        "rationale": (
            "Owner-approved AF interventional public source pool for the "
            "single-domain criteria-decomposition benchmark"
        ),
        "query_parameters": dict(sorted(contract["query"]["parameters"].items())),
        "filters": contract["filters"],
        "sampling": contract["sampling"],
        "source_pool_contract_binding": {
            "contract_id": contract["contract_id"],
            "contract_sha256": contract["contract_sha256"],
        },
        "scope_limitation": contract["scope"]["generalization_claim"],
    }


def _study_fields(study: Dict[str, Any]) -> Dict[str, Any]:
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    eligibility = protocol.get("eligibilityModule", {})
    return {
        "nct_id": identification.get("nctId"),
        "study_type": design.get("studyType"),
        "overall_status": status.get("overallStatus"),
        "first_posted": status.get("studyFirstPostDateStruct", {}).get("date"),
        "has_eligibility_text": (
            isinstance(eligibility.get("eligibilityCriteria"), str)
            and bool(eligibility["eligibilityCriteria"].strip())
        ),
    }


def _sampling_hash(contract: Dict[str, Any], nct_id: str) -> str:
    sampling = contract["sampling"]
    payload = "\0".join(
        (
            sampling["method"],
            sampling["salt"],
            contract["contract_sha256"],
            nct_id,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _filter_reasons(fields: Dict[str, Any], contract: Dict[str, Any]) -> list[str]:
    filters = contract["filters"]
    reasons = []
    if fields["study_type"] not in filters["study_types"]:
        reasons.append("study_type_not_allowed")
    if fields["overall_status"] not in filters["overall_statuses"]:
        reasons.append("recruitment_status_not_allowed")
    if not fields["has_eligibility_text"]:
        reasons.append("eligibility_text_missing")
    try:
        first_posted = date.fromisoformat(fields["first_posted"])
    except (TypeError, ValueError):
        reasons.append("first_posted_date_missing_or_invalid")
    else:
        if not (
            date.fromisoformat(filters["first_posted_from"])
            <= first_posted
            <= date.fromisoformat(filters["first_posted_to"])
        ):
            reasons.append("first_posted_date_outside_range")
    return sorted(reasons)


def select_decomposition_source_trials(
    *,
    studies: Sequence[Dict[str, Any]],
    registry_reported_total_count: int,
    pages_fetched: int,
    version_payload: Dict[str, Any],
    queried_at: str,
    contract: Optional[Dict[str, Any]] = None,
) -> Tuple[Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    """Filter a complete registry result, then hash-sample forty trials."""
    frozen = contract or load_decomposition_source_pool_contract()
    validate_decomposition_source_pool_contract(frozen)
    expected_total = frozen["query"]["expected_registry_total_count"]
    if registry_reported_total_count != expected_total:
        raise TrialSelectionError(
            "Registry total changed from the owner-approved source-pool "
            f"contract: expected {expected_total}, got {registry_reported_total_count}"
        )
    if len(studies) != registry_reported_total_count:
        raise TrialSelectionError("Complete registry fetch is required")
    if pages_fetched < 1:
        raise TrialSelectionError("pages_fetched must be positive")
    api_version = version_payload.get("apiVersion")
    data_timestamp = version_payload.get("dataTimestamp")
    if not isinstance(api_version, str) or not api_version:
        raise TrialSelectionError("API version is missing")
    if not isinstance(data_timestamp, str) or not data_timestamp:
        raise TrialSelectionError("API data timestamp is missing")

    indexed: Dict[str, Dict[str, Any]] = {}
    records = []
    for study in studies:
        if not isinstance(study, dict):
            raise TrialSelectionError("Every registry hit must be an object")
        fields = _study_fields(study)
        nct_id = fields["nct_id"]
        if not isinstance(nct_id, str) or not NCT_PATTERN.fullmatch(nct_id):
            raise TrialSelectionError("Registry hit has invalid NCT ID")
        if nct_id in indexed:
            raise TrialSelectionError(f"Duplicate registry NCT ID: {nct_id}")
        indexed[nct_id] = study
        reasons = _filter_reasons(fields, frozen)
        digest = None if reasons else _sampling_hash(frozen, nct_id)
        records.append(
            {
                **fields,
                "source_study_sha256": _canonical_hash(study),
                "filter_passed": not reasons,
                "filter_exclusion_reasons": reasons,
                "sampling_hash": digest,
                "selected": False,
                "selection_reason": (
                    "excluded_by_explicit_filter"
                    if reasons
                    else "hash_rank_outside_target"
                ),
            }
        )

    eligible = sorted(
        (record for record in records if record["filter_passed"]),
        key=lambda item: (item["sampling_hash"], item["nct_id"]),
    )
    target = frozen["sampling"]["target_study_count"]
    if len(eligible) < target:
        raise TrialSelectionError(
            f"Only {len(eligible)} trials passed filters; {target} are required"
        )
    selected_ids = {record["nct_id"] for record in eligible[:target]}
    for record in records:
        if record["nct_id"] in selected_ids:
            record["selected"] = True
            record["selection_reason"] = (
                "passed_filters_and_hash_rank_within_target"
            )

    reason_counts: Dict[str, int] = {}
    for record in records:
        for reason in record["filter_exclusion_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    audit: Dict[str, Any] = {
        "selection_audit_version": AUDIT_VERSION,
        "source_pool_contract_id": frozen["contract_id"],
        "source_pool_contract_sha256": frozen["contract_sha256"],
        "selection": source_pool_selection_document(frozen),
        "queried_at": queried_at,
        "api_version": api_version,
        "api_data_timestamp": data_timestamp,
        "query_parameters": dict(sorted(frozen["query"]["parameters"].items())),
        "flow": {
            "registry_reported_total_count": registry_reported_total_count,
            "fetched_candidate_count": len(studies),
            "filter_passed_count": len(eligible),
            "filter_excluded_count": len(studies) - len(eligible),
            "eligible_not_sampled_count": len(eligible) - target,
            "selected_count": target,
            "pages_fetched": pages_fetched,
        },
        "filter_exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "records": sorted(records, key=lambda item: item["nct_id"]),
    }
    digest = _self_hash(
        audit, "selection_audit_id", "selection_audit_sha256"
    )
    audit["selection_audit_id"] = (
        f"decomposition-source-selection-{digest[:16]}"
    )
    audit["selection_audit_sha256"] = digest
    validate_decomposition_source_selection_audit(audit, frozen)
    selected = tuple(indexed[record["nct_id"]] for record in eligible[:target])
    return selected, audit


def validate_decomposition_source_selection_audit(
    audit: Dict[str, Any],
    contract: Optional[Dict[str, Any]] = None,
) -> None:
    frozen = contract or load_decomposition_source_pool_contract()
    validate_decomposition_source_pool_contract(frozen)
    validate_document(audit, AUDIT_SCHEMA)
    expected = _self_hash(
        audit, "selection_audit_id", "selection_audit_sha256"
    )
    if audit["selection_audit_sha256"] != expected:
        raise TrialSelectionError("Decomposition selection audit hash mismatch")
    if audit["selection_audit_id"] != (
        f"decomposition-source-selection-{expected[:16]}"
    ):
        raise TrialSelectionError("Decomposition selection audit ID mismatch")
    if audit["source_pool_contract_id"] != frozen["contract_id"] or audit[
        "source_pool_contract_sha256"
    ] != frozen["contract_sha256"]:
        raise TrialSelectionError("Selection audit contract binding mismatch")
    if audit["selection"] != source_pool_selection_document(frozen):
        raise TrialSelectionError("Selection audit policy differs from contract")
    if audit["query_parameters"] != frozen["query"]["parameters"]:
        raise TrialSelectionError("Selection audit query differs from contract")
    records = audit["records"]
    if records != sorted(records, key=lambda item: item["nct_id"]):
        raise TrialSelectionError("Selection audit records must be NCT sorted")
    if len({record["nct_id"] for record in records}) != len(records):
        raise TrialSelectionError("Selection audit contains duplicate NCT IDs")
    flow = audit["flow"]
    passed = [record for record in records if record["filter_passed"]]
    selected = [record for record in records if record["selected"]]
    if flow["fetched_candidate_count"] != len(records):
        raise TrialSelectionError("Selection audit fetched count mismatch")
    if flow["filter_passed_count"] != len(passed):
        raise TrialSelectionError("Selection audit filter-passed count mismatch")
    if flow["filter_excluded_count"] != len(records) - len(passed):
        raise TrialSelectionError("Selection audit filter-excluded count mismatch")
    if flow["selected_count"] != len(selected):
        raise TrialSelectionError("Selection audit selected count mismatch")
    if flow["eligible_not_sampled_count"] != len(passed) - len(selected):
        raise TrialSelectionError(
            "Selection audit eligible-not-sampled count mismatch"
        )
    expected_selected = {
        record["nct_id"]
        for record in sorted(
            passed, key=lambda item: (item["sampling_hash"], item["nct_id"])
        )[: frozen["sampling"]["target_study_count"]]
    }
    if {record["nct_id"] for record in selected} != expected_selected:
        raise TrialSelectionError("Selected NCT IDs differ from frozen hash rank")
    reason_counts: Dict[str, int] = {}
    for record in records:
        expected_reasons = _filter_reasons(record, frozen)
        if record["filter_exclusion_reasons"] != expected_reasons:
            raise TrialSelectionError("Selection audit filter reasons mismatch")
        expected_sampling = (
            None
            if expected_reasons
            else _sampling_hash(frozen, record["nct_id"])
        )
        if record["sampling_hash"] != expected_sampling:
            raise TrialSelectionError("Selection audit sampling hash mismatch")
        is_selected = record["nct_id"] in expected_selected
        expected_reason = (
            "excluded_by_explicit_filter"
            if expected_reasons
            else (
                "passed_filters_and_hash_rank_within_target"
                if is_selected
                else "hash_rank_outside_target"
            )
        )
        if record["selected"] != is_selected:
            raise TrialSelectionError("Selection audit selected flag mismatch")
        if record["selection_reason"] != expected_reason:
            raise TrialSelectionError("Selection audit selection reason mismatch")
        for reason in expected_reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if audit["filter_exclusion_reason_counts"] != dict(sorted(reason_counts.items())):
        raise TrialSelectionError("Selection audit reason counts mismatch")
