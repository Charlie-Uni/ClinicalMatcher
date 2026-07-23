import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .evaluation import (
    DecisionRecord,
    RankingRecord,
    RetrievalRecord,
    attribute_decision_error,
    classification_metrics,
    clustered_bootstrap,
    coverage_risk_curve,
    error_attribution_summary,
    mean_ranking_metrics,
    mean_retrieval_metrics,
)
from .fixture import load_fixture
from .models import Decision
from .pipeline import match_patient
from .reporting import (
    REPORT_VERSION,
    curve_document,
    interval_document,
    report_fingerprint,
    validate_report,
)
from .splits import (
    assert_dataset_matches,
    current_git_commit,
    load_split_manifest,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def evaluate_fixture_run(
    fixture_path: Path,
    manifest_path: Path,
    split_name: str,
    k: int = 2,
    bootstrap_samples: int = 1000,
    code_commit: Optional[str] = None,
    generated_at: Optional[str] = None,
    model_ids: Sequence[str] = (
        "deterministic-neuro-symbolic-baseline@1",
    ),
    prompt_versions: Sequence[str] = (
        "not-applicable:deterministic-baseline",
    ),
    index_fingerprint: str = "not-applicable:no-index",
) -> Dict[str, Any]:
    if k <= 0:
        raise ValueError("k must be positive")
    if not model_ids or not prompt_versions or not index_fingerprint:
        raise ValueError(
            "Model IDs, prompt versions, and index fingerprint are required"
        )

    fixture = load_fixture(fixture_path)
    manifest = load_split_manifest(manifest_path)
    assert_dataset_matches(manifest, fixture_path)
    partition = manifest.partition(split_name)

    patient_ids = set(partition.entity_ids.get("patient", ()))
    trial_ids = set(partition.entity_ids.get("trial", ()))
    patients = [
        patient for patient in fixture.patients
        if patient.patient_id in patient_ids
    ]
    trials = [
        trial for trial in fixture.trials if trial.trial_id in trial_ids
    ]
    if {item.patient_id for item in patients} != patient_ids:
        raise ValueError("Split references unknown patient IDs")
    if {item.trial_id for item in trials} != trial_ids:
        raise ValueError("Split references unknown trial IDs")
    expected_criteria = {
        criterion.criterion_id
        for trial in trials
        for criterion in trial.criteria
    }
    if set(partition.entity_ids.get("criterion", ())) != expected_criteria:
        raise ValueError(
            "Split criterion membership does not match selected trials"
        )

    started = time.perf_counter()
    retrieval_records = []
    decision_records = []
    ranking_records = []
    error_cases = []
    pair_count = 0
    for patient in patients:
        matches = match_patient(patient, trials)
        pair_count += len(matches)
        relevance_grades = {
            trial.trial_id: fixture.gold_trials[
                (patient.patient_id, trial.trial_id)
            ].relevance_grade
            for trial in trials
        }
        ranking_records.append(
            RankingRecord(
                query_id=patient.patient_id,
                patient_id=patient.patient_id,
                ranked_ids=tuple(item.trial_id for item in matches),
                relevance_grades=relevance_grades,
            )
        )
        for match in matches:
            for decision in match.criterion_decisions:
                key = (
                    patient.patient_id,
                    match.trial_id,
                    decision.criterion_id,
                )
                gold = fixture.gold_criteria[key]
                retrieval_record = RetrievalRecord(
                    query_id="/".join(key),
                    patient_id=patient.patient_id,
                    retrieved_ids=decision.evidence_ids,
                    relevant_ids=gold.evidence_ids,
                )
                if gold.evidence_ids:
                    retrieval_records.append(retrieval_record)
                decision_record = DecisionRecord(
                    patient_id=patient.patient_id,
                    trial_id=match.trial_id,
                    criterion_id=decision.criterion_id,
                    gold=gold.decision,
                    predicted=decision.decision,
                    gold_evidence_ids=gold.evidence_ids,
                    retrieved_evidence_ids=decision.evidence_ids,
                    selection_score=decision.atomic_coverage,
                    abstained=decision.decision is Decision.UNKNOWN,
                )
                decision_records.append(decision_record)
                attribution = attribute_decision_error(decision_record, k)
                if attribution != "correct":
                    error_cases.append(
                        {
                            "patient_id": patient.patient_id,
                            "trial_id": match.trial_id,
                            "criterion_id": decision.criterion_id,
                            "gold": gold.decision.value,
                            "predicted": decision.decision.value,
                            "attribution": attribution,
                            "gold_evidence_ids": list(gold.evidence_ids),
                            "retrieved_evidence_ids": list(
                                decision.evidence_ids[:k]
                            ),
                        }
                    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    if not decision_records:
        raise ValueError("Selected split produced no criterion decisions")

    retrieval_metrics = (
        mean_retrieval_metrics(retrieval_records, k)
        if retrieval_records
        else {}
    )
    retrieval_metrics.update(
        {
            "evaluable_queries": len(retrieval_records),
            "unevaluable_queries": len(decision_records)
            - len(retrieval_records),
        }
    )
    ranking_evaluable = [
        record
        for record in ranking_records
        if any(grade >= 1 for grade in record.relevance_grades.values())
    ]
    ranking_metrics = (
        mean_ranking_metrics(ranking_evaluable, k)
        if ranking_evaluable
        else {}
    )
    ranking_metrics.update(
        {
            "evaluable_patients": len(ranking_evaluable),
            "unevaluable_patients": len(ranking_records)
            - len(ranking_evaluable),
        }
    )
    decision_metrics = classification_metrics(decision_records)

    bootstrap = {
        "criterion_macro_f1": interval_document(
            clustered_bootstrap(
                decision_records,
                cluster_key=lambda item: item.patient_id,
                statistic=lambda sample: classification_metrics(sample)[
                    "macro_f1"
                ],
                samples=bootstrap_samples,
                seed=manifest.seed,
            )
        )
    }
    if retrieval_records:
        metric_name = f"evidence_recall_at_{k}"
        bootstrap[metric_name] = interval_document(
            clustered_bootstrap(
                retrieval_records,
                cluster_key=lambda item: item.patient_id,
                statistic=lambda sample: mean_retrieval_metrics(sample, k)[
                    metric_name
                ],
                samples=bootstrap_samples,
                seed=manifest.seed,
            )
        )
    if ranking_evaluable:
        metric_name = f"trial_ndcg_at_{k}"
        bootstrap[metric_name] = interval_document(
            clustered_bootstrap(
                ranking_evaluable,
                cluster_key=lambda item: item.patient_id,
                statistic=lambda sample: mean_ranking_metrics(sample, k)[
                    metric_name
                ],
                samples=bootstrap_samples,
                seed=manifest.seed,
            )
        )

    runtime_commit = code_commit or current_git_commit()
    configuration = {
        "k": k,
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_unit": "patient",
        "selection_score": "deterministic_atomic_coverage",
        "evidence_source": "deterministic_fact_evidence_baseline",
        "latency_scope": "matching_and_metric_record_construction",
        "model_ids": list(model_ids),
        "prompt_versions": list(prompt_versions),
        "index_fingerprint": index_fingerprint,
    }
    run_specification = {
        "code_commit": runtime_commit,
        "dataset_sha256": manifest.dataset_sha256,
        "split_manifest_sha256": manifest.manifest_sha256,
        "split_name": split_name,
        "configuration": configuration,
    }
    report: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "run_id": report_fingerprint(run_specification),
        "generated_at": generated_at or _now(),
        "provenance": {
            "code_commit": runtime_commit,
            "dataset_id": manifest.dataset_id,
            "dataset_schema_version": manifest.dataset_schema_version,
            "dataset_sha256": manifest.dataset_sha256,
            "parent_dataset_sha256": list(
                manifest.parent_dataset_sha256
            ),
            "split": {
                "name": split_name,
                "strategy": manifest.strategy,
                "manifest_sha256": manifest.manifest_sha256,
                "seed": manifest.seed,
                "generated_at": manifest.generated_at,
                "code_commit": manifest.code_commit,
                "isolated_dimensions": list(
                    manifest.isolated_dimensions
                ),
            },
        },
        "configuration": configuration,
        "metrics": {
            "retrieval": retrieval_metrics,
            "decision": decision_metrics,
            "ranking": ranking_metrics,
            "coverage_risk": list(
                curve_document(coverage_risk_curve(decision_records))
            ),
            "bootstrap": bootstrap,
            "latency": {
                "total_ms": latency_ms,
                "patient_trial_pairs": pair_count,
                "ms_per_patient_trial": (
                    latency_ms / pair_count if pair_count else 0.0
                ),
            },
        },
        "errors": {
            "counts": error_attribution_summary(decision_records, k),
            "cases": error_cases,
        },
    }
    validate_report(report)
    return report
