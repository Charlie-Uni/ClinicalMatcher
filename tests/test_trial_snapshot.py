import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.capacity import CapacityAssumptions, build_capacity_plan
from clinical_matcher.ingestion.snapshots import (
    SnapshotError,
    TrialSelection,
    build_benchmark_trial_snapshot,
    build_trial_snapshot,
    load_snapshot_protocols,
    validate_trial_snapshot,
)
from clinical_matcher.ingestion.trial_selection import (
    ReproducibleTrialSelection,
    TrialFilterPolicy,
)
from clinical_matcher.ingestion.trials import ClinicalTrialsClient, TrialImportError


SEARCH_FIXTURE = Path(
    "fixtures/synthetic/clinicaltrials_api_search_response.json"
)
VERSION_FIXTURE = Path(
    "fixtures/synthetic/clinicaltrials_api_version.json"
)
COMMIT = "d" * 40
CREATED_AT = "2026-07-23T15:00:00Z"


def selection() -> TrialSelection:
    return TrialSelection(
        disease_domain="atrial_fibrillation",
        rationale=(
            "Synthetic AF-first benchmark candidate selection; parser remains "
            "disease-independent."
        ),
        query_parameters={
            "query.cond": "Atrial Fibrillation",
            "filter.overallStatus": "RECRUITING|NOT_YET_RECRUITING",
            "format": "json",
            "markupFormat": "markdown",
            "countTotal": "true",
            "pageSize": "100",
            "sort": "LastUpdatePostDate:desc",
        },
    )


class TrialSnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.response = json.loads(SEARCH_FIXTURE.read_text(encoding="utf-8"))
        cls.version = json.loads(VERSION_FIXTURE.read_text(encoding="utf-8"))

    def build(self, root: Path):
        return build_trial_snapshot(
            studies=self.response["studies"],
            version_payload=self.version,
            selection=selection(),
            output_dir=root / "snapshot",
            search_metadata={
                "reported_total_count": 3,
                "pages_fetched": 1,
                "selection_truncated": False,
            },
            created_at=CREATED_AT,
            builder_code_commit=COMMIT,
        )

    def test_freezes_selection_versions_hashes_and_parser_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.build(root)
            verified = validate_trial_snapshot(root / "snapshot")
            self.assertEqual(manifest["snapshot_id"], verified["snapshot_id"])
            self.assertEqual(
                "Atrial Fibrillation",
                manifest["selection"]["query_parameters"]["query.cond"],
            )
            imported = [
                record
                for record in manifest["records"]
                if record["status"] == "imported"
            ]
            self.assertEqual(2, len(imported))
            self.assertEqual(
                {"NCT99999999", "NCT99999998"},
                {record["nct_id"] for record in imported},
            )
            for record in imported:
                self.assertTrue(record["source_record_version"])
                self.assertEqual(64, len(record["eligibility_sha256"]))
                self.assertEqual(record["criterion_count"], len(record["criterion_ids"]))
            coverage = json.loads(
                (root / "snapshot" / "coverage-report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(2, coverage["imported_count"])
            self.assertEqual(1, coverage["skipped_count"])
            self.assertEqual(
                {"ambiguous_polarity": 1},
                coverage["reason_counts"],
            )
            self.assertAlmostEqual(2 / 3, coverage["parse_success_rate"])
            self.assertEqual(2, len(load_snapshot_protocols(root / "snapshot")))

    def test_protocol_tampering_breaks_snapshot_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.build(root)
            record = next(
                item
                for item in manifest["records"]
                if item["status"] == "imported"
            )
            protocol_path = root / "snapshot" / record["protocol_path"]
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            protocol["title"] = "Tampered title"
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            with self.assertRaisesRegex(SnapshotError, "hash mismatch"):
                validate_trial_snapshot(root / "snapshot")

    def test_snapshot_builder_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root)
            with self.assertRaisesRegex(SnapshotError, "already exists"):
                self.build(root)

    def test_duplicate_nct_id_is_rejected(self) -> None:
        duplicate = copy.deepcopy(self.response["studies"][0])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SnapshotError, "Duplicate"):
                build_trial_snapshot(
                    studies=[self.response["studies"][0], duplicate],
                    version_payload=self.version,
                    selection=selection(),
                    output_dir=Path(directory) / "snapshot",
                    created_at=CREATED_AT,
                    builder_code_commit=COMMIT,
                )

    def test_selection_requires_explicit_sort(self) -> None:
        invalid = TrialSelection(
            disease_domain="atrial_fibrillation",
            rationale="Synthetic selection",
            query_parameters={"query.cond": "Atrial Fibrillation"},
        )
        with self.assertRaisesRegex(SnapshotError, "sort"):
            invalid.normalized()

    def test_capacity_bound_snapshot_freezes_full_selection_audit(self) -> None:
        plan = build_capacity_plan(
            assumptions=CapacityAssumptions(
                annotator_count=2,
                hours_per_annotator=10,
                required_annotations_per_unit=2,
                minutes_per_annotation=15,
                expected_adjudication_rate=0.2,
                minutes_per_adjudication=10,
                reserve_fraction=0.2,
                estimate_source="validated_pilot_summary",
                pilot_unit_count=8,
                pilot_summary_sha256="b" * 64,
            ),
            minimum_trials=2,
            maximum_trials=2,
            minimum_patients_per_trial=5,
            selected_trial_count=2,
            generated_at=CREATED_AT,
            code_commit=COMMIT,
        )
        reproducible_selection = ReproducibleTrialSelection(
            disease_domain="atrial_fibrillation",
            rationale="Synthetic capacity-bound AF benchmark selection",
            query_parameters={
                "query.cond": "Atrial Fibrillation",
                "filter.overallStatus": (
                    "RECRUITING|NOT_YET_RECRUITING"
                ),
            },
            filters=TrialFilterPolicy(
                study_types=("INTERVENTIONAL",),
                overall_statuses=("RECRUITING", "NOT_YET_RECRUITING"),
                require_eligibility_text=True,
                first_posted_from="2024-01-01",
                first_posted_to="2025-12-31",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            snapshot_dir = Path(directory) / "snapshot"
            manifest = build_benchmark_trial_snapshot(
                studies=self.response["studies"],
                version_payload=self.version,
                registry_reported_total_count=3,
                pages_fetched=1,
                selection=reproducible_selection,
                capacity_plan=plan,
                output_dir=snapshot_dir,
                created_at=CREATED_AT,
                builder_code_commit=COMMIT,
            )
            self.assertEqual("1.1.0", manifest["snapshot_version"])
            self.assertEqual(3, manifest["search"][
                "registry_reported_total_count"
            ])
            self.assertTrue(manifest["search"]["registry_fetch_complete"])
            self.assertEqual(
                plan["plan_sha256"],
                manifest["selection"]["capacity_binding"][
                    "capacity_plan_sha256"
                ],
            )
            audit = json.loads(
                (snapshot_dir / "selection-audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(3, audit["flow"]["fetched_candidate_count"])
            self.assertEqual(2, audit["flow"]["selected_count"])
            self.assertEqual(1, audit["flow"]["filter_excluded_count"])
            validate_trial_snapshot(snapshot_dir)


class ClinicalTrialsPaginationTest(unittest.TestCase):
    def test_cursor_pagination_and_limit_are_explicit(self) -> None:
        client = ClinicalTrialsClient()
        calls = []
        first = {"studies": [{"page": 1}], "nextPageToken": "token 2", "totalCount": 5}
        second = {"studies": [{"page": 2}, {"page": 2.1}]}

        def fake_get_json(url):
            calls.append(url)
            if url.endswith("/version"):
                return {"apiVersion": "2.0.5", "dataTimestamp": "timestamp"}
            return first if len(calls) == 2 else second

        client._get_json = fake_get_json
        studies, _, metadata = client.search(
            {
                "query.cond": "Atrial Fibrillation",
                "sort": "LastUpdatePostDate:desc",
                "pageSize": "2",
            },
            max_studies=3,
        )
        self.assertEqual(3, len(studies))
        self.assertTrue(
            any("pageToken=token+2" in call for call in calls)
        )
        self.assertEqual(2, metadata["pages_fetched"])
        self.assertTrue(metadata["selection_truncated"])

    def test_transient_page_token_cannot_enter_selection(self) -> None:
        with self.assertRaisesRegex(TrialImportError, "transient"):
            ClinicalTrialsClient().search(
                {"query.cond": "Synthetic", "pageToken": "opaque"}
            )

    def test_repeated_cursor_is_rejected(self) -> None:
        client = ClinicalTrialsClient()

        def fake_get_json(url):
            if url.endswith("/version"):
                return {"apiVersion": "2.0.5", "dataTimestamp": "timestamp"}
            return {"studies": [], "nextPageToken": "repeated"}

        client._get_json = fake_get_json
        with self.assertRaisesRegex(TrialImportError, "repeated"):
            client.search({"query.cond": "Synthetic", "pageSize": "1"})

    def test_registry_refresh_during_pagination_is_rejected(self) -> None:
        client = ClinicalTrialsClient()
        version_calls = 0

        def fake_get_json(url):
            nonlocal version_calls
            if url.endswith("/version"):
                version_calls += 1
                return {
                    "apiVersion": "2.0.5",
                    "dataTimestamp": f"timestamp-{version_calls}",
                }
            return {"studies": [], "totalCount": 0}

        client._get_json = fake_get_json
        with self.assertRaisesRegex(TrialImportError, "changed"):
            client.search({"query.cond": "Synthetic", "pageSize": "1"})


if __name__ == "__main__":
    unittest.main()
