import unittest
from collections import Counter

from clinical_matcher.evaluation import (
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
from clinical_matcher.models import Decision


def decision_record(
    patient_id: str,
    criterion_id: str,
    gold: Decision,
    predicted: Decision,
    gold_evidence_ids=("gold-evidence",),
    retrieved_evidence_ids=("gold-evidence",),
    selection_score: float = 1.0,
    abstained: bool = False,
) -> DecisionRecord:
    return DecisionRecord(
        patient_id=patient_id,
        trial_id="trial",
        criterion_id=criterion_id,
        gold=gold,
        predicted=predicted,
        gold_evidence_ids=tuple(gold_evidence_ids),
        retrieved_evidence_ids=tuple(retrieved_evidence_ids),
        selection_score=selection_score,
        abstained=abstained,
    )


class EvaluationTest(unittest.TestCase):
    def test_retrieval_and_ranking_metrics_are_separate(self) -> None:
        retrieval = mean_retrieval_metrics(
            [
                RetrievalRecord(
                    query_id="criterion-a",
                    retrieved_ids=("noise", "evidence-a"),
                    relevant_ids=("evidence-a",),
                ),
                RetrievalRecord(
                    query_id="criterion-b",
                    retrieved_ids=("evidence-b", "noise"),
                    relevant_ids=("evidence-b",),
                ),
            ],
            k=2,
        )
        self.assertEqual(1.0, retrieval["evidence_recall_at_2"])
        self.assertEqual(0.75, retrieval["evidence_mrr"])

        ranking = mean_ranking_metrics(
            [
                RankingRecord(
                    query_id="patient",
                    ranked_ids=("trial-best", "trial-weak"),
                    relevance_grades={"trial-best": 3, "trial-weak": 1},
                )
            ],
            k=2,
        )
        self.assertEqual(1.0, ranking["trial_ndcg_at_2"])
        self.assertEqual(1.0, ranking["trial_mrr"])
        self.assertEqual(1.0, ranking["trial_recall_at_2"])

    def test_three_class_decision_metrics_include_confusion_matrix(self) -> None:
        records = [
            decision_record(
                "patient-a",
                "criterion-a",
                Decision.ELIGIBLE,
                Decision.ELIGIBLE,
            ),
            decision_record(
                "patient-a",
                "criterion-b",
                Decision.INELIGIBLE,
                Decision.UNKNOWN,
            ),
            decision_record(
                "patient-b",
                "criterion-c",
                Decision.UNKNOWN,
                Decision.UNKNOWN,
            ),
        ]
        metrics = classification_metrics(records)
        self.assertAlmostEqual(2 / 3, metrics["accuracy"])
        self.assertAlmostEqual(2 / 3, metrics["micro_f1"])
        self.assertAlmostEqual(5 / 9, metrics["macro_f1"])
        self.assertEqual(
            1,
            metrics["confusion_matrix"]["ineligible"]["unknown"],
        )

    def test_error_attribution_distinguishes_retrieval_and_reasoning(self) -> None:
        missing = decision_record(
            "patient-a",
            "missing",
            Decision.ELIGIBLE,
            Decision.INELIGIBLE,
            retrieved_evidence_ids=("noise",),
        )
        reasoning = decision_record(
            "patient-a",
            "reasoning",
            Decision.ELIGIBLE,
            Decision.INELIGIBLE,
        )
        abstention = decision_record(
            "patient-b",
            "abstention",
            Decision.ELIGIBLE,
            Decision.UNKNOWN,
            abstained=True,
        )
        self.assertEqual(
            "evidence_retrieval_failure",
            attribute_decision_error(missing, k=1),
        )
        self.assertEqual(
            "decision_error_with_evidence",
            attribute_decision_error(reasoning, k=1),
        )
        summary = error_attribution_summary(
            [missing, reasoning, abstention],
            k=1,
        )
        self.assertEqual(1, summary["evidence_retrieval_failure"])
        self.assertEqual(1, summary["decision_error_with_evidence"])
        self.assertEqual(1, summary["false_abstention_with_evidence"])

    def test_coverage_risk_curve_keeps_abstention_separate(self) -> None:
        records = [
            decision_record(
                "patient-a",
                "high",
                Decision.ELIGIBLE,
                Decision.ELIGIBLE,
                selection_score=0.9,
            ),
            decision_record(
                "patient-a",
                "medium",
                Decision.ELIGIBLE,
                Decision.INELIGIBLE,
                selection_score=0.6,
            ),
            decision_record(
                "patient-b",
                "abstained",
                Decision.ELIGIBLE,
                Decision.UNKNOWN,
                selection_score=0.2,
                abstained=True,
            ),
        ]
        points = coverage_risk_curve(records)
        self.assertIsNone(points[0].threshold)
        self.assertEqual(0.0, points[0].coverage)
        self.assertEqual(1 / 3, points[1].coverage)
        self.assertEqual(0.0, points[1].risk)
        self.assertEqual(2 / 3, points[2].coverage)
        self.assertEqual(0.5, points[2].risk)
        self.assertEqual(2 / 3, points[3].coverage)

    def test_bootstrap_resamples_whole_patient_clusters(self) -> None:
        records = [
            ("patient-a", "criterion-1", 1.0),
            ("patient-a", "criterion-2", 1.0),
            ("patient-b", "criterion-1", 0.0),
            ("patient-b", "criterion-2", 0.0),
        ]

        def statistic(sample):
            by_patient = {}
            for patient_id, criterion_id, _ in sample:
                by_patient.setdefault(patient_id, Counter())[criterion_id] += 1
            for counts in by_patient.values():
                self.assertEqual(
                    counts["criterion-1"],
                    counts["criterion-2"],
                )
            return sum(item[2] for item in sample) / len(sample)

        interval = clustered_bootstrap(
            records,
            cluster_key=lambda item: item[0],
            statistic=statistic,
            samples=200,
            seed=17,
        )
        self.assertEqual(2, interval.cluster_count)
        self.assertEqual(0.5, interval.estimate)
        self.assertEqual(0.0, interval.lower)
        self.assertEqual(1.0, interval.upper)


if __name__ == "__main__":
    unittest.main()
