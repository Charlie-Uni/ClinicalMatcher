import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

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
    patients, trials, expected = load_fixture(args.fixture)
    output = {}
    for patient in patients:
        matches = match_patient(patient, trials)
        ranking = [item.trial_id for item in matches]
        if ranking != expected[patient.patient_id]:
            raise AssertionError(
                f"Unexpected ranking for {patient.patient_id}: "
                f"{ranking} != {expected[patient.patient_id]}"
            )
        output[patient.patient_id] = [
            {
                "trial_id": item.trial_id,
                "decision": item.decision.value,
                "score": item.score,
            }
            for item in matches
        ]
    print(json.dumps(output, indent=2, sort_keys=True))
    print("Synthetic smoke test passed.")
    return 0
