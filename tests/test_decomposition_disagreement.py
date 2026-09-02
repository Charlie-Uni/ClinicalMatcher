import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.decomposition_disagreement import (
    DecompositionDisagreementError,
    PRIMARY_CATEGORIES,
    build_component_diagnostics,
    classify_primary_failure,
    load_disagreement_contract,
    publish_disagreement_package,
    validate_disagreement_report,
)


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RESULT = (
    ROOT / "benchmarks" / "decomposition" / "llama_dev_initial_prompt_1.0.0"
)


def atom(*, field="age", value_type="boolean", value=True, unit=None, selection="latest"):
    expected = {"value_type": value_type, "value": value}
    if unit is not None:
        expected["unit"] = unit
    return {
        "expression_type": "atom",
        "atom": {
            "condition_id": "criterion:a01",
            "field": field,
            "operator": "==",
            "expected": expected,
            "fact_selection": selection,
            "provenance": {
                "source_id": "source",
                "source_span": {"start": 0, "end": 1},
                "method": "llm",
                "model_id": "model",
                "prompt_version": "prompt",
            },
        },
    }


def prediction(status="valid", expression=None, reason=None, output_tokens=1):
    return {
        "criterion_id": "criterion",
        "output_status": status,
        "failure_reason": reason,
        "output_tokens": output_tokens,
        "expression": expression,
    }


class DecompositionDisagreementTests(unittest.TestCase):
    def test_contract_freezes_exclusive_precedence_and_closed_test(self):
        contract = load_disagreement_contract()
        self.assertEqual(PRIMARY_CATEGORIES, tuple(contract["primary_attribution"]["precedence"]))
        self.assertFalse(contract["retention"]["locked_test_unlocked"])
        self.assertIn("initial frozen prompt v1.0.0", contract["retention"]["scope_statement"])

    def test_runtime_schema_and_semantic_failures_use_precedence(self):
        reference = atom()
        cases = (
            (prediction("runtime_error", reason="error"), "runtime_error"),
            (prediction("schema_invalid", reason="invalid_json", output_tokens=4096), "schema_invalid_output_budget_reached"),
            (prediction("schema_invalid", reason="invalid_json", output_tokens=12), "schema_invalid_other"),
            (prediction("semantic_invalid", expression=atom(), reason="boolean atom requires == or !="), "semantic_invalid_boolean_operator"),
            (prediction("semantic_invalid", expression=atom(), reason="Boolean atoms must state a positive fact with expected=true; represent negation with NOT"), "semantic_invalid_negation_encoding"),
        )
        for item, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, classify_primary_failure(item, reference, output_token_limit=4096))

    def test_valid_failure_precedence_is_atom_count_then_components(self):
        reference = atom()
        two_atoms = {"expression_type": "all", "children": [atom(), atom(field="weight")]}
        self.assertEqual(
            "valid_atom_count_mismatch",
            classify_primary_failure(prediction(expression=two_atoms), reference, output_token_limit=4096),
        )
        self.assertEqual(
            "valid_field_mismatch",
            classify_primary_failure(prediction(expression=atom(field="weight")), reference, output_token_limit=4096),
        )
        self.assertEqual(
            "valid_unit_mismatch",
            classify_primary_failure(prediction(expression=atom(unit="years")), reference, output_token_limit=4096),
        )
        self.assertEqual(
            "valid_fact_selection_mismatch",
            classify_primary_failure(prediction(expression=atom(selection="all")), reference, output_token_limit=4096),
        )

    def test_component_diagnostics_are_nonexclusive_and_do_not_score_atoms(self):
        reference = atom(selection="latest")
        predicted = prediction(expression=atom(selection="all"))
        result = build_component_diagnostics([predicted], {"criterion": reference})
        self.assertTrue(result["nonexclusive"])
        self.assertTrue(result["marginal_multiset_only"])
        self.assertEqual(1.0, result["dimensions"]["field"]["f1"])
        self.assertEqual(0.0, result["dimensions"]["fact_selection"]["f1"])

    def test_numeric_equivalence_is_preserved_in_component_diagnostic(self):
        reference = atom(value_type="number", value=50)
        predicted_expression = atom(value_type="number", value=50.0)
        result = build_component_diagnostics(
            [prediction(expression=predicted_expression)], {"criterion": reference}
        )
        self.assertEqual(1, result["dimensions"]["value"]["matched_count"])

    def test_frozen_public_report_reconciles_and_keeps_test_closed(self):
        report = json.loads(
            (PUBLIC_RESULT / "disagreement-analysis.json").read_text(encoding="utf-8")
        )
        validate_disagreement_report(report)
        self.assertEqual(40, sum(report["primary_category_counts"].values()))
        self.assertFalse(report["retention_decision"]["locked_test_unlocked"])
        self.assertEqual("62820f4", report["code_commit"][:7])

        changed = copy.deepcopy(report)
        changed["primary_category_counts"]["runtime_error"] += 1
        with self.assertRaises(DecompositionDisagreementError):
            validate_disagreement_report(changed)

    def test_publication_is_write_once(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "published"
            publish_disagreement_package(
                ROOT,
                PUBLIC_RESULT,
                output,
                generated_at="2026-09-02T00:00:00Z",
                code_commit="a" * 40,
            )
            self.assertEqual(
                (PUBLIC_RESULT / "predictions.json").read_bytes(),
                (output / "predictions.json").read_bytes(),
            )
            with self.assertRaises(FileExistsError):
                publish_disagreement_package(
                    ROOT,
                    PUBLIC_RESULT,
                    output,
                    generated_at="2026-09-02T00:00:00Z",
                    code_commit="a" * 40,
                )


if __name__ == "__main__":
    unittest.main()
