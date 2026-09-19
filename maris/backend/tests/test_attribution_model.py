"""Unit tests for the ML attribution model, group splitting, and registry."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.synthetic_experiment.attribution_model import (
    train_attribution_model,
)
from app.services.synthetic_experiment.feature_builder import FEATURE_NAMES
from app.services.synthetic_experiment.model_registry import (
    get_active_model_summary,
    load_model,
    save_model,
)
from app.services.synthetic_experiment.synthetic_experiment_runner import (
    run_synthetic_experiment,
)
from app.services.synthetic_experiment.training_dataset import build_synthetic_dataset


def test_group_safe_scenario_splitting():
    """Verify that group-splitting prevents scenario leakage across train/val/test."""
    dataset = build_synthetic_dataset(num_scenarios=12, base_seed=101, candidates_per_scenario=3)

    train_scenarios = set(dataset.train.scenario_ids)
    val_scenarios = set(dataset.val.scenario_ids)
    test_scenarios = set(dataset.test.scenario_ids)

    # Absolutely zero intersection between splits
    assert train_scenarios.isdisjoint(val_scenarios)
    assert train_scenarios.isdisjoint(test_scenarios)
    assert val_scenarios.isdisjoint(test_scenarios)

    # Check non-empty
    assert len(train_scenarios) > 0
    assert len(val_scenarios) > 0
    assert len(test_scenarios) > 0


def test_model_training_and_independent_probabilities():
    """Verify model training, feature coefficients, and independent predict_proba behavior."""
    dataset = build_synthetic_dataset(num_scenarios=15, base_seed=202, candidates_per_scenario=4)
    model = train_attribution_model(dataset, model_type="logistic_regression")

    assert model.model_id.startswith("attr_lr_")
    assert len(model.feature_coefficients) == len(FEATURE_NAMES)

    # Test independent probabilities on test set
    test_probs = model.predict_candidate_probabilities(dataset.test.features)
    assert len(test_probs) == len(dataset.test.labels)

    # All probabilities in [0.0, 1.0]
    for p in test_probs:
        assert 0.0 <= p <= 1.0

    # Probabilities across a scenario are independent binary probabilities;
    # verify they do not arbitrarily sum to 1.0
    first_scen_id = dataset.test.scenario_ids[0]
    indices = [i for i, sid in enumerate(dataset.test.scenario_ids) if sid == first_scen_id]
    scen_probs = test_probs[indices]

    # With independent binary probabilities, the sum across candidates is almost never exactly 1.0
    assert not np.isclose(np.sum(scen_probs), 1.0, atol=1e-3) or len(scen_probs) == 1


def test_model_registry_save_and_load(tmp_path, monkeypatch):
    """Verify that saving and loading models preserves pipeline inference."""
    monkeypatch.setattr("app.services.synthetic_experiment.model_registry.get_model_directory", lambda: tmp_path)

    dataset = build_synthetic_dataset(num_scenarios=10, base_seed=303, candidates_per_scenario=3)
    original_model = train_attribution_model(dataset)
    save_model(original_model, is_default=True)

    loaded_model = load_model(original_model.model_id)
    assert loaded_model.model_id == original_model.model_id
    assert loaded_model.feature_coefficients == original_model.feature_coefficients

    # Predictions match identically
    p1 = original_model.predict_candidate_probabilities(dataset.test.features)
    p2 = loaded_model.predict_candidate_probabilities(dataset.test.features)
    np.testing.assert_allclose(p1, p2, rtol=1e-5)


def test_end_to_end_synthetic_experiment_runner():
    """Verify that run_synthetic_experiment runs full drift physics + ML inference."""
    result = run_synthetic_experiment(seed=999111, candidate_count=4, backtrack_hours=4.0)

    assert result["is_synthetic"] is True
    assert result["scenario_id"].startswith("syn_")
    assert len(result["vessels"]) == 4
    assert len(result["backward_steps"]) >= 2
    assert "source_zone_geojson" in result
    assert "top_candidate_id" in result
    assert isinstance(result["attribution_match"], bool)

    for v in result["vessels"]:
        assert "model_probability" in v
        assert "scenario_normalized_attribution_score" in v
        assert 0.0 <= v["model_probability"] <= 1.0
        assert 0.0 <= v["scenario_normalized_attribution_score"] <= 1.0
        assert "rank" in v
