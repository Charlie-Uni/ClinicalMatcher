import copy
import hashlib
import json
import tempfile
import unittest
from importlib.resources import files
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
from clinical_matcher.decomposition_evaluation import (
    DecompositionEvaluationError,
    compare_decomposition_expressions,
    evaluate_decomposition,
    normalize_decomposition_expression,
    render_decomposition_evaluation_markdown,
    validate_decomposition_evaluation_report,
)
from clinical_matcher.decomposition_gold import (
    DecompositionGoldError,
    build_adjudicated_gold,
    build_adjudication_template,
    build_single_annotator_gold,
    finalize_adjudication,
    validate_adjudication,
    validate_gold,
    validate_single_annotator_decision,
)
from clinical_matcher.decomposition_overlap import (
    DecompositionOverlapError,
    build_overlap_diagnostic,
    lexical_tokens,
    validate_overlap_diagnostic,
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


def completed_annotation(
    document,
    catalog,
    annotator_id="owner",
    annotation_mode="dual_independent_with_adjudication",
):
    draft = build_annotation_template(
        document,
        catalog,
        annotator_id=annotator_id,
        annotation_mode=annotation_mode,
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


def llm_expression(expression, model_id="local-llama", prompt_version="prompt-v1"):
    result = copy.deepcopy(expression)

    def replace_provenance(node):
        if node["expression_type"] == "atom":
            provenance = node["atom"]["provenance"]
            provenance["method"] = "llm"
            provenance["model_id"] = model_id
            provenance["prompt_version"] = prompt_version
            return
        for child in node["children"]:
            replace_provenance(child)

    replace_provenance(result)
    return result


def predictions_from_annotation(annotation):
    return [
        {
            "nct_id": item["nct_id"],
            "criterion_id": item["criterion_id"],
            "expression": llm_expression(item["expression"]),
        }
        for item in annotation["items"]
    ]


def _annotation_item(annotation, criterion_id):
    return next(
        item for item in annotation["items"] if item["criterion_id"] == criterion_id
    )


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


class DecompositionOverlapTest(unittest.TestCase):
    def test_tokenization_is_unicode_normalized_casefolded_and_unique(self):
        self.assertEqual(
            frozenset({"café", "age", "18"}),
            lexical_tokens("ＣＡＦÉ café AGE_18"),
        )

    def test_diagnostic_is_exhaustive_deterministic_and_report_only(self):
        document = selection()
        first = build_overlap_diagnostic(document, MANIFEST_HASH, COMMIT)
        second = build_overlap_diagnostic(document, MANIFEST_HASH, COMMIT)
        self.assertEqual(first, second)
        self.assertEqual("disclosure_only_no_selection_gate", first["status"])
        self.assertEqual(1600, first["counts"]["cross_split_pairs_evaluated"])
        self.assertEqual(20, len(first["top_pairs"]))
        self.assertEqual("none", first["method"]["selection_effect"])
        scores = [pair["jaccard_similarity"] for pair in first["top_pairs"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_diagnostic_verifier_rebuilds_and_rejects_tampering(self):
        document = selection()
        with tempfile.TemporaryDirectory() as directory:
            selection_path = Path(directory) / "selection.json"
            selection_path.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            file_hash = hashlib.sha256(selection_path.read_bytes()).hexdigest()
            report = build_overlap_diagnostic(document, file_hash, COMMIT)
            validate_overlap_diagnostic(selection_path, report)
            report["distribution"]["maximum"] = 0.0
            with self.assertRaisesRegex(
                DecompositionOverlapError, "hash mismatch"
            ):
                validate_overlap_diagnostic(selection_path, report)


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


class DecompositionGoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = selection()
        cls.catalog = catalog_for(cls.selection)

    def annotations(self):
        return (
            completed_annotation(
                self.selection, self.catalog, annotator_id="annotator-a"
            ),
            completed_annotation(
                self.selection, self.catalog, annotator_id="annotator-b"
            ),
        )

    def test_identical_annotations_freeze_adjudicated_gold(self):
        annotations = self.annotations()
        draft = build_adjudication_template(
            self.selection,
            self.catalog,
            annotations,
            ["annotator-a", "annotator-b"],
        )
        self.assertTrue(
            all(
                item["resolution_status"] == "agreed_without_dispute"
                for item in draft["items"]
            )
        )
        agreement = draft["pre_adjudication_agreement"]
        self.assertEqual(40, agreement["normalized_tree_exact_count"])
        self.assertEqual(1.0, agreement["atom_micro_f1"])
        self.assertEqual(
            {name: 0 for name in agreement["disagreement_counts"]},
            agreement["disagreement_counts"],
        )

        adjudication = finalize_adjudication(
            self.selection, self.catalog, annotations, draft
        )
        validate_adjudication(
            self.selection, self.catalog, annotations, adjudication
        )
        gold = build_adjudicated_gold(
            self.selection, self.catalog, annotations, adjudication
        )
        validate_gold(
            self.selection,
            self.catalog,
            annotations,
            gold,
            adjudication=adjudication,
        )
        self.assertEqual("adjudicated_gold", gold["gold_label"])
        self.assertEqual(
            adjudication["adjudication_sha256"],
            gold["adjudication"]["adjudication_sha256"],
        )
        self.assertNotIn("items", gold)

    def test_disagreement_must_be_resolved_before_gold(self):
        left, right = self.annotations()
        right = copy.deepcopy(right)
        right["items"][0]["expression"]["atom"]["operator"] = ">"
        right = finalize_annotation(self.selection, self.catalog, right)
        annotations = (left, right)
        draft = build_adjudication_template(
            self.selection,
            self.catalog,
            annotations,
            ["annotator-a", "annotator-b"],
        )
        disputed = [
            item for item in draft["items"] if item["resolution_status"] == "unresolved"
        ]
        self.assertEqual(1, len(disputed))
        self.assertEqual(
            ["atom_identity", "operator"],
            disputed[0]["disagreement_types"],
        )
        with self.assertRaisesRegex(DecompositionGoldError, "unresolved"):
            finalize_adjudication(
                self.selection, self.catalog, annotations, draft
            )

        left_item = _annotation_item(left, disputed[0]["criterion_id"])
        disputed[0]["resolution_status"] = "resolved"
        disputed[0]["expression"] = copy.deepcopy(left_item["expression"])
        disputed[0]["rationale"] = "Consensus retained the inclusive threshold."
        adjudication = finalize_adjudication(
            self.selection, self.catalog, annotations, draft
        )
        gold = build_adjudicated_gold(
            self.selection, self.catalog, annotations, adjudication
        )
        resolved = next(
            item
            for item in adjudication["items"]
            if item["criterion_id"] == disputed[0]["criterion_id"]
        )
        self.assertEqual(left_item["expression"], resolved["expression"])
        self.assertEqual(
            adjudication["adjudication_id"],
            gold["adjudication"]["adjudication_id"],
        )

    def test_agreed_item_cannot_be_changed_during_adjudication(self):
        annotations = self.annotations()
        draft = build_adjudication_template(
            self.selection,
            self.catalog,
            annotations,
            ["annotator-a", "annotator-b"],
        )
        draft["items"][0]["expression"]["atom"]["expected"]["value"] = 21
        with self.assertRaisesRegex(DecompositionGoldError, "cannot be changed"):
            finalize_adjudication(
                self.selection, self.catalog, annotations, draft
            )

    def test_iaa_comparison_uses_frozen_normalization(self):
        left, right = self.annotations()
        left_expression = left["items"][0]["expression"]
        right_expression = copy.deepcopy(right["items"][0]["expression"])
        right_expression["atom"]["provenance"]["source_span"] = {
            "start": 1,
            "end": 3,
        }
        result = compare_decomposition_expressions(
            left_expression, right_expression
        )
        self.assertTrue(result["normalized_tree_exact"])
        self.assertEqual(1.0, result["atom_f1"])
        self.assertEqual(["source_span"], result["disagreement_types"])

        age = copy.deepcopy(left_expression)
        bmi = copy.deepcopy(left_expression)
        bmi["atom"]["condition_id"] = "bmi-condition"
        bmi["atom"]["field"] = "bmi"
        bmi["atom"]["operator"] = "<="
        bmi["atom"]["expected"]["value"] = 30
        swapped_age = copy.deepcopy(age)
        swapped_age["atom"]["operator"] = "<="
        swapped_age["atom"]["expected"]["value"] = 30
        swapped_bmi = copy.deepcopy(bmi)
        swapped_bmi["atom"]["operator"] = ">="
        swapped_bmi["atom"]["expected"]["value"] = 18
        reassigned = compare_decomposition_expressions(
            {"expression_type": "all", "children": [age, bmi]},
            {
                "expression_type": "all",
                "children": [swapped_age, swapped_bmi],
            },
        )
        self.assertEqual(["atom_identity"], reassigned["disagreement_types"])

    def test_adjudicator_ids_are_unique_and_include_both_annotators(self):
        annotations = self.annotations()
        with self.assertRaisesRegex(DecompositionGoldError, "non-empty and unique"):
            build_adjudication_template(
                self.selection,
                self.catalog,
                annotations,
                ["annotator-a", "annotator-a", "annotator-b"],
            )
        with self.assertRaisesRegex(DecompositionGoldError, "Both source annotators"):
            build_adjudication_template(
                self.selection,
                self.catalog,
                annotations,
                ["annotator-a", "third-reviewer"],
            )

    def test_equivalence_review_is_recorded_but_does_not_resolve_gold(self):
        left, right = self.annotations()
        right = copy.deepcopy(right)
        right["items"][0]["expression"] = {
            "expression_type": "all",
            "children": [right["items"][0]["expression"]],
        }
        right = finalize_annotation(self.selection, self.catalog, right)
        annotations = (left, right)
        draft = build_adjudication_template(
            self.selection,
            self.catalog,
            annotations,
            ["annotator-a", "annotator-b"],
        )
        queued = [item for item in draft["items"] if item["equivalence_review_queued"]]
        self.assertEqual(1, len(queued))
        self.assertEqual(["structure"], queued[0]["disagreement_types"])
        queued[0]["resolution_status"] = "resolved"
        queued[0]["expression"] = copy.deepcopy(
            _annotation_item(left, queued[0]["criterion_id"])["expression"]
        )
        queued[0]["rationale"] = "Consensus retained the direct atom."
        with self.assertRaisesRegex(
            DecompositionGoldError, "requires queued equivalence review"
        ):
            finalize_adjudication(
                self.selection, self.catalog, annotations, draft
            )
        queued[0]["equivalence_review_judgment"] = "equivalent"
        adjudication = finalize_adjudication(
            self.selection, self.catalog, annotations, draft
        )
        self.assertEqual(
            "equivalent",
            adjudication["items"][0]["equivalence_review_judgment"],
        )

    def test_single_annotator_gold_cannot_claim_adjudication(self):
        annotation = completed_annotation(
            self.selection,
            self.catalog,
            annotator_id="owner",
            annotation_mode="single_annotator",
        )
        decision = json.loads(
            files("clinical_matcher")
            .joinpath(
                "resources/decomposition-single-annotator-decision-1.0.0.json"
            )
            .read_text(encoding="utf-8")
        )
        validate_single_annotator_decision(decision)
        gold = build_single_annotator_gold(
            self.selection,
            self.catalog,
            annotation,
            downgrade_decision=decision,
        )
        validate_gold(
            self.selection,
            self.catalog,
            (annotation,),
            gold,
            downgrade_decision=decision,
        )
        self.assertEqual("single_annotator_reference_gold", gold["gold_label"])
        self.assertIsNone(gold["adjudication"])
        self.assertEqual(
            decision["decision_sha256"],
            gold["single_annotator_downgrade"]["decision_sha256"],
        )

        tampered = copy.deepcopy(gold)
        tampered["gold_label"] = "adjudicated_gold"
        with self.assertRaisesRegex(DecompositionGoldError, "hash mismatch"):
            validate_gold(
                self.selection,
                self.catalog,
                (annotation,),
                tampered,
                downgrade_decision=decision,
            )

        with self.assertRaisesRegex(
            DecompositionGoldError, "requires its decision artifact"
        ):
            validate_gold(self.selection, self.catalog, (annotation,), gold)

        tampered_decision = copy.deepcopy(decision)
        tampered_decision["decided_on"] = "2026-08-30"
        with self.assertRaisesRegex(DecompositionGoldError, "hash mismatch"):
            validate_single_annotator_decision(tampered_decision)


class DecompositionEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selection = selection()
        cls.catalog = catalog_for(cls.selection)
        cls.gold = completed_annotation(cls.selection, cls.catalog)

    def evaluate(self, predictions, gold=None):
        return evaluate_decomposition(
            self.selection,
            self.catalog,
            gold or self.gold,
            predictions,
            model_id="local-llama",
            prompt_version="prompt-v1",
            bootstrap_samples=20,
            bootstrap_seed=23,
        )

    def complex_gold(self):
        gold = copy.deepcopy(self.gold)
        first = gold["items"][0]
        first_atom = copy.deepcopy(first["expression"])
        second_atom = copy.deepcopy(first_atom)
        second_atom["atom"]["condition_id"] = "condition-extra"
        second_atom["atom"]["operator"] = "<="
        second_atom["atom"]["expected"]["value"] = 65
        first["expression"] = {
            "expression_type": "all",
            "children": [first_atom, second_atom],
        }
        return finalize_annotation(self.selection, self.catalog, gold)

    def test_exact_predictions_score_one_and_report_is_self_validating(self):
        predictions = predictions_from_annotation(self.gold)
        report = self.evaluate(predictions)
        validate_decomposition_evaluation_report(report)
        self.assertEqual(report, self.evaluate(list(reversed(predictions))))
        self.assertEqual(40, report["denominators"]["criteria"])
        self.assertEqual(40, report["denominators"]["gold_atoms"])
        self.assertEqual(40, report["denominators"]["predicted_atoms"])
        for metric in (
            "normalized_tree_exact_rate",
            "operator_topology_exact_rate",
            "atom_micro_precision",
            "atom_micro_recall",
            "atom_micro_f1",
            "atom_macro_f1",
            "span_exact_rate",
            "span_mean_iou",
            "schema_valid_rate",
            "verifier_load_rate",
        ):
            self.assertEqual(1.0, report["metrics"][metric], metric)
        self.assertEqual(20, report["bootstrap"]["samples"])
        self.assertGreaterEqual(report["bootstrap"]["trial_count"], 5)
        markdown = render_decomposition_evaluation_markdown(report)
        self.assertIn("Invalid and missing predictions remain", markdown)

        tampered = copy.deepcopy(report)
        tampered["metrics"]["atom_micro_f1"] = 0.5
        with self.assertRaisesRegex(
            DecompositionEvaluationError, "hash mismatch"
        ):
            validate_decomposition_evaluation_report(tampered)

    def test_missing_and_invalid_predictions_remain_in_denominator(self):
        empty_report = self.evaluate([])
        self.assertEqual(40, empty_report["failure_counts"]["missing"])
        self.assertEqual(0.0, empty_report["metrics"]["atom_micro_recall"])
        self.assertEqual(0.0, empty_report["metrics"]["schema_valid_rate"])
        self.assertEqual(40, empty_report["denominators"]["criteria"])

        predictions = predictions_from_annotation(self.gold)
        predictions[0]["expression"] = {}
        predictions[1]["expression"] = None
        report = self.evaluate(predictions)
        self.assertEqual(1, report["failure_counts"]["schema_invalid"])
        self.assertEqual(1, report["failure_counts"]["missing"])
        self.assertEqual(38, report["metrics"]["schema_valid_count"])
        self.assertEqual(38, report["metrics"]["verifier_load_count"])
        self.assertEqual(38, report["denominators"]["predicted_atoms"])

    def test_wrong_operator_value_and_type_receive_no_partial_atom_credit(self):
        mutations = (
            ("operator", lambda atom: atom.update(operator=">")),
            ("value", lambda atom: atom["expected"].update(value=21)),
            (
                "type",
                lambda atom: atom.update(
                    operator="==",
                    expected={"value_type": "string", "value": "18"},
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                predictions = predictions_from_annotation(self.gold)
                mutate(predictions[0]["expression"]["atom"])
                report = self.evaluate(predictions)
                self.assertEqual(
                    39, report["denominators"]["identity_matched_atoms"]
                )
                self.assertLess(report["metrics"]["atom_micro_f1"], 1.0)
                self.assertEqual(1.0, report["metrics"]["schema_valid_rate"])

    def test_shifted_span_changes_span_metrics_not_atom_matching(self):
        predictions = predictions_from_annotation(self.gold)
        predictions[0]["expression"]["atom"]["provenance"]["source_span"] = {
            "start": 1,
            "end": 3,
        }
        report = self.evaluate(predictions)
        self.assertEqual(40, report["denominators"]["identity_matched_atoms"])
        self.assertEqual(39, report["metrics"]["span_exact_count"])
        self.assertEqual(39 / 40, report["metrics"]["span_exact_rate"])
        self.assertLess(report["metrics"]["span_mean_iou"], 1.0)
        self.assertEqual(1.0, report["metrics"]["atom_micro_f1"])

    def test_invalid_provenance_is_schema_valid_but_fails_verifier_load(self):
        predictions = predictions_from_annotation(self.gold)
        predictions[0]["expression"]["atom"]["provenance"]["model_id"] = (
            "another-model"
        )
        report = self.evaluate(predictions)
        self.assertEqual(
            1, report["failure_counts"]["prediction_provenance_mismatch"]
        )
        self.assertEqual(40, report["metrics"]["schema_valid_count"])
        self.assertEqual(39, report["metrics"]["verifier_load_count"])
        self.assertEqual(40, report["denominators"]["criteria"])

    def test_structure_is_separate_and_equivalence_review_never_changes_score(self):
        gold = self.complex_gold()
        predictions = predictions_from_annotation(gold)
        predictions[0]["expression"]["expression_type"] = "any"
        report = self.evaluate(predictions, gold=gold)
        self.assertEqual(1.0, report["metrics"]["atom_micro_f1"])
        self.assertEqual(39, report["metrics"]["normalized_tree_exact_count"])
        self.assertEqual(39, report["metrics"]["operator_topology_exact_count"])
        self.assertEqual(1, report["equivalence_review"]["queued_count"])
        self.assertFalse(
            report["equivalence_review"]["affects_primary_metrics"]
        )

    def test_missing_extra_atoms_and_wrong_not_structure_are_distinct(self):
        gold = self.complex_gold()

        missing = predictions_from_annotation(gold)
        missing[0]["expression"] = missing[0]["expression"]["children"][0]
        missing_report = self.evaluate(missing, gold=gold)
        self.assertEqual(41, missing_report["denominators"]["gold_atoms"])
        self.assertEqual(40, missing_report["denominators"]["predicted_atoms"])
        self.assertEqual(40, missing_report["denominators"]["identity_matched_atoms"])

        extra = predictions_from_annotation(self.gold)
        original = extra[0]["expression"]
        additional = copy.deepcopy(original)
        additional["atom"]["condition_id"] = "additional-prediction"
        additional["atom"]["operator"] = "<="
        additional["atom"]["expected"]["value"] = 65
        extra[0]["expression"] = {
            "expression_type": "all",
            "children": [original, additional],
        }
        extra_report = self.evaluate(extra)
        self.assertEqual(40, extra_report["denominators"]["gold_atoms"])
        self.assertEqual(41, extra_report["denominators"]["predicted_atoms"])
        self.assertEqual(40, extra_report["denominators"]["identity_matched_atoms"])

        wrong_not = predictions_from_annotation(gold)
        wrong_not[0]["expression"] = {
            "expression_type": "not",
            "children": [wrong_not[0]["expression"]],
        }
        not_report = self.evaluate(wrong_not, gold=gold)
        self.assertEqual(39, not_report["metrics"]["operator_topology_exact_count"])
        self.assertLess(not_report["metrics"]["atom_micro_recall"], 1.0)

    def test_not_pushdown_flattening_and_decimal_canonicalization_are_frozen(self):
        atom_a = llm_expression(self.gold["items"][0]["expression"])
        atom_b = copy.deepcopy(atom_a)
        atom_b["atom"]["condition_id"] = "second"
        atom_b["atom"]["expected"]["value"] = 50.0
        atom_a["atom"]["expected"]["value"] = 50
        left = {
            "expression_type": "not",
            "children": [
                {"expression_type": "any", "children": [atom_a, atom_b]}
            ],
        }
        right = {
            "expression_type": "all",
            "children": [
                {"expression_type": "not", "children": [atom_b]},
                {"expression_type": "not", "children": [atom_a]},
            ],
        }
        self.assertEqual(
            normalize_decomposition_expression(left),
            normalize_decomposition_expression(right),
        )

    def test_prediction_identity_and_duplicate_condition_ids_fail_closed(self):
        predictions = predictions_from_annotation(self.gold)
        predictions[1]["expression"]["atom"]["condition_id"] = predictions[0][
            "expression"
        ]["atom"]["condition_id"]
        report = self.evaluate(predictions)
        self.assertEqual(2, report["failure_counts"]["duplicate_condition_id"])

        unexpected = predictions_from_annotation(self.gold)
        unexpected[0]["criterion_id"] = "not-selected"
        with self.assertRaisesRegex(
            DecompositionEvaluationError, "unselected criterion"
        ):
            self.evaluate(unexpected)


if __name__ == "__main__":
    unittest.main()
