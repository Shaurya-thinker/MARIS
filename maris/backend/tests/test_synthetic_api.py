"""API tests for synthetic experiment endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_api_synthetic_generate():
    """Verify POST /api/experiment/synthetic/generate endpoint."""
    response = client.post(
        "/api/experiment/synthetic/generate",
        json={"seed": 12345, "candidate_count": 4, "backtrack_hours": 4.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"].startswith("syn_")
    assert data["seed"] == 12345
    assert len(data["vessels"]) == 4
    assert data["is_synthetic"] is True


def test_api_synthetic_run():
    """Verify POST /api/experiment/synthetic/run endpoint."""
    response = client.post(
        "/api/experiment/synthetic/run",
        json={"seed": 54321, "candidate_count": 3, "backtrack_hours": 3.0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_synthetic"] is True
    assert len(data["vessels"]) == 3
    assert len(data["backward_steps"]) >= 2
    assert "source_zone_geojson" in data
    assert "top_candidate_id" in data
    assert "model_id" in data

    # Verify candidates have both independent prob and scenario normalized score
    for v in data["vessels"]:
        assert "model_probability" in v
        assert "scenario_normalized_attribution_score" in v
        assert 0.0 <= v["model_probability"] <= 1.0
        assert 0.0 <= v["scenario_normalized_attribution_score"] <= 1.0


def test_api_synthetic_model_and_train():
    """Verify GET /api/experiment/synthetic/model and POST /api/experiment/synthetic/train."""
    get_res = client.get("/api/experiment/synthetic/model")
    assert get_res.status_code == 200
    model_data = get_res.json()
    assert "model_id" in model_data
    assert "feature_coefficients" in model_data
    assert "val_metrics" in model_data
    assert "test_metrics" in model_data

    # Train a quick model
    train_res = client.post(
        "/api/experiment/synthetic/train",
        json={"num_scenarios": 10, "base_seed": 777},
    )
    assert train_res.status_code == 200
    train_data = train_res.json()
    assert train_data["model_id"].startswith("attr_lr_")
    assert train_data["training_scenario_count"] >= 5


def test_api_synthetic_runs_list():
    """Verify GET /api/experiment/synthetic/runs."""
    res = client.get("/api/experiment/synthetic/runs")
    assert res.status_code == 200
    runs = res.json()
    assert isinstance(runs, list)
    assert len(runs) >= 1
    assert runs[0]["scenario_id"].startswith("syn_")
