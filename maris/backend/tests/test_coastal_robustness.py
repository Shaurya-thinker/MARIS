"""Tests for Stage D1/D3 Coastal Robustness Implementation.

Validates the 10 coastal robustness requirements (A through J):
A. Open-ocean current + wind -> forcing_mode = current_plus_windage
B. Coastal CMEMS NaN + valid wind -> trajectory continues, forcing_mode = wind_only_leeway, current_fallback_used = true
C. Wind NaN + current valid -> environmental_failure
D. Both wind/current NaN -> environmental_failure
E. Starting point outside maritime mask -> structured domain failure (MaritimeDomainError)
F. Forward trajectory enters land -> shoreline_reached, invalid position not included
G. Backward trajectory enters land -> shoreline_boundary_reached, invalid position not included
H. Environmental bounding-box exit -> domain_exit, no unhandled ValueError
I. Entire open-ocean Ulysse benchmark -> still succeeds
J. Existing regression tests continue to pass
"""

from __future__ import annotations

import datetime
from datetime import timezone
from pathlib import Path
import tempfile
import unittest

import netCDF4 as nc
import numpy as np

from app.acquisition.registry import InMemoryAssetRegistry
from app.models.asset import Asset
from app.models.common import AssetType
from app.services.drift_modelling import (
    DriftModellingError,
    MaritimeDomainChecker,
    MaritimeDomainError,
    compute_drift_for_spill,
    run_forward_drift,
    _open_netcdf,
)
from app.services.source_estimation import (
    SourceEstimationError,
    compute_source_estimate_for_spill,
    run_backward_drift,
)

_ERA5_T0 = 1090608   # 2024-06-01 00:00:00
_CMEMS_T0 = 27180   # 2024-06-01


def _create_test_era5_nc(
    path: Path | str,
    *,
    lats: list[float] | None = None,
    lons: list[float] | None = None,
    u_val: float = 2.0,
    v_val: float = 1.0,
) -> None:
    lats = lats or [52.0, 51.75, 51.5, 51.25, 51.0]
    lons = lons or [2.0, 2.25, 2.5, 2.75, 3.0]
    times = [_ERA5_T0 + i for i in range(48)]

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


def _create_test_cmems_nc(
    path: Path | str,
    *,
    lats: list[float] | None = None,
    lons: list[float] | None = None,
    u_val: float = 0.5,
    v_val: float = -0.2,
) -> None:
    lats = lats or [51.0, 51.25, 51.5, 51.75, 52.0]
    lons = lons or [2.0, 2.25, 2.5, 2.75, 3.0]
    times = [_CMEMS_T0 + i for i in range(5)]
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


class MockDomainChecker:
    """Configurable domain checker for unit tests."""

    def __init__(self, allowed_predicate=None) -> None:
        self.allowed_predicate = allowed_predicate or (lambda lon, lat: True)

    def is_maritime(self, lon: float, lat: float) -> bool:
        return self.allowed_predicate(lon, lat)


