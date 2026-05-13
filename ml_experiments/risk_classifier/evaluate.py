"""Evaluate the standalone multilingual risk classifier."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, precision_recall_fscore_support
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from transformers.utils import logging as transformers_logging


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

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
transformers_logging.set_verbosity_error()
transformers_logging.disable_progress_bar()


def read_jsonl(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found.")
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_labels(model_dir: Path) -> list[str]:
    mapping_path = model_dir / "label_mapping.json"
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        return list(mapping["labels"])
    return LABELS


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=1, keepdims=True)


def future_route(label: str, confidence: float, threshold: float) -> str:
    if label == "none" and confidence >= threshold:
        return "continue"
    return "clinician_review"


def predict_rows(
    rows: list[dict[str, str]],
    model_dir: Path,
    labels: list[str],
    max_length: int,
    batch_size: int,
    threshold: float,
) -> list[dict[str, object]]:
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    results: list[dict[str, object]] = []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            encoded = tokenizer(
                [row["text"] for row in batch],
                truncation=True,
                max_length=max_length,
                padding=True,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            logits = model(**encoded).logits.detach().cpu().numpy()
            probabilities = softmax(logits)
            pred_ids = np.argmax(probabilities, axis=1)
            confidences = np.max(probabilities, axis=1)
            for row, pred_id, confidence, scores in zip(batch, pred_ids, confidences, probabilities):
                label = labels[int(pred_id)]
                score_map = {labels[index]: float(score) for index, score in enumerate(scores)}
                results.append(
                    {
                        "id": row["id"],
                        "language": row.get("language"),
                        "source_style": row.get("source_style"),
                        "text": row["text"],
                        "true_label": row["label"],
                        "predicted_label": label,
                        "confidence": float(confidence),
                        "recommended_future_route": future_route(label, float(confidence), threshold),
                        "scores": score_map,
                    }
                )
    return results


def write_confusion_matrix(path: Path, matrix: np.ndarray, labels: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["true_label\\predicted_label", *labels])
        for label, row in zip(labels, matrix):
            writer.writerow([label, *[int(value) for value in row]])


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def compact_example(row: dict[str, object]) -> dict[str, object]:
    text = str(row["text"])
    return {
        "id": row["id"],
        "true_label": row["true_label"],
        "predicted_label": row["predicted_label"],
        "confidence": row["confidence"],
        "recommended_future_route": row["recommended_future_route"],
        "text": text[:320],
    }


def write_markdown_summary(path: Path, metrics: dict[str, object], report_text: str, false_negatives: list[dict[str, object]]) -> None:
    lines = [
        "# Risk Classifier Evaluation Summary",
        "",
        "This is a standalone synthetic-data evaluation artifact. It is not clinical validation and is not wired into Lumen.",
        "",
        "## Safety Metrics",
        "",
        f"- Total test rows: {metrics['total_rows']}",
        f"- Confidence threshold: {metrics['confidence_threshold']}",
        f"- Risk-class exact recall: {metrics['risk_class_exact_recall']:.4f}",
        f"- Risk route recall: {metrics['risk_route_recall']:.4f}",
        f"- Route-level false negatives: {metrics['route_false_negative_count']}",
        f"- Accuracy: {metrics['accuracy']:.4f}",
        f"- Macro F1: {metrics['macro_f1']:.4f}",
        "",
        "## Classification Report",
        "",
        "```text",
        report_text.strip(),
        "```",
        "",
        "## Route-Level False Negatives",
        "",
    ]
    if false_negatives:
        for row in false_negatives[:10]:
            lines.append(
                f"- {row['id']}: true={row['true_label']}, predicted={row['predicted_label']}, confidence={row['confidence']:.4f}"
            )
    else:
        lines.append("- None found at the configured threshold.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the standalone multilingual risk classifier.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--max-length", type=int, default=160)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--confidence-threshold", type=float, default=0.70)
    parser.add_argument("--examples-per-section", type=int, default=12)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    labels = load_labels(args.model_dir)
    label2id = {label: index for index, label in enumerate(labels)}
    rows = read_jsonl(args.data_dir / "test.jsonl")
    results = predict_rows(
        rows=rows,
        model_dir=args.model_dir,
        labels=labels,
        max_length=args.max_length,
        batch_size=args.batch_size,
        threshold=args.confidence_threshold,
    )

    true_ids = [label2id[str(row["true_label"])] for row in results]
    pred_ids = [label2id[str(row["predicted_label"])] for row in results]
    precision, recall, f1, _ = precision_recall_fscore_support(true_ids, pred_ids, average="macro", zero_division=0)
    risk_rows = [row for row in results if row["true_label"] != "none"]
    exact_risk_hits = [row for row in risk_rows if row["true_label"] == row["predicted_label"]]
    route_risk_hits = [row for row in risk_rows if row["recommended_future_route"] == "clinician_review"]
    false_negatives = [row for row in risk_rows if row["recommended_future_route"] == "continue"]
    label_false_negatives = [row for row in risk_rows if row["predicted_label"] == "none"]

    report_dict = classification_report(true_ids, pred_ids, labels=list(range(len(labels))), target_names=labels, output_dict=True, zero_division=0)
    report_text = classification_report(true_ids, pred_ids, labels=list(range(len(labels))), target_names=labels, zero_division=0)
    matrix = confusion_matrix(true_ids, pred_ids, labels=list(range(len(labels))))

    metrics = {
        "total_rows": len(results),
        "confidence_threshold": args.confidence_threshold,
        "accuracy": float(accuracy_score(true_ids, pred_ids)),
        "macro_precision": float(precision),
        "macro_recall": float(recall),
        "macro_f1": float(f1),
        "risk_class_exact_recall": float(len(exact_risk_hits) / len(risk_rows)) if risk_rows else 0.0,
        "risk_route_recall": float(len(route_risk_hits) / len(risk_rows)) if risk_rows else 0.0,
        "route_false_negative_count": len(false_negatives),
        "label_false_negative_count": len(label_false_negatives),
        "risk_support": len(risk_rows),
        "labels": labels,
    }

    (args.output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.output_dir / "classification_report.json").write_text(
        json.dumps(report_dict, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "classification_report.txt").write_text(report_text, encoding="utf-8")
    write_confusion_matrix(args.output_dir / "confusion_matrix.csv", matrix, labels)
    write_jsonl(args.output_dir / "false_negatives.jsonl", [compact_example(row) for row in false_negatives])

    correct = [compact_example(row) for row in results if row["true_label"] == row["predicted_label"]]
    incorrect = [compact_example(row) for row in results if row["true_label"] != row["predicted_label"]]
    examples = {
        "correct": correct[: args.examples_per_section],
        "incorrect": incorrect[: args.examples_per_section],
        "route_false_negatives": [compact_example(row) for row in false_negatives[: args.examples_per_section]],
    }
    (args.output_dir / "prediction_examples.json").write_text(
        json.dumps(examples, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_markdown_summary(args.output_dir / "evaluation_summary.md", metrics, report_text, false_negatives)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
