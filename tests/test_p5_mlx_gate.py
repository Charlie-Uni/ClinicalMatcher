import copy
import hashlib
import json
import os
import tempfile
import unittest
from collections import UserDict
from pathlib import Path
from unittest.mock import patch

from clinical_matcher.p5_mlx_gate import (
    P5MLXGateError,
    _self_hash,
    build_exact_length_synthetic_gate_rows,
    build_p5_mlx_model_artifact_manifest,
    inventory_directory,
    jsonl_sha256,
    load_p5_mlx_8k_probe_contract,
    load_p5_mlx_gate_contract,
    p5_mlx_execution_contract_sha256,
    validate_p5_mlx_gate_contract,
    validate_p5_mlx_8k_probe_contract,
    validate_p5_mlx_model_artifact_manifest,
    verify_p5_mlx_completion_loss_module,
    verify_directory_inventory,
    write_owner_only_json,
)
from clinical_matcher.p5_mlx_gate_cli import (
    _GateCallback,
    _assert_resolved_lora_modules,
    _rendered_token_ids,
    _training_namespace,
    _tracked_worktree_clean,
    record_8k_native_abort,
)


class LinearSyntheticTokenizer:
    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        payload = json.dumps(messages)
        repetitions = payload.count(" evidence")
        return list(range(100 + repetitions))


class MappingTokenizer:
    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        return UserDict({"input_ids": [1, 2, 3]})


def _write(path: Path, content: bytes) -> None:
    path.write_bytes(content)


