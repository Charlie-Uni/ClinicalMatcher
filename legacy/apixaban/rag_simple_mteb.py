import argparse
import textwrap
import subprocess

import pandas as pd
import torch
from sentence_transformers import SentenceTransformer


def load_data():
    print("加载文档向量和元数据...")
    embeddings = torch.load("mteb_small_embeddings.pt", map_location="cpu")
    embeddings = embeddings.to(torch.float32)
    df = pd.read_csv("apixaban_with_mteb_small.csv")
    print(f"加载完成，共 {len(df)} 条记录。")
    print(f"embeddings 设备: {embeddings.device}, 形状: {embeddings.shape}")
    return embeddings, df


def load_encoder():
    model_name = "intfloat/multilingual-e5-small"
    print(f"加载查询编码模型: {model_name}")
    model = SentenceTransformer(model_name, device="cpu")
    return model


def search_top_k(query, encoder, embeddings, df, top_k=5):
    query_emb = encoder.encode(
        [query],
        convert_to_tensor=True,
        normalize_embeddings=True,
    )
    query_emb = query_emb[0].to(embeddings.device)

    with torch.no_grad():
        scores = torch.matmul(embeddings, query_emb).cpu().numpy()

    top_k = min(top_k, len(scores))
    top_idx = scores.argsort()[::-1][:top_k]

    results = []
    for rank, idx in enumerate(top_idx, start=1):
        row = df.iloc[idx]
        results.append(
            {
                "rank": rank,
                "score": float(scores[idx]),
                "note_id": row.get("note_id", ""),
                "hadm_id": row.get("hadm_id", ""),
                "semi_ideal_candidate": row.get("semi_ideal_candidate", ""),
                "ideal_candidate": row.get("ideal_candidate", ""),
                "text": row.get("text", "")[:1200],
            }
        )
    return results


def build_prompt(query, retrieved):
    context_blocks = []
    for r in retrieved:
        meta = (
            f"note_id={r['note_id']}, hadm_id={r['hadm_id']}, "
            f"semi_ideal_candidate={r['semi_ideal_candidate']}, ideal_candidate={r['ideal_candidate']}"
        )
        block = f"[Rank {r['rank']}, score={r['score']:.3f}, {meta}]\n{r['text']}"
        context_blocks.append(block)

    context_text = "\n\n".join(context_blocks)

    system_instruction = textwrap.dedent(
        """
        You are a clinical trial matching assistant.
        You will receive:
        - A clinical question about apixaban trial eligibility.
        - Several discharge summaries that may contain evidence.

        Your tasks:
        1. Carefully read the retrieved notes as evidence.
        2. Use the trial criteria implicitly reflected by the labels:
           - ideal_candidate = 1 means the patient is a good / ideal candidate.
           - semi_ideal_candidate = 1 means the patient is a borderline or suboptimal candidate.
           - Both 0 usually means not a good candidate.
        3. Based on the evidence, answer the question and give EXACTLY ONE final label in English:
           - "ideal"
           - "semi-ideal"
           - "non-ideal"

        Output format (in English):
        - First, a short explanation (2–4 sentences).
        - Last line: "Final label: <ideal / semi-ideal / non-ideal>".
        """
    ).strip()

    user_message = textwrap.dedent(
        f"""
        Question:
        {query}

        Retrieved discharge summaries (evidence):
        {context_text}
        """
    ).strip()

    full_prompt = (
        f"System:\n{system_instruction}\n\n"
        f"User:\n{user_message}\n\n"
        f"Assistant:\n"
    )
    return full_prompt


def call_ollama(ollama_model, prompt):
    cmd = ["ollama", "run", ollama_model]
    try:
        result = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print("❌ 找不到 ollama 命令，请确认已经安装并且在 PATH 里。")
        return None

    if result.returncode != 0:
        print("❌ 调用 ollama 出错：")
        print(result.stderr.decode("utf-8", errors="ignore"))
        return None

    return result.stdout.decode("utf-8", errors="ignore")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top_k", type=int, default=5)
    parser.add_argument("--ollama_model", type=str, default="llama3.1")
    args = parser.parse_args()

    embeddings, df = load_data()
    encoder = load_encoder()

    print("RAG 系统已启动。输入英文问题，按回车运行；直接回车退出。")
    print("示例: Is this patient an ideal candidate for the apixaban trial based on the criteria?\n")

    while True:
        try:
            query = input("> ").strip()
        except EOFError:
            break

        if not query:
            print("退出。")
            break

        retrieved = search_top_k(query, encoder, embeddings, df, top_k=args.top_k)

        print("\nTop retrieved notes:")
        for r in retrieved:
            print("-" * 80)
            print(
                f"Rank {r['rank']} | score={r['score']:.3f} | "
                f"note_id={r['note_id']} | hadm_id={r['hadm_id']} | "
                f"semi_ideal={r['semi_ideal_candidate']} | ideal={r['ideal_candidate']}"
            )

        prompt = build_prompt(query, retrieved)

        print("\n=== 发送给 Llama 的总结性问答 ===")
        answer = call_ollama(args.ollama_model, prompt)
        if answer is None:
            print("本次未能从 Llama 获得回答。")
        else:
            print("\nLLama Answer:\n")
            print(answer)
        print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
