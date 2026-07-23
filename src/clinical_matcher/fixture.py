import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .models import Criterion, CriterionType, Evidence, Patient, Trial


def load_fixture(path: Path) -> Tuple[List[Patient], List[Trial], Dict[str, List[str]]]:
    raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    patients = [
        Patient(
            patient_id=item["patient_id"],
            facts=item["facts"],
            evidence=tuple(Evidence(**evidence) for evidence in item["evidence"]),
        )
        for item in raw["patients"]
    ]
    trials = [
        Trial(
            trial_id=item["trial_id"],
            title=item["title"],
            criteria=tuple(
                Criterion(
                    criterion_id=criterion["criterion_id"],
                    criterion_type=CriterionType(criterion["criterion_type"]),
                    field=criterion["field"],
                    operator=criterion["operator"],
                    value=criterion["value"],
                    description=criterion["description"],
                    hard=criterion.get("hard", False),
                )
                for criterion in item["criteria"]
            ),
        )
        for item in raw["trials"]
    ]
    return patients, trials, raw["expected_rankings"]
