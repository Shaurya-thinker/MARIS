"""Tests for MARIS Stage D1 — Forward Drift Modelling.

All tests are offline and hermetic. No network calls, no external services.
Synthetic ERA5 and CMEMS NetCDF fixtures are created using netCDF4 in temporary directories.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import Settings
from app.main import app
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.drift import DriftResult, DriftStep
from app.models.satellite import SpillDetection
from app.services.drift_modelling import (
    DEFAULT_DRIFT_HOURS,
    DEFAULT_LEEWAY_FRACTION,
    DEFAULT_STEP_HOURS,
    MODEL_VERSION,
    DriftModellingError,
    compute_drift_for_spill,
    extract_centroid,
    run_forward_drift,
)

# Reference timestamp: 2024-06-01 12:00:00 UTC
# ERA5: hours since 1900-01-01 -> 1090608 is 2024-06-01 00:00:00
_ERA5_T0 = 1090608
# CMEMS: days since 1950-01-01 -> 27180 is 2024-06-01
_CMEMS_T0 = 27180


def _create_synthetic_era5_nc(
    path: str | Path,
    *,
    lats: list[float] | None = None,
    lons: list[float] | None = None,
    times: list[int] | None = None,
    u_val: float = 2.0,
    v_val: float = 1.0,
) -> None:
    """Write a minimal valid ERA5 NetCDF file for tests."""
    lats = lats if lats is not None else [52.0, 51.75, 51.5, 51.25, 51.0]
    lons = lons if lons is not None else [2.0, 2.25, 2.5, 2.75, 3.0]
    times = times if times is not None else [_ERA5_T0 + i for i in range(48)]

    ds = nc.Dataset(str(path), "w", format="NETCDF4_CLASSIC")
    ds.createDimension("time", len(times))
    ds.createDimension("latitude", len(lats))
    ds.createDimension("longitude", len(lons))

    tv = ds.createVariable("time", "i4", ("time",))
    tv.units = "hours since 1900-01-01 00:00:00.0"
    tv.calendar = "gregorian"
    tv[:] = times

    lav = ds.createVariable("latitude", "f4", ("latitude",))
    lav.units = "degrees_north"
    lav[:] = lats

    lov = ds.createVariable("longitude", "f4", ("longitude",))
    lov.units = "degrees_east"
    lov[:] = lons

    shape = (len(times), len(lats), len(lons))
    u10 = ds.createVariable("u10", "f4", ("time", "latitude", "longitude"))
    u10.units = "m s**-1"
    u10[:] = np.full(shape, u_val, dtype=np.float32)

    v10 = ds.createVariable("v10", "f4", ("time", "latitude", "longitude"))
    v10.units = "m s**-1"
    v10[:] = np.full(shape, v_val, dtype=np.float32)

    ds.close()


def _create_synthetic_cmems_nc(
    path: str | Path,
    *,
    lats: list[float] | None = None,
    lons: list[float] | None = None,
    times: list[int] | None = None,
    u_val: float = 0.5,
    v_val: float = -0.2,
) -> None:
    """Write a minimal valid CMEMS NetCDF file for tests."""
    lats = lats if lats is not None else [51.0, 51.25, 51.5, 51.75, 52.0]
    lons = lons if lons is not None else [2.0, 2.25, 2.5, 2.75, 3.0]
    times = times if times is not None else [_CMEMS_T0 + i for i in range(5)]
    depths = [0.494]

    ds = nc.Dataset(str(path), "w", format="NETCDF4_CLASSIC")
    ds.createDimension("time", len(times))
    ds.createDimension("depth", len(depths))
    ds.createDimension("latitude", len(lats))
    ds.createDimension("longitude", len(lons))

    tv = ds.createVariable("time", "i4", ("time",))
    tv.units = "days since 1950-01-01 00:00:00"
    tv.calendar = "gregorian"
    tv[:] = times

    dv = ds.createVariable("depth", "f4", ("depth",))
    dv.units = "m"
    dv.positive = "down"
    dv[:] = depths

    lav = ds.createVariable("latitude", "f4", ("latitude",))
    lav.units = "degrees_north"
    lav[:] = lats

    lov = ds.createVariable("longitude", "f4", ("longitude",))
    lov.units = "degrees_east"
    lov[:] = lons

    shape = (len(times), len(depths), len(lats), len(lons))
    uo = ds.createVariable("uo", "f4", ("time", "depth", "latitude", "longitude"))
    uo.units = "m s-1"
    uo[:] = np.full(shape, u_val, dtype=np.float32)

    vo = ds.createVariable("vo", "f4", ("time", "depth", "latitude", "longitude"))
    vo.units = "m s-1"
    vo[:] = np.full(shape, v_val, dtype=np.float32)

    ds.close()


class TestDriftModellingValidation(unittest.TestCase):
    """Test fail-closed input validation for forward drift modelling."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.wind_path = Path(self.tmp_dir.name) / "wind.nc"
        self.curr_path = Path(self.tmp_dir.name) / "current.nc"
        _create_synthetic_era5_nc(self.wind_path)
        _create_synthetic_cmems_nc(self.curr_path)

        self.wind_asset = Asset(
            id="asset-wind-test",
            investigation_id="inv-test",
            type=AssetType.ENVIRONMENT_WIND,
            provider="copernicus-climate-data-store",
            source="era5",
            location=str(self.wind_path),
            provenance=Provenance(product_id="era5-wind"),
        )
        self.curr_asset = Asset(
            id="asset-curr-test",
            investigation_id="inv-test",
            type=AssetType.ENVIRONMENT_CURRENT,
            provider="copernicus-marine-service",
            source="cmems",
            location=str(self.curr_path),
            provenance=Provenance(product_id="cmems-curr"),
        )
        self.obs_time = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_undetected_spill_raises(self) -> None:
        spill = SpillDetection(
            id="spill-none",
            investigation_id="inv-test",
            asset_id="asset-spill",
            scene_id="scene-1",
            detected=False,
            confidence=0.0,
            geometry={},
            metadata={"centroid": {"longitude": 2.5, "latitude": 51.5}},
        )
        with self.assertRaises(DriftModellingError) as ctx:
            extract_centroid(spill)
        self.assertIn("detected=False", str(ctx.exception))

    def test_missing_centroid_raises(self) -> None:
        spill = SpillDetection(
            id="spill-nocentroid",
            investigation_id="inv-test",
            asset_id="asset-spill",
            scene_id="scene-1",
            detected=True,
            confidence=0.9,
            geometry={},
            metadata={},
        )
        with self.assertRaises(DriftModellingError) as ctx:
            extract_centroid(spill)
        self.assertIn("no centroid in metadata", str(ctx.exception))

    def test_missing_wind_file_raises(self) -> None:
        missing_asset = self.wind_asset.model_copy(update={"location": "non_existent_wind.nc"})
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=missing_asset,
                current_asset=self.curr_asset,
            )
        self.assertIn("ERA5 wind asset does not exist", str(ctx.exception))

    def test_missing_current_file_raises(self) -> None:
        missing_asset = self.curr_asset.model_copy(update={"location": "non_existent_curr.nc"})
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=missing_asset,
            )
        self.assertIn("CMEMS current asset does not exist", str(ctx.exception))

    def test_origin_outside_wind_bounds_raises(self) -> None:
        # Longitude 10.0 is well outside ERA5 grid [2.0, 3.0]
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=10.0,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
            )
        self.assertIn("outside the ERA5 grid", str(ctx.exception))

    def test_origin_outside_current_bounds_raises(self) -> None:
        # Latitude 40.0 is well outside CMEMS grid [51.0, 52.0]
        curr_out_path = Path(self.tmp_dir.name) / "curr_tight.nc"
        _create_synthetic_cmems_nc(
            curr_out_path,
            lats=[51.4, 51.6],
            lons=[2.4, 2.6],
        )
        tight_curr_asset = self.curr_asset.model_copy(update={"location": str(curr_out_path)})
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.1,  # inside ERA5 [2, 3], but outside CMEMS [2.4, 2.6]
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=tight_curr_asset,
            )
        self.assertIn("outside the CMEMS grid", str(ctx.exception))

    def test_time_outside_wind_range_raises(self) -> None:
        # ERA5 grid spans 2024-06-01 00:00 to 2024-06-02 23:00 (48 hours)
        far_future = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=far_future,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
            )
        self.assertIn("outside the ERA5 data temporal range", str(ctx.exception))

    def test_zero_or_negative_duration_raises(self) -> None:
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
                drift_hours=0.0,
            )
        self.assertIn("drift_hours must be > 0", str(ctx.exception))

        with self.assertRaises(DriftModellingError):
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
                drift_hours=-5.0,
            )

    def test_negative_step_raises(self) -> None:
        with self.assertRaises(DriftModellingError) as ctx:
            compute_drift_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
                step_hours=0.0,
            )
        self.assertIn("step_hours must be > 0", str(ctx.exception))


