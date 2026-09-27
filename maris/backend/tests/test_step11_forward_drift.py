"""Tests for MARIS Step 11 — Forward Drift Prediction & Visualization.

Verifies:
1. Forward integration starts at observation coordinate and steps forward in time.
2. Forward timestamps are strictly future (t0 + 1h, t0 + 2h, ...).
3. Metocean forcing (ERA5 wind + CMEMS currents) drives trajectory advection.
4. Requested horizon exceeding available environmental forcing fails closed.
5. Zero or negative prediction duration raises ForwardPredictionError.
6. Forward and backward trajectories remain strictly distinct.
7. API endpoint POST /api/experiment/drift/forward-predict returns valid schema.
8. ExperimentRunner integrates forward prediction when forward_prediction_hours > 0.
9. ExperimentStore persists forward_prediction_json with backward compatibility for NULLs.
10. Report generator produces forward_drift_prediction in JSON data and renders PDF.
"""

from __future__ import annotations

import math
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from starlette.testclient import TestClient

from app.main import app
from app.services.real_experiment.experiment_runner import (
    ExperimentResult,
    ExperimentRunner,
    VesselFeatures,
)
from app.services.real_experiment.experiment_store import ExperimentStore
from app.services.real_experiment.forward_prediction import (
    ForwardPredictionError,
    predict_forward_drift,
)
from app.services.report_generator import (
    build_scientific_report_data,
    render_scientific_report_pdf,
)


def _make_forward_netcdf_fixtures(tmp_path: Path, obs_time: datetime, forward_hours: int = 14):
    """Create ERA5 and CMEMS NetCDF files covering past and future hours around obs_time."""
    # From obs_time - 14h to obs_time + forward_hours
    times = [
        obs_time + timedelta(hours=h)
        for h in range(-14, forward_hours + 2)
    ]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in times], dtype="datetime64[s]")
    lat_arr = np.array([float(i) for i in range(35, 50)], dtype=np.float64)  # 35.0 to 49.0
    lon_arr = np.array([float(i) for i in range(-5, 15)], dtype=np.float64)  # -5.0 to 14.0

    shape = (len(time_arr), len(lat_arr), len(lon_arr))
    # Eastward 4 m/s, Northward 2 m/s
    u10 = np.full(shape, 4.0, dtype=np.float32)
    v10 = np.full(shape, 2.0, dtype=np.float32)

    era5_ds = xr.Dataset(
        {
            "u10": (["time", "latitude", "longitude"], u10),
            "v10": (["time", "latitude", "longitude"], v10),
        },
        coords={"time": time_arr, "latitude": lat_arr, "longitude": lon_arr},
    )
    era5_path = tmp_path / "era5_forward.nc"
    era5_ds.to_netcdf(str(era5_path))
    era5_ds.close()

    # Ocean current: Eastward 0.15 m/s, Northward 0.08 m/s
    uo = np.full(shape, 0.15, dtype=np.float32)
    vo = np.full(shape, 0.08, dtype=np.float32)

    cmems_ds = xr.Dataset(
        {
            "uo": (["time", "latitude", "longitude"], uo),
            "vo": (["time", "latitude", "longitude"], vo),
        },
        coords={"time": time_arr, "latitude": lat_arr, "longitude": lon_arr},
    )
    cmems_path = tmp_path / "cmems_forward.nc"
    cmems_ds.to_netcdf(str(cmems_path))
    cmems_ds.close()

    return str(era5_path), str(cmems_path)


