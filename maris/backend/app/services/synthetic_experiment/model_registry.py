"""Model registry and artifact manager for MARIS attribution ML models.

Stores versioned models, pipeline binaries, and performance manifests under:
    <MARIS_DATA_DIR>/models/
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

from app.core.config import settings
from app.services.synthetic_experiment.attribution_model import (
    EvaluationMetrics,
    TrainedAttributionModel,
    train_attribution_model,
)
from app.services.synthetic_experiment.training_dataset import build_synthetic_dataset

_DEFAULT_MANIFEST_NAME = "registry_manifest.json"
_CACHED_ACTIVE_MODEL: TrainedAttributionModel | None = None


def get_model_directory() -> Path:
    """Return and ensure existence of the model storage directory."""
    model_dir = settings.data_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir


def _get_manifest_path() -> Path:
    return get_model_directory() / _DEFAULT_MANIFEST_NAME


def load_registry_manifest() -> list[dict[str, Any]]:
    """Load the JSON registry manifest containing all registered models."""
    path = _get_manifest_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_registry_manifest(manifest: list[dict[str, Any]]) -> None:
    """Persist the JSON registry manifest."""
    path = _get_manifest_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)


def save_model(
    model: TrainedAttributionModel,
    is_default: bool = True,
) -> Path:
    """Save model pipeline and update registry manifest."""
    global _CACHED_ACTIVE_MODEL
    model_dir = get_model_directory()
    artifact_filename = f"{model.model_id}.joblib"
    artifact_path = model_dir / artifact_filename

    # Serialize trained model container
    save_payload = {
        "model_id": model.model_id,
        "model_type": model.model_type,
        "pipeline": model.pipeline,
        "feature_names": model.feature_names,
        "feature_coefficients": model.feature_coefficients,
        "train_metrics": model.train_metrics.to_dict() if model.train_metrics else None,
        "val_metrics": model.val_metrics.to_dict(),
        "test_metrics": model.test_metrics.to_dict(),
        "trained_at": model.trained_at.isoformat(),
        "training_sample_count": model.training_sample_count,
        "training_scenario_count": model.training_scenario_count,
        "training_seed": model.training_seed,
    }
    joblib.dump(save_payload, str(artifact_path))

    # Update manifest
    manifest = load_registry_manifest()
    if is_default:
        for entry in manifest:
            entry["is_default"] = False

    entry = {
        "model_id": model.model_id,
        "model_type": model.model_type,
        "artifact_file": artifact_filename,
        "feature_coefficients": model.feature_coefficients,
        "train_metrics": model.train_metrics.to_dict() if model.train_metrics else None,
        "val_metrics": model.val_metrics.to_dict(),
        "test_metrics": model.test_metrics.to_dict(),
        "trained_at": model.trained_at.isoformat(),
        "training_sample_count": model.training_sample_count,
        "training_scenario_count": model.training_scenario_count,
        "training_seed": model.training_seed,
        "is_default": is_default,
    }
    manifest = [m for m in manifest if m["model_id"] != model.model_id]
    manifest.append(entry)
    _save_registry_manifest(manifest)

    if is_default:
        _CACHED_ACTIVE_MODEL = model

    return artifact_path


def load_model(model_id: str | None = None) -> TrainedAttributionModel:
    """Load a model by ID, or load the default/latest registered model.

    If no model is currently registered, automatically trains and registers
    an initial baseline model so MARIS is immediately functional.
    """
    global _CACHED_ACTIVE_MODEL

    model_dir = get_model_directory()
    manifest = load_registry_manifest()

    target_entry: dict[str, Any] | None = None

    if model_id:
        target_entry = next((m for m in manifest if m["model_id"] == model_id), None)
        if not target_entry:
            raise FileNotFoundError(f"Model ID '{model_id}' not found in registry manifest.")
    elif manifest:
        # Default or most recent
        target_entry = next((m for m in manifest if m.get("is_default")), manifest[-1])

    if target_entry:
        if _CACHED_ACTIVE_MODEL and _CACHED_ACTIVE_MODEL.model_id == target_entry["model_id"]:
            return _CACHED_ACTIVE_MODEL

        artifact_path = model_dir / target_entry["artifact_file"]
        if artifact_path.exists():
            data = joblib.load(str(artifact_path))
            train_m = data.get("train_metrics")
            val_m = data["val_metrics"]
            test_m = data["test_metrics"]
            pipeline = data["pipeline"]
            try:
                clf = (
                    pipeline.named_steps.get("classifier")
                    if hasattr(pipeline, "named_steps")
                    else (pipeline.steps[-1][1] if hasattr(pipeline, "steps") else None)
                )
                if clf is not None and not hasattr(clf, "multi_class"):
                    setattr(clf, "multi_class", "auto")
            except Exception:
                pass
            model = TrainedAttributionModel(
                model_id=data["model_id"],
                model_type=data["model_type"],
                pipeline=pipeline,
                feature_names=data["feature_names"],
                feature_coefficients=data["feature_coefficients"],
                train_metrics=EvaluationMetrics(**train_m) if train_m else None,
                val_metrics=EvaluationMetrics(**val_m),
                test_metrics=EvaluationMetrics(**test_m),
                trained_at=datetime.fromisoformat(data["trained_at"]),
                training_sample_count=data["training_sample_count"],
                training_scenario_count=data["training_scenario_count"],
                training_seed=data.get("training_seed"),
            )
            if target_entry.get("is_default", False):
                _CACHED_ACTIVE_MODEL = model
            return model

    # If no valid model exists, train a quick initial baseline model
    dataset = build_synthetic_dataset(num_scenarios=25, base_seed=42)
    model = train_attribution_model(dataset)
    save_model(model, is_default=True)
    return model


def list_models() -> list[dict[str, Any]]:
    """List all registered models with their metadata and performance metrics."""
    manifest = load_registry_manifest()
    if not manifest:
        # Ensure at least the baseline model is registered
        load_model()
        manifest = load_registry_manifest()
    return manifest


def get_active_model_summary() -> dict[str, Any]:
    """Get metadata and metrics for the currently active default model."""
    model = load_model()
    return {
        "model_id": model.model_id,
        "model_type": model.model_type,
        "trained_at": model.trained_at.isoformat(),
        "feature_coefficients": model.feature_coefficients,
        "val_metrics": model.val_metrics.to_dict(),
        "test_metrics": model.test_metrics.to_dict(),
        "training_sample_count": model.training_sample_count,
        "training_scenario_count": model.training_scenario_count,
    }
