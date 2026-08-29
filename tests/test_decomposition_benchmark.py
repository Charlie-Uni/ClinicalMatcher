import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinical_matcher.decomposition_annotation import (
    DecompositionAnnotationError,
    build_annotation_template,
    finalize_annotation,
    finalize_concept_catalog,
    validate_annotation,
    validate_concept_catalog,
)
from clinical_matcher.decomposition_benchmark import (
    DecompositionBenchmarkError,
    build_decomposition_selection_from_verified_documents,
    criterion_complexity,
    validate_decomposition_selection,
    validate_decomposition_selection_document,
    write_new_json,
)


COMMIT = "a" * 40
SNAPSHOT_HASH = "b" * 64
MANIFEST_HASH = "c" * 64
GUIDE_HASH = "d" * 64
RULES_HASH = "e" * 64


def canonical_hash(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def texts(nct_id, index):
    unique = f"{nct_id} item {index}"
    return {
        "low": f"Age 18 {unique}",
        "medium": f"Age 18 and clinically stable {unique}",
        "high": (
            f"Age 18 and no prior treatment within 30 days {unique}; "
            + "clinically documented eligibility context " * 5
        ),
    }


def synthetic_documents(trial_count=20, omit_stratum=None, duplicate=False):
    protocols = []
    records = []
    duplicate_text = None
    for trial_index in range(trial_count):
        nct_id = f"NCT{trial_index + 1:08d}"
        eligibility_parts = []
        criteria = []
        cursor = 0
        item_index = 0
        for criterion_type in ("inclusion", "exclusion"):
            for tier in ("low", "medium", "high"):
                if omit_stratum == f"{criterion_type}-{tier}":
                    continue
                source_text = texts(nct_id, item_index)[tier]
                if duplicate and trial_index == 1 and item_index == 0:
                    source_text = duplicate_text
                if trial_index == 0 and item_index == 0:
                    duplicate_text = source_text
                if eligibility_parts:
                    eligibility_parts.append("\n")
                    cursor += 1
                start = cursor
                eligibility_parts.append(source_text)
                cursor += len(source_text)
                end = cursor
                source_id = f"clinicaltrials.gov:{nct_id}:eligibility:v1"
                criteria.append(
                    {
                        "criterion_id": (
                            f"{nct_id.lower()}-{criterion_type}-{item_index}"
                        ),
                        "criterion_type": criterion_type,
                        "source_id": source_id,
                        "source_span": {"start": start, "end": end},
                        "source_text": source_text,
                        "normalized_text": " ".join(source_text.split()),
                    }
                )
                item_index += 1
        eligibility_text = "".join(eligibility_parts)
        eligibility_sha = hashlib.sha256(
            eligibility_text.encode("utf-8")
        ).hexdigest()
        protocol = {
            "nct_id": nct_id,
            "source_record_version": f"2026-01-01:{eligibility_sha[:12]}",
            "last_update_posted": "2026-01-01",
            "eligibility_text": eligibility_text,
            "eligibility_sha256": eligibility_sha,
            "criteria": criteria,
        }
        protocols.append(protocol)
        records.append(
            {
                "status": "imported",
                "nct_id": nct_id,
                "protocol_sha256": canonical_hash(protocol),
                "eligibility_sha256": eligibility_sha,
            }
        )
    manifest = {
        "snapshot_version": "1.1.0",
        "snapshot_id": f"ctg-{SNAPSHOT_HASH[:16]}",
        "snapshot_content_sha256": SNAPSHOT_HASH,
        "records": records,
    }
    return manifest, protocols


def selection(**kwargs):
    manifest, protocols = synthetic_documents(**kwargs)
    return build_decomposition_selection_from_verified_documents(
        manifest,
        MANIFEST_HASH,
        protocols,
        COMMIT,
    )


def catalog_for(document, split="dev"):
    return finalize_concept_catalog(
        document,
        {
            "split": split,
            "construction_rules_version": "concept-catalog-rules/1.0.0",
            "construction_rules_sha256": RULES_HASH,
            "entries": [
                {
                    "field_id": "age",
                    "definition": "Age threshold stated in the criterion.",
                    "aliases": ["Age"],
                }
            ],
        },
    )


def completed_annotation(document, catalog):
    draft = build_annotation_template(
        document,
        catalog,
        annotator_id="owner",
        annotation_mode="dual_independent_with_adjudication",
        annotation_guide_version="decomposition-guide/1.0.0",
        annotation_guide_sha256=GUIDE_HASH,
    )
    draft["independence_attestation"] = {
        "other_annotations_not_viewed": True,
        "model_outputs_not_viewed": True,
    }
    for index, item in enumerate(draft["items"]):
        item["expression"] = {
            "expression_type": "atom",
            "atom": {
                "condition_id": f"condition-{index:03d}",
                "field": "age",
                "operator": ">=",
                "expected": {
                    "value_type": "number",
                    "value": 18,
                    "unit": "years",
                },
                "fact_selection": "any",
                "provenance": {
                    "source_id": item["source_id"],
                    "source_span": {"start": 0, "end": 3},
                    "method": "human",
                },
            },
        }
    return finalize_annotation(document, catalog, draft)


class DecompositionSelectionTest(unittest.TestCase):
    def test_complexity_contract_uses_all_frozen_proxies(self):
        self.assertEqual("low", criterion_complexity("Age 18")["tier"])
        self.assertEqual(
            "medium",
            criterion_complexity("Age 18 and clinically stable")["tier"],
        )
        high = criterion_complexity(
            "Age 18 and no prior treatment within 30 days; " + "context " * 30
        )
        self.assertEqual("high", high["tier"])
        self.assertEqual(6, high["score"])

    def test_selection_is_deterministic_trial_isolated_and_exact(self):
        first = selection()
        second = selection()
        self.assertEqual(first, second)
        validate_decomposition_selection_document(first)
        self.assertEqual(80, first["counts"]["selected_count"])
        split_by_trial = {}
        selected_per_trial = {}
        for record in first["records"]:
            if record["assigned_split"]:
                split_by_trial.setdefault(
                    record["nct_id"], record["assigned_split"]
                )
                self.assertEqual(
                    split_by_trial[record["nct_id"]],
                    record["assigned_split"],
                )
            if record["selected"]:
                selected_per_trial[record["nct_id"]] = (
                    selected_per_trial.get(record["nct_id"], 0) + 1
                )
        self.assertTrue(all(count <= 8 for count in selected_per_trial.values()))
        for split in ("dev", "test"):
            self.assertEqual(
                40, first["counts"]["selected_by_split"][split]["total"]
            )
            self.assertGreaterEqual(
                first["counts"]["selected_trials_by_split"][split], 5
            )

    def test_duplicate_text_has_one_hash_selected_representative(self):
        document = selection(duplicate=True)
        duplicated_text = "age 18 nct00000001 item 0"
        duplicate_group = [
            record
            for record in document["records"]
            if record["normalized_text"] == duplicated_text
        ]
        self.assertEqual(2, len(duplicate_group))
        self.assertEqual(
            1,
            sum(record["assigned_split"] is not None for record in duplicate_group),
        )
        self.assertEqual(1, document["counts"]["duplicate_excluded_count"])

    def test_quota_shortage_fails_without_relaxation(self):
        with self.assertRaisesRegex(
            DecompositionBenchmarkError, "Quota shortage"
        ):
            selection(omit_stratum="exclusion-high")

    def test_manifest_tampering_breaks_self_hash(self):
        document = selection()
        document["records"][0]["source_text"] = "tampered"
        with self.assertRaisesRegex(
            DecompositionBenchmarkError, "hash mismatch"
        ):
            validate_decomposition_selection_document(document)

    def test_source_bound_verifier_rebuilds_from_snapshot_files(self):
        manifest, protocols = synthetic_documents()
        with tempfile.TemporaryDirectory() as directory:
            snapshot_dir = Path(directory)
            manifest_path = snapshot_dir / "snapshot-manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            document = build_decomposition_selection_from_verified_documents(
                manifest,
                manifest_sha,
                protocols,
                COMMIT,
            )
            with mock.patch(
                "clinical_matcher.decomposition_benchmark.validate_trial_snapshot",
                return_value=manifest,
            ), mock.patch(
                "clinical_matcher.decomposition_benchmark.load_snapshot_protocols",
                return_value=tuple(protocols),
            ):
                validate_decomposition_selection(snapshot_dir, document)
                manifest_path.write_text("{}\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    DecompositionBenchmarkError, "does not reproduce"
                ):
                    validate_decomposition_selection(snapshot_dir, document)

    def test_writer_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            write_new_json(path, selection())
            with self.assertRaises(FileExistsError):
                write_new_json(path, selection())


class DecompositionAnnotationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = selection()
        cls.catalog = catalog_for(cls.selection)

    def test_catalog_is_hash_bound_and_source_grounded(self):
        validate_concept_catalog(self.selection, self.catalog)
        bad = {
            "split": "dev",
            "construction_rules_version": "concept-catalog-rules/1.0.0",
            "construction_rules_sha256": RULES_HASH,
            "entries": [
                {
                    "field_id": "fictional",
                    "definition": "Not in the selected source.",
                    "aliases": ["definitely absent alias"],
                }
            ],
        }
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "not grounded"
        ):
            finalize_concept_catalog(self.selection, bad)

        duplicate_alias = {
            "split": "dev",
            "construction_rules_version": "concept-catalog-rules/1.0.0",
            "construction_rules_sha256": RULES_HASH,
            "entries": [
                {
                    "field_id": "age",
                    "definition": "Age threshold.",
                    "aliases": ["Age", "age"],
                }
            ],
        }
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "unique after normalization"
        ):
            finalize_concept_catalog(self.selection, duplicate_alias)

    def test_template_binds_catalog_after_selection_and_covers_split(self):
        template = build_annotation_template(
            self.selection,
            self.catalog,
            "annotator-2",
            "dual_independent_with_adjudication",
            "decomposition-guide/1.0.0",
            GUIDE_HASH,
        )
        validate_annotation(
            self.selection,
            self.catalog,
            template,
            require_completed=False,
        )
        self.assertEqual(40, len(template["items"]))
        self.assertTrue(all(item["expression"] is None for item in template["items"]))

    def test_completed_annotation_enforces_semantic_contract(self):
        annotation = completed_annotation(self.selection, self.catalog)
        validate_annotation(self.selection, self.catalog, annotation)
        self.assertEqual("completed", annotation["annotation_status"])

    def test_unknown_catalog_field_is_rejected(self):
        annotation = completed_annotation(self.selection, self.catalog)
        annotation["items"][0]["expression"]["atom"]["field"] = "unknown"
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "absent from the frozen catalog"
        ):
            finalize_annotation(self.selection, self.catalog, annotation)

    def test_boolean_relational_operator_is_rejected(self):
        annotation = completed_annotation(self.selection, self.catalog)
        atom = annotation["items"][0]["expression"]["atom"]
        atom["expected"] = {"value_type": "boolean", "value": True}
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "requires == or !="
        ):
            finalize_annotation(self.selection, self.catalog, annotation)

    def test_nonfinite_numeric_value_is_rejected(self):
        annotation = completed_annotation(self.selection, self.catalog)
        annotation["items"][0]["expression"]["atom"]["expected"][
            "value"
        ] = float("nan")
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "must be finite"
        ):
            finalize_annotation(self.selection, self.catalog, annotation)

    def test_duplicate_condition_ids_are_rejected(self):
        annotation = completed_annotation(self.selection, self.catalog)
        first = annotation["items"][0]["expression"]["atom"]["condition_id"]
        annotation["items"][1]["expression"]["atom"]["condition_id"] = first
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "Duplicate condition ID"
        ):
            finalize_annotation(self.selection, self.catalog, annotation)

    def test_source_identity_and_spans_fail_closed(self):
        annotation = completed_annotation(self.selection, self.catalog)
        annotation["items"][0]["source_text"] = "different source"
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "source identity mismatch"
        ):
            finalize_annotation(self.selection, self.catalog, annotation)

        annotation = completed_annotation(self.selection, self.catalog)
        span = annotation["items"][0]["expression"]["atom"]["provenance"][
            "source_span"
        ]
        span["end"] = len(annotation["items"][0]["source_text"]) + 1
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "outside source text"
        ):
            finalize_annotation(self.selection, self.catalog, annotation)

    def test_completed_annotation_requires_attestations_and_every_tree(self):
        template = build_annotation_template(
            self.selection,
            self.catalog,
            "owner",
            "single_annotator",
            "decomposition-guide/1.0.0",
            GUIDE_HASH,
        )
        with self.assertRaisesRegex(
            DecompositionAnnotationError, "attestations"
        ):
            finalize_annotation(self.selection, self.catalog, template)


if __name__ == "__main__":
    unittest.main()
