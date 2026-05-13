"""Standalone prediction wrapper for the fine-tuned risk classifier.

This script is for local testing only. It is not imported by the Lumen backend.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import torch
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
DEFAULT_MODEL_DIR = ROOT / "models" / "distilbert-risk-classifier"

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
transformers_logging.set_verbosity_error()
transformers_logging.disable_progress_bar()


def load_labels(model_dir: Path) -> list[str]:
    mapping_path = model_dir / "label_mapping.json"
    if mapping_path.exists():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        return list(mapping["labels"])
    return LABELS


def future_route(label: str, confidence: float, threshold: float) -> str:
    if label == "none" and confidence >= threshold:
        return "continue"
    return "clinician_review"


def classify_text(text: str, model_dir: Path, threshold: float, max_length: int, include_scores: bool) -> dict[str, object]:
    labels = load_labels(model_dir)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    model.eval()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    encoded = tokenizer(text, truncation=True, max_length=max_length, padding=True, return_tensors="pt")
    encoded = {key: value.to(device) for key, value in encoded.items()}
    with torch.no_grad():
        logits = model(**encoded).logits.detach().cpu().numpy()[0]
    shifted = logits - np.max(logits)
    probabilities = np.exp(shifted) / np.sum(np.exp(shifted))
    pred_id = int(np.argmax(probabilities))
    confidence = float(probabilities[pred_id])
    label = labels[pred_id]

    result: dict[str, object] = {
        "label": label,
        "confidence": confidence,
        "recommended_future_route": future_route(label, confidence, threshold),
    }
    if include_scores:
        result["scores"] = {labels[index]: float(score) for index, score in enumerate(probabilities)}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone risk classifier prediction.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--confidence-threshold", type=float, default=0.70)
    parser.add_argument("--max-length", type=int, default=160)
    parser.add_argument("--include-scores", action="store_true")
    args = parser.parse_args()

    try:
        result = classify_text(
            text=args.text,
            model_dir=args.model_dir,
            threshold=args.confidence_threshold,
            max_length=args.max_length,
            include_scores=args.include_scores,
        )
    except Exception as exc:
        result = {
            "label": "unclear",
            "confidence": 0.0,
            "recommended_future_route": "clinician_review",
            "error": str(exc),
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
