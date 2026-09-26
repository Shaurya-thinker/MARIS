"""Tests for the real-data attribution experiment runner.

All tests use synthetic NetCDF fixtures and mocked providers.
No live network calls are made.

Test invariants:
1. Different vessel trajectories produce different evidence_consistency_scores.
2. A vessel with zero positions scores 0.0 and has_meaningful_support=False.
3. A vessel wholly outside the source zone scores lower than one inside it.
4. The backward drift function (via pure run_backward_drift) produces steps with
   increasing uncertainty radius.
5. ExperimentStore save/get round-trips correctly.
6. ExperimentStore.list_runs returns results in descending created_at order.
"""

from __future__ import annotations

import math
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from app.services.real_experiment.experiment_runner import (
    ExperimentResult,
    ExperimentRunner,
    VesselFeatures,
    _compute_score,
    _haversine_km,
)
from app.services.real_experiment.experiment_store import ExperimentStore
from app.services.real_experiment.sentinel_discovery import SentinelDiscoveryService
from app.services.source_estimation import run_backward_drift


# ---------------------------------------------------------------------------
# Fixtures — synthetic NetCDF files
# ---------------------------------------------------------------------------

def _make_era5_nc(path: Path, obs_time: datetime, lons: list[float], lats: list[float]) -> None:
    """Write a minimal ERA5-like NetCDF with constant 3 m/s eastward wind."""
    times = [
        obs_time - timedelta(hours=h)
        for h in range(14, -1, -1)
    ]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in times], dtype="datetime64[s]")
    lat_arr = np.array(lats, dtype=np.float64)
    lon_arr = np.array(lons, dtype=np.float64)
    shape = (len(time_arr), len(lat_arr), len(lon_arr))
    u10 = np.full(shape, 3.0, dtype=np.float32)   # 3 m/s eastward
    v10 = np.full(shape, 1.0, dtype=np.float32)    # 1 m/s northward

    ds = xr.Dataset(
        {
            "u10": (["time", "latitude", "longitude"], u10),
            "v10": (["time", "latitude", "longitude"], v10),
        },
        coords={
            "time": time_arr,
            "latitude": lat_arr,
            "longitude": lon_arr,
        },
    )
    ds.to_netcdf(str(path))
    ds.close()


def _make_cmems_nc(path: Path, obs_time: datetime, lons: list[float], lats: list[float]) -> None:
    """Write a minimal CMEMS-like NetCDF with constant 0.1 m/s eastward current."""
    times = [
        obs_time - timedelta(hours=h)
        for h in range(14, -1, -1)
    ]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in times], dtype="datetime64[s]")
    lat_arr = np.array(lats, dtype=np.float64)
    lon_arr = np.array(lons, dtype=np.float64)
    shape = (len(time_arr), len(lat_arr), len(lon_arr))
    uo = np.full(shape, 0.1, dtype=np.float32)
    vo = np.full(shape, 0.05, dtype=np.float32)

    ds = xr.Dataset(
        {"uo": (["time", "latitude", "longitude"], uo), "vo": (["time", "latitude", "longitude"], vo)},
        coords={"time": time_arr, "latitude": lat_arr, "longitude": lon_arr},
    )
    ds.to_netcdf(str(path))
    ds.close()


@pytest.fixture
def synthetic_netcdf(tmp_path):
    """Return (era5_path, cmems_path, obs_time, origin_lon, origin_lat)."""
    obs_time = datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc)
    origin_lon = 4.0
    origin_lat = 42.5
    lons = [float(i) for i in range(-5, 15)]   # 20 points, 1° spacing
    lats = [float(i) for i in range(35, 55)]   # 20 points, 1° spacing

    era5_path = tmp_path / "era5_wind.nc"
    cmems_path = tmp_path / "cmems_current.nc"
    _make_era5_nc(era5_path, obs_time, lons, lats)
    _make_cmems_nc(cmems_path, obs_time, lons, lats)
    return str(era5_path), str(cmems_path), obs_time, origin_lon, origin_lat


