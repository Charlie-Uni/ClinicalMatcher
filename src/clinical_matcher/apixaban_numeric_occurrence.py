import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import (
    file_sha256,
    validate_apixaban_benchmark,
)
from .apixaban_bm25 import validate_bm25_run
from .apixaban_contract import load_question_catalog
from .apixaban_dense import validate_dense_run
from .apixaban_evidence_index import evidence_index_records
from .apixaban_rrf import validate_rrf_run
from .apixaban_split import load_apixaban_split_manifest, write_private_json
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = (
    "resources/apixaban-numeric-occurrence-contract-1.0.0.json"
)
REPORT_SCHEMA = (
    "schemas/apixaban-numeric-occurrence-report-1.0.0.schema.json"
)
REPORT_VERSION = "1.0.0"
RETRIEVER_NAMES = ("bm25", "medcpt_dense", "rrf60")
NUMERIC_TOKEN_PATTERN = (
    r"(?<![A-Za-z0-9_.])[-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|"
    r"[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?![A-Za-z0-9_.])"
)


class ApixabanNumericOccurrenceError(ValueError):
    """Raised when the frozen weak numeric-occurrence diagnostic is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("report_sha256", None)
    return canonical_sha256(unsigned)


def load_numeric_occurrence_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document: Dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    validate_numeric_occurrence_contract(document)
    return document


def validate_numeric_occurrence_contract(document: Mapping[str, Any]) -> None:
    required = {
        "contract_version",
        "contract_id",
        "split_name",
        "test_labels_used",
        "population",
        "numeric_matching",
        "retrievers",
        "cutoffs",
        "interpretation",
        "privacy",
    }
    if set(document) != required:
        raise ApixabanNumericOccurrenceError("Occurrence contract is incomplete")
    if document["contract_version"] != "1.0.0":
        raise ApixabanNumericOccurrenceError("Unsupported occurrence contract")
    if document["contract_id"] != "apixaban-numeric-answer-occurrence-v1":
        raise ApixabanNumericOccurrenceError("Unexpected occurrence contract ID")
    if document["split_name"] != "validation":
        raise ApixabanNumericOccurrenceError("Diagnostic must remain validation-only")
    if document["test_labels_used"] is not False:
        raise ApixabanNumericOccurrenceError("Test labels are forbidden")
    if document["retrievers"] != list(RETRIEVER_NAMES):
        raise ApixabanNumericOccurrenceError("Occurrence retriever set changed")
    if document["cutoffs"] != [1, 3]:
        raise ApixabanNumericOccurrenceError("Occurrence cutoffs changed")
    population = document["population"]
    if population != {
        "included_question_type": "numeric",
        "included_fact_status": "present",
        "full_context_exact_occurrence_required": True,
        "excluded_question_types": ["boolean"],
        "excluded_fact_statuses": ["unknown"],
        "excluded_protocol_values": [
            {
                "source_criterion_label": "lvef",
                "value": 55,
                "reason": "source_protocol_maps_any_minimum_at_or_above_55_to_55",
            }
        ],
    }:
        raise ApixabanNumericOccurrenceError("Occurrence population changed")
    matching = document["numeric_matching"]
    if matching != {
        "method": "independent_decimal_token_exact_value",
        "token_pattern": NUMERIC_TOKEN_PATTERN,
        "thousands_separator": ",",
        "comparison": "exact_decimal_equality",
        "scientific_notation_supported": False,
        "selected_match_scope": "within_any_individual_selected_chunk",
    }:
        raise ApixabanNumericOccurrenceError("Occurrence matching contract changed")
    try:
        re.compile(matching["token_pattern"])
    except re.error as error:
        raise ApixabanNumericOccurrenceError(
            "Occurrence token pattern is invalid"
        ) from error
    interpretation = document["interpretation"]
    if interpretation != {
        "signal_tier": "weak_diagnostic",
        "independent_evidence_gold": False,
        "evidence_relevance_metric": False,
        "allowed_claim": "retrieved_chunks_retain_an_exact_gold_numeric_token",
        "forbidden_claim": (
            "retrieved_chunks_are_clinically_relevant_or_complete"
        ),
    }:
        raise ApixabanNumericOccurrenceError(
            "Occurrence interpretation boundary changed"
        )
    privacy = document["privacy"]
    if privacy != {
        "patient_level_output_allowed": False,
        "note_text_output_allowed": False,
        "restricted_aggregate_report": True,
    }:
        raise ApixabanNumericOccurrenceError("Occurrence privacy boundary changed")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Numeric occurrence value must be a number")
    try:
        resolved = Decimal(str(value).replace(",", ""))
    except InvalidOperation as error:
        raise ValueError("Numeric occurrence value is invalid") from error
    if not resolved.is_finite():
        raise ValueError("Numeric occurrence value must be finite")
    return resolved


def contains_exact_numeric_token(
    texts: Sequence[str],
    value: Any,
    *,
    token_pattern: Optional[str] = None,
) -> bool:
    resolved_pattern = token_pattern
    if resolved_pattern is None:
        resolved_pattern = load_numeric_occurrence_contract()[
            "numeric_matching"
        ]["token_pattern"]
    pattern = re.compile(resolved_pattern)
    target = _decimal(value)
    for text in texts:
        for match in pattern.finditer(text):
            if _decimal(match.group(0)) == target:
                return True
    return False


def validate_numeric_occurrence_report(document: Mapping[str, Any]) -> None:
    validate_document(dict(document), REPORT_SCHEMA)
    if _self_hash(document) != document["report_sha256"]:
        raise ApixabanNumericOccurrenceError("Occurrence report hash mismatch")
    contract = load_numeric_occurrence_contract()
    if document["provenance"]["contract_sha256"] != canonical_sha256(contract):
        raise ApixabanNumericOccurrenceError("Occurrence contract hash mismatch")
    expected_configuration = {
        "contract_id": contract["contract_id"],
        "matching_method": contract["numeric_matching"]["method"],
        "token_pattern": contract["numeric_matching"]["token_pattern"],
        "comparison": contract["numeric_matching"]["comparison"],
        "cutoffs": contract["cutoffs"],
        "selected_match_scope": contract["numeric_matching"][
            "selected_match_scope"
        ],
        "test_labels_used": False,
    }
    if document["configuration"] != expected_configuration:
        raise ApixabanNumericOccurrenceError(
            "Occurrence report configuration differs from contract"
        )
    if {
        field: document["interpretation"][field]
        for field in contract["interpretation"]
    } != contract["interpretation"]:
        raise ApixabanNumericOccurrenceError(
            "Occurrence report interpretation differs from contract"
        )
    population = document["population"]
    if population["split_assessment_count"] != (
        population["boolean_excluded_count"]
        + population["numeric_assessment_count"]
    ):
        raise ApixabanNumericOccurrenceError("Occurrence type counts do not reconcile")
    if population["numeric_assessment_count"] != (
        population["numeric_unknown_excluded_count"]
        + population["protocol_value_excluded_count"]
        + population["full_context_absent_excluded_count"]
        + population["evaluable_count"]
    ):
        raise ApixabanNumericOccurrenceError(
            "Occurrence numeric population does not reconcile"
        )
    expected_fraction = (
        population["evaluable_count"] / population["numeric_assessment_count"]
    )
    if not math.isclose(
        population["evaluable_fraction_of_numeric"], expected_fraction
    ):
        raise ApixabanNumericOccurrenceError("Occurrence population rate differs")
    for metrics in document["retrievers"].values():
        if metrics["evaluable_count"] != population["evaluable_count"]:
            raise ApixabanNumericOccurrenceError("Retriever denominator differs")
        if not (
            0
            <= metrics["occurrence_at_1_count"]
            <= metrics["occurrence_at_3_count"]
            <= metrics["evaluable_count"]
        ):
            raise ApixabanNumericOccurrenceError("Occurrence counts are impossible")
        for cutoff in (1, 3):
            expected_rate = (
                metrics[f"occurrence_at_{cutoff}_count"]
                / metrics["evaluable_count"]
            )
            if not math.isclose(
                metrics[f"occurrence_at_{cutoff}_rate"], expected_rate
            ):
                raise ApixabanNumericOccurrenceError(
                    "Occurrence rate does not match count"
                )


def _validate_component_runs(
    runs: Mapping[str, Mapping[str, Any]],
    benchmark_sha256: str,
    split_manifest_sha256: str,
) -> None:
    if set(runs) != set(RETRIEVER_NAMES):
        raise ApixabanNumericOccurrenceError("Diagnostic component set differs")
    validate_bm25_run(runs["bm25"])
    validate_dense_run(runs["medcpt_dense"])
    validate_rrf_run(runs["rrf60"])
    expected = {
        "benchmark_sha256": benchmark_sha256,
        "split_manifest_sha256": split_manifest_sha256,
        "split_name": "validation",
    }
    for name, run in runs.items():
        for field, value in expected.items():
            if run["provenance"][field] != value:
                raise ApixabanNumericOccurrenceError(
                    f"{name} run differs from diagnostic input: {field}"
                )
    rrf_provenance = runs["rrf60"]["provenance"]
    if rrf_provenance["bm25_run_sha256"] != runs["bm25"]["run_sha256"]:
        raise ApixabanNumericOccurrenceError("RRF does not cite the BM25 run")
    if rrf_provenance["dense_run_sha256"] != runs["medcpt_dense"][
        "run_sha256"
    ]:
        raise ApixabanNumericOccurrenceError("RRF does not cite the dense run")


def build_numeric_occurrence_report(
    benchmark: Mapping[str, Any],
    split: Mapping[str, Any],
    staging_corpus: Mapping[str, Any],
    runs: Mapping[str, Mapping[str, Any]],
    *,
    benchmark_sha256: str,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    contract = load_numeric_occurrence_contract()
    catalog = load_question_catalog()
    patient_ids = set(split["splits"]["validation"]["patient_ids"])
    records = evidence_index_records(staging_corpus, sorted(patient_ids))
    records_by_id = {record["evidence_id"]: record for record in records}
    full_text_by_patient: Dict[str, str] = {}
    for record in records:
        full_text_by_patient[record["patient_id"]] = (
            full_text_by_patient.get(record["patient_id"], "") + record["text"]
        )
    rankings = {
        name: {
            (item["patient_id"], item["question_id"]): [
                evidence["evidence_id"] for evidence in item["selected_evidence"]
            ]
            for item in run["results"]
        }
        for name, run in runs.items()
    }
    expected_keys = {
        (patient_id, question["question_id"])
        for patient_id in patient_ids
        for question in catalog["questions"]
    }
    if any(set(items) != expected_keys for items in rankings.values()):
        raise ApixabanNumericOccurrenceError("Retriever query grids differ")
    lvef_question_id = next(
        question["question_id"]
        for question in catalog["questions"]
        if question["source_criterion_label"] == "lvef"
    )
    exclusions: Counter[str] = Counter()
    evaluable: list[Tuple[str, str, Any]] = []
    split_assessments = [
        item for item in benchmark["assessments"] if item["patient_id"] in patient_ids
    ]
    for assessment in split_assessments:
        if assessment["question_type"] != "numeric":
            exclusions["boolean"] += 1
            continue
        if assessment["fact_status"] != "present" or assessment["value"] is None:
            exclusions["numeric_unknown"] += 1
            continue
        if (
            assessment["question_id"] == lvef_question_id
            and _decimal(assessment["value"]) == Decimal("55")
        ):
            exclusions["protocol_value"] += 1
            continue
        if not contains_exact_numeric_token(
            [full_text_by_patient[assessment["patient_id"]]],
            assessment["value"],
            token_pattern=contract["numeric_matching"]["token_pattern"],
        ):
            exclusions["full_context_absent"] += 1
            continue
        evaluable.append(
            (
                assessment["patient_id"],
                assessment["question_id"],
                assessment["value"],
            )
        )
    if not evaluable:
        raise ApixabanNumericOccurrenceError("No evaluable numeric occurrences")
    retriever_metrics: Dict[str, Dict[str, Any]] = {}
    for name in RETRIEVER_NAMES:
        counts = {1: 0, 3: 0}
        for patient_id, question_id, value in evaluable:
            selected_ids = rankings[name][(patient_id, question_id)]
            if any(evidence_id not in records_by_id for evidence_id in selected_ids):
                raise ApixabanNumericOccurrenceError(
                    f"{name} selected evidence outside validation records"
                )
            for cutoff in (1, 3):
                texts = [
                    records_by_id[evidence_id]["text"]
                    for evidence_id in selected_ids[:cutoff]
                ]
                counts[cutoff] += contains_exact_numeric_token(
                    texts,
                    value,
                    token_pattern=contract["numeric_matching"]["token_pattern"],
                )
        denominator = len(evaluable)
        retriever_metrics[name] = {
            "evaluable_count": denominator,
            "occurrence_at_1_count": counts[1],
            "occurrence_at_1_rate": counts[1] / denominator,
            "occurrence_at_3_count": counts[3],
            "occurrence_at_3_rate": counts[3] / denominator,
        }
    numeric_count = sum(
        item["question_type"] == "numeric" for item in split_assessments
    )
    runtime_commit = code_commit or current_git_commit()
    report: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "report_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": runtime_commit,
        "provenance": {
            "source_csv_sha256": benchmark["source"]["source_csv_sha256"],
            "benchmark_sha256": benchmark_sha256,
            "staging_corpus_sha256": benchmark["source"][
                "staging_corpus_sha256"
            ],
            "split_manifest_sha256": split["manifest_sha256"],
            "split_name": "validation",
            "question_catalog_sha256": catalog["catalog_sha256"],
            "contract_sha256": canonical_sha256(contract),
            "component_run_sha256": {
                name: runs[name]["run_sha256"] for name in RETRIEVER_NAMES
            },
        },
        "configuration": {
            "contract_id": contract["contract_id"],
            "matching_method": contract["numeric_matching"]["method"],
            "token_pattern": contract["numeric_matching"]["token_pattern"],
            "comparison": contract["numeric_matching"]["comparison"],
            "cutoffs": contract["cutoffs"],
            "selected_match_scope": contract["numeric_matching"][
                "selected_match_scope"
            ],
            "test_labels_used": False,
        },
        "population": {
            "split_assessment_count": len(split_assessments),
            "boolean_excluded_count": exclusions["boolean"],
            "numeric_assessment_count": numeric_count,
            "numeric_unknown_excluded_count": exclusions["numeric_unknown"],
            "protocol_value_excluded_count": exclusions["protocol_value"],
            "full_context_absent_excluded_count": exclusions[
                "full_context_absent"
            ],
            "evaluable_count": len(evaluable),
            "evaluable_fraction_of_numeric": len(evaluable) / numeric_count,
            "reconciliation_passed": True,
        },
        "retrievers": retriever_metrics,
        "interpretation": {
            **contract["interpretation"],
            "limitations": [
                "Exact numeric equality can match an unrelated number in the same chunk.",
                "The diagnostic excludes values absent from full context and "
                "cannot measure recall over all numeric answers.",
                "Boolean, unknown, and ambiguous protocol-transformed values "
                "are excluded.",
                "No human-authored evidence relevance or completeness judgment "
                "is available.",
            ],
        },
        "disclosure_note": (
            "Restricted aggregate diagnostic derived from MIMIC text and labels. "
            "Keep local unless separately approved for disclosure."
        ),
    }
    report["report_sha256"] = _self_hash(report)
    validate_numeric_occurrence_report(report)
    return report


def evaluate_numeric_occurrence_from_paths(
    benchmark_path: Path,
    split_path: Path,
    staging_corpus_path: Path,
    bm25_run_path: Path,
    dense_run_path: Path,
    rrf_run_path: Path,
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    paths = (
        benchmark_path,
        split_path,
        staging_corpus_path,
        bm25_run_path,
        dense_run_path,
        rrf_run_path,
    )
    for path in paths:
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanNumericOccurrenceError(
                f"Restricted diagnostic input is not owner-only: {path}"
            )
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    validate_apixaban_benchmark(benchmark)
    split = load_apixaban_split_manifest(split_path)
    if split["status"] != "frozen" or not split["freeze"]["test_locked"]:
        raise ApixabanNumericOccurrenceError("Diagnostic requires frozen split")
    benchmark_sha256 = file_sha256(benchmark_path)
    if split["dataset"]["benchmark_sha256"] != benchmark_sha256:
        raise ApixabanNumericOccurrenceError("Benchmark differs from frozen split")
    staging = json.loads(staging_corpus_path.read_text(encoding="utf-8"))
    validate_apixaban_staging_corpus(staging)
    if file_sha256(staging_corpus_path) != benchmark["source"][
        "staging_corpus_sha256"
    ]:
        raise ApixabanNumericOccurrenceError("Staging corpus differs from benchmark")
    runs = {
        "bm25": json.loads(bm25_run_path.read_text(encoding="utf-8")),
        "medcpt_dense": json.loads(dense_run_path.read_text(encoding="utf-8")),
        "rrf60": json.loads(rrf_run_path.read_text(encoding="utf-8")),
    }
    _validate_component_runs(
        runs,
        benchmark_sha256,
        split["manifest_sha256"],
    )
    return build_numeric_occurrence_report(
        benchmark,
        split,
        staging,
        runs,
        benchmark_sha256=benchmark_sha256,
        generated_at=generated_at,
        code_commit=code_commit,
    )


def write_numeric_occurrence_report(
    report: Mapping[str, Any], output_path: Path
) -> Path:
    assert_restricted_local_path(output_path)
    validate_numeric_occurrence_report(report)
    if output_path.exists():
        raise FileExistsError("Refusing to overwrite numeric occurrence report")
    return write_private_json(dict(report), output_path)
