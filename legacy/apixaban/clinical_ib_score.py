import os
import json
from typing import Dict, List

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

CANDIDATES_PATH = "data/apixaban_candidates.jsonl"
LLAMA_PATH = os.environ.get("LLAMA_MODEL_PATH", "meta-llama/Llama-3.1-8B")
MAX_LEN = 1024
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

ALPHA = 1.0
BETA = 5.0

LABEL_MAP = {"non-ideal": 0, "semi-ideal": 1, "ideal": 2}
LABEL_STRINGS = {
    "non-ideal": "non-ideal",
    "semi-ideal": "semi-ideal",
    "ideal": "ideal",
}

IB_ALL_OUT = "data/apixaban_candidates_ib.jsonl"
IB_BEST_OUT = "data/apixaban_ib_best_per_patient.jsonl"

SYSTEM_PROMPT = (
    "You are an AI assistant for clinical trial eligibility matching. "
    "Classify the patient description as one of: ideal, semi-ideal, non-ideal."
)
PROMPT_TEMPLATE = (
    "<<SYS>>\n{system}\n<</SYS>>\n"
    "Patient clinical summary:\n{context}\n\n"
    "Respond with the final judgement only using the form 'Final label: <ideal / semi-ideal / non-ideal>'.\n"
    "Final label: "
)


def load_candidates(path: str) -> List[Dict]:
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    print(f"Loaded {len(data)} patients from {path}")
    return data


