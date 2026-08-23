"""Owner-only quality audit for Apixaban silver citation supervision.

This module deliberately separates pre-audit candidates, sampled review
packages, completed judgments, and accepted silver.  None of these artifacts
is independent evidence gold.
"""

import copy
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import serialized_document_sha256, validate_apixaban_benchmark
from .apixaban_calibration import validate_apixaban_calibration_reservation
from .apixaban_contract import (
    known_fact_allows_empty_evidence,
    load_question_catalog,
    question_index,
)
from .apixaban_sft import (
    ACCEPTED_SILVER_VERSION,
    validate_accepted_silver,
    validate_apixaban_sft_input_plan,
)
from .apixaban_split import validate_apixaban_split_manifest, write_private_json
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CANDIDATE_VERSION = "1.0.0"
CANDIDATE_SCHEMA = "schemas/apixaban-silver-candidate-1.0.0.schema.json"
AUDIT_PACKAGE_VERSION = "1.0.0"
AUDIT_PACKAGE_SCHEMA = "schemas/apixaban-silver-audit-package-1.0.0.schema.json"
JUDGMENT_VERSION = "1.0.0"
JUDGMENT_SCHEMA = "schemas/apixaban-silver-judgment-1.0.0.schema.json"
QUALITY_AUDIT_VERSION = "1.0.0"
QUALITY_AUDIT_SCHEMA = "schemas/apixaban-silver-quality-audit-1.0.0.schema.json"

SAMPLING_ALGORITHM = "sha256_stratified_silver_audit_sampling/1.0.0"
SAMPLING_SALT = "clinicalmatcher-p5-silver-audit-v1"
ALLOCATION_VERSION = "1.0.0"
RUBRIC_VERSION = "1.0.0"
REVIEW_BUDGET = 100
MIN_SOURCE_SUPPORT_PERCENT = 90
MIN_OVERALL_COVERAGE_PERCENT = 60
MIN_QUESTION_COVERAGE_PERCENT = 30
MIN_QUESTION_ACCEPTED_ROWS = 5
JUDGMENTS = {"support", "not_support", "ambiguous"}


