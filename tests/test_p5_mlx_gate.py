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
    build_exact_length_synthetic_gate_rows,
    build_p5_mlx_model_artifact_manifest,
    inventory_directory,
    jsonl_sha256,
    load_p5_mlx_gate_contract,
    validate_p5_mlx_gate_contract,
    validate_p5_mlx_model_artifact_manifest,
    verify_directory_inventory,
    write_owner_only_json,
)
from clinical_matcher.p5_mlx_gate_cli import (
    _assert_resolved_lora_modules,
    _rendered_token_ids,
    _training_namespace,
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
        self.assertEqual("mlx.optimizers.Adam", contract["optimizer"]["implementation"])
        self.assertFalse(contract["loss_implementation"]["chunked_cross_entropy"])
        tampered = copy.deepcopy(contract)
        tampered["optimizer"]["eps"] = 1e-7
        with self.assertRaisesRegex(P5MLXGateError, "owner approval"):
            validate_p5_mlx_gate_contract(tampered)

    def test_builds_exact_16384_token_synthetic_rows(self):
        rows, length = build_exact_length_synthetic_gate_rows(
            LinearSyntheticTokenizer(), row_count=4
        )
        self.assertEqual(16384, length)
        self.assertEqual(4, len(rows))
        self.assertEqual(64, len(jsonl_sha256(rows)))
        self.assertNotIn("patient_id", json.dumps(rows))

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
