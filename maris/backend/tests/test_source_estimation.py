"""Tests for MARIS Stage D3 — Backward Drift & Source Candidate Zone Estimation.

All tests are offline and hermetic. No network calls, no external services.
Synthetic ERA5 and CMEMS NetCDF fixtures are created using netCDF4 in temporary directories.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.main import app
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.satellite import SpillDetection
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.services.drift_modelling import (
    DEFAULT_LEEWAY_FRACTION,
    DriftModellingError,
    _haversine_m,
    compute_drift_for_spill,
    extract_centroid,
    run_forward_drift,
)
from app.services.source_estimation import (
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_STEP_HOURS,
    DEFAULT_UNCERTAINTY_GROWTH_M_PER_H,
    MIN_INITIAL_RADIUS_M,
    MODEL_VERSION,
    SourceEstimationError,
    compute_source_estimate_for_spill,
    generate_source_candidate_polygon,
    run_backward_drift,
)

# Reference timestamp: 2024-06-02 12:00:00 UTC
# Hours since 1900-01-01 for 2024-06-01 00:00:00 UTC = 1090608
_ERA5_T0 = 1090608
# Days since 1950-01-01 for 2024-06-01 = 27180
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
    """Write a minimal valid ERA5 NetCDF file spanning 72 hours starting 2024-06-01 00:00."""
    lats = lats if lats is not None else [52.0, 51.75, 51.5, 51.25, 51.0]
    lons = lons if lons is not None else [2.0, 2.25, 2.5, 2.75, 3.0]
    times = times if times is not None else [_ERA5_T0 + i for i in range(72)]

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
    """Write a minimal valid CMEMS NetCDF file spanning 5 days starting 2024-06-01."""
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


def _make_wind_asset(asset_id: str, inv_id: str, location: str) -> Asset:
    return Asset(
        id=asset_id,
        investigation_id=inv_id,
        type=AssetType.ENVIRONMENT_WIND,
        provider="copernicus-climate-data-store",
        source="era5",
        location=location,
        provenance=Provenance(product_id="era5-wind"),
    )


def _make_curr_asset(asset_id: str, inv_id: str, location: str) -> Asset:
    return Asset(
        id=asset_id,
        investigation_id=inv_id,
        type=AssetType.ENVIRONMENT_CURRENT,
        provider="copernicus-marine-service",
        source="cmems",
        location=location,
        provenance=Provenance(product_id="cmems-curr"),
    )


class TestSourceEstimationValidation(unittest.TestCase):
    """Test fail-closed input validation for backward drift & source estimation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.wind_path = Path(self.tmp_dir.name) / "wind.nc"
        self.curr_path = Path(self.tmp_dir.name) / "current.nc"
        _create_synthetic_era5_nc(self.wind_path)
        _create_synthetic_cmems_nc(self.curr_path)

        self.wind_asset = _make_wind_asset("asset-wind-test", "inv-test", str(self.wind_path))
        self.curr_asset = _make_curr_asset("asset-curr-test", "inv-test", str(self.curr_path))
        # Observation time is 2024-06-02 12:00:00 (36 hours after ERA5 start)
        self.obs_time = datetime(2024, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

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
        bad_asset = self.wind_asset.model_copy(update={"location": "nonexistent_wind.nc"})
        with self.assertRaises(SourceEstimationError) as ctx:
            compute_source_estimate_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=bad_asset,
                current_asset=self.curr_asset,
            )
        self.assertIn("does not exist", str(ctx.exception))

    def test_missing_current_file_raises(self) -> None:
        bad_asset = self.curr_asset.model_copy(update={"location": "nonexistent_curr.nc"})
        with self.assertRaises(SourceEstimationError) as ctx:
            compute_source_estimate_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=bad_asset,
            )
        self.assertIn("does not exist", str(ctx.exception))

    def test_insufficient_historical_coverage_raises(self) -> None:
        # Obs is 2024-06-02 12:00 UTC (36h after 2024-06-01 00:00).
        # Requesting lookback of 48 hours would reach 2024-05-31 12:00, which precedes ERA5 start!
        with self.assertRaises(SourceEstimationError) as ctx:
            compute_source_estimate_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
                lookback_hours=48.0,
            )
        self.assertIn("precedes the start of available ERA5 wind data", str(ctx.exception))

    def test_invalid_lookback_raises(self) -> None:
        with self.assertRaises(SourceEstimationError) as ctx:
            compute_source_estimate_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
                lookback_hours=0.0,
            )
        self.assertIn("lookback_hours must be > 0", str(ctx.exception))

    def test_invalid_step_raises(self) -> None:
        with self.assertRaises(SourceEstimationError) as ctx:
            compute_source_estimate_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
                step_hours=-1.0,
            )
        self.assertIn("step_hours must be > 0", str(ctx.exception))

    def test_origin_outside_grids_raises(self) -> None:
        with self.assertRaises(SourceEstimationError) as ctx:
            compute_source_estimate_for_spill(
                investigation_id="inv-test",
                spill_detection_id="spill-1",
                origin_lon=10.0,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_asset=self.wind_asset,
                current_asset=self.curr_asset,
            )
        self.assertIn("outside ERA5 grid", str(ctx.exception))