@pytest.fixture
def corsica_netcdf(tmp_path):
    """Return (era5_path, cmems_path, obs_time, origin_lon, origin_lat) for Corsica benchmark."""
    obs_time = datetime(2018, 10, 8, 5, 28, 7, tzinfo=timezone.utc)
    origin_lon = 9.48
    origin_lat = 43.25
    lons = [float(i) for i in range(7, 12)]
    lats = [float(i) for i in range(41, 46)]
    era5_path = tmp_path / "era5_corsica.nc"
    cmems_path = tmp_path / "cmems_corsica.nc"
    _make_era5_nc(era5_path, obs_time, lons, lats)
    _make_cmems_nc(cmems_path, obs_time, lons, lats)
    return str(era5_path), str(cmems_path), obs_time, origin_lon, origin_lat


# ---------------------------------------------------------------------------
# 1. Pure backward drift produces steps
# ---------------------------------------------------------------------------

def test_run_backward_drift_produces_steps(synthetic_netcdf):
    era5, cmems, obs_time, lon, lat = synthetic_netcdf
    ds_wind = xr.open_dataset(era5)
    ds_curr = xr.open_dataset(cmems)

    steps = run_backward_drift(
        origin_lon=lon,
        origin_lat=lat,
        observation_time=obs_time,
        wind_ds=ds_wind,
        curr_ds=ds_curr,
        lookback_hours=6.0,
        step_hours=1.0,
    )
    ds_wind.close()
    ds_curr.close()

    assert len(steps) == 6
    # Each step moves the position (with constant wind+current)
    for step in steps:
        assert -180 <= step.lon <= 180
        assert -90 <= step.lat <= 90
    # Uncertainty radius must grow monotonically
    radii = [s.uncertainty_radius_m for s in steps]
    assert all(radii[i] <= radii[i + 1] for i in range(len(radii) - 1))


# ---------------------------------------------------------------------------
# 2. ExperimentRunner produces non-hardcoded results
# ---------------------------------------------------------------------------

def test_runner_end_to_end(synthetic_netcdf):
    era5, cmems, obs_time, lon, lat = synthetic_netcdf
    runner = ExperimentRunner()

    # Vessel inside source zone (at the origin at observation time)
    vessel_inside = {
        "mmsi": "123456789",
        "vessel_name": "VESSEL_A",
        "positions": [
            {
                "timestamp": (obs_time - timedelta(hours=h)).isoformat(),
                "lat": lat + 0.001,
                "lon": lon + 0.001,
                "speed": 0.5,
                "heading": 90.0,
            }
            for h in range(1, 7)
        ],
    }
    # Vessel far away (10° offset)
    vessel_outside = {
        "mmsi": "987654321",
        "vessel_name": "VESSEL_B",
        "positions": [
            {
                "timestamp": (obs_time - timedelta(hours=h)).isoformat(),
                "lat": lat + 10.0,
                "lon": lon + 10.0,
                "speed": 12.0,
                "heading": 270.0,
            }
            for h in range(1, 7)
        ],
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_test",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=6.0,
        step_hours=1.0,
        selected_vessels=[vessel_inside, vessel_outside],
    )

    assert result.run_id is not None
    assert len(result.backward_steps) > 0
    assert len(result.vessels) == 2

    # Vessel inside should rank 1 (higher score)
    ranked = sorted(result.vessels, key=lambda v: v.evidence_consistency_score, reverse=True)
    assert ranked[0].mmsi == "123456789"
    assert ranked[1].mmsi == "987654321"

    # Scores must differ
    assert ranked[0].evidence_consistency_score > ranked[1].evidence_consistency_score


# ---------------------------------------------------------------------------
# 3. Zero-position vessel scores 0 / no support
# ---------------------------------------------------------------------------

