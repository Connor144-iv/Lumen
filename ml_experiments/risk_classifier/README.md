# Standalone Multilingual Risk Classifier

This directory contains an isolated synthetic-data experiment for a future Lumen risk classifier. It does not change the running app, replace the keyword classifier, or alter workflow routing.

The classifier is trained on synthetic English and Portuguese referral-style text with these labels:

- `none`
- `self_harm_or_suicidality`
- `acute_crisis`
- `safeguarding_or_abuse`
- `unclear`

## Setup

Use a separate environment so the Lumen app runtime stays unchanged.

```powershell
python -m venv ml_experiments\risk_classifier\.venv
.\ml_experiments\risk_classifier\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r ml_experiments\risk_classifier\requirements.txt
```

## Reproduce The Artifact

```powershell
python ml_experiments\risk_classifier\generate_synthetic_dataset.py
python ml_experiments\risk_classifier\train.py
python ml_experiments\risk_classifier\evaluate.py
python ml_experiments\risk_classifier\predict.py --text "I do not feel safe and might hurt myself"
```

Generated data is written to `data/`. The trained model and tokenizer are written to `models/distilbert-risk-classifier/`. Evaluation artifacts are written to `outputs/`.

The default base model is `distilbert/distilbert-base-multilingual-cased`. Override it with `--base-model` if needed.

## Outputs

Dataset generation writes:

- `data/synthetic_referrals.csv`
- `data/synthetic_referrals.jsonl`
- `data/train.jsonl`
- `data/test.jsonl`
- `data/dataset_summary.json`

Training writes:

- `models/distilbert-risk-classifier/`
- `models/distilbert-risk-classifier/label_mapping.json`
- `outputs/training_summary.json`

Evaluation writes:

- `outputs/metrics.json`
- `outputs/classification_report.json`
- `outputs/classification_report.txt`
- `outputs/confusion_matrix.csv`
- `outputs/false_negatives.jsonl`
- `outputs/prediction_examples.json`
- `outputs/evaluation_summary.md`

## Safety Bias

The standalone route rule mirrors the intended future safety posture:

- non-`none` prediction -> `clinician_review`
- `unclear` -> `clinician_review`
- low confidence -> `clinician_review`
- model failure -> `clinician_review`
- only high-confidence `none` may return `continue`

The default confidence threshold is `0.70` and can be changed with `--confidence-threshold`.

## Future Lumen Integration Notes

Do not integrate this experiment into the app yet.

The current production path remains:

- Keyword classifier: `backend/lumen_agentic/agents.py`, `RiskClassifierClient`
- Graph call site: `backend/lumen_agentic/graph.py`, `risk_review_node`
- Routing gate: `backend/lumen_agentic/graph.py`, `risk_router`
- Current schema: `backend/lumen_agentic/schemas.py`, `RiskReview`

A future adapter should match the current interface:

```python
class FutureFineTunedRiskClassifierClient:
    def classify(self, text: str) -> RiskReview:
        ...
```

Recommended mapping without changing `RiskReview`:

| Model label | Future `RiskReview.risk_category` | `required_handoff` |
| --- | --- | --- |
| `none` with confidence >= threshold | `none` | `continue` |
| `none` with confidence < threshold | `unknown` | `clinician_review` |
| `self_harm_or_suicidality` | `unknown` | `clinician_review` |
| `acute_crisis` | `acute_crisis` | `clinician_review` |
| `safeguarding_or_abuse` | `safeguarding` | `clinician_review` |
| `unclear` | `unknown` | `clinician_review` |
| model failure | `unknown` | `clinician_review` |

Routing rules to preserve:

- elevated risk -> clinician review
- unclear or unknown -> clinician review
- low confidence -> clinician review
- model failure -> clinician review
- only clear low-risk and high-confidence results continue normally

When integration happens later, update tests for adapter mapping, low-confidence fail-closed behavior, classifier exceptions, graph routing to clinical review, and referral status persistence. Keep the existing graph review gate tests passing.

## Verification

```powershell
python -m py_compile ml_experiments\risk_classifier\*.py
pytest tests\test_graph_review_gates.py
```

This experiment uses synthetic data only. It is demo-ready evidence for an NLP upgrade path, not a clinically validated model.

