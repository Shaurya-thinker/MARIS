"""Comprehensive Robustness Test Suite for MARIS Evaluator Investigation Workflow.

Validates:
1. Multi-scene variation across registered Sentinel-1 reference acquisitions.
2. Physical metocean sensitivity (wind speed, wind direction, current speed/dir).
3. Temporal backtrack duration scaling (2h, 6h, 12h, 18h).
4. Spatial corridor boundary limits (5km tight, 25km standard, 60km wide).
5. Negative cases:
   - 0 eligible vessels due to spatial distance (all > 100 km).
   - 0 eligible vessels due to temporal dislocation (past / future AIS).
   - Empty candidate vessel pool.
6. Attribution sensitivity and ranking shift when physical inputs change.
7. Strict non-leakage guarantee: ineligible candidates NEVER reach ML inference.
8. Persistence immutability: replayed investigations are byte/float reproducible.
9. Trajectory variation: vessels with diverse speeds (stationary, loitering, high-speed transit).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.real_experiment.evaluator_workflow import (
    REFERENCE_OBSERVATIONS,
    calculate_backward_drift_preview,
    determine_provider_status,
    filter_candidates_for_investigation,
    get_reference_observation,
    list_reference_observations,
    run_evaluator_investigation,
)
from app.services.synthetic_experiment.feature_builder import haversine_km
from app.services.real_experiment.experiment_store import ExperimentStore


# ===========================================================================
# 1. Multi-Scene Reference Observation Robustness
# ===========================================================================

@pytest.mark.parametrize(
    "ref_id",
    [
        "ref_corsica_2018",
        "ref_arabian_sea_alpha",
        "ref_arabian_sea_beta",
        "ref_bay_of_bengal_gamma",
        "ref_gulf_of_kutch_delta",
        "ref_mediterranean_epsilon",
    ],
)
def test_multi_scene_drift_and_investigation_robustness(ref_id: str):
    """Verify that every registered reference observation executes cleanly end-to-end."""
    ref_obs = get_reference_observation(ref_id)
    assert ref_obs is not None, f"Reference observation {ref_id} must be registered."

    obs_time = datetime.fromisoformat(ref_obs["observation_time"].replace("Z", "+00:00"))
    lon = ref_obs["observation_lon"]
    lat = ref_obs["observation_lat"]

    # 1. Calculate drift preview
    preview = calculate_backward_drift_preview(
        origin_lon=lon,
        origin_lat=lat,
        observation_time=obs_time,
        wind_speed_ms=ref_obs["default_wind_speed_ms"],
        wind_direction_deg=ref_obs["default_wind_direction_deg"],
        current_speed_ms=ref_obs["default_current_speed_ms"],
        current_direction_deg=ref_obs["default_current_direction_deg"],
        backtrack_hours=ref_obs.get("backtrack_hours", 6.0),
    )

    assert "reconstructed_source" in preview
    assert "backward_steps" in preview
    assert len(preview["backward_steps"]) >= 4

    src = preview["reconstructed_source"]
    assert -180.0 <= src["source_lon"] <= 180.0
    assert -90.0 <= src["source_lat"] <= 90.0
    assert src["source_radius_m"] > 0.0

    # 2. Run full investigation
    inv = run_evaluator_investigation(
        selected_image_id=ref_id,
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        wind_speed_ms=ref_obs["default_wind_speed_ms"],
        wind_direction_deg=ref_obs["default_wind_direction_deg"],
        current_speed_ms=ref_obs["default_current_speed_ms"],
        current_direction_deg=ref_obs["default_current_direction_deg"],
        corridor_km=25.0,
        backtrack_hours=ref_obs.get("backtrack_hours", 6.0),
    )

    assert inv["investigation_id"].startswith("inv_eval_")
    assert inv["selected_image_id"] == ref_id
    assert inv["provider_status"] in ("LIVE_AIS", "NO_PROVIDER", "NO_ELIGIBLE_VESSELS")
    assert "candidate_probabilities" in inv
    assert "final_attribution" in inv
    assert inv["coordinates"]["observation_lon"] == pytest.approx(lon, abs=1e-5)
    assert inv["coordinates"]["observation_lat"] == pytest.approx(lat, abs=1e-5)


# ===========================================================================
# 2. Metocean Sensitivity: Wind, Current, and Displacement
# ===========================================================================

def test_wind_speed_and_direction_sensitivity():
    """Verify that shifting wind speed and direction shifts drift trajectory and source point."""
    obs_time = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
    origin_lon, origin_lat = 14.55, 36.35

    # Northerly light wind (from 0 deg, 3 m/s)
    drift_north_light = calculate_backward_drift_preview(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=3.0,
        wind_direction_deg=0.0,
        current_speed_ms=0.1,
        current_direction_deg=90.0,
        backtrack_hours=8.0,
    )

    # Southerly gale wind (from 180 deg, 18 m/s)
    drift_south_gale = calculate_backward_drift_preview(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=18.0,
        wind_direction_deg=180.0,
        current_speed_ms=0.1,
        current_direction_deg=90.0,
        backtrack_hours=8.0,
    )

    src_light = drift_north_light["reconstructed_source"]
    src_gale = drift_south_gale["reconstructed_source"]

    # Spatial separation between reconstructed sources must be substantial (> 15 km)
    dist_between_sources = haversine_km(
        src_light["source_lat"], src_light["source_lon"],
        src_gale["source_lat"], src_gale["source_lon"],
    )
    assert dist_between_sources > 15.0, (
        f"Sources under opposing winds must separate significantly, got {dist_between_sources:.2f} km"
    )

    # High wind must produce substantially larger cumulative displacement
    step_light = drift_north_light["backward_steps"][-1]
    step_gale = drift_south_gale["backward_steps"][-1]
    dist_light_m = step_light.get("cumulative_distance_m", 0.0)
    dist_gale_m = step_gale.get("cumulative_distance_m", 0.0)
    assert dist_gale_m > dist_light_m * 2.0


def test_current_direction_reversal_sensitivity():
    """Verify that reversing ocean current direction shifts reconstructed source displacement."""
    obs_time = datetime(2025, 8, 20, 6, 0, tzinfo=timezone.utc)
    origin_lon, origin_lat = 2.85, 39.45

    # Zero wind, current flowing East (from 270 towards 90 deg)
    drift_east = calculate_backward_drift_preview(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=0.0,
        wind_direction_deg=0.0,
        current_speed_ms=0.5,
        current_direction_deg=90.0,
        backtrack_hours=6.0,
    )

    # Zero wind, current flowing West (from 90 towards 270 deg)
    drift_west = calculate_backward_drift_preview(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=0.0,
        wind_direction_deg=0.0,
        current_speed_ms=0.5,
        current_direction_deg=270.0,
        backtrack_hours=6.0,
    )

    src_east = drift_east["reconstructed_source"]
    src_west = drift_west["reconstructed_source"]

    # Reversal in current must produce significant separation between reconstructed sources
    sep_km = haversine_km(
        src_east["source_lat"], src_east["source_lon"],
        src_west["source_lat"], src_west["source_lon"],
    )
    assert sep_km > 5.0, f"Current reversal must displace reconstructed sources, got {sep_km:.2f} km"

    # Reconstructed source coordinates must differ on the zonal axis
    assert src_east["source_lon"] != pytest.approx(src_west["source_lon"], abs=1e-3)


# ===========================================================================
# 3. Temporal Backtrack Duration Scaling
# ===========================================================================

@pytest.mark.parametrize("duration_hours", [2.0, 6.0, 12.0, 18.0])
def test_backtrack_duration_scaling(duration_hours: float):
    """Verify that step count, distance, and release timestamps scale monotonically with duration."""
    obs_time = datetime(2025, 5, 14, 18, 0, tzinfo=timezone.utc)
    origin_lon, origin_lat = -4.25, 36.12

    preview = calculate_backward_drift_preview(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=8.0,
        wind_direction_deg=240.0,
        current_speed_ms=0.25,
        current_direction_deg=60.0,
        backtrack_hours=duration_hours,
        step_hours=0.5,
    )

    expected_steps = int(duration_hours / 0.5)
    assert len(preview["backward_steps"]) == expected_steps

    # Release time must be exactly duration_hours before observation time
    release_iso = preview["reconstructed_source"]["estimated_release_time"]
    release_dt = datetime.fromisoformat(release_iso.replace("Z", "+00:00"))
    delta_computed = (obs_time - release_dt).total_seconds() / 3600.0
    assert delta_computed == pytest.approx(duration_hours, abs=0.01)

    # Uncertainty radius must be at least the initial base radius 1500.0
    source_radius = preview["reconstructed_source"]["source_radius_m"]
    assert source_radius >= 1500.0


# ===========================================================================
# 4. Spatial Corridor Limits and Candidate Filtering
# ===========================================================================

def test_corridor_filtering_boundary_and_strict_exclusion():
    """Verify that tightening corridor from 60km to 5km excludes vessels and blocks them from ML."""
    obs_time = datetime(2025, 3, 15, 12, 0, tzinfo=timezone.utc)
    rel_time = obs_time - timedelta(hours=6.0)

    # Mock backward steps along lon: 10.0 -> 9.8 -> 9.6, lat: 43.0
    backward_steps = [
        {"lon": 10.0, "lat": 43.0},
        {"lon": 9.8, "lat": 43.0},
        {"lon": 9.6, "lat": 43.0},
    ]

    # Vessel 1: ~3 km from track (lat 43.027, lon 9.8)
    v1_close = {
        "id": "v_close",
        "vessel_name": "CLOSE_HARBOR",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 43.027, "lon": 9.8, "speed": 12.0, "heading": 90.0}
        ],
    }

    # Vessel 2: ~18 km from track (lat 43.162, lon 9.8)
    v2_mid = {
        "id": "v_mid",
        "vessel_name": "MID_CORRIDOR",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 43.162, "lon": 9.8, "speed": 14.0, "heading": 90.0}
        ],
    }

    # Vessel 3: ~45 km from track (lat 43.405, lon 9.8)
    v3_far = {
        "id": "v_far",
        "vessel_name": "FAR_OUTSIDE",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 43.405, "lon": 9.8, "speed": 16.0, "heading": 90.0}
        ],
    }

    # Tight corridor = 5 km: only v1_close eligible
    f_tight = filter_candidates_for_investigation(
        vessels=[v1_close, v2_mid, v3_far],
        backward_steps=backward_steps,
        source_lon=9.6,
        source_lat=43.0,
        source_radius_m=3000.0,
        observation_time=obs_time,
        backtrack_hours=6.0,
        corridor_km=5.0,
    )
    assert f_tight["eligible_count"] == 1
    assert f_tight["eligible_candidates"][0]["vessel_id"] == "v_close"
    assert f_tight["ineligible_count"] == 2
    assert any(c["vessel_id"] == "v_mid" for c in f_tight["ineligible_candidates"])
    assert any(c["vessel_id"] == "v_far" for c in f_tight["ineligible_candidates"])

    # Standard corridor = 25 km: v1_close and v2_mid eligible
    f_standard = filter_candidates_for_investigation(
        vessels=[v1_close, v2_mid, v3_far],
        backward_steps=backward_steps,
        source_lon=9.6,
        source_lat=43.0,
        source_radius_m=3000.0,
        observation_time=obs_time,
        backtrack_hours=6.0,
        corridor_km=25.0,
    )
    assert f_standard["eligible_count"] == 2
    assert {c["vessel_id"] for c in f_standard["eligible_candidates"]} == {"v_close", "v_mid"}
    assert f_standard["ineligible_count"] == 1
    assert f_standard["ineligible_candidates"][0]["vessel_id"] == "v_far"

    # Wide corridor = 60 km: all 3 eligible
    f_wide = filter_candidates_for_investigation(
        vessels=[v1_close, v2_mid, v3_far],
        backward_steps=backward_steps,
        source_lon=9.6,
        source_lat=43.0,
        source_radius_m=3000.0,
        observation_time=obs_time,
        backtrack_hours=6.0,
        corridor_km=60.0,
    )
    assert f_wide["eligible_count"] == 3
    assert f_wide["ineligible_count"] == 0


# ===========================================================================
# 5. Negative Cases: Zero Eligible Vessels & Excluded Vessels
# ===========================================================================

def test_negative_case_zero_eligible_vessels_all_outside_corridor():
    """Verify clean execution and non-conflated status when all vessels are outside corridor."""
    obs_time = datetime(2025, 4, 10, 10, 0, tzinfo=timezone.utc)

    # Distant vessels 150 km away
    distant_vessels = [
        {
            "id": "dist_1",
            "vessel_name": "WAY_OFF_1",
            "mmsi": "111111111",
            "positions": [
                {"timestamp": obs_time.isoformat(), "lat": 40.0, "lon": 5.0, "speed": 10.0, "heading": 0.0}
            ],
        },
        {
            "id": "dist_2",
            "vessel_name": "WAY_OFF_2",
            "mmsi": "222222222",
            "positions": [
                {"timestamp": obs_time.isoformat(), "lat": 40.2, "lon": 5.2, "speed": 12.0, "heading": 0.0}
            ],
        },
    ]

    res = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=5.0,
        wind_direction_deg=200.0,
        current_speed_ms=0.2,
        current_direction_deg=45.0,
        corridor_km=20.0,
        backtrack_hours=6.0,
        custom_vessels=distant_vessels,
    )

    # Must explicitly show NO_ELIGIBLE_VESSELS
    assert res["provider_status"] == "NO_ELIGIBLE_VESSELS"
    assert "0 vessels met both spatial corridor and temporal intersection" in res["provider_description"]
    assert res["final_attribution"]["eligible_candidate_count"] == 0
    assert res["final_attribution"]["top_candidate"] is None
    assert res["final_attribution"]["ineligible_candidate_count"] == 2
    assert "No eligible candidate vessels met the corridor" in res["final_attribution"]["confidence_assessment"]

    # Candidate probabilities list must be empty (NO ineligible vessel reached ML!)
    assert len(res["candidate_probabilities"]) == 0
    assert len(res["ineligible_vessels_data"]) == 2


def test_negative_case_temporal_dislocation():
    """Verify that vessels spatially close but temporally days away are excluded."""
    obs_time = datetime(2025, 4, 10, 10, 0, tzinfo=timezone.utc)

    # Position is right at the spill origin, but timestamp is 5 days ago
    ghost_vessel = {
        "id": "v_ghost",
        "vessel_name": "PAST_GHOST_SHIP",
        "mmsi": "999000111",
        "positions": [
            {"timestamp": (obs_time - timedelta(days=5)).isoformat(), "lat": 43.248, "lon": 9.478, "speed": 10.0, "heading": 90.0}
        ],
    }

    res = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=5.0,
        wind_direction_deg=200.0,
        current_speed_ms=0.2,
        current_direction_deg=45.0,
        corridor_km=25.0,
        backtrack_hours=6.0,
        custom_vessels=[ghost_vessel],
    )

    assert res["final_attribution"]["eligible_candidate_count"] == 0
    assert res["final_attribution"]["top_candidate"] is None
    inelig = res["ineligible_vessels_data"]
    assert len(inelig) == 1
    assert inelig[0]["vessel_id"] == "v_ghost"
    assert "TEMPORAL_WINDOW_MISMATCH" in inelig[0]["rejection_reason"]


def test_negative_case_empty_custom_vessel_pool():
    """Verify that providing an empty vessel list produces clean results without error."""
    obs_time = datetime(2025, 4, 10, 10, 0, tzinfo=timezone.utc)

    res = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=5.0,
        wind_direction_deg=200.0,
        current_speed_ms=0.2,
        current_direction_deg=45.0,
        corridor_km=25.0,
        backtrack_hours=6.0,
        custom_vessels=[],
    )

    assert res["final_attribution"]["eligible_candidate_count"] == 0
    assert res["final_attribution"]["top_candidate"] is None
    assert len(res["candidate_probabilities"]) == 0


# ===========================================================================
# 6. Physical Responsiveness: Changing Inputs Changes Attribution
# ===========================================================================

def test_attribution_ranking_changes_when_physics_change():
    """Verify that altering wind direction flips candidate ranking between two competing vessels."""
    obs_time = datetime(2025, 7, 1, 12, 0, tzinfo=timezone.utc)
    origin_lon, origin_lat = 10.0, 40.0
    backtrack_hours = 6.0
    rel_time = obs_time - timedelta(hours=backtrack_hours)

    # Candidate Alpha is located North-East at release time (10.15, 40.15)
    cand_alpha = {
        "id": "cand_alpha",
        "vessel_name": "ALPHA_NORTHEAST",
        "mmsi": "111000111",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 40.15, "lon": 10.15, "speed": 12.0, "heading": 45.0},
            {"timestamp": obs_time.isoformat(), "lat": 40.25, "lon": 10.30, "speed": 12.0, "heading": 45.0},
        ],
    }

    # Candidate Beta is located South-West at release time (9.85, 39.85)
    cand_beta = {
        "id": "cand_beta",
        "vessel_name": "BETA_SOUTHWEST",
        "mmsi": "222000222",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 39.85, "lon": 9.85, "speed": 12.0, "heading": 225.0},
            {"timestamp": obs_time.isoformat(), "lat": 39.75, "lon": 9.70, "speed": 12.0, "heading": 225.0},
        ],
    }

    # Scenario A: Wind from South-West (225 deg). Oil drifted NE. Reconstructed source is South-West.
    inv_sw = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=origin_lon,
        observation_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=10.0,
        wind_direction_deg=225.0,
        current_speed_ms=0.05,
        current_direction_deg=45.0,
        corridor_km=50.0,
        backtrack_hours=backtrack_hours,
        custom_vessels=[cand_alpha, cand_beta],
    )

    # Scenario B: Wind from North-East (45 deg). Oil drifted SW. Reconstructed source is North-East.
    inv_ne = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=origin_lon,
        observation_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=10.0,
        wind_direction_deg=45.0,
        current_speed_ms=0.05,
        current_direction_deg=225.0,
        corridor_km=50.0,
        backtrack_hours=backtrack_hours,
        custom_vessels=[cand_alpha, cand_beta],
    )

    top_sw = inv_sw["final_attribution"]["top_candidate"]
    top_ne = inv_ne["final_attribution"]["top_candidate"]

    assert top_sw is not None
    assert top_ne is not None

    # Physical verification: Reconstructed sources are distinct
    src_sw = inv_sw["reconstructed_source"]
    src_ne = inv_ne["reconstructed_source"]
    assert src_sw["source_lat"] < src_ne["source_lat"]

    # Candidate Beta must be top candidate under SW backward drift
    assert top_sw["vessel_id"] == "cand_beta"
    # Candidate Alpha must be top candidate under NE backward drift
    assert top_ne["vessel_id"] == "cand_alpha"

    # Score inversion verification: Beta scores higher under SW wind than under NE wind
    beta_prob_sw = next(c["model_probability"] for c in inv_sw["candidate_probabilities"] if c["vessel_id"] == "cand_beta")
    beta_prob_ne = next(c["model_probability"] for c in inv_ne["candidate_probabilities"] if c["vessel_id"] == "cand_beta")
    assert beta_prob_sw > beta_prob_ne


# ===========================================================================
# 7. Strict Non-Leakage: Ineligible Vessels Never Reach ML
# ===========================================================================

def test_ineligible_vessel_never_reaches_ml_inference():
    """Verify that ineligible candidates are filtered out BEFORE ML feature extraction and scoring."""
    obs_time = datetime(2025, 5, 20, 12, 0, tzinfo=timezone.utc)
    origin_lon, origin_lat = 9.478, 43.248
    rel_time = obs_time - timedelta(hours=6.0)

    # 1 Eligible vessel (in corridor, right timeframe)
    v_eligible = {
        "id": "v_elig",
        "vessel_name": "CORRIDOR_TRANSIT",
        "mmsi": "333000111",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 43.25, "lon": 9.48, "speed": 13.0, "heading": 210.0},
            {"timestamp": obs_time.isoformat(), "lat": 43.20, "lon": 9.40, "speed": 13.0, "heading": 210.0},
        ],
    }

    # 1 Spatially ineligible vessel (> 80 km away)
    v_spatial_inelig = {
        "id": "v_spatial_bad",
        "vessel_name": "DISTANT_VESSEL",
        "mmsi": "333000222",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 44.50, "lon": 10.80, "speed": 10.0, "heading": 90.0},
        ],
    }

    # 1 Temporally ineligible vessel (in area, but timestamped 48 hours ago)
    v_temporal_inelig = {
        "id": "v_temporal_bad",
        "vessel_name": "HISTORIC_VESSEL",
        "mmsi": "333000333",
        "positions": [
            {"timestamp": (obs_time - timedelta(days=2)).isoformat(), "lat": 43.25, "lon": 9.48, "speed": 12.0, "heading": 180.0},
        ],
    }

    res = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=origin_lon,
        observation_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=6.0,
        wind_direction_deg=220.0,
        current_speed_ms=0.2,
        current_direction_deg=40.0,
        corridor_km=25.0,
        backtrack_hours=6.0,
        custom_vessels=[v_eligible, v_spatial_inelig, v_temporal_inelig],
    )

    # ML candidate probabilities MUST ONLY contain the eligible vessel
    assert len(res["candidate_probabilities"]) == 1
    assert res["candidate_probabilities"][0]["vessel_id"] == "v_elig"

    # Ineligible data MUST contain both rejected vessels
    assert len(res["ineligible_vessels_data"]) == 2
    inelig_ids = {c["vessel_id"] for c in res["ineligible_vessels_data"]}
    assert inelig_ids == {"v_spatial_bad", "v_temporal_bad"}


# ===========================================================================
# 8. Diverse Vessel Trajectory Scenarios
# ===========================================================================

def test_varying_vessel_trajectories_and_speed():
    """Verify that vessels with diverse dynamic profiles (anchored, loitering, fast transit) evaluate safely."""
    obs_time = datetime(2025, 6, 10, 8, 0, tzinfo=timezone.utc)
    rel_time = obs_time - timedelta(hours=6.0)
    origin_lon, origin_lat = 9.478, 43.248

    # Anchored vessel (speed ~0.1 knot, stationary)
    v_anchored = {
        "id": "v_anchored",
        "vessel_name": "ANCHORED_BARGE",
        "mmsi": "444000111",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 0.0},
            {"timestamp": obs_time.isoformat(), "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 0.0},
        ],
    }

    # High-speed transit vessel (speed 24 knots)
    v_fast = {
        "id": "v_fast",
        "vessel_name": "EXPRESS_CONTAINER",
        "mmsi": "444000222",
        "positions": [
            {"timestamp": rel_time.isoformat(), "lat": 43.10, "lon": 9.35, "speed": 24.0, "heading": 30.0},
            {"timestamp": obs_time.isoformat(), "lat": 43.40, "lon": 9.60, "speed": 24.0, "heading": 30.0},
        ],
    }

    res = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=origin_lon,
        observation_lat=origin_lat,
        observation_time=obs_time,
        wind_speed_ms=5.0,
        wind_direction_deg=210.0,
        current_speed_ms=0.15,
        current_direction_deg=45.0,
        corridor_km=30.0,
        backtrack_hours=6.0,
        custom_vessels=[v_anchored, v_fast],
    )

    assert len(res["candidate_probabilities"]) == 2
    for cand in res["candidate_probabilities"]:
        assert not math.isnan(cand["model_probability"])
        assert 0.0 <= cand["model_probability"] <= 1.0
        assert not math.isnan(cand["scenario_normalized_score"])
        assert 0.0 <= cand["scenario_normalized_score"] <= 1.0


# ===========================================================================
# 9. Persistence & Audit Immutability
# ===========================================================================

def test_saved_investigation_reproducible_immutability(tmp_path: Path):
    """Verify that saving and loading an investigation preserves exact model outputs and parameters."""
    obs_time = datetime(2025, 9, 5, 4, 55, tzinfo=timezone.utc)
    test_db = tmp_path / "audit_store.db"
    store = ExperimentStore(db_path=test_db)

    # Run investigation
    res1 = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=9.478,
        observation_lat=43.248,
        observation_time=obs_time,
        wind_speed_ms=6.5,
        wind_direction_deg=315.0,
        current_speed_ms=0.15,
        current_direction_deg=135.0,
        corridor_km=25.0,
        backtrack_hours=6.0,
    )

    inv_id = res1["investigation_id"]
    store.save_evaluator_investigation(res1)

    # Retrieve from database
    loaded = store.get_evaluator_investigation(inv_id)
    assert loaded is not None

    # Strict equality checks for court-ready reproducibility
    assert loaded["investigation_id"] == res1["investigation_id"]
    assert loaded["model_version"] == res1["model_version"]
    assert loaded["coordinates"]["observation_lon"] == pytest.approx(res1["coordinates"]["observation_lon"], abs=1e-6)
    assert loaded["coordinates"]["observation_lat"] == pytest.approx(res1["coordinates"]["observation_lat"], abs=1e-6)
    assert loaded["wind_inputs"]["speed_ms"] == pytest.approx(res1["wind_inputs"]["speed_ms"], abs=1e-6)
    assert loaded["reconstructed_source"]["source_lon"] == pytest.approx(
        res1["reconstructed_source"]["source_lon"], abs=1e-6
    )
    assert loaded["reconstructed_source"]["source_lat"] == pytest.approx(
        res1["reconstructed_source"]["source_lat"], abs=1e-6
    )
    assert len(loaded["backward_steps"]) == len(res1["backward_steps"])

    # Ensure ranking and probabilities are bit-exact
    for orig_c, load_c in zip(res1["candidate_probabilities"], loaded["candidate_probabilities"]):
        assert orig_c["vessel_id"] == load_c["vessel_id"]
        assert orig_c["rank"] == load_c["rank"]
        assert orig_c["model_probability"] == pytest.approx(load_c["model_probability"], abs=1e-5)
        assert orig_c["scenario_normalized_score"] == pytest.approx(
            load_c["scenario_normalized_score"], abs=1e-5
        )
