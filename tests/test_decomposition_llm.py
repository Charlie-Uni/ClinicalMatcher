import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.decomposition_llm import (
    DecompositionLLMError,
    build_messages,
    item_bound_output_schema,
    load_decomposition_llm_contract,
    load_frozen_dev_inputs,
    parse_model_output,
    render_comparison_markdown,
    run_decomposition_llm_dev,
    validate_comparison_report,
    validate_decomposition_llm_contract,
    validate_prediction_artifact,
    write_decomposition_llm_run,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeOllamaClient:
    def __init__(self, contract):
        self.contract = contract
        self.requests = []

    def version(self):
        return self.contract["runtime"]["engine_version"]

    def tags(self):
        return {
            "models": [
                {
                    "name": self.contract["model"]["ollama_model_name"],
                    "digest": self.contract["model"]["ollama_manifest_sha256"],
                }
            ]
        }

    def running_models(self):
        return {"models": []}

    def chat(self, payload):
        self.requests.append(payload)
        user = json.loads(payload["messages"][1]["content"])
        criterion = user["criterion"]
        field = user["concept_catalog"][0]["field_id"]
        expression = {
            "expression_type": "atom",
            "atom": {
                "condition_id": f"{criterion['criterion_id']}:a01",
                "field": field,
                "operator": "==",
                "expected": {"value_type": "boolean", "value": True},
                "fact_selection": "latest",
                "provenance": {
                    "source_id": criterion["source_id"],
                    "source_span": {"start": 0, "end": 1},
                    "method": "llm",
                    "model_id": user["required_atom_provenance"]["model_id"],
                    "prompt_version": user["required_atom_provenance"]["prompt_version"],
                },
            },
        }
        return {
            "message": {"content": json.dumps({"expression": expression})},
            "prompt_eval_count": 100,
            "eval_count": 50,
        }


class InvalidOutputClient(FakeOllamaClient):
    def chat(self, payload):
        self.requests.append(payload)
        return {"message": {"content": "not json"}, "prompt_eval_count": 1, "eval_count": 1}


class DecompositionLLMTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_decomposition_llm_contract()
        cls.inputs = load_frozen_dev_inputs(ROOT, cls.contract)

    def test_contract_is_self_hashed_and_fail_closed(self):
        changed = copy.deepcopy(self.contract)
        changed["input_policy"]["assisted_silver_included"] = True
        with self.assertRaises(DecompositionLLMError):
            validate_decomposition_llm_contract(changed)

    def test_prompt_excludes_reference_and_item_resolution(self):
        item = self.inputs["source_package"]["items"][0]
        messages = build_messages(
            self.contract,
            self.inputs["concept_catalog"],
            self.inputs["annotation_guide"],
            item,
        )
        rendered = json.loads(messages[1]["content"])
        self.assertEqual(item["source_text"], rendered["criterion"]["source_text"])
        self.assertEqual(85, len(rendered["concept_catalog"]))
        self.assertNotIn("approved_resolution", messages[1]["content"])
        self.assertNotIn("reviewed_expression", messages[1]["content"])
        self.assertNotIn("draft_expression", messages[1]["content"])
        self.assertNotIn("independence_rules", rendered["common_annotation_guide"])
        self.assertNotIn("provenance_rules", rendered["common_annotation_guide"])

    def test_item_bound_schema_and_semantic_validation(self):
        item = self.inputs["source_package"]["items"][0]
        client = FakeOllamaClient(self.contract)
        response = client.chat(
            {
                "messages": build_messages(
                    self.contract,
                    self.inputs["concept_catalog"],
                    self.inputs["annotation_guide"],
                    item,
                )
            }
        )
        schema = item_bound_output_schema(
            self.contract, self.inputs["concept_catalog"], item
        )
        self.assertEqual(
            {"expression", "atom", "nonEmptyString", "typedValue", "timeWindow", "provenance", "sourceSpan"},
            set(schema["$defs"]),
        )
        expression, status, reason = parse_model_output(
            response["message"]["content"],
            schema,
            item=item,
            catalog=self.inputs["concept_catalog"],
            contract=self.contract,
        )
        self.assertEqual("valid", status)
        self.assertIsNone(reason)
        self.assertIsNotNone(expression)

    def test_complete_fake_run_and_disclosures(self):
        client = FakeOllamaClient(self.contract)
        prediction, report = run_decomposition_llm_dev(
            ROOT,
            client=client,
            generated_at="2026-09-02T00:00:00Z",
            code_commit="a" * 40,
            hardware={"fixture": True},
        )
        validate_prediction_artifact(prediction, self.inputs)
        validate_comparison_report(report)
        self.assertEqual(40, len(client.requests))
        self.assertEqual(40, report["owner_review_outcome"]["accepted_unchanged"])
        self.assertEqual(0, report["owner_review_outcome"]["review_notes"])
        self.assertEqual(8, report["information_asymmetry"]["item_count"])
        self.assertFalse(report["claim_boundaries"]["decomposition_accuracy"])
        self.assertIn("not accuracy", report["model_roles"]["relationship"])
        markdown = render_comparison_markdown(report)
        self.assertIn("40/40 accepted unchanged", markdown)
        self.assertIn("rubber-stamp risk", markdown)

    def test_invalid_outputs_remain_in_denominator(self):
        prediction, report = run_decomposition_llm_dev(
            ROOT,
            client=InvalidOutputClient(self.contract),
            generated_at="2026-09-02T00:00:00Z",
            code_commit="b" * 40,
            hardware={"fixture": True},
        )
        self.assertEqual({"schema_invalid": 40}, report["failure_counts"])
        self.assertEqual(40, report["overall_metrics"]["criteria"])
        self.assertEqual(0, report["overall_metrics"]["schema_valid_outputs"])
        self.assertEqual(0, report["overall_metrics"]["semantic_valid_outputs"])
        self.assertEqual(0.0, report["overall_metrics"]["atom_micro_f1"])
        self.assertTrue(all(item["expression"] is None for item in prediction["predictions"]))

    def test_write_is_exclusive(self):
        prediction, report = run_decomposition_llm_dev(
            ROOT,
            client=FakeOllamaClient(self.contract),
            generated_at="2026-09-02T00:00:00Z",
            code_commit="c" * 40,
            hardware={"fixture": True},
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            write_decomposition_llm_run(prediction, report, output)
            with self.assertRaises(FileExistsError):
                write_decomposition_llm_run(prediction, report, output)


if __name__ == "__main__":
    unittest.main()
