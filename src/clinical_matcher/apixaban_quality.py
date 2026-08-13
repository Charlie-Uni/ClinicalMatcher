import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .apixaban_benchmark import (
    EXPECTED_OFFICIAL_COUNTS,
    OFFICIAL_SOURCE_SHA256,
    validate_apixaban_benchmark,
    validate_apixaban_benchmark_manifest,
    verify_apixaban_benchmark_files,
)
from .apixaban_contract import load_question_catalog
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


QUALITY_REPORT_VERSION = "1.0.0"
RESTRICTED_SCHEMA = (
    "schemas/apixaban-quality-report-restricted-1.0.0.schema.json"
)
PUBLIC_SCHEMA = (
    "schemas/apixaban-quality-report-public-1.0.0.schema.json"
)
SUPPRESSION_REASON = "minimum_cell_or_complementary_suppression"
SOURCE_TOTAL_KEYS = {
    "answered": "answered_source_count",
    "not_specified": "not_specified_source_count",
    "source_anomaly": "source_anomaly_count",
}


class ApixabanQualityError(ValueError):
    """Raised when quality accounting or disclosure control is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _report_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("report_sha256", None)
    return canonical_sha256(unsigned)


def _serialized(document: Dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _source_status(assessment: Mapping[str, Any]) -> str:
    if not assessment["abstained"]:
        return "answered"
    if assessment["abstention_reason"] == "source_not_specified":
        return "not_specified"
    if assessment["abstention_reason"] == "source_anomaly":
        return "source_anomaly"
    raise ApixabanQualityError(
        "Released-label benchmark contains an unsupported abstention reason"
    )


def _count_cell(value: int, suppressed: bool = False) -> Dict[str, Any]:
    return {
        "value": None if suppressed else value,
        "suppressed": suppressed,
        "suppression_reason": SUPPRESSION_REASON if suppressed else None,
    }


def _suppress_additive_group(
    counts: Mapping[str, int], minimum_cell_size: int
) -> Dict[str, Dict[str, Any]]:
    suppressed = {
        name
        for name, count in counts.items()
        if 0 < count < minimum_cell_size
    }
    if suppressed and len(suppressed) == 1:
        candidates = [
            (count, name)
            for name, count in counts.items()
            if name not in suppressed and count > 0
        ]
        if not candidates:
            raise ApixabanQualityError(
                "Cannot apply complementary suppression to additive group"
            )
        suppressed.add(min(candidates)[1])
    return {
        name: _count_cell(count, name in suppressed)
        for name, count in counts.items()
    }


def _rate_cell(value: float, source_cell: Mapping[str, Any]) -> Dict[str, Any]:
    if source_cell["suppressed"]:
        return {
            "value": None,
            "suppressed": True,
            "suppression_reason": "derived_from_suppressed_cell",
        }
    return {
        "value": value,
        "suppressed": False,
        "suppression_reason": None,
    }


def _question_metrics(
    question: Mapping[str, Any],
    assessments: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    rows = list(assessments)
    fact_counts = Counter(row["fact_status"] for row in rows)
    source_counts = Counter(_source_status(row) for row in rows)
    numeric_summary = None
    if question["question_type"] == "numeric":
        values = [
            float(row["value"])
            for row in rows
            if row["fact_status"] == "present"
        ]
        numeric_summary = {
            "known_value_count": len(values),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "plausibility_rule_status": (
                "not_assessed_no_reviewed_unit_aware_rule"
            ),
            "flagged_implausible_value_count": None,
            "removed_value_count": 0,
        }
    assessment_count = len(rows)
    return {
        "question_id": question["question_id"],
        "source_criterion_label": question["source_criterion_label"],
        "fact_field": question["fact_field"],
        "question_type": question["question_type"],
        "aggregation": question["aggregation"],
        "assessment_count": assessment_count,
        "fact_status_counts": {
            name: fact_counts[name]
            for name in ("present", "absent", "unknown")
        },
        "source_status_counts": {
            name: source_counts[name]
            for name in ("answered", "not_specified", "source_anomaly")
        },
        "unknown_rate": (
            fact_counts["unknown"] / assessment_count
            if assessment_count
            else 0.0
        ),
        "numeric_summary": numeric_summary,
    }


def validate_restricted_quality_report(
    document: Dict[str, Any],
    *,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> None:
    validate_document(document, RESTRICTED_SCHEMA)
    if _report_hash(document) != document["report_sha256"]:
        raise ApixabanQualityError("Restricted quality report hash mismatch")
    catalog = load_question_catalog()
    catalog_by_id = {
        question["question_id"]: question
        for question in catalog["questions"]
    }
    if document["source"]["question_catalog_sha256"] != catalog[
        "catalog_sha256"
    ]:
        raise ApixabanQualityError("Quality report catalog hash mismatch")
    question_ids = [item["question_id"] for item in document["questions"]]
    if question_ids != sorted(catalog_by_id):
        raise ApixabanQualityError(
            "Quality report must cover each frozen question once in ID order"
        )

    aggregate_fact: Counter[str] = Counter()
    aggregate_source: Counter[str] = Counter()
    total_assessments = 0
    type_counts: Counter[str] = Counter()
    for item in document["questions"]:
        question = catalog_by_id[item["question_id"]]
        for field in (
            "source_criterion_label",
            "fact_field",
            "question_type",
            "aggregation",
        ):
            if item[field] != question[field]:
                raise ApixabanQualityError(
                    f"Question quality metadata mismatch: {item['question_id']}"
                )
        fact_total = sum(item["fact_status_counts"].values())
        source_total = sum(item["source_status_counts"].values())
        if fact_total != item["assessment_count"] or source_total != fact_total:
            raise ApixabanQualityError(
                "Per-question status counts do not reconcile"
            )
        expected_rate = (
            item["fact_status_counts"]["unknown"] / fact_total
        )
        if not math.isclose(item["unknown_rate"], expected_rate):
            raise ApixabanQualityError("Per-question unknown rate is incorrect")
        numeric = item["numeric_summary"]
        if item["question_type"] == "numeric":
            if numeric is None:
                raise ApixabanQualityError(
                    "Numeric question is missing its range summary"
                )
            if numeric["known_value_count"] != item["fact_status_counts"][
                "present"
            ]:
                raise ApixabanQualityError(
                    "Numeric known-value count does not reconcile"
                )
            if numeric["known_value_count"] > 0 and (
                numeric["minimum"] is None
                or numeric["maximum"] is None
                or numeric["minimum"] > numeric["maximum"]
            ):
                raise ApixabanQualityError("Numeric range is invalid")
        elif numeric is not None:
            raise ApixabanQualityError(
                "Boolean question cannot contain a numeric range"
            )
        aggregate_fact.update(item["fact_status_counts"])
        aggregate_source.update(item["source_status_counts"])
        total_assessments += item["assessment_count"]
        type_counts[item["question_type"]] += item["assessment_count"]

    totals = document["totals"]
    if totals["assessment_count"] != total_assessments:
        raise ApixabanQualityError("Question totals do not reconcile")
    for name in ("present", "absent", "unknown"):
        if totals[f"{name}_count"] != aggregate_fact[name]:
            raise ApixabanQualityError("Aggregate fact counts do not reconcile")
    for name in ("answered", "not_specified", "source_anomaly"):
        if totals[SOURCE_TOTAL_KEYS[name]] != aggregate_source[name]:
            raise ApixabanQualityError(
                "Aggregate source-status counts do not reconcile"
            )
    for name in ("boolean", "numeric"):
        if totals[f"{name}_assessment_count"] != type_counts[name]:
            raise ApixabanQualityError("Assessment type counts do not reconcile")
    if totals["expected_assessment_count"] != (
        totals["patient_count"] * totals["question_count"]
    ):
        raise ApixabanQualityError("Expected grid size is incorrect")
    if totals["complete_patient_count"] + totals[
        "incomplete_patient_count"
    ] != totals["patient_count"]:
        raise ApixabanQualityError("Patient completeness counts do not reconcile")
    complete = (
        totals["missing_patient_question_pair_count"] == 0
        and totals["duplicate_patient_question_pair_count"] == 0
        and totals["incomplete_patient_count"] == 0
    )
    if document["quality"]["complete_patient_question_grid"] != complete:
        raise ApixabanQualityError("Grid-completeness flag is incorrect")
    if required_counts is not None:
        mapping = {
            "patient_count": "patient_count",
            "question_count": "question_count",
            "assessment_count": "assessment_count",
            "answered_source_count": "answered_source_count",
            "not_specified_source_count": "not_specified_source_count",
            "source_anomaly_count": "source_anomaly_count",
        }
        for expected_name, total_name in mapping.items():
            if totals[total_name] != required_counts[expected_name]:
                raise ApixabanQualityError(
                    f"Official quality total {total_name} is incorrect"
                )


def _validate_count_cells(
    cells: Mapping[str, Mapping[str, Any]], minimum_cell_size: int
) -> None:
    suppressed_count = 0
    for cell in cells.values():
        if cell["suppressed"]:
            suppressed_count += 1
        elif 0 < cell["value"] < minimum_cell_size:
            raise ApixabanQualityError(
                "Public report discloses a positive cell below threshold"
            )
    if suppressed_count == 1:
        raise ApixabanQualityError(
            "Additive group lacks complementary suppression"
        )


def _walk_suppressed_cells(value: Any) -> int:
    if isinstance(value, dict):
        if set(value) == {"value", "suppressed", "suppression_reason"}:
            return int(value["suppressed"])
        return sum(_walk_suppressed_cells(item) for item in value.values())
    if isinstance(value, list):
        return sum(_walk_suppressed_cells(item) for item in value)
    return 0


def validate_public_quality_report(document: Dict[str, Any]) -> None:
    validate_document(document, PUBLIC_SCHEMA)
    if _report_hash(document) != document["report_sha256"]:
        raise ApixabanQualityError("Public quality report hash mismatch")
    control = document["disclosure_control"]
    approved = control["governance_status"] == "approved"
    reference = control["governance_approval_reference"]
    if approved != control["release_authorized"]:
        raise ApixabanQualityError(
            "Release authorization must match governance status"
        )
    if approved != bool(reference and reference.strip()):
        raise ApixabanQualityError(
            "Approved reports require a non-empty governance reference"
        )
    threshold = control["minimum_cell_size"]
    total_fact = {
        name: document["totals"][f"{name}_count"]
        for name in ("present", "absent", "unknown")
    }
    total_source = {
        name: document["totals"][SOURCE_TOTAL_KEYS[name]]
        for name in ("answered", "not_specified", "source_anomaly")
    }
    _validate_count_cells(total_fact, threshold)
    _validate_count_cells(total_source, threshold)
    for question in document["questions"]:
        _validate_count_cells(question["fact_status_counts"], threshold)
        _validate_count_cells(question["source_status_counts"], threshold)
        unknown = question["fact_status_counts"]["unknown"]
        rate = question["unknown_rate"]
        if unknown["suppressed"] != rate["suppressed"]:
            raise ApixabanQualityError(
                "Unknown rate suppression does not match its source count"
            )
        numeric = question["numeric_summary"]
        if numeric is not None:
            present = question["fact_status_counts"]["present"]
            if numeric["known_value_count"] != present:
                raise ApixabanQualityError(
                    "Public numeric known count must reuse present-count control"
                )
    actual_suppressed = _walk_suppressed_cells(document)
    if control["suppressed_cell_count"] != actual_suppressed:
        raise ApixabanQualityError("Suppressed-cell total is incorrect")
    forbidden_keys = {
        "patient_id",
        "patient_ids",
        "assessment_id",
        "benchmark_sha256",
        "benchmark_manifest_sha256",
        "code_commit",
        "minimum",
        "maximum",
    }
    serialized = json.dumps(document, sort_keys=True)
    for key in forbidden_keys - {"minimum", "maximum"}:
        if f'"{key}"' in serialized:
            raise ApixabanQualityError(
                f"Public report contains forbidden field: {key}"
            )
    for question in document["questions"]:
        numeric = question["numeric_summary"]
        if numeric is not None and (
            numeric["minimum"] is not None or numeric["maximum"] is not None
        ):
            raise ApixabanQualityError(
                "Public report cannot disclose individual numeric extrema"
            )


def _public_projection(
    restricted: Dict[str, Any],
    minimum_cell_size: int,
    governance_approval_reference: Optional[str],
) -> Dict[str, Any]:
    if minimum_cell_size < 2:
        raise ApixabanQualityError("minimum_cell_size must be at least 2")
    approved = bool(
        governance_approval_reference
        and governance_approval_reference.strip()
    )
    totals = restricted["totals"]
    total_fact = _suppress_additive_group(
        {name: totals[f"{name}_count"] for name in (
            "present", "absent", "unknown"
        )},
        minimum_cell_size,
    )
    total_source = _suppress_additive_group(
        {
            name: totals[SOURCE_TOTAL_KEYS[name]]
            for name in ("answered", "not_specified", "source_anomaly")
        },
        minimum_cell_size,
    )
    public_totals = {
        name: totals[name]
        for name in (
            "patient_count",
            "question_count",
            "assessment_count",
            "expected_assessment_count",
            "missing_patient_question_pair_count",
            "duplicate_patient_question_pair_count",
            "complete_patient_count",
            "incomplete_patient_count",
            "minimum_assessments_per_patient",
            "maximum_assessments_per_patient",
            "boolean_assessment_count",
            "numeric_assessment_count",
        )
    }
    public_totals.update(
        {f"{name}_count": total_fact[name] for name in total_fact}
    )
    public_totals.update(
        {
            SOURCE_TOTAL_KEYS[name]: total_source[name]
            for name in total_source
        }
    )
    public_questions = []
    for question in restricted["questions"]:
        fact_cells = _suppress_additive_group(
            question["fact_status_counts"], minimum_cell_size
        )
        source_cells = _suppress_additive_group(
            question["source_status_counts"], minimum_cell_size
        )
        numeric = None
        if question["numeric_summary"] is not None:
            numeric = {
                "known_value_count": dict(fact_cells["present"]),
                "minimum": None,
                "maximum": None,
                "extrema_suppression_reason": (
                    "individual_extrema_require_separate_governance_review"
                ),
                "plausibility_rule_status": (
                    "not_assessed_no_reviewed_unit_aware_rule"
                ),
                "flagged_implausible_value_count": None,
                "removed_value_count": 0,
            }
        public_questions.append(
            {
                **{
                    name: question[name]
                    for name in (
                        "question_id",
                        "source_criterion_label",
                        "fact_field",
                        "question_type",
                        "aggregation",
                        "assessment_count",
                    )
                },
                "fact_status_counts": fact_cells,
                "source_status_counts": source_cells,
                "unknown_rate": _rate_cell(
                    question["unknown_rate"], fact_cells["unknown"]
                ),
                "numeric_summary": numeric,
            }
        )
    public: Dict[str, Any] = {
        "apixaban_public_quality_report_version": QUALITY_REPORT_VERSION,
        "report_sha256": "pending",
        "report_scope": "aggregate_public_candidate",
        "generated_at": restricted["generated_at"],
        "source": {
            name: restricted["source"][name]
            for name in (
                "dataset_id",
                "dataset_version",
                "benchmark_version",
                "question_catalog_version",
                "question_catalog_sha256",
            )
        },
        "disclosure_control": {
            "minimum_cell_size": minimum_cell_size,
            "zero_counts_are_disclosable": True,
            "complementary_suppression": True,
            "numeric_extrema_disclosed": False,
            "governance_status": "approved" if approved else "pending_review",
            "governance_approval_reference": (
                governance_approval_reference if approved else None
            ),
            "release_authorized": approved,
            "suppressed_cell_count": 0,
        },
        "totals": public_totals,
        "questions": public_questions,
        "quality": dict(restricted["quality"]),
        "disclosure_note": (
            "This aggregate projection applies positive-cell and complementary "
            "suppression and withholds numeric extrema. "
            + (
                "Release is authorized under the recorded governance reference."
                if approved
                else "It is not authorized for public release until "
                "governance_status is approved."
            )
        ),
    }
    public["disclosure_control"]["suppressed_cell_count"] = (
        _walk_suppressed_cells(public)
    )
    public["report_sha256"] = _report_hash(public)
    validate_public_quality_report(public)
    return public


def validate_quality_report_pair(
    restricted: Dict[str, Any], public: Dict[str, Any],
    *,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> None:
    validate_restricted_quality_report(
        restricted, required_counts=required_counts
    )
    validate_public_quality_report(public)
    if public["generated_at"] != restricted["generated_at"]:
        raise ApixabanQualityError("Quality report timestamps differ")
    if public["quality"] != restricted["quality"]:
        raise ApixabanQualityError("Public quality flags changed")
    if public["source"] != {
        name: restricted["source"][name]
        for name in public["source"]
    }:
        raise ApixabanQualityError("Public report source metadata changed")
    for name, value in public["totals"].items():
        if isinstance(value, dict):
            if not value["suppressed"] and value["value"] != (
                restricted["totals"][name]
            ):
                raise ApixabanQualityError(
                    "Public unsuppressed total differs from restricted report"
                )
        elif value != restricted["totals"][name]:
            raise ApixabanQualityError(
                "Public structural total differs from restricted report"
            )
    for restricted_question, public_question in zip(
        restricted["questions"], public["questions"]
    ):
        for name in (
            "question_id",
            "source_criterion_label",
            "fact_field",
            "question_type",
            "aggregation",
            "assessment_count",
        ):
            if restricted_question[name] != public_question[name]:
                raise ApixabanQualityError("Public question metadata changed")
        for group_name in ("fact_status_counts", "source_status_counts"):
            for name, cell in public_question[group_name].items():
                if not cell["suppressed"] and cell["value"] != (
                    restricted_question[group_name][name]
                ):
                    raise ApixabanQualityError(
                        "Public unsuppressed count differs from restricted report"
                    )
        if not public_question["unknown_rate"]["suppressed"] and not math.isclose(
            public_question["unknown_rate"]["value"],
            restricted_question["unknown_rate"],
        ):
            raise ApixabanQualityError(
                "Public unknown rate differs from restricted report"
            )


def build_apixaban_quality_reports(
    benchmark: Dict[str, Any],
    benchmark_manifest: Dict[str, Any],
    *,
    minimum_cell_size: int,
    governance_approval_reference: Optional[str] = None,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    benchmark_counts = validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=required_source_sha256,
        required_counts=required_counts,
    )
    validate_apixaban_benchmark_manifest(benchmark_manifest)
    if benchmark_manifest["output"]["benchmark_sha256"] != hashlib.sha256(
        _serialized(benchmark)
    ).hexdigest():
        raise ApixabanQualityError(
            "Benchmark content does not match its aggregate manifest"
        )
    catalog = load_question_catalog()
    assessments_by_question: Dict[str, List[Mapping[str, Any]]] = defaultdict(
        list
    )
    patient_counts: Counter[str] = Counter()
    pair_counts: Counter[Tuple[str, str]] = Counter()
    for assessment in benchmark["assessments"]:
        assessments_by_question[assessment["question_id"]].append(assessment)
        patient_counts[assessment["patient_id"]] += 1
        pair_counts[(assessment["patient_id"], assessment["question_id"])] += 1
    question_metrics = [
        _question_metrics(question, assessments_by_question[question["question_id"]])
        for question in sorted(
            catalog["questions"], key=lambda item: item["question_id"]
        )
    ]
    expected_pair_count = len(benchmark["patient_ids"]) * len(
        catalog["questions"]
    )
    observed_unique_pairs = len(pair_counts)
    duplicate_count = sum(count - 1 for count in pair_counts.values())
    expected_per_patient = len(catalog["questions"])
    complete_patient_count = sum(
        patient_counts[patient_id] == expected_per_patient
        for patient_id in benchmark["patient_ids"]
    )
    totals = {
        **benchmark_counts,
        "expected_assessment_count": expected_pair_count,
        "missing_patient_question_pair_count": (
            expected_pair_count - observed_unique_pairs
        ),
        "duplicate_patient_question_pair_count": duplicate_count,
        "complete_patient_count": complete_patient_count,
        "incomplete_patient_count": (
            len(benchmark["patient_ids"]) - complete_patient_count
        ),
        "minimum_assessments_per_patient": min(patient_counts.values()),
        "maximum_assessments_per_patient": max(patient_counts.values()),
    }
    benchmark_sha256 = benchmark_manifest["output"]["benchmark_sha256"]
    restricted: Dict[str, Any] = {
        "apixaban_quality_report_version": QUALITY_REPORT_VERSION,
        "report_sha256": "pending",
        "report_scope": "restricted_local",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "source": {
            "dataset_id": benchmark["source"]["dataset_id"],
            "dataset_version": benchmark["source"]["dataset_version"],
            "benchmark_version": benchmark["apixaban_benchmark_version"],
            "benchmark_sha256": benchmark_sha256,
            "benchmark_manifest_sha256": benchmark_manifest[
                "manifest_sha256"
            ],
            "question_catalog_version": benchmark["contract"][
                "question_catalog_version"
            ],
            "question_catalog_sha256": benchmark["contract"][
                "question_catalog_sha256"
            ],
        },
        "totals": totals,
        "questions": question_metrics,
        "quality": {
            "complete_patient_question_grid": (
                totals["missing_patient_question_pair_count"] == 0
                and totals["duplicate_patient_question_pair_count"] == 0
                and totals["incomplete_patient_count"] == 0
            ),
            "source_anomalies_preserved": True,
            "values_removed_count": 0,
            "plausibility_rule_status": (
                "not_assessed_no_reviewed_unit_aware_rules"
            ),
        },
        "disclosure_note": (
            "This report contains exact small cells and numeric extrema from "
            "restricted MIMIC-derived labels. Keep it local and use only the "
            "separately generated disclosure-controlled projection for review."
        ),
    }
    restricted["report_sha256"] = _report_hash(restricted)
    validate_restricted_quality_report(
        restricted, required_counts=required_counts
    )
    public = _public_projection(
        restricted,
        minimum_cell_size,
        governance_approval_reference,
    )
    validate_quality_report_pair(
        restricted, public, required_counts=required_counts
    )
    return restricted, public


def build_apixaban_quality_reports_from_paths(
    benchmark_path: Path,
    benchmark_manifest_path: Path,
    **kwargs: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    verify_apixaban_benchmark_files(benchmark_path, benchmark_manifest_path)
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        benchmark_manifest_path.read_text(encoding="utf-8")
    )
    return build_apixaban_quality_reports(benchmark, manifest, **kwargs)


def _write_private_file(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite quality report: {path}"
        ) from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def write_apixaban_quality_reports(
    restricted: Dict[str, Any],
    public: Dict[str, Any],
    restricted_output_path: Path,
    public_output_path: Optional[Path] = None,
    *,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Tuple[Path, Path]:
    public_path = public_output_path or restricted_output_path.with_name(
        f"{restricted_output_path.stem}.public-candidate.json"
    )
    for path in (restricted_output_path, public_path):
        assert_restricted_local_path(path)
    if restricted_output_path.resolve() == public_path.resolve():
        raise ApixabanQualityError("Restricted and public outputs must differ")
    existing = [
        path for path in (restricted_output_path, public_path) if path.exists()
    ]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite quality report: "
            + ", ".join(str(path) for path in existing)
        )
    validate_quality_report_pair(
        restricted, public, required_counts=required_counts
    )
    restricted_output_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_file(restricted_output_path, _serialized(restricted))
    _write_private_file(public_path, _serialized(public))
    return restricted_output_path, public_path


def verify_apixaban_quality_report_files(
    restricted_path: Path,
    public_path: Path,
    *,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    for path in (restricted_path, public_path):
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanQualityError(
                f"Quality report file is not owner-only: {path}"
            )
    restricted = json.loads(restricted_path.read_text(encoding="utf-8"))
    public = json.loads(public_path.read_text(encoding="utf-8"))
    validate_quality_report_pair(
        restricted, public, required_counts=required_counts
    )
    return restricted, public
