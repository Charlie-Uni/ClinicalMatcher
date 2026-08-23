import importlib.util
import platform
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from clinical_matcher.p5_mlx_completion_loss import (
    PROJECTION_WINDOW_TOKENS,
    P5MLXCompletionLossError,
    completion_only_projection_loss,
    completion_projection_bounds,
)


MLX_TEST_AVAILABLE = (
    platform.system() == "Darwin" and importlib.util.find_spec("mlx") is not None
)


class CompletionProjectionBoundaryTests(unittest.TestCase):
    def test_first_completion_token_uses_last_prompt_hidden_state(self):
        bounds = completion_projection_bounds(
            batch_token_count=9,
            prompt_offset=3,
            full_token_count=9,
        )
        self.assertEqual(2, bounds["first_hidden_index"])
        self.assertEqual(3, bounds["first_supervised_step"])

    def test_prompt_one_completion_one_and_completion_512_boundaries(self):
        single = completion_projection_bounds(
            batch_token_count=2,
            prompt_offset=1,
            full_token_count=2,
        )
        self.assertEqual(1, single["supervised_position_count"])
        self.assertEqual(0, single["first_hidden_index"])

        upper = completion_projection_bounds(
            batch_token_count=515,
            prompt_offset=3,
            full_token_count=515,
        )
        self.assertEqual(512, upper["supervised_position_count"])
        self.assertEqual(2, upper["first_hidden_index"])
        self.assertEqual(513, upper["last_hidden_index"])

    def test_projection_window_covers_pinned_padding_slack(self):
        self.assertEqual(544, PROJECTION_WINDOW_TOKENS)
        padded = completion_projection_bounds(
            batch_token_count=545,
            prompt_offset=3,
            full_token_count=515,
        )
        self.assertEqual(0, padded["window_start_index"])
        self.assertEqual(513, padded["supervised_position_count"])

    def test_rejects_target_outside_frozen_window(self):
        with self.assertRaisesRegex(P5MLXCompletionLossError, "outside"):
            completion_projection_bounds(
                batch_token_count=2049,
                prompt_offset=100,
                full_token_count=2049,
            )


