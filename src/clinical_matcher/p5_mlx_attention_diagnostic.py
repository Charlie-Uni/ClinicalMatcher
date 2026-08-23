import importlib.metadata
import json
import os
import platform
import re
import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, Mapping

from .p5_mlx_gate import _self_hash, _utc_now


DIAGNOSTIC_RESOURCE = "resources/p5-mlx-attention-diagnostic-1.0.0.json"
ALLOCATION_ERROR = re.compile(
    r"Attempting to allocate (?P<requested>[0-9]+) bytes .* "
    r"maximum allowed buffer size of (?P<maximum>[0-9]+) bytes"
)


class P5MLXAttentionDiagnosticError(ValueError):
    """Raised when the frozen synthetic attention diagnostic is violated."""


def load_attention_diagnostic_contract() -> Dict[str, Any]:
    document = json.loads(
        files("clinical_matcher")
        .joinpath(DIAGNOSTIC_RESOURCE)
        .read_text(encoding="utf-8")
    )
    validate_attention_diagnostic_contract(document)
    return document


def predicted_score_allocation(context_tier: int) -> Dict[str, Any]:
    if context_tier not in {4096, 8192, 16384}:
        raise P5MLXAttentionDiagnosticError(
            "Diagnostic context tier must be exactly 4096, 8192, or 16384"
        )
    input_length = context_tier - 1
    query_heads = 32
    key_value_heads = 8
    repeats = query_heads // key_value_heads
    predicted_bytes = 2 * query_heads * input_length**2
    return {
        "context_tier": context_tier,
        "input_length": input_length,
        "score_shape": [1, key_value_heads, repeats, input_length, input_length],
        "predicted_score_bytes": predicted_bytes,
    }


