import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..capacity import validate_capacity_plan
from ..validation import validate_document
from .trials import NCT_PATTERN


SELECTION_AUDIT_VERSION = "1.0.0"
SELECTION_AUDIT_SCHEMA_RESOURCE = (
    "schemas/trial-selection-audit-1.0.0.schema.json"
)
SAMPLING_METHOD = "sha256_seeded_nct_id_v1"


class TrialSelectionError(ValueError):
    """Raised when trial selection is incomplete or not reproducible."""


@dataclass(frozen=True)
class TrialFilterPolicy:
    study_types: Tuple[str, ...]
    overall_statuses: Tuple[str, ...]
    require_eligibility_text: bool
    first_posted_from: str
    first_posted_to: str

    def normalized(self) -> Dict[str, Any]:
        if not self.study_types:
            raise TrialSelectionError("At least one study type is required")
        if not self.overall_statuses:
            raise TrialSelectionError("At least one recruitment status is required")
        try:
            start = date.fromisoformat(self.first_posted_from)
            end = date.fromisoformat(self.first_posted_to)
        except ValueError as error:
            raise TrialSelectionError(
                "First-posted bounds must use ISO dates"
            ) from error
        if start > end:
            raise TrialSelectionError(
                "first_posted_from cannot be after first_posted_to"
            )
        if not self.require_eligibility_text:
            raise TrialSelectionError(
                "Benchmark selection must require eligibility text"
            )
        return {
            "study_types": sorted(set(self.study_types)),
            "overall_statuses": sorted(set(self.overall_statuses)),
            "require_eligibility_text": True,
            "first_posted_from": start.isoformat(),
            "first_posted_to": end.isoformat(),
        }


@dataclass(frozen=True)
class ReproducibleTrialSelection:
    disease_domain: str
    rationale: str
    query_parameters: Mapping[str, str]
    filters: TrialFilterPolicy

    def normalized(self, capacity_plan: Dict[str, Any]) -> Dict[str, Any]:
        validate_capacity_plan(capacity_plan)
        if not capacity_plan["snapshot_design_allowed"]:
            raise TrialSelectionError(
                "Capacity plan is provisional or has no selected design"
            )
        if not self.disease_domain.strip():
            raise TrialSelectionError("disease_domain must not be empty")
        if not self.rationale.strip():
            raise TrialSelectionError("selection rationale must not be empty")
        parameters = {
            str(key): str(value)
            for key, value in sorted(self.query_parameters.items())
        }
        if "pageToken" in parameters:
            raise TrialSelectionError("pageToken is transient")
        if not parameters.get("query.cond"):
            raise TrialSelectionError("query.cond is required")
        parameters.setdefault("format", "json")
        parameters.setdefault("markupFormat", "markdown")
        parameters.setdefault("countTotal", "true")
        parameters.setdefault("pageSize", "1000")
        if parameters["format"] != "json":
            raise TrialSelectionError("format=json is required")
        if parameters["markupFormat"] != "markdown":
            raise TrialSelectionError("markupFormat=markdown is required")
        if parameters["countTotal"].lower() != "true":
            raise TrialSelectionError("countTotal=true is required")
        if "sort" in parameters:
            raise TrialSelectionError(
                "Registry sort is intentionally omitted; sample order is "
                "capacity-plan hash based"
            )
        try:
            page_size = int(parameters["pageSize"])
        except ValueError as error:
            raise TrialSelectionError("pageSize must be an integer") from error
        if not 1 <= page_size <= 1000:
            raise TrialSelectionError("pageSize must be between 1 and 1000")
        filters = self.filters.normalized()
        api_statuses = set(
            parameters.get("filter.overallStatus", "").split("|")
        ) - {""}
        if api_statuses and api_statuses != set(filters["overall_statuses"]):
            raise TrialSelectionError(
                "API status filter and local status filter must match"
            )
        return {
            "disease_domain": self.disease_domain.strip(),
            "rationale": self.rationale.strip(),
            "query_parameters": parameters,
            "filters": filters,
            "sampling": {
                "method": SAMPLING_METHOD,
                "seed": (
                    f"capacity-plan-sha256:{capacity_plan['plan_sha256']}"
                ),
                "target_study_count": capacity_plan["selected_design"][
                    "trial_count"
                ],
                "registry_order_used_for_sampling": False,
            },
            "capacity_binding": {
                "capacity_plan_id": capacity_plan["plan_id"],
                "capacity_plan_sha256": capacity_plan["plan_sha256"],
                "target_patient_count": capacity_plan["selected_design"][
                    "patient_count"
                ],
                "target_patient_trial_units": capacity_plan["selected_design"][
                    "patient_trial_units"
                ],
            },
        }


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _audit_hash_payload(audit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in audit.items()
        if key not in {"selection_audit_id", "selection_audit_sha256"}
    }