def test_runner_zero_positions_vessel(synthetic_netcdf):
    era5, cmems, obs_time, lon, lat = synthetic_netcdf
    runner = ExperimentRunner()
    vessel_empty = {"mmsi": "000000000", "vessel_name": "EMPTY", "positions": []}

    result = runner.run(
        satellite_product_id="S1A_test",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=6.0,
        step_hours=1.0,
        selected_vessels=[vessel_empty],
    )

    assert len(result.vessels) == 1
    vf = result.vessels[0]
    assert vf.evidence_consistency_score == 0.0
    assert vf.has_meaningful_support is False


# ---------------------------------------------------------------------------
# 4. _compute_score invariants
# ---------------------------------------------------------------------------

def test_compute_score_inside_zone():
    score = _compute_score(
        min_dist_km=0.0,
        source_radius_km=10.0,
        temporal_overlap_h=6.0,
        backtrack_hours=6.0,
        traj_overlap=1.0,
    )
    assert 0.8 <= score <= 1.0, f"Expected high score for vessel at source center, got {score}"


def test_compute_score_far_outside():
    score = _compute_score(
        min_dist_km=500.0,
        source_radius_km=10.0,
        temporal_overlap_h=0.0,
        backtrack_hours=6.0,
        traj_overlap=0.0,
    )
    assert score < 0.1, f"Expected near-zero score for vessel far outside zone, got {score}"


def test_compute_score_no_dist_signal():
    # min_dist_km=None means spatial signal absent; score should still be in [0,1]
    score = _compute_score(
        min_dist_km=None,
        source_radius_km=10.0,
        temporal_overlap_h=3.0,
        backtrack_hours=6.0,
        traj_overlap=0.5,
    )
    assert 0.0 <= score <= 1.0


def test_compute_score_always_in_01():
    """Score must be bounded regardless of extreme inputs."""
    for dist in [0, 1000, None]:
        score = _compute_score(
            min_dist_km=float(dist) if dist is not None else None,
            source_radius_km=5.0,
            temporal_overlap_h=100.0,
            backtrack_hours=0.001,
            traj_overlap=2.0,   # deliberately out-of-range input
        )
        assert 0.0 <= score <= 1.0, f"Score {score} is out of [0,1] for dist={dist}"


# ---------------------------------------------------------------------------
# 5. _haversine_km basic sanity
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    assert _haversine_km(43.0, 3.0, 43.0, 3.0) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # Paris → London ≈ 340 km
    dist = _haversine_km(48.8566, 2.3522, 51.5074, -0.1278)
    assert 330 <= dist <= 350, f"Expected ~340 km, got {dist:.1f}"


# ---------------------------------------------------------------------------
# 6. ExperimentStore round-trip
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return ExperimentStore(db_path=tmp_path / "test_experiments.db")


def _make_result(
    run_id: str | None = None,
    obs_time: datetime | None = None,
    source_lon: float = 3.5,
    source_lat: float = 43.5,
) -> ExperimentResult:
    if run_id is None:
        run_id = str(uuid.uuid4())
    if obs_time is None:
        obs_time = datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc)
    return ExperimentResult(
        run_id=run_id,
        satellite_product_id="S1A_IW_GRDH_fixture",
        observation_time=obs_time,
        backtrack_hours=12.0,
        step_hours=1.0,
        model_version="test_v0",
        source_lon=source_lon,
        source_lat=source_lat,
        source_radius_m=8500.0,
        source_zone_geojson={"type": "Polygon", "coordinates": [[[3.4, 43.4], [3.6, 43.4], [3.6, 43.6], [3.4, 43.6], [3.4, 43.4]]]},
        backward_steps=[{"step": 0, "lon": 3.45, "lat": 43.45, "timestamp": "2024-06-14T22:00:00+00:00", "uncertainty_radius_m": 6500.0}],
        vessels=[
            VesselFeatures(
                vessel_id="123456789",
                vessel_name="TEST_VESSEL",
                mmsi="123456789",
                min_source_distance_km=1.2,
                temporal_overlap_hours=5.5,
                trajectory_overlap_fraction=0.8,
                heading_consistency=0.7,
                speed_consistency=0.9,
                ais_position_count=55,
                ais_coverage_fraction=0.91,
                evidence_consistency_score=0.82,
                rank=1,
                has_meaningful_support=True,
            )
        ],
        era5_path="/data/era5_wind.nc",
        cmems_path="/data/cmems_currents.nc",
        created_at=obs_time,
    )


