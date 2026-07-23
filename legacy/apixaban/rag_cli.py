import argparse
import os

import faiss
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel

# -------- 基本配置 --------
GATORTRON_NAME = "UFNLP/gatortron-large"
NEMOTRON_NAME = "nvidia/llama-embed-nemotron-8b"

INDEX_DIR = "indexes"
TOP_K_DEFAULT = 5


# -------- 加载索引 + 元数据 --------
def load_index_and_meta(model_key: str):
    if model_key == "gatortron":
        index_path = os.path.join(INDEX_DIR, "gatortron.index")
        meta_path = os.path.join(INDEX_DIR, "gatortron_meta.csv")
    elif model_key == "nemotron":
        index_path = os.path.join(INDEX_DIR, "nemotron.index")
        meta_path = os.path.join(INDEX_DIR, "nemotron_meta.csv")
    else:
        raise ValueError("model_key 只能是 'gatortron' 或 'nemotron'")

    if not os.path.exists(index_path):
        raise FileNotFoundError(f"找不到索引文件: {index_path}")
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"找不到元数据文件: {meta_path}")

    index = faiss.read_index(index_path)
    meta = pd.read_csv(meta_path)
    print(f"已加载索引 {index_path} 和元数据 {meta_path}，共 {len(meta)} 条记录。")
    return index, meta


# -------- 加载 embedding 模型（用于 query 编码） --------
def load_embedder(model_key: str):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    if model_key == "gatortron":
        tokenizer = AutoTokenizer.from_pretrained(GATORTRON_NAME)
        model = AutoModel.from_pretrained(GATORTRON_NAME)
    elif model_key == "nemotron":
        attn_impl = "flash_attention_2" if torch.cuda.is_available() else "eager"
        tokenizer = AutoTokenizer.from_pretrained(
            NEMOTRON_NAME,
            trust_remote_code=True,
            padding_side="left",
        )
        model = AutoModel.from_pretrained(
            NEMOTRON_NAME,
            trust_remote_code=True,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            attn_implementation=attn_impl,
        )
    else:
        raise ValueError("model_key 只能是 'gatortron' 或 'nemotron'")

    model.to(device)
    model.eval()
    return tokenizer, model, device


