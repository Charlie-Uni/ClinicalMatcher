import hashlib
import json
import os
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

from .apixaban_sft_contract import (
    build_apixaban_sft_prompt_messages,
    load_apixaban_sft_length_contract,
    rendered_chat_token_count,
)
from .splits import canonical_sha256


GATE_RESOURCE = "resources/p5-mlx-qlora-16k-gate-1.1.0.json"
EIGHT_K_PROBE_RESOURCE = "resources/p5-mlx-qlora-8k-probe-1.0.0.json"


class P5MLXGateError(ValueError):
    """Raised when the frozen local MLX feasibility gate is violated."""


def load_p5_mlx_gate_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(GATE_RESOURCE)
    document = json.loads(resource.read_text(encoding="utf-8"))
    validate_p5_mlx_gate_contract(document)
    return document


def load_p5_mlx_8k_probe_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(EIGHT_K_PROBE_RESOURCE)
    document = json.loads(resource.read_text(encoding="utf-8"))
    validate_p5_mlx_8k_probe_contract(document)
    return document


def validate_p5_mlx_gate_contract(document: Mapping[str, Any]) -> None:
    if set(document) != {
        "gate_contract_version",
        "model",
        "environment",
        "quantization",
        "lora",
        "optimizer",
        "training_shape",
        "dry_run",
        "loss_implementation",
        "fallback_revision",
    }:
        raise P5MLXGateError("P5 MLX gate-contract fields are incomplete")
    expected = {
        "gate_contract_version": "1.1.0",
        "model": {
            "model_id": "meta-llama/Llama-3.1-8B-Instruct",
            "revision": "0e9e39f249a16976918f6564b8830bc894c89659",
        },
        "environment": {"python": "3.11.16", "mlx": "0.31.2", "mlx_lm": "0.31.3"},
        "quantization": {"bits": 4, "group_size": 64, "mode": "affine"},
        "lora": {
            "fine_tune_type": "lora",
            "num_layers": 16,
            "rank": 8,
            "scale": 20.0,
            "dropout": 0.0,
            "target_module_keys": [
                "self_attn.q_proj",
                "self_attn.k_proj",
                "self_attn.v_proj",
                "self_attn.o_proj",
                "mlp.gate_proj",
                "mlp.up_proj",
                "mlp.down_proj",
            ],
        },
        "optimizer": {
            "implementation": "mlx.optimizers.Adam",
            "learning_rate": 1e-5,
            "betas": [0.9, 0.999],
            "eps": 1e-8,
            "bias_correction": False,
            "weight_decay": 0.0,
            "schedule": "constant",
            "warmup_steps": 0,
        },
        "training_shape": {
            "max_seq_length": 16384,
            "micro_batch_size": 1,
            "gradient_accumulation_steps": 4,
            "mask_prompt": True,
            "gradient_checkpointing": True,
            "seed": 17,
        },
        "dry_run": {
            "synthetic_only": True,
            "iterations": 8,
            "steps_per_report": 4,
            "external_reporting": False,
            "required_outputs": [
                "seconds_per_step",
                "supervised_tokens_per_second",
                "input_tokens_per_second",
                "peak_memory_gb",
                "peak_stage",
                "resolved_target_modules",
            ],
            "sequence_requirement": (
                "at_least_one_rendered_sequence_equals_16384_tokens"
            ),
            "parameter_change_policy": (
                "rerun_gate_before_restricted_training"
            ),
        },
        "loss_implementation": {
            "implementation_version": "1.0.0",
            "source": (
                "clinical_matcher.p5_mlx_completion_loss."
                "completion_only_projection_loss"
            ),
            "module_sha256": (
                "d95ca72cacbb63ec027c83324e015331dccbcdddcc8b9ffbe6e8f42cad518d60"
            ),
            "reference_source": "mlx_lm.tuner.trainer.default_loss",
            "stock_trainer": "mlx_lm.tuner.trainer.train",
            "objective": "pinned_mask_prompt_whole_completion_cross_entropy",
            "full_sequence_hidden_states": True,
            "prompt_token_logits_materialized": False,
            "projection_window_tokens": 544,
            "output_reserve_tokens": 512,
            "pinned_batch_padding_tokens": 32,
            "completion_internal_field_masking": False,
            "chunked_cross_entropy": False,
            "full_logits_materialized": False,
        },
        "fallback_revision": {
            "approved_on": "2026-08-24",
            "trigger_failed_gate_sha256": (
                "43937dea18fe54609c549edfd69ff8bedacfebd2e9131b5b0d8d2d79d080c2d5"
            ),
            "trigger_allocation_bytes": 17177772096,
            "metal_max_buffer_bytes": 14302248960,
            "supervision_semantics_changed": False,
            "field_selective_loss_remains_rejected": True,
            "failure_policy": "stop_and_require_new_owner_review",
        },
    }
    if dict(document) != expected:
        raise P5MLXGateError("P5 MLX gate contract differs from owner approval")


