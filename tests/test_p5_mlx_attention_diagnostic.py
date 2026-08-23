import importlib.util
import platform
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.p5_mlx_attention_diagnostic import (
    P5MLXAttentionDiagnosticError,
    load_attention_diagnostic_contract,
    parse_metal_allocation_error,
    predicted_score_allocation,
    run_gradient_probe,
    validate_attention_diagnostic_contract,
    write_diagnostic_result,
)


MLX_TEST_AVAILABLE = (
    platform.system() == "Darwin" and importlib.util.find_spec("mlx") is not None
)


class AttentionDiagnosticContractTests(unittest.TestCase):
    def test_predictions_match_exact_factorization(self):
        expected = {
            4096: 1_073_217_600,
            8192: 4_293_918_784,
            16384: 17_177_772_096,
        }
        for tier, byte_count in expected.items():
            with self.subTest(tier=tier):
                prediction = predicted_score_allocation(tier)
                self.assertEqual(byte_count, prediction["predicted_score_bytes"])
                self.assertEqual(tier - 1, prediction["input_length"])
                self.assertEqual(
                    [1, 8, 4, tier - 1, tier - 1],
                    prediction["score_shape"],
                )

    def test_contract_freezes_scope_source_findings_and_stop_policy(self):
        contract = load_attention_diagnostic_contract()
        self.assertFalse(contract["scope"]["restricted_data_allowed"])
        self.assertFalse(contract["scope"]["authorizes_fallback"])
        self.assertEqual(
            "diagnose_only_then_require_new_owner_review",
            contract["stop_policy"],
        )
        tampered = dict(contract)
        tampered["attention_geometry"] = dict(contract["attention_geometry"])
        tampered["attention_geometry"]["query_heads"] = 16
        with self.assertRaisesRegex(
            P5MLXAttentionDiagnosticError, "Attention geometry"
        ):
            validate_attention_diagnostic_contract(tampered)

    def test_metal_error_parser_requires_exact_byte_counts(self):
        parsed = parse_metal_allocation_error(
            "[metal::malloc] Attempting to allocate 17177772096 bytes which is "
            "greater than the maximum allowed buffer size of 14302248960 bytes."
        )
        self.assertEqual(17_177_772_096, parsed["requested_bytes"])
        self.assertEqual(14_302_248_960, parsed["maximum_buffer_bytes"])
        with self.assertRaises(P5MLXAttentionDiagnosticError):
            parse_metal_allocation_error("generic out of memory")

    def test_writer_is_owner_only_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            write_diagnostic_result({"ok": True}, path)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                write_diagnostic_result({"ok": True}, path)


@unittest.skipUnless(MLX_TEST_AVAILABLE, "Apple MLX synthetic probe required")
class AppleMLXAttentionDiagnosticTests(unittest.TestCase):
    def test_small_gradient_probe_executes(self):
        result = run_gradient_probe()
        self.assertEqual("evaluated", result["status"])
        self.assertEqual([1, 32, 256, 128], result["gradient_shape"])


if __name__ == "__main__":
    unittest.main()
