import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .evaluation import ndcg_at_k
from .fixture import load_fixture
from .pipeline import match_patient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the synthetic smoke test.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/synthetic/trial_matching.json"),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    fixture = load_fixture(args.fixture)
    output = {}
    for patient in fixture.patients:
        matches = match_patient(patient, fixture.trials)
        for match in matches:
            trial_gold = fixture.gold_trials[(patient.patient_id, match.trial_id)]
            if match.decision is not trial_gold.decision:
                raise AssertionError(
                    f"Trial decision mismatch for {patient.patient_id}/"
                    f"{match.trial_id}: {match.decision.value} != "
                    f"{trial_gold.decision.value}"
                )
            for decision in match.criterion_decisions:
                criterion_gold = fixture.gold_criteria[
                    (patient.patient_id, match.trial_id, decision.criterion_id)
                ]
                if (
                    decision.decision is not criterion_gold.decision
                    or set(decision.evidence_ids)
                    != set(criterion_gold.evidence_ids)
                ):
                    raise AssertionError(
                        f"Criterion gold mismatch for {patient.patient_id}/"
                        f"{match.trial_id}/{decision.criterion_id}"
                    )

        relevance = {
            trial.trial_id: fixture.gold_trials[
                (patient.patient_id, trial.trial_id)
            ].relevance_grade
            for trial in fixture.trials
        }
        ranked_ids = [item.trial_id for item in matches]
        ndcg = ndcg_at_k(ranked_ids, relevance, k=2)
        if ndcg != 1.0:
            raise AssertionError(
                f"Ranking does not match independent gold for "
                f"{patient.patient_id}: nDCG@2={ndcg}"
            )
        output[patient.patient_id] = {
            "ndcg_at_2": round(ndcg, 6),
            "matches": [
                {
                    "trial_id": item.trial_id,
                    "decision": item.decision.value,
                    "eligibility_score": item.eligibility_score,
                    "coverage": item.coverage,
                    "atomic_coverage": item.atomic_coverage,
                    "abstained": item.abstained,
                    "data_quality_issues": list(item.data_quality_issues),
                }
                for item in matches
            ],
        }
    print(json.dumps(output, indent=2, sort_keys=True))
    print("Synthetic smoke test passed.")
    return 0