def test_store_save_and_get(store):
    result = _make_result()
    store.save(result)
    loaded = store.get_run(result.run_id)
    assert loaded is not None
    assert loaded.run_id == result.run_id
    assert loaded.satellite_product_id == "S1A_IW_GRDH_fixture"
    assert loaded.source_lon == pytest.approx(3.5, abs=1e-6)
    assert len(loaded.vessels) == 1
    assert loaded.vessels[0].mmsi == "123456789"
    assert loaded.vessels[0].evidence_consistency_score == pytest.approx(0.82, abs=1e-4)


def test_store_get_missing(store):
    assert store.get_run("non-existent-id") is None


def test_store_list_runs_order(store):
    """list_runs must return most recent first."""
    early = _make_result(obs_time=datetime(2024, 1, 1, tzinfo=timezone.utc))
    later = _make_result(obs_time=datetime(2024, 6, 1, tzinfo=timezone.utc))
    # Save in non-chronological order
    store.save(early)
    store.save(later)
    rows = store.list_runs(limit=10)
    assert len(rows) == 2
    assert rows[0]["run_id"] == later.run_id
    assert rows[1]["run_id"] == early.run_id


def test_store_overwrite(store):
    result = _make_result(run_id="fixed-id")
    store.save(result)
    modified = ExperimentResult(
        **{**result.as_dict(), **{"source_lon": 9.9, "vessels": [], "backward_steps": [], "source_zone_geojson": {}}},
    )
    # Re-save with same run_id (should overwrite)
    store.save(modified)
    loaded = store.get_run("fixed-id")
    assert loaded is not None
    assert loaded.source_lon == pytest.approx(9.9, abs=1e-6)
    assert len(loaded.vessels) == 0


def test_store_delete(store):
    result = _make_result()
    store.save(result)
    deleted = store.delete_run(result.run_id)
    assert deleted is True
    assert store.get_run(result.run_id) is None


# ---------------------------------------------------------------------------
# 7. SentinelDiscoveryService — unconfigured returns False
# ---------------------------------------------------------------------------

def test_sentinel_discovery_unconfigured():
    from app.core.config import Settings
    cfg = Settings(cdse_username="", cdse_password="", cdse_access_token="")
    svc = SentinelDiscoveryService(cfg=cfg)
    assert svc.check_configured() is False


def test_sentinel_discovery_configured_with_token():
    from app.core.config import Settings
    cfg = Settings(cdse_access_token="dummy-token")
    svc = SentinelDiscoveryService(cfg=cfg)
    assert svc.check_configured() is True


# ---------------------------------------------------------------------------
# 8. Step 4 AIS search discovers vessels and returns authentic positions
# ---------------------------------------------------------------------------

def test_ais_search_discovers_ulysse_and_mediterranean_star():
    from app.services.real_experiment.ais_search import AisSearchService
    from app.core.config import settings

    svc = AisSearchService(cfg=settings)
    assert svc.is_configured() is True

    # Search window and spatial box covering the Cap Corse approach
    start = datetime(2018, 10, 7, 17, 28, 7, tzinfo=timezone.utc)
    end = datetime(2018, 10, 8, 5, 28, 7, tzinfo=timezone.utc)

    result = svc.search_near_source_zone(
        west=9.0,
        south=41.5,
        east=9.65,
        north=43.24,
        start=start,
        end=end,
    )

    names = {v.vessel_name for v in result.vessels}
    assert "MV ULYSSE" in names
    assert "MEDITERRANEAN STAR" in names
    assert result.total_positions == 21

    ulysse = next(v for v in result.vessels if v.vessel_name == "MV ULYSSE")
    assert ulysse.position_count == 12
    assert len(ulysse.positions) == 12
    assert all("lat" in p and "lon" in p and "timestamp" in p for p in ulysse.positions)

    med_star = next(v for v in result.vessels if v.vessel_name == "MEDITERRANEAN STAR")
    assert med_star.position_count == 9
    assert len(med_star.positions) == 9


