# Risk Classifier Evaluation Summary

This is a standalone synthetic-data evaluation artifact. It is not clinical validation and is not wired into Lumen.

## Safety Metrics

- Total test rows: 60
- Confidence threshold: 0.7
- Risk-class exact recall: 0.7500
- Risk route recall: 1.0000
- Route-level false negatives: 0
- Accuracy: 0.7500
- Macro F1: 0.7461

## Classification Report

```text
precision    recall  f1-score   support

                    none       0.82      0.75      0.78        12
self_harm_or_suicidality       0.69      0.92      0.79        12
            acute_crisis       0.78      0.58      0.67        12
   safeguarding_or_abuse       1.00      0.58      0.74        12
                 unclear       0.65      0.92      0.76        12

                accuracy                           0.75        60
               macro avg       0.79      0.75      0.75        60
            weighted avg       0.79      0.75      0.75        60
```

## Route-Level False Negatives

- None found at the configured threshold.