class ApixabanSilverAuditError(ValueError):
    """Raised when silver audit provenance or safety boundaries fail."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any], field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ApixabanSilverAuditError(f"{name} must be non-empty and NUL-free")
    return value


def sampling_digest(candidate_sha256: str, patient_id: str, question_id: str) -> str:
    components = (
        SAMPLING_ALGORITHM,
        SAMPLING_SALT,
        candidate_sha256,
        patient_id,
        question_id,
    )
    for index, value in enumerate(components):
        _require_text(value, f"sampling component {index}")
    return hashlib.sha256("\0".join(components).encode("utf-8")).hexdigest()


def validate_silver_candidate(document: Dict[str, Any]) -> None:
    validate_document(document, CANDIDATE_SCHEMA)
    if _self_hash(document, "artifact_sha256") != document["artifact_sha256"]:
        raise ApixabanSilverAuditError("Silver candidate artifact hash mismatch")
    counts = document["generation_counts"]
    if counts["accepted_candidate_count"] != len(document["rows"]):
        raise ApixabanSilverAuditError("Candidate accepted count does not reconcile")
    if counts["proposed_count"] != (
        counts["accepted_candidate_count"]
        + counts["typed_disagreement_count"]
        + counts["missing_evidence_count"]
    ):
        raise ApixabanSilverAuditError("Candidate generation counts do not reconcile")
    seen = set()
    questions = question_index()
    for row in document["rows"]:
        key = (row["patient_id"], row["question_id"])
        if key in seen:
            raise ApixabanSilverAuditError("Silver candidate rows must be unique")
        seen.add(key)
        question = questions.get(row["question_id"])
        if question is None or row["question_type"] != question["question_type"]:
            raise ApixabanSilverAuditError("Candidate question contract mismatch")
        if len(row["evidence_ids"]) != len(set(row["evidence_ids"])):
            raise ApixabanSilverAuditError("Candidate evidence IDs must be unique")
        if len(row["provenance_ids"]) != len(set(row["provenance_ids"])):
            raise ApixabanSilverAuditError("Candidate provenance IDs must be unique")


def _validate_frozen_inputs(
    staging_corpus: Dict[str, Any],
    benchmark: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
    input_plan: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[Tuple[str, str], Dict[str, Any]], set[str]]:
    validate_apixaban_staging_corpus(staging_corpus)
    validate_apixaban_benchmark(
        benchmark, required_source_sha256=None, required_counts=None
    )
    validate_apixaban_split_manifest(
        split_manifest, expected_patient_ids=benchmark["patient_ids"]
    )
    if (
        split_manifest["status"] != "frozen"
        or not split_manifest["freeze"]["test_locked"]
    ):
        raise ApixabanSilverAuditError("Silver audit requires a frozen, locked split")
    validate_apixaban_calibration_reservation(calibration_reservation, split_manifest)
    validate_apixaban_sft_input_plan(input_plan)
    benchmark_sha = serialized_document_sha256(benchmark)
    staging_sha = serialized_document_sha256(staging_corpus)
    if benchmark_sha != split_manifest["dataset"]["benchmark_sha256"]:
        raise ApixabanSilverAuditError("Benchmark does not match the frozen split")
    if staging_sha != split_manifest["dataset"]["staging_corpus_sha256"]:
        raise ApixabanSilverAuditError("Staging corpus does not match the frozen split")
    if calibration_reservation["source"]["benchmark_sha256"] != benchmark_sha:
        raise ApixabanSilverAuditError("Calibration reservation benchmark mismatch")
    if calibration_reservation["source"]["staging_corpus_sha256"] != staging_sha:
        raise ApixabanSilverAuditError("Calibration reservation corpus mismatch")
    train_fit = set(calibration_reservation["partitions"]["train_fit"]["patient_ids"])
    questions = question_index()
    expected = {
        (patient_id, question_id)
        for patient_id in train_fit
        for question_id in questions
    }
    plan = {(row["patient_id"], row["question_id"]): row for row in input_plan["rows"]}
    if set(plan) != expected:
        raise ApixabanSilverAuditError("Input plan must exactly cover train-fit")
    patients = {
        patient["patient_id"]: patient for patient in staging_corpus["patients"]
    }
    if set(patients) != set(benchmark["patient_ids"]):
        raise ApixabanSilverAuditError("Staging and benchmark membership differ")
    assessments = {
        (row["patient_id"], row["question_id"]): row
        for row in benchmark["assessments"]
        if row["patient_id"] in train_fit
    }
    return patients, assessments, train_fit


def _validated_candidate_rows(
    candidate: Dict[str, Any],
    patients: Mapping[str, Mapping[str, Any]],
    assessments: Mapping[Tuple[str, str], Mapping[str, Any]],
    train_fit: set[str],
    input_plan: Dict[str, Any],
    *,
    expected_source: Optional[str] = None,
) -> list[Dict[str, Any]]:
    validate_silver_candidate(candidate)
    if expected_source is not None and candidate["source"] != expected_source:
        raise ApixabanSilverAuditError("Silver candidate source mismatch")
    plan = {(row["patient_id"], row["question_id"]): row for row in input_plan["rows"]}
    questions = question_index()
    rows = []
    for row in candidate["rows"]:
        key = (row["patient_id"], row["question_id"])
        if row["patient_id"] not in train_fit or key not in assessments:
            raise ApixabanSilverAuditError("Candidate crosses a train-fit boundary")
        assessment = assessments[key]
        question = questions[row["question_id"]]
        if assessment["fact_status"] == "unknown" or known_fact_allows_empty_evidence(
            question, assessment
        ):
            raise ApixabanSilverAuditError(
                "Candidate is not citation-required gold-known"
            )
        for field in ("question_type", "fact_status", "value", "unit"):
            expected = (
                question[field] if field == "question_type" else assessment[field]
            )
            if row[field] != expected:
                raise ApixabanSilverAuditError(
                    f"Candidate {field} differs from frozen gold"
                )
        owned = {
            item["evidence_id"] for item in patients[row["patient_id"]]["evidence"]
        }
        visible = set(plan[key]["evidence_ids"])
        cited = set(row["evidence_ids"])
        if not cited.issubset(owned):
            raise ApixabanSilverAuditError("cross_patient_citation")
        if not cited.issubset(visible):
            raise ApixabanSilverAuditError("student_invisible_citation")
        rows.append(row)
    return rows


def _allocations(rows: Sequence[Mapping[str, Any]]) -> Dict[Tuple[str, str], int]:
    grouped: Dict[Tuple[str, str], int] = Counter(
        (row["question_id"], row["fact_status"]) for row in rows
    )
    if len(rows) <= REVIEW_BUDGET:
        return dict(grouped)
    if len(grouped) > REVIEW_BUDGET:
        raise ApixabanSilverAuditError(
            "Audit budget cannot cover every non-empty stratum"
        )
    allocated = {key: 1 for key in grouped}
    remaining_budget = REVIEW_BUDGET - len(grouped)
    capacity = {key: count - 1 for key, count in grouped.items()}
    total_capacity = sum(capacity.values())
    shares = {
        key: (
            divmod(remaining_budget * capacity[key], total_capacity)
            if total_capacity
            else (0, 0)
        )
        for key in grouped
    }
    for key in grouped:
        allocated[key] += shares[key][0]
    left = REVIEW_BUDGET - sum(allocated.values())
    order = sorted(grouped, key=lambda key: (-shares[key][1], key))
    for key in order:
        if left == 0:
            break
        if allocated[key] < grouped[key]:
            allocated[key] += 1
            left -= 1
    if left or any(allocated[key] > grouped[key] for key in grouped):
        raise ApixabanSilverAuditError("Audit allocation did not reconcile")
    return allocated


def build_silver_audit_package(
    staging_corpus: Dict[str, Any],
    benchmark: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
    input_plan: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    _require_text(generation_command, "generation_command")
    patients, assessments, train_fit = _validate_frozen_inputs(
        staging_corpus, benchmark, split_manifest, calibration_reservation, input_plan
    )
    rows = _validated_candidate_rows(
        candidate, patients, assessments, train_fit, input_plan
    )
    if not rows:
        raise ApixabanSilverAuditError("Silver candidate has no auditable rows")
    allocations = _allocations(rows)
    grouped: Dict[Tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["question_id"], row["fact_status"])].append(row)
    questions = question_index()
    selected = []
    for key in sorted(grouped):
        ranked = sorted(
            grouped[key],
            key=lambda row: (
                sampling_digest(
                    candidate["artifact_sha256"], row["patient_id"], row["question_id"]
                ),
                row["patient_id"],
            ),
        )
        selected.extend(ranked[: allocations[key]])
    selected.sort(
        key=lambda row: sampling_digest(
            candidate["artifact_sha256"], row["patient_id"], row["question_id"]
        )
    )
    evidence_by_patient = {
        patient_id: {item["evidence_id"]: item for item in patient["evidence"]}
        for patient_id, patient in patients.items()
    }
    package_rows = []
    for row in selected:
        question = questions[row["question_id"]]
        package_rows.append(
            {
                "sampling_digest": sampling_digest(
                    candidate["artifact_sha256"], row["patient_id"], row["question_id"]
                ),
                "patient_id": row["patient_id"],
                "question_id": row["question_id"],
                "question_type": row["question_type"],
                "fact_status": row["fact_status"],
                "value": row["value"],
                "unit": row["unit"],
                "source_question": question["source_question"],
                "cited_evidence": [
                    copy.deepcopy(evidence_by_patient[row["patient_id"]][item])
                    for item in row["evidence_ids"]
                ],
                "silver_source": candidate["source"],
                "provenance_ids": list(row["provenance_ids"]),
                "machine_checks": {
                    "cross_patient_citation": False,
                    "student_invisible_citation": False,
                },
            }
        )
    strata = [
        {
            "question_id": key[0],
            "question_type": questions[key[0]]["question_type"],
            "fact_status": key[1],
            "candidate_count": len(grouped[key]),
            "allocated_count": allocations[key],
        }
        for key in sorted(grouped)
    ]
    package: Dict[str, Any] = {
        "apixaban_silver_audit_package_version": AUDIT_PACKAGE_VERSION,
        "package_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command,
        "source": {
            "silver_source": candidate["source"],
            "candidate_artifact_sha256": candidate["artifact_sha256"],
            "source_artifact_sha256": candidate["source_artifact_sha256"],
            "benchmark_sha256": serialized_document_sha256(benchmark),
            "staging_corpus_sha256": serialized_document_sha256(staging_corpus),
            "split_manifest_sha256": split_manifest["manifest_sha256"],
            "calibration_reservation_sha256": calibration_reservation[
                "manifest_sha256"
            ],
            "input_policy_sha256": input_plan["input_policy_sha256"],
            "question_catalog_sha256": load_question_catalog()["catalog_sha256"],
        },
        "protocol": {
            "sampling_algorithm": SAMPLING_ALGORITHM,
            "selection_salt": SAMPLING_SALT,
            "allocation_version": ALLOCATION_VERSION,
            "rubric_version": RUBRIC_VERSION,
            "review_budget": REVIEW_BUDGET,
            "reviewer_count": 1,
            "reviewer_role": "data_owner",
        },
        "population": {
            "candidate_count": len(rows),
            "stratum_count": len(strata),
            "sample_count": len(package_rows),
            "all_candidates_reviewed": len(rows) <= REVIEW_BUDGET,
        },
        "strata": strata,
        "rows": package_rows,
        "restrictions": {
            "contains_restricted_text": True,
            "owner_only": True,
            "online_upload_permitted": False,
            "independent_evidence_gold": False,
        },
    }
    package["package_sha256"] = _self_hash(package, "package_sha256")
    validate_silver_audit_package(package, candidate)
    _validate_package_context(
        package,
        candidate,
        patients,
        benchmark,
        staging_corpus,
        split_manifest,
        calibration_reservation,
        input_plan,
    )
    pending = {
        "apixaban_silver_judgment_version": JUDGMENT_VERSION,
        "judgment_sha256": None,
        "status": "pending_owner_review",
        "completed_at": None,
        "audit_package_sha256": package["package_sha256"],
        "candidate_artifact_sha256": candidate["artifact_sha256"],
        "protocol": copy.deepcopy(package["protocol"]),
        "reviewer": {
            "count": 1,
            "role": "data_owner",
            "independent_evidence_gold": False,
        },
        "rows": [
            {
                **copy.deepcopy(row),
                "judgment": None,
                "zero_tolerance_reconfirmed": {
                    "cross_patient_citation": None,
                    "student_invisible_citation": None,
                },
            }
            for row in package_rows
        ],
    }
    validate_silver_judgment_template(pending, package)
    return package, pending


def validate_silver_audit_package(
    package: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    validate_document(package, AUDIT_PACKAGE_SCHEMA)
    validate_silver_candidate(candidate)
    if _self_hash(package, "package_sha256") != package["package_sha256"]:
        raise ApixabanSilverAuditError("Audit package hash mismatch")
    if package["source"]["candidate_artifact_sha256"] != candidate["artifact_sha256"]:
        raise ApixabanSilverAuditError("Audit package candidate binding mismatch")
    protocol = package["protocol"]
    expected_protocol = (
        SAMPLING_ALGORITHM,
        SAMPLING_SALT,
        ALLOCATION_VERSION,
        RUBRIC_VERSION,
        REVIEW_BUDGET,
        1,
        "data_owner",
    )
    actual_protocol = tuple(
        protocol[key]
        for key in (
            "sampling_algorithm",
            "selection_salt",
            "allocation_version",
            "rubric_version",
            "review_budget",
            "reviewer_count",
            "reviewer_role",
        )
    )
    if actual_protocol != expected_protocol:
        raise ApixabanSilverAuditError("Audit package protocol mismatch")
    allocations = _allocations(candidate["rows"])
    expected_keys = set()
    grouped: Dict[Tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in candidate["rows"]:
        grouped[(row["question_id"], row["fact_status"])].append(row)
    for key, rows in grouped.items():
        ranked = sorted(
            rows,
            key=lambda row: (
                sampling_digest(
                    candidate["artifact_sha256"], row["patient_id"], row["question_id"]
                ),
                row["patient_id"],
            ),
        )
        expected_keys.update(
            (row["patient_id"], row["question_id"])
            for row in ranked[: allocations[key]]
        )
    package_keys = {(row["patient_id"], row["question_id"]) for row in package["rows"]}
    if package_keys != expected_keys or len(package_keys) != len(package["rows"]):
        raise ApixabanSilverAuditError(
            "Audit package sample is not the deterministic selection"
        )
    digests = [row["sampling_digest"] for row in package["rows"]]
    if digests != sorted(digests):
        raise ApixabanSilverAuditError("Audit package rows must be digest-sorted")
    for row in package["rows"]:
        expected = sampling_digest(
            candidate["artifact_sha256"], row["patient_id"], row["question_id"]
        )
        if row["sampling_digest"] != expected:
            raise ApixabanSilverAuditError("Audit row digest mismatch")
        if any(row["machine_checks"].values()):
            raise ApixabanSilverAuditError(
                "Audit package contains a zero-tolerance defect"
            )
        candidate_row = next(
            item
            for item in candidate["rows"]
            if (item["patient_id"], item["question_id"])
            == (row["patient_id"], row["question_id"])
        )
        for field in ("question_type", "fact_status", "value", "unit"):
            if row[field] != candidate_row[field]:
                raise ApixabanSilverAuditError("Audit row differs from its candidate")
        if row["silver_source"] != candidate["source"]:
            raise ApixabanSilverAuditError("Audit row silver source mismatch")
        if row["provenance_ids"] != candidate_row["provenance_ids"]:
            raise ApixabanSilverAuditError("Audit row provenance mismatch")
        if [item["evidence_id"] for item in row["cited_evidence"]] != candidate_row[
            "evidence_ids"
        ]:
            raise ApixabanSilverAuditError(
                "Audit row evidence IDs differ from candidate"
            )
        if (
            row["source_question"]
            != question_index()[row["question_id"]]["source_question"]
        ):
            raise ApixabanSilverAuditError("Audit row source question mismatch")
    population = package["population"]
    if population != {
        "candidate_count": len(candidate["rows"]),
        "stratum_count": len(grouped),
        "sample_count": len(package["rows"]),
        "all_candidates_reviewed": len(candidate["rows"]) <= REVIEW_BUDGET,
    }:
        raise ApixabanSilverAuditError("Audit population counts do not reconcile")
    questions = question_index()
    expected_strata = [
        {
            "question_id": key[0],
            "question_type": questions[key[0]]["question_type"],
            "fact_status": key[1],
            "candidate_count": len(grouped[key]),
            "allocated_count": allocations[key],
        }
        for key in sorted(grouped)
    ]
    if package["strata"] != expected_strata:
        raise ApixabanSilverAuditError("Audit strata do not match candidate allocation")


def _validate_package_context(
    package: Dict[str, Any],
    candidate: Dict[str, Any],
    patients: Mapping[str, Mapping[str, Any]],
    benchmark: Dict[str, Any],
    staging_corpus: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
    input_plan: Dict[str, Any],
) -> None:
    expected_source = {
        "silver_source": candidate["source"],
        "candidate_artifact_sha256": candidate["artifact_sha256"],
        "source_artifact_sha256": candidate["source_artifact_sha256"],
        "benchmark_sha256": serialized_document_sha256(benchmark),
        "staging_corpus_sha256": serialized_document_sha256(staging_corpus),
        "split_manifest_sha256": split_manifest["manifest_sha256"],
        "calibration_reservation_sha256": calibration_reservation["manifest_sha256"],
        "input_policy_sha256": input_plan["input_policy_sha256"],
        "question_catalog_sha256": load_question_catalog()["catalog_sha256"],
    }
    if package["source"] != expected_source:
        raise ApixabanSilverAuditError("Audit package source context mismatch")
    candidate_by_key = {
        (row["patient_id"], row["question_id"]): row for row in candidate["rows"]
    }
    for row in package["rows"]:
        candidate_row = candidate_by_key[(row["patient_id"], row["question_id"])]
        evidence = {
            item["evidence_id"]: item
            for item in patients[row["patient_id"]]["evidence"]
        }
        expected_evidence = [evidence[item] for item in candidate_row["evidence_ids"]]
        if row["cited_evidence"] != expected_evidence:
            raise ApixabanSilverAuditError(
                "Audit package citation text differs from staging corpus"
            )


def validate_silver_judgment_template(
    template: Dict[str, Any], package: Dict[str, Any]
) -> None:
    if (
        template["status"] != "pending_owner_review"
        or template["judgment_sha256"] is not None
        or template["completed_at"] is not None
    ):
        raise ApixabanSilverAuditError("Judgment template status is invalid")
    _validate_judgment_binding(template, package, completed=False)


def _validate_judgment_binding(
    judgments: Dict[str, Any], package: Dict[str, Any], *, completed: bool
) -> None:
    expected_fields = {
        "apixaban_silver_judgment_version",
        "judgment_sha256",
        "status",
        "completed_at",
        "audit_package_sha256",
        "candidate_artifact_sha256",
        "protocol",
        "reviewer",
        "rows",
    }
    if set(judgments) != expected_fields:
        raise ApixabanSilverAuditError("Judgment record fields are incomplete")
    if judgments["audit_package_sha256"] != package["package_sha256"]:
        raise ApixabanSilverAuditError("Judgment record package binding mismatch")
    if (
        judgments["candidate_artifact_sha256"]
        != package["source"]["candidate_artifact_sha256"]
    ):
        raise ApixabanSilverAuditError("Judgment record candidate binding mismatch")
    if judgments["protocol"] != package["protocol"]:
        raise ApixabanSilverAuditError("Judgment record protocol mismatch")
    if judgments["reviewer"] != {
        "count": 1,
        "role": "data_owner",
        "independent_evidence_gold": False,
    }:
        raise ApixabanSilverAuditError("Judgment reviewer contract mismatch")
    if len(judgments["rows"]) != len(package["rows"]):
        raise ApixabanSilverAuditError("Judgment row count differs from audit package")
    mutable = {"judgment", "zero_tolerance_reconfirmed"}
    for judgment, audited in zip(judgments["rows"], package["rows"]):
        if set(judgment) != set(audited) | mutable:
            raise ApixabanSilverAuditError("Judgment row fields are incomplete")
        immutable = {
            key: value for key, value in judgment.items() if key not in mutable
        }
        if immutable != audited:
            raise ApixabanSilverAuditError(
                "Judgment row changed immutable audit content"
            )
        if completed:
            if judgment["judgment"] not in JUDGMENTS:
                raise ApixabanSilverAuditError(
                    "Every sampled row requires one judgment"
                )
            if judgment["zero_tolerance_reconfirmed"] != {
                "cross_patient_citation": False,
                "student_invisible_citation": False,
            }:
                raise ApixabanSilverAuditError(
                    "Manual review found or omitted a zero-tolerance check"
                )
        elif judgment["judgment"] is not None or any(
            value is not None
            for value in judgment["zero_tolerance_reconfirmed"].values()
        ):
            raise ApixabanSilverAuditError("Pending judgment template was pre-filled")


def finalize_silver_judgments(
    filled_template: Dict[str, Any],
    package: Dict[str, Any],
    *,
    completed_at: Optional[str] = None,
) -> Dict[str, Any]:
    completed = copy.deepcopy(filled_template)
    completed["status"] = "completed"
    completed["completed_at"] = completed_at or _now()
    completed["judgment_sha256"] = "pending"
    _validate_judgment_binding(completed, package, completed=True)
    completed["judgment_sha256"] = _self_hash(completed, "judgment_sha256")
    validate_silver_judgments(completed, package)
    return completed


def validate_silver_judgments(
    judgments: Dict[str, Any], package: Dict[str, Any]
) -> None:
    validate_document(judgments, JUDGMENT_SCHEMA)
    if _self_hash(judgments, "judgment_sha256") != judgments["judgment_sha256"]:
        raise ApixabanSilverAuditError("Judgment record hash mismatch")
    _validate_judgment_binding(judgments, package, completed=True)


def _citation_denominators(
    benchmark: Dict[str, Any], train_fit: set[str]
) -> Tuple[Counter[str], Counter[str], Counter[str], Counter[str]]:
    questions = question_index()
    per_question: Counter[str] = Counter()
    per_type: Counter[str] = Counter()
    per_status: Counter[str] = Counter()
    population: Counter[str] = Counter()
    for row in benchmark["assessments"]:
        if row["patient_id"] not in train_fit:
            continue
        population["train_fit_assessment_count"] += 1
        if row["fact_status"] == "unknown":
            population["gold_unknown_count"] += 1
            continue
        population["gold_known_count"] += 1
        question = questions[row["question_id"]]
        if known_fact_allows_empty_evidence(question, row):
            population["default_absent_exception_count"] += 1
            continue
        population["citation_required_count"] += 1
        per_question[row["question_id"]] += 1
        per_type[row["question_type"]] += 1
        per_status[row["fact_status"]] += 1
    return per_question, per_type, per_status, population


def _source_after_review(
    candidate: Dict[str, Any], package: Dict[str, Any], judgments: Dict[str, Any]
) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    validate_silver_audit_package(package, candidate)
    validate_silver_judgments(judgments, package)
    counts = Counter(row["judgment"] for row in judgments["rows"])
    sample_count = len(judgments["rows"])
    passed = counts["support"] * 100 >= sample_count * MIN_SOURCE_SUPPORT_PERCENT
    rejected = {
        (row["patient_id"], row["question_id"])
        for row in judgments["rows"]
        if row["judgment"] != "support"
    }
    accepted = (
        [
            copy.deepcopy(row)
            for row in candidate["rows"]
            if (row["patient_id"], row["question_id"]) not in rejected
        ]
        if passed
        else []
    )
    return accepted, {
        "source": candidate["source"],
        "candidate_artifact_sha256": candidate["artifact_sha256"],
        "audit_package_sha256": package["package_sha256"],
        "judgment_sha256": judgments["judgment_sha256"],
        "candidate_count": len(candidate["rows"]),
        "sample_count": sample_count,
        "support_count": counts["support"],
        "not_support_count": counts["not_support"],
        "ambiguous_count": counts["ambiguous"],
        "support_percent": counts["support"] * 100.0 / sample_count,
        "source_quality_passed": passed,
        "reviewed_failure_removed_count": len(rejected),
        "accepted_after_review_count": len(accepted),
    }


def build_silver_quality_gate(
    staging_corpus: Dict[str, Any],
    benchmark: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
    input_plan: Dict[str, Any],
    d_candidate: Dict[str, Any],
    d_package: Dict[str, Any],
    d_judgments: Dict[str, Any],
    e_bundle: Optional[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = None,
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: str,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    _require_text(generation_command, "generation_command")
    patients, assessments, train_fit = _validate_frozen_inputs(
        staging_corpus, benchmark, split_manifest, calibration_reservation, input_plan
    )
    _validated_candidate_rows(
        d_candidate, patients, assessments, train_fit, input_plan, expected_source="D"
    )
    _validate_package_context(
        d_package,
        d_candidate,
        patients,
        benchmark,
        staging_corpus,
        split_manifest,
        calibration_reservation,
        input_plan,
    )
    accepted_d, d_report = _source_after_review(d_candidate, d_package, d_judgments)
    accepted_e: list[Dict[str, Any]] = []
    source_reports = [d_report]
    e_candidate = None
    if e_bundle is not None:
        e_candidate, e_package, e_judgments = e_bundle
        _validated_candidate_rows(
            e_candidate,
            patients,
            assessments,
            train_fit,
            input_plan,
            expected_source="E",
        )
        _validate_package_context(
            e_package,
            e_candidate,
            patients,
            benchmark,
            staging_corpus,
            split_manifest,
            calibration_reservation,
            input_plan,
        )
        accepted_e, e_report = _source_after_review(e_candidate, e_package, e_judgments)
        source_reports.append(e_report)
        d_keys = {(row["patient_id"], row["question_id"]) for row in accepted_d}
        e_candidate_keys = {
            (row["patient_id"], row["question_id"]) for row in e_candidate["rows"]
        }
        if d_keys & e_candidate_keys:
            raise ApixabanSilverAuditError("Teacher E candidate overlaps accepted D")
        e_keys = {(row["patient_id"], row["question_id"]) for row in accepted_e}
        if d_keys & e_keys:
            raise ApixabanSilverAuditError("Accepted E overlaps accepted D")
    accepted_rows = [("D", row) for row in accepted_d] + [
        ("E", row) for row in accepted_e
    ]
    (
        denominator_by_question,
        denominator_by_type,
        denominator_by_status,
        population,
    ) = _citation_denominators(benchmark, train_fit)
    denominator = population["citation_required_count"]
    accepted_by_question: Counter[str] = Counter(
        row["question_id"] for _, row in accepted_rows
    )
    accepted_by_type: Counter[str] = Counter(
        row["question_type"] for _, row in accepted_rows
    )
    accepted_by_status: Counter[str] = Counter(
        row["fact_status"] for _, row in accepted_rows
    )
    accepted_keys = [
        (row["patient_id"], row["question_id"]) for _, row in accepted_rows
    ]
    if len(accepted_keys) != len(set(accepted_keys)):
        raise ApixabanSilverAuditError("Accepted silver rows overlap across sources")
    overall_pass = (
        len(accepted_rows) * 100 >= denominator * MIN_OVERALL_COVERAGE_PERCENT
    )
    questions = question_index()
    per_question = []
    question_pass = True
    for question_id in sorted(questions):
        count = denominator_by_question[question_id]
        accepted = accepted_by_question[question_id]
        applicable = count > 0
        passed = (not applicable) or (
            accepted >= MIN_QUESTION_ACCEPTED_ROWS
            and accepted * 100 >= count * MIN_QUESTION_COVERAGE_PERCENT
        )
        question_pass &= passed
        per_question.append(
            {
                "question_id": question_id,
                "question_type": questions[question_id]["question_type"],
                "citation_required_count": count,
                "accepted_count": accepted,
                "coverage_percent": (accepted * 100.0 / count if count else None),
                "applicable": applicable,
                "passed": passed,
            }
        )
    source_pass = all(item["source_quality_passed"] for item in source_reports)
    passed = source_pass and overall_pass and question_pass
    if passed:
        status = "passed_predeclared_thresholds"
    elif not source_pass:
        status = "failed_source_quality"
    elif e_bundle is None:
        status = "needs_e_backoff"
    else:
        status = "failed_coverage"
    report: Dict[str, Any] = {
        "apixaban_silver_quality_audit_version": QUALITY_AUDIT_VERSION,
        "quality_audit_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command,
        "status": status,
        "protocol": {
            "sampling_algorithm": SAMPLING_ALGORITHM,
            "minimum_source_support_percent": MIN_SOURCE_SUPPORT_PERCENT,
            "minimum_overall_coverage_percent": MIN_OVERALL_COVERAGE_PERCENT,
            "minimum_question_coverage_percent": MIN_QUESTION_COVERAGE_PERCENT,
            "minimum_question_accepted_rows": MIN_QUESTION_ACCEPTED_ROWS,
        },
        "source": {
            "benchmark_sha256": serialized_document_sha256(benchmark),
            "staging_corpus_sha256": serialized_document_sha256(staging_corpus),
            "split_manifest_sha256": split_manifest["manifest_sha256"],
            "calibration_reservation_sha256": calibration_reservation[
                "manifest_sha256"
            ],
            "input_policy_sha256": input_plan["input_policy_sha256"],
            "question_catalog_sha256": load_question_catalog()["catalog_sha256"],
        },
        "source_audits": source_reports,
        "population": {
            "train_fit_patient_count": len(train_fit),
            **{
                field: population[field]
                for field in (
                    "train_fit_assessment_count",
                    "gold_known_count",
                    "gold_unknown_count",
                    "default_absent_exception_count",
                    "citation_required_count",
                )
            },
        },
        "coverage": {
            "citation_required_count": denominator,
            "accepted_count": len(accepted_rows),
            "accepted_d_count": len(accepted_d),
            "accepted_e_count": len(accepted_e),
            "coverage_percent": (
                len(accepted_rows) * 100.0 / denominator if denominator else 0.0
            ),
            "overall_passed": overall_pass,
            "all_questions_passed": question_pass,
            "per_question": per_question,
            "by_answer_type": [
                {
                    "answer_type": name,
                    "citation_required_count": denominator_by_type[name],
                    "accepted_count": accepted_by_type[name],
                    "coverage_percent": (
                        accepted_by_type[name] * 100.0 / denominator_by_type[name]
                        if denominator_by_type[name]
                        else None
                    ),
                }
                for name in ("boolean", "numeric")
            ],
            "by_fact_status": [
                {
                    "fact_status": name,
                    "citation_required_count": denominator_by_status[name],
                    "accepted_count": accepted_by_status[name],
                    "coverage_percent": (
                        accepted_by_status[name] * 100.0 / denominator_by_status[name]
                        if denominator_by_status[name]
                        else None
                    ),
                }
                for name in ("present", "absent")
            ],
        },
        "rejections": {
            "typed_disagreement_count": sum(
                candidate["generation_counts"]["typed_disagreement_count"]
                for candidate in (
                    [d_candidate] + ([e_candidate] if e_candidate else [])
                )
            ),
            "missing_evidence_count": sum(
                candidate["generation_counts"]["missing_evidence_count"]
                for candidate in (
                    [d_candidate] + ([e_candidate] if e_candidate else [])
                )
            ),
            "invalid_ownership_count": 0,
            "student_invisibility_count": 0,
            "ambiguous_count": sum(item["ambiguous_count"] for item in source_reports),
            "not_support_count": sum(
                item["not_support_count"] for item in source_reports
            ),
            "failed_manual_review_count": sum(
                item["ambiguous_count"] + item["not_support_count"]
                for item in source_reports
            ),
        },
        "restrictions": {
            "owner_only": True,
            "online_upload_permitted": False,
            "silver_is_evidence_gold": False,
            "test_labels_used": False,
        },
    }
    report["quality_audit_sha256"] = _self_hash(report, "quality_audit_sha256")
    validate_silver_quality_audit(report)
    if not passed:
        return report, None, None

    def accepted_document(
        source: str, candidate: Dict[str, Any], rows: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        document = {
            "accepted_silver_version": ACCEPTED_SILVER_VERSION,
            "artifact_sha256": "pending",
            "source": source,
            "source_artifact_sha256": candidate["source_artifact_sha256"],
            "quality_audit_sha256": report["quality_audit_sha256"],
            "audit_status": "passed_predeclared_thresholds",
            "rows": [
                {
                    key: copy.deepcopy(row[key])
                    for key in (
                        "patient_id",
                        "question_id",
                        "fact_status",
                        "value",
                        "unit",
                        "evidence_ids",
                    )
                }
                for row in rows
            ],
        }
        document["artifact_sha256"] = _self_hash(document, "artifact_sha256")
        validate_accepted_silver(document, source)
        return document

    d_document = accepted_document("D", d_candidate, accepted_d)
    e_document = (
        accepted_document("E", e_candidate, accepted_e)
        if e_candidate is not None and accepted_e
        else None
    )
    return report, d_document, e_document


def validate_silver_quality_audit(report: Dict[str, Any]) -> None:
    validate_document(report, QUALITY_AUDIT_SCHEMA)
    if _self_hash(report, "quality_audit_sha256") != report["quality_audit_sha256"]:
        raise ApixabanSilverAuditError("Silver quality-audit hash mismatch")
    coverage = report["coverage"]
    population = report["population"]
    source_audits = report["source_audits"]
    if [row["source"] for row in source_audits] not in (["D"], ["D", "E"]):
        raise ApixabanSilverAuditError("Source audit order must be D then optional E")
    for source in source_audits:
        if source["sample_count"] != (
            source["support_count"]
            + source["not_support_count"]
            + source["ambiguous_count"]
        ):
            raise ApixabanSilverAuditError("Source judgment counts do not reconcile")
        if source["reviewed_failure_removed_count"] != (
            source["not_support_count"] + source["ambiguous_count"]
        ):
            raise ApixabanSilverAuditError("Source rejection counts do not reconcile")
        if source["support_percent"] != (
            source["support_count"] * 100.0 / source["sample_count"]
        ):
            raise ApixabanSilverAuditError("Source support percentage is incorrect")
        passed = (
            source["support_count"] * 100
            >= source["sample_count"] * MIN_SOURCE_SUPPORT_PERCENT
        )
        if source["source_quality_passed"] != passed:
            raise ApixabanSilverAuditError("Source quality decision is incorrect")
        expected_accepted = (
            source["candidate_count"] - source["reviewed_failure_removed_count"]
            if passed
            else 0
        )
        if source["accepted_after_review_count"] != expected_accepted:
            raise ApixabanSilverAuditError("Source accepted count is incorrect")
    if population["gold_known_count"] != (
        population["citation_required_count"]
        + population["default_absent_exception_count"]
    ):
        raise ApixabanSilverAuditError("Gold-known population does not reconcile")
    if population["train_fit_assessment_count"] != (
        population["gold_known_count"] + population["gold_unknown_count"]
    ):
        raise ApixabanSilverAuditError("Train-fit population does not reconcile")
    if population["citation_required_count"] != coverage["citation_required_count"]:
        raise ApixabanSilverAuditError("Population and coverage denominators differ")
    if coverage["accepted_d_count"] != source_audits[0]["accepted_after_review_count"]:
        raise ApixabanSilverAuditError("D accepted count differs from its source audit")
    expected_e = (
        source_audits[1]["accepted_after_review_count"]
        if len(source_audits) == 2
        else 0
    )
    if coverage["accepted_e_count"] != expected_e:
        raise ApixabanSilverAuditError("E accepted count differs from its source audit")
    if (
        coverage["accepted_count"]
        != coverage["accepted_d_count"] + coverage["accepted_e_count"]
    ):
        raise ApixabanSilverAuditError("Accepted source counts do not reconcile")
    if (
        sum(row["citation_required_count"] for row in coverage["per_question"])
        != coverage["citation_required_count"]
    ):
        raise ApixabanSilverAuditError("Per-question denominator does not reconcile")
    if (
        sum(row["accepted_count"] for row in coverage["per_question"])
        != coverage["accepted_count"]
    ):
        raise ApixabanSilverAuditError("Per-question accepted count does not reconcile")
    question_ids = sorted(question_index())
    if [row["question_id"] for row in coverage["per_question"]] != question_ids:
        raise ApixabanSilverAuditError("Coverage must report every question once")
    for row in coverage["per_question"]:
        applicable = row["citation_required_count"] > 0
        passed = (not applicable) or (
            row["accepted_count"] >= MIN_QUESTION_ACCEPTED_ROWS
            and row["accepted_count"] * 100
            >= row["citation_required_count"] * MIN_QUESTION_COVERAGE_PERCENT
        )
        if row["applicable"] != applicable or row["passed"] != passed:
            raise ApixabanSilverAuditError(
                "Per-question coverage decision is incorrect"
            )
    for field in ("by_answer_type", "by_fact_status"):
        if (
            sum(row["citation_required_count"] for row in coverage[field])
            != coverage["citation_required_count"]
        ):
            raise ApixabanSilverAuditError(f"{field} denominator does not reconcile")
        if (
            sum(row["accepted_count"] for row in coverage[field])
            != coverage["accepted_count"]
        ):
            raise ApixabanSilverAuditError(f"{field} accepted count does not reconcile")
    if [row["answer_type"] for row in coverage["by_answer_type"]] != [
        "boolean",
        "numeric",
    ]:
        raise ApixabanSilverAuditError("Answer-type coverage order is incorrect")
    if [row["fact_status"] for row in coverage["by_fact_status"]] != [
        "present",
        "absent",
    ]:
        raise ApixabanSilverAuditError("Fact-status coverage order is incorrect")
    rejections = report["rejections"]
    if rejections["failed_manual_review_count"] != (
        rejections["ambiguous_count"] + rejections["not_support_count"]
    ):
        raise ApixabanSilverAuditError(
            "Manual-review rejection counts do not reconcile"
        )
    if rejections["ambiguous_count"] != sum(
        row["ambiguous_count"] for row in source_audits
    ) or rejections["not_support_count"] != sum(
        row["not_support_count"] for row in source_audits
    ):
        raise ApixabanSilverAuditError("Source and aggregate rejection counts differ")
    expected_overall = (
        coverage["accepted_count"] * 100
        >= coverage["citation_required_count"] * MIN_OVERALL_COVERAGE_PERCENT
    )
    expected_questions = all(row["passed"] for row in coverage["per_question"])
    if (
        coverage["overall_passed"] != expected_overall
        or coverage["all_questions_passed"] != expected_questions
    ):
        raise ApixabanSilverAuditError("Coverage gate decision is incorrect")
    source_pass = all(row["source_quality_passed"] for row in source_audits)
    if source_pass and expected_overall and expected_questions:
        expected_status = "passed_predeclared_thresholds"
    elif not source_pass:
        expected_status = "failed_source_quality"
    elif len(source_audits) == 1:
        expected_status = "needs_e_backoff"
    else:
        expected_status = "failed_coverage"
    if report["status"] != expected_status:
        raise ApixabanSilverAuditError("Silver quality-audit status is incorrect")


def write_silver_audit_package(
    package: Dict[str, Any], pending: Dict[str, Any], output_directory: Path
) -> Tuple[Path, Path]:
    paths = (
        output_directory / "audit-package.json",
        output_directory / "judgments.pending.json",
    )
    written = []
    try:
        for document, path in zip((package, pending), paths):
            write_private_json(document, path)
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return paths
