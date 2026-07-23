import os
import torch
import faiss
import numpy as np
import pandas as pd

CONFIGS = [
    {
        "name": "gatortron",
        "emb_path": "gatortron_embeddings.pt",
        "meta_csv": "apixaban_with_gatortron.csv",
        "index_path": "indexes/gatortron.index",
        "meta_out": "indexes/gatortron_meta.csv",
    },
    {
        "name": "nemotron",
        "emb_path": "nemotron_embeddings.pt",
        "meta_csv": "apixaban_with_nemotron.csv",
        "index_path": "indexes/nemotron.index",
        "meta_out": "indexes/nemotron_meta.csv",
    },
]


def build_single_index(cfg):
    print(f"=== 构建 {cfg['name']} 索引 ===")
    if not os.path.exists(cfg["emb_path"]):
        raise FileNotFoundError(f"找不到 {cfg['emb_path']}")
    if not os.path.exists(cfg["meta_csv"]):
        raise FileNotFoundError(f"找不到 {cfg['meta_csv']}")

    embs = torch.load(cfg["emb_path"])  # [N, D]
    if isinstance(embs, torch.Tensor):
        embs = embs.detach().cpu().numpy().astype("float32")
    else:
        embs = np.asarray(embs, dtype="float32")

    # 再做一次 L2 归一化（安全起见）
    faiss.normalize_L2(embs)
    n, d = embs.shape
    print(f"向量形状: {n} x {d}")

    index = faiss.IndexFlatIP(d)
    index.add(embs)

    os.makedirs(os.path.dirname(cfg["index_path"]), exist_ok=True)
    faiss.write_index(index, cfg["index_path"])
    print(f"索引已保存到 {cfg['index_path']}")

    df = pd.read_csv(cfg["meta_csv"])
    df.to_csv(cfg["meta_out"], index=False)
    print(f"元数据已保存到 {cfg['meta_out']}\n")


def main():
    for cfg in CONFIGS:
        build_single_index(cfg)


if __name__ == "__main__":
    main()
