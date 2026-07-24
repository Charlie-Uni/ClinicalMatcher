import copy
import json
import unittest
from pathlib import Path

from clinical_matcher.pilot import (
    PilotValidationError,
    build_adjudication_template,
    build_annotation_template,
    build_pilot_summary,
    finalize_pilot_manifest,
    validate_adjudication,
    validate_annotation,
    validate_pilot_summary,
)


def pilot_manifest():
    return finalize_pilot_manifest(
        {
            "pilot_manifest_version": "1.0.0",
            "pilot_id": "pilot-synthetic-test",
            "manifest_sha256": "0" * 64,
            "annotation_manual_version": "1.0.0",
            "created_at": "2026-07-24T08:00:00Z",
            "code_commit": "a" * 40,
            "source": {
                "patient_source_id": "synthetic-patients-v1",
                "patient_source_sha256": "b" * 64,
                "trial_source_id": "synthetic-trials-v1",
                "trial_source_sha256": "c" * 64,
                "access_policy": "synthetic",
            },
            "blinding": {
                "model_outputs_hidden": True,
                "peer_annotations_hidden": True,
            },
            "annotator_ids": ["annotator-a", "annotator-b"],
            "units": [
                {
                    "unit_id": "pilot-unit-1",
                    "patient_id": "synthetic-patient-a",
                    "trial_id": "synthetic-trial-1",
                    "criterion_ids": ["criterion-1", "criterion-2"],
                    "allowed_evidence_ids": ["evidence-1", "evidence-2"],
                },
                {
                    "unit_id": "pilot-unit-2",
                    "patient_id": "synthetic-patient-b",
                    "trial_id": "synthetic-trial-2",
                    "criterion_ids": ["criterion-3", "criterion-4"],
                    "allowed_evidence_ids": ["evidence-3", "evidence-4"],
                },
            ],
            "disclosure_note": "Synthetic test manifest; no clinical data.",
        }
    )


def annotations(manifest):
    left = build_annotation_template(manifest, "annotator-a")
    right = build_annotation_template(manifest, "annotator-b")
    for annotation, minutes in ((left, (12, 15)), (right, (14, 18))):
        annotation["annotation_status"] = "completed"
        annotation["independence_attestation"] = {
            "peer_annotations_not_viewed": True,
            "model_outputs_not_viewed": True,
        }
        for index, unit in enumerate(annotation["units"]):
            unit["active_minutes"] = minutes[index]
            unit["trial_judgment"] = {
                "decision": "eligible",
                "relevance_grade": 3,
                "rationale": "Synthetic trial judgment.",
            }
            for criterion_index, judgment in enumerate(
                unit["criterion_judgments"]
            ):
                judgment["decision"] = "eligible"
                judgment["evidence_ids"] = [
                    manifest["units"][index]["allowed_evidence_ids"][
                        criterion_index
                    ]
                ]
                judgment["rationale"] = "Synthetic criterion judgment."
    right["units"][0]["trial_judgment"]["relevance_grade"] = 2
    right["units"][0]["criterion_judgments"][1]["decision"] = "unknown"
    right["units"][0]["criterion_judgments"][1]["evidence_ids"] = []
    return left, right


def completed_adjudication(manifest, records):
    adjudication = build_adjudication_template(
        manifest,
        records,
        ["adjudicator-1"],
    )
    adjudication["adjudication_status"] = "completed"
    for index, unit in enumerate(adjudication["units"]):
        unit["trial_judgment"]["rationale"] = "Synthetic final judgment."
        for judgment in unit["criterion_judgments"]:
            judgment["rationale"] = "Synthetic final criterion judgment."
        if index == 0:
            unit["resolution_status"] = "resolved"
            unit["active_person_minutes"] = 10
            unit["trial_judgment"]["relevance_grade"] = 3
            unit["criterion_judgments"][1]["decision"] = "eligible"
            unit["criterion_judgments"][1]["evidence_ids"] = ["evidence-2"]
    return adjudication


