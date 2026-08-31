import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.ingestion.decomposition_source_pool import (
    load_decomposition_source_pool_contract,
    select_decomposition_source_trials,
    source_pool_selection_document,
    validate_decomposition_source_pool_contract,
    validate_decomposition_source_selection_audit,
)
from clinical_matcher.ingestion.snapshots import (
    SnapshotError,
    build_decomposition_trial_snapshot,
    validate_trial_snapshot,
)
from clinical_matcher.ingestion.trial_selection import TrialSelectionError


FIXTURE = Path("fixtures/synthetic/clinicaltrials_api_search_response.json")
VERSION = {
    "apiVersion": "2.0.5",
    "dataTimestamp": "2026-08-31T00:00:00",
}
QUERIED_AT = "2026-08-31T10:00:00Z"
COMMIT = "f" * 40


def synthetic_hits():
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))["studies"][0]
    studies = []
    statuses = (
        "ENROLLING_BY_INVITATION",
        "NOT_YET_RECRUITING",
        "RECRUITING",
    )
    for index in range(833):
        study = copy.deepcopy(base)
        protocol = study["protocolSection"]
        protocol["identificationModule"]["nctId"] = f"NCT{10000000 + index:08d}"
        protocol["identificationModule"]["briefTitle"] = (
            f"Synthetic AF source trial {index}"
        )
        protocol["statusModule"]["overallStatus"] = statuses[index % 3]
        protocol["statusModule"]["lastUpdatePostDateStruct"]["date"] = (
            f"2026-08-{(index % 28) + 1:02d}"
        )
        studies.append(study)
    studies[0]["protocolSection"]["designModule"]["studyType"] = "OBSERVATIONAL"
    studies[1]["protocolSection"]["eligibilityModule"].pop(
        "eligibilityCriteria"
    )
    studies[2]["protocolSection"]["statusModule"][
        "studyFirstPostDateStruct"
    ]["date"] = "1999-12-31"
    return studies


def selected_ids(studies):
    return {
        study["protocolSection"]["identificationModule"]["nctId"]
        for study in studies
    }


class DecompositionSourcePoolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_decomposition_source_pool_contract()

    def select(self, studies):
        return select_decomposition_source_trials(
            studies=studies,
            registry_reported_total_count=833,
            pages_fetched=1,
            version_payload=VERSION,
            queried_at=QUERIED_AT,
            contract=self.contract,
        )

    def test_complete_query_is_filtered_and_hash_sampled(self):
        selected, audit = self.select(synthetic_hits())
        self.assertEqual(40, len(selected))
        self.assertEqual(833, len(audit["records"]))
        self.assertEqual(830, audit["flow"]["filter_passed_count"])
        self.assertEqual(3, audit["flow"]["filter_excluded_count"])
        self.assertEqual(40, audit["flow"]["selected_count"])
        self.assertFalse(
            audit["selection"]["sampling"]["registry_order_used"]
        )
        self.assertFalse(audit["selection"]["sampling"]["recency_used"])
        validate_decomposition_source_selection_audit(audit, self.contract)

    def test_contract_tampering_breaks_self_hash(self):
        tampered = copy.deepcopy(self.contract)
        tampered["contract_sha256"] = "0" * 64
        with self.assertRaisesRegex(TrialSelectionError, "contract hash mismatch"):
            validate_decomposition_source_pool_contract(tampered)

    def test_selection_ignores_registry_order_and_recency(self):
        studies = synthetic_hits()
        first, _ = self.select(studies)
        changed = list(reversed(copy.deepcopy(studies)))
        for index, study in enumerate(changed):
            study["protocolSection"]["statusModule"][
                "lastUpdatePostDateStruct"
            ]["date"] = f"2025-12-{(index % 28) + 1:02d}"
        second, _ = self.select(changed)
        self.assertEqual(selected_ids(first), selected_ids(second))

    def test_changed_registry_total_fails_closed(self):
        with self.assertRaisesRegex(TrialSelectionError, "Registry total changed"):
            select_decomposition_source_trials(
                studies=synthetic_hits(),
                registry_reported_total_count=832,
                pages_fetched=1,
                version_payload=VERSION,
                queried_at=QUERIED_AT,
                contract=self.contract,
            )

    def test_snapshot_binds_complete_audit_and_selected_sources(self):
        selected, audit = self.select(synthetic_hits())
        with tempfile.TemporaryDirectory() as directory:
            snapshot_dir = Path(directory) / "snapshot"
            manifest = build_decomposition_trial_snapshot(
                studies=selected,
                version_payload=VERSION,
                registry_reported_total_count=833,
                pages_fetched=1,
                selection=source_pool_selection_document(self.contract),
                selection_audit=audit,
                output_dir=snapshot_dir,
                queried_at=QUERIED_AT,
                builder_code_commit=COMMIT,
            )
            self.assertEqual("1.2.0", manifest["snapshot_version"])
            self.assertEqual(40, len(manifest["records"]))
            self.assertEqual(40, len(manifest["candidate_keys"]))
            verified = validate_trial_snapshot(snapshot_dir)
            self.assertEqual(manifest["snapshot_id"], verified["snapshot_id"])

            audit_path = snapshot_dir / "selection-audit.json"
            tampered = json.loads(audit_path.read_text(encoding="utf-8"))
            tampered["records"][0]["overall_status"] = "COMPLETED"
            audit_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(
                (SnapshotError, TrialSelectionError), "hash mismatch"
            ):
                validate_trial_snapshot(snapshot_dir)


if __name__ == "__main__":
    unittest.main()