@unittest.skipUnless(
    MLX_TEST_AVAILABLE,
    "Pinned MLX gradient-equivalence test requires Apple Silicon MLX",
)
class PinnedMLXLossEquivalenceTests(unittest.TestCase):
    def _assert_case(self, *, prompt_length: int, completion_length: int) -> None:
        import mlx.core as mx
        import mlx.nn as nn
        from mlx.utils import tree_flatten
        from mlx_lm.tuner.trainer import default_loss

        class TinyBackbone(nn.Module):
            def __init__(self, vocab_size: int, hidden_size: int):
                super().__init__()
                self.embed_tokens = nn.Embedding(vocab_size, hidden_size)
                self.mix = nn.Linear(hidden_size, hidden_size, bias=True)

            def __call__(self, token_ids):
                embedded = self.embed_tokens(token_ids)
                causal = mx.cumsum(embedded, axis=1)
                return self.mix(causal)

        class TinyCausalModel(nn.Module):
            def __init__(self, vocab_size: int = 17, hidden_size: int = 8):
                super().__init__()
                self.args = type("Args", (), {"tie_word_embeddings": True})()
                self.model = TinyBackbone(vocab_size, hidden_size)

            def __call__(self, token_ids):
                hidden = self.model(token_ids)
                return self.model.embed_tokens.as_linear(hidden)

        total_length = prompt_length + completion_length
        token_ids = [[(index * 5 + 3) % 17 for index in range(total_length)]]
        batch = mx.array(token_ids, dtype=mx.int32)
        lengths = mx.array([[prompt_length, total_length]], dtype=mx.int32)

        mx.random.seed(17)
        model = TinyCausalModel()
        reference_value_and_grad = nn.value_and_grad(model, default_loss)
        fallback_value_and_grad = nn.value_and_grad(
            model, completion_only_projection_loss
        )
        (reference_loss, reference_ntoks), reference_gradients = (
            reference_value_and_grad(model, batch, lengths)
        )
        (fallback_loss, fallback_ntoks), fallback_gradients = (
            fallback_value_and_grad(model, batch, lengths)
        )
        mx.eval(
            reference_loss,
            reference_ntoks,
            reference_gradients,
            fallback_loss,
            fallback_ntoks,
            fallback_gradients,
        )

        self.assertEqual(reference_ntoks.item(), fallback_ntoks.item())
        self.assertTrue(
            mx.allclose(reference_loss, fallback_loss, rtol=1e-6, atol=1e-7).item()
        )
        reference_flat = dict(tree_flatten(reference_gradients))
        fallback_flat = dict(tree_flatten(fallback_gradients))
        trainable_names = {
            name for name, _ in tree_flatten(model.trainable_parameters())
        }
        self.assertEqual(trainable_names, set(reference_flat))
        self.assertEqual(trainable_names, set(fallback_flat))
        for name in sorted(trainable_names):
            self.assertTrue(
                mx.allclose(
                    reference_flat[name],
                    fallback_flat[name],
                    rtol=1e-5,
                    atol=1e-6,
                ).item(),
                msg=f"Gradient differs for trainable parameter {name}",
            )

    def test_matches_pinned_mask_prompt_loss_and_all_trainable_gradients(self):
        for case in (
            {"prompt_length": 3, "completion_length": 1},
            {"prompt_length": 1, "completion_length": 7},
            {"prompt_length": 3, "completion_length": 512},
            {"prompt_length": 700, "completion_length": 20},
        ):
            with self.subTest(**case):
                self._assert_case(**case)

    def test_injects_loss_into_stock_trainer_without_changing_optimizer(self):
        import mlx.core as mx
        from mlx_lm.models.llama import Model, ModelArgs

        from clinical_matcher.p5_mlx_gate_cli import (
            _GateCallback,
            _resolved_lora_modules,
            _train_model_with_completion_loss,
        )

        class PreparedDataset:
            def __init__(self):
                self.rows = [(list(range(32)), 20)]

            def __len__(self):
                return len(self.rows)

            def __getitem__(self, index):
                return self.rows[index]

            def process(self, row):
                return row

        model = Model(
            ModelArgs(
                model_type="llama",
                hidden_size=16,
                num_hidden_layers=2,
                intermediate_size=32,
                num_attention_heads=4,
                num_key_value_heads=2,
                rms_norm_eps=1e-5,
                vocab_size=64,
                head_dim=4,
                max_position_embeddings=64,
                tie_word_embeddings=True,
            )
        )
        mx.eval(model.parameters())
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(
                seed=17,
                num_layers=1,
                fine_tune_type="lora",
                lora_parameters={
                    "rank": 2,
                    "scale": 4.0,
                    "dropout": 0.0,
                    "keys": [
                        "self_attn.q_proj",
                        "self_attn.k_proj",
                        "self_attn.v_proj",
                        "self_attn.o_proj",
                        "mlp.gate_proj",
                        "mlp.up_proj",
                        "mlp.down_proj",
                    ],
                },
                adapter_path=str(Path(directory) / "adapters"),
                batch_size=1,
                iters=1,
                val_batches=0,
                steps_per_report=1,
                steps_per_eval=2,
                save_every=1,
                max_seq_length=32,
                grad_checkpoint=False,
                grad_accumulation_steps=1,
                clear_cache_threshold=0,
                learning_rate=1e-5,
                optimizer="adam",
                lr_schedule=None,
                mask_prompt=True,
                optimizer_config={
                    "adam": {
                        "betas": [0.9, 0.999],
                        "eps": 1e-8,
                        "bias_correction": False,
                    }
                },
                loss_implementation={"implementation_version": "test"},
            )
            callback = _GateCallback(input_tokens_per_step=32)
            _train_model_with_completion_loss(
                args,
                model,
                PreparedDataset(),
                callback,
            )
            self.assertEqual(1, len(callback.training_reports))
            self.assertEqual(7, len(_resolved_lora_modules(model)))
            self.assertTrue((Path(args.adapter_path) / "adapters.safetensors").is_file())
            self.assertTrue((Path(args.adapter_path) / "adapter_config.json").is_file())


if __name__ == "__main__":
    unittest.main()
