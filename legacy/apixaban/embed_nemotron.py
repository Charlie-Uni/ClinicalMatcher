import os

import torch
import torch.nn.functional as F
import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModel


MODEL_NAME = "nvidia/llama-embed-nemotron-8b"
CSV_PATH = "data/apixaban_processed.csv"
OUT_CSV_PATH = "apixaban_with_nemotron.csv"
OUT_EMB_PATH = "nemotron_embeddings.pt"

BATCH_SIZE = 1
MAX_LENGTH = 4096


def get_prompt_column(df: pd.DataFrame) -> str:
    if "prompt" in df.columns:
        return "prompt"
    elif "text" in df.columns:
        return "text"
    else:
        raise ValueError("CSV 里找不到 'prompt' 或 'text' 列，请检查表头。")


def load_model():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    attn_impl = "flash_attention_2" if torch.cuda.is_available() else "eager"

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_NAME,
        trust_remote_code=True,
        padding_side="left",
    )

    model = AutoModel.from_pretrained(
        MODEL_NAME,
        trust_remote_code=True,
        torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
        attn_implementation=attn_impl,
    )

    model.to(device)
    model.eval()

    return tokenizer, model, device


def encode_batch(texts, tokenizer, model, device):
    enc = tokenizer(
        text=texts,
        max_length=MAX_LENGTH,
        padding=True,
        truncation=True,
        return_tensors="pt",
    )

    input_ids = enc["input_ids"].to(device)
    attention_mask = enc["attention_mask"].to(device)

    with torch.no_grad():
        last_hidden_state = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
        ).last_hidden_state.to(torch.float32)

        # Nemotron 官方示例：mean pooling + L2 normalize
        emb = last_hidden_state.sum(dim=1) / attention_mask.sum(dim=1)[..., None]
        emb = F.normalize(emb, dim=-1)

    return emb.cpu()


def main():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"找不到 {CSV_PATH}，请确认路径。")

    df = pd.read_csv(CSV_PATH)
    prompt_col = get_prompt_column(df)
    print(f"使用列 '{prompt_col}' 作为输入文本。")

    tokenizer, model, device = load_model()

    all_embeddings = []
    texts = df[prompt_col].fillna("").astype(str).tolist()

    for i in tqdm(range(0, len(texts), BATCH_SIZE), desc="Encoding with Nemotron"):
        batch_texts = texts[i : i + BATCH_SIZE]
        emb = encode_batch(batch_texts, tokenizer, model, device)
        all_embeddings.append(emb)

    all_embeddings = torch.cat(all_embeddings, dim=0)
    print("Embedding 形状：", all_embeddings.shape)

    torch.save(all_embeddings, OUT_EMB_PATH)
    print(f"已保存 embedding 到 {OUT_EMB_PATH}")

    meta_cols = ["note_id", "hadm_id"]
    keep_cols = [c for c in meta_cols if c in df.columns]
    keep_cols.append(prompt_col)

    meta_df = df[keep_cols].copy()
    meta_df["embedding_index"] = range(len(meta_df))

    meta_df.to_csv(OUT_CSV_PATH, index=False)
    print(f"已保存元数据到 {OUT_CSV_PATH}")


if __name__ == "__main__":
    main()
