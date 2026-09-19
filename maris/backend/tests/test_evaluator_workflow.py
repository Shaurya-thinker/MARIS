"""Tests for the Evaluator Investigation Workflow Service and APIs.

Verifies:
1. Reference observation truthfulness & historical demo semantics.
2. Physics-based backward drift calculation with environmental vector perturbation.
3. Strict spatial corridor AND temporal window AIS candidate filtering.
4. Clear separation of provider states (LIVE_AIS, NO_PROVIDER, NO_ELIGIBLE_VESSELS).
5. Active ML model inference and independent/normalized attribution scores.
6. SQLite persistence and instant replay without rerunning analysis.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.real_experiment.evaluator_workflow import (
    calculate_backward_drift_preview,
    determine_provider_status,
    filter_candidates_for_investigation,
    get_reference_observation,
    list_reference_observations,
    run_evaluator_investigation,
)
from app.services.real_experiment.experiment_store import ExperimentStore


# ---------------------------------------------------------------------------
# 1. Reference Observations Truthfulness & Semantics
# ---------------------------------------------------------------------------

def test_reference_observations_metadata_truthfulness():
    refs = list_reference_observations()
    assert len(refs) >= 6

    # Verify Corsica historical demo semantics
    corsica = get_reference_observation("ref_corsica_2018")
    assert corsica is not None
    assert corsica["is_historical_demo"] is True
    assert "Historical Corsica benchmark" in corsica["historical_context"]
    assert corsica["observation_lon"] == pytest.approx(9.47833, abs=1e-4)
    assert corsica["observation_lat"] == pytest.approx(43.24833, abs=1e-4)
    assert "2018-10-08" in corsica["observation_time"]
    assert len(corsica["benchmark_candidates"]) >= 2

    # Verify non-historical scenes
    arabian = get_reference_observation("ref_arabian_sea_alpha")
    assert arabian is not None
    assert arabian["is_historical_demo"] is False
    assert arabian["observation_lon"] == pytest.approx(66.198, abs=1e-3)
    assert arabian["observation_lat"] == pytest.approx(15.642, abs=1e-3)


# ---------------------------------------------------------------------------
# 2. Physics-Based Backward Drift Calculation
# ---------------------------------------------------------------------------

def test_backward_drift_environmental_variation():
    obs_time = datetime(2025, 3, 15, 6, 0, tzinfo=timezone.utc)
    lon, lat = 66.198, 15.642

    # Baseline run (5 m/s wind from 270 deg, 0.2 m/s current to 90 deg)
    res_base = calculate_backward_drift_preview(
        origin_lon=lon,
        origin_lat=lat,
        observation_time=obs_time,
        wind_speed_ms=5.0,
        wind_direction_deg=270.0,
        current_speed_ms=0.2,
        current_direction_deg=90.0,
        backtrack_hours=6.0,
    )

    # Perturbed run (stronger wind from 180 deg)
    res_perturbed = calculate_backward_drift_preview(
        origin_lon=lon,
        origin_lat=lat,
        observation_time=obs_time,
        wind_speed_ms=12.0,
        wind_direction_deg=180.0,
        current_speed_ms=0.2,
        current_direction_deg=90.0,
        backtrack_hours=6.0,
    )

    src_base = res_base["reconstructed_source"]
    src_pert = res_perturbed["reconstructed_source"]

    # Reconstructed source coordinates must physically change under different winds
    assert src_base["source_lon"] != src_pert["source_lon"] or src_base["source_lat"] != src_pert["source_lat"]
    assert len(res_base["backward_steps"]) > 0
    assert len(res_perturbed["backward_steps"]) > 0


# ---------------------------------------------------------------------------
# 3. Strict Candidate Filtering (Corridor AND Temporal)
# ---------------------------------------------------------------------------

def test_strict_candidate_filtering():
    obs_time = datetime(2025, 3, 15, 6, 0, tzinfo=timezone.utc)
    backtrack_hours = 6.0
    rel_time = obs_time - timedelta(hours=backtrack_hours)

    source_lon, source_lat = 66.0, 15.5
    backward_steps = [
        {"lon": 66.198, "lat": 15.642},
        {"lon": 66.10, "lat": 15.58},
        {"lon": 66.00, "lat": 15.50},
    ]

    # Candidate 1: Both temporal AND spatial match (within 5 km and at release time)
    vessel_eligible = {
        "id": "v_elig",
        "vessel_name": "CORRIDOR_INSIDER",
        "mmsi": "111222333",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 15.51, "lon": 66.01, "speed": 10.0, "heading": 45.0},
        ],
    }

    # Candidate 2: Temporal match, but SPATIALLY OUTSIDE CORRIDOR (60 km away)
    vessel_out_corridor = {
        "id": "v_far",
        "vessel_name": "OFFSHORE_DISTANT",
        "mmsi": "444555666",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 16.50, "lon": 66.00, "speed": 12.0, "heading": 90.0},
        ],
    }

    # Candidate 3: Spatially close, but TEMPORALLY OUTSIDE WINDOW (24 hours prior)
    vessel_wrong_time = {
        "id": "v_time_mismatch",
        "vessel_name": "YESTERDAY_TRANSIT",
        "mmsi": "777888999",
        "positions": [
            {"timestamp": (obs_time - timedelta(hours=24)).isoformat(), "lat": 15.50, "lon": 66.00, "speed": 11.0, "heading": 90.0},
        ],
    }

    # Candidate 4: Empty positions
    vessel_empty = {
        "id": "v_empty",
        "vessel_name": "NO_REPORTS",
        "positions": [],
    }

    result = filter_candidates_for_investigation(
        vessels=[vessel_eligible, vessel_out_corridor, vessel_wrong_time, vessel_empty],
        backward_steps=backward_steps,
        source_lon=source_lon,
        source_lat=source_lat,
        source_radius_m=5000.0,
        observation_time=obs_time,
        backtrack_hours=backtrack_hours,
        corridor_km=25.0,
    )

    assert result["total_evaluated"] == 4
    assert result["eligible_count"] == 1
    assert result["ineligible_count"] == 3

    assert result["eligible_candidates"][0]["vessel_id"] == "v_elig"
    assert result["eligible_candidates"][0]["has_temporal_overlap"] is True
    assert result["eligible_candidates"][0]["has_spatial_corridor_overlap"] is True

    inelig_reasons = {inv["vessel_id"]: inv["rejection_reason"] for inv in result["ineligible_candidates"]}
    assert "OUTSIDE_CORRIDOR_25.0KM" in inelig_reasons["v_far"]
    assert "TEMPORAL_WINDOW_MISMATCH" in inelig_reasons["v_time_mismatch"]
    assert inelig_reasons["v_empty"] == "NO_AIS_POSITIONS"


# ---------------------------------------------------------------------------
# 4. Provider Boundary Status States
# ---------------------------------------------------------------------------

def test_provider_status_discrimination():
    # 1. Unconfigured and zero found -> NO_PROVIDER
    p_stat, _ = determine_provider_status(has_live_provider=False, total_found=0, eligible_count=0)
    assert p_stat == "NO_PROVIDER"

    # 2. Vessels examined, but 0 passed filters -> NO_ELIGIBLE_VESSELS
    p_stat2, _ = determine_provider_status(has_live_provider=True, total_found=5, eligible_count=0)
    assert p_stat2 == "NO_ELIGIBLE_VESSELS"

    # 3. Eligible vessels found -> LIVE_AIS
    p_stat3, _ = determine_provider_status(has_live_provider=True, total_found=5, eligible_count=2)
    assert p_stat3 == "LIVE_AIS"


# ---------------------------------------------------------------------------
# 5. End-to-End Run and Persistence Round-Trip
# ---------------------------------------------------------------------------

def test_evaluator_investigation_end_to_end_and_replay(tmp_path):
    # Point test store to tmp database
    test_db = tmp_path / "test_eval_store.db"
    store = ExperimentStore(db_path=test_db)

    obs_time = datetime(2018, 10, 8, 5, 28, tzinfo=timezone.utc)
    res = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        corridor_km=25.0,
        backtrack_hours=8.0,
    )

    assert res["investigation_id"].startswith("inv_eval_")
    assert res["is_historical_demo"] is True
    assert res["selected_image_id"] == "ref_corsica_2018"
    assert "reconstructed_source" in res
    assert "backward_steps" in res
    assert len(res["backward_steps"]) > 0

    # Attribution check
    assert res["final_attribution"]["eligible_candidate_count"] > 0
    top_cand = res["final_attribution"]["top_candidate"]
    assert top_cand is not None
    assert 0.0 <= top_cand["model_probability"] <= 1.0
    assert 0.0 <= top_cand["scenario_normalized_score"] <= 1.0

    # Persistence verification: fetch back and confirm identical without rerunning
    inv_id = res["investigation_id"]
    from app.services.real_experiment.experiment_store import get_experiment_store
    reloaded = get_experiment_store().get_evaluator_investigation(inv_id)
    assert reloaded is not None
    assert reloaded["investigation_id"] == inv_id
    assert reloaded["model_version"] == res["model_version"]
    assert reloaded["final_attribution"]["top_candidate"]["vessel_name"] == top_cand["vessel_name"]

    # History listing check
    listing = get_experiment_store().list_evaluator_investigations(limit=10)
    assert any(item["investigation_id"] == inv_id for item in listing)