def load_model_and_tokenizer():
    tokenizer = AutoTokenizer.from_pretrained(
        LLAMA_PATH,
        use_fast=False,
        padding_side="left",
        trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    config = AutoConfig.from_pretrained(
        LLAMA_PATH,
        trust_remote_code=True,
    )
    if not getattr(config, "rope_scaling", None) or "type" not in config.rope_scaling:
        config.rope_scaling = {"type": "linear", "factor": 1.0}
    model = AutoModelForCausalLM.from_pretrained(
        LLAMA_PATH,
        config=config,
        device_map="auto" if torch.cuda.is_available() else None,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        trust_remote_code=True,
    )
    model.to(DEVICE)
    model.eval()
    print(f"Loaded HuggingFace Llama model from {LLAMA_PATH}")
    return tokenizer, model


def build_label_token_ids(tokenizer) -> Dict[str, List[int]]:
    label_token_ids = {}
    for label, text in LABEL_STRINGS.items():
        tokens = (
            tokenizer(
                text + tokenizer.eos_token,
                add_special_tokens=False,
                return_tensors="pt",
            )["input_ids"][0]
            .tolist()
        )
        label_token_ids[label] = tokens
    return label_token_ids


def build_prompt(candidate_text: str) -> str:
    return PROMPT_TEMPLATE.format(system=SYSTEM_PROMPT, context=candidate_text.strip())


@torch.no_grad()
def compute_label_logprobs(tokenizer, model, prompt: str, label_token_ids: Dict[str, List[int]]):
    max_label_len = max(len(toks) for toks in label_token_ids.values())
    prompt_enc = tokenizer(
        prompt,
        truncation=True,
        max_length=MAX_LEN - max_label_len,
        return_tensors="pt",
        add_special_tokens=False,
    )
    prompt_ids = prompt_enc["input_ids"].to(DEVICE)
    prompt_attention = prompt_enc["attention_mask"].to(DEVICE)

    log_probs = {}
    for label_name, label_tokens in label_token_ids.items():
        label_tensor = torch.tensor(label_tokens, dtype=torch.long, device=DEVICE).unsqueeze(0)
        input_ids = torch.cat([prompt_ids, label_tensor], dim=1)
        attention_mask = torch.cat([prompt_attention, torch.ones_like(label_tensor)], dim=1)
        target_ids = input_ids.clone()
        label_len = label_tensor.size(1)
        target_ids[:, :-label_len] = -100

        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        logits = outputs.logits[:, :-1, :]
        shift_labels = target_ids[:, 1:]

        loss = F.cross_entropy(
            logits.reshape(-1, logits.size(-1)),
            shift_labels.reshape(-1),
            ignore_index=-100,
            reduction="sum",
        )
        log_probs[label_name] = -loss.item()

    return log_probs


def logprobs_to_probs(logprob_dict: Dict[str, float]) -> np.ndarray:
    values = np.array([logprob_dict[label] for label in LABEL_MAP.keys()], dtype=np.float64)
    values -= values.max()
    probs = np.exp(values)
    probs /= probs.sum()
    return probs


@torch.no_grad()
def score_patient_candidates(tokenizer, model, patient: Dict, label_token_ids) -> List[Dict]:
    patient_id = patient["patient_id"]
    label_str = patient["trial_label"]
    if label_str not in LABEL_MAP:
        raise ValueError(f"Unknown label: {label_str}")
    label_id = LABEL_MAP[label_str]

    full_text = patient.get("full_text", "")
    cands_dict: Dict[str, str] = patient.get("candidates", {})

    if not isinstance(full_text, str):
        full_text = ""
    if "full_text" not in cands_dict:
        cands_dict["full_text"] = full_text

    cand_names = sorted(cands_dict.keys())

    full_prompt = build_prompt(full_text)
    full_logprob = compute_label_logprobs(tokenizer, model, full_prompt, label_token_ids)
    full_probs = logprobs_to_probs(full_logprob)

    results = []
    eps = 1e-12

    ordered_names = ["full_text"] + cand_names
    for name in ordered_names:
        cand_text = full_text if name == "full_text" else (cands_dict.get(name) or "")
        prompt = build_prompt(cand_text)
        cand_logprob = compute_label_logprobs(tokenizer, model, prompt, label_token_ids)
        cand_probs = logprobs_to_probs(cand_logprob)

        loss_cls = -np.log(cand_probs[label_id] + eps)
        kl = float(
            np.sum(
                cand_probs
                * (np.log(cand_probs + eps) - np.log(full_probs + eps))
            )
        )
        ib = kl - BETA * loss_cls

        results.append(
            {
                "patient_id": patient_id,
                "trial_label": label_str,
                "candidate_name": name,
                "candidate_text": cand_text,
                "loss_cls": float(loss_cls),
                "kl_to_full": kl,
                "ib_score": float(ib),
            }
        )

    return results


def main():
    patients = load_candidates(CANDIDATES_PATH)
    tokenizer, model = load_model_and_tokenizer()
    label_token_ids = build_label_token_ids(tokenizer)

    all_scored = []
    for i, p in enumerate(patients):
        scored = score_patient_candidates(tokenizer, model, p, label_token_ids)
        all_scored.extend(scored)
        if (i + 1) % 10 == 0:
            print(f"Scored {i+1}/{len(patients)} patients")

    with open(IB_ALL_OUT, "w", encoding="utf-8") as f:
        for rec in all_scored:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Saved all candidate IB scores to {IB_ALL_OUT}")

    by_patient: Dict[str, List[Dict]] = {}
    for rec in all_scored:
        pid = rec["patient_id"]
        by_patient.setdefault(pid, []).append(rec)

    with open(IB_BEST_OUT, "w", encoding="utf-8") as f:
        for pid, cand_list in by_patient.items():
            trial_label = cand_list[0]["trial_label"] if cand_list else "unknown"

            best = min(cand_list, key=lambda x: x["ib_score"])
            worst = max(cand_list, key=lambda x: x["ib_score"])

            out = {
                "patient_id": pid,
                "trial_label": trial_label,
                "best_candidate_name": best["candidate_name"],
                "best_candidate_text": best["candidate_text"],
                "best_ib_score": best["ib_score"],
                "best_loss_cls": best["loss_cls"],
                "best_kl_to_full": best["kl_to_full"],
                "worst_candidate_name": worst["candidate_name"],
                "worst_candidate_text": worst["candidate_text"],
                "worst_ib_score": worst["ib_score"],
                "worst_loss_cls": worst["loss_cls"],
                "worst_kl_to_full": worst["kl_to_full"],
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

    print(f"Saved per-patient best/worst IB candidates to {IB_BEST_OUT}")


if __name__ == "__main__":
    main()
