"""Unit and regression tests for Phase #3 — Dynamic AIS Correlation.

Covers:
1. Dynamic corridor envelope derived from backward_steps.
2. Uncertainty radius incorporated into the corridor.
3. Temporal corridor derivation.
4. AIS database queried using the dynamic corridor.
5. Static Sentinel-1 bbox is NOT used when dynamic drift information is available.
6. Vessel tracks compared against backward_steps.
7. Minimum trajectory distance (CPA) calculated correctly.
8. Trajectory time delta calculated correctly.
9. Source-zone intersection detected correctly within temporal tolerance (+/- 1.5h).
10. Dynamic candidates change when drift trajectory changes.
11. Zero fabrication: No AIS interpolation or dead-reckoning occurs.
12. Existing manual selected_vessels behavior still works.
13. Existing AIS database / provenance behavior remains intact.
14. API /ais/search switches between dynamic corridor and static bbox based on backward_steps presence.
"""

from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.real_experiment.ais_database import (
    get_connection,
    init_ais_database,
)
from app.services.real_experiment.ais_search import (
    VesselTrackSummary,
    _haversine_km,
    derive_drift_ais_corridor,
    search_along_drift_corridor,
)
from app.services.real_experiment.experiment_runner import (
    ExperimentRunner,
    VesselFeatures,
)
from app.services.source_estimation import BackwardDriftStep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_drift_steps() -> list[BackwardDriftStep]:
    """Synthetic backward drift steps starting at Cap Corse detecting slick and drifting SW."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    steps = [
        BackwardDriftStep(
            lon=9.4913,
            lat=43.2736,
            timestamp=obs_t,
            uncertainty_radius_m=500.0,
            u_wind_ms=0.0,
            v_wind_ms=0.0,
            drift_u_ms=0.0,
            drift_v_ms=0.0,
            cumulative_backward_distance_m=0.0,
        ),
        BackwardDriftStep(
            lon=9.4500,
            lat=43.2500,
            timestamp=obs_t - timedelta(hours=2),
            uncertainty_radius_m=1500.0,
            u_wind_ms=0.0,
            v_wind_ms=0.0,
            drift_u_ms=0.0,
            drift_v_ms=0.0,
            cumulative_backward_distance_m=1000.0,
        ),
        BackwardDriftStep(
            lon=9.4000,
            lat=43.2200,
            timestamp=obs_t - timedelta(hours=4),
            uncertainty_radius_m=3000.0,
            u_wind_ms=0.0,
            v_wind_ms=0.0,
            drift_u_ms=0.0,
            drift_v_ms=0.0,
            cumulative_backward_distance_m=3000.0,
        ),
        BackwardDriftStep(
            lon=9.3500,
            lat=43.1800,
            timestamp=obs_t - timedelta(hours=6),
            uncertainty_radius_m=5000.0,
            u_wind_ms=0.0,
            v_wind_ms=0.0,
            drift_u_ms=0.0,
            drift_v_ms=0.0,
            cumulative_backward_distance_m=6000.0,
        ),
    ]
    return steps


@pytest.fixture
def isolated_ais_db(tmp_path: Path) -> Path:
    """Create an isolated, temporary SQLite AIS database for correlation testing."""
    db_path = tmp_path / "test_ais_vessels.db"
    init_ais_database(db_path)

    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # Insert data_source record
    cur.execute(
        """
        INSERT INTO data_sources (source_id, provider_name, source_type, acquisition_timestamp, is_real_observation)
        VALUES ('src_test', 'ais_vessels.db', 'MANUAL_REFERENCE', ?, 1)
        """,
        (obs_t.isoformat().replace("+00:00", "Z"),)
    )

    now_iso = obs_t.isoformat().replace("+00:00", "Z")

    # 1. Candidate Vessel 1: Close to trajectory step 2 and enters source zone at source time (t - 6h)
    cur.execute(
        """
        INSERT INTO vessels (vessel_id, mmsi, vessel_name, vessel_type, flag_country, imo, source_id, source_type, is_real_observation, created_at)
        VALUES ('v_ulysse', '228064900', 'ULYSSE', 'Cargo', 'France', '9123456', 'src_test', 'sqlite_ais', 1, ?)
        """,
        (now_iso,)
    )
    # Positions: one near step 2 (43.2205, 9.4005) at t-4h, and one in source zone (43.1802, 9.3501) at t-6h
    p1_t = (obs_t - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    p2_t = (obs_t - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    cur.execute(
        """
        INSERT INTO ais_positions (vessel_id, mmsi, timestamp, lat, lon, sog, cog, heading, source_id, source_type, is_real_observation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'src_test', 'sqlite_ais', 1)
        """,
        ('v_ulysse', '228064900', p1_t, 43.2205, 9.4005, 12.5, 210.0, 210.0),
    )
    cur.execute(
        """
        INSERT INTO ais_positions (vessel_id, mmsi, timestamp, lat, lon, sog, cog, heading, source_id, source_type, is_real_observation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'src_test', 'sqlite_ais', 1)
        """,
        ('v_ulysse', '228064900', p2_t, 43.1802, 9.3501, 10.0, 210.0, 210.0),
    )

    # 2. Candidate Vessel 2: Far outside corridor (e.g. lat=42.0, lon=8.0)
    cur.execute(
        """
        INSERT INTO vessels (vessel_id, mmsi, vessel_name, vessel_type, flag_country, imo, source_id, source_type, is_real_observation, created_at)
        VALUES ('v_far', '999999999', 'FAR_AWAY', 'Tanker', 'Panama', '9876543', 'src_test', 'sqlite_ais', 1, ?)
        """,
        (now_iso,)
    )
    cur.execute(
        """
        INSERT INTO ais_positions (vessel_id, mmsi, timestamp, lat, lon, sog, cog, heading, source_id, source_type, is_real_observation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'src_test', 'sqlite_ais', 1)
        """,
        ('v_far', '999999999', p1_t, 42.0000, 8.0000, 15.0, 90.0, 90.0),
    )

    # 3. Candidate Vessel 3: Intersects source location spatially, but 10 hours LATER (temporal mismatch)
    cur.execute(
        """
        INSERT INTO vessels (vessel_id, mmsi, vessel_name, vessel_type, flag_country, imo, source_id, source_type, is_real_observation, created_at)
        VALUES ('v_late', '888888888', 'LATE_ARRIVER', 'Cargo', 'Malta', '9555555', 'src_test', 'sqlite_ais', 1, ?)
        """,
        (now_iso,)
    )
    late_t = (obs_t + timedelta(hours=10)).isoformat().replace("+00:00", "Z")
    cur.execute(
        """
        INSERT INTO ais_positions (vessel_id, mmsi, timestamp, lat, lon, sog, cog, heading, source_id, source_type, is_real_observation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'src_test', 'sqlite_ais', 1)
        """,
        ('v_late', '888888888', late_t, 43.1802, 9.3501, 8.0, 180.0, 180.0),
    )

    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_1_dynamic_corridor_envelope_derived(sample_drift_steps):
    """1. Dynamic corridor envelope is correctly derived from backward_steps."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    corridor = derive_drift_ais_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        buffer_km=5.0,
    )

    assert isinstance(corridor, dict)
    # The southernmost step is lat=43.1800, northernmost is 43.2736
    # The westernmost is lon=9.3500, easternmost is 9.4913
    # Bounding box must fully contain these plus the uncertainty and buffer
    assert corridor["south"] < 43.1800
    assert corridor["north"] > 43.2736
    assert corridor["west"] < 9.3500
    assert corridor["east"] > 9.4913