def validate_p5_mlx_8k_probe_contract(document: Mapping[str, Any]) -> None:
    base = load_p5_mlx_gate_contract()
    expected = {
        "probe_contract_version": "1.0.0",
        "approved_on": "2026-08-24",
        "source_gate_contract_sha256": p5_mlx_gate_contract_sha256(base),
        "scope": {
            "synthetic_only": True,
            "restricted_data_allowed": False,
            "changes_frozen_input_policy": False,
            "authorizes_fallback": False,
        },
        "evidence": {
            "attention_diagnostic_contract_sha256": (
                "912d3bf67ed491a86b7f03f0815152cdcfad174c0bf4359eaa4cb125d7741a8f"
            ),
            "allocation_result_manifest_sha256s": {
                "4096": (
                    "c4efb33ca479dc1bc1d3b5d05fc66bc276667770ac264124d5acc1d07ff4d470"
                ),
                "8192": (
                    "3b7d81a550da8351510884f672ea5c6a0fdb8e65890705cd2270c6ce7b44243c"
                ),
                "16384": (
                    "d15c756df1a48d95734ea8c66ceeeca98655cfdce520efb146eb7def977cb128"
                ),
            },
        },
        "model": dict(base["model"]),
        "environment": dict(base["environment"]),
        "quantization": dict(base["quantization"]),
        "lora": dict(base["lora"]),
        "optimizer": dict(base["optimizer"]),
        "training_shape": {
            **base["training_shape"],
            "max_seq_length": 8192,
        },
        "dry_run": {
            **base["dry_run"],
            "sequence_requirement": (
                "at_least_one_rendered_sequence_equals_8192_tokens"
            ),
        },
        "loss_implementation": dict(base["loss_implementation"]),
        "reference_budget": {
            "grid_rows_per_epoch": 1265,
            "seconds_per_step_rule": "maximum_of_two_report_windows",
            "wall_clock_formula": (
                "seconds_per_step_times_1265_divided_by_3600"
            ),
        },
        "length_screen": {
            "freeze_before_reading_owner_report": True,
            "context_tier": 8192,
            "original_grid_rows": 1265,
            "maximum_overflow_fraction": 0.05,
            "maximum_overflow_rows": 63,
            "per_question_denominator": (
                "original_train_fit_rows_for_that_question"
            ),
            "minimum_retained_fraction_per_question": 0.3,
            "minimum_retained_rows_per_question": 5,
            "screen_all_questions_without_labels": True,
            "later_silver_gate_is_not_replaced": True,
            "pass_rule": "all_overall_and_per_question_conditions_must_pass",
        },
        "stop_policy": (
            "probe_and_length_screen_then_return_to_owner_review_before_revision"
        ),
    }
    if dict(document) != expected:
        raise P5MLXGateError("P5 MLX 8K probe contract differs from owner approval")


def validate_p5_mlx_execution_contract(document: Mapping[str, Any]) -> None:
    if "gate_contract_version" in document:
        validate_p5_mlx_gate_contract(document)
        return
    if "probe_contract_version" in document:
        validate_p5_mlx_8k_probe_contract(document)
        return
    raise P5MLXGateError("Unknown P5 MLX execution contract")


def p5_mlx_execution_contract_sha256(contract: Mapping[str, Any]) -> str:
    validate_p5_mlx_execution_contract(contract)
    return canonical_sha256(dict(contract))


def p5_mlx_gate_contract_sha256(contract: Mapping[str, Any]) -> str:
    validate_p5_mlx_gate_contract(contract)
    return canonical_sha256(dict(contract))


