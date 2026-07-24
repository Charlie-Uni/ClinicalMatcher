from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .validation import validate_document


GOLD_READINESS_VERSION = "1.0.0"
GOLD_READINESS_SCHEMA_RESOURCE = (
    "schemas/benchmark-gold-readiness-1.0.0.schema.json"
)


class BenchmarkNotReadyError(ValueError):
    """Raised when a benchmark claim is attempted without complete gold."""


@dataclass(frozen=True)
class GoldAuditCounts:
    patient_count: int = 0
    trial_count: int = 0
    expected_patient_trial_pairs: int = 0
    adjudicated_patient_trial_pairs: int = 0
    expected_criterion_units: int = 0
    adjudicated_criterion_units: int = 0
    minimum_annotators_per_unit: int = 0
    unresolved_adjudications: int = 0

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if (
            self.adjudicated_patient_trial_pairs
            > self.expected_patient_trial_pairs
        ):
            raise ValueError(
                "adjudicated_patient_trial_pairs cannot exceed expected"
            )
        if self.adjudicated_criterion_units > self.expected_criterion_units:
            raise ValueError(
                "adjudicated_criterion_units cannot exceed expected"
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_gold_readiness_report(
    snapshot_manifest: Dict[str, Any],
    counts: GoldAuditCounts,
    gold_source_description: str,
    generated_at: Optional[str] = None,
    counts_provenance: str = "self_reported_aggregate",
) -> Dict[str, Any]:
    """Create a PHI-free aggregate gate; no patient IDs or text are accepted."""
    snapshot_version = snapshot_manifest.get("snapshot_version")
    snapshot_schema = {
        "1.0.0": "schemas/trial-snapshot-1.0.0.schema.json",
        "1.1.0": "schemas/trial-snapshot-1.1.0.schema.json",
    }.get(snapshot_version)
    if snapshot_schema is None:
        raise ValueError("Unsupported trial snapshot version")
    validate_document(
        snapshot_manifest,
        snapshot_schema,
    )
    counts.validate()
    if not gold_source_description.strip():
        raise ValueError("gold_source_description must not be empty")
    if counts_provenance not in {
        "self_reported_aggregate",
        "validated_annotation_records",
    }:
        raise ValueError("Unsupported counts_provenance")
    imported_trials = sum(
        record["status"] == "imported"
        for record in snapshot_manifest["records"]
    )
    gaps: List[str] = []
    if counts_provenance != "validated_annotation_records":
        gaps.append("gold_counts_not_derived_from_validated_records")
    if (
        snapshot_version == "1.0.0"
        and snapshot_manifest["search"]["selection_truncated"]
    ):
        gaps.append("trial_selection_is_truncated")
    if imported_trials < 2:
        gaps.append("snapshot_requires_multiple_imported_trials")
    if counts.patient_count < 1:
        gaps.append("no_gold_patients")
    if counts.trial_count != imported_trials:
        gaps.append("gold_trial_count_does_not_cover_snapshot")
    if snapshot_version == "1.1.0":
        target_trials = snapshot_manifest["selection"]["sampling"][
            "target_study_count"
        ]
        target_patients = snapshot_manifest["selection"]["capacity_binding"][
            "target_patient_count"
        ]
        target_units = snapshot_manifest["selection"]["capacity_binding"][
            "target_patient_trial_units"
        ]
        if imported_trials != target_trials:
            gaps.append("selected_trial_parse_incomplete")
        if counts.patient_count != target_patients:
            gaps.append("gold_patient_count_differs_from_capacity_plan")
        if counts.expected_patient_trial_pairs != target_units:
            gaps.append("gold_units_differ_from_capacity_plan")
    if counts.expected_patient_trial_pairs < 1:
        gaps.append("patient_trial_prediction_units_not_defined")
    elif (
        counts.adjudicated_patient_trial_pairs
        != counts.expected_patient_trial_pairs
    ):
        gaps.append("patient_trial_gold_incomplete")
    if counts.expected_criterion_units < 1:
        gaps.append("criterion_prediction_units_not_defined")
    elif counts.adjudicated_criterion_units != counts.expected_criterion_units:
        gaps.append("criterion_evidence_gold_incomplete")
    if counts.minimum_annotators_per_unit < 2:
        gaps.append("fewer_than_two_independent_annotators")
    if counts.unresolved_adjudications:
        gaps.append("unresolved_adjudications")

    ready = not gaps
    report = {
        "gold_readiness_version": GOLD_READINESS_VERSION,
        "snapshot_id": snapshot_manifest["snapshot_id"],
        "snapshot_content_sha256": snapshot_manifest[
            "snapshot_content_sha256"
        ],
        "generated_at": generated_at or _now(),
        "prediction_units": [
            "patient_x_trial",
            "patient_x_trial_x_criterion",
        ],
        "gold_source_description": gold_source_description.strip(),
        "counts_provenance": counts_provenance,
        "aggregate_counts": asdict(counts),
        "required_imported_trial_count": imported_trials,
        "status": "ready" if ready else "not_ready",
        "benchmark_release_allowed": ready,
        "blocking_gaps": gaps,
        "claim_boundary": (
            "A valid public trial snapshot alone is not a multi-trial "
            "benchmark. Benchmark claims require complete independent "
            "patient-trial and criterion-evidence adjudication."
        ),
    }
    validate_document(report, GOLD_READINESS_SCHEMA_RESOURCE)
    return report


def assert_benchmark_ready(report: Dict[str, Any]) -> None:
    validate_document(report, GOLD_READINESS_SCHEMA_RESOURCE)
    semantically_ready = (
        report["status"] == "ready"
        and report["benchmark_release_allowed"]
        and not report["blocking_gaps"]
    )
    semantically_not_ready = (
        report["status"] == "not_ready"
        and not report["benchmark_release_allowed"]
        and bool(report["blocking_gaps"])
    )
    if not (semantically_ready or semantically_not_ready):
        raise BenchmarkNotReadyError(
            "Gold readiness status, release flag, and blocking gaps disagree"
        )
    if not report["benchmark_release_allowed"] or report["status"] != "ready":
        gaps = ", ".join(report["blocking_gaps"]) or "unspecified"
        raise BenchmarkNotReadyError(
            f"Benchmark gold is not ready; blocking gaps: {gaps}"
        )
