import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Hashable,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from .models import Decision


T = TypeVar("T")


def evidence_recall_at_k(
    retrieved_ids: Sequence[str], relevant_ids: Iterable[str], k: int
) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    relevant = set(relevant_ids)
    if not relevant:
        raise ValueError("relevant_ids must not be empty")
    return len(set(retrieved_ids[:k]) & relevant) / len(relevant)


def reciprocal_rank(
    retrieved_ids: Sequence[str], relevant_ids: Iterable[str]
) -> float:
    relevant = set(relevant_ids)
    if not relevant:
        raise ValueError("relevant_ids must not be empty")
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked_trial_ids: Sequence[str],
    relevance_grades: Mapping[str, int],
    k: int,
) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    if any(grade < 0 for grade in relevance_grades.values()):
        raise ValueError("relevance grades must be non-negative")

    def dcg(grades: Sequence[int]) -> float:
        return sum(
            (2**grade - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(grades, start=1)
        )

    actual = [relevance_grades.get(trial_id, 0) for trial_id in ranked_trial_ids[:k]]
    ideal = sorted(relevance_grades.values(), reverse=True)[:k]
    ideal_dcg = dcg(ideal)
    return dcg(actual) / ideal_dcg if ideal_dcg else 0.0


def ranking_recall_at_k(
    ranked_ids: Sequence[str],
    relevance_grades: Mapping[str, int],
    k: int,
    minimum_relevance: int = 1,
) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    relevant = {
        item_id
        for item_id, grade in relevance_grades.items()
        if grade >= minimum_relevance
    }
    if not relevant:
        raise ValueError("At least one item must meet minimum_relevance")
    return len(set(ranked_ids[:k]) & relevant) / len(relevant)


@dataclass(frozen=True)
class RetrievalRecord:
    query_id: str
    retrieved_ids: Tuple[str, ...]
    relevant_ids: Tuple[str, ...]
    patient_id: str = ""


@dataclass(frozen=True)
class RankingRecord:
    query_id: str
    ranked_ids: Tuple[str, ...]
    relevance_grades: Mapping[str, int]
    patient_id: str = ""


@dataclass(frozen=True)
class DecisionRecord:
    patient_id: str
    trial_id: str
    criterion_id: str
    gold: Decision
    predicted: Decision
    gold_evidence_ids: Tuple[str, ...]
    retrieved_evidence_ids: Tuple[str, ...]
    selection_score: float
    abstained: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.selection_score <= 1.0:
            raise ValueError("selection_score must be between 0 and 1")


@dataclass(frozen=True)
class CoverageRiskPoint:
    threshold: Optional[float]
    coverage: float
    risk: Optional[float]
    answered: int
    total: int


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float
    confidence: float
    samples: int
    cluster_count: int


def mean_retrieval_metrics(
    records: Sequence[RetrievalRecord], k: int
) -> Dict[str, float]:
    if not records:
        raise ValueError("Retrieval records must not be empty")
    recalls = [
        evidence_recall_at_k(record.retrieved_ids, record.relevant_ids, k)
        for record in records
    ]
    reciprocal_ranks = [
        reciprocal_rank(record.retrieved_ids, record.relevant_ids)
        for record in records
    ]
    ndcgs = [
        ndcg_at_k(
            record.retrieved_ids,
            {item_id: 1 for item_id in record.relevant_ids},
            k,
        )
        for record in records
    ]
    return {
        f"evidence_recall_at_{k}": sum(recalls) / len(recalls),
        "evidence_mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
        f"evidence_ndcg_at_{k}": sum(ndcgs) / len(ndcgs),
    }


def mean_ranking_metrics(
    records: Sequence[RankingRecord],
    k: int,
    minimum_relevance: int = 1,
) -> Dict[str, float]:
    if not records:
        raise ValueError("Ranking records must not be empty")
    ndcgs = [
        ndcg_at_k(record.ranked_ids, record.relevance_grades, k)
        for record in records
    ]
    reciprocal_ranks = [
        reciprocal_rank(
            record.ranked_ids,
            {
                item_id
                for item_id, grade in record.relevance_grades.items()
                if grade >= minimum_relevance
            },
        )
        for record in records
    ]
    recalls = [
        ranking_recall_at_k(
            record.ranked_ids,
            record.relevance_grades,
            k,
            minimum_relevance,
        )
        for record in records
    ]
    return {
        f"trial_ndcg_at_{k}": sum(ndcgs) / len(ndcgs),
        "trial_mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
        f"trial_recall_at_{k}": sum(recalls) / len(recalls),
    }


def confusion_matrix(
    records: Sequence[DecisionRecord],
) -> Dict[str, Dict[str, int]]:
    labels = tuple(Decision)
    matrix = {
        gold.value: {predicted.value: 0 for predicted in labels}
        for gold in labels
    }
    for record in records:
        matrix[record.gold.value][record.predicted.value] += 1
    return matrix


def classification_metrics(
    records: Sequence[DecisionRecord],
) -> Dict[str, Any]:
    if not records:
        raise ValueError("Decision records must not be empty")
    matrix = confusion_matrix(records)
    per_class: Dict[str, Dict[str, float]] = {}
    total_true_positive = 0
    total_false_positive = 0
    total_false_negative = 0
    for label in Decision:
        name = label.value
        true_positive = matrix[name][name]
        false_positive = sum(
            matrix[gold.value][name]
            for gold in Decision
            if gold is not label
        )
        false_negative = sum(
            matrix[name][predicted.value]
            for predicted in Decision
            if predicted is not label
        )
        support = sum(matrix[name].values())
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[name] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        }
        total_true_positive += true_positive
        total_false_positive += false_positive
        total_false_negative += false_negative

    micro_precision = total_true_positive / (
        total_true_positive + total_false_positive
    )
    micro_recall = total_true_positive / (
        total_true_positive + total_false_negative
    )
    micro_f1 = (
        2
        * micro_precision
        * micro_recall
        / (micro_precision + micro_recall)
        if micro_precision + micro_recall
        else 0.0
    )
    active_labels = [
        label.value
        for label in Decision
        if per_class[label.value]["support"]
        or sum(matrix[gold.value][label.value] for gold in Decision)
    ]
    return {
        "count": len(records),
        "accuracy": total_true_positive / len(records),
        "macro_f1": sum(per_class[label]["f1"] for label in active_labels)
        / len(active_labels),
        "macro_f1_all_classes": sum(
            item["f1"] for item in per_class.values()
        )
        / len(per_class),
        "micro_f1": micro_f1,
        "per_class": per_class,
        "confusion_matrix": matrix,
    }


