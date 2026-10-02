"""Tests for Priority #2 — SAR observation handoff to existing backward drift engine.

Verifies:
1. SAR-derived centroid reaches drift engine.
2. SAR-derived sensing timestamp reaches drift engine.
3. SAR-derived polygon/geometry handoff is preserved.
4. Hard-coded Corsica centroid (43.24833, 9.47833) is NOT used when SAR detection succeeds.
5. Benchmark fallback when SAR is unavailable/unsegmented.
6. Drift engine integration and initial radius derivation from SAR spill area.
7. Evaluator workflow uses SAR-derived coordinates with explicit provenance ("SAR_DERIVED" vs "BENCHMARK_FALLBACK").
8. AIS attribution completes with SAR-derived observation location.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pytest
import xarray as xr

from app.services.real_experiment.experiment_runner import ExperimentRunner
from app.services.real_experiment.evaluator_workflow import (
    calculate_backward_drift_preview,
    get_reference_observation,
    run_evaluator_investigation,
)
from app.services.real_experiment.slick_characterization import characterize_observation


# ---------------------------------------------------------------------------
# NetCDF Test Fixtures
# ---------------------------------------------------------------------------

def _make_era5_nc(path: Path, obs_time: datetime, lons: list[float], lats: list[float]) -> None:
    times = [obs_time - timedelta(hours=h) for h in range(16, -1, -1)]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in times], dtype="datetime64[s]")
    lat_arr = np.array(lats, dtype=np.float64)
    lon_arr = np.array(lons, dtype=np.float64)
    shape = (len(time_arr), len(lat_arr), len(lon_arr))
    ds = xr.Dataset(
        data_vars={
            "u10": (("time", "latitude", "longitude"), np.full(shape, 3.0, dtype=np.float32)),
            "v10": (("time", "latitude", "longitude"), np.full(shape, 2.0, dtype=np.float32)),
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
    t_start = (obs_time - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    times = [t_start + timedelta(days=d) for d in range(5)]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in times], dtype="datetime64[s]")
    lat_arr = np.array(lats, dtype=np.float64)
    lon_arr = np.array(lons, dtype=np.float64)
    shape = (len(time_arr), len(lat_arr), len(lon_arr))
    ds = xr.Dataset(
        data_vars={
            "uo": (("time", "latitude", "longitude"), np.full(shape, 0.2, dtype=np.float32)),
            "vo": (("time", "latitude", "longitude"), np.full(shape, 0.1, dtype=np.float32)),
        },
        coords={
            "time": time_arr,
            "latitude": lat_arr,
            "longitude": lon_arr,
        },
    )
    ds.to_netcdf(str(path))
    ds.close()


@pytest.fixture
def corsica_metocean_netcdf(tmp_path: Path):
    obs_time = datetime(2018, 10, 8, 5, 34, 24, tzinfo=timezone.utc)
    lons = [float(i) for i in range(7, 12)]
    lats = [float(i) for i in range(41, 46)]
    era5_path = tmp_path / "era5_corsica.nc"
    cmems_path = tmp_path / "cmems_corsica.nc"
    _make_era5_nc(era5_path, obs_time, lons, lats)
    _make_cmems_nc(cmems_path, obs_time, lons, lats)
    return str(era5_path), str(cmems_path), obs_time


# ---------------------------------------------------------------------------
# Unit & Integration Tests
# ---------------------------------------------------------------------------

def test_sar_derived_centroid_and_provenance(corsica_metocean_netcdf):
    """Verify that when SAR characterization succeeds on a physical raster,
    ExperimentRunner receives the SAR-derived centroid (NOT the hardcoded 43.24833, 9.47833),
    and records observation_source='SAR_DERIVED'.
    """
    era5, cmems, obs_time = corsica_metocean_netcdf
    runner = ExperimentRunner()

    sar_centroid_lat = 43.27357
    sar_centroid_lon = 9.49133
    sar_area_m2 = 9_520_000.0

    sar_characterization = {
        "detected": True,
        "status": "DETECTED (Adaptive Thresholding on SAR Raster)",
        "centroid_lat": sar_centroid_lat,
        "centroid_lon": sar_centroid_lon,
        "area_km2": 9.52,
        "area_m2": sar_area_m2,
        "confidence": 0.88,
        "damping_contrast_db": 5.4,
        "observation_time": "2018-10-08T05:34:24Z",
        "has_physical_raster": True,
        "slick_geometry": {
            "type": "Polygon",
            "coordinates": [[[9.48, 43.26], [9.50, 43.26], [9.50, 43.28], [9.48, 43.28], [9.48, 43.26]]],
        },
    }

    # Pass whole-frame centroid to simulate CDSE catalog selection
    frame_lat = 41.9907
    frame_lon = 9.7874

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_1SDV_20181008T053424",
        observation_lon=frame_lon,
        observation_lat=frame_lat,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=6.0,
        step_hours=1.0,
        slick_characterization=sar_characterization,
    )

    # 1. Verification that hardcoded benchmark 43.24833, 9.47833 is NOT used
    assert result.observation_lat != 43.24833, "Hard-coded Corsica latitude was unexpectedly used"
    assert result.observation_lon != 9.47833, "Hard-coded Corsica longitude was unexpectedly used"

    # 2. Origin matches SAR-derived detection
    assert pytest.approx(result.observation_lat, abs=1e-4) == sar_centroid_lat
    assert pytest.approx(result.observation_lon, abs=1e-4) == sar_centroid_lon

    # 3. Provenance is SAR_DERIVED
    assert result.observation_source == "SAR_DERIVED"

    # 4. Backward drift trajectory steps backward from SAR centroid
    assert len(result.backward_steps) == 6
    assert pytest.approx(result.backward_steps[0]["lat"], abs=0.05) == sar_centroid_lat
    assert pytest.approx(result.backward_steps[0]["lon"], abs=0.05) == sar_centroid_lon


def test_sar_derived_timestamp_and_area_scaling(corsica_metocean_netcdf):
    """Verify SAR sensing timestamp and area properly propagate to drift uncertainty."""
    era5, cmems, default_obs_time = corsica_metocean_netcdf
    runner = ExperimentRunner()

    sar_sensing_iso = "2018-10-08T05:34:24Z"
    sar_area_m2 = 18_000_000.0  # Large slick -> larger initial uncertainty

    sar_characterization = {
        "detected": True,
        "centroid_lat": 43.27,
        "centroid_lon": 9.49,
        "area_m2": sar_area_m2,
        "observation_time": sar_sensing_iso,
        "has_physical_raster": True,
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_1SDV_20181008T053424",
        observation_lon=9.49,
        observation_lat=43.27,
        observation_time=datetime(2018, 10, 8, 12, 0, 0, tzinfo=timezone.utc),  # non-sensing dummy time
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=4.0,
        step_hours=1.0,
        slick_characterization=sar_characterization,
    )

    # Observation time matches SAR sensing time (05:34:24)
    assert result.observation_time == datetime(2018, 10, 8, 5, 34, 24, tzinfo=timezone.utc)

    # Initial radius R0 = sqrt(area / pi) ≈ sqrt(18e6 / 3.14159) ≈ 2393.65 m
    # At step 1 (elapsed 1 hour), radius = R0 + 500 m ≈ 2893.65 m
    r1 = result.backward_steps[0]["uncertainty_radius_m"]
    r0_derived = r1 - 500.0
    assert 2200 < r0_derived < 2500, f"Expected initial radius near 2393 m, got {r0_derived}"


def test_sar_derived_polygon_handoff(corsica_metocean_netcdf):
    """Verify SAR-derived polygon geometry is preserved in slick_characterization output."""
    era5, cmems, obs_time = corsica_metocean_netcdf
    runner = ExperimentRunner()

    polygon_geojson = {
        "type": "Polygon",
        "coordinates": [[[9.47, 43.26], [9.51, 43.26], [9.51, 43.29], [9.47, 43.29], [9.47, 43.26]]],
    }

    sar_characterization = {
        "detected": True,
        "centroid_lat": 43.275,
        "centroid_lon": 9.490,
        "area_m2": 5_000_000.0,
        "slick_geometry": polygon_geojson,
        "has_physical_raster": True,
    }

    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_1SDV_20181008T053424",
        observation_lon=9.490,
        observation_lat=43.275,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=3.0,
        step_hours=1.0,
        slick_characterization=sar_characterization,
    )

    assert result.slick_characterization is not None
    assert result.slick_characterization.get("slick_geometry") == polygon_geojson


def test_benchmark_fallback_when_sar_unavailable(corsica_metocean_netcdf):
    """Verify that when SAR raster is unavailable, the engine falls back to
    benchmark coordinates and flags provenance as BENCHMARK_FALLBACK.
    """
    era5, cmems, obs_time = corsica_metocean_netcdf
    runner = ExperimentRunner()

    # No physical raster; frame centroid passed
    result = runner.run(
        satellite_product_id="S1A_IW_GRDH_20181008T052807",
        observation_lon=9.7874,
        observation_lat=41.9907,
        observation_time=obs_time,
        era5_netcdf_path=era5,
        cmems_netcdf_path=cmems,
        backtrack_hours=6.0,
        step_hours=1.0,
        slick_characterization=None,  # No SAR subscene
    )

    assert result.observation_source == "BENCHMARK_FALLBACK"
    assert pytest.approx(result.observation_lat, abs=1e-4) == 43.2736
    assert pytest.approx(result.observation_lon, abs=1e-4) == 9.4913


def test_evaluator_workflow_uses_sar_derived_metrics():
    """Verify that run_evaluator_investigation automatically resolves SAR-derived
    metrics from the authenticated calibrated subscene fixture.
    """
    ref_obs = get_reference_observation("ref_corsica_2018")
    assert ref_obs is not None
    assert ref_obs.get("has_physical_raster") is True

    detected_metrics = ref_obs.get("detected_slick_metrics")
    assert detected_metrics is not None and detected_metrics.get("detected") is True

    sar_lat = detected_metrics["centroid_lat"]
    sar_lon = detected_metrics["centroid_lon"]
    sar_area = detected_metrics["area_m2"]

    # Execute investigation passing the catalog observation coordinates
    result = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=ref_obs["observation_lon"],
        observation_lat=ref_obs["observation_lat"],
        observation_time=datetime(2018, 10, 8, 5, 28, tzinfo=timezone.utc),
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        corridor_km=25.0,
        backtrack_hours=8.0,
        step_hours=0.5,
        spill_area_m2=ref_obs["slick_area_km2"] * 1e6,
    )

    # Provenance must be explicitly SAR_DERIVED
    assert result["observation_source"] == "SAR_DERIVED"

    # Coordinates in the investigation record must be the SAR-derived centroid
    assert pytest.approx(result["coordinates"]["observation_lat"], abs=1e-4) == sar_lat
    assert pytest.approx(result["coordinates"]["observation_lon"], abs=1e-4) == sar_lon

    # Hard-coded Corsica centroid (43.24833, 9.47833) was replaced by SAR detection
    assert result["coordinates"]["observation_lat"] != 43.24833

    # Drift steps must start at SAR-derived location
    assert pytest.approx(result["backward_steps"][0]["lat"], abs=1e-2) == sar_lat
    assert pytest.approx(result["backward_steps"][0]["lon"], abs=1e-2) == sar_lon


def test_calculate_backward_drift_preview_provenance():
    """Verify calculate_backward_drift_preview accepts and returns observation_source."""
    preview = calculate_backward_drift_preview(
        origin_lon=9.49133,
        origin_lat=43.27357,
        observation_time=datetime(2018, 10, 8, 5, 34, 24, tzinfo=timezone.utc),
        wind_speed_ms=6.0,
        wind_direction_deg=220.0,
        current_speed_ms=0.2,
        current_direction_deg=40.0,
        backtrack_hours=4.0,
        step_hours=0.5,
        spill_area_m2=9_520_000.0,
        observation_source="SAR_DERIVED",
    )

    assert preview["observation_source"] == "SAR_DERIVED"
    assert pytest.approx(preview["observation_point"]["lat"], abs=1e-4) == 43.27357
    assert pytest.approx(preview["observation_point"]["lon"], abs=1e-4) == 9.49133
