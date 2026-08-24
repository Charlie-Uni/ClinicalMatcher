import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .p5_mlx_gate import (
    P5MLXGateError,
    _self_hash,
    _utc_now,
    build_exact_length_synthetic_gate_rows,
    build_p5_mlx_model_artifact_manifest,
    inventory_directory,
    inventory_sha256,
    jsonl_sha256,
    load_p5_mlx_8k_probe_contract,
    load_p5_mlx_gate_contract,
    p5_mlx_execution_contract_sha256,
    sha256_path,
    validate_p5_mlx_model_artifact_manifest,
    verify_p5_mlx_completion_loss_module,
    verify_directory_inventory,
    write_owner_only_json,
)
from .apixaban_sft_length import load_frozen_apixaban_sft_tokenizer
from .splits import current_git_commit


def _load_json(path: Path) -> Dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise P5MLXGateError(f"Expected a JSON object: {path}")
    return document


NATIVE_METAL_OOM_MESSAGE = (
    "[METAL] Command buffer execution failed: Insufficient Memory "
    "(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)"
)


def _write_owner_only_jsonl(rows: Sequence[Dict[str, Any]], path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _versions() -> Dict[str, str]:
    return {
        "python": platform.python_version(),
        "mlx": importlib.metadata.version("mlx"),
        "mlx_lm": importlib.metadata.version("mlx-lm"),
    }


def _tracked_worktree_clean() -> bool:
    unstaged = subprocess.run(
        ["git", "diff", "--quiet"], check=False, capture_output=True
    )
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        check=False,
        capture_output=True,
    )
    if unstaged.returncode not in {0, 1} or staged.returncode not in {0, 1}:
        raise P5MLXGateError("Cannot inspect tracked worktree state")
    return unstaged.returncode == 0 and staged.returncode == 0


def record_8k_native_abort(output_directory: Path) -> Dict[str, Any]:
    preflight_path = output_directory / "preflight.json"
    synthetic_path = output_directory / "synthetic-train.jsonl"
    adapter_config_path = output_directory / "adapters" / "adapter_config.json"
    result_path = output_directory / "gate-result.json"
    if result_path.exists():
        raise FileExistsError(f"Gate result already exists: {result_path}")
    for path in (preflight_path, synthetic_path, adapter_config_path):
        if not path.is_file() or path.is_symlink():
            raise P5MLXGateError(f"Native-abort record requires regular file: {path}")
    preflight = _load_json(preflight_path)
    if preflight.get("preflight_sha256") != _self_hash(
        preflight, "preflight_sha256"
    ):
        raise P5MLXGateError("8K probe preflight hash differs")
    contract = load_p5_mlx_8k_probe_contract()
    if preflight.get("probe_contract_sha256") != p5_mlx_execution_contract_sha256(
        contract
    ):
        raise P5MLXGateError("8K probe preflight contract hash differs")
    if preflight.get("training_shape") != contract["training_shape"]:
        raise P5MLXGateError("8K probe preflight training shape differs")
    if preflight.get("environment") != contract["environment"]:
        raise P5MLXGateError("8K probe preflight environment differs")
    if preflight.get("tracked_worktree_clean") is not True:
        raise P5MLXGateError("8K probe did not start from a clean tracked worktree")
    tracked_worktree_clean = _tracked_worktree_clean()
    if not tracked_worktree_clean:
        raise P5MLXGateError("Native-abort recorder requires a clean tracked worktree")
    partial_inventory = [
        {
            "path": path.relative_to(output_directory).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_path(path),
        }
        for path in (preflight_path, synthetic_path, adapter_config_path)
    ]
    result: Dict[str, Any] = {
        "gate_result_version": "1.0.0",
        "generated_at": _utc_now(),
        "status": "failed_native_process_abort",
        "scope": "synthetic_8k_feasibility_probe_only_not_policy_revision",
        "probe_contract_sha256": preflight["probe_contract_sha256"],
        "preflight_sha256": preflight["preflight_sha256"],
        "model_artifact_manifest_sha256": preflight[
            "model_artifact_manifest_sha256"
        ],
        "probe_implementation_commit": preflight["implementation_commit"],
        "recorder_implementation_commit": current_git_commit(),
        "tracked_worktree_clean": tracked_worktree_clean,
        "environment": dict(preflight["environment"]),
        "training_shape": dict(preflight["training_shape"]),
        "loss_implementation": dict(preflight["loss_implementation"]),
        "observed_loss_module_sha256": preflight[
            "observed_loss_module_sha256"
        ],
        "partial_artifact_inventory": partial_inventory,
        "partial_artifact_inventory_sha256": inventory_sha256(partial_inventory),
        "training_reports": [],
        "peak_memory_gb": None,
        "reference_budget_projection": None,
        "failure": {
            "type": "native_metal_command_buffer_out_of_memory",
            "process_exit_code": 134,
            "signal": "SIGABRT",
            "message": NATIVE_METAL_OOM_MESSAGE,
            "observation_source": (
                "operator_recorded_from_terminal_after_uncatchable_native_abort"
            ),
            "no_completed_throughput_window": True,
            "single_buffer_limit_error_observed": False,
        },
        "manifest_sha256": "pending",
    }
    result["manifest_sha256"] = _self_hash(result)
    write_owner_only_json(result, result_path)
    return result


