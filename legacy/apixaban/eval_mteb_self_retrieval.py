import os
import faiss
import torch
import numpy as np
import pandas as pd

# Embeddings, metadata, and FAISS index paths
EMB_PATH = "mteb_small_embeddings.pt"
META_PATH = "apixaban_with_mteb_small.csv"
INDEX_PATH = "indexes/mteb_small.index"


def load_data():
    """Load embeddings, metadata, and the FAISS index."""
    if not os.path.exists(EMB_PATH):
        raise FileNotFoundError(f"Missing {EMB_PATH}. Please run embed_mteb_small.py first.")

    if not os.path.exists(META_PATH):
        raise FileNotFoundError(f"Missing {META_PATH}. Please run embed_mteb_small.py first.")

    if not os.path.exists(INDEX_PATH):
        raise FileNotFoundError(
            f"Missing {INDEX_PATH}. Please run build_index_mteb_small.py first."
        )

    embs = torch.load(EMB_PATH)
    if isinstance(embs, torch.Tensor):
        embs = embs.detach().cpu().numpy().astype("float32")
    else:
        embs = np.asarray(embs, dtype="float32")

    meta = pd.read_csv(META_PATH)
    index = faiss.read_index(INDEX_PATH)

    print(f"Embeddings shape: {embs.shape}, metadata rows: {len(meta)}")
    return embs, meta, index


def eval_self_retrieval(embs: np.ndarray, meta: pd.DataFrame, index, k_max: int = 10):
    """
    Self-retrieval evaluation: treat each vector as a query and check if
    the correct document appears in the top-k results.
    """
    n, dim = embs.shape
    print(f"Running self-retrieval evaluation on {n} samples with dim {dim}")

    ks = [1, 3, 5, 10]
    hits = {k: 0 for k in ks}

    for i in range(n):
        query_vec = embs[i : i + 1]
        scores, idxs = index.search(query_vec, k_max)
        idxs = idxs[0]

        for k in ks:
            if i in idxs[:k]:
                hits[k] += 1

        if i < 3:
            print(f"\nSample {i} top-{k_max} indices: {idxs}")
            print(f"Ground truth index: {i}")

    print("\n===== Self-retrieval Results =====")
    for k in ks:
        hit_rate = hits[k] / n
        print(f"Hit@{k}: {hits[k]}/{n} = {hit_rate:.3f}")
    print("==================================")


def main():
    embs, meta, index = load_data()
    eval_self_retrieval(embs, meta, index, k_max=10)


if __name__ == "__main__":
    main()
