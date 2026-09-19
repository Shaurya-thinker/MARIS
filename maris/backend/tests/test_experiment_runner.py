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
    origin_lon = 3.5
    origin_lat = 43.5
    lons = [float(i) for i in range(-5, 15)]   # 20 points, 1° spacing
    lats = [float(i) for i in range(35, 55)]   # 20 points, 1° spacing

    era5_path = tmp_path / "era5_wind.nc"
    cmems_path = tmp_path / "cmems_current.nc"
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
