import math
from typing import Iterable, Mapping, Sequence


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