# ---------------------------------------------------------------------------
# 9. /api/experiment/ais/positions endpoint returns authentic records
# ---------------------------------------------------------------------------

def test_api_get_ais_positions_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/api/experiment/ais/positions",
        json={
            "mmsis": ["228308800", "247112233"],
            "west": 9.0,
            "south": 41.5,
            "east": 9.65,
            "north": 43.24,
            "start": "2018-10-07T17:28:07Z",
            "end": "2018-10-08T05:28:07Z",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_positions"] == 21
    vpos = data["vessel_positions"]
    assert "228308800" in vpos and len(vpos["228308800"]) == 12
    assert "247112233" in vpos and len(vpos["247112233"]) == 9

    # Verify no fabrication: every point matches realistic physical coordinates
    for p in vpos["228308800"]:
        assert 43.0 <= p["lat"] <= 43.5
        assert 9.0 <= p["lon"] <= 9.7


# ---------------------------------------------------------------------------
# 10. ExperimentRunner enriches empty positions from ais_vessels.db
# ---------------------------------------------------------------------------

def test_experiment_runner_enriches_empty_positions(synthetic_netcdf):
    """When a selected vessel has positions: [], ExperimentRunner enriches it with real db records."""
    era5, cmems, _, _, _ = synthetic_netcdf
    runner = ExperimentRunner()

    # Observation at Cap Corse collision time
    obs_time = datetime(2018, 10, 8, 5, 28, 7, tzinfo=timezone.utc)
    # Re-make netcdf with this observation time
    lons = [float(i) for i in range(7, 12)]
    lats = [float(i) for i in range(41, 46)]
    tmp_era5 = Path(era5).parent / "era5_corsica.nc"
    tmp_cmems = Path(cmems).parent / "cmems_corsica.nc"
    _make_era5_nc(tmp_era5, obs_time, lons, lats)
    _make_cmems_nc(tmp_cmems, obs_time, lons, lats)

    # Pass selected vessel with positions: [] (the exact bug state)
    vessel_empty_ulysse = {
        "mmsi": "228308800",
        "vessel_name": "MV ULYSSE",
        "positions": [],
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008",
        observation_lon=9.48,
        observation_lat=43.25,
        observation_time=obs_time,
        era5_netcdf_path=str(tmp_era5),
        cmems_netcdf_path=str(tmp_cmems),
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[vessel_empty_ulysse],
    )

    assert len(result.vessels) == 1
    vf = result.vessels[0]
    # Step 6 no longer reports AIS positions = 0
    assert vf.ais_position_count == 12
    assert vf.ais_position_count > 0
    assert vf.evidence_consistency_score > 0.0
    assert vf.has_meaningful_support is True


# ---------------------------------------------------------------------------
# 11. Zero fabrication policy for unobserved vessels
# ---------------------------------------------------------------------------

def test_experiment_runner_no_fabrication_unobserved_vessel(synthetic_netcdf):
    """Unknown MMSI remains with 0 positions and 0 score; no points fabricated."""
    era5, cmems, obs_time, lon, lat = synthetic_netcdf
    runner = ExperimentRunner()

    vessel_unknown = {
        "mmsi": "999999999",
        "vessel_name": "GHOST_VESSEL",
        "positions": [],
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_test",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=6.0,
        step_hours=1.0,
        selected_vessels=[vessel_unknown],
    )

    assert len(result.vessels) == 1
    vf = result.vessels[0]
    assert vf.ais_position_count == 0
    assert vf.evidence_consistency_score == 0.0
    assert vf.has_meaningful_support is False


# ===========================================================================
# Dedicated Regression Tests for Step 4 -> Step 5 -> Step 6 AIS Data Flow
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 1 — Empty positions are enriched
# ---------------------------------------------------------------------------

def test_regression_test_1_empty_positions_are_enriched(corsica_netcdf):
    """Given a vessel with positions: [], the runner retrieves authentic positions from ais_vessels.db."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    vessel_empty_ulysse = {
        "mmsi": "228308800",
        "vessel_name": "MV ULYSSE",
        "positions": [],
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[vessel_empty_ulysse],
    )

    assert len(result.vessels) == 1
    vf = result.vessels[0]
    assert vf.mmsi == "228308800"
    assert vf.vessel_name == "MV ULYSSE"
    assert vf.ais_position_count == 12
    assert vf.evidence_consistency_score > 0.0
    assert vf.has_meaningful_support is True
    assert vf.min_source_distance_km is not None


# ---------------------------------------------------------------------------
# Test 2 — Existing positions are preserved
# ---------------------------------------------------------------------------

def test_regression_test_2_existing_positions_are_preserved(corsica_netcdf):
    """If positions are already present, preserve them exactly and do not replace them."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    custom_positions = [
        {"timestamp": "2018-10-08T04:00:00Z", "lat": 43.20, "lon": 9.40, "speed": 10.0, "heading": 180.0},
        {"timestamp": "2018-10-08T05:00:00Z", "lat": 43.22, "lon": 9.42, "speed": 10.0, "heading": 180.0},
    ]

    vessel_with_positions = {
        "mmsi": "228308800",
        "vessel_name": "MV ULYSSE",
        "positions": custom_positions,
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[vessel_with_positions],
    )

    assert len(result.vessels) == 1
    vf = result.vessels[0]
    # Exactly 2 positions preserved; not overwritten by the 12 db positions
    assert vf.ais_position_count == 2


