import os
import torch
import torch.nn.functional as F
import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModel

# 使用 MTEB 榜单上的小模型
MODEL_NAME = "intfloat/multilingual-e5-small"

CSV_PATH = "data/apixaban_processed.csv"  # 你之前整理好的宽表
OUT_CSV_PATH = "apixaban_with_mteb_small.csv"  # 带元数据的新表
OUT_EMB_PATH = "mteb_small_embeddings.pt"  # 向量文件

BATCH_SIZE = 4  # Mac 内存有限，保守一点
MAX_LENGTH = 512  # 每条 note 最多 512 token，够用了


def get_text_col(df: pd.DataFrame) -> str:
    """
    决定用哪一列作为文本输入：
    - 优先用 prompt
    - 没有 prompt 就用 text
    """
    if "prompt" in df.columns:
        return "prompt"
    elif "text" in df.columns:
        return "text"
    else:
        raise ValueError("CSV 里找不到 'prompt' 或 'text' 列，请检查表头。")


def load_model():
    """
    加载 multilingual-e5-small 模型和 tokenizer。
    第一次运行时会自动从 Hugging Face 下载到本地缓存。
    """
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)

    model.to(device)
    model.eval()
    return tokenizer, model, device


def encode_batch(texts, tokenizer, model, device):
    """
    对一批文本做 embedding，返回 shape = (batch_size, dim) 的 tensor。
    multilingual-e5 官方推荐：
      - 文档: 'passage: ...'
      - 查询: 'query: ...'
    这里是“文档离线编码”，统一加 passage 前缀。
    """
    texts = [f"passage: {t}" for t in texts]

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
        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        last_hidden_state = outputs.last_hidden_state.to(torch.float32)

        # mean pooling（按 mask 加权平均）
        masked = last_hidden_state * attention_mask.unsqueeze(-1)
        sum_emb = masked.sum(dim=1)
        lengths = attention_mask.sum(dim=1).unsqueeze(-1)
        emb = sum_emb / lengths

        # L2 归一化，方便后面用 inner product 当 cosine 相似度
        emb = F.normalize(emb, dim=-1)

    return emb.cpu()


def main():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"找不到 {CSV_PATH}，请确认文件路径。")

    df = pd.read_csv(CSV_PATH)
    text_col = get_text_col(df)
    print(f"使用列 '{text_col}' 作为输入文本。")

    tokenizer, model, device = load_model()

    all_embeddings = []
    texts = df[text_col].fillna("").astype(str).tolist()

    for i in tqdm(
        range(0, len(texts), BATCH_SIZE), desc="Encoding with multilingual-e5-small"
    ):
        batch_texts = texts[i : i + BATCH_SIZE]
        emb = encode_batch(batch_texts, tokenizer, model, device)
        all_embeddings.append(emb)

    all_embeddings = torch.cat(all_embeddings, dim=0)
    print("最终 embedding 形状:", all_embeddings.shape)

    # 保存向量
    torch.save(all_embeddings, OUT_EMB_PATH)
    print(f"已保存 embeddings 到 {OUT_EMB_PATH}")

    # 保存带元数据的 CSV：note_id / hadm_id / label / text
    meta_cols = ["note_id", "hadm_id", "semi_ideal_candidate", "ideal_candidate"]
    keep_cols = [c for c in meta_cols if c in df.columns]
    keep_cols.append(text_col)

    meta_df = df[keep_cols].copy()
    meta_df["embedding_index"] = range(len(meta_df))

    meta_df.to_csv(OUT_CSV_PATH, index=False)
    print(f"已保存元数据到 {OUT_CSV_PATH}")


if __name__ == "__main__":
    main()