def test_2_uncertainty_radius_incorporated_into_corridor(sample_drift_steps):
    """2. Uncertainty radius is incorporated into the corridor."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)

    # Derive corridor with 0 uncertainty
    steps_no_unc = [
        BackwardDriftStep(
            lon=s.lon,
            lat=s.lat,
            timestamp=s.timestamp,
            uncertainty_radius_m=0.0,
            u_wind_ms=s.u_wind_ms,
            v_wind_ms=s.v_wind_ms,
            drift_u_ms=s.drift_u_ms,
            drift_v_ms=s.drift_v_ms,
            cumulative_backward_distance_m=s.cumulative_backward_distance_m,
        )
        for s in sample_drift_steps
    ]
    corridor_tight = derive_drift_ais_corridor(
        backward_steps=steps_no_unc,
        observation_time=obs_t,
        backtrack_hours=6.0,
        buffer_km=0.0,
    )

    # Corridor with real expanding uncertainty
    corridor_expanded = derive_drift_ais_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        buffer_km=0.0,
    )

    # The expanded corridor with uncertainty should be strictly wider
    assert corridor_expanded["west"] < corridor_tight["west"]
    assert corridor_expanded["south"] < corridor_tight["south"]
    assert corridor_expanded["east"] > corridor_tight["east"]
    assert corridor_expanded["north"] > corridor_tight["north"]


def test_3_temporal_corridor_derived_correctly(sample_drift_steps):
    """3. Temporal corridor is correctly derived."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    corridor = derive_drift_ais_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
    )

    # Source time is obs_t - 6h
    expected_source_time = datetime(2018, 10, 8, 0, 0, tzinfo=timezone.utc)
    assert corridor["source_time"] == expected_source_time
    assert corridor["observation_time"] == obs_t
    # Search window starts with 1h padding before source time
    assert corridor["window_start"] == expected_source_time - timedelta(hours=1.0)
    assert corridor["window_end"] == obs_t