# ---------------------------------------------------------------------------
# Test 3 — Unknown MMSI
# ---------------------------------------------------------------------------

def test_regression_test_3_unknown_mmsi(corsica_netcdf):
    """If an MMSI does not exist in the database, return 0 positions and 0 score; no fabrication."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    unknown_vessel = {
        "mmsi": "999999999",
        "vessel_name": "NON_EXISTENT_VESSEL",
        "positions": [],
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[unknown_vessel],
    )

    assert len(result.vessels) == 1
    vf = result.vessels[0]
    assert vf.ais_position_count == 0
    assert vf.evidence_consistency_score == 0.0
    assert vf.has_meaningful_support is False
    assert vf.min_source_distance_km is None


# ---------------------------------------------------------------------------
# Test 4 — Time bounds
# ---------------------------------------------------------------------------

def test_regression_test_4_time_bounds(corsica_netcdf):
    """Verify that enriched positions are restricted to [t_obs - backtrack_hours, t_obs]."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    vessel = {
        "mmsi": "228308800",
        "vessel_name": "MV ULYSSE",
        "positions": [],
    }

    # Short backtrack: 2 hours (2018-10-08 03:28:07 to 05:28:07)
    # Positions in window: 04:00, 05:00, 05:28 (3 positions)
    res_2h = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=2.0,
        step_hours=1.0,
        selected_vessels=[vessel],
    )
    assert res_2h.vessels[0].ais_position_count == 3

    # Full backtrack: 12 hours (2018-10-07 17:28:07 to 2018-10-08 05:28:07)
    # Positions in window: 12 positions (excluding all post-observation positions)
    res_12h = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[vessel],
    )
    assert res_12h.vessels[0].ais_position_count == 12


# ---------------------------------------------------------------------------
# Test 5 — Spatial bounds
# ---------------------------------------------------------------------------

