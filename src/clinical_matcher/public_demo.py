"""Offline, synthetic-only public demonstration for ClinicalMatcher.

The demo is intentionally a presentation layer over the frozen deterministic
retrieval and verification components.  It does not call a model, access the
network, or estimate clinical performance.
"""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .fixture import SyntheticFixture, load_fixture
from .models import (
    Criterion,
    CriterionDecision,
    Fact,
    Patient,
    Trial,
    TrialMatch,
    TypedValue,
)
from .pipeline import match_patient
from .retrieval.bm25 import BM25PatientRetriever
from .validation import validate_document


DEMO_VERSION = "1.0.0"
DEMO_SCHEMA = "schemas/public-demo-report-1.0.0.schema.json"
EXPECTED_FIXTURE_NOTICE = (
    "Independently authored synthetic records and gold judgments; not derived "
    "from MIMIC or any real patient."
)
RESEARCH_WARNING = (
    "SYNTHETIC RESEARCH DEMO ONLY. Not medical advice, a medical device, or "
    "an autonomous enrollment or exclusion system."
)


class PublicDemoError(ValueError):
    """Raised when an input cannot safely drive the public demo."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _typed_value(value: TypedValue) -> Dict[str, Any]:
    rendered = (
        value.value.isoformat()
        if hasattr(value.value, "isoformat")
        else value.value
    )
    return {
        "value_type": value.value_type.value,
        "value": rendered,
        "unit": value.unit,
    }


def _evidence_records(fixture: SyntheticFixture) -> Tuple[Dict[str, Any], ...]:
    records = []
    for patient in fixture.patients:
        for source_order, evidence in enumerate(patient.evidence):
            records.append(
                {
                    "patient_id": patient.patient_id,
                    "evidence_id": evidence.evidence_id,
                    "text": evidence.text,
                    # The BM25 implementation consumes source order only as a
                    # deterministic tie-break.  This is not claimed as a real
                    # source-document offset.
                    "source_span": {"start": source_order, "end": source_order + 1},
                }
            )
    return tuple(records)


def _fact_trace(patient: Patient) -> Sequence[Dict[str, Any]]:
    return [
        {
            "fact_id": fact.fact_id,
            "field": fact.field,
            "typed_value": _typed_value(fact.value),
            "observed_at": fact.observed_at.isoformat() if fact.observed_at else None,
            "evidence_ids": list(fact.evidence_ids),
        }
        for fact in patient.facts
    ]


def _criterion_trace(
    *,
    patient: Patient,
    criterion: Criterion,
    decision: CriterionDecision,
    retriever: BM25PatientRetriever,
    evidence_by_id: Mapping[str, Any],
) -> Dict[str, Any]:
    retrieved = retriever.retrieve(
        patient.patient_id,
        criterion.source.source_text,
        k=2,
    )
    return {
        "criterion_id": criterion.criterion_id,
        "criterion_type": criterion.criterion_type.value,
        "hard": criterion.hard,
        "source_text": criterion.source.source_text,
        "retrieval": {
            "method": "patient-local-bm25",
            "query": criterion.source.source_text,
            "top_k": [
                {
                    "rank": item.rank,
                    "evidence_id": item.evidence_id,
                    "score": round(item.score, 6),
                    "text": evidence_by_id[item.evidence_id].text,
                    "linked_to_deterministic_decision": (
                        item.evidence_id in decision.evidence_ids
                    ),
                }
                for item in retrieved
            ],
        },
        "decision": decision.decision.value,
        "reason": decision.reason,
        "evidence_ids": list(decision.evidence_ids),
        "atomic_coverage": decision.atomic_coverage,
        "issues": list(decision.issues),
        "atomic_trace": [
            {
                "condition_id": atomic.condition_id,
                "truth_value": atomic.truth_value.value,
                "negated": atomic.negated,
                "evidence_ids": list(atomic.evidence_ids),
                "reason": atomic.reason,
                "issues": list(atomic.issues),
            }
            for atomic in decision.atomic_decisions
        ],
    }


def _find_demo_inputs(
    fixture: SyntheticFixture,
) -> Tuple[Patient, Trial, Criterion, Fact]:
    for trial in fixture.trials:
        for criterion in trial.criteria:
            atoms = []
            stack = [criterion.expression]
            while stack:
                expression = stack.pop()
                if expression.atom is not None:
                    atoms.append(expression.atom)
                else:
                    stack.extend(reversed(expression.children))
            numeric_atoms = [
                atom for atom in atoms if atom.expected.unit is not None
            ]
            if not numeric_atoms:
                continue
            field = numeric_atoms[0].field
            for patient in fixture.patients:
                fact = next(
                    (
                        item
                        for item in patient.facts
                        if item.field == field and item.value.unit is not None
                    ),
                    None,
                )
                if fact is not None:
                    return patient, trial, criterion, fact
    raise PublicDemoError(
        "The declared synthetic fixture has no numeric criterion/fact pair "
        "for safety probes"
    )


def _single_criterion_match(
    patient: Patient, trial: Trial, criterion: Criterion
) -> TrialMatch:
    probe_trial = Trial(
        trial_id=f"{trial.trial_id}-probe",
        title=f"{trial.title} (derived synthetic safety probe)",
        criteria=(criterion,),
    )
    return match_patient(patient, (probe_trial,))[0]


def _safety_probes(fixture: SyntheticFixture) -> Sequence[Dict[str, Any]]:
    patient, trial, criterion, numeric_fact = _find_demo_inputs(fixture)

    missing_patient = replace(
        patient,
        patient_id=f"{patient.patient_id}-missing-fact-probe",
        facts=tuple(
            fact for fact in patient.facts if fact.field != numeric_fact.field
        ),
    )
    missing_match = _single_criterion_match(missing_patient, trial, criterion)

    incompatible_fact = replace(
        numeric_fact,
        fact_id=f"{numeric_fact.fact_id}-unit-conflict-probe",
        value=TypedValue(
            value_type=numeric_fact.value.value_type,
            value=numeric_fact.value.value,
            unit="synthetic-incompatible-unit",
        ),
    )
    conflict_patient = replace(
        patient,
        patient_id=f"{patient.patient_id}-unit-conflict-probe",
        facts=tuple(
            incompatible_fact if fact.fact_id == numeric_fact.fact_id else fact
            for fact in patient.facts
        ),
    )
    conflict_match = _single_criterion_match(conflict_patient, trial, criterion)

    return [
        {
            "probe_id": "missing_required_fact",
            "classification": "derived_synthetic_safety_probe",
            "purpose": (
                "Show fail-closed abstention when a required fact is unavailable."
            ),
            "criterion_id": criterion.criterion_id,
            "decision": missing_match.decision.value,
            "abstained": missing_match.abstained,
            "abstention_reasons": list(missing_match.abstention_reasons),
            "verifier_issues": list(missing_match.data_quality_issues),
        },
        {
            "probe_id": "typed_unit_conflict",
            "classification": "derived_synthetic_safety_probe",
            "purpose": (
                "Show fail-closed abstention when observed and expected units conflict."
            ),
            "criterion_id": criterion.criterion_id,
            "decision": conflict_match.decision.value,
            "abstained": conflict_match.abstained,
            "abstention_reasons": list(conflict_match.abstention_reasons),
            "verifier_issues": list(conflict_match.data_quality_issues),
        },
    ]


def build_public_demo(
    fixture: SyntheticFixture, *, fixture_file_sha256: str
) -> Dict[str, Any]:
    """Build one deterministic, model-free synthetic demonstration report."""

    retriever = BM25PatientRetriever(_evidence_records(fixture))
    patients = []
    for patient in fixture.patients:
        evidence_by_id = {item.evidence_id: item for item in patient.evidence}
        matches = match_patient(patient, fixture.trials)
        trial_by_id = {trial.trial_id: trial for trial in fixture.trials}
        patient_matches = []
        for rank, match in enumerate(matches, start=1):
            trial = trial_by_id[match.trial_id]
            criteria_by_id = {
                criterion.criterion_id: criterion for criterion in trial.criteria
            }
            patient_matches.append(
                {
                    "rank": rank,
                    "trial_id": match.trial_id,
                    "title": trial.title,
                    "decision": match.decision.value,
                    "eligibility_score": match.eligibility_score,
                    "coverage": match.coverage,
                    "atomic_coverage": match.atomic_coverage,
                    "abstained": match.abstained,
                    "abstention_reasons": list(match.abstention_reasons),
                    "data_quality_issues": list(match.data_quality_issues),
                    "criteria": [
                        _criterion_trace(
                            patient=patient,
                            criterion=criteria_by_id[decision.criterion_id],
                            decision=decision,
                            retriever=retriever,
                            evidence_by_id=evidence_by_id,
                        )
                        for decision in match.criterion_decisions
                    ],
                }
            )
        patients.append(
            {
                "patient_id": patient.patient_id,
                "index_date": patient.index_date.isoformat(),
                "typed_facts": _fact_trace(patient),
                "ranked_trials": patient_matches,
            }
        )

    report = {
        "demo_version": DEMO_VERSION,
        "warning": RESEARCH_WARNING,
        "fixture": {
            "declaration": EXPECTED_FIXTURE_NOTICE,
            "fixture_file_sha256": fixture_file_sha256,
            "patient_count": len(fixture.patients),
            "trial_count": len(fixture.trials),
        },
        "runtime": {
            "network_required": False,
            "model_required": False,
            "cpu_only": True,
            "retrieval": "deterministic patient-local BM25",
            "reasoning": "typed deterministic verifier with Kleene three-valued logic",
        },
        "claim_boundary": [
            (
                "All people, evidence, trials, and judgments in this "
                "demonstration are fictional."
            ),
            (
                "The report demonstrates software behavior, not clinical "
                "accuracy or trial suitability."
            ),
            (
                "Typed facts are fixture inputs; the displayed BM25 ranking "
                "does not generate those facts."
            ),
            (
                "BM25 ranks candidate text; retrieved text is not independent "
                "evidence-relevance gold."
            ),
        ],
        "patients": patients,
        "safety_probes": _safety_probes(fixture),
    }
    validate_public_demo_report(report)
    return report


def validate_public_demo_report(report: Dict[str, Any]) -> None:
    validate_document(report, DEMO_SCHEMA)
    probe_ids = [probe["probe_id"] for probe in report["safety_probes"]]
    if probe_ids != ["missing_required_fact", "typed_unit_conflict"]:
        raise PublicDemoError("The public safety-probe set or order changed")
    if not all(probe["decision"] == "unknown" for probe in report["safety_probes"]):
        raise PublicDemoError("Every public safety probe must fail closed as unknown")
    if not all(probe["abstained"] for probe in report["safety_probes"]):
        raise PublicDemoError("Every public safety probe must record abstention")


def load_and_build_public_demo(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PublicDemoError("The fixture could not be read as JSON") from exc
    if raw.get("fixture_notice") != EXPECTED_FIXTURE_NOTICE:
        raise PublicDemoError(
            "The public demo accepts only the declared synthetic fixture contract"
        )
    try:
        fixture = load_fixture(path)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PublicDemoError("The synthetic fixture failed validation") from exc
    try:
        return build_public_demo(fixture, fixture_file_sha256=_file_sha256(path))
    except PublicDemoError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PublicDemoError(
            "The validated synthetic fixture could not drive the demo"
        ) from exc


def render_public_demo_markdown(report: Mapping[str, Any]) -> str:
    """Render the validated report without changing any decision semantics."""

    validate_public_demo_report(dict(report))
    lines = [
        "# ClinicalMatcher synthetic public demo",
        "",
        f"> {report['warning']}",
        "",
        "## Runtime boundary",
        "",
        "- Offline: yes",
        "- CPU only: yes",
        "- LLM required: no",
        f"- Retrieval: {report['runtime']['retrieval']}",
        f"- Reasoning: {report['runtime']['reasoning']}",
        "",
    ]
    for patient in report["patients"]:
        lines.extend(
            [
                f"## {patient['patient_id']}",
                "",
                "Typed facts:",
                "",
            ]
        )
        for fact in patient["typed_facts"]:
            value = fact["typed_value"]
            unit = f" {value['unit']}" if value["unit"] else ""
            lines.append(
                f"- `{fact['field']}` = `{value['value']}{unit}` "
                f"({value['value_type']}), evidence `{', '.join(fact['evidence_ids'])}`"
            )
        lines.extend(["", "Ranked fictional trials:", ""])
        for match in patient["ranked_trials"]:
            lines.append(
                f"{match['rank']}. **{match['title']}** — `{match['decision']}`, "
                f"score `{match['eligibility_score']}`, coverage `{match['coverage']}`"
            )
            for criterion in match["criteria"]:
                hits = criterion["retrieval"]["top_k"]
                hit_ids = ", ".join(item["evidence_id"] for item in hits) or "none"
                lines.append(
                    f"   - `{criterion['criterion_id']}` → "
                    f"`{criterion['decision']}`; "
                    f"BM25 hits: `{hit_ids}`; evidence used: "
                    f"`{', '.join(criterion['evidence_ids']) or 'none'}`"
                )
        lines.append("")

    lines.extend(["## Fail-closed safety probes", ""])
    for probe in report["safety_probes"]:
        issues = (
            "; ".join(probe["verifier_issues"])
            or "no typed issue; fact unavailable"
        )
        reasons = "; ".join(probe["abstention_reasons"])
        lines.append(
            f"- **{probe['probe_id']}** → `{probe['decision']}`; "
            f"reason: {reasons}; verifier trace: {issues}"
        )
    lines.extend(
        [
            "",
            "## Claim boundary",
            "",
            *[f"- {item}" for item in report["claim_boundary"]],
            "",
        ]
    )
    return "\n".join(lines)
