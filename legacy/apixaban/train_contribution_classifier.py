import os
import json
import argparse
import random
from collections import defaultdict

import torch
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForSequenceClassification, get_linear_schedule_with_warmup


def set_seed(seed: int = 42):
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def load_jsonl(path):
    data = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data.append(json.loads(line))
    return data


class ContributionDataset(Dataset):
    def __init__(self, examples, tokenizer, max_length: int = 512):
        self.examples = examples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, idx):
        ex = self.examples[idx]
        text = ex["text"]
        label = int(ex["label"])

        cand_name = ex.get("candidate_name", "")
        trial_label = ex.get("trial_label", "")

        prefix = f"[CAND={cand_name}] [TRIAL={trial_label}] "
        full_text = prefix + text

        encoded = self.tokenizer(
            full_text,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )

        item = {
            "input_ids": encoded["input_ids"].squeeze(0),
            "attention_mask": encoded["attention_mask"].squeeze(0),
            "labels": torch.tensor(label, dtype=torch.long),
        }
        return item


def group_split_by_patient(examples, train_ratio=0.8, seed=42):
    groups = defaultdict(list)
    for ex in examples:
        pid = ex["patient_id"]
        groups[pid].append(ex)

    patient_ids = list(groups.keys())
    random.Random(seed).shuffle(patient_ids)

    n_train = int(len(patient_ids) * train_ratio)
    train_pids = set(patient_ids[:n_train])

    train_set, val_set = [], []
    for pid, exs in groups.items():
        if pid in train_pids:
            train_set.extend(exs)
        else:
            val_set.extend(exs)

    return train_set, val_set


def compute_accuracy(logits, labels):
    preds = torch.argmax(logits, dim=-1)
    correct = (preds == labels).sum().item()
    total = labels.size(0)
    return correct / total if total > 0 else 0.0


def train(
    data_path: str,
    model_name_or_path: str,
    output_dir: str,
    max_length: int = 512,
    batch_size: int = 8,
    num_epochs: int = 3,
    learning_rate: float = 5e-5,
    weight_decay: float = 0.01,
    warmup_ratio: float = 0.1,
    seed: int = 42,
):

    set_seed(seed)

    os.makedirs(output_dir, exist_ok=True)

    print(f"Loading data from {data_path} ...")
    all_data = load_jsonl(data_path)
    print(f"Total examples: {len(all_data)}")

    for k in ["text", "label", "patient_id"]:
        if k not in all_data[0]:
            raise ValueError(f"Key '{k}' not found in dataset example. Please check your jsonl format.")

    train_data, val_data = group_split_by_patient(all_data, train_ratio=0.8, seed=seed)
    print(f"Train examples: {len(train_data)}")
    print(f"Val examples:   {len(val_data)}")

    def label_stats(data, name):
        from collections import Counter
        c = Counter(int(ex["label"]) for ex in data)
        print(f"[{name}] label counts:", c)

    label_stats(train_data, "train")
    label_stats(val_data, "val")

    print(f"Loading tokenizer and model: {model_name_or_path}")
    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name_or_path,
        num_labels=2,
    )

    device = torch.device(
        "cuda"
        if torch.cuda.is_available()
        else "mps"
        if torch.backends.mps.is_available()
        else "cpu"
    )
    model.to(device)
    print(f"Using device: {device}")

    train_dataset = ContributionDataset(train_data, tokenizer, max_length=max_length)
    val_dataset = ContributionDataset(val_data, tokenizer, max_length=max_length)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

    no_decay = ["bias", "LayerNorm.weight"]
    optimizer_grouped_parameters = [
        {
            "params": [
                p
                for n, p in model.named_parameters()
                if not any(nd in n for nd in no_decay)
            ],
            "weight_decay": weight_decay,
        },
        {
            "params": [
                p
                for n, p in model.named_parameters()
                if any(nd in n for nd in no_decay)
            ],
            "weight_decay": 0.0,
        },
    ]
    optimizer = AdamW(optimizer_grouped_parameters, lr=learning_rate)

    total_steps = num_epochs * len(train_loader)
    warmup_steps = int(total_steps * warmup_ratio)

    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_steps,
    )

    best_val_acc = 0.0

    for epoch in range(1, num_epochs + 1):
        print(f"\n===== Epoch {epoch}/{num_epochs} =====")

        model.train()
        train_loss = 0.0
        train_correct = 0
        train_total = 0

        for batch in tqdm(train_loader, desc="Training"):
            batch = {k: v.to(device) for k, v in batch.items()}

            optimizer.zero_grad()
            outputs = model(
                input_ids=batch["input_ids"],
                attention_mask=batch["attention_mask"],
                labels=batch["labels"],
            )
            loss = outputs.loss
            logits = outputs.logits

            loss.backward()
            optimizer.step()
            scheduler.step()

            train_loss += loss.item() * batch["labels"].size(0)
            train_correct += (logits.argmax(dim=-1) == batch["labels"]).sum().item()
            train_total += batch["labels"].size(0)

        avg_train_loss = train_loss / train_total
        train_acc = train_correct / train_total if train_total > 0 else 0.0
        print(f"Train loss: {avg_train_loss:.4f}, Train acc: {train_acc:.4f}")

        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0

        with torch.no_grad():
            for batch in tqdm(val_loader, desc="Validation"):
                batch = {k: v.to(device) for k, v in batch.items()}
                outputs = model(
                    input_ids=batch["input_ids"],
                    attention_mask=batch["attention_mask"],
                    labels=batch["labels"],
                )
                loss = outputs.loss
                logits = outputs.logits

                val_loss += loss.item() * batch["labels"].size(0)
                val_correct += (logits.argmax(dim=-1) == batch["labels"]).sum().item()
                val_total += batch["labels"].size(0)

        avg_val_loss = val_loss / val_total
        val_acc = val_correct / val_total if val_total > 0 else 0.0
        print(f"Val   loss: {avg_val_loss:.4f}, Val   acc: {val_acc:.4f}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            save_path = os.path.join(output_dir, "best_model")
            os.makedirs(save_path, exist_ok=True)
            print(f"New best val acc: {best_val_acc:.4f}, saving model to {save_path}")
            model.save_pretrained(save_path)
            tokenizer.save_pretrained(save_path)

    last_path = os.path.join(output_dir, "last_model")
    os.makedirs(last_path, exist_ok=True)
    model.save_pretrained(last_path)
    tokenizer.save_pretrained(last_path)
    print(f"\nTraining finished. Best val acc = {best_val_acc:.4f}")
    print(f"Best model saved to: {os.path.join(output_dir, 'best_model')}")
    print(f"Last model saved to: {last_path}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--data_path",
        type=str,
        default="data/contribution_dataset.jsonl",
        help="Path to contribution_dataset.jsonl",
    )
    parser.add_argument(
        "--model_name_or_path",
        type=str,
        default="distilbert-base-uncased",
        help="HF model name or local path",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="models/contribution_distilbert",
    )
    parser.add_argument("--max_length", type=int, default=512)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--num_epochs", type=int, default=3)
    parser.add_argument("--learning_rate", type=float, default=5e-5)
    parser.add_argument("--weight_decay", type=float, default=0.01)
    parser.add_argument("--warmup_ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    train(
        data_path=args.data_path,
        model_name_or_path=args.model_name_or_path,
        output_dir=args.output_dir,
        max_length=args.max_length,
        batch_size=args.batch_size,
        num_epochs=args.num_epochs,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        warmup_ratio=args.warmup_ratio,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