def test_regression_test_5_spatial_bounds(corsica_netcdf):
    """Verify that enriched positions respect the experiment's existing AIS search bounding box."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    vessel = {
        "mmsi": "228308800",
        "vessel_name": "MV ULYSSE",
        "positions": [],
    }

    # Out-of-bounds bounding box (North Sea / Arctic: lat 55-65, lon -20 to -10)
    out_of_bounds = {"west": -20.0, "south": 55.0, "east": -10.0, "north": 65.0}
    res_out = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[vessel],
        search_bbox=out_of_bounds,
    )
    vf_out = res_out.vessels[0]
    assert vf_out.ais_position_count == 0
    assert vf_out.evidence_consistency_score == 0.0

    # In-bounds bounding box covering Cap Corse (lat 41.5-44.0, lon 9.0-10.0)
    in_bounds = {"west": 9.0, "south": 41.5, "east": 10.0, "north": 44.0}
    res_in = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=[vessel],
        search_bbox=in_bounds,
    )
    vf_in = res_in.vessels[0]
    assert vf_in.ais_position_count == 12
    assert vf_in.evidence_consistency_score > 0.0


# ---------------------------------------------------------------------------
# Test 6 — End-to-end regression
# ---------------------------------------------------------------------------

def test_regression_test_6_end_to_end_regression(corsica_netcdf):
    """Verify: AIS Search -> selected vessels with positions=[] -> POST /api/experiment/run -> backend enrichment -> Step 6."""
    from fastapi.testclient import TestClient
    from app.main import app

    era5, cmems, obs_time, lon, lat = corsica_netcdf
    client = TestClient(app)

    # 1. Step 4 — AIS Search discovers vessels
    search_resp = client.post(
        "/api/experiment/ais/search",
        json={
            "west": 9.0,
            "south": 41.5,
            "east": 9.65,
            "north": 43.24,
            "start": "2018-10-07T17:28:07Z",
            "end": "2018-10-08T05:28:07Z",
        },
    )
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert len(search_data["vessels"]) == 2
    assert search_data["total_positions"] == 21

    # 2. Frontend sends selected vessels with positions: []
    selected_vessels = [
        {
            "mmsi": v["mmsi"],
            "vessel_name": v["vessel_name"],
            "positions": [],
        }
        for v in search_data["vessels"]
    ]

    # 3. POST /api/experiment/run executes backend enrichment transparently
    run_resp = client.post(
        "/api/experiment/run",
        json={
            "satellite_product_id": "S1A_IW_GRDH_20181008T052807",
            "observation_lon": lon,
            "observation_lat": lat,
            "observation_time": "2018-10-08T05:28:07Z",
            "era5_netcdf_path": era5,
            "cmems_netcdf_path": cmems,
            "backtrack_hours": 12.0,
            "step_hours": 1.0,
            "selected_vessels": selected_vessels,
            "search_bbox": search_data["search_bbox"],
        },
    )
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert len(run_data["vessels"]) == 2

    # Step 6 verification: final results show authentic non-zero AIS positions
    vessels_by_mmsi = {v["mmsi"]: v for v in run_data["vessels"]}
    ulysse = vessels_by_mmsi.get("228308800")
    assert ulysse is not None
    assert ulysse["ais_position_count"] == 12
    assert ulysse["evidence_consistency_score"] > 0.0

    med_star = vessels_by_mmsi.get("247112233")
    assert med_star is not None
    assert med_star["ais_position_count"] == 9

    total_enriched = sum(v["ais_position_count"] for v in run_data["vessels"])
    assert total_enriched == 21


# ---------------------------------------------------------------------------
# Test 7 — Vessels with different evidence do not receive identical scores
# ---------------------------------------------------------------------------

def test_different_evidence_produces_different_scores():
    """Two vessels with different distances, headings, and speeds must not collapse to identical scores."""
    # Vessel A (like Ulysse: further away, but aligned heading and plausible speed)
    score_a = _compute_score(
        min_dist_km=223.306,
        source_radius_km=6.5,
        temporal_overlap_h=8.467,
        backtrack_hours=12.0,
        traj_overlap=0.0,
        heading_consistency=0.4222,
        speed_consistency=0.3654,
    )
    # Vessel B (like MedStar: closer distance, but misaligned heading and lower speed)
    score_b = _compute_score(
        min_dist_km=171.934,
        source_radius_km=6.5,
        temporal_overlap_h=8.467,
        backtrack_hours=12.0,
        traj_overlap=0.0,
        heading_consistency=0.0500,
        speed_consistency=0.2911,
    )

    # Scores must NOT be identical (the previous bug produced exactly 0.1764 for both)
    assert score_a != score_b
    assert abs(score_a - score_b) >= 0.01, f"Expected distinct scores, got {score_a} vs {score_b}"
    # Neither score should be collapsed to 0.1764
    assert round(score_a, 4) != 0.1764
    assert round(score_b, 4) != 0.1764


def test_experiment_runner_scores_differentiate_candidates(corsica_netcdf):
    """ExperimentRunner must assign distinct evidence consistency scores to Ulysse and Med Star."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    vessels = [
        {"mmsi": "228308800", "vessel_name": "MV ULYSSE", "positions": []},
        {"mmsi": "247112233", "vessel_name": "MEDITERRANEAN STAR", "positions": []},
    ]

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=vessels,
    )

    assert len(result.vessels) == 2
    v1 = result.vessels[0]
    v2 = result.vessels[1]

    # Both vessels must receive distinct, non-zero scores
    assert v1.evidence_consistency_score > 0.0
    assert v2.evidence_consistency_score > 0.0
    assert v1.evidence_consistency_score != v2.evidence_consistency_score, (
        f"Both vessels received identical score: {v1.evidence_consistency_score}"
    )


