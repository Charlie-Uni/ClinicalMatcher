import copy
import unittest
from datetime import date

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.model_fact_verifier import (
    CRITERION_FIELD_MISMATCH,
    MAPPED,
    MISSING_EVIDENCE,
    MODEL_UNKNOWN,
    UNKNOWN_EVIDENCE,
    verify_model_fact_for_criterion,
)
from clinical_matcher.models import (
    AtomicCondition,
    AtomProvenance,
    ComparisonOperator,
    ConditionExpression,
    Criterion,
    CriterionSource,
    CriterionType,
    Decision,
    DecompositionMethod,
    Evidence,
    ExpressionType,
    FactSelection,
    SourceSpan,
    TypedValue,
    ValueType,
)


PATIENT_ID = "patient-0123456789abcdef01234567"
EVIDENCE_ID = "evidence-0123456789abcdef01234567-000"


def question(catalog, question_type):
    return next(
        item
        for item in catalog["questions"]
        if item["question_type"] == question_type
    )


def criterion_for(
    fact_field,
    criterion_type,
    expected,
    value_type,
    operator=ComparisonOperator.EQ,
    unit=None,
):
    source_id = f"source-{criterion_type.value}"
    source_text = "Synthetic explicit criterion."
    return Criterion(
        criterion_id=f"criterion-{criterion_type.value}-{fact_field}",
        criterion_type=criterion_type,
        description=source_text,
        source=CriterionSource(
            source_id=source_id,
            source_text=source_text,
            section=criterion_type,
            document_version="synthetic-v1",
        ),
        expression=ConditionExpression(
            expression_type=ExpressionType.ATOM,
            atom=AtomicCondition(
                condition_id=f"atom-{criterion_type.value}-{fact_field}",
                field=fact_field,
                operator=operator,
                expected=TypedValue(value_type, expected, unit),
                fact_selection=FactSelection.ANY,
                provenance=AtomProvenance(
                    source_id=source_id,
                    source_span=SourceSpan(0, len(source_text)),
                    method=DecompositionMethod.HUMAN,
                ),
            ),
        ),
        hard=True,
    )


def prediction_set(question_item, *, status, value, evidence_ids, unit=None):
    unknown = status == "unknown"
    return {
        "prediction_set_version": "1.2.0",
        "benchmark_sha256": "a" * 64,
        "split_manifest_sha256": "b" * 64,
        "split_name": "validation",
        "model_id": "synthetic-model",
        "prompt_version": "synthetic-prompt-v1",
        "inference_config_sha256": "c" * 64,
        "generated_at": "2026-01-01T00:00:00Z",
        "code_commit": "d" * 40,
        "predictions": [
            {
                "patient_id": PATIENT_ID,
                "question_id": question_item["question_id"],
                "question_type": question_item["question_type"],
                "fact_status": status,
                "value": value,
                "unit": unit,
                "abstained": unknown,
                "abstention_reason": "model_returned_unknown" if unknown else None,
                "evidence_ids": evidence_ids,
                "trace_ids": ["synthetic.model"],
            }
        ],
    }


class ModelFactVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()
        cls.boolean_question = question(cls.catalog, "boolean")
        cls.numeric_question = question(cls.catalog, "numeric")
        cls.evidence = Evidence(
            evidence_id=EVIDENCE_ID,
            source_id="synthetic-note",
            text="Synthetic evidence only.",
        )

    def verify(self, document, criterion, evidence=None):
        return verify_model_fact_for_criterion(
            prediction_set=document,
            patient_id=PATIENT_ID,
            question_id=document["predictions"][0]["question_id"],
            criterion=criterion,
            patient_evidence=(self.evidence,) if evidence is None else evidence,
            index_date=date(2026, 1, 1),
            catalog=self.catalog,
        )

    def test_adverse_positive_fact_excludes_for_exclusion_criterion(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="present",
            value=True,
            evidence_ids=[EVIDENCE_ID],
        )
        exclusion = criterion_for(
            field, CriterionType.EXCLUSION, True, ValueType.BOOLEAN
        )

        result = self.verify(document, exclusion)

        self.assertEqual(MAPPED, result.mapping_status)
        self.assertIsNotNone(result.fact)
        self.assertEqual(Decision.INELIGIBLE, result.criterion_decision.decision)
        self.assertEqual((EVIDENCE_ID,), result.criterion_decision.evidence_ids)

    def test_same_fact_reverses_between_inclusion_and_exclusion(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="present",
            value=True,
            evidence_ids=[EVIDENCE_ID],
        )
        inclusion = criterion_for(
            field, CriterionType.INCLUSION, True, ValueType.BOOLEAN
        )
        exclusion = criterion_for(
            field, CriterionType.EXCLUSION, True, ValueType.BOOLEAN
        )

        inclusion_result = self.verify(document, inclusion)
        exclusion_result = self.verify(document, exclusion)

        self.assertEqual(
            Decision.ELIGIBLE, inclusion_result.criterion_decision.decision
        )
        self.assertEqual(
            Decision.INELIGIBLE, exclusion_result.criterion_decision.decision
        )

    def test_numeric_value_is_compared_by_typed_verifier(self):
        field = self.numeric_question["fact_field"]
        document = prediction_set(
            self.numeric_question,
            status="present",
            value=45,
            evidence_ids=[EVIDENCE_ID],
        )
        minimum = criterion_for(
            field,
            CriterionType.INCLUSION,
            50,
            ValueType.NUMBER,
            ComparisonOperator.GE,
        )

        result = self.verify(document, minimum)

        self.assertEqual(MAPPED, result.mapping_status)
        self.assertEqual(Decision.INELIGIBLE, result.criterion_decision.decision)

    def test_numeric_unit_is_preserved_and_mismatch_stays_unknown(self):
        field = self.numeric_question["fact_field"]
        document = prediction_set(
            self.numeric_question,
            status="present",
            value=90,
            evidence_ids=[EVIDENCE_ID],
            unit="umol/L",
        )
        threshold = criterion_for(
            field,
            CriterionType.INCLUSION,
            50,
            ValueType.NUMBER,
            ComparisonOperator.GE,
            unit="mg/dL",
        )

        result = self.verify(document, threshold)

        self.assertEqual(MAPPED, result.mapping_status)
        self.assertEqual("umol/L", result.fact.value.unit)
        self.assertEqual(Decision.UNKNOWN, result.criterion_decision.decision)
        self.assertIn(
            "unit mismatch", result.criterion_decision.atomic_decisions[0].reason
        )

    def test_model_unknown_remains_unknown_without_a_fact(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="unknown",
            value=None,
            evidence_ids=[],
        )
        criterion = criterion_for(
            field, CriterionType.INCLUSION, True, ValueType.BOOLEAN
        )

        result = self.verify(document, criterion)

        self.assertEqual(MODEL_UNKNOWN, result.mapping_status)
        self.assertIsNone(result.fact)
        self.assertEqual(Decision.UNKNOWN, result.criterion_decision.decision)

    def test_missing_evidence_cannot_create_a_known_fact(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="present",
            value=True,
            evidence_ids=[],
        )
        criterion = criterion_for(
            field, CriterionType.INCLUSION, True, ValueType.BOOLEAN
        )

        result = self.verify(document, criterion)

        self.assertEqual(MISSING_EVIDENCE, result.mapping_status)
        self.assertIsNone(result.fact)
        self.assertEqual(Decision.UNKNOWN, result.criterion_decision.decision)
        self.assertEqual((), result.criterion_decision.evidence_ids)

    def test_unknown_evidence_cannot_create_a_known_fact(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="present",
            value=True,
            evidence_ids=[EVIDENCE_ID],
        )
        criterion = criterion_for(
            field, CriterionType.INCLUSION, True, ValueType.BOOLEAN
        )

        result = self.verify(document, criterion, evidence=())

        self.assertEqual(UNKNOWN_EVIDENCE, result.mapping_status)
        self.assertIsNone(result.fact)
        self.assertEqual(Decision.UNKNOWN, result.criterion_decision.decision)

    def test_fact_is_not_applied_to_an_unrelated_criterion(self):
        document = prediction_set(
            self.boolean_question,
            status="present",
            value=True,
            evidence_ids=[EVIDENCE_ID],
        )
        criterion = criterion_for(
            "different_field",
            CriterionType.INCLUSION,
            True,
            ValueType.BOOLEAN,
        )

        result = self.verify(document, criterion)

        self.assertEqual(CRITERION_FIELD_MISMATCH, result.mapping_status)
        self.assertIsNone(result.fact)
        self.assertEqual(Decision.UNKNOWN, result.criterion_decision.decision)

    def test_schema_invalid_uncertainty_is_rejected_before_mapping(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="unknown",
            value=None,
            evidence_ids=[],
        )
        document["predictions"][0]["abstained"] = False
        criterion = criterion_for(
            field, CriterionType.INCLUSION, True, ValueType.BOOLEAN
        )

        with self.assertRaises(ValueError):
            self.verify(document, criterion)

    def test_ambiguous_patient_question_binding_is_rejected(self):
        field = self.boolean_question["fact_field"]
        document = prediction_set(
            self.boolean_question,
            status="present",
            value=True,
            evidence_ids=[EVIDENCE_ID],
        )
        duplicate = copy.deepcopy(document["predictions"][0])
        document["predictions"].append(duplicate)
        criterion = criterion_for(
            field, CriterionType.INCLUSION, True, ValueType.BOOLEAN
        )

        with self.assertRaises(ValueError):
            self.verify(document, criterion)


if __name__ == "__main__":
    unittest.main()
