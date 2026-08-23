import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import (
    serialized_document_sha256,
    validate_apixaban_benchmark,
)
from .apixaban_calibration import (
    validate_apixaban_calibration_reservation,
)
from .apixaban_contract import (
    known_fact_allows_empty_evidence,
    load_question_catalog,
    question_index,
)
from .apixaban_split import validate_apixaban_split_manifest
from .apixaban_sft_contract import (
    INPUT_PLAN_VERSION,
    assert_apixaban_sft_sequence_fits,
    build_apixaban_sft_prompt_messages,
    load_apixaban_sft_length_contract,
)
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


SFT_RECORD_VERSION = "1.0.0"
SFT_RECORD_SCHEMA = "schemas/apixaban-sft-record-1.0.0.schema.json"
SFT_EXPORT_MANIFEST_VERSION = "1.1.0"
SFT_EXPORT_MANIFEST_SCHEMA = (
    "schemas/apixaban-sft-export-manifest-1.1.0.schema.json"
)
ACCEPTED_SILVER_VERSION = "1.0.0"
ROW_POLICY_VERSION = "1.0.0"


class ApixabanSFTError(ValueError):
    """Raised when an SFT export violates its frozen data boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any], field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_id(
    patient_id: str, question_id: str, input_policy_sha256: str
) -> str:
    payload = (
        f"{SFT_RECORD_VERSION}\0{input_policy_sha256}\0{patient_id}\0"
        f"{question_id}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"apixaban-sft-{digest[:24]}"


def validate_apixaban_sft_input_plan(
    document: Mapping[str, Any],
) -> None:
    required = {
        "input_plan_version",
        "input_policy_id",
        "input_policy_sha256",
        "prompt_version",
        "system_instruction",
        "context",
        "rows",
    }
    if set(document) != required:
        raise ApixabanSFTError("SFT input-plan fields are incomplete")
    if document["input_plan_version"] != INPUT_PLAN_VERSION:
        raise ApixabanSFTError("Unsupported SFT input-plan version")
    for field in ("input_policy_id", "prompt_version", "system_instruction"):
        if not isinstance(document[field], str) or not document[field].strip():
            raise ApixabanSFTError(f"SFT input-plan {field} must be non-empty")
    contract = load_apixaban_sft_length_contract()
    if document["input_policy_id"] != contract["input_policy"]["input_policy_id"]:
        raise ApixabanSFTError("SFT input policy differs from the frozen contract")
    if document["prompt_version"] != contract["prompt"]["prompt_version"]:
        raise ApixabanSFTError("SFT prompt version differs from the frozen contract")
    if document["system_instruction"] != contract["prompt"]["system_instruction"]:
        raise ApixabanSFTError(
            "SFT system instruction differs from the frozen contract"
        )
    context = document["context"]
    if set(context) != {
        "length_report_sha256",
        "model_id",
        "model_revision",
        "tokenizer_sha256",
        "tokenizer_config_sha256",
        "chat_template_sha256",
        "output_reserve_tokens",
        "max_seq_len",
        "overflow_policy",
    }:
        raise ApixabanSFTError("SFT input-plan context fields are incomplete")
    expected_context = {
        "model_id": contract["model"]["model_id"],
        "model_revision": contract["model"]["revision"],
        "tokenizer_sha256": contract["tokenizer"]["files"]["tokenizer.json"],
        "tokenizer_config_sha256": contract["tokenizer"]["files"][
            "tokenizer_config.json"
        ],
        "chat_template_sha256": contract["tokenizer"]["chat_template_sha256"],
        "output_reserve_tokens": contract["length_policy"][
            "output_reserve_tokens"
        ],
        "overflow_policy": contract["length_policy"]["holdout_overflow_policy"],
    }
    for field, expected in expected_context.items():
        if context[field] != expected:
            raise ApixabanSFTError(
                f"SFT input-plan context {field} differs from approval"
            )
    for field in ("length_report_sha256",):
        if not isinstance(context[field], str) or not re.fullmatch(
            r"[0-9a-f]{64}", context[field]
        ):
            raise ApixabanSFTError(f"SFT input-plan context {field} is invalid")
    if context["max_seq_len"] not in contract["length_policy"]["context_tiers"]:
        raise ApixabanSFTError("SFT input-plan context tier is not approved")
    if _self_hash(document, "input_policy_sha256") != document[
        "input_policy_sha256"
    ]:
        raise ApixabanSFTError("SFT input-plan hash mismatch")
    seen = set()
    for row in document["rows"]:
        if set(row) != {"patient_id", "question_id", "evidence_ids"}:
            raise ApixabanSFTError("SFT input-plan row fields are incomplete")
        key = (row["patient_id"], row["question_id"])
        if key in seen:
            raise ApixabanSFTError("SFT input plan contains duplicate rows")
        seen.add(key)
        evidence_ids = row["evidence_ids"]
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ApixabanSFTError(
                "Every SFT input-plan row must expose at least one chunk"
            )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ApixabanSFTError(
                "SFT input-plan evidence IDs must be unique"
            )


def validate_accepted_silver(
    document: Mapping[str, Any], expected_source: str
) -> None:
    required = {
        "accepted_silver_version",
        "artifact_sha256",
        "source",
        "source_artifact_sha256",
        "quality_audit_sha256",
        "audit_status",
        "rows",
    }
    if set(document) != required:
        raise ApixabanSFTError("Accepted-silver fields are incomplete")
    if document["accepted_silver_version"] != ACCEPTED_SILVER_VERSION:
        raise ApixabanSFTError("Unsupported accepted-silver version")
    if document["source"] != expected_source or expected_source not in {
        "D",
        "E",
    }:
        raise ApixabanSFTError("Accepted-silver source is invalid")
    if document["audit_status"] != "passed_predeclared_thresholds":
        raise ApixabanSFTError(
            "Silver cannot enter SFT before its quality audit passes"
        )
    for field in (
        "artifact_sha256",
        "source_artifact_sha256",
        "quality_audit_sha256",
    ):
        value = document[field]
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ApixabanSFTError(f"Accepted-silver {field} is invalid")
    if _self_hash(document, "artifact_sha256") != document["artifact_sha256"]:
        raise ApixabanSFTError("Accepted-silver artifact hash mismatch")
    seen = set()
    row_fields = {
        "patient_id",
        "question_id",
        "fact_status",
        "value",
        "unit",
        "evidence_ids",
    }
    for row in document["rows"]:
        if set(row) != row_fields:
            raise ApixabanSFTError("Accepted-silver row fields are incomplete")
        key = (row["patient_id"], row["question_id"])
        if key in seen:
            raise ApixabanSFTError("Accepted-silver rows must be unique")
        seen.add(key)
        evidence_ids = row["evidence_ids"]
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ApixabanSFTError(
                "Accepted silver must contain at least one evidence ID"
            )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ApixabanSFTError(
                "Accepted-silver evidence IDs must be unique"
            )


def _silver_index(
    documents: Sequence[Mapping[str, Any]],
) -> Dict[Tuple[str, str], Tuple[str, Mapping[str, Any], Mapping[str, Any]]]:
    result: Dict[
        Tuple[str, str], Tuple[str, Mapping[str, Any], Mapping[str, Any]]
    ] = {}
    for source, document in zip(("D", "E"), documents):
        validate_accepted_silver(document, source)
        for row in document["rows"]:
            key = (row["patient_id"], row["question_id"])
            if key in result:
                raise ApixabanSFTError(
                    "Teacher E must not overlap accepted rule D silver"
                )
            result[key] = (source, document, row)
    return result


def validate_apixaban_sft_record(
    document: Dict[str, Any],
    catalog: Optional[Mapping[str, Any]] = None,
) -> None:
    validate_document(document, SFT_RECORD_SCHEMA)
    questions = question_index(dict(catalog) if catalog else None)
    question = questions.get(document["question_id"])
    if question is None:
        raise ApixabanSFTError("SFT record references an unknown question")
    if document["question_type"] != question["question_type"]:
        raise ApixabanSFTError("SFT record question type mismatch")
    if document["input"]["source_question"] != question["source_question"]:
        raise ApixabanSFTError("SFT source question differs from catalog")
    expected_record_id = _record_id(
        document["patient_id"],
        document["question_id"],
        document["input"]["input_policy_sha256"],
    )
    if document["record_id"] != expected_record_id:
        raise ApixabanSFTError("SFT record ID is not bound to its inputs")
    evidence = document["input"]["visible_evidence"]
    evidence_ids = [item["evidence_id"] for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ApixabanSFTError("Visible evidence IDs must be unique")
    if not set(document["target"]["evidence_ids"]).issubset(evidence_ids):
        raise ApixabanSFTError(
            "Target citation is not visible in the student input"
        )


def _target_from_assessment(
    assessment: Mapping[str, Any], evidence_ids: Sequence[str]
) -> Dict[str, Any]:
    return {
        "fact_status": assessment["fact_status"],
        "value": assessment["value"],
        "unit": assessment["unit"],
        "abstained": assessment["abstained"],
        "abstention_reason": assessment["abstention_reason"],
        "evidence_ids": list(evidence_ids),
    }


def _messages(
    record: Mapping[str, Any], system_instruction: str
) -> list[Dict[str, str]]:
    messages = build_apixaban_sft_prompt_messages(
        {
            "question_id": record["question_id"],
            "question_type": record["question_type"],
            "source_question": record["input"]["source_question"],
        },
        record["input"]["visible_evidence"],
        system_instruction,
    )
    return messages + [
        {
            "role": "assistant",
            "content": json.dumps(
                record["target"],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    ]


def _mlx_row(
    record: Mapping[str, Any], system_instruction: str
) -> Dict[str, Any]:
    return {"messages": _messages(record, system_instruction)}


def _medicalgpt_row(
    record: Mapping[str, Any], system_instruction: str
) -> Dict[str, Any]:
    role_map = {"system": "system", "user": "human", "assistant": "gpt"}
    return {
        "conversations": [
            {"from": role_map[item["role"]], "value": item["content"]}
            for item in _messages(record, system_instruction)
        ]
    }


def _assert_rendering_consistency(
    mlx_rows: Sequence[Mapping[str, Any]],
    medicalgpt_rows: Sequence[Mapping[str, Any]],
) -> None:
    reverse_roles = {"system": "system", "human": "user", "gpt": "assistant"}
    normalized = [
        {
            "messages": [
                {
                    "role": reverse_roles[item["from"]],
                    "content": item["value"],
                }
                for item in row["conversations"]
            ]
        }
        for row in medicalgpt_rows
    ]
    if list(mlx_rows) != normalized:
        raise ApixabanSFTError(
            "MLX and MedicalGPT compatibility rows render different messages"
        )


def build_apixaban_sft_export(
    staging_corpus: Dict[str, Any],
    benchmark: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
    input_plan: Dict[str, Any],
    accepted_d_silver: Dict[str, Any],
    accepted_e_silver: Optional[Dict[str, Any]] = None,
    *,
    tokenizer: Any,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: str,
) -> Tuple[
    list[Dict[str, Any]],
    list[Dict[str, Any]],
    list[Dict[str, Any]],
    Dict[str, Any],
]:
    if not generation_command.strip():
        raise ApixabanSFTError("SFT generation command must be recorded")
    validate_apixaban_staging_corpus(staging_corpus)
    validate_apixaban_benchmark(
        benchmark, required_source_sha256=None, required_counts=None
    )
    validate_apixaban_split_manifest(
        split_manifest, expected_patient_ids=benchmark["patient_ids"]
    )
    if split_manifest["status"] != "frozen" or not split_manifest["freeze"][
        "test_locked"
    ]:
        raise ApixabanSFTError("SFT export requires a frozen, locked split")
    validate_apixaban_calibration_reservation(
        calibration_reservation, split_manifest
    )
    validate_apixaban_sft_input_plan(input_plan)

    benchmark_sha256 = serialized_document_sha256(benchmark)
    staging_sha256 = serialized_document_sha256(staging_corpus)
    if benchmark_sha256 != split_manifest["dataset"]["benchmark_sha256"]:
        raise ApixabanSFTError("SFT benchmark does not match the frozen split")
    if staging_sha256 != split_manifest["dataset"]["staging_corpus_sha256"]:
        raise ApixabanSFTError("SFT staging corpus does not match the frozen split")

    catalog = load_question_catalog()
    questions = question_index(catalog)
    train_fit = set(
        calibration_reservation["partitions"]["train_fit"]["patient_ids"]
    )
    calibration_only = set(
        calibration_reservation["partitions"]["calibration_only"]["patient_ids"]
    )
    validation = set(split_manifest["splits"]["validation"]["patient_ids"])
    test = set(split_manifest["splits"]["test"]["patient_ids"])
    if train_fit & (calibration_only | validation | test):
        raise ApixabanSFTError("SFT train-fit membership crosses a holdout boundary")

    expected_pairs = {
        (patient_id, question_id)
        for patient_id in train_fit
        for question_id in questions
    }
    input_rows = {
        (row["patient_id"], row["question_id"]): row
        for row in input_plan["rows"]
    }
    if set(input_rows) != expected_pairs:
        raise ApixabanSFTError(
            "SFT input plan must cover exactly the train-fit patient-question grid"
        )

    patient_by_id = {
        patient["patient_id"]: patient for patient in staging_corpus["patients"]
    }
    if set(patient_by_id) != set(benchmark["patient_ids"]):
        raise ApixabanSFTError("Staging and benchmark patient membership differ")
    assessment_by_pair = {
        (item["patient_id"], item["question_id"]): item
        for item in benchmark["assessments"]
    }
    silver_documents = [accepted_d_silver]
    if accepted_e_silver is not None:
        silver_documents.append(accepted_e_silver)
    silver = _silver_index(silver_documents)
    if not set(silver).issubset(expected_pairs):
        raise ApixabanSFTError(
            "Accepted silver contains calibration, validation, test, or unknown rows"
        )

    records: list[Dict[str, Any]] = []
    per_question: Dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for patient_id, question_id in sorted(expected_pairs):
        patient = patient_by_id[patient_id]
        question = questions[question_id]
        assessment = assessment_by_pair[(patient_id, question_id)]
        visible_ids = input_rows[(patient_id, question_id)]["evidence_ids"]
        evidence_by_id = {
            item["evidence_id"]: item for item in patient["evidence"]
        }
        if visible_ids != list(evidence_by_id):
            raise ApixabanSFTError(
                "Input plan must expose every complete patient chunk in source order"
            )
        visible_evidence = [evidence_by_id[item] for item in visible_ids]
        counts = per_question[question_id]
        counts["eligible_pair_count"] += 1
        totals["eligible_pair_count"] += 1

        silver_entry = silver.get((patient_id, question_id))
        if assessment["fact_status"] == "unknown":
            if silver_entry is not None:
                raise ApixabanSFTError(
                    "Gold-unknown rows cannot carry accepted silver citations"
                )
            target = _target_from_assessment(assessment, ())
            supervision = {
                "row_policy": "gold_unknown_empty_evidence",
                "citation_required": False,
                "silver_source": None,
                "silver_source_artifact_sha256": None,
                "silver_quality_audit_sha256": None,
            }
            bucket = "unknown_empty_evidence_count"
        elif known_fact_allows_empty_evidence(question, assessment):
            if silver_entry is not None:
                raise ApixabanSFTError(
                    "The source-default absent exception must not use silver"
                )
            target = _target_from_assessment(assessment, ())
            supervision = {
                "row_policy": "source_default_absent_empty_evidence",
                "citation_required": False,
                "silver_source": None,
                "silver_source_artifact_sha256": None,
                "silver_quality_audit_sha256": None,
            }
            bucket = "default_absent_exception_count"
        elif silver_entry is None:
            counts["excluded_known_without_silver_count"] += 1
            totals["excluded_known_without_silver_count"] += 1
            continue
        else:
            source, silver_document, silver_row = silver_entry
            for field in ("fact_status", "value", "unit"):
                if silver_row[field] != assessment[field]:
                    raise ApixabanSFTError(
                        "Accepted silver typed value does not equal released gold"
                    )
            cited = silver_row["evidence_ids"]
            if not set(cited).issubset(evidence_by_id):
                raise ApixabanSFTError(
                    "Accepted silver cites evidence outside its patient"
                )
            if not set(cited).issubset(visible_ids):
                raise ApixabanSFTError(
                    "Accepted silver citation is not visible to the student"
                )
            target = _target_from_assessment(assessment, cited)
            supervision = {
                "row_policy": "accepted_audited_silver",
                "citation_required": True,
                "silver_source": source,
                "silver_source_artifact_sha256": silver_document[
                    "source_artifact_sha256"
                ],
                "silver_quality_audit_sha256": silver_document[
                    "quality_audit_sha256"
                ],
            }
            bucket = f"accepted_{source.lower()}_count"

        record = {
            "apixaban_sft_record_version": SFT_RECORD_VERSION,
            "record_id": _record_id(
                patient_id, question_id, input_plan["input_policy_sha256"]
            ),
            "patient_id": patient_id,
            "question_id": question_id,
            "question_type": question["question_type"],
            "input": {
                "input_policy_id": input_plan["input_policy_id"],
                "input_policy_sha256": input_plan["input_policy_sha256"],
                "prompt_version": input_plan["prompt_version"],
                "source_question": question["source_question"],
                "visible_evidence": visible_evidence,
            },
            "target": target,
            "supervision": supervision,
        }
        validate_apixaban_sft_record(record, catalog)
        records.append(record)
        counts["included_row_count"] += 1
        counts[bucket] += 1
        totals["included_row_count"] += 1
        totals[bucket] += 1

    if not records:
        raise ApixabanSFTError("SFT row policy excluded every training row")
    if set(silver) - {
        (record["patient_id"], record["question_id"]) for record in records
    }:
        raise ApixabanSFTError("Accepted silver was not consumed by the export")

    mlx_rows = [
        _mlx_row(record, input_plan["system_instruction"]) for record in records
    ]
    medicalgpt_rows = [
        _medicalgpt_row(record, input_plan["system_instruction"])
        for record in records
    ]
    _assert_rendering_consistency(mlx_rows, medicalgpt_rows)
    context = input_plan["context"]
    sequence_lengths = [
        assert_apixaban_sft_sequence_fits(
            tokenizer,
            row["messages"],
            max_seq_len=context["max_seq_len"],
            output_reserve_tokens=context["output_reserve_tokens"],
        )
        for row in mlx_rows
    ]

    canonical_payload = _jsonl_bytes(records)
    mlx_payload = _jsonl_bytes(mlx_rows)
    medicalgpt_payload = _jsonl_bytes(medicalgpt_rows)
    count_fields = (
        "eligible_pair_count",
        "included_row_count",
        "excluded_known_without_silver_count",
        "accepted_d_count",
        "accepted_e_count",
        "unknown_empty_evidence_count",
        "default_absent_exception_count",
    )
    manifest: Dict[str, Any] = {
        "apixaban_sft_export_manifest_version": SFT_EXPORT_MANIFEST_VERSION,
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command,
        "source": {
            "benchmark_sha256": benchmark_sha256,
            "staging_corpus_sha256": staging_sha256,
            "split_manifest_sha256": split_manifest["manifest_sha256"],
            "calibration_reservation_sha256": calibration_reservation[
                "manifest_sha256"
            ],
            "question_catalog_sha256": catalog["catalog_sha256"],
        },
        "input_contract": {
            "input_policy_id": input_plan["input_policy_id"],
            "input_policy_sha256": input_plan["input_policy_sha256"],
            "prompt_version": input_plan["prompt_version"],
            "system_instruction_sha256": hashlib.sha256(
                input_plan["system_instruction"].encode("utf-8")
            ).hexdigest(),
            **context,
        },
        "row_policy": {
            "version": ROW_POLICY_VERSION,
            "training_partition": "train_fit",
            "calibration_excluded": True,
            "validation_excluded": True,
            "test_excluded": True,
            "silver_priority": ["D", "E"],
            "uncovered_known_action": "exclude",
        },
        "counts": {
            "train_fit_patient_count": len(train_fit),
            **{field: totals[field] for field in count_fields},
        },
        "per_question": [
            {
                "question_id": question_id,
                **{field: per_question[question_id][field] for field in count_fields},
            }
            for question_id in sorted(questions)
        ],
        "outputs": {
            "canonical_jsonl_sha256": _sha256_bytes(canonical_payload),
            "mlx_jsonl_sha256": _sha256_bytes(mlx_payload),
            "medicalgpt_jsonl_sha256": _sha256_bytes(medicalgpt_payload),
        },
        "sequence_validation": {
            "row_count": len(sequence_lengths),
            "max_prompt_tokens": max(
                item["prompt_tokens"] for item in sequence_lengths
            ),
            "max_actual_target_tokens": max(
                item["target_tokens"] for item in sequence_lengths
            ),
            "max_full_tokens": max(
                item["full_tokens"] for item in sequence_lengths
            ),
            "every_target_within_reserve": True,
            "every_reserved_sequence_within_context": True,
        },
        "restrictions": {
            "contains_restricted_text": True,
            "owner_only": True,
            "online_upload_permitted": False,
            "silver_is_evidence_gold": False,
        },
    }
    manifest["manifest_sha256"] = _self_hash(manifest, "manifest_sha256")
    validate_apixaban_sft_export_manifest(manifest)
    return records, mlx_rows, medicalgpt_rows, manifest


def validate_apixaban_sft_export_manifest(document: Dict[str, Any]) -> None:
    validate_document(document, SFT_EXPORT_MANIFEST_SCHEMA)
    if _self_hash(document, "manifest_sha256") != document["manifest_sha256"]:
        raise ApixabanSFTError("SFT export manifest hash mismatch")
    counts = document["counts"]
    contract = load_apixaban_sft_length_contract()
    input_contract = document["input_contract"]
    expected_context = {
        "model_id": contract["model"]["model_id"],
        "model_revision": contract["model"]["revision"],
        "tokenizer_sha256": contract["tokenizer"]["files"]["tokenizer.json"],
        "tokenizer_config_sha256": contract["tokenizer"]["files"][
            "tokenizer_config.json"
        ],
        "chat_template_sha256": contract["tokenizer"]["chat_template_sha256"],
        "output_reserve_tokens": contract["length_policy"][
            "output_reserve_tokens"
        ],
        "overflow_policy": contract["length_policy"]["holdout_overflow_policy"],
    }
    for field, expected in expected_context.items():
        if input_contract[field] != expected:
            raise ApixabanSFTError(
                f"SFT export input contract {field} differs from approval"
            )
    if input_contract["max_seq_len"] not in contract["length_policy"][
        "context_tiers"
    ]:
        raise ApixabanSFTError("SFT export context tier is not approved")
    sequence = document["sequence_validation"]
    if sequence["row_count"] != counts["included_row_count"]:
        raise ApixabanSFTError(
            "SFT sequence-validation count differs from included rows"
        )
    if sequence["max_actual_target_tokens"] > input_contract[
        "output_reserve_tokens"
    ]:
        raise ApixabanSFTError("SFT target exceeds the frozen reserve")
    if (
        sequence["max_prompt_tokens"] + input_contract["output_reserve_tokens"]
        > input_contract["max_seq_len"]
    ):
        raise ApixabanSFTError("SFT reserved sequence exceeds its context tier")
    if sequence["max_full_tokens"] > input_contract["max_seq_len"]:
        raise ApixabanSFTError("SFT full sequence exceeds its context tier")
    if counts["included_row_count"] + counts[
        "excluded_known_without_silver_count"
    ] != counts["eligible_pair_count"]:
        raise ApixabanSFTError("SFT aggregate row counts do not reconcile")
    included_buckets = (
        counts["accepted_d_count"]
        + counts["accepted_e_count"]
        + counts["unknown_empty_evidence_count"]
        + counts["default_absent_exception_count"]
    )
    if included_buckets != counts["included_row_count"]:
        raise ApixabanSFTError("SFT included-row buckets do not reconcile")
    catalog_question_ids = sorted(question_index())
    question_rows = document["per_question"]
    if [row["question_id"] for row in question_rows] != catalog_question_ids:
        raise ApixabanSFTError(
            "SFT per-question report must cover the frozen catalog once"
        )
    for row in question_rows:
        if row["eligible_pair_count"] != counts["train_fit_patient_count"]:
            raise ApixabanSFTError(
                "SFT per-question population differs from train-fit membership"
            )
        if row["included_row_count"] + row[
            "excluded_known_without_silver_count"
        ] != row["eligible_pair_count"]:
            raise ApixabanSFTError(
                "SFT per-question row counts do not reconcile"
            )
        row_buckets = (
            row["accepted_d_count"]
            + row["accepted_e_count"]
            + row["unknown_empty_evidence_count"]
            + row["default_absent_exception_count"]
        )
        if row_buckets != row["included_row_count"]:
            raise ApixabanSFTError(
                "SFT per-question included buckets do not reconcile"
            )
    for field in (
        "eligible_pair_count",
        "included_row_count",
        "excluded_known_without_silver_count",
        "accepted_d_count",
        "accepted_e_count",
        "unknown_empty_evidence_count",
        "default_absent_exception_count",
    ):
        if sum(row[field] for row in document["per_question"]) != counts[field]:
            raise ApixabanSFTError(
                f"SFT per-question {field} counts do not reconcile"
            )


def write_apixaban_sft_export(
    records: Sequence[Mapping[str, Any]],
    mlx_rows: Sequence[Mapping[str, Any]],
    medicalgpt_rows: Sequence[Mapping[str, Any]],
    manifest: Dict[str, Any],
    output_directory: Path,
) -> Tuple[Path, Path, Path, Path]:
    validate_apixaban_sft_export_manifest(manifest)
    pairs = [(record["patient_id"], record["question_id"]) for record in records]
    record_ids = [record["record_id"] for record in records]
    if len(pairs) != len(set(pairs)) or len(record_ids) != len(set(record_ids)):
        raise ApixabanSFTError("Canonical SFT records must be unique")
    for record in records:
        validate_apixaban_sft_record(dict(record))
        contract = manifest["input_contract"]
        if (
            record["input"]["input_policy_id"] != contract["input_policy_id"]
            or record["input"]["input_policy_sha256"]
            != contract["input_policy_sha256"]
            or record["input"]["prompt_version"] != contract["prompt_version"]
        ):
            raise ApixabanSFTError(
                "Canonical row input contract differs from the export manifest"
            )
    if len(records) != manifest["counts"]["included_row_count"]:
        raise ApixabanSFTError("Canonical row count differs from the manifest")
    _assert_rendering_consistency(mlx_rows, medicalgpt_rows)
    assert_restricted_local_path(output_directory)
    paths = (
        output_directory / "canonical.jsonl",
        output_directory / "train.jsonl",
        output_directory / "medicalgpt.jsonl",
        output_directory / "manifest.json",
    )
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite restricted SFT output: "
            + ", ".join(str(path) for path in existing)
        )
    payloads = (
        _jsonl_bytes(records),
        _jsonl_bytes(mlx_rows),
        _jsonl_bytes(medicalgpt_rows),
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    expected_hashes = (
        manifest["outputs"]["canonical_jsonl_sha256"],
        manifest["outputs"]["mlx_jsonl_sha256"],
        manifest["outputs"]["medicalgpt_jsonl_sha256"],
    )
    if tuple(_sha256_bytes(payload) for payload in payloads[:3]) != expected_hashes:
        raise ApixabanSFTError("SFT output content does not match its manifest")
    output_directory.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for path, payload in zip(paths, payloads):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return paths