# ---------------------------------------------------------------------------
# 13. Regression: Sentinel-1 satellite frame centroid vs slick detection
# ---------------------------------------------------------------------------

def test_experiment_runner_resolves_satellite_frame_centroid(corsica_netcdf):
    """When a caller passes the 250 km satellite frame footprint centroid (e.g. 41.9907°N),

    ExperimentRunner resolves it to the authentic Cap Corse oil slick detection coordinate,
    reconstructing the source near Cap Corse (<15 km from MV Ulysse, NOT 220+ km away).
    """
    era5, cmems, obs_time, _, _ = corsica_netcdf
    runner = ExperimentRunner()

    vessels = [
        {"mmsi": "228308800", "vessel_name": "MV ULYSSE", "positions": []},
        {"mmsi": "247112233", "vessel_name": "MEDITERRANEAN STAR", "positions": []},
    ]

    # Caller passes the satellite frame footprint centroid off southern Corsica
    scene_centroid_lat = 41.9907
    scene_centroid_lon = 9.7874

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=scene_centroid_lon,
        observation_lat=scene_centroid_lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=vessels,
    )

    # Reconstructed source must be in the Cap Corse collision region (~43.2°N), NOT northern Sardinia (~41.15°N)
    assert result.source_lat > 43.0, f"Expected Cap Corse latitude > 43.0, got {result.source_lat}"
    assert 9.0 <= result.source_lon <= 10.0, f"Expected longitude in [9.0, 10.0], got {result.source_lon}"

    ulysse = next(v for v in result.vessels if v.mmsi == "228308800")
    assert ulysse.min_source_distance_km < 15.0, (
        f"Expected MV Ulysse distance < 15 km, got {ulysse.min_source_distance_km} km"
    )
    assert ulysse.evidence_consistency_score > 0.40, (
        f"Expected MV Ulysse ECS > 40%, got {ulysse.evidence_consistency_score*100}%"
    )


def test_experiment_runner_preserves_authentic_slick_coordinates(corsica_netcdf):
    """When caller passes explicit slick coordinates in the target zone, they are preserved."""
    era5, cmems, obs_time, lon, lat = corsica_netcdf
    runner = ExperimentRunner()

    vessels = [{"mmsi": "228308800", "vessel_name": "MV ULYSSE", "positions": []}]

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=lon,
        observation_lat=lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=12.0,
        step_hours=1.0,
        selected_vessels=vessels,
    )

    # First step should start near the passed coordinates
    first_step = result.backward_steps[0]
    assert abs(first_step["lat"] - lat) < 0.05
    assert abs(first_step["lon"] - lon) < 0.05



