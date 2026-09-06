"""Tests for Stage A4.4 — CMEMS Near-Surface Current NetCDF Validation.

All tests are offline.  No Copernicus Marine credentials, no network access.
Temporary NetCDF fixtures are built in-memory using netCDF4.

Fixture structure mirrors the confirmed CMEMS / GLORYS12V1 probe output:
  uo, vo   : (time, depth, latitude, longitude) — depth dimension retained
  depth    : size 1, value ≈ 0.494025 m
  time     : days since 1950-01-01 00:00:00, gregorian
  units    : 'm s-1'
  fill     : 9.96921e+36 (exposed as NaN by xarray masking)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np

from app.acquisition.schemas import AcquiredArtifact
from app.models.common import AssetType, Provenance
from app.validation import CmemsValidator, ValidationSeverity
from app.validation.cmems import (
    VALIDATOR_NAME,
    VALIDATOR_VERSION,
    _CLASS_INVALID,
    _CLASS_PREFERRED,
    _CLASS_USABLE,
    _NEAR_SURFACE_DEPTH_M,
    _NEAR_SURFACE_DEPTH_TOLERANCE_M,
)

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING

# ---------------------------------------------------------------------------
# NetCDF fixture helpers
# ---------------------------------------------------------------------------

_FILL = np.float32(9.96921e+36)

# days since 1950-01-01 for 2024-06-01 (= 27180 days)
_T0 = 27180


def _make_nc(
    path: str,
    *,
    lat: list[float] | None = None,
    lon: list[float] | None = None,
    depth: list[float] | None = None,
    times: list[int] | None = None,
    u_data: np.ndarray | None = None,
    v_data: np.ndarray | None = None,
    u_units: str | None = "m s-1",
    v_units: str | None = "m s-1",
    depth_units: str | None = "m",
    depth_positive: str | None = "down",
    include_u: bool = True,
    include_v: bool = True,
    include_time: bool = True,
    include_lat: bool = True,
    include_lon: bool = True,
    include_depth: bool = True,
    global_attrs: dict | None = None,
) -> None:
    """Write a minimal CMEMS-shaped NetCDF to *path*.

    Dimensions: (time, depth, latitude, longitude).
    depth is retained (size 1 by default) matching A3.4 contract.
    """
    lat = lat if lat is not None else [41.0, 41.5, 42.0, 42.5, 43.0,
                                        43.5, 44.0, 44.5, 45.0, 45.5,
                                        46.0, 46.5, 47.0, 47.5]
    lon = lon if lon is not None else [7.0, 7.5, 8.0, 8.5, 9.0,
                                        9.5, 10.0, 10.5, 11.0, 11.5,
                                        12.0, 12.5, 13.0, 13.5]
    depth = depth if depth is not None else [_NEAR_SURFACE_DEPTH_M]
    times = times if times is not None else [_T0, _T0 + 1, _T0 + 2]

    nlat, nlon, ndepth, nt = len(lat), len(lon), len(depth), len(times)
    shape = (nt, ndepth, nlat, nlon)

    ds = nc.Dataset(path, "w", format="NETCDF4_CLASSIC")
    ds.createDimension("time", nt)
    ds.createDimension("depth", ndepth)
    ds.createDimension("latitude", nlat)
    ds.createDimension("longitude", nlon)

    if include_time:
        tv = ds.createVariable("time", "i4", ("time",))
        tv.units = "days since 1950-01-01 00:00:00"
        tv.long_name = "time"
        tv.calendar = "gregorian"
        tv[:] = times

    if include_depth:
        dv = ds.createVariable("depth", "f4", ("depth",))
        if depth_units is not None:
            dv.units = depth_units
        if depth_positive is not None:
            dv.positive = depth_positive
        dv.standard_name = "depth"
        dv.long_name = "Depth"
        dv[:] = depth

    if include_lat:
        lav = ds.createVariable("latitude", "f4", ("latitude",))
        lav.units = "degrees_north"
        lav.long_name = "latitude"
        lav[:] = lat

    if include_lon:
        lov = ds.createVariable("longitude", "f4", ("longitude",))
        lov.units = "degrees_east"
        lov.long_name = "longitude"
        lov[:] = lon

    if include_u:
        uv = ds.createVariable("uo", "f4", ("time", "depth", "latitude", "longitude"),
                                fill_value=_FILL)
        if u_units is not None:
            uv.units = u_units
        uv.long_name = "Eastward sea water velocity"
        uv.standard_name = "eastward_sea_water_velocity"
        uv[:] = u_data if u_data is not None else np.full(shape, 0.2, dtype=np.float32)

    if include_v:
        vv = ds.createVariable("vo", "f4", ("time", "depth", "latitude", "longitude"),
                                fill_value=_FILL)
        if v_units is not None:
            vv.units = v_units
        vv.long_name = "Northward sea water velocity"
        vv.standard_name = "northward_sea_water_velocity"
        vv[:] = v_data if v_data is not None else np.full(shape, 0.1, dtype=np.float32)

    # Dataset-level global attributes (realistic CMEMS metadata)
    ds.Conventions = "CF-1.6"
    ds.institution = "Mercator Ocean International"
    ds.source = "GLORYS12V1"
    ds.product_id = "GLOBAL_MULTIYEAR_PHY_001_030"
    ds.dataset_id = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
    ds.processing_level = "L4"

    if global_attrs:
        for k, v in global_attrs.items():
            setattr(ds, k, v)

    ds.close()


def _artifact(path: str) -> AcquiredArtifact:
    return AcquiredArtifact(
        asset_type=AssetType.ENVIRONMENT_CURRENT,
        location=path,
        source="copernicus-marine-service",
        provenance=Provenance(
            product_id="cmems_mod_glo_phy_my_0.083deg_P1D-m",
            extra={
                "product_id": "GLOBAL_MULTIYEAR_PHY_001_030",
                "variables": ["uo", "vo"],
            },
        ),
    )


def _validator() -> CmemsValidator:
    return CmemsValidator()


def _codes(result) -> list[str]:
    return [i.code for i in result.issues]


# ---------------------------------------------------------------------------
# Validator interface
# ---------------------------------------------------------------------------

class CmemsValidatorInterfaceTests(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(_validator().name, VALIDATOR_NAME)

    def test_version(self) -> None:
        self.assertEqual(_validator().version, VALIDATOR_VERSION)

    def test_is_scientific_validator(self) -> None:
        from app.validation import ScientificValidator
        self.assertIsInstance(_validator(), ScientificValidator)

    def test_exported_from_package(self) -> None:
        from app.validation import CmemsValidator as V
        self.assertIs(V, CmemsValidator)


# ---------------------------------------------------------------------------
# Artifact readability
# ---------------------------------------------------------------------------

class ArtifactReadabilityTests(unittest.TestCase):
    def test_missing_file_fails(self) -> None:
        result = _validator().validate(_artifact("/nonexistent/cmems.nc"))
        self.assertFalse(result.passed)
        self.assertIn("CMEMS_ARTIFACT_NOT_FOUND", _codes(result))

    def test_empty_file_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_ARTIFACT_EMPTY", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_corrupt_file_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            f.write(b"not a netcdf file at all")
            path = f.name
        try:
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_NETCDF_UNREADABLE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_source_file_unchanged_after_validation(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            size_before = Path(path).stat().st_size
            mtime_before = Path(path).stat().st_mtime
            _validator().validate(_artifact(path))
            self.assertEqual(Path(path).stat().st_size, size_before)
            self.assertEqual(Path(path).stat().st_mtime, mtime_before)
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Valid product passes
# ---------------------------------------------------------------------------

class ValidProductTests(unittest.TestCase):
    def test_valid_cmems_netcdf_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_PREFERRED)
            self.assertEqual(result.issues, [])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_uo_vo_recognized(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata.get("identified_u_component"), "uo")
            self.assertEqual(result.metadata.get("identified_v_component"), "vo")
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Current variable presence
# ---------------------------------------------------------------------------

class CurrentVariableTests(unittest.TestCase):
    def test_missing_uo_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_u=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_UO_MISSING", _codes(result))
            self.assertIsNone(result.metadata.get("identified_u_component"))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_vo_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_v=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_VO_MISSING", _codes(result))
            self.assertIsNone(result.metadata.get("identified_v_component"))
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Coordinate presence
# ---------------------------------------------------------------------------

class CoordinatePresenceTests(unittest.TestCase):
    def test_missing_time_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_time=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_TIME_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_latitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_lat=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_LATITUDE_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_longitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_lon=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_LONGITUDE_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_depth_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_depth=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_DEPTH_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Latitude validation
# ---------------------------------------------------------------------------

class LatitudeTests(unittest.TestCase):
    def test_invalid_latitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[95.0, 41.5, 42.0])
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_LATITUDE_OUT_OF_RANGE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_finite_latitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[float("nan"), 41.5, 42.0])
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_LATITUDE_NON_FINITE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_ascending_latitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[41.0, 41.5, 42.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_LATITUDE_NON_MONOTONIC", _codes(result))
            self.assertEqual(result.metadata.get("latitude_order"), "ascending")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_descending_latitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[42.0, 41.5, 41.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_LATITUDE_NON_MONOTONIC", _codes(result))
            self.assertEqual(result.metadata.get("latitude_order"), "descending")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_monotonic_latitude_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[42.0, 41.0, 41.5, 40.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_LATITUDE_NON_MONOTONIC", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_LATITUDE_NON_MONOTONIC"][0]
            self.assertEqual(w.severity, _WARN)
            self.assertEqual(result.metadata.get("latitude_order"), "non_monotonic")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_spatial_extent_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[41.0, 41.5, 42.0])
            result = _validator().validate(_artifact(path))
            self.assertAlmostEqual(result.metadata["latitude_min"], 41.0, places=4)
            self.assertAlmostEqual(result.metadata["latitude_max"], 42.0, places=4)
            self.assertEqual(result.metadata["latitude_count"], 3)
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Longitude validation
# ---------------------------------------------------------------------------

class LongitudeTests(unittest.TestCase):
    def test_invalid_longitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[200.0, 8.0, 9.0])
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_LONGITUDE_OUT_OF_RANGE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_finite_longitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[float("inf"), 8.0, 9.0])
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_LONGITUDE_NON_FINITE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_ascending_longitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[7.0, 8.0, 9.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_LONGITUDE_NON_MONOTONIC", _codes(result))
            self.assertEqual(result.metadata.get("longitude_order"), "ascending")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_descending_longitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[9.0, 8.0, 7.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_LONGITUDE_NON_MONOTONIC", _codes(result))
            self.assertEqual(result.metadata.get("longitude_order"), "descending")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_monotonic_longitude_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[7.0, 9.0, 8.0, 10.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_LONGITUDE_NON_MONOTONIC", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_LONGITUDE_NON_MONOTONIC"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_longitude_extent_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[7.0, 8.0, 9.0])
            result = _validator().validate(_artifact(path))
            self.assertAlmostEqual(result.metadata["longitude_min"], 7.0, places=4)
            self.assertAlmostEqual(result.metadata["longitude_max"], 9.0, places=4)
            self.assertEqual(result.metadata["longitude_count"], 3)
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Depth validation
# ---------------------------------------------------------------------------

class DepthTests(unittest.TestCase):
    def test_depth_coordinate_retained(self) -> None:
        """Depth dimension must not be squeezed away."""
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            # uo/vo should have depth in their dims
            self.assertIn("depth", result.metadata.get("u_dims", []))
            self.assertIn("depth", result.metadata.get("v_dims", []))
            self.assertEqual(result.metadata.get("depth_count"), 1)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_expected_near_surface_depth_recognized(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, depth=[_NEAR_SURFACE_DEPTH_M])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertTrue(result.metadata.get("near_surface_depth_recognized"))
            self.assertAlmostEqual(
                result.metadata.get("near_surface_depth", -1),
                _NEAR_SURFACE_DEPTH_M,
                places=4,
            )
        finally:
            Path(path).unlink(missing_ok=True)

    def test_depth_not_near_surface_warns(self) -> None:
        """A valid depth coordinate that differs from expected first level → WARNING."""
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            # Use a depth clearly outside the tolerance
            _make_nc(path, depth=[10.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)  # only a WARNING
            self.assertIn("CMEMS_DEPTH_NOT_NEAR_SURFACE", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_DEPTH_NOT_NEAR_SURFACE"][0]
            self.assertEqual(w.severity, _WARN)
            self.assertFalse(result.metadata.get("near_surface_depth_recognized"))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_depth_within_tolerance_recognized(self) -> None:
        """Small floating-point deviations within tolerance should still be recognized."""
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            # Slightly perturbed but within tolerance
            _make_nc(path, depth=[_NEAR_SURFACE_DEPTH_M + 0.005])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.metadata.get("near_surface_depth_recognized"))
            self.assertNotIn("CMEMS_DEPTH_NOT_NEAR_SURFACE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_valid_depth_units(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, depth_units="m")
            result = _validator().validate(_artifact(path))
            self.assertNotIn("CMEMS_DEPTH_UNITS_UNEXPECTED", _codes(result))
            self.assertNotIn("CMEMS_DEPTH_UNITS_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_depth_units_metres_alternative_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, depth_units="meters")
            result = _validator().validate(_artifact(path))
            self.assertNotIn("CMEMS_DEPTH_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_depth_units_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, depth_units=None)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_DEPTH_UNITS_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_DEPTH_UNITS_MISSING"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_incorrect_depth_units_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, depth_units="feet")
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_DEPTH_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_depth_min_max_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertIn("depth_min", result.metadata)
            self.assertIn("depth_max", result.metadata)
            self.assertIn("depth_count", result.metadata)
            self.assertIn("depth_units", result.metadata)
            self.assertIn("depth_positive", result.metadata)
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Time validation
# ---------------------------------------------------------------------------

class TimeTests(unittest.TestCase):
    def test_valid_time_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, times=[_T0, _T0 + 1, _T0 + 2])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_TIME_NON_MONOTONIC", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_monotonic_time_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, times=[_T0, _T0 + 2, _T0 + 1])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_TIME_NON_MONOTONIC", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_TIME_NON_MONOTONIC"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_temporal_extent_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, times=[_T0, _T0 + 1, _T0 + 2])
            result = _validator().validate(_artifact(path))
            self.assertIn("time_start", result.metadata)
            self.assertIn("time_end", result.metadata)
            self.assertIn("time_count", result.metadata)
            self.assertEqual(result.metadata["time_count"], 3)
            # start should differ from end
            self.assertNotEqual(result.metadata["time_start"], result.metadata["time_end"])
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Units validation
# ---------------------------------------------------------------------------

class UnitsTests(unittest.TestCase):
    def test_canonical_cmems_units_pass(self) -> None:
        """'m s-1' is the canonical CMEMS units string."""
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units="m s-1", v_units="m s-1")
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_UO_UNITS_UNEXPECTED", _codes(result))
            self.assertNotIn("CMEMS_VO_UNITS_UNEXPECTED", _codes(result))
            self.assertEqual(result.metadata.get("u_units"), "m s-1")
            self.assertEqual(result.metadata.get("v_units"), "m s-1")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_equivalent_units_ms_slash_pass(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units="m/s", v_units="m/s")
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_UO_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_equivalent_units_m_s_star_star_pass(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units="m s**-1", v_units="m s**-1")
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("CMEMS_UO_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_units_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units=None, v_units=None)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_UO_UNITS_MISSING", _codes(result))
            self.assertIn("CMEMS_VO_UNITS_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_UO_UNITS_MISSING"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_unexpected_units_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units="knots", v_units="knots")
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_UO_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_coordinate_units_recorded(self) -> None:
        """Latitude, longitude, and depth units are recorded in metadata."""
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata.get("latitude_units"), "degrees_north")
            self.assertEqual(result.metadata.get("longitude_units"), "degrees_east")
            self.assertEqual(result.metadata.get("depth_units"), "m")
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Missing / non-finite current data
# ---------------------------------------------------------------------------

class CurrentDataTests(unittest.TestCase):
    def test_all_missing_uo_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            all_fill = np.full(shape, float(_FILL), dtype=np.float32)
            _make_nc(path, u_data=all_fill)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_UO_ALL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_all_missing_vo_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            all_fill = np.full(shape, float(_FILL), dtype=np.float32)
            _make_nc(path, v_data=all_fill)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("CMEMS_VO_ALL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_partial_missing_uo_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            arr = np.full(shape, 0.2, dtype=np.float32)
            arr[0, 0, 0, 0] = float(_FILL)  # one fill value
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_UO_PARTIAL_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_UO_PARTIAL_MISSING"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_partial_missing_vo_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            arr = np.full(shape, 0.1, dtype=np.float32)
            arr[0, 0, 0, 0] = float(_FILL)
            _make_nc(path, v_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_VO_PARTIAL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_fill_value_appears_as_nan_not_sentinel(self) -> None:
        """xarray masking converts _FillValue (9.96921e+36) to NaN.

        The validator must detect these as NaN via xarray masking, not by
        comparing raw sentinel values.  We verify by checking the missing count
        matches the number of sentinel-valued cells we inserted.
        """
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            arr = np.full(shape, 0.2, dtype=np.float32)
            arr[0, 0, 0, 0] = float(_FILL)
            arr[0, 0, 0, 1] = float(_FILL)
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            # Should be detected as 2 missing (NaN after xarray masking)
            self.assertEqual(result.metadata.get("u_missing_count"), 2)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_inf_values_warn(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            arr = np.full(shape, 0.2, dtype=np.float32)
            arr[0, 0, 0, 0] = np.inf
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_UO_NON_FINITE", _codes(result))
            w = [i for i in result.issues if i.code == "CMEMS_UO_NON_FINITE"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_neg_inf_values_warn(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            arr = np.full(shape, 0.1, dtype=np.float32)
            arr[0, 0, 0, 0] = -np.inf
            _make_nc(path, v_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("CMEMS_VO_NON_FINITE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_counts_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (3, 1, 14, 14)
            arr = np.full(shape, 0.2, dtype=np.float32)
            arr[0, 0, 0, 0] = float(_FILL)
            arr[0, 0, 0, 1] = float(_FILL)
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata.get("u_missing_count"), 2)
            self.assertEqual(result.metadata.get("u_total_count"), 3 * 1 * 14 * 14)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_no_missing_no_issue(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata.get("u_missing_count"), 0)
            self.assertEqual(result.metadata.get("v_missing_count"), 0)
            self.assertNotIn("CMEMS_UO_PARTIAL_MISSING", _codes(result))
            self.assertNotIn("CMEMS_VO_PARTIAL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Variable dimensions / shape metadata
# ---------------------------------------------------------------------------

class VariableDimensionTests(unittest.TestCase):
    def test_variable_dimensions_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertIn("u_dims", result.metadata)
            self.assertIn("v_dims", result.metadata)
            self.assertIn("u_shape", result.metadata)
            self.assertIn("v_shape", result.metadata)
            # All four dimensions must be present (order not enforced)
            u_dims = result.metadata["u_dims"]
            for d in ("time", "depth", "latitude", "longitude"):
                self.assertIn(d, u_dims)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_variable_shape_matches_fixture(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            # default fixture: 3 times, 1 depth, 14 lat, 14 lon
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata["u_shape"], [3, 1, 14, 14])
            self.assertEqual(result.metadata["v_shape"], [3, 1, 14, 14])
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Dataset identity / metadata
# ---------------------------------------------------------------------------

class DatasetMetadataTests(unittest.TestCase):
    def test_dataset_identity_metadata_recorded(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            m = result.metadata
            self.assertEqual(m.get("dataset_product_id"), "GLOBAL_MULTIYEAR_PHY_001_030")
            self.assertEqual(m.get("dataset_dataset_id"), "cmems_mod_glo_phy_my_0.083deg_P1D-m")
            self.assertEqual(m.get("dataset_source"), "GLORYS12V1")
            self.assertEqual(m.get("dataset_institution"), "Mercator Ocean International")
            self.assertEqual(m.get("dataset_processing_level"), "L4")
            self.assertEqual(m.get("dataset_conventions"), "CF-1.6")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_no_credentials_in_result(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            dumped = str(result.model_dump())
            for word in ("password", "api_key", "access_token", "secret"):
                self.assertNotIn(word, dumped.lower())
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Spatial / temporal extent
# ---------------------------------------------------------------------------

class SpatialTemporalExtentTests(unittest.TestCase):
    def test_spatial_extent_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[41.0, 42.0, 43.0], lon=[7.0, 8.0, 9.0])
            result = _validator().validate(_artifact(path))
            self.assertAlmostEqual(result.metadata["latitude_min"], 41.0, places=4)
            self.assertAlmostEqual(result.metadata["latitude_max"], 43.0, places=4)
            self.assertAlmostEqual(result.metadata["longitude_min"], 7.0, places=4)
            self.assertAlmostEqual(result.metadata["longitude_max"], 9.0, places=4)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_temporal_extent_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, times=[_T0, _T0 + 1, _T0 + 2])
            result = _validator().validate(_artifact(path))
            self.assertIn("time_start", result.metadata)
            self.assertIn("time_end", result.metadata)
            self.assertNotEqual(result.metadata["time_start"], result.metadata["time_end"])
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

class ClassificationTests(unittest.TestCase):
    def test_preferred_classification(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_PREFERRED)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_usable_classification_on_warning(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units=None)  # missing units → WARNING
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_USABLE)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_invalid_classification_on_error(self) -> None:
        result = _validator().validate(_artifact("/nonexistent/cmems.nc"))
        self.assertFalse(result.passed)
        self.assertEqual(result.metadata.get("validation_classification"), _CLASS_INVALID)

    def test_error_causes_passed_false(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_u=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_warning_keeps_passed_true(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[42.0, 41.0, 41.5, 40.0])  # non-monotonic → WARNING
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertTrue(any(i.severity == _WARN for i in result.issues))
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Validator metadata
# ---------------------------------------------------------------------------

class ValidatorMetadataTests(unittest.TestCase):
    def test_validator_name_and_version(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.validator_name, VALIDATOR_NAME)
            self.assertEqual(result.validator_version, VALIDATOR_VERSION)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_validated_at_is_utc(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            before = datetime.now(timezone.utc)
            result = _validator().validate(_artifact(path))
            after = datetime.now(timezone.utc)
            self.assertGreaterEqual(result.validated_at, before)
            self.assertLessEqual(result.validated_at, after)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_full_metadata_populated(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            m = result.metadata
            for key in (
                "identified_u_component", "identified_v_component",
                "latitude_min", "latitude_max", "latitude_count", "latitude_order",
                "longitude_min", "longitude_max", "longitude_count", "longitude_order",
                "depth_min", "depth_max", "depth_count", "depth_units", "depth_positive",
                "near_surface_depth", "near_surface_depth_recognized",
                "time_start", "time_end", "time_count",
                "u_units", "v_units",
                "u_missing_count", "v_missing_count",
                "u_nonfinite_count", "v_nonfinite_count",
                "u_dims", "v_dims", "u_shape", "v_shape",
                "validation_classification",
            ):
                self.assertIn(key, m, f"missing metadata key: {key}")
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