def select_trials(
    studies: Sequence[Dict[str, Any]],
    registry_reported_total_count: int,
    selection: ReproducibleTrialSelection,
    capacity_plan: Dict[str, Any],
) -> Tuple[Tuple[Dict[str, Any], ...], Dict[str, Any]]:
    """Filter all registry hits, then take a seeded NCT-hash sample."""
    normalized = selection.normalized(capacity_plan)
    if registry_reported_total_count < 0:
        raise TrialSelectionError("Registry total count cannot be negative")
    if len(studies) != registry_reported_total_count:
        raise TrialSelectionError(
            "All registry matches must be fetched before deterministic sampling"
        )
    filters = normalized["filters"]
    start = date.fromisoformat(filters["first_posted_from"])
    end = date.fromisoformat(filters["first_posted_to"])
    records = []
    seen = set()
    indexed_studies = {}
    for study in studies:
        if not isinstance(study, dict):
            raise TrialSelectionError("Every registry candidate must be an object")
        fields = _study_fields(study)
        nct_id = fields["nct_id"]
        if not isinstance(nct_id, str) or not NCT_PATTERN.fullmatch(nct_id):
            raise TrialSelectionError("Registry candidate has invalid NCT ID")
        if nct_id in seen:
            raise TrialSelectionError(f"Duplicate registry NCT ID: {nct_id}")
        seen.add(nct_id)
        indexed_studies[nct_id] = study
        reasons = []
        if fields["study_type"] not in filters["study_types"]:
            reasons.append("study_type_not_allowed")
        if fields["overall_status"] not in filters["overall_statuses"]:
            reasons.append("recruitment_status_not_allowed")
        if not fields["has_eligibility_text"]:
            reasons.append("eligibility_text_missing")
        first_posted = fields["first_posted"]
        try:
            first_posted_date = date.fromisoformat(first_posted)
        except (TypeError, ValueError):
            reasons.append("first_posted_date_missing_or_invalid")
        else:
            if first_posted_date < start or first_posted_date > end:
                reasons.append("first_posted_date_outside_range")
        sampling_hash = None
        if not reasons:
            sampling_hash = hashlib.sha256(
                (
                    f"{SAMPLING_METHOD}|{normalized['sampling']['seed']}|"
                    f"{nct_id}"
                ).encode("utf-8")
            ).hexdigest()
        records.append(
            {
                **fields,
                "source_study_sha256": _canonical_hash(study),
                "filter_passed": not reasons,
                "filter_exclusion_reasons": sorted(reasons),
                "sampling_hash": sampling_hash,
                "selected": False,
                "selection_reason": (
                    "excluded_by_explicit_filter"
                    if reasons
                    else "eligible_for_deterministic_sampling"
                ),
            }
        )

    eligible = sorted(
        (record for record in records if record["filter_passed"]),
        key=lambda record: (record["sampling_hash"], record["nct_id"]),
    )
    target = normalized["sampling"]["target_study_count"]
    if len(eligible) < target:
        raise TrialSelectionError(
            f"Only {len(eligible)} trials pass filters; capacity plan requires "
            f"{target}"
        )
    selected_ids = {record["nct_id"] for record in eligible[:target]}
    for record in records:
        if record["nct_id"] in selected_ids:
            record["selected"] = True
            record["selection_reason"] = (
                "passed_filters_and_hash_rank_within_capacity_target"
            )
        elif record["filter_passed"]:
            record["selection_reason"] = (
                "hash_rank_outside_capacity_bound_target"
            )

    reason_counts: Dict[str, int] = {}
    for record in records:
        for reason in record["filter_exclusion_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    audit: Dict[str, Any] = {
        "selection_audit_version": SELECTION_AUDIT_VERSION,
        "selection": normalized,
        "flow": {
            "registry_reported_total_count": registry_reported_total_count,
            "fetched_candidate_count": len(studies),
            "filter_passed_count": len(eligible),
            "filter_excluded_count": len(studies) - len(eligible),
            "eligible_not_sampled_count": len(eligible) - target,
            "selected_count": target,
        },
        "filter_exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "records": sorted(records, key=lambda record: record["nct_id"]),
    }
    audit_hash = _canonical_hash(_audit_hash_payload(audit))
    audit["selection_audit_sha256"] = audit_hash
    audit["selection_audit_id"] = f"trial-selection-{audit_hash[:16]}"
    validate_document(audit, SELECTION_AUDIT_SCHEMA_RESOURCE)
    selected = tuple(
        indexed_studies[record["nct_id"]]
        for record in eligible[:target]
    )
    return selected, audit