def verify_p5_mlx_completion_loss_module(contract: Mapping[str, Any]) -> str:
    validate_p5_mlx_execution_contract(contract)
    from . import p5_mlx_completion_loss

    module_path = Path(p5_mlx_completion_loss.__file__).resolve(strict=True)
    observed = sha256_path(module_path)
    expected = contract["loss_implementation"]["module_sha256"]
    if observed != expected:
        raise P5MLXGateError("Completion-only loss module SHA-256 differs from gate")
    if (
        p5_mlx_completion_loss.LOSS_IMPLEMENTATION_VERSION
        != contract["loss_implementation"]["implementation_version"]
    ):
        raise P5MLXGateError("Completion-only loss implementation version differs")
    return observed


def _synthetic_messages(filler_repetitions: int) -> list[Dict[str, str]]:
    if filler_repetitions < 0:
        raise P5MLXGateError("Synthetic filler count cannot be negative")
    length_contract = load_apixaban_sft_length_contract()
    evidence_text = "Synthetic evidence." + " evidence" * filler_repetitions
    question = {
        "question_id": "synthetic-p5-memory-gate",
        "question_type": "boolean",
        "source_question": "Is the synthetic gate fact explicitly present?",
    }
    evidence = [
        {
            "evidence_id": "synthetic-evidence-001",
            "source_id": "synthetic-source-001",
            "source_span": {"start": 0, "end": len(evidence_text)},
            "text": evidence_text,
        }
    ]
    messages = build_apixaban_sft_prompt_messages(
        question,
        evidence,
        length_contract["prompt"]["system_instruction"],
    )
    target = {
        "abstained": False,
        "abstention_reason": None,
        "evidence_ids": ["synthetic-evidence-001"],
        "fact_status": "present",
        "unit": None,
        "value": True,
    }
    return messages + [
        {
            "role": "assistant",
            "content": json.dumps(
                target,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        }
    ]


def build_exact_length_synthetic_gate_rows(
    tokenizer: Any,
    *,
    row_count: int = 4,
    contract: Mapping[str, Any] | None = None,
) -> Tuple[list[Dict[str, Any]], int]:
    contract = dict(contract or load_p5_mlx_gate_contract())
    validate_p5_mlx_execution_contract(contract)
    target_length = contract["training_shape"]["max_seq_length"]
    if row_count < 1:
        raise P5MLXGateError("Synthetic gate requires at least one row")

    low = 0
    high = target_length * 2
    best_messages: Sequence[Mapping[str, str]] | None = None
    best_length = -1
    while low <= high:
        middle = (low + high) // 2
        messages = _synthetic_messages(middle)
        observed = rendered_chat_token_count(
            tokenizer, messages, add_generation_prompt=False
        )
        if observed <= target_length and observed > best_length:
            best_messages = messages
            best_length = observed
        if observed < target_length:
            low = middle + 1
        elif observed > target_length:
            high = middle - 1
        else:
            best_messages = messages
            best_length = observed
            break
    if best_messages is None or best_length != target_length:
        raise P5MLXGateError(
            f"Could not construct an exact {target_length:,}-token synthetic row"
        )
    rows = [{"messages": list(best_messages)} for _ in range(row_count)]
    return rows, best_length


def jsonl_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            (
                json.dumps(
                    row,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_directory(directory: Path) -> list[Dict[str, Any]]:
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise P5MLXGateError(f"Artifact path is not a directory: {directory}")
    inventory: list[Dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise P5MLXGateError(f"Artifact inventory rejects symlinks: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise P5MLXGateError(f"Artifact inventory rejects special files: {path}")
        relative = path.relative_to(directory).as_posix()
        inventory.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_path(path),
            }
        )
    if not inventory:
        raise P5MLXGateError(f"Artifact directory is empty: {directory}")
    return inventory


def inventory_sha256(inventory: Sequence[Mapping[str, Any]]) -> str:
    return canonical_sha256(list(inventory))


def verify_directory_inventory(
    directory: Path,
    expected_inventory: Sequence[Mapping[str, Any]],
) -> None:
    observed = inventory_directory(directory)
    if observed != list(expected_inventory):
        raise P5MLXGateError(f"Artifact inventory changed: {directory}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _self_hash(document: Mapping[str, Any], field: str = "manifest_sha256") -> str:
    unsigned = dict(document)
    unsigned.pop(field, None)
    return canonical_sha256(unsigned)


def build_p5_mlx_model_artifact_manifest(
    source_directory: Path,
    converted_directory: Path,
    *,
    mlx_version: str,
    mlx_lm_version: str,
    python_version: str,
    load_check_passed: bool,
    tokenizer_compatibility: Mapping[str, Any],
    generated_at: str | None = None,
) -> Dict[str, Any]:
    contract = load_p5_mlx_gate_contract()
    length_contract = load_apixaban_sft_length_contract()
    source_inventory = inventory_directory(source_directory)
    converted_inventory = inventory_directory(converted_directory)
    source_files = {item["path"] for item in source_inventory}
    source_weight_files = [
        "model-00001-of-00004.safetensors",
        "model-00002-of-00004.safetensors",
        "model-00003-of-00004.safetensors",
        "model-00004-of-00004.safetensors",
        "model.safetensors.index.json",
    ]
    if not set(source_weight_files).issubset(source_files):
        raise P5MLXGateError("Pinned source weight shard set is incomplete")
    for relative, expected_sha256 in length_contract["tokenizer"]["files"].items():
        source_path = source_directory / relative
        if not source_path.is_file() or sha256_path(source_path) != expected_sha256:
            raise P5MLXGateError(
                f"Source tokenizer/config file differs from the frozen pin: {relative}"
            )
    converted_files = {item["path"] for item in converted_inventory}
    if "config.json" not in converted_files or not any(
        path.endswith(".safetensors") for path in converted_files
    ):
        raise P5MLXGateError("Converted MLX artifact is incomplete")
    config = json.loads((converted_directory / "config.json").read_text("utf-8"))
    if config.get("quantization") != {
        "group_size": contract["quantization"]["group_size"],
        "bits": contract["quantization"]["bits"],
        "mode": contract["quantization"]["mode"],
    }:
        raise P5MLXGateError("Converted MLX quantization differs from the gate")
    if not load_check_passed:
        raise P5MLXGateError("Converted MLX artifact must pass a real load check")
    manifest: Dict[str, Any] = {
        "manifest_version": "1.0.0",
        "generated_at": generated_at or _utc_now(),
        "model": dict(contract["model"]),
        "conversion": {
            "implementation": "mlx_lm.convert",
            "mlx_version": mlx_version,
            "mlx_lm_version": mlx_lm_version,
            "python_version": python_version,
            "parameters": {
                "quantize": True,
                "q_bits": contract["quantization"]["bits"],
                "q_group_size": contract["quantization"]["group_size"],
                "q_mode": contract["quantization"]["mode"],
            },
            "command_template_argv": [
                "python",
                "-m",
                "mlx_lm",
                "convert",
                "--hf-path",
                "<source-model-directory>",
                "--mlx-path",
                "<converted-model-directory>",
                "--quantize",
                "--q-group-size",
                str(contract["quantization"]["group_size"]),
                "--q-bits",
                str(contract["quantization"]["bits"]),
                "--q-mode",
                contract["quantization"]["mode"],
            ],
        },
        "source": {
            "inventory": source_inventory,
            "inventory_sha256": inventory_sha256(source_inventory),
        },
        "converted": {
            "inventory": converted_inventory,
            "inventory_sha256": inventory_sha256(converted_inventory),
            "quantization": dict(contract["quantization"]),
            "load_check": "passed",
            "tokenizer_compatibility": dict(tokenizer_compatibility),
        },
        "source_deletion_policy": {
            "allowed_after_gate": source_weight_files,
            "retain": sorted(source_files - set(source_weight_files)),
            "whole_cache_purge": False,
        },
        "manifest_sha256": "pending",
    }
    manifest["manifest_sha256"] = _self_hash(manifest)
    validate_p5_mlx_model_artifact_manifest(manifest)
    return manifest


def validate_p5_mlx_model_artifact_manifest(document: Mapping[str, Any]) -> None:
    if set(document) != {
        "manifest_version",
        "generated_at",
        "model",
        "conversion",
        "source",
        "converted",
        "source_deletion_policy",
        "manifest_sha256",
    }:
        raise P5MLXGateError("MLX model-artifact manifest fields are incomplete")
    contract = load_p5_mlx_gate_contract()
    if document["manifest_version"] != "1.0.0":
        raise P5MLXGateError("Unsupported MLX model-artifact manifest version")
    if document["model"] != contract["model"]:
        raise P5MLXGateError("MLX artifact model pin differs from the gate")
    conversion = document["conversion"]
    if conversion.get("implementation") != "mlx_lm.convert":
        raise P5MLXGateError("MLX conversion implementation is not frozen")
    if conversion.get("parameters") != {
        "quantize": True,
        "q_bits": contract["quantization"]["bits"],
        "q_group_size": contract["quantization"]["group_size"],
        "q_mode": contract["quantization"]["mode"],
    }:
        raise P5MLXGateError("MLX conversion parameters differ from the gate")
    if conversion.get("command_template_argv") != [
        "python",
        "-m",
        "mlx_lm",
        "convert",
        "--hf-path",
        "<source-model-directory>",
        "--mlx-path",
        "<converted-model-directory>",
        "--quantize",
        "--q-group-size",
        str(contract["quantization"]["group_size"]),
        "--q-bits",
        str(contract["quantization"]["bits"]),
        "--q-mode",
        contract["quantization"]["mode"],
    ]:
        raise P5MLXGateError("MLX conversion command differs from the gate")
    if {
        "python": conversion.get("python_version"),
        "mlx": conversion.get("mlx_version"),
        "mlx_lm": conversion.get("mlx_lm_version"),
    } != contract["environment"]:
        raise P5MLXGateError("MLX conversion environment differs from the gate")
    for section in ("source", "converted"):
        payload = document[section]
        inventory = payload.get("inventory")
        if not isinstance(inventory, list) or not inventory:
            raise P5MLXGateError(f"MLX artifact {section} inventory is empty")
        if payload.get("inventory_sha256") != inventory_sha256(inventory):
            raise P5MLXGateError(f"MLX artifact {section} inventory hash differs")
    if document["converted"].get("quantization") != contract["quantization"]:
        raise P5MLXGateError("MLX artifact quantization differs from the gate")
    if document["converted"].get("load_check") != "passed":
        raise P5MLXGateError("MLX artifact load check did not pass")
    compatibility = document["converted"].get("tokenizer_compatibility")
    if not isinstance(compatibility, Mapping) or set(compatibility) != {
        "method",
        "rendered_tokens",
        "source_token_ids_sha256",
        "converted_token_ids_sha256",
        "source_chat_template_sha256",
        "converted_chat_template_sha256",
        "exact_token_ids_equal",
        "chat_template_equal",
    }:
        raise P5MLXGateError("MLX tokenizer compatibility record is incomplete")
    if compatibility["method"] != "frozen_16384_synthetic_probe_v1":
        raise P5MLXGateError("MLX tokenizer compatibility method differs")
    if compatibility["rendered_tokens"] != 16384:
        raise P5MLXGateError("MLX tokenizer probe did not exercise 16,384 tokens")
    if compatibility["exact_token_ids_equal"] is not True or (
        compatibility["source_token_ids_sha256"]
        != compatibility["converted_token_ids_sha256"]
    ):
        raise P5MLXGateError("Converted tokenizer token IDs differ from source")
    if compatibility["chat_template_equal"] is not True or (
        compatibility["source_chat_template_sha256"]
        != compatibility["converted_chat_template_sha256"]
    ):
        raise P5MLXGateError("Converted chat template differs from source")
    policy = document["source_deletion_policy"]
    if policy.get("whole_cache_purge") is not False:
        raise P5MLXGateError("Whole-cache deletion is prohibited")
    allowed = policy.get("allowed_after_gate")
    if allowed != [
        "model-00001-of-00004.safetensors",
        "model-00002-of-00004.safetensors",
        "model-00003-of-00004.safetensors",
        "model-00004-of-00004.safetensors",
        "model.safetensors.index.json",
    ]:
        raise P5MLXGateError("Source deletion allowlist differs from approval")
    if _self_hash(document) != document["manifest_sha256"]:
        raise P5MLXGateError("MLX model-artifact manifest hash differs")


def write_owner_only_json(document: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