def validate_attention_diagnostic_contract(document: Mapping[str, Any]) -> None:
    if set(document) != {
        "diagnostic_contract_version",
        "approved_on",
        "scope",
        "failed_gate_evidence",
        "attention_geometry",
        "allocation_prediction",
        "probe_protocol",
        "official_source_audit",
        "stop_policy",
    }:
        raise P5MLXAttentionDiagnosticError("Diagnostic contract fields differ")
    expected = {
        tier["context_tier"]: tier
        for tier in document.get("allocation_prediction", {}).get("tiers", [])
    }
    if document.get("diagnostic_contract_version") != "1.0.0":
        raise P5MLXAttentionDiagnosticError("Unexpected diagnostic version")
    if document.get("approved_on") != "2026-08-24":
        raise P5MLXAttentionDiagnosticError("Diagnostic approval date differs")
    if document.get("scope") != {
        "synthetic_only": True,
        "restricted_data_allowed": False,
        "changes_training_configuration": False,
        "authorizes_fallback": False,
    }:
        raise P5MLXAttentionDiagnosticError("Diagnostic scope differs from approval")
    if document.get("failed_gate_evidence") != [
        {
            "manifest_sha256": (
                "43937dea18fe54609c549edfd69ff8bedacfebd2e9131b5b0d8d2d79d080c2d5"
            ),
            "requested_allocation_bytes": 17177772096,
        },
        {
            "manifest_sha256": (
                "1d8b751d2608a7c74f8410474fb96d99af9ba50dc1a7bf629b06347de977b720"
            ),
            "requested_allocation_bytes": 17177772096,
        },
    ]:
        raise P5MLXAttentionDiagnosticError("Failed-gate evidence differs")
    if set(expected) != {4096, 8192, 16384}:
        raise P5MLXAttentionDiagnosticError("Diagnostic tiers are incomplete")
    prediction_contract = document["allocation_prediction"]
    if prediction_contract.get("formula") != (
        "bytes_per_element * query_heads * "
        "(context_tier - causal_shift_tokens)^2"
    ) or prediction_contract.get("score_shape_formula") != [
        "batch_size",
        "key_value_heads",
        "grouped_query_repeats",
        "input_length",
        "input_length",
    ]:
        raise P5MLXAttentionDiagnosticError("Diagnostic formula differs")
    for context_tier, tier in expected.items():
        prediction = predicted_score_allocation(context_tier)
        if tier != {
            "context_tier": context_tier,
            "input_length": prediction["input_length"],
            "predicted_score_bytes": prediction["predicted_score_bytes"],
        }:
            raise P5MLXAttentionDiagnosticError("Diagnostic byte prediction differs")
    geometry = document.get("attention_geometry")
    if geometry != {
        "batch_size": 1,
        "query_heads": 32,
        "key_value_heads": 8,
        "grouped_query_repeats": 4,
        "head_dimension": 128,
        "dtype": "bfloat16",
        "bytes_per_element": 2,
        "causal_shift_tokens": 1,
    }:
        raise P5MLXAttentionDiagnosticError("Attention geometry differs from Llama 8B")
    probe = document.get("probe_protocol", {})
    if probe != {
        "allocation_probe_environment": {"python": "3.11.16", "mlx": "0.31.2"},
        "allocation_probe": "explicit_pinned_sdpa_fallback_qk_transpose_matmul",
        "allocation_probe_lengths": [4096, 8192, 16384],
        "gradient_probe": "fast_sdpa_causal_query_gradient",
        "gradient_probe_input_length": 256,
        "gradient_probe_environments": [
            {"label": "pinned", "mlx": "0.31.2"},
            {"label": "latest_stable", "mlx": "0.32.1"},
        ],
        "process_isolation_required": True,
        "full_model_or_trainer_allowed": False,
    }:
        raise P5MLXAttentionDiagnosticError("Diagnostic probe protocol differs")
    source_audit = document.get("official_source_audit", {})
    if source_audit != {
        "pinned": {
            "mlx_version": "0.31.2",
            "commit": "68cf2fddd8de5edd8ab3d926391772b2e2cedad8",
            "fast_cpp_sha256": (
                "fa9c12db58e5aee9cff7ed02c155413be22701bdb3987e25a664f2607ffe318e"
            ),
            "metal_sdpa_cpp_sha256": (
                "d09e7ec5101bf93754761ad5b8381fd874599c0642eab161bc8d148abaf95f66"
            ),
            "training_forces_unfused_fallback": True,
            "metal_vjp_use_fallback": True,
            "metal_vjp_eval_gpu": "NYI",
        },
        "latest_stable": {
            "mlx_version": "0.32.1",
            "commit": "3a6219917e4535575ce5bce2fc2ba27a483a709b",
            "metal_sdpa_cpp_sha256": (
                "397f250d897dc5f1d0757c7abed8edc92cc7b6de0fb59ad5a7fd1dfd8c003ca2"
            ),
            "training_forces_unfused_fallback": True,
            "metal_vjp_use_fallback": True,
            "metal_vjp_eval_gpu": "NYI",
        },
        "main_checked_at": {
            "checked_at": "2026-08-24",
            "commit": "d9077d8316ad7305497a3ecf2296bd0e0e99a627",
            "committed_at": "2026-08-23T10:07:03Z",
            "metal_sdpa_cpp_sha256": (
                "215ebac6495706cf01a2635c4dcec29802e130165ed8704f212d789679d2e673"
            ),
            "training_forces_unfused_fallback": True,
            "metal_vjp_use_fallback": True,
            "metal_vjp_eval_gpu": "NYI",
        },
    }:
        raise P5MLXAttentionDiagnosticError(
            "Official-source SDPA finding differs from frozen audit"
        )
    if document.get("stop_policy") != "diagnose_only_then_require_new_owner_review":
        raise P5MLXAttentionDiagnosticError("Diagnostic stop policy differs")


def parse_metal_allocation_error(message: str) -> Dict[str, int]:
    match = ALLOCATION_ERROR.search(message)
    if match is None:
        raise P5MLXAttentionDiagnosticError(
            "Metal failure did not expose the expected allocation byte counts"
        )
    return {
        "requested_bytes": int(match.group("requested")),
        "maximum_buffer_bytes": int(match.group("maximum")),
    }


def _runtime() -> Dict[str, str]:
    return {
        "python": platform.python_version(),
        "mlx": importlib.metadata.version("mlx"),
    }


def _git_state() -> Dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise P5MLXAttentionDiagnosticError("Diagnostic git commit is invalid")
    tracked_diff = subprocess.run(
        ["git", "diff", "--quiet"],
        check=False,
        capture_output=True,
    )
    if tracked_diff.returncode not in {0, 1}:
        raise P5MLXAttentionDiagnosticError("Cannot inspect diagnostic worktree")
    staged_diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        check=False,
        capture_output=True,
    )
    if staged_diff.returncode not in {0, 1}:
        raise P5MLXAttentionDiagnosticError("Cannot inspect diagnostic index")
    return {
        "implementation_commit": commit,
        "tracked_worktree_clean": (
            tracked_diff.returncode == 0 and staged_diff.returncode == 0
        ),
    }