def test_4_and_5_ais_queried_along_dynamic_corridor_not_static_bbox(sample_drift_steps, isolated_ais_db):
    """4 & 5. AIS database is queried using dynamic corridor; static scene bbox is NOT used."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    source_candidate_zone = {
        "type": "Polygon",
        "coordinates": [[[9.3, 43.1], [9.4, 43.1], [9.4, 43.2], [9.3, 43.2], [9.3, 43.1]]]
    }

    results = search_along_drift_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        source_candidate_zone=source_candidate_zone,
        db_path=isolated_ais_db,
    )

    mmsis = [v.mmsi for v in results]
    # Vessel 1 (ULYSSE) is in corridor and must be found
    assert "228064900" in mmsis
    # Vessel 2 (FAR_AWAY) is at 42.0°N, 8.0°E (far outside dynamic corridor) and must NOT be found
    assert "999999999" not in mmsis
    # Vessel 3 (LATE_ARRIVER) is outside temporal window and must NOT be found
    assert "888888888" not in mmsis


def test_6_7_8_track_level_distance_and_time_delta(sample_drift_steps, isolated_ais_db):
    """6, 7 & 8. Vessel tracks are compared against backward_steps: min distance and time delta."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    results = search_along_drift_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        db_path=isolated_ais_db,
    )

    ulysse = next((v for v in results if v.mmsi == "228064900"), None)
    assert ulysse is not None

    # Step 2 is at (43.2200, 9.4000), position 1 is at (43.2205, 9.4005)
    # The distance is ~0.06 km
    assert ulysse.min_trajectory_distance_km is not None
    assert ulysse.min_trajectory_distance_km < 1.0  # very close!

    # Time delta between position at t-4h and step 2 at t-4h should be ~0.0h
    assert ulysse.trajectory_time_delta_hours is not None
    assert abs(ulysse.trajectory_time_delta_hours - 0.0) < 0.01


def test_9_source_zone_intersection_detected(sample_drift_steps, isolated_ais_db):
    """9. Source-zone intersection is detected correctly within temporal tolerance (+/- 1.5h)."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    results = search_along_drift_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        db_path=isolated_ais_db,
    )

    ulysse = next((v for v in results if v.mmsi == "228064900"), None)
    assert ulysse is not None
    # Position at (43.1802, 9.3501) at t-6h matches step 3 (earliest source step) within 5km radius
    assert ulysse.source_zone_intersection is True


def test_10_dynamic_candidates_change_when_trajectory_changes(sample_drift_steps, isolated_ais_db):
    """10. Dynamic candidates can change when the drift trajectory changes."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)

    # Shift drift trajectory completely away from Ulysse (e.g. drift goes NE instead of SW)
    shifted_steps = [
        BackwardDriftStep(lon=9.4913, lat=43.2736, timestamp=obs_t, uncertainty_radius_m=500.0, u_wind_ms=0.0, v_wind_ms=0.0, drift_u_ms=0.0, drift_v_ms=0.0, cumulative_backward_distance_m=0.0),
        BackwardDriftStep(lon=9.6000, lat=43.3500, timestamp=obs_t - timedelta(hours=2), uncertainty_radius_m=1000.0, u_wind_ms=0.0, v_wind_ms=0.0, drift_u_ms=0.0, drift_v_ms=0.0, cumulative_backward_distance_m=1000.0),
        BackwardDriftStep(lon=9.7000, lat=43.4500, timestamp=obs_t - timedelta(hours=4), uncertainty_radius_m=1000.0, u_wind_ms=0.0, v_wind_ms=0.0, drift_u_ms=0.0, drift_v_ms=0.0, cumulative_backward_distance_m=2000.0),
        BackwardDriftStep(lon=9.8000, lat=43.5500, timestamp=obs_t - timedelta(hours=6), uncertainty_radius_m=1000.0, u_wind_ms=0.0, v_wind_ms=0.0, drift_u_ms=0.0, drift_v_ms=0.0, cumulative_backward_distance_m=3000.0),
    ]

    shifted_results = search_along_drift_corridor(
        backward_steps=shifted_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        db_path=isolated_ais_db,
    )

    shifted_ulysse = next((v for v in shifted_results if v.mmsi == "228064900"), None)
    # Either Ulysse is excluded from the NE corridor, or its min_trajectory_distance_km is much larger
    if shifted_ulysse is not None:
        assert shifted_ulysse.min_trajectory_distance_km > 20.0
        assert shifted_ulysse.source_zone_intersection is False


