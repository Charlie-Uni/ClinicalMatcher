import os
import numpy as np
import pandas as pd
from typing import Dict, Tuple, List

import torch
from torch.utils.data import Dataset, DataLoader
from torch.nn.utils import clip_grad_norm_
from torch import nn

from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    get_linear_schedule_with_warmup,
)

from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score, f1_score
from sklearn.feature_selection import mutual_info_classif
from sklearn.utils.class_weight import compute_class_weight

CSV_PATH = "data/apixaban_processed.csv"
TEXT_COL = "text"
CHECKPOINT_DIR = "checkpoints_llm_mi_best"
CHECKPOINT_PATH = os.path.join(CHECKPOINT_DIR, "model.pt")
MI_OUT_PATH = "mi_llm_logits_ranking.csv"

MODEL_NAME = "distilbert-base-uncased"
NUM_LABELS = 3
MAX_LEN = 256
BATCH_SIZE = 8
NUM_EPOCHS = 5
LEARNING_RATE = 2e-5
WARMUP_RATIO = 0.1
RANDOM_STATE = 42
MAX_GRAD_NORM = 1.0

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def set_seed(seed: int = 42):
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    print("Loaded CSV:", path)
    print("Shape:", df.shape)
    return df


def build_trial_label(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["ideal_candidate", "semi_ideal_candidate"]:
        if col not in df.columns:
            raise ValueError(f"Missing column in CSV: {col}")

    def label_row(row):
        if row["ideal_candidate"] == 1:
            return "ideal"
        if row["semi_ideal_candidate"] == 1:
            return "semi-ideal"
        return "non-ideal"

    df["trial_label"] = df.apply(label_row, axis=1)
    print("\n[Label counts]")
    print(df["trial_label"].value_counts())
    return df


def encode_label(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, int]]:
    label_map = {"non-ideal": 0, "semi-ideal": 1, "ideal": 2}
    if "trial_label" not in df.columns:
        raise ValueError("trial_label column not found, please run build_trial_label first.")
    df["trial_label_id"] = df["trial_label"].map(label_map)
    if df["trial_label_id"].isna().any():
        raise ValueError("Found unmapped labels in trial_label.")
    return df, label_map


class ApixabanTextDataset(Dataset):
    def __init__(self, texts: List[str], labels: List[int], tokenizer, max_len: int = 256):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx: int):
        text = str(self.texts[idx])
        label = int(self.labels[idx])

        encoding = self.tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=self.max_len,
            return_tensors="pt",
        )

        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(label, dtype=torch.long),
        }


def create_dataloader(dataset: Dataset, batch_size: int, shuffle: bool = False) -> DataLoader:
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


def build_model(num_labels: int, class_weights: np.ndarray = None):
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=num_labels,
    )

    if class_weights is not None:
        weight_tensor = torch.tensor(class_weights, dtype=torch.float32).to(DEVICE)
        model.loss_fn = nn.CrossEntropyLoss(weight=weight_tensor)
    else:
        model.loss_fn = nn.CrossEntropyLoss()

    return model.to(DEVICE)


def train_one_epoch(
    model,
    dataloader: DataLoader,
    optimizer,
    scheduler,
    epoch: int,
    total_epochs: int,
) -> float:
    model.train()
    total_loss = 0.0

    for batch in dataloader:
        input_ids = batch["input_ids"].to(DEVICE)
        attention_mask = batch["attention_mask"].to(DEVICE)
        labels = batch["labels"].to(DEVICE)

        optimizer.zero_grad()

        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
        )
        logits = outputs.logits
        loss = model.loss_fn(logits, labels)

        loss.backward()
        clip_grad_norm_(model.parameters(), MAX_GRAD_NORM)
        optimizer.step()
        if scheduler is not None:
            scheduler.step()

        total_loss += loss.item()

    avg_loss = total_loss / len(dataloader)
    print(f"--- Epoch {epoch}/{total_epochs} ---")
    print(f"Train loss: {avg_loss:.4f}")
    return avg_loss


@torch.no_grad()
def evaluate(model, dataloader: DataLoader, split_name: str = "Val") -> Dict:
    model.eval()
    all_labels = []
    all_preds = []
    all_logits = []

    for batch in dataloader:
        input_ids = batch["input_ids"].to(DEVICE)
        attention_mask = batch["attention_mask"].to(DEVICE)
        labels = batch["labels"].to(DEVICE)

        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
        )
        logits = outputs.logits
        preds = torch.argmax(logits, dim=-1)

        all_labels.extend(labels.cpu().numpy())
        all_preds.extend(preds.cpu().numpy())
        all_logits.append(logits.cpu().numpy())

    all_labels = np.array(all_labels)
    all_preds = np.array(all_preds)
    all_logits = np.concatenate(all_logits, axis=0)

    acc = accuracy_score(all_labels, all_preds)
    macro_f1 = f1_score(all_labels, all_preds, average="macro", zero_division=0)
    report = classification_report(all_labels, all_preds, output_dict=True, zero_division=0)

    print(f"[{split_name}] accuracy = {acc:.3f}, macro-F1 = {macro_f1:.3f}")
    return {
        "accuracy": acc,
        "macro_f1": macro_f1,
        "report": report,
        "logits": all_logits,
        "labels": all_labels,
    }