# -------- 对查询做 embedding --------
def encode_query(query: str, model_key: str, tokenizer, model, device) -> np.ndarray:
    if model_key == "gatortron":
        enc = tokenizer(
            query,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        input_ids = enc["input_ids"].to(device)
        attention_mask = enc["attention_mask"].to(device)

        with torch.no_grad():
            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            last_hidden_state = outputs.last_hidden_state.to(torch.float32)
            masked = last_hidden_state * attention_mask.unsqueeze(-1)
            sum_emb = masked.sum(dim=1)
            lengths = attention_mask.sum(dim=1).unsqueeze(-1)
            emb = sum_emb / lengths
            emb = F.normalize(emb, dim=-1)

    else:  # nemotron
        enc = tokenizer(
            text=[query],
            max_length=4096,
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
            emb = last_hidden_state.sum(dim=1) / attention_mask.sum(dim=1)[..., None]
            emb = F.normalize(emb, dim=-1)

    emb_np = emb.detach().cpu().numpy().astype("float32")
    return emb_np  # shape: [1, D]


# -------- 从索引里检索 --------
def retrieve(query: str, model_key: str, top_k: int):
    index, meta = load_index_and_meta(model_key)
    tokenizer, model, device = load_embedder(model_key)
    q_emb = encode_query(query, model_key, tokenizer, model, device)

    scores, idxs = index.search(q_emb, top_k)
    scores = scores[0]
    idxs = idxs[0]

    # 自动识别文本列名
    text_col = "prompt" if "prompt" in meta.columns else "text"

    results = []
    for score, idx in zip(scores, idxs):
        row = meta.iloc[int(idx)]
        item = {
            "score": float(score),
            "note_id": row.get("note_id", None),
            "hadm_id": row.get("hadm_id", None),
            "semi_ideal_candidate": row.get("semi_ideal_candidate", None)
            if "semi_ideal_candidate" in meta.columns
            else None,
            "ideal_candidate": row.get("ideal_candidate", None)
            if "ideal_candidate" in meta.columns
            else None,
            "text": row[text_col],
        }
        results.append(item)

    return results


# -------- 构造给 Llama8 的 RAG Prompt --------
def build_rag_prompt(question: str, contexts, trial_name="Apixaban trial for AFib"):
    lines = []
    lines.append(
        "You are a clinical-trial matching assistant. "
        "Your job is to read EHR discharge summaries and decide whether the patient fits the Apixaban trial."
    )
    lines.append(f"Trial name: {trial_name}")
    lines.append(
        "Use ONLY the information in the retrieved notes. "
        "If something is not mentioned, treat it as unknown."
    )
    lines.append("")
    lines.append("Retrieved patient notes:")

    for i, ctx in enumerate(contexts, start=1):
        lines.append(
            f"\n[NOTE {i}] "
            f"note_id={ctx['note_id']}, hadm_id={ctx['hadm_id']}, "
            f"semi_ideal_candidate={ctx['semi_ideal_candidate']}, "
            f"ideal_candidate={ctx['ideal_candidate']}, "
            f"similarity_score={ctx['score']:.4f}"
        )
        text = ctx["text"]
        if len(text) > 1200:
            text = text[:1200] + " ...[TRUNCATED]"
        lines.append(text)

    lines.append("\nNow answer the user question based on the above notes.")
    lines.append("Question:")
    lines.append(question)
    lines.append(
        "\nIn your answer, please:\n"
        "1. State whether the patient is likely eligible for the trial (Yes/No/Uncertain).\n"
        "2. List the key inclusion criteria met.\n"
        "3. List the key exclusion criteria triggered.\n"
        "4. If uncertain, explain what information is missing."
    )

    return "\n".join(lines)


# -------- 这里接 Llama8 --------
import subprocess
import textwrap


def call_llama8(prompt: str) -> str:
    """
    调用本地 Ollama 的 llama3.1 模型。
    如果你用的是别的名字（比如 llama3.1:8b），把命令里的模型名改一下即可。
    """
    try:
        cmd = ["ollama", "run", "llama3.1"]
        print("\n[DEBUG] 调用命令：", " ".join(cmd))

        result = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )

        if result.returncode != 0:
            err_msg = textwrap.dedent(
                f"""
                调用 Ollama 失败，退出码 {result.returncode}。
                stderr:
                {result.stderr}
                """
            )
            return err_msg

        return result.stdout

    except FileNotFoundError:
        return (
            "找不到 `ollama` 命令。\n"
            "请确认你已经安装了 Ollama，并且在终端里能运行 `ollama help`。"
        )
    except Exception as e:
        return f"调用 Llama8 过程中出现异常：{e}"


# -------- CLI 主程序 --------
def main():
    parser = argparse.ArgumentParser(description="Simple RAG CLI for Apixaban trial")
    parser.add_argument(
        "--model",
        type=str,
        choices=["gatortron", "nemotron"],
        default="gatortron",
        help="选择用哪个 embedding 模型做检索",
    )
    parser.add_argument(
        "--top_k",
        type=int,
        default=TOP_K_DEFAULT,
        help="检索的文档数量",
    )
    args = parser.parse_args()

    print(f"使用模型: {args.model}, top_k = {args.top_k}")
    question = input(
        "请输入你的问题（英文效果更好，例如: Is this patient an ideal candidate for apixaban trial?）\n> "
    )

    contexts = retrieve(question, args.model, args.top_k)
    print(f"\n检索到 {len(contexts)} 条结果。")

    for i, c in enumerate(contexts, start=1):
        print(f"\n========== RESULT {i}  (score={c['score']:.4f}) ==========")
        print(f"note_id: {c['note_id']}, hadm_id: {c['hadm_id']}")
        print(
            f"semi_ideal_candidate: {c['semi_ideal_candidate']}, ideal_candidate: {c['ideal_candidate']}"
        )
        preview = c["text"][:300].replace("\n", " ")
        print(f"text preview: {preview}...")

    rag_prompt = build_rag_prompt(question, contexts)
    answer = call_llama8(rag_prompt)

    print("\n================= Llama8 Answer (stub) =================\n")
    print(answer)


if __name__ == "__main__":
    main()
