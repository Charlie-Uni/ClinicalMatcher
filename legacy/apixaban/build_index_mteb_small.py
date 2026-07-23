import os

import faiss
import numpy as np
import pandas as pd
import torch

EMB_PATH = "mteb_small_embeddings.pt"
META_CSV = "apixaban_with_mteb_small.csv"
INDEX_PATH = "indexes/mteb_small.index"
META_OUT = "indexes/mteb_small_meta.csv"


def main():
    if not os.path.exists(EMB_PATH):
        raise FileNotFoundError(f"找不到 {EMB_PATH}，请先运行 embed_mteb_small.py")
    if not os.path.exists(META_CSV):
        raise FileNotFoundError(f"找不到 {META_CSV}，请先运行 embed_mteb_small.py")

    embs = torch.load(EMB_PATH)
    if isinstance(embs, torch.Tensor):
        embs = embs.detach().cpu().numpy().astype("float32")
    else:
        embs = np.asarray(embs, dtype="float32")

    faiss.normalize_L2(embs)

    n, d = embs.shape
    print(f"向量形状：{n} x {d}")

    index = faiss.IndexFlatIP(d)
    index.add(embs)

    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    faiss.write_index(index, INDEX_PATH)
    print(f"索引已写入 {INDEX_PATH}")

    meta = pd.read_csv(META_CSV)
    meta.to_csv(META_OUT, index=False)
    print(f"元数据已写入 {META_OUT}")


if __name__ == "__main__":
    main()