def compute_class_weights(labels: np.ndarray, num_classes: int) -> np.ndarray:
    classes = np.arange(num_classes)
    class_weights = compute_class_weight(
        class_weight="balanced",
        classes=classes,
        y=labels,
    )
    print("\n[Class weights (for CrossEntropyLoss)]")
    for c, w in zip(classes, class_weights):
        print(f"  class {c}: weight={w:.4f}")
    return class_weights


def compute_mi_for_logits_probs(logits: np.ndarray, labels: np.ndarray) -> pd.DataFrame:
    logits = logits.astype(np.float64)
    logits = logits - logits.max(axis=1, keepdims=True)
    exp_logits = np.exp(logits)
    probs = exp_logits / exp_logits.sum(axis=1, keepdims=True)

    num_classes = probs.shape[1]
    rows = []
    mi_scores = []

    for k in range(num_classes):
        feature = probs[:, k].reshape(-1, 1)
        mi = mutual_info_classif(
            feature,
            labels,
            discrete_features=False,
            random_state=42,
        )[0]
        mi_scores.append(mi)
        rows.append({"feature": f"prob_class_{k}", "mi_score": mi})

    mi_mean = float(np.mean(mi_scores))
    rows.append({"feature": "all_probs_mean_MI_over_dims", "mi_score": mi_mean})

    mi_df = pd.DataFrame(rows).sort_values("mi_score", ascending=False)
    return mi_df


def main():
    set_seed(RANDOM_STATE)

    df = load_data(CSV_PATH)
    if TEXT_COL not in df.columns:
        raise ValueError(f"Column `{TEXT_COL}` not found in {CSV_PATH}.")
    df = build_trial_label(df)
    df, label_map = encode_label(df)

    texts = df[TEXT_COL].fillna("").astype(str).tolist()
    labels = df["trial_label_id"].values
    num_classes = len(label_map)

    X_train_val, X_test, y_train_val, y_test = train_test_split(
        texts,
        labels,
        test_size=0.2,
        stratify=labels,
        random_state=RANDOM_STATE,
    )

    X_train, X_val, y_train, y_val = train_test_split(
        X_train_val,
        y_train_val,
        test_size=0.25,
        stratify=y_train_val,
        random_state=RANDOM_STATE,
    )

    print("\n[Split sizes]")
    print(f"Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    train_dataset = ApixabanTextDataset(X_train, y_train, tokenizer, max_len=MAX_LEN)
    val_dataset = ApixabanTextDataset(X_val, y_val, tokenizer, max_len=MAX_LEN)
    test_dataset = ApixabanTextDataset(X_test, y_test, tokenizer, max_len=MAX_LEN)

    train_loader = create_dataloader(train_dataset, BATCH_SIZE, shuffle=True)
    val_loader = create_dataloader(val_dataset, BATCH_SIZE, shuffle=False)
    test_loader = create_dataloader(test_dataset, BATCH_SIZE, shuffle=False)

    class_weights = compute_class_weights(np.array(y_train), num_classes)
    model = build_model(num_classes, class_weights)

    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE)
    total_steps = len(train_loader) * NUM_EPOCHS
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(WARMUP_RATIO * total_steps),
        num_training_steps=total_steps,
    )

    best_state_dict = None
    best_val_f1 = -1.0

    for epoch in range(1, NUM_EPOCHS + 1):
        train_one_epoch(
            model=model,
            dataloader=train_loader,
            optimizer=optimizer,
            scheduler=scheduler,
            epoch=epoch,
            total_epochs=NUM_EPOCHS,
        )

        val_metrics = evaluate(model, val_loader, split_name="Val")
        val_f1 = val_metrics["macro_f1"]

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            best_state_dict = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            print(f"New best Val macro-F1: {val_f1:.3f}")

    if best_state_dict is None:
        raise RuntimeError("Training did not produce a valid checkpoint.")

    model.load_state_dict(best_state_dict)
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    torch.save(best_state_dict, CHECKPOINT_PATH)
    tokenizer.save_pretrained(CHECKPOINT_DIR)
    print(f"Best model checkpoint saved to {CHECKPOINT_PATH}")

    print("\n===== Evaluation on TEST set =====")
    test_metrics = evaluate(model, test_loader, split_name="Test")
    test_report = test_metrics["report"]
    print("\n[Test classification report]")
    for label_idx, label_name in enumerate(label_map.keys()):
        if str(label_idx) in test_report:
            print(f"  Class {label_idx} ({label_name}):", test_report[str(label_idx)])

    logits_arr = test_metrics["logits"]
    labels_arr = test_metrics["labels"]
    print("\n[Logits shape]:", logits_arr.shape)
    print("[Labels shape]:", labels_arr.shape)

    mi_df = compute_mi_for_logits_probs(logits_arr, labels_arr)
    print("\n=== MI scores for LLM logits/probs (TEST set) ===")
    print(mi_df)

    mi_df.to_csv(MI_OUT_PATH, index=False)
    print(f"\nSaved MI results to {MI_OUT_PATH}")


if __name__ == "__main__":
    main()