def _load_mlx_model(model_directory: Path):
    import mlx.core as mx
    from mlx_lm.utils import load

    mx.reset_peak_memory()
    model, tokenizer = load(
        str(model_directory), tokenizer_config={"trust_remote_code": False}
    )
    mx.eval(model.parameters())
    return model, tokenizer, mx.get_peak_memory() / 1e9


def _rendered_token_ids(tokenizer: Any, messages: Sequence[Dict[str, str]]) -> list[int]:
    rendered = tokenizer.apply_chat_template(
        list(messages), tokenize=True, add_generation_prompt=False
    )
    if isinstance(rendered, Mapping):
        rendered = rendered.get("input_ids")
    if hasattr(rendered, "tolist"):
        rendered = rendered.tolist()
    if not isinstance(rendered, list) or not all(
        isinstance(token, int) for token in rendered
    ):
        raise P5MLXGateError("Tokenizer compatibility probe returned invalid IDs")
    return rendered


def _token_id_sha256(token_ids: Sequence[int]) -> str:
    return hashlib.sha256(
        json.dumps(list(token_ids), separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _tokenizer_compatibility(
    source_directory: Path, converted_tokenizer: Any
) -> Dict[str, Any]:
    source_tokenizer = load_frozen_apixaban_sft_tokenizer(source_directory)
    rows, exact_length = build_exact_length_synthetic_gate_rows(
        source_tokenizer, row_count=1
    )
    messages = rows[0]["messages"]
    source_ids = _rendered_token_ids(source_tokenizer, messages)
    converted_ids = _rendered_token_ids(converted_tokenizer, messages)
    source_template = source_tokenizer.chat_template or ""
    converted_template = converted_tokenizer.chat_template or ""
    return {
        "method": "frozen_16384_synthetic_probe_v1",
        "rendered_tokens": exact_length,
        "source_token_ids_sha256": _token_id_sha256(source_ids),
        "converted_token_ids_sha256": _token_id_sha256(converted_ids),
        "source_chat_template_sha256": hashlib.sha256(
            source_template.encode("utf-8")
        ).hexdigest(),
        "converted_chat_template_sha256": hashlib.sha256(
            converted_template.encode("utf-8")
        ).hexdigest(),
        "exact_token_ids_equal": source_ids == converted_ids,
        "chat_template_equal": source_template == converted_template,
    }


def create_model_manifest(
    source_directory: Path,
    converted_directory: Path,
    output_path: Path,
) -> Dict[str, Any]:
    model, converted_tokenizer, _ = _load_mlx_model(converted_directory)
    del model
    versions = _versions()
    manifest = build_p5_mlx_model_artifact_manifest(
        source_directory,
        converted_directory,
        mlx_version=versions["mlx"],
        mlx_lm_version=versions["mlx_lm"],
        python_version=versions["python"],
        load_check_passed=True,
        tokenizer_compatibility=_tokenizer_compatibility(
            source_directory, converted_tokenizer
        ),
    )
    write_owner_only_json(manifest, output_path)
    persisted = _load_json(output_path)
    validate_p5_mlx_model_artifact_manifest(persisted)
    verify_directory_inventory(
        source_directory, persisted["source"]["inventory"]
    )
    verify_directory_inventory(
        converted_directory, persisted["converted"]["inventory"]
    )
    return manifest


class _GateCallback:
    def __init__(self, input_tokens_per_step: int) -> None:
        self.training_reports: list[Dict[str, Any]] = []
        self.input_tokens_per_step = input_tokens_per_step

    def on_train_loss_report(self, train_info: Dict[str, Any]) -> None:
        iterations_per_second = float(train_info["iterations_per_second"])
        self.training_reports.append(
            {
                "iteration": int(train_info["iteration"]),
                "seconds_per_step": 1.0 / iterations_per_second,
                "supervised_tokens_per_second": float(
                    train_info["tokens_per_second"]
                ),
                "input_tokens_per_second": (
                    self.input_tokens_per_step * iterations_per_second
                ),
                "peak_memory_gb": float(train_info["peak_memory"]),
                "train_loss": float(train_info["train_loss"]),
                "learning_rate": float(train_info["learning_rate"]),
                "trained_tokens": int(train_info["trained_tokens"]),
            }
        )

    def on_val_loss_report(self, val_info: Dict[str, Any]) -> None:
        raise P5MLXGateError("Synthetic feasibility gate must not run validation")


def _resolved_lora_modules(model: Any) -> list[str]:
    from mlx_lm.tuner.lora import LoRALinear

    return sorted(
        name for name, module in model.named_modules() if isinstance(module, LoRALinear)
    )


def _assert_resolved_lora_modules(
    names: Sequence[str], contract: Dict[str, Any]
) -> None:
    expected_suffixes = contract["lora"]["target_module_keys"]
    expected_count = contract["lora"]["num_layers"] * len(expected_suffixes)
    if len(names) != expected_count:
        raise P5MLXGateError(
            f"Resolved {len(names)} LoRA modules; expected {expected_count}"
        )
    for suffix in expected_suffixes:
        if sum(name.endswith(suffix) for name in names) != contract["lora"][
            "num_layers"
        ]:
            raise P5MLXGateError(f"Resolved LoRA target mismatch for {suffix}")


def _training_namespace(
    contract: Dict[str, Any], adapter_path: Path
) -> types.SimpleNamespace:
    optimizer = contract["optimizer"]
    return types.SimpleNamespace(
        seed=contract["training_shape"]["seed"],
        num_layers=contract["lora"]["num_layers"],
        fine_tune_type=contract["lora"]["fine_tune_type"],
        lora_parameters={
            "rank": contract["lora"]["rank"],
            "scale": contract["lora"]["scale"],
            "dropout": contract["lora"]["dropout"],
            "keys": contract["lora"]["target_module_keys"],
        },
        resume_adapter_file=None,
        adapter_path=str(adapter_path),
        batch_size=contract["training_shape"]["micro_batch_size"],
        iters=contract["dry_run"]["iterations"],
        val_batches=0,
        steps_per_report=contract["dry_run"]["steps_per_report"],
        steps_per_eval=contract["dry_run"]["iterations"] + 1,
        save_every=contract["dry_run"]["iterations"],
        max_seq_length=contract["training_shape"]["max_seq_length"],
        grad_checkpoint=contract["training_shape"]["gradient_checkpointing"],
        grad_accumulation_steps=contract["training_shape"][
            "gradient_accumulation_steps"
        ],
        learning_rate=optimizer["learning_rate"],
        lr_schedule=None,
        optimizer="adam",
        optimizer_config={
            "adam": {
                "betas": optimizer["betas"],
                "eps": optimizer["eps"],
                "bias_correction": optimizer["bias_correction"],
            }
        },
        mask_prompt=contract["training_shape"]["mask_prompt"],
        clear_cache_threshold=0,
        loss_implementation=dict(contract["loss_implementation"]),
    )


def _train_model_with_completion_loss(
    args: types.SimpleNamespace,
    model: Any,
    train_set: Any,
    callback: _GateCallback,
) -> None:
    import mlx.core as mx
    import mlx.optimizers as optim
    from mlx_lm.tuner.datasets import CacheDataset
    from mlx_lm.tuner.trainer import TrainingArgs, train
    from mlx_lm.tuner.utils import linear_to_lora_layers, print_trainable_parameters
    from mlx_lm.utils import save_config

    from .p5_mlx_completion_loss import completion_only_projection_loss

    if args.fine_tune_type != "lora":
        raise P5MLXGateError("Completion-loss gate permits LoRA only")
    if args.optimizer != "adam" or args.lr_schedule is not None:
        raise P5MLXGateError(
            "Completion-loss gate requires pinned Adam with no LR schedule"
        )
    if args.mask_prompt is not True:
        raise P5MLXGateError(
            "Completion-loss gate requires the frozen mask_prompt supervision"
        )
    mx.random.seed(args.seed)
    model.freeze()
    if args.num_layers > len(model.layers):
        raise P5MLXGateError("Requested LoRA layers exceed loaded model layers")
    linear_to_lora_layers(model, args.num_layers, args.lora_parameters)
    print_trainable_parameters(model)

    adapter_path = Path(args.adapter_path)
    adapter_path.mkdir(parents=True, exist_ok=True)
    adapter_file = adapter_path / "adapters.safetensors"
    save_config(vars(args), adapter_path / "adapter_config.json")

    training_args = TrainingArgs(
        batch_size=args.batch_size,
        iters=args.iters,
        val_batches=args.val_batches,
        steps_per_report=args.steps_per_report,
        steps_per_eval=args.steps_per_eval,
        steps_per_save=args.save_every,
        adapter_file=adapter_file,
        max_seq_length=args.max_seq_length,
        grad_checkpoint=args.grad_checkpoint,
        grad_accumulation_steps=args.grad_accumulation_steps,
        clear_cache_threshold=args.clear_cache_threshold,
    )
    optimizer = optim.Adam(
        learning_rate=args.learning_rate,
        **args.optimizer_config["adam"],
    )
    train(
        model=model,
        args=training_args,
        optimizer=optimizer,
        train_dataset=CacheDataset(train_set),
        val_dataset=CacheDataset([]),
        loss=completion_only_projection_loss,
        training_callback=callback,
    )


def run_gate(
    converted_directory: Path,
    model_manifest_path: Path,
    output_directory: Path,
    *,
    contract: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    import mlx.core as mx
    from mlx_lm.tuner.datasets import create_dataset

    from .p5_mlx_completion_loss import completion_projection_bounds

    contract = dict(contract or load_p5_mlx_gate_contract())
    is_probe = "probe_contract_version" in contract
    contract_hash_field = (
        "probe_contract_sha256" if is_probe else "gate_contract_sha256"
    )
    result_scope = (
        "synthetic_8k_feasibility_probe_only_not_policy_revision"
        if is_probe
        else "synthetic_feasibility_only_not_model_quality"
    )
    implementation_commit = current_git_commit()
    tracked_worktree_clean = _tracked_worktree_clean()
    if is_probe and not tracked_worktree_clean:
        raise P5MLXGateError("8K probe requires a clean tracked worktree")
    loss_module_sha256 = verify_p5_mlx_completion_loss_module(contract)
    model_manifest = _load_json(model_manifest_path)
    validate_p5_mlx_model_artifact_manifest(model_manifest)
    verify_directory_inventory(
        converted_directory, model_manifest["converted"]["inventory"]
    )
    versions = _versions()
    if versions != contract["environment"]:
        raise P5MLXGateError("Installed MLX environment differs from the gate contract")
    if versions != {
        "python": model_manifest["conversion"]["python_version"],
        "mlx": model_manifest["conversion"]["mlx_version"],
        "mlx_lm": model_manifest["conversion"]["mlx_lm_version"],
    }:
        raise P5MLXGateError("Gate environment differs from conversion environment")
    if output_directory.exists():
        raise FileExistsError(f"Gate output already exists: {output_directory}")
    output_directory.mkdir(mode=0o700, parents=True)
    preflight = {
        "preflight_version": "1.0.0",
        "generated_at": _utc_now(),
        "status": "started",
        "implementation_commit": implementation_commit,
        "tracked_worktree_clean": tracked_worktree_clean,
        contract_hash_field: p5_mlx_execution_contract_sha256(contract),
        "model_artifact_manifest_sha256": model_manifest["manifest_sha256"],
        "environment": versions,
        "loss_implementation": dict(contract["loss_implementation"]),
        "observed_loss_module_sha256": loss_module_sha256,
        "training_shape": dict(contract["training_shape"]),
        "optimizer": dict(contract["optimizer"]),
        "lora": dict(contract["lora"]),
        "preflight_sha256": "pending",
    }
    preflight["preflight_sha256"] = _self_hash(preflight, "preflight_sha256")
    write_owner_only_json(preflight, output_directory / "preflight.json")

    model = None
    load_peak: float | None = None
    exact_length: int | None = None
    rows: list[Dict[str, Any]] = []
    active_stage = "model_load"
    callback = _GateCallback(contract["training_shape"]["max_seq_length"])
    projection_bounds: Dict[str, int] | None = None
    result: Dict[str, Any]
    try:
        model, tokenizer, load_peak = _load_mlx_model(converted_directory)
        rows, exact_length = build_exact_length_synthetic_gate_rows(
            tokenizer, contract=contract
        )
        _write_owner_only_jsonl(rows, output_directory / "synthetic-train.jsonl")
        dataset_config = types.SimpleNamespace(
            mask_prompt=contract["training_shape"]["mask_prompt"]
        )
        train_set = create_dataset(rows, tokenizer, dataset_config)
        processed_rows = [train_set.process(row) for row in rows]
        processed_lengths = [len(item[0]) for item in processed_rows]
        if processed_lengths != [contract["training_shape"]["max_seq_length"]] * len(
            rows
        ):
            raise P5MLXGateError(
                "Stock MLX-LM dataset did not preserve exact "
                f"{contract['training_shape']['max_seq_length']:,}-token rows"
            )
        all_projection_bounds = [
            completion_projection_bounds(
                batch_token_count=len(tokens),
                prompt_offset=prompt_offset,
                full_token_count=len(tokens),
            )
            for tokens, prompt_offset in processed_rows
        ]
        if any(item != all_projection_bounds[0] for item in all_projection_bounds):
            raise P5MLXGateError("Synthetic gate rows have inconsistent loss bounds")
        projection_bounds = all_projection_bounds[0]
        mx.reset_peak_memory()
        active_stage = "adapter_setup_and_training"
        args = _training_namespace(contract, output_directory / "adapters")
        _train_model_with_completion_loss(args, model, train_set, callback)
        resolved = _resolved_lora_modules(model)
        _assert_resolved_lora_modules(resolved, contract)
        training_peak = mx.get_peak_memory() / 1e9
        if len(callback.training_reports) != 2:
            raise P5MLXGateError("Gate did not emit both frozen throughput windows")
        stages = {
            "model_load": load_peak,
            "adapter_setup_and_training": training_peak,
        }
        peak_stage = max(stages, key=stages.get)
        adapter_inventory = inventory_directory(output_directory / "adapters")
        result = {
            "gate_result_version": "1.0.0",
            "generated_at": _utc_now(),
            "status": "passed_mechanism_gate",
            "implementation_commit": implementation_commit,
            "tracked_worktree_clean": tracked_worktree_clean,
            "scope": result_scope,
            contract_hash_field: preflight[contract_hash_field],
            "model_artifact_manifest_sha256": model_manifest["manifest_sha256"],
            "environment": versions,
            "sequence": {
                "row_count": len(rows),
                "rendered_tokens_per_row": exact_length,
                "synthetic_jsonl_sha256": jsonl_sha256(rows),
                "no_truncation": True,
                "completion_projection_bounds": projection_bounds,
            },
            "resolved_target_modules": resolved,
            "training_reports": callback.training_reports,
            "memory_by_stage_gb": stages,
            "peak_stage": peak_stage,
            "peak_memory_gb": stages[peak_stage],
            "loss_implementation": dict(contract["loss_implementation"]),
            "observed_loss_module_sha256": loss_module_sha256,
            "adapter_inventory": adapter_inventory,
            "adapter_inventory_sha256": inventory_sha256(adapter_inventory),
            "failure": None,
            "manifest_sha256": "pending",
        }
        if is_probe:
            seconds_per_step = max(
                item["seconds_per_step"] for item in callback.training_reports
            )
            reference_rows = contract["reference_budget"]["grid_rows_per_epoch"]
            result["reference_budget_projection"] = {
                "grid_rows_per_epoch": reference_rows,
                "seconds_per_step_rule": contract["reference_budget"][
                    "seconds_per_step_rule"
                ],
                "selected_seconds_per_step": seconds_per_step,
                "projected_epoch_hours": seconds_per_step
                * reference_rows
                / 3600.0,
            }
    except Exception as error:
        resolved = _resolved_lora_modules(model) if model is not None else []
        failure_peak = mx.get_peak_memory() / 1e9
        stages = {active_stage: failure_peak}
        if load_peak is not None and active_stage != "model_load":
            stages["model_load"] = load_peak
        peak_stage = max(stages, key=stages.get)
        result = {
            "gate_result_version": "1.0.0",
            "generated_at": _utc_now(),
            "status": "failed",
            "implementation_commit": implementation_commit,
            "tracked_worktree_clean": tracked_worktree_clean,
            "scope": result_scope,
            contract_hash_field: preflight[contract_hash_field],
            "model_artifact_manifest_sha256": model_manifest["manifest_sha256"],
            "environment": versions,
            "sequence": (
                {
                    "row_count": len(rows),
                    "rendered_tokens_per_row": exact_length,
                    "synthetic_jsonl_sha256": jsonl_sha256(rows),
                    "no_truncation": True,
                    "completion_projection_bounds": projection_bounds,
                }
                if rows and exact_length is not None
                else None
            ),
            "resolved_target_modules": resolved,
            "training_reports": callback.training_reports,
            "memory_by_stage_gb": stages,
            "peak_stage": peak_stage,
            "peak_memory_gb": stages[peak_stage],
            "loss_implementation": dict(contract["loss_implementation"]),
            "observed_loss_module_sha256": loss_module_sha256,
            "adapter_inventory": None,
            "adapter_inventory_sha256": None,
            "failure": {"type": type(error).__name__, "message": str(error)},
            "manifest_sha256": "pending",
        }
        if is_probe:
            result["reference_budget_projection"] = None
        result["manifest_sha256"] = _self_hash(result)
        write_owner_only_json(result, output_directory / "gate-result.json")
        raise
    result["manifest_sha256"] = _self_hash(result)
    write_owner_only_json(result, output_directory / "gate-result.json")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and run frozen local P5 MLX feasibility checks"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--source-model", type=Path, required=True)
    manifest.add_argument("--converted-model", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    gate = subparsers.add_parser("run")
    gate.add_argument("--converted-model", type=Path, required=True)
    gate.add_argument("--model-manifest", type=Path, required=True)
    gate.add_argument("--output-dir", type=Path, required=True)
    probe = subparsers.add_parser("probe-8k")
    probe.add_argument("--converted-model", type=Path, required=True)
    probe.add_argument("--model-manifest", type=Path, required=True)
    probe.add_argument("--output-dir", type=Path, required=True)
    abort = subparsers.add_parser("record-8k-native-abort")
    abort.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "manifest":
        manifest = create_model_manifest(
            args.source_model, args.converted_model, args.output
        )
        print(
            "Pinned MLX model manifest written: "
            f"{args.output} ({manifest['manifest_sha256']})"
        )
        return 0
    if args.command == "record-8k-native-abort":
        result = record_8k_native_abort(args.output_dir)
        print(
            "8K native-abort result recorded: "
            f"{args.output_dir / 'gate-result.json'} "
            f"({result['manifest_sha256']})"
        )
        return 0
    contract = (
        load_p5_mlx_8k_probe_contract()
        if args.command == "probe-8k"
        else load_p5_mlx_gate_contract()
    )
    result = run_gate(
        args.converted_model,
        args.model_manifest,
        args.output_dir,
        contract=contract,
    )
    label = (
        "8K MLX feasibility probe"
        if args.command == "probe-8k"
        else "16K MLX gate"
    )
    print(
        f"Synthetic {label} passed: "
        f"{args.output_dir / 'gate-result.json'} ({result['manifest_sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