class TestStep11ForwardDriftPrediction:
    def test_forward_drift_future_timestamps_and_displacement(self, tmp_path):
        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=12)

        origin_lon = 4.0
        origin_lat = 42.5

        result = predict_forward_drift(
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=obs_time,
            era5_netcdf_path=era5_path,
            cmems_netcdf_path=cmems_path,
            prediction_hours=6.0,
            step_hours=1.0,
        )

        assert result["model_version"] == "leeway_euler_v1"
        assert result["prediction_hours"] == 6.0
        assert result["origin_lon"] == origin_lon
        assert result["origin_lat"] == origin_lat
        assert len(result["steps"]) == 6

        # Step 1 must be t0 + 1h
        t1 = datetime.fromisoformat(result["steps"][0]["timestamp"])
        assert t1 == obs_time + timedelta(hours=1)

        # Step 6 must be t0 + 6h
        t6 = datetime.fromisoformat(result["steps"][5]["timestamp"])
        assert t6 == obs_time + timedelta(hours=6)

        # Displaced northeastward because u_wind > 0 and v_wind > 0
        final_lon = result["final_lon"]
        final_lat = result["final_lat"]
        assert final_lon > origin_lon
        assert final_lat > origin_lat
        assert result["total_distance_km"] > 0.0

        # Disclaimer must state deterministic model projection
        assert "deterministic" in result["scientific_disclaimer"].lower()
        assert "not an observed future trajectory" in result["scientific_disclaimer"].lower()

    def test_forward_drift_exceeding_horizon_fails_closed(self, tmp_path):
        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        # Fixture only covers up to obs_time + 4h
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=4)

        # Requesting 12h horizon when data only covers 4h must raise ForwardPredictionError
        with pytest.raises(ForwardPredictionError, match="exceeds available ERA5 wind forcing coverage"):
            predict_forward_drift(
                origin_lon=4.0,
                origin_lat=42.5,
                observation_time=obs_time,
                era5_netcdf_path=era5_path,
                cmems_netcdf_path=cmems_path,
                prediction_hours=12.0,
            )

    def test_zero_or_negative_duration_raises_error(self, tmp_path):
        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=6)

        with pytest.raises(ForwardPredictionError, match="prediction_hours must be > 0"):
            predict_forward_drift(
                origin_lon=4.0,
                origin_lat=42.5,
                observation_time=obs_time,
                era5_netcdf_path=era5_path,
                cmems_netcdf_path=cmems_path,
                prediction_hours=0.0,
            )

    def test_forward_prediction_api_endpoint(self, tmp_path):
        client = TestClient(app)
        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=12)

        payload = {
            "satellite_product_id": "TEST_S1A_SCENE_001",
            "observation_time": obs_time.isoformat(),
            "origin_lon": 4.0,
            "origin_lat": 42.5,
            "era5_netcdf_path": era5_path,
            "cmems_netcdf_path": cmems_path,
            "prediction_hours": 8.0,
            "step_hours": 1.0,
        }

        resp = client.post("/api/experiment/drift/forward-predict", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "prediction" in data
        pred = data["prediction"]
        assert pred["prediction_hours"] == 8.0
        assert len(pred["steps"]) == 8
        assert pred["final_lat"] > 42.5
        assert pred["final_lon"] > 4.0

    def test_experiment_runner_attaches_forward_prediction(self, tmp_path):
        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=12)

        runner = ExperimentRunner()
        result = runner.run(
            satellite_product_id="TEST_RUN_S1A",
            observation_lon=4.0,
            observation_lat=42.5,
            observation_time=obs_time,
            era5_netcdf_path=era5_path,
            cmems_netcdf_path=cmems_path,
            backtrack_hours=6.0,
            forward_prediction_hours=6.0,
            forward_step_hours=1.0,
        )

        assert result.forward_prediction is not None
        assert result.forward_prediction["prediction_hours"] == 6.0
        assert len(result.forward_prediction["steps"]) == 6
        assert len(result.backward_steps) == 6

        # Backward steps move backwards in time
        t_back_first = result.backward_steps[0]["timestamp"]
        t_back_last = result.backward_steps[-1]["timestamp"]
        assert t_back_last < t_back_first

        # Forward steps move forward in time
        t_fwd_first = result.forward_prediction["steps"][0]["timestamp"]
        t_fwd_last = result.forward_prediction["steps"][-1]["timestamp"]
        assert t_fwd_last > t_fwd_first

        d = result.as_dict()
        assert "forward_prediction" in d

    def test_store_persistence_and_backward_compatibility(self, tmp_path):
        db_path = tmp_path / "test_store_forward.db"
        store = ExperimentStore(db_path=db_path)

        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=12)

        runner = ExperimentRunner()
        res_with_fwd = runner.run(
            satellite_product_id="RUN_WITH_FWD",
            observation_lon=4.0,
            observation_lat=42.5,
            observation_time=obs_time,
            era5_netcdf_path=era5_path,
            cmems_netcdf_path=cmems_path,
            backtrack_hours=4.0,
            forward_prediction_hours=4.0,
        )

        store.save(res_with_fwd)
        reloaded = store.get_run(res_with_fwd.run_id)
        assert reloaded is not None
        assert reloaded.forward_prediction is not None
        assert reloaded.forward_prediction["prediction_hours"] == 4.0

        # Backward compatibility test: insert an older record where forward_prediction_json is NULL
        res_without_fwd = runner.run(
            satellite_product_id="RUN_WITHOUT_FWD",
            observation_lon=4.0,
            observation_lat=42.5,
            observation_time=obs_time,
            era5_netcdf_path=era5_path,
            cmems_netcdf_path=cmems_path,
            backtrack_hours=4.0,
            forward_prediction_hours=None,
        )
        store.save(res_without_fwd)
        reloaded_old = store.get_run(res_without_fwd.run_id)
        assert reloaded_old is not None
        assert reloaded_old.forward_prediction is None

    def test_report_data_and_pdf_generation_with_forward_drift(self, tmp_path):
        obs_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        era5_path, cmems_path = _make_forward_netcdf_fixtures(tmp_path, obs_time, forward_hours=12)

        runner = ExperimentRunner()
        res = runner.run(
            satellite_product_id="RUN_REPORT_TEST",
            observation_lon=4.0,
            observation_lat=42.5,
            observation_time=obs_time,
            era5_netcdf_path=era5_path,
            cmems_netcdf_path=cmems_path,
            backtrack_hours=6.0,
            forward_prediction_hours=6.0,
        )

        report_data = build_scientific_report_data(res)
        assert "forward_drift_prediction" in report_data
        fwd_rep = report_data["forward_drift_prediction"]
        assert fwd_rep is not None
        assert fwd_rep["prediction_hours"] == 6.0
        assert fwd_rep["steps_count"] == 6
        assert "deterministic" in fwd_rep["scientific_disclaimer"].lower()

        # Render PDF
        pdf_bytes = render_scientific_report_pdf(report_data)
        assert len(pdf_bytes) > 1000
        assert pdf_bytes.startswith(b"%PDF")
