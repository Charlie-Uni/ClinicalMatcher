import json
import os
from collections import Counter

import pandas as pd

IB_JSONL_PATH = "data/apixaban_ib_best_per_patient.jsonl"


def load_ib_results(path: str):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    print(f"Loaded {len(rows)} patients from {path}")
    return pd.DataFrame(rows)


def main():
    if not os.path.exists(IB_JSONL_PATH):
        raise FileNotFoundError(f"{IB_JSONL_PATH} not found, please check the path.")

    df = load_ib_results(IB_JSONL_PATH)

    print("\n[Basic info]")
    print("Columns:", list(df.columns))
    print(df.head(3))

    print("\n[Label distribution]")
    print(df["trial_label"].value_counts())

    print("\n[Best candidate type counts]")
    print(df["best_candidate_name"].value_counts())

    print("\n[Worst candidate type counts]")
    print(df["worst_candidate_name"].value_counts())

    print("\n[Cross-tab: trial_label x best_candidate_name]")
    crosstab = pd.crosstab(df["trial_label"], df["best_candidate_name"])
    print(crosstab)

    if "best_ib_score" in df.columns:
        print("\n[Mean scores per best_candidate_name]")
        print(
            df.groupby("best_candidate_name")[
                ["best_ib_score", "best_loss_cls", "best_kl_to_full"]
            ].agg(["mean", "std", "count"])
        )

    if "worst_ib_score" in df.columns:
        print("\n[Mean scores per worst_candidate_name]")
        print(
            df.groupby("worst_candidate_name")[
                ["worst_ib_score", "worst_loss_cls", "worst_kl_to_full"]
            ].agg(["mean", "std", "count"])
        )

    if {"best_ib_score", "worst_ib_score"} <= set(df.columns):
        df["ib_gap"] = df["worst_ib_score"] - df["best_ib_score"]
        print("\n[IB gap statistics: worst - best]")
        print(df["ib_gap"].describe())

        print("\n[How many patients have IB(worst) > IB(best)?]")
        print((df["ib_gap"] > 0).value_counts())


if __name__ == "__main__":
    main()