def attribute_decision_error(record: DecisionRecord, k: int) -> str:
    if k <= 0:
        raise ValueError("k must be positive")
    if record.predicted is record.gold:
        return "correct"
    evidence_found = bool(
        set(record.retrieved_evidence_ids[:k])
        & set(record.gold_evidence_ids)
    )
    if record.gold_evidence_ids and not evidence_found:
        return "evidence_retrieval_failure"
    if (
        record.predicted is Decision.UNKNOWN
        and record.gold is not Decision.UNKNOWN
    ):
        return "false_abstention_with_evidence"
    return "decision_error_with_evidence"


def error_attribution_summary(
    records: Sequence[DecisionRecord], k: int
) -> Dict[str, int]:
    counts = Counter(attribute_decision_error(record, k) for record in records)
    categories = (
        "correct",
        "evidence_retrieval_failure",
        "decision_error_with_evidence",
        "false_abstention_with_evidence",
    )
    return {category: counts.get(category, 0) for category in categories}


def coverage_risk_curve(
    records: Sequence[DecisionRecord],
    thresholds: Optional[Sequence[float]] = None,
) -> Tuple[CoverageRiskPoint, ...]:
    if not records:
        raise ValueError("Decision records must not be empty")
    selected_thresholds = (
        tuple(sorted(set(thresholds), reverse=True))
        if thresholds is not None
        else tuple(
            sorted(
                {record.selection_score for record in records},
                reverse=True,
            )
        )
    )
    if any(not 0.0 <= threshold <= 1.0 for threshold in selected_thresholds):
        raise ValueError("Coverage-risk thresholds must be between 0 and 1")

    points: List[CoverageRiskPoint] = [
        CoverageRiskPoint(
            threshold=None,
            coverage=0.0,
            risk=None,
            answered=0,
            total=len(records),
        )
    ]
    for threshold in selected_thresholds:
        answered = [
            record
            for record in records
            if not record.abstained
            and record.selection_score >= threshold
        ]
        errors = sum(
            record.predicted is not record.gold for record in answered
        )
        points.append(
            CoverageRiskPoint(
                threshold=threshold,
                coverage=len(answered) / len(records),
                risk=errors / len(answered) if answered else None,
                answered=len(answered),
                total=len(records),
            )
        )
    return tuple(points)


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return (
        ordered[lower_index] * (1 - fraction)
        + ordered[upper_index] * fraction
    )


def clustered_bootstrap(
    records: Sequence[T],
    cluster_key: Callable[[T], Hashable],
    statistic: Callable[[Sequence[T]], float],
    samples: int = 1000,
    confidence: float = 0.95,
    seed: int = 17,
) -> BootstrapInterval:
    if not records:
        raise ValueError("Bootstrap records must not be empty")
    if samples <= 0:
        raise ValueError("samples must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")

    grouped: Dict[Hashable, List[T]] = {}
    for record in records:
        grouped.setdefault(cluster_key(record), []).append(record)
    clusters = tuple(grouped)
    generator = random.Random(seed)
    estimates = []
    for _ in range(samples):
        sampled_records: List[T] = []
        for cluster in generator.choices(clusters, k=len(clusters)):
            sampled_records.extend(grouped[cluster])
        estimates.append(statistic(sampled_records))

    tail = (1.0 - confidence) / 2.0
    return BootstrapInterval(
        estimate=statistic(records),
        lower=_percentile(estimates, tail),
        upper=_percentile(estimates, 1.0 - tail),
        confidence=confidence,
        samples=samples,
        cluster_count=len(clusters),
    )
