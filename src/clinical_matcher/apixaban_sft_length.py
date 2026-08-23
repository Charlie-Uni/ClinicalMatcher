import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import serialized_document_sha256
from .apixaban_calibration import validate_apixaban_calibration_reservation
from .apixaban_contract import load_question_catalog, question_index
from .apixaban_sft import validate_apixaban_sft_input_plan
from .apixaban_sft_contract import (
    INPUT_PLAN_VERSION,
    apixaban_sft_contract_sha256,
    build_apixaban_sft_prompt_messages,
    load_apixaban_sft_length_contract,
    nearest_rank_percentile,
    rendered_chat_token_count,
    sha256_file,
)
from .apixaban_split import validate_apixaban_split_manifest
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


LENGTH_REPORT_VERSION = "1.0.0"
LENGTH_REPORT_SCHEMA = "schemas/apixaban-sft-length-report-1.0.0.schema.json"


class ApixabanSFTLengthError(ValueError):
    """Raised when P5 SFT length analysis violates its frozen boundary."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _self_hash(document: Mapping[str, Any], field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def _summary(values: Sequence[int]) -> Dict[str, Any]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "values_sha256": canonical_sha256(ordered),
        "minimum": ordered[0],
        "p50": nearest_rank_percentile(ordered, 50),
        "p90": nearest_rank_percentile(ordered, 90),
        "p95": nearest_rank_percentile(ordered, 95),
        "p99": nearest_rank_percentile(ordered, 99),
        "maximum": ordered[-1],
    }


def _candidate_input_sha256(
    *,
    input_policy_id: str,
    prompt_version: str,
    system_instruction: str,
    rows: Sequence[Mapping[str, Any]],
) -> str:
    return canonical_sha256(
        {
            "input_policy_id": input_policy_id,
            "prompt_version": prompt_version,
            "system_instruction_sha256": hashlib.sha256(
                system_instruction.encode("utf-8")
            ).hexdigest(),
            "rows": list(rows),
        }
    )


def verify_frozen_tokenizer_directory(
    tokenizer_directory: Path, contract: Mapping[str, Any]
) -> None:
    if tokenizer_directory.is_symlink() or not tokenizer_directory.is_dir():
        raise ApixabanSFTLengthError(
            "Frozen tokenizer path must be a real local directory"
        )
    for filename, expected in contract["tokenizer"]["files"].items():
        path = tokenizer_directory / filename
        if path.is_symlink() or not path.is_file():
            raise ApixabanSFTLengthError(
                f"Frozen tokenizer file is missing or symbolic: {filename}"
            )
        if sha256_file(path) != expected:
            raise ApixabanSFTLengthError(
                f"Frozen tokenizer file hash mismatch: {filename}"
            )
    configuration = json.loads(
        (tokenizer_directory / "tokenizer_config.json").read_text(
            encoding="utf-8"
        )
    )
    template = configuration.get("chat_template")
    if not isinstance(template, str):
        raise ApixabanSFTLengthError("Frozen tokenizer has no chat template")
    observed = hashlib.sha256(template.encode("utf-8")).hexdigest()
    if observed != contract["tokenizer"]["chat_template_sha256"]:
        raise ApixabanSFTLengthError("Frozen chat-template hash mismatch")


def load_frozen_apixaban_sft_tokenizer(tokenizer_directory: Path) -> Any:
    contract = load_apixaban_sft_length_contract()
    verify_frozen_tokenizer_directory(tokenizer_directory, contract)
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise ApixabanSFTLengthError(
            "The separate MLX environment with transformers is required"
        ) from error
    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_directory),
        local_files_only=True,
        trust_remote_code=False,
    )
    template = getattr(tokenizer, "chat_template", None)
    if not isinstance(template, str) or hashlib.sha256(
        template.encode("utf-8")
    ).hexdigest() != contract["tokenizer"]["chat_template_sha256"]:
        raise ApixabanSFTLengthError(
            "Loaded tokenizer chat template differs from the frozen artifact"
        )
    return tokenizer


def _source_bound_population(
    staging_corpus: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
) -> Tuple[Dict[str, Mapping[str, Any]], set[str]]:
    validate_apixaban_staging_corpus(staging_corpus)
    patients = {
        patient["patient_id"]: patient for patient in staging_corpus["patients"]
    }
    validate_apixaban_split_manifest(
        split_manifest, expected_patient_ids=patients.keys()
    )
    if split_manifest["status"] != "frozen" or not split_manifest["freeze"][
        "test_locked"
    ]:
        raise ApixabanSFTLengthError(
            "SFT length analysis requires a frozen locked split"
        )
    validate_apixaban_calibration_reservation(
        calibration_reservation, split_manifest
    )
    staging_hash = serialized_document_sha256(staging_corpus)
    if staging_hash != split_manifest["dataset"]["staging_corpus_sha256"]:
        raise ApixabanSFTLengthError(
            "SFT length staging corpus differs from the frozen split"
        )
    train_fit = set(
        calibration_reservation["partitions"]["train_fit"]["patient_ids"]
    )
    held_out = set(
        calibration_reservation["partitions"]["calibration_only"]["patient_ids"]
    )
    held_out.update(split_manifest["splits"]["validation"]["patient_ids"])
    held_out.update(split_manifest["splits"]["test"]["patient_ids"])
    if not train_fit or train_fit & held_out:
        raise ApixabanSFTLengthError(
            "SFT length population crosses a holdout boundary"
        )
    if not train_fit.issubset(patients):
        raise ApixabanSFTLengthError(
            "SFT length train-fit patient is missing from staging"
        )
    return patients, train_fit


def build_apixaban_sft_length_report(
    staging_corpus: Dict[str, Any],
    split_manifest: Dict[str, Any],
    calibration_reservation: Dict[str, Any],
    tokenizer: Any,
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: str,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    if not generation_command.strip():
        raise ApixabanSFTLengthError("Length generation command is required")
    contract = load_apixaban_sft_length_contract()
    patients, train_fit = _source_bound_population(
        staging_corpus, split_manifest, calibration_reservation
    )
    catalog = load_question_catalog()
    questions = question_index(catalog)
    input_policy = contract["input_policy"]
    prompt = contract["prompt"]
    length_policy = contract["length_policy"]

    plan_rows = []
    prompt_lengths = []
    for patient_id in sorted(train_fit):
        evidence = patients[patient_id]["evidence"]
        evidence_ids = [item["evidence_id"] for item in evidence]
        if not evidence_ids:
            raise ApixabanSFTLengthError(
                "Every train-fit patient must expose at least one complete chunk"
            )
        for question_id in sorted(questions):
            question = questions[question_id]
            plan_rows.append(
                {
                    "patient_id": patient_id,
                    "question_id": question_id,
                    "evidence_ids": evidence_ids,
                }
            )
            messages = build_apixaban_sft_prompt_messages(
                question, evidence, prompt["system_instruction"]
            )
            prompt_lengths.append(
                rendered_chat_token_count(
                    tokenizer, messages, add_generation_prompt=True
                )
            )

    expected_rows = len(train_fit) * len(questions)
    if len(plan_rows) != expected_rows or len(prompt_lengths) != expected_rows:
        raise ApixabanSFTLengthError(
            "SFT length report does not cover the full train-fit question grid"
        )
    reserve = length_policy["output_reserve_tokens"]
    reserved_lengths = [value + reserve for value in prompt_lengths]
    tier_coverage = []
    selected = None
    for tier in length_policy["context_tiers"]:
        fit = sum(value <= tier for value in reserved_lengths)
        tier_coverage.append(
            {
                "context_tier": tier,
                "fit_row_count": fit,
                "overflow_row_count": expected_rows - fit,
            }
        )
        if selected is None and fit == expected_rows:
            selected = tier

    candidate_hash = _candidate_input_sha256(
        input_policy_id=input_policy["input_policy_id"],
        prompt_version=prompt["prompt_version"],
        system_instruction=prompt["system_instruction"],
        rows=plan_rows,
    )
    report: Dict[str, Any] = {
        "apixaban_sft_length_report_version": LENGTH_REPORT_VERSION,
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command,
        "source": {
            "staging_corpus_sha256": serialized_document_sha256(staging_corpus),
            "benchmark_sha256": split_manifest["dataset"]["benchmark_sha256"],
            "split_manifest_sha256": split_manifest["manifest_sha256"],
            "calibration_reservation_sha256": calibration_reservation[
                "manifest_sha256"
            ],
            "question_catalog_sha256": catalog["catalog_sha256"],
            "candidate_input_sha256": candidate_hash,
        },
        "contract": {
            "contract_sha256": apixaban_sft_contract_sha256(contract),
            "model_id": contract["model"]["model_id"],
            "model_revision": contract["model"]["revision"],
            "tokenizer_file_sha256": contract["tokenizer"]["files"][
                "tokenizer.json"
            ],
            "tokenizer_config_sha256": contract["tokenizer"]["files"][
                "tokenizer_config.json"
            ],
            "chat_template_sha256": contract["tokenizer"][
                "chat_template_sha256"
            ],
            "input_policy_id": input_policy["input_policy_id"],
            "prompt_version": prompt["prompt_version"],
            "system_instruction_sha256": hashlib.sha256(
                prompt["system_instruction"].encode("utf-8")
            ).hexdigest(),
            "output_reserve_tokens": reserve,
            "fit_formula": length_policy["fit_formula"],
        },
        "population": {
            "partition": "train_fit",
            "patient_count": len(train_fit),
            "question_count": len(questions),
            "row_count": expected_rows,
            "calibration_patients_used": False,
            "validation_patients_used": False,
            "test_patients_used": False,
            "labels_used": False,
        },
        "lengths": {
            "prompt_tokens": _summary(prompt_lengths),
            "reserved_total_tokens": _summary(reserved_lengths),
        },
        "tier_coverage": tier_coverage,
        "selection": {
            "rule": length_policy["selection_rule"],
            "status": "selected" if selected is not None else "no_approved_tier_fits",
            "selected_context_tier": selected,
            "holdout_overflow_policy": length_policy[
                "holdout_overflow_policy"
            ],
        },
        "restrictions": {
            "contains_restricted_aggregate": True,
            "contains_patient_text": False,
            "contains_patient_ids": False,
            "contains_row_lengths": False,
            "owner_only": True,
            "online_upload_permitted": False,
        },
    }
    report["manifest_sha256"] = _self_hash(report, "manifest_sha256")
    validate_apixaban_sft_length_report(report)

    if selected is None:
        return report, None
    context = {
        "length_report_sha256": report["manifest_sha256"],
        "model_id": contract["model"]["model_id"],
        "model_revision": contract["model"]["revision"],
        "tokenizer_sha256": contract["tokenizer"]["files"]["tokenizer.json"],
        "tokenizer_config_sha256": contract["tokenizer"]["files"][
            "tokenizer_config.json"
        ],
        "chat_template_sha256": contract["tokenizer"][
            "chat_template_sha256"
        ],
        "output_reserve_tokens": reserve,
        "max_seq_len": selected,
        "overflow_policy": length_policy["holdout_overflow_policy"],
    }
    input_plan = {
        "input_plan_version": INPUT_PLAN_VERSION,
        "input_policy_id": input_policy["input_policy_id"],
        "input_policy_sha256": "pending",
        "prompt_version": prompt["prompt_version"],
        "system_instruction": prompt["system_instruction"],
        "context": context,
        "rows": plan_rows,
    }
    input_plan["input_policy_sha256"] = _self_hash(
        input_plan, "input_policy_sha256"
    )
    validate_apixaban_sft_input_plan(input_plan)
    return report, input_plan


def validate_apixaban_sft_length_report(document: Dict[str, Any]) -> None:
    validate_document(document, LENGTH_REPORT_SCHEMA)
    if _self_hash(document, "manifest_sha256") != document["manifest_sha256"]:
        raise ApixabanSFTLengthError("SFT length-report hash mismatch")
    contract = load_apixaban_sft_length_contract()
    expected_contract = {
        "contract_sha256": apixaban_sft_contract_sha256(contract),
        "model_id": contract["model"]["model_id"],
        "model_revision": contract["model"]["revision"],
        "tokenizer_file_sha256": contract["tokenizer"]["files"]["tokenizer.json"],
        "tokenizer_config_sha256": contract["tokenizer"]["files"][
            "tokenizer_config.json"
        ],
        "chat_template_sha256": contract["tokenizer"]["chat_template_sha256"],
        "input_policy_id": contract["input_policy"]["input_policy_id"],
        "prompt_version": contract["prompt"]["prompt_version"],
        "system_instruction_sha256": hashlib.sha256(
            contract["prompt"]["system_instruction"].encode("utf-8")
        ).hexdigest(),
        "output_reserve_tokens": contract["length_policy"][
            "output_reserve_tokens"
        ],
        "fit_formula": contract["length_policy"]["fit_formula"],
    }
    if document["contract"] != expected_contract:
        raise ApixabanSFTLengthError(
            "SFT length report differs from the frozen contract"
        )
    if document["source"]["question_catalog_sha256"] != load_question_catalog()[
        "catalog_sha256"
    ]:
        raise ApixabanSFTLengthError(
            "SFT length report question catalog is not frozen"
        )
    population = document["population"]
    row_count = population["row_count"]
    if row_count != population["patient_count"] * population["question_count"]:
        raise ApixabanSFTLengthError("SFT length population does not reconcile")
    for summary in document["lengths"].values():
        if summary["count"] != row_count:
            raise ApixabanSFTLengthError("SFT length summary count is incorrect")
        ordered = [
            summary["minimum"],
            summary["p50"],
            summary["p90"],
            summary["p95"],
            summary["p99"],
            summary["maximum"],
        ]
        if ordered != sorted(ordered):
            raise ApixabanSFTLengthError("SFT length percentiles are not monotonic")
    prompt_summary = document["lengths"]["prompt_tokens"]
    reserved_summary = document["lengths"]["reserved_total_tokens"]
    reserve = contract["length_policy"]["output_reserve_tokens"]
    for field in ("minimum", "p50", "p90", "p95", "p99", "maximum"):
        if reserved_summary[field] != prompt_summary[field] + reserve:
            raise ApixabanSFTLengthError(
                "SFT reserved-length summary does not include the frozen reserve"
            )
    tiers = document["tier_coverage"]
    expected_tiers = contract["length_policy"]["context_tiers"]
    if [row["context_tier"] for row in tiers] != expected_tiers:
        raise ApixabanSFTLengthError("SFT context tiers differ from approval")
    prior_fit = -1
    fully_fitting = []
    for row in tiers:
        if row["fit_row_count"] + row["overflow_row_count"] != row_count:
            raise ApixabanSFTLengthError("SFT tier coverage does not reconcile")
        if row["fit_row_count"] < prior_fit:
            raise ApixabanSFTLengthError("SFT tier fit counts must be monotonic")
        prior_fit = row["fit_row_count"]
        if row["fit_row_count"] == row_count:
            fully_fitting.append(row["context_tier"])
    expected_selected = min(fully_fitting) if fully_fitting else None
    if document["selection"]["selected_context_tier"] != expected_selected:
        raise ApixabanSFTLengthError("SFT selected tier is not the smallest full fit")
    expected_status = "selected" if expected_selected else "no_approved_tier_fits"
    if document["selection"]["status"] != expected_status:
        raise ApixabanSFTLengthError("SFT length selection status is incorrect")


def write_apixaban_sft_length_outputs(
    report: Dict[str, Any],
    input_plan: Optional[Dict[str, Any]],
    output_directory: Path,
) -> Tuple[Path, ...]:
    validate_apixaban_sft_length_report(report)
    if input_plan is not None:
        validate_apixaban_sft_input_plan(input_plan)
        if input_plan["context"]["length_report_sha256"] != report[
            "manifest_sha256"
        ]:
            raise ApixabanSFTLengthError(
                "SFT input plan is not bound to the length report"
            )
    assert_restricted_local_path(output_directory)
    paths = [output_directory / "length-report.json"]
    documents = [report]
    if input_plan is not None:
        paths.append(output_directory / "input-plan.json")
        documents.append(input_plan)
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite restricted SFT length output: "
            + ", ".join(str(path) for path in existing)
        )
    output_directory.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for path, document in zip(paths, documents):
            payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            )
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return tuple(paths)
