"""Fine-tune a lightweight multilingual sequence classifier for risk labels."""

from __future__ import annotations

import argparse
import inspect
import json
import random
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from torch.utils.data import Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments


LABELS = [
    "none",
    "self_harm_or_suicidality",
    "acute_crisis",
    "safeguarding_or_abuse",
    "unclear",
]
ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_MODEL_DIR = ROOT / "models" / "distilbert-risk-classifier"
DEFAULT_OUTPUT_DIR = ROOT / "outputs"


class ReferralRiskDataset(Dataset):
    def __init__(self, rows: list[dict[str, str]], tokenizer, label2id: dict[str, int], max_length: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.label2id = label2id
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        encoded = self.tokenizer(
            row["text"],
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        item["labels"] = torch.tensor(self.label2id[row["label"]], dtype=torch.long)
        return item


def read_jsonl(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run generate_synthetic_dataset.py first.")
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=1, keepdims=True)


def future_route(label: str, confidence: float, threshold: float) -> str:
    if label == "none" and confidence >= threshold:
        return "continue"
    return "clinician_review"


def build_compute_metrics(id2label: dict[int, str], threshold: float):
    none_id = next(index for index, label in id2label.items() if label == "none")

    def compute_metrics(eval_prediction) -> dict[str, float]:
        logits, true_ids = eval_prediction
        probabilities = softmax(logits)
        pred_ids = np.argmax(probabilities, axis=1)
        confidences = np.max(probabilities, axis=1)
        precision, recall, f1, _ = precision_recall_fscore_support(
            true_ids,
            pred_ids,
            average="macro",
            zero_division=0,
        )
        risk_mask = true_ids != none_id
        risk_total = int(np.sum(risk_mask))
        risk_exact_hits = int(np.sum((pred_ids == true_ids) & risk_mask))
        review_hits = 0
        for pred_id, confidence, is_risk in zip(pred_ids, confidences, risk_mask):
            if is_risk and future_route(id2label[int(pred_id)], float(confidence), threshold) == "clinician_review":
                review_hits += 1
        return {
            "accuracy": float(accuracy_score(true_ids, pred_ids)),
            "precision_macro": float(precision),
            "recall_macro": float(recall),
            "f1_macro": float(f1),
            "risk_class_exact_recall": float(risk_exact_hits / risk_total) if risk_total else 0.0,
            "risk_route_recall": float(review_hits / risk_total) if risk_total else 0.0,
        }

    return compute_metrics


def training_arguments(args: argparse.Namespace) -> TrainingArguments:
    kwargs = {
        "output_dir": str(args.output_dir / "trainer_runs"),
        "num_train_epochs": args.epochs,
        "per_device_train_batch_size": args.train_batch_size,
        "per_device_eval_batch_size": args.eval_batch_size,
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "logging_steps": 10,
        "save_total_limit": 2,
        "seed": args.seed,
        "report_to": "none",
    }
    signature = inspect.signature(TrainingArguments.__init__)
    if "eval_strategy" in signature.parameters:
        kwargs["eval_strategy"] = "epoch"
    elif "evaluation_strategy" in signature.parameters:
        kwargs["evaluation_strategy"] = "epoch"
    if "save_strategy" in signature.parameters:
        kwargs["save_strategy"] = "epoch"
    return TrainingArguments(**kwargs)


def build_trainer(model, args, train_dataset, eval_dataset, tokenizer, compute_metrics) -> Trainer:
    kwargs = {
        "model": model,
        "args": training_arguments(args),
        "train_dataset": train_dataset,
        "eval_dataset": eval_dataset,
        "compute_metrics": compute_metrics,
    }
    signature = inspect.signature(Trainer.__init__)
    if "processing_class" in signature.parameters:
        kwargs["processing_class"] = tokenizer
    elif "tokenizer" in signature.parameters:
        kwargs["tokenizer"] = tokenizer
    return Trainer(**kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune the standalone multilingual risk classifier.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--base-model", default="distilbert/distilbert-base-multilingual-cased")
    parser.add_argument("--max-length", type=int, default=160)
    parser.add_argument("--epochs", type=float, default=4.0)
    parser.add_argument("--train-batch-size", type=int, default=8)
    parser.add_argument("--eval-batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--confidence-threshold", type=float, default=0.70)
    parser.add_argument("--seed", type=int, default=20260512)
    args = parser.parse_args()

    set_seed(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.model_dir.mkdir(parents=True, exist_ok=True)

    label2id = {label: index for index, label in enumerate(LABELS)}
    id2label = {index: label for label, index in label2id.items()}

    train_rows = read_jsonl(args.data_dir / "train.jsonl")
    test_rows = read_jsonl(args.data_dir / "test.jsonl")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        num_labels=len(LABELS),
        label2id=label2id,
        id2label=id2label,
    )

    train_dataset = ReferralRiskDataset(train_rows, tokenizer, label2id, args.max_length)
    eval_dataset = ReferralRiskDataset(test_rows, tokenizer, label2id, args.max_length)

    trainer = build_trainer(
        model=model,
        args=args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        tokenizer=tokenizer,
        compute_metrics=build_compute_metrics(id2label, args.confidence_threshold),
    )
    train_result = trainer.train()
    eval_metrics = trainer.evaluate()

    trainer.save_model(str(args.model_dir))
    tokenizer.save_pretrained(str(args.model_dir))

    label_mapping = {
        "labels": LABELS,
        "label2id": label2id,
        "id2label": {str(index): label for index, label in id2label.items()},
        "risk_labels": [label for label in LABELS if label != "none"],
        "confidence_threshold": args.confidence_threshold,
        "future_route_rule": "Only label=none with confidence >= threshold may continue; all other results route to clinician_review.",
    }
    (args.model_dir / "label_mapping.json").write_text(
        json.dumps(label_mapping, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    training_summary = {
        "base_model": args.base_model,
        "model_dir": str(args.model_dir),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_metrics": train_result.metrics,
        "eval_metrics": eval_metrics,
        "confidence_threshold": args.confidence_threshold,
        "seed": args.seed,
    }
    (args.output_dir / "training_summary.json").write_text(
        json.dumps(training_summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(training_summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
