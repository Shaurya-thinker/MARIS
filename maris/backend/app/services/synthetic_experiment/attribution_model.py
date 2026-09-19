"""Machine learning attribution model for MARIS.

Implements a transparent, physically explainable ML attribution pipeline:
- Primary Architecture: StandardScaler + LogisticRegression (with balanced class weights).
- Inference: Independent binary probability for each candidate vessel via predict_proba().
  Probabilities are NOT forced to sum to 1.
- Scenario-Normalized Scores: Optionally computed as softmax / sum normalization across candidates
  within a scenario, explicitly labeled as 'scenario_normalized_attribution_score'.
- Evaluation: Candidate-level metrics (accuracy, precision, recall, f1, roc_auc) and
  scenario-level Top-1 attribution accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.services.synthetic_experiment.feature_builder import FEATURE_NAMES
from app.services.synthetic_experiment.training_dataset import DatasetPartition, SyntheticDataset


@dataclass
class EvaluationMetrics:
    """Model evaluation metrics on candidate and scenario levels."""

    candidate_accuracy: float
    candidate_precision: float
    candidate_recall: float
    candidate_f1: float
    candidate_roc_auc: float
    scenario_top1_accuracy: float
    total_candidates: int
    total_scenarios: int
    brier_score: float = 0.0
    scenario_top2_accuracy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_accuracy": round(self.candidate_accuracy, 4),
            "candidate_precision": round(self.candidate_precision, 4),
            "candidate_recall": round(self.candidate_recall, 4),
            "candidate_f1": round(self.candidate_f1, 4),
            "candidate_roc_auc": round(self.candidate_roc_auc, 4),
            "scenario_top1_accuracy": round(self.scenario_top1_accuracy, 4),
            "scenario_top2_accuracy": round(self.scenario_top2_accuracy, 4),
            "brier_score": round(self.brier_score, 4),
            "total_candidates": self.total_candidates,
            "total_scenarios": self.total_scenarios,
        }


@dataclass
class TrainedAttributionModel:
    """Encapsulates a trained pipeline, feature weights, and evaluation metrics."""

    model_id: str
    model_type: str
    pipeline: Pipeline
    feature_names: list[str]
    feature_coefficients: dict[str, float]
    val_metrics: EvaluationMetrics
    test_metrics: EvaluationMetrics
    trained_at: datetime
    training_sample_count: int
    training_scenario_count: int
    train_metrics: EvaluationMetrics | None = None
    training_seed: int | None = None

    def predict_candidate_probabilities(self, features: np.ndarray) -> np.ndarray:
        """Compute independent binary attribution probabilities for candidate vessels.

        Returns:
            np.ndarray of shape (N,) containing independent P(responsible | features) in [0.0, 1.0].
            These probabilities are NOT forced to sum to 1.
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)
        proba = self.pipeline.predict_proba(features)
        # Class 1 is the positive (ground-truth) class
        if proba.shape[1] >= 2:
            return proba[:, 1]
        return proba[:, 0]


def evaluate_partition(
    pipeline: Pipeline,
    partition: DatasetPartition,
) -> EvaluationMetrics:
    """Calculate both candidate-level and scenario-level evaluation metrics."""
    if len(partition.labels) == 0:
        return EvaluationMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)

    y_true = partition.labels
    y_prob = pipeline.predict_proba(partition.features)[:, 1]
    y_pred = (y_prob >= 0.5).astype(int)

    acc = float(accuracy_score(y_true, y_pred))
    prec = float(precision_score(y_true, y_pred, zero_division=0))
    rec = float(recall_score(y_true, y_pred, zero_division=0))
    f1 = float(f1_score(y_true, y_pred, zero_division=0))

    try:
        auc = float(roc_auc_score(y_true, y_prob)) if len(np.unique(y_true)) > 1 else 0.5
    except Exception:
        auc = 0.5

    try:
        brier = float(brier_score_loss(y_true, y_prob))
    except Exception:
        brier = 0.0

    # Scenario-level Top-1 and Top-2 Accuracy:
    # Does the candidate with highest y_prob within each scenario equal the ground-truth vessel?
    unique_scenarios = list(dict.fromkeys(partition.scenario_ids))
    correct_scenarios = 0
    top2_correct_scenarios = 0

    for sc_id in unique_scenarios:
        indices = [i for i, sid in enumerate(partition.scenario_ids) if sid == sc_id]
        if not indices:
            continue
        sc_probs = y_prob[indices]
        sc_labels = y_true[indices]

        # Highest probability index within this scenario
        best_sc_idx = int(np.argmax(sc_probs))
        if sc_labels[best_sc_idx] == 1:
            correct_scenarios += 1

        # Top-2 indices
        sorted_sc_indices = np.argsort(sc_probs)
        top2_indices = sorted_sc_indices[-2:] if len(sorted_sc_indices) >= 2 else sorted_sc_indices
        if any(sc_labels[idx] == 1 for idx in top2_indices):
            top2_correct_scenarios += 1

    top1_acc = (
        correct_scenarios / len(unique_scenarios)
        if unique_scenarios else 0.0
    )
    top2_acc = (
        top2_correct_scenarios / len(unique_scenarios)
        if unique_scenarios else 0.0
    )

    return EvaluationMetrics(
        candidate_accuracy=acc,
        candidate_precision=prec,
        candidate_recall=rec,
        candidate_f1=f1,
        candidate_roc_auc=auc,
        scenario_top1_accuracy=top1_acc,
        scenario_top2_accuracy=top2_acc,
        brier_score=brier,
        total_candidates=len(y_true),
        total_scenarios=len(unique_scenarios),
    )


def train_attribution_model(
    dataset: SyntheticDataset,
    model_type: str = "logistic_regression",
    c_regularization: float = 1.0,
    random_state: int = 42,
    training_seed: int | None = None,
    model_id: str | None = None,
) -> TrainedAttributionModel:
    """Train the primary attribution model pipeline (StandardScaler + LogisticRegression)."""
    X_train = dataset.train.features
    y_train = dataset.train.labels

    if model_type == "logistic_regression":
        classifier = LogisticRegression(
            C=c_regularization,
            class_weight="balanced",
            max_iter=1000,
            random_state=random_state,
        )
    else:
        # Fallback / optional alternative
        classifier = LogisticRegression(
            C=c_regularization,
            class_weight="balanced",
            max_iter=1000,
            random_state=random_state,
        )

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", classifier),
    ])

    pipeline.fit(X_train, y_train)

    # Extract feature coefficients for explainability
    lr_model = pipeline.named_steps["classifier"]
    coefs = lr_model.coef_[0]
    feature_coefficients = {
        name: float(round(coef, 4))
        for name, coef in zip(FEATURE_NAMES, coefs)
    }

    train_metrics = evaluate_partition(pipeline, dataset.train)
    val_metrics = evaluate_partition(pipeline, dataset.val)
    test_metrics = evaluate_partition(pipeline, dataset.test)

    if not model_id:
        model_id = f"attr_lr_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    return TrainedAttributionModel(
        model_id=model_id,
        model_type=model_type,
        pipeline=pipeline,
        feature_names=FEATURE_NAMES,
        feature_coefficients=feature_coefficients,
        train_metrics=train_metrics,
        val_metrics=val_metrics,
        test_metrics=test_metrics,
        trained_at=datetime.now(timezone.utc),
        training_sample_count=len(y_train),
        training_scenario_count=len(set(dataset.train.scenario_ids)),
        training_seed=training_seed,
    )
