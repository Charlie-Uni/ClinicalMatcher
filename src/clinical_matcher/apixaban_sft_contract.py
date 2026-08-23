import hashlib
import json
import math
from importlib.resources import files
from typing import Any, Dict, Mapping, Sequence

from .splits import canonical_sha256


CONTRACT_RESOURCE = "resources/apixaban-sft-length-contract-1.0.0.json"
INPUT_PLAN_VERSION = "1.1.0"


class ApixabanSFTContractError(ValueError):
    """Raised when the frozen P5 SFT sequence contract is violated."""


def load_apixaban_sft_length_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document = json.loads(resource.read_text(encoding="utf-8"))
    validate_apixaban_sft_length_contract(document)
    return document


def validate_apixaban_sft_length_contract(document: Mapping[str, Any]) -> None:
    if set(document) != {
        "contract_version",
        "model",
        "tokenizer",
        "input_policy",
        "prompt",
        "length_policy",
    }:
        raise ApixabanSFTContractError("SFT length-contract fields are incomplete")
    if document["contract_version"] != "1.0.0":
        raise ApixabanSFTContractError("Unsupported SFT length-contract version")
    model = document["model"]
    if model != {
        "model_id": "meta-llama/Llama-3.1-8B-Instruct",
        "revision": "0e9e39f249a16976918f6564b8830bc894c89659",
    }:
        raise ApixabanSFTContractError("SFT model pin differs from the approved pin")
    tokenizer = document["tokenizer"]
    if set(tokenizer) != {"files", "chat_template_sha256"}:
        raise ApixabanSFTContractError("SFT tokenizer contract is incomplete")
    expected_files = {
        "config.json",
        "generation_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    if set(tokenizer["files"]) != expected_files:
        raise ApixabanSFTContractError("SFT tokenizer file set is not frozen")
    for digest in (*tokenizer["files"].values(), tokenizer["chat_template_sha256"]):
        if not isinstance(digest, str) or len(digest) != 64:
            raise ApixabanSFTContractError("SFT tokenizer SHA-256 is invalid")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ApixabanSFTContractError("SFT tokenizer SHA-256 is invalid")
    policy = document["input_policy"]
    if policy != {
        "input_policy_id": "all-complete-evidence-v1",
        "complete_chunks_only": True,
        "preserve_source_order": True,
        "label_based_selection": False,
        "retriever_used": False,
        "partial_chunk_truncation": False,
    }:
        raise ApixabanSFTContractError("SFT input policy differs from approval")
    prompt = document["prompt"]
    if set(prompt) != {"prompt_version", "system_instruction"}:
        raise ApixabanSFTContractError("SFT prompt contract is incomplete")
    if prompt["prompt_version"] != "apixaban-single-fact-sft-1.0.0":
        raise ApixabanSFTContractError("SFT prompt version differs from approval")
    if "For numeric facts with no value in the note, return unknown." not in prompt[
        "system_instruction"
    ]:
        raise ApixabanSFTContractError("SFT numeric-missing instruction is absent")
    length_policy = document["length_policy"]
    if length_policy != {
        "population": "train_fit_patient_question_grid",
        "output_reserve_tokens": 512,
        "context_tiers": [2048, 4096, 8192, 16384],
        "fit_formula": "rendered_prompt_tokens_plus_512_lte_context_tier",
        "selection_rule": "smallest_tier_with_100_percent_train_fit_rows",
        "percentile_method": "nearest_rank",
        "actual_target_policy": "must_be_lte_512_tokens_no_truncation",
        "holdout_overflow_policy": "measured_failure_abstention_no_truncation",
    }:
        raise ApixabanSFTContractError("SFT length policy differs from approval")


def apixaban_sft_contract_sha256(contract: Mapping[str, Any]) -> str:
    validate_apixaban_sft_length_contract(contract)
    return canonical_sha256(dict(contract))


def build_apixaban_sft_prompt_messages(
    question: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    system_instruction: str,
) -> list[Dict[str, str]]:
    user_payload = {
        "question_id": question["question_id"],
        "question_type": question["question_type"],
        "source_question": question["source_question"],
        "evidence": list(evidence),
    }
    return [
        {"role": "system", "content": system_instruction},
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    ]


def rendered_chat_token_count(
    tokenizer: Any,
    messages: Sequence[Mapping[str, str]],
    *,
    add_generation_prompt: bool,
) -> int:
    try:
        rendered = tokenizer.apply_chat_template(
            list(messages),
            tokenize=True,
            add_generation_prompt=add_generation_prompt,
        )
    except Exception as error:
        raise ApixabanSFTContractError(
            "Tokenizer could not render the frozen SFT chat messages"
        ) from error
    if isinstance(rendered, Mapping):
        rendered = rendered.get("input_ids")
    if rendered is None:
        raise ApixabanSFTContractError("Tokenizer returned no input IDs")
    if hasattr(rendered, "tolist"):
        rendered = rendered.tolist()
    if not isinstance(rendered, list) or (
        rendered and isinstance(rendered[0], (list, tuple))
    ):
        raise ApixabanSFTContractError(
            "Tokenizer must return one flat token-ID sequence"
        )
    if not all(isinstance(item, int) for item in rendered):
        raise ApixabanSFTContractError("Tokenizer returned non-integer token IDs")
    return len(rendered)


def assert_apixaban_sft_sequence_fits(
    tokenizer: Any,
    messages: Sequence[Mapping[str, str]],
    *,
    max_seq_len: int,
    output_reserve_tokens: int,
) -> Dict[str, int]:
    if len(messages) != 3 or [message.get("role") for message in messages] != [
        "system",
        "user",
        "assistant",
    ]:
        raise ApixabanSFTContractError(
            "SFT sequence validation requires system/user/assistant messages"
        )
    prompt_tokens = rendered_chat_token_count(
        tokenizer, messages[:2], add_generation_prompt=True
    )
    full_tokens = rendered_chat_token_count(
        tokenizer, messages, add_generation_prompt=False
    )
    target_tokens = full_tokens - prompt_tokens
    if target_tokens < 0:
        raise ApixabanSFTContractError(
            "Rendered target-token delta cannot be negative"
        )
    if target_tokens > output_reserve_tokens:
        raise ApixabanSFTContractError(
            "Actual SFT target exceeds the frozen output reserve"
        )
    if prompt_tokens + output_reserve_tokens > max_seq_len:
        raise ApixabanSFTContractError(
            "Reserved SFT sequence exceeds the frozen context tier"
        )
    if full_tokens > max_seq_len:
        raise ApixabanSFTContractError(
            "Actual rendered SFT sequence exceeds the frozen context tier"
        )
    return {
        "prompt_tokens": prompt_tokens,
        "target_tokens": target_tokens,
        "full_tokens": full_tokens,
    }


def nearest_rank_percentile(values: Sequence[int], percentile: int) -> int:
    if not values:
        raise ApixabanSFTContractError("Cannot summarize an empty length population")
    if not 0 <= percentile <= 100:
        raise ApixabanSFTContractError("Percentile must be between zero and 100")
    ordered = sorted(values)
    if percentile == 0:
        return ordered[0]
    rank = math.ceil((percentile / 100) * len(ordered))
    return ordered[rank - 1]


def sha256_file(path: Any) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