def validate_selection_audit(audit: Dict[str, Any]) -> None:
    validate_document(audit, SELECTION_AUDIT_SCHEMA_RESOURCE)
    expected_hash = _canonical_hash(_audit_hash_payload(audit))
    if audit["selection_audit_sha256"] != expected_hash:
        raise TrialSelectionError("Selection audit hash mismatch")
    if audit["selection_audit_id"] != f"trial-selection-{expected_hash[:16]}":
        raise TrialSelectionError("Selection audit ID mismatch")
    flow = audit["flow"]
    if flow["fetched_candidate_count"] != len(audit["records"]):
        raise TrialSelectionError("Selection audit record count mismatch")
    if flow["registry_reported_total_count"] != flow["fetched_candidate_count"]:
        raise TrialSelectionError("Registry fetch is incomplete")
    selected = sum(record["selected"] for record in audit["records"])
    if selected != flow["selected_count"]:
        raise TrialSelectionError("Selected count does not match audit records")
    filter_passed = [
        record for record in audit["records"] if record["filter_passed"]
    ]
    if len(filter_passed) != flow["filter_passed_count"]:
        raise TrialSelectionError("Filter-passed count mismatch")
    if len(audit["records"]) - len(filter_passed) != flow[
        "filter_excluded_count"
    ]:
        raise TrialSelectionError("Filter-excluded count mismatch")
    if len(filter_passed) - selected != flow["eligible_not_sampled_count"]:
        raise TrialSelectionError("Eligible-not-sampled count mismatch")
    target = audit["selection"]["sampling"]["target_study_count"]
    if selected != target:
        raise TrialSelectionError("Selected count differs from capacity target")
    seed = audit["selection"]["sampling"]["seed"]
    for record in audit["records"]:
        if record["filter_passed"]:
            expected_sampling_hash = hashlib.sha256(
                (
                    f"{SAMPLING_METHOD}|{seed}|{record['nct_id']}"
                ).encode("utf-8")
            ).hexdigest()
            if record["sampling_hash"] != expected_sampling_hash:
                raise TrialSelectionError("Sampling hash mismatch")
            if record["filter_exclusion_reasons"]:
                raise TrialSelectionError(
                    "Filter-passed record has exclusion reasons"
                )
        elif record["sampling_hash"] is not None:
            raise TrialSelectionError(
                "Filter-excluded record cannot have a sampling hash"
            )
    expected_selected_ids = {
        record["nct_id"]
        for record in sorted(
            filter_passed,
            key=lambda item: (item["sampling_hash"], item["nct_id"]),
        )[:target]
    }
    actual_selected_ids = {
        record["nct_id"] for record in audit["records"] if record["selected"]
    }
    if actual_selected_ids != expected_selected_ids:
        raise TrialSelectionError("Selected IDs do not match deterministic rank")
    reason_counts: Dict[str, int] = {}
    for record in audit["records"]:
        for reason in record["filter_exclusion_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if dict(sorted(reason_counts.items())) != audit[
        "filter_exclusion_reason_counts"
    ]:
        raise TrialSelectionError("Filter exclusion reason counts mismatch")
