import os
import subprocess
from typing import List

import faiss
import pandas as pd
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel

# ==== 配置部分 ====
EMBED_MODEL_NAME = "intfloat/multilingual-e5-small"
INDEX_PATH = "indexes/mteb_small.index"
META_PATH = "indexes/mteb_small_meta.csv"
MAX_LENGTH = 512


def load_encoder():
    """加载 multilingual-e5-small 编码器，用来对“问题”做 embedding。"""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Embedding 使用设备: {device}")

    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_NAME)
    model = AutoModel.from_pretrained(EMBED_MODEL_NAME)
    model.to(device)
    model.eval()
    return tokenizer, model, device


def encode_query(text: str, tokenizer, model, device: str):
    """把用户问题编码成一个 1 x dim 的向量（float32 numpy）"""
    q = "query: " + text.strip()

    enc = tokenizer(
        [q],
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
        emb = F.normalize(emb, dim=-1)

    return emb.cpu().numpy().astype("float32")


def load_index():
    """加载 Faiss 索引 + 元数据 CSV。"""
    if not os.path.exists(INDEX_PATH):
        raise FileNotFoundError(
            f"找不到索引文件 {INDEX_PATH}，请先运行 build_index_mteb_small.py"
        )
    if not os.path.exists(META_PATH):
        raise FileNotFoundError(
            f"找不到元数据文件 {META_PATH}，请确认 build_index_mteb_small.py 已正常执行"
        )

    index = faiss.read_index(INDEX_PATH)
    meta = pd.read_csv(META_PATH)
    return index, meta


def retrieve(
    query: str,
    index,
    meta: pd.DataFrame,
    tokenizer,
    model,
    device: str,
    top_k: int = 5,
):
    """用问题做向量检索，返回 top_k 条记录及其文本。"""
    q_emb = encode_query(query, tokenizer, model, device)
    scores, idxs = index.search(q_emb, top_k)

    scores = scores[0]
    idxs = idxs[0]

    results = []
    for rank, (score, idx) in enumerate(zip(scores, idxs), start=1):
        row = meta.iloc[idx]
        text = row.get("prompt", row.get("text", ""))
        text = str(text)[:1200]  # 防止太长

        results.append(
            {
                "rank": rank,
                "score": float(score),
                "note_id": row.get("note_id", ""),
                "hadm_id": row.get("hadm_id", ""),
                "semi_ideal_candidate": row.get("semi_ideal_candidate", ""),
                "ideal_candidate": row.get("ideal_candidate", ""),
                "text": text,
            }
        )

    return results


def build_prompt(question: str, docs: List[dict]) -> str:
    parts = []

    system_instructions = """
You are an AI assistant for clinical trial eligibility matching.

Task:
- Your goal is to decide whether the CURRENT PATIENT is an IDEAL, SEMI-IDEAL, or NON-IDEAL candidate
  for an Apixaban clinical trial.
- You must base your judgement PRIMARILY on:
  (1) the clinical features of the CURRENT PATIENT described in the question
      (e.g. CHADS2, LVEF, creatinine, hemoglobin, platelets, bilirubin, AST, comorbidities),
  (2) the high-level trial criteria listed below.
- You may use the retrieved EHR examples ONLY as secondary reference (for calibration),
  NOT as a vote or majority label.

Clinical criteria (rule-based intuition, but NOT rigid exclusion rules):
1. AFib Safe:
   - Non-valvular atrial fibrillation.
   - No clear evidence of major bleeding risk (no recent major stroke, no uncontrolled hemorrhage, etc.).
2. Stroke Risk / Heart Function:
   - CHADS2 score <= 2 is low risk; CHADS2 >= 3 is higher risk but does NOT automatically exclude the patient.
   - Left ventricular ejection fraction (LVEF) >= 30% is generally acceptable.
   - LVEF < 30% is a limitation and often pushes the patient to SEMI-IDEAL.
   - If CHADS2 or LVEF are missing, treat them as UNKNOWN (do not automatically downgrade).
3. Lab Safety (approximate thresholds; think in three levels):

   Major red-flag abnormalities (strongly suggest NON-IDEAL):
   - Creatinine > 3.0 mg/dL
   - Hemoglobin < 9.0 g/dL
   - Platelets < 50 x10^9/L
   - Total bilirubin >= 3.0 mg/dL
   - AST >= 200 U/L

   Moderate abnormalities (borderline → often SEMI-IDEAL, unless combined with other severe issues):
   - Creatinine between 2.0–3.0 mg/dL
   - Hemoglobin between 9.0–10.0 g/dL
   - Platelets between 50–100 x10^9/L
   - Total bilirubin between 1.5–3.0 mg/dL
   - AST between 80–200 U/L

   Normal / mildly abnormal (generally safe):
   - Values within the usual trial-like thresholds:
     Creatinine <= 2.5 mg/dL,
     Hemoglobin >= 10 g/dL,
     Platelets >= 100 x10^9/L,
     Total bilirubin <= 1.5 mg/dL,
     AST <= 80 U/L.

4. Mental Health:
   - If the question clearly mentions severe bipolar disorder, schizophrenia, or major depression
     that impairs medical decision-making, this is a negative factor.
   - If mental health is NOT mentioned, treat it as UNKNOWN (do NOT downgrade by default).

5. Metabolic / Comorbidity Control:
   - Diabetes and hypertension reasonably controlled is acceptable.
   - Uncontrolled comorbidities that make anticoagulation clearly unsafe are a negative factor.
   - If control status is not described, treat it as UNKNOWN (do NOT automatically downgrade).

IMPORTANT:
- In this task, you usually receive ONLY basic numeric values and very brief clinical context.
  Missing information (e.g. unknown CHADS2, unknown LVEF, no mention of mental health)
  should NOT by itself push the patient to NON-IDEAL.
- Focus on obvious, clinically significant problems in the CURRENT PATIENT’s labs and heart function.

How to reason (follow these steps):

1. First, summarise the key clinical features of the CURRENT PATIENT from the question:
   - AFib status (if mentioned), stroke risk (CHADS2), heart function (LVEF),
     lab values (creatinine, hemoglobin, platelets, bilirubin, AST),
     and any comorbidities.

2. Then, using ONLY the CURRENT PATIENT’s data, classify each domain:
   - AFib safety: satisfied / violated / unknown.
   - Stroke risk / heart function: satisfied / limited / clearly problematic / unknown.
   - Labs: mostly normal, moderately abnormal, OR severely abnormal (use the thresholds above).
   - Mental health: OK / clearly problematic / unknown.
   - Metabolic & comorbidities: reasonably controlled / clearly uncontrolled / unknown.

3. Use the retrieved EHR examples and their labels ONLY as supporting context:
   - You may mention them briefly to justify that similar patients were treated as acceptable or risky.
   - DO NOT simply copy the majority label from retrieved notes.

4. Decide ONE final label based on the overall pattern:

   - "ideal":
     - No major red-flag lab abnormalities.
     - At most mild or moderate issues, but nothing that clearly makes anticoagulation unsafe.
     - It is acceptable that CHADS2, LVEF, or mental health are UNKNOWN if nothing dangerous is described.

   - "semi-ideal":
     - Mostly safe, but with at least one meaningful limitation or moderate abnormality
       (e.g. LVEF < 30%, moderately abnormal labs, or comorbidities that require caution),
     - AND no single very severe red-flag abnormality.
     - Use this when the patient looks "borderline" or "suboptimal" but not clearly unsafe.

   - "non-ideal":
     - Clear, major safety problems:
       - Any of the major red-flag lab abnormalities (e.g. creatinine > 3.0, very low hemoglobin,
         very low platelets, very high bilirubin, very high AST),
       - OR multiple moderate abnormalities combined that make anticoagulation clearly unsafe,
       - OR explicit strong contraindications (e.g. active major bleeding, severe hepatic failure).

Answer format:
1. Provide a short, step-by-step explanation in English:
   - Explicitly state for each domain (AFib safety, heart/stroke risk, labs, mental health, comorbidities)
     whether it is satisfied, limited, clearly unsafe, or unknown.
   - Optionally mention retrieved examples as supporting context.
2. At the very end, output exactly one line in the format (all lower-case label):
   Final label: ideal
   or
   Final label: semi-ideal
   or
   Final label: non-ideal
    parts.append(system_instructions.strip())
    parts.append("\nQuestion:\n" + question.strip())
    parts.append("\nRelevant documents (from EHR):\n")
"""

    for d in docs:
        header = (
            f"[Doc {d['rank']}] "
            f"note_id={d['note_id']}, hadm_id={d['hadm_id']}, "
            f"semi_ideal={d['semi_ideal_candidate']}, "
            f"ideal={d['ideal_candidate']}, "
            f"score={d['score']:.4f}"
        )
        parts.append(header)
        parts.append(d["text"])
        parts.append("\n---\n")

    return "\n".join(parts)


def call_ollama(prompt: str, model_name: str = "llama3.1") -> str:
    """调用本地 Ollama 的 Llama 模型生成回答。"""
    try:
        proc = subprocess.run(
            ["ollama", "run", model_name],
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            print("⚠️ 调用 ollama 失败，stderr:")
            print(proc.stderr)
        return proc.stdout
    except FileNotFoundError:
        print("❌ 找不到 'ollama' 命令，请先安装 Ollama 并拉取模型，例如：")
        print("   brew install ollama")
        print("   ollama pull llama3.1")
        return ""


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--top_k", type=int, default=5, help="每次检索返回多少条文档")
    parser.add_argument(
        "--ollama_model",
        type=str,
        default="llama3.1",
        help="Ollama 中的模型名称，例如 llama3, llama3.1 等",
    )
    args = parser.parse_args()

    print("加载 Faiss 索引和元数据...")
    index, meta = load_index()
    print(f"索引加载完成，共 {len(meta)} 条记录。")

    print("加载 multilingual-e5-small 编码器...")
    tokenizer, model, device = load_encoder()

    print("\nRAG 系统已启动。输入英文问题，按回车运行；直接回车退出。")
    print("示例: Is this patient an ideal candidate for the apixaban trial based on the criteria?")

    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break

        if q == "":
            print("退出。")
            break

        # 1. 检索
        docs = retrieve(q, index, meta, tokenizer, model, device, top_k=args.top_k)

        print("\n=== Top retrieved documents (for debug) ===")
        for d in docs:
            print(
                f"[{d['rank']}] score={d['score']:.4f}, "
                f"note_id={d['note_id']}, hadm_id={d['hadm_id']}, "
                f"semi_ideal={d['semi_ideal_candidate']}, ideal={d['ideal_candidate']}"
            )
        print("==========================================\n")

        # 2. 组 prompt
        prompt = build_prompt(q, docs)

        # 3. 调 Llama
        print("正在调用本地 Llama 模型生成回答...\n")
        answer = call_ollama(prompt, model_name=args.ollama_model)

        print("=== Llama Answer ===")
        print(answer)
        print("====================")


if __name__ == "__main__":
    main()