def _base_result(probe: str) -> Dict[str, Any]:
    contract = load_attention_diagnostic_contract()
    result = {
        "diagnostic_result_version": "1.0.0",
        "generated_at": _utc_now(),
        "scope": "synthetic_attention_diagnostic_only",
        "probe": probe,
        "runtime": _runtime(),
        "diagnostic_contract_sha256": _self_hash(
            {**contract, "manifest_sha256": "pending"}
        ),
    }
    result.update(_git_state())
    return result


def run_allocation_probe(context_tier: int) -> Dict[str, Any]:
    import mlx.core as mx

    contract = load_attention_diagnostic_contract()
    required_runtime = contract["probe_protocol"]["allocation_probe_environment"]
    runtime = _runtime()
    if runtime != required_runtime:
        raise P5MLXAttentionDiagnosticError(
            f"Allocation probe requires {required_runtime}, observed {runtime}"
        )
    prediction = predicted_score_allocation(context_tier)
    input_length = prediction["input_length"]

    q = mx.zeros((1, 8, 4, input_length, 128), dtype=mx.bfloat16)
    k = mx.zeros((1, 8, 1, input_length, 128), dtype=mx.bfloat16)
    mx.eval(q, k)
    mx.reset_peak_memory()
    scores = mx.matmul(q, mx.swapaxes(k, -1, -2))
    observed_score_bytes = scores.nbytes
    if list(scores.shape) != prediction["score_shape"]:
        raise P5MLXAttentionDiagnosticError("Fallback score shape differs")
    if observed_score_bytes != prediction["predicted_score_bytes"]:
        raise P5MLXAttentionDiagnosticError("Fallback score bytes differ")

    result = _base_result("explicit_pinned_sdpa_fallback_qk_transpose_matmul")
    result["prediction"] = prediction
    result["observed_score_shape"] = list(scores.shape)
    result["observed_score_bytes"] = observed_score_bytes
    try:
        mx.eval(scores)
    except RuntimeError as error:
        allocation = parse_metal_allocation_error(str(error))
        result["status"] = (
            "matched_predicted_max_buffer_failure"
            if (
                context_tier == 16384
                and allocation["requested_bytes"] == observed_score_bytes
                and allocation["maximum_buffer_bytes"] == 14302248960
            )
            else "unexpected_failure"
        )
        result["allocation_failure"] = allocation
        result["peak_memory_bytes"] = mx.get_peak_memory()
    else:
        result["status"] = (
            "unexpected_success" if context_tier == 16384 else "evaluated"
        )
        result["allocation_failure"] = None
        result["peak_memory_bytes"] = mx.get_peak_memory()
    result["manifest_sha256"] = _self_hash(result)
    return result


def run_gradient_probe(input_length: int = 256) -> Dict[str, Any]:
    import mlx.core as mx

    contract = load_attention_diagnostic_contract()
    if input_length != contract["probe_protocol"]["gradient_probe_input_length"]:
        raise P5MLXAttentionDiagnosticError("Gradient probe length differs")

    q = mx.zeros((1, 32, input_length, 128), dtype=mx.bfloat16)
    k = mx.zeros((1, 8, input_length, 128), dtype=mx.bfloat16)
    v = mx.ones((1, 8, input_length, 128), dtype=mx.bfloat16)
    mx.eval(q, k, v)
    mx.reset_peak_memory()

    def loss(query):
        output = mx.fast.scaled_dot_product_attention(
            query,
            k,
            v,
            scale=128**-0.5,
            mask="causal",
        )
        return output.astype(mx.float32).sum()

    gradient = mx.grad(loss)(q)
    mx.eval(gradient)
    result = _base_result("fast_sdpa_causal_query_gradient")
    result.update(
        {
            "status": "evaluated",
            "input_length": input_length,
            "query_shape": list(q.shape),
            "key_value_shape": list(k.shape),
            "gradient_shape": list(gradient.shape),
            "gradient_dtype": str(gradient.dtype),
            "peak_memory_bytes": mx.get_peak_memory(),
            "routing_interpretation": (
                "capability_is_determined_by_hash_bound_official_source_audit"
            ),
        }
    )
    result["manifest_sha256"] = _self_hash(result)
    return result


def write_diagnostic_result(document: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
