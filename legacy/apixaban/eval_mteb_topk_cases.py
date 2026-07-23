import os
from typing import Dict

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoTokenizer

# ==== Path configuration ====
EMBED_MODEL_NAME = "intfloat/multilingual-e5-small"
EMB_PATH = "mteb_small_embeddings.pt"
META_CSV = "apixaban_with_mteb_small.csv"
EVAL_CSV = "data/eval_cases.csv"
MAX_LENGTH = 512


def load_encoder():
    """Load multilingual-e5-small to embed evaluation questions."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[Encoder] Using device: {device}")
    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_NAME)
    model = AutoModel.from_pretrained(EMBED_MODEL_NAME)
    model.to(device)
    model.eval()
    return tokenizer, model, device


def encode_query(text: str, tokenizer, model, device: str) -> torch.Tensor:
    """Encode a natural-language question into a 1 x dim torch tensor."""
    query_text = "query: " + text.strip()

    enc = tokenizer(
        [query_text],
        padding=True,
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    )

    input_ids = enc["input_ids"].to(device)
    attention_mask = enc["attention_mask"].to(device)

    with torch.no_grad():
        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        last_hidden = outputs.last_hidden_state.to(torch.float32)
        masked = last_hidden * attention_mask.unsqueeze(-1)
        sum_emb = masked.sum(dim=1)
        lengths = attention_mask.sum(dim=1).unsqueeze(-1)
        emb = sum_emb / lengths
        emb = F.normalize(emb, dim=-1)  # L2 normalization

    return emb.cpu()  # [1, dim]


def load_doc_embeddings():
    """Load precomputed document embeddings and metadata."""
    if not os.path.exists(EMB_PATH):
        raise FileNotFoundError(
            f"Missing {EMB_PATH}. Please run embed_mteb_small.py first."
        )

    if not os.path.exists(META_CSV):
        raise FileNotFoundError(
            f"Missing {META_CSV}. Please run embed_mteb_small.py first."
        )

    embs = torch.load(EMB_PATH)
    if isinstance(embs, torch.Tensor):
        embs = embs.to(torch.float32)
    else:
        embs = torch.tensor(np.asarray(embs, dtype="float32"))

    embs = F.normalize(embs, dim=-1)  # Re-normalize to be safe.

    meta = pd.read_csv(META_CSV)
    for col in ["semi_ideal_candidate", "ideal_candidate"]:
        if col in meta.columns:
            meta[col] = meta[col].fillna(0).astype(int)

    print(f"[Docs] Embedding shape = {embs.shape}, documents = {len(meta)}")
    return embs, meta


def load_eval_cases():
    """Load the manually curated evaluation set."""
    if not os.path.exists(EVAL_CSV):
        raise FileNotFoundError(
            f"Missing evaluation file {EVAL_CSV}. Please create data/eval_cases.csv."
        )

    df = pd.read_csv(EVAL_CSV)
    df["GT_label"] = df["GT_label"].str.strip().str.lower()
    print(f"[Eval] Loaded {len(df)} evaluation cases.")
    return df


def is_relevant(doc_row: pd.Series, gt_label: str) -> bool:
    """
    Determine whether a document is relevant for the GT_label.

    Rules:
      - ideal: ideal_candidate == 1
      - semi-ideal: semi_ideal_candidate == 1
      - non-ideal: both ideal_candidate and semi_ideal_candidate == 0
    """
    ideal = int(doc_row.get("ideal_candidate", 0))
    semi = int(doc_row.get("semi_ideal_candidate", 0))

    if gt_label == "ideal":
        return ideal == 1
    if gt_label == "semi-ideal":
        return semi == 1
    if gt_label == "non-ideal":
        return (ideal == 0) and (semi == 0)
    return False


def main():
    # 1. Load document embeddings, metadata, encoder, and evaluation cases.
    doc_embs, meta = load_doc_embeddings()  # [N, dim]
    tokenizer, model, device = load_encoder()
    df_eval = load_eval_cases()

    ks = [3, 5, 10, 15]
    hit_counts: Dict[int, int] = {k: 0 for k in ks}
    num_cases = len(df_eval)

    for _, row in df_eval.iterrows():
        case_id = row.get("case_id")
        question = str(row.get("question", ""))
        gt_label = str(row.get("GT_label", "")).lower().strip()

        print("\n" + "=" * 80)
        print(f"[Case {case_id}] GT_label = {gt_label}")
        truncated_question = question[:200]
        ellipsis = " ..." if len(question) > 200 else ""
        print(f"Question: {truncated_question}{ellipsis}")

        # 2. Encode the question.
        q_emb = encode_query(question, tokenizer, model, device)  # [1, dim]

        # 3. Compute cosine similarity via inner product (embeddings are L2 normalized).
        scores = (doc_embs @ q_emb.squeeze(0).unsqueeze(-1)).squeeze(-1)  # [N]
        scores_np = scores.numpy()
        sorted_indices = np.argsort(-scores_np)  # Descending order.

        print("\nTop-5 retrieved results (manual inspection):")
        for rank, idx in enumerate(sorted_indices[:5], start=1):
            mrow = meta.iloc[idx]
            print(
                f"  Rank {rank} | score={scores_np[idx]:.3f} | "
                f"note_id={mrow.get('note_id', '')} | "
                f"hadm_id={mrow.get('hadm_id', '')} | "
                f"semi_ideal={mrow.get('semi_ideal_candidate', '')} | "
                f"ideal={mrow.get('ideal_candidate', '')}"
            )

        # 4. Check whether top-k contains any relevant document for each k.
        case_hits: Dict[int, bool] = {}
        for k in ks:
            top_k_indices = sorted_indices[:k]
            relevant_found = False
            for idx in top_k_indices:
                doc = meta.iloc[idx]
                if is_relevant(doc, gt_label):
                    relevant_found = True
                    break

            case_hits[k] = relevant_found
            if relevant_found:
                hit_counts[k] += 1

        print("Hit summary by k for this case:")
        for k in ks:
            status = "1 (hit)" if case_hits[k] else "0 (miss)"
            print(f"  Hit@{k}: {status}")

    # 5. Aggregate metrics.
    print("\n" + "=" * 80)
    print("===== Top-k Retrieval Evaluation (based on GT_label) =====")
    for k in ks:
        hit = hit_counts[k]
        hit_rate = hit / num_cases
        print(f"Hit@{k}: {hit}/{num_cases} = {hit_rate:.3f}")
    print("===========================================================")


if __name__ == "__main__":
    main()