class PilotTest(unittest.TestCase):
    def test_committed_synthetic_workflow_is_valid(self) -> None:
        root = Path("fixtures/synthetic/pilot")
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        records = [
            json.loads(
                (root / name).read_text(encoding="utf-8")
            )
            for name in ("annotation-a.json", "annotation-b.json")
        ]
        adjudication = json.loads(
            (root / "adjudication.json").read_text(encoding="utf-8")
        )
        summary = build_pilot_summary(
            manifest,
            records,
            adjudication,
            generated_at="2026-07-24T09:00:00Z",
            code_commit="d" * 40,
        )
        self.assertEqual(15, summary["capacity_inputs"]["minutes_per_annotation"])
        self.assertEqual(
            0.5,
            summary["capacity_inputs"]["expected_adjudication_rate"],
        )

    def test_completed_annotations_require_attested_independence(self) -> None:
        manifest = pilot_manifest()
        left, _ = annotations(manifest)
        left["independence_attestation"]["peer_annotations_not_viewed"] = False
        with self.assertRaisesRegex(PilotValidationError, "attestations"):
            validate_annotation(manifest, left)

    def test_evidence_must_belong_to_patient_trial_unit(self) -> None:
        manifest = pilot_manifest()
        left, _ = annotations(manifest)
        left["units"][0]["criterion_judgments"][0]["evidence_ids"] = [
            "evidence-3"
        ]
        with self.assertRaisesRegex(PilotValidationError, "outside"):
            validate_annotation(manifest, left)

    def test_adjudication_template_exposes_real_disagreements(self) -> None:
        manifest = pilot_manifest()
        records = annotations(manifest)
        adjudication = build_adjudication_template(
            manifest,
            records,
            ["adjudicator-1"],
        )
        self.assertEqual(
            ["criterion_decision", "criterion_evidence", "trial_relevance"],
            adjudication["units"][0]["disagreement_types"],
        )
        self.assertEqual(
            "agreed_without_dispute",
            adjudication["units"][1]["resolution_status"],
        )

    def test_unresolved_dispute_cannot_produce_summary(self) -> None:
        manifest = pilot_manifest()
        records = annotations(manifest)
        adjudication = build_adjudication_template(
            manifest,
            records,
            ["adjudicator-1"],
        )
        adjudication["adjudication_status"] = "completed"
        with self.assertRaisesRegex(PilotValidationError, "unresolved"):
            build_pilot_summary(manifest, records, adjudication)

    def test_summary_is_aggregate_and_capacity_valid(self) -> None:
        manifest = pilot_manifest()
        records = annotations(manifest)
        adjudication = completed_adjudication(manifest, records)
        summary = build_pilot_summary(
            manifest,
            records,
            adjudication,
            generated_at="2026-07-24T09:00:00Z",
            code_commit="d" * 40,
        )
        self.assertEqual(2, summary["counts"]["patient_trial_unit_count"])
        self.assertEqual(14, summary["timing"]["annotation_median_minutes"])
        self.assertEqual(15, summary["timing"]["annotation_p75_minutes"])
        self.assertEqual(
            0.5,
            summary["agreement"]["patient_trial_disagreement_rate"],
        )
        self.assertEqual(
            10,
            summary["capacity_inputs"]["minutes_per_adjudication"],
        )
        serialized = str(summary)
        for forbidden in (
            "synthetic-patient",
            "synthetic-trial",
            "criterion-",
            "evidence-",
            "annotator-a",
        ):
            self.assertNotIn(forbidden, serialized)
        validate_pilot_summary(summary)

    def test_agreed_judgment_cannot_be_changed_by_adjudication(self) -> None:
        manifest = pilot_manifest()
        records = annotations(manifest)
        adjudication = completed_adjudication(manifest, records)
        adjudication["units"][1]["trial_judgment"]["decision"] = "unknown"
        with self.assertRaisesRegex(PilotValidationError, "changed"):
            validate_adjudication(manifest, records, adjudication)

    def test_summary_hash_detects_mutation(self) -> None:
        manifest = pilot_manifest()
        records = annotations(manifest)
        summary = build_pilot_summary(
            manifest,
            records,
            completed_adjudication(manifest, records),
            generated_at="2026-07-24T09:00:00Z",
            code_commit="d" * 40,
        )
        mutated = copy.deepcopy(summary)
        mutated["capacity_inputs"]["minutes_per_annotation"] = 1
        with self.assertRaisesRegex(PilotValidationError, "hash"):
            validate_pilot_summary(mutated)


if __name__ == "__main__":
    unittest.main()