def test_11_zero_fabrication_rule(sample_drift_steps, isolated_ais_db):
    """11. No AIS interpolation or dead-reckoning occurs."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    results = search_along_drift_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        db_path=isolated_ais_db,
    )

    ulysse = next((v for v in results if v.mmsi == "228064900"), None)
    assert ulysse is not None
    # We inserted exactly 2 points in isolated_ais_db for ULYSSE; point count must be exactly 2
    assert ulysse.position_count == 2
    assert len(ulysse.positions) == 2


def test_12_manual_selected_vessels_still_works():
    """12. Existing manual selected_vessels behavior still works in ExperimentRunner."""
    runner = ExperimentRunner()
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)

    # Manually pass custom selected vessel
    manual_vessel = {
        "mmsi": "111222333",
        "vessel_name": "MANUAL_TESTER",
        "positions": [
            {"timestamp": (obs_t - timedelta(hours=1)).isoformat(), "lat": 43.25, "lon": 9.45, "speed": 5.0, "heading": 180.0}
        ],
    }

    # Test scoring directly through _score_vessel
    features = runner._score_vessel(
        vessel_data=manual_vessel,
        source_lon=9.35,
        source_lat=43.18,
        source_radius_m=5000.0,
        observation_time=obs_t,
        backtrack_hours=6.0,
        backward_steps=[],
    )

    assert isinstance(features, VesselFeatures)
    assert features.mmsi == "111222333"
    assert features.vessel_name == "MANUAL_TESTER"
    assert features.evidence_consistency_score > 0.0
    assert "min_trajectory_distance_km" in features.as_dict()
    assert "source_type" in features.as_dict()


def test_13_provenance_fields_present_in_vessel_track_summary(sample_drift_steps, isolated_ais_db):
    """13. Existing AIS database / provenance behavior remains intact."""
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)
    results = search_along_drift_corridor(
        backward_steps=sample_drift_steps,
        observation_time=obs_t,
        backtrack_hours=6.0,
        db_path=isolated_ais_db,
    )

    ulysse = next((v for v in results if v.mmsi == "228064900"), None)
    assert ulysse is not None
    assert ulysse.source_type == "sqlite_ais"
    assert ulysse.provider_name == "ais_vessels.db"


def test_14_api_ais_search_switches_to_dynamic_when_backward_steps_supplied():
    """14. API /ais/search performs dynamic corridor search when backward_steps are provided, static otherwise."""
    client = TestClient(app)
    obs_t = datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc)

    # A. Dynamic call with backward_steps
    dynamic_payload = {
        "backward_steps": [
            {"step": 0, "lon": 9.4913, "lat": 43.2736, "uncertainty_radius_m": 500.0},
            {"step": 1, "lon": 9.3500, "lat": 43.1800, "uncertainty_radius_m": 5000.0},
        ],
        "observation_time": obs_t.isoformat(),
        "backtrack_hours": 6.0,
        "investigation_id": "test-dynamic",
    }
    resp_dyn = client.post("/api/experiment/ais/search", json=dynamic_payload)
    assert resp_dyn.status_code == 200
    data_dyn = resp_dyn.json()
    assert data_dyn["adapter_id"] == "SqliteAisAdapter"
    assert "search_bbox" in data_dyn
    # Dynamic search bbox derived from the steps
    assert data_dyn["search_bbox"]["west"] < 9.3500
    assert data_dyn["search_bbox"]["east"] > 9.4913

    # B. Static fallback call (without backward_steps)
    static_payload = {
        "west": 9.0,
        "south": 43.0,
        "east": 10.0,
        "north": 44.0,
        "start": (obs_t - timedelta(hours=6)).isoformat(),
        "end": obs_t.isoformat(),
        "investigation_id": "test-static",
    }
    resp_static = client.post("/api/experiment/ais/search", json=static_payload)
    assert resp_static.status_code == 200
    data_static = resp_static.json()
    assert data_static["search_bbox"]["west"] == 9.0
    assert data_static["search_bbox"]["south"] == 43.0
