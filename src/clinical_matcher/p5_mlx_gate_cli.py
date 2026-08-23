import argparse
import importlib.metadata
import json
import os
import platform
import sys
import types
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .p5_mlx_gate import (
    P5MLXGateError,
    _self_hash,
    _utc_now,
    build_exact_length_synthetic_gate_rows,
    build_p5_mlx_model_artifact_manifest,
    inventory_directory,
    inventory_sha256,
    jsonl_sha256,
    load_p5_mlx_gate_contract,
    p5_mlx_gate_contract_sha256,
    validate_p5_mlx_model_artifact_manifest,
    verify_directory_inventory,
    write_owner_only_json,
)


def _load_json(path: Path) -> Dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise P5MLXGateError(f"Expected a JSON object: {path}")
    return document


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


def _load_mlx_model(model_directory: Path):
    import mlx.core as mx
    from mlx_lm.utils import load

    mx.reset_peak_memory()
    model, tokenizer = load(
        str(model_directory), tokenizer_config={"trust_remote_code": False}
    )
    mx.eval(model.parameters())
    return model, tokenizer, mx.get_peak_memory() / 1e9


def create_model_manifest(
    source_directory: Path,
    converted_directory: Path,
    output_path: Path,
) -> Dict[str, Any]:
    model, _, _ = _load_mlx_model(converted_directory)
    del model
    versions = _versions()
    manifest = build_p5_mlx_model_artifact_manifest(
        source_directory,
        converted_directory,
        mlx_version=versions["mlx"],
        mlx_lm_version=versions["mlx_lm"],
        python_version=versions["python"],
        load_check_passed=True,
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
    def __init__(self) -> None:
        self.training_reports: list[Dict[str, Any]] = []

    def on_train_loss_report(self, train_info: Dict[str, Any]) -> None:
        self.training_reports.append(
            {
                "iteration": int(train_info["iteration"]),
                "seconds_per_step": 1.0
                / float(train_info["iterations_per_second"]),
                "tokens_per_second": float(train_info["tokens_per_second"]),
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
    )


def run_gate(
    converted_directory: Path,
    model_manifest_path: Path,
    output_directory: Path,
) -> Dict[str, Any]:
    import mlx.core as mx
    from mlx_lm.lora import train_model
    from mlx_lm.tuner.datasets import create_dataset

    contract = load_p5_mlx_gate_contract()
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
        "gate_contract_sha256": p5_mlx_gate_contract_sha256(contract),
        "model_artifact_manifest_sha256": model_manifest["manifest_sha256"],
        "environment": versions,
        "loss_implementation": dict(contract["loss_implementation"]),
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
    callback = _GateCallback()
    result: Dict[str, Any]
    try:
        model, tokenizer, load_peak = _load_mlx_model(converted_directory)
        rows, exact_length = build_exact_length_synthetic_gate_rows(tokenizer)
        _write_owner_only_jsonl(rows, output_directory / "synthetic-train.jsonl")
        dataset_config = types.SimpleNamespace(
            mask_prompt=contract["training_shape"]["mask_prompt"]
        )
        train_set = create_dataset(rows, tokenizer, dataset_config)
        processed_lengths = [len(train_set.process(row)[0]) for row in rows]
        if processed_lengths != [contract["training_shape"]["max_seq_length"]] * len(
            rows
        ):
            raise P5MLXGateError(
                "Stock MLX-LM dataset did not preserve exact 16,384-token rows"
            )
        mx.reset_peak_memory()
        active_stage = "adapter_setup_and_training"
        args = _training_namespace(contract, output_directory / "adapters")
        train_model(args, model, train_set, [], callback)
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
            "scope": "synthetic_feasibility_only_not_model_quality",
            "gate_contract_sha256": preflight["gate_contract_sha256"],
            "model_artifact_manifest_sha256": model_manifest["manifest_sha256"],
            "environment": versions,
            "sequence": {
                "row_count": len(rows),
                "rendered_tokens_per_row": exact_length,
                "synthetic_jsonl_sha256": jsonl_sha256(rows),
                "no_truncation": True,
            },
            "resolved_target_modules": resolved,
            "training_reports": callback.training_reports,
            "memory_by_stage_gb": stages,
            "peak_stage": peak_stage,
            "peak_memory_gb": stages[peak_stage],
            "loss_implementation": dict(contract["loss_implementation"]),
            "adapter_inventory": adapter_inventory,
            "adapter_inventory_sha256": inventory_sha256(adapter_inventory),
            "failure": None,
            "manifest_sha256": "pending",
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
            "scope": "synthetic_feasibility_only_not_model_quality",
            "gate_contract_sha256": preflight["gate_contract_sha256"],
            "model_artifact_manifest_sha256": model_manifest["manifest_sha256"],
            "environment": versions,
            "sequence": (
                {
                    "row_count": len(rows),
                    "rendered_tokens_per_row": exact_length,
                    "synthetic_jsonl_sha256": jsonl_sha256(rows),
                    "no_truncation": True,
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
            "adapter_inventory": None,
            "adapter_inventory_sha256": None,
            "failure": {"type": type(error).__name__, "message": str(error)},
            "manifest_sha256": "pending",
        }
        result["manifest_sha256"] = _self_hash(result)
        write_owner_only_json(result, output_directory / "gate-result.json")
        raise
    result["manifest_sha256"] = _self_hash(result)
    write_owner_only_json(result, output_directory / "gate-result.json")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare and run the frozen local P5 MLX 16K feasibility gate"
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
    result = run_gate(args.converted_model, args.model_manifest, args.output_dir)
    print(
        "Synthetic 16K MLX gate passed: "
        f"{args.output_dir / 'gate-result.json'} ({result['manifest_sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
