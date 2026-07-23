import os
import json

import torch
import torch.nn.functional as F
import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModel


MODEL_NAME = "UFNLP/gatortron-large"
CSV_PATH = "data/apixaban_processed.csv"  # 输入 CSV
OUT_CSV_PATH = "apixaban_with_gatortron.csv"  # 带有 embedding 的 CSV（只存 id 和文本）
OUT_EMB_PATH = "gatortron_embeddings.pt"  # 纯 embedding 张量单独存成 pt 文件

BATCH_SIZE = 1  # 先用 1，防止 OOM；觉得稳了可以改成 2 / 4
MAX_LENGTH = 512  # GatorTron 最大长度是 512 tokens


def get_prompt_column(df: pd.DataFrame) -> str:
    """自动判断用哪一列作为输入文本。"""
    if "prompt" in df.columns:
        return "prompt"
    elif "text" in df.columns:
        return "text"
    else:
        raise ValueError("CSV 里找不到 'prompt' 或 'text' 列，请检查表头。")


def load_model():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.to(device)
    model.eval()

    return tokenizer, model, device


def encode_batch(texts, tokenizer, model, device):
    """对一个 batch 的文本做 mean pooling 得到句向量。"""
    enc = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=MAX_LENGTH,
        return_tensors="pt",
    )

    input_ids = enc["input_ids"].to(device)
    attention_mask = enc["attention_mask"].to(device)

    with torch.no_grad():
        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
        )
        last_hidden_state = outputs.last_hidden_state  # [B, L, H]
        last_hidden_state = last_hidden_state.to(torch.float32)

        # mean pooling
        masked = last_hidden_state * attention_mask.unsqueeze(-1)
        sum_emb = masked.sum(dim=1)
        lengths = attention_mask.sum(dim=1).unsqueeze(-1)
        emb = sum_emb / lengths
        emb = F.normalize(emb, dim=-1)

    return emb.cpu()


def main():
    # 1) 读 CSV
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"找不到 {CSV_PATH}，请确认路径。")

    df = pd.read_csv(CSV_PATH)
    prompt_col = get_prompt_column(df)
    print(f"使用列 '{prompt_col}' 作为输入文本。")

    # 2) 加载模型
    tokenizer, model, device = load_model()

    # 3) 遍历数据做 embedding
    all_embeddings = []
    texts = df[prompt_col].fillna("").astype(str).tolist()

    for i in tqdm(range(0, len(texts), BATCH_SIZE), desc="Encoding with GatorTron"):
        batch_texts = texts[i : i + BATCH_SIZE]
        emb = encode_batch(batch_texts, tokenizer, model, device)
        all_embeddings.append(emb)

    # 拼接所有 batch
    all_embeddings = torch.cat(all_embeddings, dim=0)  # [N, H]
    print("Embedding 形状：", all_embeddings.shape)

    # 4) 保存
    # 4.1 保存 embedding 张量
    torch.save(all_embeddings, OUT_EMB_PATH)
    print(f"已保存 embedding 到 {OUT_EMB_PATH}")

    # 4.2 再保存一个“轻量版 CSV”，方便之后做索引 / 对照
    meta_cols = ["note_id", "hadm_id"]
    keep_cols = [c for c in meta_cols if c in df.columns]
    keep_cols.append(prompt_col)

    meta_df = df[keep_cols].copy()
    meta_df["embedding_index"] = range(len(meta_df))  # 行号，对应 all_embeddings 的下标

    meta_df.to_csv(OUT_CSV_PATH, index=False)
    print(f"已保存元数据到 {OUT_CSV_PATH}")


if __name__ == "__main__":
    main()
