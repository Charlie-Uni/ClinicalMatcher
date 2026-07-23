import json
import os

IB_INPUT_PATH = "data/apixaban_ib_best_per_patient.jsonl"
OUT_PATH = "data/contribution_dataset.jsonl"


def load_jsonl(path):
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data.append(json.loads(line))
    return data


def write_jsonl(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for obj in data:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main():
    if not os.path.exists(IB_INPUT_PATH):
        raise FileNotFoundError(
            f"Cannot find {IB_INPUT_PATH}. Make sure you have run the IB score script and that this path is correct."
        )

    ib_rows = load_jsonl(IB_INPUT_PATH)
    print(f"Loaded {len(ib_rows)} rows from {IB_INPUT_PATH}")

    out_examples = []

    for row in ib_rows:
        patient_id = row.get("patient_id")
        trial_label = row.get("trial_label")

        best_name = row.get("best_candidate_name")
        best_text = row.get("best_candidate_text", "")
        best_ib = row.get("best_ib_score")
        best_loss = row.get("best_loss_cls")
        best_kl = row.get("best_kl_to_full")

        if best_text:
            out_examples.append(
                {
                    "patient_id": patient_id,
                    "trial_label": trial_label,
                    "candidate_name": best_name,
                    "text": best_text,
                    "label": 1,
                    "ib_score": best_ib,
                    "loss_cls": best_loss,
                    "kl_to_full": best_kl,
                    "which": "best",
                }
            )

        worst_name = row.get("worst_candidate_name")
        worst_text = row.get("worst_candidate_text", "")
        worst_ib = row.get("worst_ib_score")
        worst_loss = row.get("worst_loss_cls")
        worst_kl = row.get("worst_kl_to_full")

        if worst_text:
            out_examples.append(
                {
                    "patient_id": patient_id,
                    "trial_label": trial_label,
                    "candidate_name": worst_name,
                    "text": worst_text,
                    "label": 0,
                    "ib_score": worst_ib,
                    "loss_cls": worst_loss,
                    "kl_to_full": worst_kl,
                    "which": "worst",
                }
            )

    print(f"Built {len(out_examples)} contribution examples")

    write_jsonl(OUT_PATH, out_examples)
    print(f"Saved contribution dataset to {OUT_PATH}")


if __name__ == "__main__":
    main()