class TestDriftModellingTrajectory(unittest.TestCase):
    """Test forward drift physical integration and properties."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.registry = InMemoryAssetRegistry()
        self.obs_time = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_step_count_correct_exact_multiple(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind.nc"
        curr_p = Path(self.tmp_dir.name) / "curr.nc"
        _create_synthetic_era5_nc(wind_p)
        _create_synthetic_cmems_nc(curr_p)
        w_asset = Asset(id="w1", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c1", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd1",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=6.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(len(result.steps), 6)
        self.assertEqual(result.total_duration_hours, 6.0)
        self.assertEqual(result.step_hours, 1.0)

    def test_step_count_correct_non_multiple_shortens_final_step(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind2.nc"
        curr_p = Path(self.tmp_dir.name) / "curr2.nc"
        _create_synthetic_era5_nc(wind_p)
        _create_synthetic_cmems_nc(curr_p)
        w_asset = Asset(id="w2", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c2", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        # 2.5 hours with 1.0 h step -> 3 steps: 1h, 1h, 0.5h
        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd2",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=2.5,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(len(result.steps), 3)

    def test_trajectory_deterministic(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_det.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_det.nc"
        _create_synthetic_era5_nc(wind_p, u_val=3.0, v_val=-2.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.4, v_val=0.1)
        w_asset = Asset(id="w_det", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_det", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        res1, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_det",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=4.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        res2, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_det",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=4.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(res1.endpoint_lon, res2.endpoint_lon)
        self.assertEqual(res1.endpoint_lat, res2.endpoint_lat)
        for s1, s2 in zip(res1.steps, res2.steps):
            self.assertEqual(s1.lon, s2.lon)
            self.assertEqual(s1.lat, s2.lat)
            self.assertEqual(s1.cumulative_distance_m, s2.cumulative_distance_m)

    def test_zero_forcing_stationary(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_zero.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_zero.nc"
        _create_synthetic_era5_nc(wind_p, u_val=0.0, v_val=0.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.0, v_val=0.0)
        w_asset = Asset(id="w_zero", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_zero", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_zero",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=5.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertAlmostEqual(result.endpoint_lon, 2.5, places=6)
        self.assertAlmostEqual(result.endpoint_lat, 51.5, places=6)
        self.assertAlmostEqual(result.steps[-1].cumulative_distance_m, 0.0, places=3)

    def test_current_only_displacement(self) -> None:
        # Wind = 0, Current: u = 1.0 m/s, v = 0 m/s
        # 1 hour = 3600 s. Displacement in metres: dx = 3600 m
        # dlon = 3600 / (cos(51.5°) * 111320)
        wind_p = Path(self.tmp_dir.name) / "wind_co.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_co.nc"
        _create_synthetic_era5_nc(wind_p, u_val=0.0, v_val=0.0)
        _create_synthetic_cmems_nc(curr_p, u_val=1.0, v_val=0.0)
        w_asset = Asset(id="w_co", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_co", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_co",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=1.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        expected_dlon = 3600.0 / (math.cos(math.radians(51.5)) * 111320.0)
        expected_lon = 2.5 + expected_dlon
        self.assertAlmostEqual(result.endpoint_lat, 51.5, places=5)
        self.assertAlmostEqual(result.endpoint_lon, expected_lon, places=5)
        # Distance should be approximately 3600 m
        self.assertAlmostEqual(result.steps[0].cumulative_distance_m, 3600.0, delta=10.0)

    def test_wind_leeway_displacement(self) -> None:
        # Current = 0, Wind: u = 10.0 m/s, v = 0 m/s, alpha = 0.035
        # Net drift u = 0.35 m/s. 1 hour = 3600 s. Displacement: 0.35 * 3600 = 1260 m
        wind_p = Path(self.tmp_dir.name) / "wind_lw.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_lw.nc"
        _create_synthetic_era5_nc(wind_p, u_val=10.0, v_val=0.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.0, v_val=0.0)
        w_asset = Asset(id="w_lw", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_lw", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_lw",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=1.0,
            step_hours=1.0,
            leeway_fraction=0.035,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertAlmostEqual(result.steps[0].drift_u_ms, 0.35, places=3)
        self.assertAlmostEqual(result.steps[0].drift_v_ms, 0.0, places=3)
        self.assertAlmostEqual(result.steps[0].cumulative_distance_m, 1260.0, delta=10.0)

    def test_zero_leeway_ignores_wind(self) -> None:
        # Wind = 100 m/s, Current = 1 m/s eastward, alpha = 0.0
        # Drift should be purely current (1.0 m/s)
        wind_p = Path(self.tmp_dir.name) / "wind_zlw.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_zlw.nc"
        _create_synthetic_era5_nc(wind_p, u_val=100.0, v_val=50.0)
        _create_synthetic_cmems_nc(curr_p, u_val=1.0, v_val=0.0)
        w_asset = Asset(id="w_zlw", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_zlw", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_zlw",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=1.0,
            step_hours=1.0,
            leeway_fraction=0.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertAlmostEqual(result.steps[0].drift_u_ms, 1.0, places=3)
        self.assertAlmostEqual(result.steps[0].drift_v_ms, 0.0, places=3)

    def test_endpoint_matches_last_step(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_ep.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_ep.nc"
        _create_synthetic_era5_nc(wind_p, u_val=2.5, v_val=-1.2)
        _create_synthetic_cmems_nc(curr_p, u_val=0.3, v_val=0.4)
        w_asset = Asset(id="w_ep", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_ep", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_ep",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=3.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(result.endpoint_lon, result.steps[-1].lon)
        self.assertEqual(result.endpoint_lat, result.steps[-1].lat)
        self.assertIsNone(result.endpoint_uncertainty_km)

    def test_cumulative_distance_monotone(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_cd.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_cd.nc"
        _create_synthetic_era5_nc(wind_p, u_val=4.0, v_val=2.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.5, v_val=0.5)
        w_asset = Asset(id="w_cd", investigation_id="inv1", type=AssetType.ENVIRONMENT_WIND,
                        provider="copernicus-climate-data-store", source="era5", location=str(wind_p))
        c_asset = Asset(id="c_cd", investigation_id="inv1", type=AssetType.ENVIRONMENT_CURRENT,
                        provider="copernicus-marine-service", source="cmems", location=str(curr_p))

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_cd",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            drift_hours=6.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        prev_dist = 0.0
        for step in result.steps:
            self.assertGreaterEqual(step.cumulative_distance_m, prev_dist)
            prev_dist = step.cumulative_distance_m


class TestDriftModellingArtifactsAndRegistration(unittest.TestCase):
    """Test asset registration, GeoJSON generation, and Pydantic serialization."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.registry = InMemoryAssetRegistry()
        self.obs_time = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        self.wind_path = Path(self.tmp_dir.name) / "wind.nc"
        self.curr_path = Path(self.tmp_dir.name) / "curr.nc"
        _create_synthetic_era5_nc(self.wind_path, u_val=3.0, v_val=1.5)
        _create_synthetic_cmems_nc(self.curr_path, u_val=0.4, v_val=-0.2)

        self.w_asset = Asset(
            id="asset-wind-art",
            investigation_id="inv-art",
            type=AssetType.ENVIRONMENT_WIND,
            provider="copernicus-climate-data-store",
            source="era5",
            location=str(self.wind_path),
        )
        self.c_asset = Asset(
            id="asset-curr-art",
            investigation_id="inv-art",
            type=AssetType.ENVIRONMENT_CURRENT,
            provider="copernicus-marine-service",
            source="cmems",
            location=str(self.curr_path),
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_drift_result_json_serializable(self) -> None:
        result, _ = compute_drift_for_spill(
            investigation_id="inv-art",
            spill_detection_id="spill-ser",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.w_asset,
            current_asset=self.c_asset,
            drift_hours=3.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        raw_json = result.model_dump_json()
        loaded = json.loads(raw_json)
        self.assertEqual(loaded["investigation_id"], "inv-art")
        self.assertEqual(loaded["model_version"], MODEL_VERSION)
        self.assertEqual(len(loaded["steps"]), 3)

        # Roundtrip back to DriftResult model
        roundtrip = DriftResult.model_validate_json(raw_json)
        self.assertEqual(roundtrip.id, result.id)
        self.assertEqual(roundtrip.endpoint_lon, result.endpoint_lon)

    def test_drift_result_linkage_ids(self) -> None:
        result, derived_asset = compute_drift_for_spill(
            investigation_id="inv-art",
            spill_detection_id="spill-linkage",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.w_asset,
            current_asset=self.c_asset,
            drift_hours=2.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(result.spill_detection_id, "spill-linkage")
        self.assertEqual(result.wind_asset_id, self.w_asset.id)
        self.assertEqual(result.current_asset_id, self.c_asset.id)
        self.assertEqual(result.asset_id, derived_asset.id)

    def test_asset_registration(self) -> None:
        result, derived_asset = compute_drift_for_spill(
            investigation_id="inv-art",
            spill_detection_id="spill-reg",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.w_asset,
            current_asset=self.c_asset,
            drift_hours=2.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(derived_asset.type, AssetType.DRIFT_PRODUCT)
        self.assertEqual(derived_asset.investigation_id, "inv-art")
        self.assertIn("drift_modelling_service", derived_asset.source)

        # Asset should be retrieved by ID from registry
        retrieved = self.registry.get(derived_asset.id)
        self.assertEqual(retrieved.id, derived_asset.id)
        self.assertEqual(retrieved.type, AssetType.DRIFT_PRODUCT)

    def test_geojson_linestring_written(self) -> None:
        result, derived_asset = compute_drift_for_spill(
            investigation_id="inv-art",
            spill_detection_id="spill-geojson",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.w_asset,
            current_asset=self.c_asset,
            drift_hours=3.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        geojson_path = Path(derived_asset.location)
        self.assertTrue(geojson_path.exists())

        with open(geojson_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)

        self.assertEqual(data["type"], "Feature")
        self.assertEqual(data["geometry"]["type"], "LineString")
        # Coordinates must contain origin + 3 steps = 4 points
        coords = data["geometry"]["coordinates"]
        self.assertEqual(len(coords), 4)
        self.assertEqual(coords[0], [2.5, 51.5])
        self.assertEqual(coords[-1], [result.endpoint_lon, result.endpoint_lat])
        self.assertIn("scientific_note", data["properties"])


class TestDriftModellingApi(unittest.TestCase):
    """Test HTTP API route for forward drift modelling."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.wind_path = Path(self.tmp_dir.name) / "wind_api.nc"
        self.curr_path = Path(self.tmp_dir.name) / "curr_api.nc"
        _create_synthetic_era5_nc(self.wind_path, u_val=2.0, v_val=1.0)
        _create_synthetic_cmems_nc(self.curr_path, u_val=0.3, v_val=0.1)

        self.obs_time = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Register wind and current assets into default_asset_registry
        self.wind_asset = default_asset_registry.register(
            investigation_id="inv-api",
            provider_id="copernicus-climate-data-store",
            artifact=AcquiredArtifact(
                asset_type=AssetType.ENVIRONMENT_WIND,
                location=str(self.wind_path),
                source="era5",
                provenance=Provenance(product_id="era5-wind"),
            ),
        )
        self.curr_asset = default_asset_registry.register(
            investigation_id="inv-api",
            provider_id="copernicus-marine-service",
            artifact=AcquiredArtifact(
                asset_type=AssetType.ENVIRONMENT_CURRENT,
                location=str(self.curr_path),
                source="cmems",
                provenance=Provenance(product_id="cmems-curr"),
            ),
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_api_drift_200_success(self) -> None:
        # Register a detected spill asset
        spill_artifact = AcquiredArtifact(
            asset_type=AssetType.SPILL_GEOMETRY,
            location=str(Path(self.tmp_dir.name) / "spill.geojson"),
            source="spill_detection_service",
            acquisition_time=self.obs_time,
            provenance=Provenance(product_id="spill-api-1"),
            metadata={
                "detected": True,
                "spill_count": 1,
                "centroid": {"longitude": 2.5, "latitude": 51.5},
            },
        )
        spill_asset = default_asset_registry.register(
            "inv-api", "sar_detector", spill_artifact
        )

        resp = self.client.post(
            f"/api/v1/investigations/inv-api/spills/{spill_asset.id}/drift",
            json={
                "wind_asset_id": self.wind_asset.id,
                "current_asset_id": self.curr_asset.id,
                "drift_hours": 4.0,
                "step_hours": 1.0,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["investigation_id"], "inv-api")
        self.assertEqual(data["wind_asset_id"], self.wind_asset.id)
        self.assertEqual(data["current_asset_id"], self.curr_asset.id)
        self.assertEqual(len(data["steps"]), 4)
        self.assertAlmostEqual(data["origin_lon"], 2.5, places=5)
        self.assertAlmostEqual(data["origin_lat"], 51.5, places=5)

    def test_api_drift_422_undetected_spill(self) -> None:
        # Register an undetected spill asset
        spill_artifact = AcquiredArtifact(
            asset_type=AssetType.SPILL_GEOMETRY,
            location=str(Path(self.tmp_dir.name) / "spill_empty.geojson"),
            source="spill_detection_service",
            acquisition_time=self.obs_time,
            provenance=Provenance(product_id="spill-api-undetected"),
            metadata={
                "detected": False,
                "spill_count": 0,
                "centroid": None,
            },
        )
        spill_asset = default_asset_registry.register(
            "inv-api", "sar_detector", spill_artifact
        )

        resp = self.client.post(
            f"/api/v1/investigations/inv-api/spills/{spill_asset.id}/drift",
            json={
                "wind_asset_id": self.wind_asset.id,
                "current_asset_id": self.curr_asset.id,
                "drift_hours": 4.0,
            },
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("detected=False", resp.json()["detail"])

    def test_api_drift_404_missing_asset(self) -> None:
        resp = self.client.post(
            "/api/v1/investigations/inv-api/spills/non_existent_spill/drift",
            json={
                "wind_asset_id": self.wind_asset.id,
                "current_asset_id": self.curr_asset.id,
            },
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