class TestCoastalRobustness(unittest.TestCase):
    """Test suite covering requirements A through J."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.wind_path = Path(self.tmp_dir.name) / "wind.nc"
        self.curr_path = Path(self.tmp_dir.name) / "curr.nc"
        _create_test_era5_nc(self.wind_path, u_val=2.0, v_val=1.0)
        _create_test_cmems_nc(self.curr_path, u_val=0.4, v_val=-0.2)

        self.w_asset = Asset(
            id="w1",
            investigation_id="inv1",
            type=AssetType.ENVIRONMENT_WIND,
            provider="era5",
            source="era5",
            location=str(self.wind_path),
        )
        self.c_asset = Asset(
            id="c1",
            investigation_id="inv1",
            type=AssetType.ENVIRONMENT_CURRENT,
            provider="cmems",
            source="cmems",
            location=str(self.curr_path),
        )
        self.obs_time = datetime.datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.registry = InMemoryAssetRegistry()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_A_open_ocean_current_plus_wind(self) -> None:
        """A. Open-ocean current + wind -> forcing_mode = current_plus_windage."""
        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd1",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.w_asset,
            current_asset=self.c_asset,
            drift_hours=3.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(result.termination_status, "completed")
        self.assertEqual(result.forcing_mode, "current_plus_windage")
        self.assertEqual(result.forcing_modes, ["current_plus_windage"])
        self.assertFalse(result.current_fallback_used)
        self.assertEqual(len(result.steps), 3)
        for step in result.steps:
            self.assertEqual(step.forcing_mode, "current_plus_windage")
            self.assertEqual(step.current_source, "CMEMS")
            self.assertFalse(step.current_fallback)
            self.assertIsNotNone(step.u_current_ms)

    def test_B_coastal_cmems_nan_fallback(self) -> None:
        """B. Coastal CMEMS NaN + valid wind -> continues with wind_only_leeway and current_fallback_used=True."""
        nan_curr_path = Path(self.tmp_dir.name) / "curr_nan.nc"
        _create_test_cmems_nc(nan_curr_path, u_val=np.nan, v_val=np.nan)
        c_nan_asset = self.c_asset.model_copy(update={"location": str(nan_curr_path)})

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_b",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.w_asset,
            current_asset=c_nan_asset,
            drift_hours=3.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(result.termination_status, "completed")
        self.assertEqual(result.forcing_mode, "wind_only_leeway")
        self.assertEqual(result.forcing_modes, ["wind_only_leeway"])
        self.assertTrue(result.current_fallback_used)
        self.assertEqual(len(result.steps), 3)
        self.assertIn("degraded_forcing_note", result.metadata)

        for step in result.steps:
            self.assertEqual(step.forcing_mode, "wind_only_leeway")
            self.assertEqual(step.current_source, "unavailable")
            self.assertTrue(step.current_fallback)
            self.assertIsNone(step.u_current_ms)
            self.assertIsNone(step.v_current_ms)
            # Drift velocity should equal pure leeway * wind
            self.assertAlmostEqual(step.drift_u_ms, 0.035 * step.u_wind_ms, places=4)
            self.assertAlmostEqual(step.drift_v_ms, 0.035 * step.v_wind_ms, places=4)

    def test_C_wind_nan_environmental_failure(self) -> None:
        """C. Wind NaN + current valid -> environmental_failure."""
        nan_wind_path = Path(self.tmp_dir.name) / "wind_nan.nc"
        _create_test_era5_nc(nan_wind_path, u_val=np.nan, v_val=np.nan)
        w_nan_asset = self.w_asset.model_copy(update={"location": str(nan_wind_path)})

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_c",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_nan_asset,
            current_asset=self.c_asset,
            drift_hours=3.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(result.termination_status, "environmental_failure")
        self.assertEqual(len(result.steps), 0)
        self.assertIn("termination_reason", result.metadata)

    def test_D_both_wind_current_nan_environmental_failure(self) -> None:
        """D. Both wind and current NaN -> environmental_failure."""
        nan_wind_path = Path(self.tmp_dir.name) / "wind_nan2.nc"
        nan_curr_path = Path(self.tmp_dir.name) / "curr_nan2.nc"
        _create_test_era5_nc(nan_wind_path, u_val=np.nan, v_val=np.nan)
        _create_test_cmems_nc(nan_curr_path, u_val=np.nan, v_val=np.nan)
        w_nan_asset = self.w_asset.model_copy(update={"location": str(nan_wind_path)})
        c_nan_asset = self.c_asset.model_copy(update={"location": str(nan_curr_path)})

        result, _ = compute_drift_for_spill(
            investigation_id="inv1",
            spill_detection_id="sd_d",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=w_nan_asset,
            current_asset=c_nan_asset,
            drift_hours=3.0,
            registry=self.registry,
            output_dir=self.tmp_dir.name,
        )
        self.assertEqual(result.termination_status, "environmental_failure")

    def test_E_starting_point_outside_maritime_mask(self) -> None:
        """E. Starting point outside maritime mask -> structured domain failure (MaritimeDomainError)."""
        ds_w = _open_netcdf(str(self.wind_path))
        ds_c = _open_netcdf(str(self.curr_path))

        # Checker that denies the starting point (e.g. on land)
        land_checker = MockDomainChecker(allowed_predicate=lambda lon, lat: False)

        with self.assertRaises(MaritimeDomainError) as ctx:
            run_forward_drift(
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_ds=ds_w,
                curr_ds=ds_c,
                domain_checker=land_checker,
            )
        self.assertIn("outside the maritime domain", str(ctx.exception))

        with self.assertRaises(MaritimeDomainError) as ctx_d3:
            run_backward_drift(
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_ds=ds_w,
                curr_ds=ds_c,
                domain_checker=land_checker,
            )
        self.assertIn("outside the maritime domain", str(ctx_d3.exception))
        ds_w.close()
        ds_c.close()

    def test_F_forward_trajectory_enters_land(self) -> None:
        """F. Forward trajectory enters land -> shoreline_reached, invalid position not included."""
        ds_w = _open_netcdf(str(self.wind_path))
        ds_c = _open_netcdf(str(self.curr_path))

        # Origin is maritime, but any step after 1 step is land
        step_count = 0
        def land_after_one_step(lon, lat):
            nonlocal step_count
            step_count += 1
            # Step 1 is origin check (True); Step 2 is first displacement (True); Step 3 is land (False)
            return step_count <= 2

        checker = MockDomainChecker(allowed_predicate=land_after_one_step)

        traj = run_forward_drift(
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_ds=ds_w,
            curr_ds=ds_c,
            drift_hours=6.0,
            step_hours=1.0,
            domain_checker=checker,
        )
        self.assertEqual(traj.termination_status, "shoreline_reached")
        self.assertEqual(len(traj), 1)  # Only the valid maritime step was included
        ds_w.close()
        ds_c.close()

    def test_G_backward_trajectory_enters_land(self) -> None:
        """G. Backward trajectory enters land -> shoreline_boundary_reached, invalid position not included."""
        ds_w = _open_netcdf(str(self.wind_path))
        ds_c = _open_netcdf(str(self.curr_path))

        step_count = 0
        def land_after_two_steps(lon, lat):
            nonlocal step_count
            step_count += 1
            # Origin check (1) + 2 steps (2, 3) = allowed; step 4 = land
            return step_count <= 3

        checker = MockDomainChecker(allowed_predicate=land_after_two_steps)

        traj = run_backward_drift(
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_ds=ds_w,
            curr_ds=ds_c,
            lookback_hours=6.0,
            step_hours=1.0,
            domain_checker=checker,
        )
        self.assertEqual(traj.termination_status, "shoreline_boundary_reached")
        self.assertEqual(len(traj), 2)
        ds_w.close()
        ds_c.close()

    def test_H_environmental_bounding_box_exit(self) -> None:
        """H. Environmental bounding-box exit -> domain_exit, no unhandled ValueError."""
        # Strong eastward wind blowing trajectory outside the grid [2.0, 3.0]
        strong_wind_path = Path(self.tmp_dir.name) / "wind_strong.nc"
        _create_test_era5_nc(strong_wind_path, u_val=50.0, v_val=0.0)
        ds_w = _open_netcdf(str(strong_wind_path))
        ds_c = _open_netcdf(str(self.curr_path))

        permissive_checker = MockDomainChecker(allowed_predicate=lambda lon, lat: True)

        traj = run_forward_drift(
            origin_lon=2.9,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_ds=ds_w,
            curr_ds=ds_c,
            drift_hours=6.0,
            step_hours=1.0,
            domain_checker=permissive_checker,
        )
        # Should exit domain cleanly rather than raise unhandled ValueError
        self.assertEqual(traj.termination_status, "domain_exit")
        self.assertGreater(len(traj), 0)
        self.assertLess(len(traj), 6)
        ds_w.close()
        ds_c.close()

    def test_I_open_ocean_ulysse_benchmark(self) -> None:
        """I. Entire open-ocean Ulysse benchmark still succeeds with 100% current_plus_windage."""
        era5_real = list(Path("acquisitions/real-experiment/era5").rglob("*wind*.nc"))
        cmems_real = list(Path("acquisitions/real-experiment/cmems").rglob("*currents*.nc"))
        if not era5_real or not cmems_real:
            self.skipTest("Real experiment NetCDF datasets not present")

        ds_w = _open_netcdf(str(era5_real[0]))
        ds_c = _open_netcdf(str(cmems_real[0]))
        obs_t = datetime.datetime(2018, 10, 9, 6, 0, 0, tzinfo=timezone.utc)

        # Ulysse location: (9.47833 E, 43.24833 N)
        traj = run_forward_drift(
            origin_lon=9.47833,
            origin_lat=43.24833,
            observation_time=obs_t,
            wind_ds=ds_w,
            curr_ds=ds_c,
            drift_hours=6.0,
            step_hours=1.0,
        )
        self.assertEqual(traj.termination_status, "completed")
        self.assertEqual(len(traj), 6)
        self.assertEqual(traj.forcing_modes, ["current_plus_windage"])
        self.assertFalse(traj.current_fallback_used)
        ds_w.close()
        ds_c.close()

    def test_J_real_candidates_execute_robustly(self) -> None:
        """J. Real coastal candidates 1–4 execute robustly without fatal crashes."""
        era5_real = list(Path("acquisitions/real-experiment/era5").rglob("*wind*.nc"))
        cmems_real = list(Path("acquisitions/real-experiment/cmems").rglob("*currents*.nc"))
        if not era5_real or not cmems_real:
            self.skipTest("Real experiment NetCDF datasets not present")

        ds_w = _open_netcdf(str(era5_real[0]))
        ds_c = _open_netcdf(str(cmems_real[0]))
        obs_t = datetime.datetime(2018, 10, 9, 6, 0, 0, tzinfo=timezone.utc)

        candidates = [
            ("Candidate 1", 8.198, 44.021),
            ("Candidate 2", 8.465, 44.258),
            ("Candidate 3", 8.627, 44.379),
            ("Candidate 4", 9.215, 44.316),
        ]

        for name, lon, lat in candidates:
            traj = run_forward_drift(
                origin_lon=lon,
                origin_lat=lat,
                observation_time=obs_t,
                wind_ds=ds_w,
                curr_ds=ds_c,
                drift_hours=6.0,
                step_hours=1.0,
            )
            # Trajectory must execute without raising unhandled exception
            self.assertIn(traj.termination_status, ("completed", "shoreline_reached", "domain_exit"))
            self.assertGreater(len(traj), 0)

        ds_w.close()
        ds_c.close()


if __name__ == "__main__":
    unittest.main()
