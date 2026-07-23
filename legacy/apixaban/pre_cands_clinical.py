import os
import re
import json
import random
from typing import List, Dict

import pandas as pd

CSV_PATH = "data/apixaban_processed.csv"
TEXT_COL = "text"
ID_COL = "patient_id"
LABEL_COL = "trial_label"
OUT_PATH = "data/apixaban_candidates.jsonl"
RANDOM_SEED = 42

random.seed(RANDOM_SEED)


def split_into_sentences(text: str) -> List[str]:
    if not isinstance(text, str):
        return []

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    chunks = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            continue
        parts = re.split(r"(?<=[\.\?!])\s+", block)
        for p in parts:
            p = p.strip()
            if len(p) >= 5:
                chunks.append(p)
    return chunks


def sentences_with_keywords(sentences: List[str], keywords: List[str]) -> List[str]:
    kw_lower = [k.lower() for k in keywords]
    selected = []
    for s in sentences:
        s_low = s.lower()
        if any(k in s_low for k in kw_lower):
            selected.append(s)
    return selected


def build_candidates_for_patient(
    full_text: str,
    hi_risk_keywords: List[str],
    lab_keywords: List[str],
    comorb_keywords: List[str],
) -> Dict[str, str]:
    sents = split_into_sentences(full_text)
    if not sents:
        return {
            "full_text": "",
            "hi_risk_core": "",
            "labs_only": "",
            "comorb_only": "",
            "random_drop": "",
        }

    full_candidate = " ".join(sents)

    hi_risk_sents = sentences_with_keywords(sents, hi_risk_keywords)
    labs_sents = sentences_with_keywords(sents, lab_keywords)
    comorb_sents = sentences_with_keywords(sents, comorb_keywords)

    n = len(sents)
    keep_idx = sorted(random.sample(range(n), max(1, n // 2)))
    random_sents = [sents[i] for i in keep_idx]

    def join_or_empty(sent_list: List[str]) -> str:
        return " ".join(sent_list) if sent_list else ""

    return {
        "full_text": full_candidate,
        "hi_risk_core": join_or_empty(hi_risk_sents),
        "labs_only": join_or_empty(labs_sents),
        "comorb_only": join_or_empty(comorb_sents),
        "random_drop": join_or_empty(random_sents),
    }


def ensure_trial_label(df: pd.DataFrame) -> pd.DataFrame:
    if LABEL_COL in df.columns:
        return df

    required = {"ideal_candidate", "semi_ideal_candidate"}
    if not required.issubset(df.columns):
        raise ValueError(
            f"Column `{LABEL_COL}` not found and cannot be built because "
            f"{required} columns are missing."
        )

    def label_row(row):
        if row["ideal_candidate"] == 1:
            return "ideal"
        if row["semi_ideal_candidate"] == 1:
            return "semi-ideal"
        return "non-ideal"

    df[LABEL_COL] = df.apply(label_row, axis=1)
    print(f"[Info] Built `{LABEL_COL}` column from ideal/semi_ideal flags.")
    return df


def main():
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"CSV not found at {CSV_PATH}")

    df = pd.read_csv(CSV_PATH)
    print("Loaded CSV:", CSV_PATH)
    print("Shape:", df.shape)

    if TEXT_COL not in df.columns:
        raise ValueError(f"Column `{TEXT_COL}` not found in CSV.")

    df = ensure_trial_label(df)

    if ID_COL in df.columns:
        ids = df[ID_COL].astype(str).tolist()
    else:
        print(f"[Warning] `{ID_COL}` not found, using row index as id.")
        ids = [str(i) for i in range(len(df))]

    texts = df[TEXT_COL].fillna("").astype(str).tolist()
    labels = df[LABEL_COL].astype(str).tolist()

    hi_risk_keywords = [
        "atrial fibrillation", "a-fib", "afib",
        "heart failure", "reduced ejection fraction",
        "stroke", "cva", "tia",
        "bleeding", "gastrointestinal bleed", "intracranial hemorrhage",
        "hypertension", "high blood pressure",
        "chads2", "cha2ds2-vasc"
    ]

    lab_keywords = [
        "creatinine", "creat", "cr ", "ast", "alt",
        "bilirubin", "bili", "platelet", "plt",
        "hemoglobin", "hgb", "glucose", "blood sugar"
    ]

    comorb_keywords = [
        "diabetes", "t2d",
        "bipolar", "depression", "mdd", "schizophrenia",
        "mental", "psychiatric",
        "decision capacity", "capacity", "cognition", "med_decisions",
        "comorbidity", "comorbid", "copd"
    ]

    with open(OUT_PATH, "w", encoding="utf-8") as out_f:
        count = 0
        for pid, text, label in zip(ids, texts, labels):
            candidates = build_candidates_for_patient(
                full_text=text,
                hi_risk_keywords=hi_risk_keywords,
                lab_keywords=lab_keywords,
                comorb_keywords=comorb_keywords,
            )

            record = {
                "patient_id": pid,
                "trial_label": label,
                "full_text": text,
                "candidates": candidates,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1

    print(f"Saved {count} records with candidates to {OUT_PATH}")


if __name__ == "__main__":
    main()
