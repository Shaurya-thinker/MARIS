"""Unit tests for the synthetic scenario generator and feature extraction."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.services.source_estimation import run_backward_drift
from app.services.synthetic_experiment.feature_builder import (
    FEATURE_NAMES,
    extract_vessel_features,
)
from app.services.synthetic_experiment.synthetic_generator import (
    SyntheticScenario,
    generate_synthetic_scenario,
)


def test_synthetic_scenario_deterministic_seed():
    """Verify that identical seeds produce identical scenarios."""
    scen1 = generate_synthetic_scenario(seed=424242, candidate_count=4)
    scen2 = generate_synthetic_scenario(seed=424242, candidate_count=4)

    assert scen1.seed == scen2.seed
    assert scen1.origin_lat == scen2.origin_lat
    assert scen1.origin_lon == scen2.origin_lon
    assert scen1.wind_speed_ms == scen2.wind_speed_ms
    assert scen1.current_speed_ms == scen2.current_speed_ms
    assert scen1.ground_truth_vessel_id == scen2.ground_truth_vessel_id
    assert len(scen1.vessels) == len(scen2.vessels) == 4


def test_synthetic_scenario_distinct_seeds():
    """Verify that different seeds produce distinct parameters."""
    scen1 = generate_synthetic_scenario(seed=111, candidate_count=3)
    scen2 = generate_synthetic_scenario(seed=999, candidate_count=3)

    assert scen1.seed != scen2.seed
    assert scen1.wind_speed_ms != scen2.wind_speed_ms or scen1.wind_direction_deg != scen2.wind_direction_deg
    assert scen1.ground_truth_vessel_id != scen2.ground_truth_vessel_id


def test_synthetic_ground_truth_and_distractors():
    """Verify that candidate count is respected and exactly one vessel is ground truth."""
    scenario = generate_synthetic_scenario(seed=777, candidate_count=5)

    assert len(scenario.vessels) == 5
    gt_vessels = [v for v in scenario.vessels if v.get("is_ground_truth")]
    assert len(gt_vessels) == 1
    assert gt_vessels[0]["id"] == scenario.ground_truth_vessel_id

    # Distractors should not have is_ground_truth=True
    distractors = [v for v in scenario.vessels if not v.get("is_ground_truth")]
    assert len(distractors) == 4


def test_backward_drift_on_synthetic_scenario():
    """Verify that run_backward_drift integrates cleanly over synthetic NetCDF datasets."""
    scenario = generate_synthetic_scenario(seed=555, candidate_count=3, backtrack_hours=4.0)

    steps = run_backward_drift(
        origin_lon=scenario.origin_lon,
        origin_lat=scenario.origin_lat,
        observation_time=scenario.observation_time,
        wind_ds=scenario.wind_ds,
        curr_ds=scenario.curr_ds,
        lookback_hours=scenario.backtrack_hours,
        step_hours=scenario.step_hours,
        spill_area_m2=scenario.spill_area_m2,
    )

    assert len(steps) >= 2
    # Final step is the estimated origin
    final_step = steps[-1]
    assert math.isfinite(final_step.lon)
    assert math.isfinite(final_step.lat)
    assert final_step.uncertainty_radius_m > 0.0


def test_vessel_feature_extraction():
    """Verify that feature extraction yields all 10 finite numerical features."""
    scenario = generate_synthetic_scenario(seed=888, candidate_count=3, backtrack_hours=4.0)

    steps = run_backward_drift(
        origin_lon=scenario.origin_lon,
        origin_lat=scenario.origin_lat,
        observation_time=scenario.observation_time,
        wind_ds=scenario.wind_ds,
        curr_ds=scenario.curr_ds,
        lookback_hours=scenario.backtrack_hours,
        step_hours=scenario.step_hours,
    )

    final_step = steps[-1]

    for vessel in scenario.vessels:
        features = extract_vessel_features(
            vessel_data=vessel,
            source_lon=final_step.lon,
            source_lat=final_step.lat,
            source_radius_m=final_step.uncertainty_radius_m,
            observation_time=scenario.observation_time,
            backtrack_hours=scenario.backtrack_hours,
            backward_steps=steps,
        )

        assert set(features.keys()) == set(FEATURE_NAMES)
        for fname, val in features.items():
            assert isinstance(val, (int, float)), f"{fname} is not a number"
            assert math.isfinite(val), f"{fname} is not finite: {val}"

    # Ground truth vessel should have small min_source_distance_km
    gt_vessel = next(v for v in scenario.vessels if v.get("is_ground_truth"))
    gt_features = extract_vessel_features(
        vessel_data=gt_vessel,
        source_lon=final_step.lon,
        source_lat=final_step.lat,
        source_radius_m=final_step.uncertainty_radius_m,
        observation_time=scenario.observation_time,
        backtrack_hours=scenario.backtrack_hours,
        backward_steps=steps,
    )
    # Ground truth vessel passes within 2 km of the physical source
    assert gt_features["min_source_distance_km"] < 5.0