class P5MLXGateTests(unittest.TestCase):
    def test_contract_is_exact_and_tampering_fails(self):
        contract = load_p5_mlx_gate_contract()
        self.assertEqual("1.1.0", contract["gate_contract_version"])
        self.assertEqual("mlx.optimizers.Adam", contract["optimizer"]["implementation"])
        self.assertFalse(contract["loss_implementation"]["chunked_cross_entropy"])
        self.assertFalse(
            contract["loss_implementation"]["completion_internal_field_masking"]
        )
        self.assertFalse(
            contract["fallback_revision"]["supervision_semantics_changed"]
        )
        self.assertEqual(
            contract["loss_implementation"]["module_sha256"],
            verify_p5_mlx_completion_loss_module(contract),
        )
        tampered = copy.deepcopy(contract)
        tampered["optimizer"]["eps"] = 1e-7
        with self.assertRaisesRegex(P5MLXGateError, "owner approval"):
            validate_p5_mlx_gate_contract(tampered)

    def test_8k_probe_freezes_scope_configuration_and_length_screen(self):
        probe = load_p5_mlx_8k_probe_contract()
        gate = load_p5_mlx_gate_contract()
        self.assertEqual(8192, probe["training_shape"]["max_seq_length"])
        self.assertFalse(probe["scope"]["changes_frozen_input_policy"])
        self.assertFalse(probe["scope"]["authorizes_fallback"])
        self.assertEqual(gate["lora"], probe["lora"])
        self.assertEqual(gate["optimizer"], probe["optimizer"])
        self.assertEqual(gate["loss_implementation"], probe["loss_implementation"])
        self.assertEqual(63, probe["length_screen"]["maximum_overflow_rows"])
        self.assertEqual(
            0.05, probe["length_screen"]["maximum_overflow_fraction"]
        )
        self.assertTrue(probe["length_screen"]["screen_all_questions_without_labels"])
        tampered = copy.deepcopy(probe)
        tampered["length_screen"]["maximum_overflow_rows"] = 64
        with self.assertRaisesRegex(P5MLXGateError, "owner approval"):
            validate_p5_mlx_8k_probe_contract(tampered)

    def test_builds_exact_16384_token_synthetic_rows(self):
        rows, length = build_exact_length_synthetic_gate_rows(
            LinearSyntheticTokenizer(), row_count=4
        )
        self.assertEqual(16384, length)
        self.assertEqual(4, len(rows))
        self.assertEqual(64, len(jsonl_sha256(rows)))
        self.assertNotIn("patient_id", json.dumps(rows))

    def test_builds_exact_8192_token_probe_rows(self):
        rows, length = build_exact_length_synthetic_gate_rows(
            LinearSyntheticTokenizer(),
            row_count=4,
            contract=load_p5_mlx_8k_probe_contract(),
        )
        self.assertEqual(8192, length)
        self.assertEqual(4, len(rows))

    def test_token_id_probe_accepts_transformers_mapping_result(self):
        self.assertEqual(
            [1, 2, 3],
            _rendered_token_ids(MappingTokenizer(), [{"role": "user", "content": "x"}]),
        )

    def test_training_namespace_pins_optimizer_and_lora_targets(self):
        contract = load_p5_mlx_gate_contract()
        args = _training_namespace(contract, Path("local-adapters"))
        self.assertEqual("adam", args.optimizer)
        self.assertEqual(
            {
                "betas": [0.9, 0.999],
                "eps": 1e-8,
                "bias_correction": False,
            },
            args.optimizer_config["adam"],
        )
        self.assertIsNone(args.lr_schedule)
        self.assertEqual(
            contract["lora"]["target_module_keys"], args.lora_parameters["keys"]
        )
        names = [
            f"model.layers.{layer}.{suffix}"
            for layer in range(16, 32)
            for suffix in contract["lora"]["target_module_keys"]
        ]
        _assert_resolved_lora_modules(names, contract)
        with self.assertRaisesRegex(P5MLXGateError, "expected 112"):
            _assert_resolved_lora_modules(names[:-1], contract)

    def test_gate_callback_reports_supervised_and_full_input_throughput(self):
        callback = _GateCallback(input_tokens_per_step=16384)
        callback.on_train_loss_report(
            {
                "iteration": 1,
                "iterations_per_second": 0.5,
                "tokens_per_second": 128.0,
                "peak_memory": 9.5,
                "train_loss": 0.25,
                "learning_rate": 1e-5,
                "trained_tokens": 256,
            }
        )
        self.assertEqual(
            {
                "iteration": 1,
                "seconds_per_step": 2.0,
                "supervised_tokens_per_second": 128.0,
                "input_tokens_per_second": 8192.0,
                "peak_memory_gb": 9.5,
                "train_loss": 0.25,
                "learning_rate": 1e-5,
                "trained_tokens": 256,
            },
            callback.training_reports[0],
        )

    def test_tracked_worktree_check_requires_both_diffs_clean(self):
        clean = unittest.mock.Mock(returncode=0)
        dirty = unittest.mock.Mock(returncode=1)
        with patch(
            "clinical_matcher.p5_mlx_gate_cli.subprocess.run",
            side_effect=[clean, clean],
        ):
            self.assertTrue(_tracked_worktree_clean())
        with patch(
            "clinical_matcher.p5_mlx_gate_cli.subprocess.run",
            side_effect=[dirty, clean],
        ):
            self.assertFalse(_tracked_worktree_clean())

    def test_native_abort_record_is_hash_bound_and_owner_only(self):
        contract = load_p5_mlx_8k_probe_contract()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "adapters").mkdir()
            preflight = {
                "preflight_sha256": "pending",
                "probe_contract_sha256": p5_mlx_execution_contract_sha256(
                    contract
                ),
                "model_artifact_manifest_sha256": "a" * 64,
                "implementation_commit": "b" * 40,
                "tracked_worktree_clean": True,
                "environment": dict(contract["environment"]),
                "training_shape": dict(contract["training_shape"]),
                "loss_implementation": dict(contract["loss_implementation"]),
                "observed_loss_module_sha256": contract["loss_implementation"][
                    "module_sha256"
                ],
            }
            preflight["preflight_sha256"] = _self_hash(
                preflight, "preflight_sha256"
            )
            (output / "preflight.json").write_text(json.dumps(preflight))
            (output / "synthetic-train.jsonl").write_text("{}\n")
            (output / "adapters" / "adapter_config.json").write_text("{}")
            with patch(
                "clinical_matcher.p5_mlx_gate_cli._tracked_worktree_clean",
                return_value=True,
            ), patch(
                "clinical_matcher.p5_mlx_gate_cli.current_git_commit",
                return_value="c" * 40,
            ):
                result = record_8k_native_abort(output)
            self.assertEqual("failed_native_process_abort", result["status"])
            self.assertEqual([], result["training_reports"])
            self.assertIsNone(result["peak_memory_gb"])
            self.assertEqual(0o600, (output / "gate-result.json").stat().st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                record_8k_native_abort(output)

    def test_artifact_manifest_binds_files_and_deletion_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            converted = root / "converted"
            source.mkdir()
            converted.mkdir()
            for index in range(1, 5):
                _write(
                    source / f"model-{index:05d}-of-00004.safetensors",
                    f"shard-{index}".encode(),
                )
            _write(source / "model.safetensors.index.json", b"{}")
            tokenizer_payloads = {
                "config.json": b"source-config",
                "generation_config.json": b"generation-config",
                "special_tokens_map.json": b"special-tokens",
                "tokenizer.json": b"tokenizer",
                "tokenizer_config.json": b"tokenizer-config",
            }
            for relative, payload in tokenizer_payloads.items():
                _write(source / relative, payload)
                if relative != "config.json":
                    _write(converted / relative, payload)
            _write(converted / "model.safetensors", b"converted")
            _write(
                converted / "config.json",
                json.dumps(
                    {
                        "quantization": {
                            "group_size": 64,
                            "bits": 4,
                            "mode": "affine",
                        }
                    }
                ).encode(),
            )
            fake_length_contract = {
                "tokenizer": {
                    "files": {
                        relative: hashlib.sha256(payload).hexdigest()
                        for relative, payload in tokenizer_payloads.items()
                    }
                }
            }
            with patch(
                "clinical_matcher.p5_mlx_gate.load_apixaban_sft_length_contract",
                return_value=fake_length_contract,
            ):
                manifest = build_p5_mlx_model_artifact_manifest(
                    source,
                    converted,
                    mlx_version="0.31.2",
                    mlx_lm_version="0.31.3",
                    python_version="3.11.16",
                    load_check_passed=True,
                    tokenizer_compatibility={
                        "method": "frozen_16384_synthetic_probe_v1",
                        "rendered_tokens": 16384,
                        "source_token_ids_sha256": "a" * 64,
                        "converted_token_ids_sha256": "a" * 64,
                        "source_chat_template_sha256": "b" * 64,
                        "converted_chat_template_sha256": "b" * 64,
                        "exact_token_ids_equal": True,
                        "chat_template_equal": True,
                    },
                    generated_at="2026-08-23T00:00:00Z",
                )
            validate_p5_mlx_model_artifact_manifest(manifest)
            incompatible = copy.deepcopy(manifest)
            incompatible["converted"]["tokenizer_compatibility"][
                "exact_token_ids_equal"
            ] = False
            with self.assertRaisesRegex(P5MLXGateError, "token IDs differ"):
                validate_p5_mlx_model_artifact_manifest(incompatible)
            self.assertIn(
                "tokenizer.json", manifest["source_deletion_policy"]["retain"]
            )
            verify_directory_inventory(converted, manifest["converted"]["inventory"])
            _write(converted / "model.safetensors", b"tampered")
            with self.assertRaisesRegex(P5MLXGateError, "inventory changed"):
                verify_directory_inventory(
                    converted, manifest["converted"]["inventory"]
                )

    def test_owner_only_writer_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "manifest.json"
            write_owner_only_json({"ok": True}, path)
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                write_owner_only_json({"ok": True}, path)


if __name__ == "__main__":
    unittest.main()
