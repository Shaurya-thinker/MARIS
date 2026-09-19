"""Synthetic experiment runner for MARIS.

Coordinates end-to-end execution of a synthetic investigation:
1. Scenario generation (seeded, physically consistent, or parameter overrides)
2. Backward drift physics execution (pure run_backward_drift)
3. 10 physical features extracted per candidate vessel
4. ML inference using trained attribution model
5. Direct independent binary probabilities and scenario-normalized scores
6. Comparison against ground-truth for transparent validation
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from app.services.source_estimation import (
    generate_source_candidate_polygon,
    run_backward_drift,
)
from app.services.synthetic_experiment.feature_builder import (
    FEATURE_NAMES,
    extract_vessel_features,
)
from app.services.synthetic_experiment.model_registry import load_model
from app.services.synthetic_experiment.synthetic_generator import (
    SyntheticScenario,
    generate_synthetic_scenario,
)


def run_synthetic_experiment(
    scenario: SyntheticScenario | None = None,
    seed: int | None = None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    observation_time: datetime | None = None,
    wind_speed_ms: float | None = None,
    wind_direction_deg: float | None = None,
    current_speed_ms: float | None = None,
    current_direction_deg: float | None = None,
    candidate_count: int = 4,
    backtrack_hours: float = 6.0,
    step_hours: float = 0.5,
    spill_area_m2: float | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Execute complete synthetic experiment with physics drift and ML attribution."""
    run_id = f"syn_run_{uuid.uuid4().hex[:8]}"

    # Step 1: Generate or use provided scenario
    if scenario is None:
        scenario = generate_synthetic_scenario(
            seed=seed,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            observation_time=observation_time,
            wind_speed_ms=wind_speed_ms,
            wind_direction_deg=wind_direction_deg,
            current_speed_ms=current_speed_ms,
            current_direction_deg=current_direction_deg,
            candidate_count=candidate_count,
            backtrack_hours=backtrack_hours,
            step_hours=step_hours,
            spill_area_m2=spill_area_m2,
        )

    # Step 2: Backward drift integration using real physics
    backward_steps = run_backward_drift(
        origin_lon=scenario.origin_lon,
        origin_lat=scenario.origin_lat,
        observation_time=scenario.observation_time,
        wind_ds=scenario.wind_ds,
        curr_ds=scenario.curr_ds,
        lookback_hours=scenario.backtrack_hours,
        step_hours=scenario.step_hours,
        spill_area_m2=scenario.spill_area_m2,
    )

    final_step = backward_steps[-1]
    source_lon = final_step.lon
    source_lat = final_step.lat
    source_radius_m = final_step.uncertainty_radius_m

    source_zone_geojson = generate_source_candidate_polygon(
        center_lon=source_lon,
        center_lat=source_lat,
        radius_m=source_radius_m,
    )

    # Step 3: Feature extraction for all candidate vessels
    extracted_candidates = []
    feature_matrix_list = []

    for vessel in scenario.vessels:
        feat_dict = extract_vessel_features(
            vessel_data=vessel,
            source_lon=source_lon,
            source_lat=source_lat,
            source_radius_m=source_radius_m,
            observation_time=scenario.observation_time,
            backtrack_hours=scenario.backtrack_hours,
            backward_steps=backward_steps,
        )

        v_id = str(vessel.get("id") or vessel.get("mmsi") or vessel.get("vessel_name"))
        is_gt = bool(vessel.get("is_ground_truth", False))

        feature_vector = [feat_dict[fname] for fname in FEATURE_NAMES]
        feature_matrix_list.append(feature_vector)

        extracted_candidates.append({
            "vessel_id": v_id,
            "vessel_name": vessel.get("vessel_name", "UNKNOWN"),
            "mmsi": vessel.get("mmsi"),
            "vessel_type": vessel.get("vessel_type", "Vessel"),
            "is_ground_truth": is_gt,
            "features": feat_dict,
            "positions": vessel.get("positions", []),
        })

    # Step 4: ML Attribution Inference
    trained_model = load_model(model_id)
    features_np = np.array(feature_matrix_list, dtype=np.float32)

    # Independent binary probabilities P(responsible | features)
    independent_probs = trained_model.predict_candidate_probabilities(features_np)

    # Step 5: Compute scenario-normalized attribution scores
    # S_i = p_i / sum(p) (explicitly labeled as scenario-normalized attribution score)
    prob_sum = float(np.sum(independent_probs))
    if prob_sum > 1e-6:
        normalized_scores = independent_probs / prob_sum
    else:
        normalized_scores = np.full_like(independent_probs, 1.0 / len(independent_probs))

    # Package candidates with independent prob and scenario normalized score
    scored_candidates = []
    for idx, cand in enumerate(extracted_candidates):
        p_val = float(round(independent_probs[idx], 4))
        norm_score = float(round(normalized_scores[idx], 4))

        cand_result = dict(cand)
        cand_result["model_probability"] = p_val
        cand_result["scenario_normalized_attribution_score"] = norm_score
        cand_result["has_meaningful_support"] = bool(p_val >= 0.25)
        scored_candidates.append(cand_result)

    # Rank by model probability descending
    scored_candidates.sort(key=lambda c: c["model_probability"], reverse=True)
    for rank_idx, c in enumerate(scored_candidates, start=1):
        c["rank"] = rank_idx

    top_candidate = scored_candidates[0] if scored_candidates else None
    gt_match = (
        top_candidate is not None
        and top_candidate["vessel_id"] == scenario.ground_truth_vessel_id
    )

    return {
        "run_id": run_id,
        "is_synthetic": True,
        "scenario_id": scenario.scenario_id,
        "seed": scenario.seed,
        "observation_time": scenario.observation_time.isoformat(),
        "backtrack_hours": scenario.backtrack_hours,
        "step_hours": scenario.step_hours,
        "origin_lon": round(scenario.origin_lon, 5),
        "origin_lat": round(scenario.origin_lat, 5),
        "spill_area_m2": round(scenario.spill_area_m2, 1),
        "wind_speed_ms": round(scenario.wind_speed_ms, 2),
        "wind_direction_deg": round(scenario.wind_direction_deg, 1),
        "current_speed_ms": round(scenario.current_speed_ms, 3),
        "current_direction_deg": round(scenario.current_direction_deg, 1),
        "u10": round(scenario.u10, 3),
        "v10": round(scenario.v10, 3),
        "uo": round(scenario.uo, 3),
        "vo": round(scenario.vo, 3),
        "model_id": trained_model.model_id,
        "model_type": trained_model.model_type,
        "source_lon": round(source_lon, 5),
        "source_lat": round(source_lat, 5),
        "source_radius_m": round(source_radius_m, 1),
        "source_zone_geojson": source_zone_geojson,
        "backward_steps": [
            {
                "step": i,
                "lon": round(s.lon, 5),
                "lat": round(s.lat, 5),
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "uncertainty_radius_m": round(s.uncertainty_radius_m, 1),
            }
            for i, s in enumerate(backward_steps)
        ],
        "vessels": scored_candidates,
        "ground_truth_vessel_id": scenario.ground_truth_vessel_id,
        "top_candidate_id": top_candidate["vessel_id"] if top_candidate else None,
        "attribution_match": gt_match,
        "model_coefficients": trained_model.feature_coefficients,
        "model_test_metrics": trained_model.test_metrics.to_dict(),
    }