class TestSourceEstimationPhysics(unittest.TestCase):
    """Test time-reversed Leeway-Euler physical integration properties."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.registry = InMemoryAssetRegistry()
        self.obs_time = datetime(2024, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_zero_forcing_stationary(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_z.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_z.nc"
        _create_synthetic_era5_nc(wind_p, u_val=0.0, v_val=0.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.0, v_val=0.0)
        w_asset = _make_wind_asset("wz", "inv1", str(wind_p))
        c_asset = _make_curr_asset("cz", "inv1", str(curr_p))

        result, _ = compute_source_estimate_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_z",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            lookback_hours=6.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertAlmostEqual(result.source_point_lon, 2.5, places=6)
        self.assertAlmostEqual(result.source_point_lat, 51.5, places=6)
        self.assertAlmostEqual(result.steps[-1].cumulative_backward_distance_m, 0.0, places=3)

    def test_pure_current_reversal(self) -> None:
        # Current uo = 1.0 m/s eastward. Wind = 0.
        # Stepping backward should move WESTWARD (lon decreases).
        wind_p = Path(self.tmp_dir.name) / "wind_cr.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_cr.nc"
        _create_synthetic_era5_nc(wind_p, u_val=0.0, v_val=0.0)
        _create_synthetic_cmems_nc(curr_p, u_val=1.0, v_val=0.0)
        w_asset = _make_wind_asset("wcr", "inv1", str(wind_p))
        c_asset = _make_curr_asset("ccr", "inv1", str(curr_p))

        result, _ = compute_source_estimate_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_cr",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            lookback_hours=1.0,
            step_hours=1.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        # Expected displacement: dx = -3600 m
        expected_dlon = -3600.0 / (math.cos(math.radians(51.5)) * 111320.0)
        self.assertAlmostEqual(result.source_point_lon, 2.5 + expected_dlon, places=5)
        self.assertAlmostEqual(result.source_point_lat, 51.5, places=5)
        self.assertAlmostEqual(result.steps[0].cumulative_backward_distance_m, 3600.0, delta=10.0)

    def test_pure_wind_reversal(self) -> None:
        # Wind v10 = 10.0 m/s northward. Current = 0. Leeway = 0.035.
        # Drift v = 0.35 m/s northward.
        # Backward step should move SOUTHWARD (lat decreases).
        wind_p = Path(self.tmp_dir.name) / "wind_wr.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_wr.nc"
        _create_synthetic_era5_nc(wind_p, u_val=0.0, v_val=10.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.0, v_val=0.0)
        w_asset = _make_wind_asset("wwr", "inv1", str(wind_p))
        c_asset = _make_curr_asset("cwr", "inv1", str(curr_p))

        result, _ = compute_source_estimate_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_wr",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_asset,
            current_asset=c_asset,
            lookback_hours=1.0,
            step_hours=1.0,
            leeway_fraction=0.035,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        expected_dy = -(0.35 * 3600.0) / 111320.0
        self.assertAlmostEqual(result.source_point_lat, 51.5 + expected_dy, places=5)
        self.assertAlmostEqual(result.source_point_lon, 2.5, places=5)

    def test_deterministic_repeatability(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_rep.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_rep.nc"
        _create_synthetic_era5_nc(wind_p, u_val=2.5, v_val=-1.5)
        _create_synthetic_cmems_nc(curr_p, u_val=0.3, v_val=0.2)
        w_asset = _make_wind_asset("wrep", "inv1", str(wind_p))
        c_asset = _make_curr_asset("crep", "inv1", str(curr_p))

        r1, _ = compute_source_estimate_for_spill(
            investigation_id="inv1", spill_detection_id="sd_rep", origin_lon=2.5, origin_lat=51.5,
            observation_time=self.obs_time, wind_asset=w_asset, current_asset=c_asset,
            lookback_hours=4.0, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        r2, _ = compute_source_estimate_for_spill(
            investigation_id="inv1", spill_detection_id="sd_rep", origin_lon=2.5, origin_lat=51.5,
            observation_time=self.obs_time, wind_asset=w_asset, current_asset=c_asset,
            lookback_hours=4.0, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        self.assertEqual(r1.source_point_lon, r2.source_point_lon)
        self.assertEqual(r1.source_point_lat, r2.source_point_lat)
        self.assertEqual(r1.source_time, r2.source_time)
        self.assertEqual(len(r1.steps), len(r2.steps))

    def test_exact_duration_shortened_final_step(self) -> None:
        wind_p = Path(self.tmp_dir.name) / "wind_part.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_part.nc"
        _create_synthetic_era5_nc(wind_p)
        _create_synthetic_cmems_nc(curr_p)
        w_asset = _make_wind_asset("wpart", "inv1", str(wind_p))
        c_asset = _make_curr_asset("cpart", "inv1", str(curr_p))

        # 2.5 hours with step 1.0 h -> 3 steps: 1h, 1h, 0.5h
        result, _ = compute_source_estimate_for_spill(
            investigation_id="inv1", spill_detection_id="sd_part", origin_lon=2.5, origin_lat=51.5,
            observation_time=self.obs_time, wind_asset=w_asset, current_asset=c_asset,
            lookback_hours=2.5, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        self.assertEqual(len(result.steps), 3)
        self.assertEqual(result.source_time, self.obs_time - timedelta(hours=2.5))
        self.assertEqual(result.steps[-1].timestamp, self.obs_time - timedelta(hours=2.5))

    def test_reversibility_numerical_roundtrip(self) -> None:
        """Constant-forcing forward trajectory followed by equivalent backward trajectory returns to origin."""
        wind_p = Path(self.tmp_dir.name) / "wind_rev.nc"
        curr_p = Path(self.tmp_dir.name) / "curr_rev.nc"
        # Constant steady forcing
        _create_synthetic_era5_nc(wind_p, u_val=3.0, v_val=2.0)
        _create_synthetic_cmems_nc(curr_p, u_val=0.4, v_val=-0.2)
        w_asset = _make_wind_asset("wrev", "inv1", str(wind_p))
        c_asset = _make_curr_asset("crev", "inv1", str(curr_p))

        t0 = datetime(2024, 6, 2, 0, 0, 0, tzinfo=timezone.utc)
        start_lon, start_lat = 2.4, 51.4
        duration = 4.0

        # 1. Forward run for 4 hours
        fwd_res, _ = compute_drift_for_spill(
            investigation_id="inv1", spill_detection_id="sd_rev", origin_lon=start_lon, origin_lat=start_lat,
            observation_time=t0, wind_asset=w_asset, current_asset=c_asset,
            drift_hours=duration, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        end_lon, end_lat = fwd_res.endpoint_lon, fwd_res.endpoint_lat
        t_end = t0 + timedelta(hours=duration)

        # 2. Backward run for 4 hours starting from forward endpoint
        bwd_res, _ = compute_source_estimate_for_spill(
            investigation_id="inv1", spill_detection_id="sd_rev_bwd", origin_lon=end_lon, origin_lat=end_lat,
            observation_time=t_end, wind_asset=w_asset, current_asset=c_asset,
            lookback_hours=duration, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        returned_lon = bwd_res.source_point_lon
        returned_lat = bwd_res.source_point_lat

        # Check distance between initial start and roundtrip return is tiny (< 5 meters)
        residual_distance_m = _haversine_m(start_lon, start_lat, returned_lon, returned_lat)
        self.assertLess(residual_distance_m, 5.0)


class TestSourceCandidateZoneGeometry(unittest.TestCase):
    """Test analytical uncertainty envelope expansion and GeoJSON artifact generation."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.registry = InMemoryAssetRegistry()
        self.obs_time = datetime(2024, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

        self.wind_path = Path(self.tmp_dir.name) / "wind_geo.nc"
        self.curr_path = Path(self.tmp_dir.name) / "curr_geo.nc"
        _create_synthetic_era5_nc(self.wind_path)
        _create_synthetic_cmems_nc(self.curr_path)

        self.w_asset = _make_wind_asset("wg", "inv_g", str(self.wind_path))
        self.c_asset = _make_curr_asset("cg", "inv_g", str(self.curr_path))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_analytical_polygon_generator(self) -> None:
        poly = generate_source_candidate_polygon(2.5, 51.5, 1000.0, num_vertices=32)
        self.assertEqual(poly["type"], "Polygon")
        coords = poly["coordinates"][0]
        # 32 vertices + 1 closing vertex = 33 coordinates
        self.assertEqual(len(coords), 33)
        self.assertEqual(coords[0], coords[-1])  # closed ring

    def test_analytical_radius_growth(self) -> None:
        # Area = 1,000,000 m2 -> sqrt(1000000 / pi) ≈ 564.19 m (R0)
        # Growth rate = 500 m/hour. Lookback = 4 hours.
        # Expected source uncertainty radius: 564.19 + 500 * 4 = 2564.19 m ≈ 2.564 km
        spill_area = 1_000_000.0
        expected_r0 = math.sqrt(spill_area / math.pi)

        result, _ = compute_source_estimate_for_spill(
            investigation_id="inv_g", spill_detection_id="sd_rad", origin_lon=2.5, origin_lat=51.5,
            observation_time=self.obs_time, wind_asset=self.w_asset, current_asset=self.c_asset,
            lookback_hours=4.0, step_hours=1.0, spill_area_m2=spill_area,
            uncertainty_growth_m_per_h=500.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )

        expected_final_r_m = expected_r0 + 500.0 * 4.0
        self.assertAlmostEqual(result.source_uncertainty_radius_km, expected_final_r_m / 1000.0, places=3)

        # Monotonic expansion of uncertainty radius across steps
        prev_r = 0.0
        for s in result.steps:
            self.assertGreater(s.uncertainty_radius_m, prev_r)
            prev_r = s.uncertainty_radius_m

    def test_geojson_feature_collection_written(self) -> None:
        result, derived_asset = compute_source_estimate_for_spill(
            investigation_id="inv_g", spill_detection_id="sd_fc", origin_lon=2.5, origin_lat=51.5,
            observation_time=self.obs_time, wind_asset=self.w_asset, current_asset=self.c_asset,
            lookback_hours=3.0, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        geojson_file = Path(derived_asset.location)
        self.assertTrue(geojson_file.exists())

        with open(geojson_file, "r", encoding="utf-8") as fp:
            fc = json.load(fp)

        self.assertEqual(fc["type"], "FeatureCollection")
        features = fc["features"]
        self.assertEqual(len(features), 2)

        # Feature 1: LineString
        f_line = features[0]
        self.assertEqual(f_line["type"], "Feature")
        self.assertEqual(f_line["geometry"]["type"], "LineString")
        self.assertEqual(len(f_line["geometry"]["coordinates"]), 4)  # origin + 3 steps

        # Feature 2: Polygon
        f_poly = features[1]
        self.assertEqual(f_poly["type"], "Feature")
        self.assertEqual(f_poly["geometry"]["type"], "Polygon")
        self.assertEqual(len(f_poly["geometry"]["coordinates"][0]), 33)

    def test_model_serialization(self) -> None:
        result, _ = compute_source_estimate_for_spill(
            investigation_id="inv_g", spill_detection_id="sd_ser", origin_lon=2.5, origin_lat=51.5,
            observation_time=self.obs_time, wind_asset=self.w_asset, current_asset=self.c_asset,
            lookback_hours=2.0, step_hours=1.0, registry=self.registry, output_dir=self.tmp_dir.name,
        )
        serialized = result.model_dump_json()
        roundtrip = SourceEstimateResult.model_validate_json(serialized)
        self.assertEqual(roundtrip.id, result.id)
        self.assertEqual(roundtrip.source_point_lon, result.source_point_lon)
        self.assertEqual(roundtrip.source_point_lat, result.source_point_lat)


class TestSourceEstimationApi(unittest.TestCase):
    """Test HTTP API route for backward drift & source candidate zone estimation."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.wind_path = Path(self.tmp_dir.name) / "wind_api.nc"
        self.curr_path = Path(self.tmp_dir.name) / "curr_api.nc"
        _create_synthetic_era5_nc(self.wind_path, u_val=2.0, v_val=1.0)
        _create_synthetic_cmems_nc(self.curr_path, u_val=0.3, v_val=0.1)

        self.obs_time = datetime(2024, 6, 2, 12, 0, 0, tzinfo=timezone.utc)

        self.wind_asset = default_asset_registry.register(
            investigation_id="inv-api-d3",
            provider_id="copernicus-climate-data-store",
            artifact=AcquiredArtifact(
                asset_type=AssetType.ENVIRONMENT_WIND,
                location=str(self.wind_path),
                source="era5",
                provenance=Provenance(product_id="era5-wind"),
            ),
        )
        self.curr_asset = default_asset_registry.register(
            investigation_id="inv-api-d3",
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

    def test_api_source_estimate_200_success(self) -> None:
        spill_artifact = AcquiredArtifact(
            asset_type=AssetType.SPILL_GEOMETRY,
            location=str(Path(self.tmp_dir.name) / "spill_api.geojson"),
            source="spill_detection_service",
            acquisition_time=self.obs_time,
            provenance=Provenance(product_id="spill-d3-api"),
            metadata={
                "detected": True,
                "spill_count": 1,
                "area": 250000.0,
                "centroid": {"longitude": 2.5, "latitude": 51.5},
            },
        )
        spill_asset = default_asset_registry.register(
            "inv-api-d3", "sar_detector", spill_artifact
        )

        resp = self.client.post(
            f"/api/v1/investigations/inv-api-d3/spills/{spill_asset.id}/source-estimate",
            json={
                "wind_asset_id": self.wind_asset.id,
                "current_asset_id": self.curr_asset.id,
                "lookback_hours": 3.0,
                "step_hours": 1.0,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["investigation_id"], "inv-api-d3")
        self.assertEqual(data["spill_detection_id"], "spill-d3-api")
        self.assertEqual(len(data["steps"]), 3)
        self.assertIn("source_zone_geometry", data)
        self.assertEqual(data["source_zone_geometry"]["type"], "Polygon")

    def test_api_source_estimate_422_undetected(self) -> None:
        spill_artifact = AcquiredArtifact(
            asset_type=AssetType.SPILL_GEOMETRY,
            location=str(Path(self.tmp_dir.name) / "spill_undetected.geojson"),
            source="spill_detection_service",
            acquisition_time=self.obs_time,
            provenance=Provenance(product_id="spill-d3-undetected"),
            metadata={
                "detected": False,
                "spill_count": 0,
                "centroid": None,
            },
        )
        spill_asset = default_asset_registry.register(
            "inv-api-d3", "sar_detector", spill_artifact
        )

        resp = self.client.post(
            f"/api/v1/investigations/inv-api-d3/spills/{spill_asset.id}/source-estimate",
            json={
                "wind_asset_id": self.wind_asset.id,
                "current_asset_id": self.curr_asset.id,
                "lookback_hours": 3.0,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_api_source_estimate_404_missing_asset(self) -> None:
        resp = self.client.post(
            "/api/v1/investigations/inv-api-d3/spills/nonexistent/source-estimate",
            json={
                "wind_asset_id": self.wind_asset.id,
                "current_asset_id": self.curr_asset.id,
            },
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
