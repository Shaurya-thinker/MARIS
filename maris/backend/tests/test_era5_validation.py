"""Tests for Stage A4.3 — ERA5 10 m Wind NetCDF Validation.

All tests are offline.  No CDS credentials, no network access.
Temporary NetCDF fixtures are built in-memory using netCDF4.
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
from app.validation import Era5Validator, ValidationSeverity
from app.validation.era5 import (
    VALIDATOR_NAME,
    VALIDATOR_VERSION,
    _CLASS_INVALID,
    _CLASS_PREFERRED,
    _CLASS_USABLE,
)

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING

# ---------------------------------------------------------------------------
# NetCDF fixture helpers
# ---------------------------------------------------------------------------

_FILL = np.float32(9.96921e+36)
# hours since 1900-01-01 for 2024-06-01 00:00 UTC
_T0 = 1085784


def _make_nc(
    path: str,
    *,
    lat: list[float] | None = None,
    lon: list[float] | None = None,
    times: list[int] | None = None,
    u_data: np.ndarray | None = None,
    v_data: np.ndarray | None = None,
    u_units: str | None = "m s**-1",
    v_units: str | None = "m s**-1",
    include_u: bool = True,
    include_v: bool = True,
    include_time: bool = True,
    include_lat: bool = True,
    include_lon: bool = True,
) -> None:
    """Write a minimal ERA5-shaped NetCDF to *path*."""
    lat = lat if lat is not None else [52.0, 51.75, 51.5, 51.25, 51.0]
    lon = lon if lon is not None else [2.0, 2.25, 2.5, 2.75, 3.0]
    times = times if times is not None else [_T0 + i for i in range(6)]

    nlat, nlon, nt = len(lat), len(lon), len(times)

    ds = nc.Dataset(path, "w", format="NETCDF4_CLASSIC")
    ds.createDimension("time", nt)
    ds.createDimension("latitude", nlat)
    ds.createDimension("longitude", nlon)

    if include_time:
        tv = ds.createVariable("time", "i4", ("time",))
        tv.units = "hours since 1900-01-01 00:00:00.0"
        tv.long_name = "time"
        tv.calendar = "gregorian"
        tv[:] = times

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

    shape = (nt, nlat, nlon)

    if include_u:
        u10 = ds.createVariable("u10", "f4", ("time", "latitude", "longitude"),
                                fill_value=_FILL)
        if u_units is not None:
            u10.units = u_units
        u10.long_name = "10 metre U wind component"
        u10.standard_name = "eastward_wind"
        u10[:] = u_data if u_data is not None else np.full(shape, 2.5, dtype=np.float32)

    if include_v:
        v10 = ds.createVariable("v10", "f4", ("time", "latitude", "longitude"),
                                fill_value=_FILL)
        if v_units is not None:
            v10.units = v_units
        v10.long_name = "10 metre V wind component"
        v10.standard_name = "northward_wind"
        v10[:] = v_data if v_data is not None else np.full(shape, 1.5, dtype=np.float32)

    ds.Conventions = "CF-1.6"
    ds.close()


def _artifact(path: str) -> AcquiredArtifact:
    return AcquiredArtifact(
        asset_type=AssetType.ENVIRONMENT_WIND,
        location=path,
        source="copernicus-climate-data-store",
        provenance=Provenance(product_id="reanalysis-era5-single-levels"),
    )


def _validator() -> Era5Validator:
    return Era5Validator()


def _codes(result) -> list[str]:
    return [i.code for i in result.issues]


# ---------------------------------------------------------------------------
# Validator interface
# ---------------------------------------------------------------------------

class Era5ValidatorInterfaceTests(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(_validator().name, VALIDATOR_NAME)

    def test_version(self) -> None:
        self.assertEqual(_validator().version, VALIDATOR_VERSION)

    def test_is_scientific_validator(self) -> None:
        from app.validation import ScientificValidator
        self.assertIsInstance(_validator(), ScientificValidator)

    def test_exported_from_package(self) -> None:
        from app.validation import Era5Validator as V
        self.assertIs(V, Era5Validator)


# ---------------------------------------------------------------------------
# Artifact readability
# ---------------------------------------------------------------------------

class ArtifactReadabilityTests(unittest.TestCase):
    def test_missing_file_fails(self) -> None:
        result = _validator().validate(_artifact("/nonexistent/era5.nc"))
        self.assertFalse(result.passed)
        self.assertIn("ERA5_ARTIFACT_NOT_FOUND", _codes(result))

    def test_empty_file_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_ARTIFACT_EMPTY", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_corrupt_file_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            f.write(b"not a netcdf file at all")
            path = f.name
        try:
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_NETCDF_UNREADABLE", _codes(result))
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
    def test_valid_era5_netcdf_passes(self) -> None:
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

    def test_u10_v10_recognized(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata.get("identified_u_component"), "u10")
            self.assertEqual(result.metadata.get("identified_v_component"), "v10")
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Wind variable presence
# ---------------------------------------------------------------------------

class WindVariableTests(unittest.TestCase):
    def test_missing_u10_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_u=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_U10_MISSING", _codes(result))
            self.assertIsNone(result.metadata.get("identified_u_component"))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_v10_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_v=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_V10_MISSING", _codes(result))
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
            self.assertIn("ERA5_TIME_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_latitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_lat=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_LATITUDE_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_longitude_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, include_lon=False)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_LONGITUDE_MISSING", _codes(result))
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
            _make_nc(path, lat=[95.0, 51.5, 51.0])
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_LATITUDE_OUT_OF_RANGE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_descending_latitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[52.0, 51.5, 51.0])  # descending — normal ERA5
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("ERA5_LATITUDE_NON_MONOTONIC", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_ascending_latitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[51.0, 51.5, 52.0])  # ascending
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("ERA5_LATITUDE_NON_MONOTONIC", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_monotonic_latitude_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[52.0, 51.0, 51.5, 50.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_LATITUDE_NON_MONOTONIC", _codes(result))
            w = [i for i in result.issues if i.code == "ERA5_LATITUDE_NON_MONOTONIC"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_spatial_extent_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lat=[52.0, 51.5, 51.0])
            result = _validator().validate(_artifact(path))
            self.assertAlmostEqual(result.metadata["latitude_min"], 51.0)
            self.assertAlmostEqual(result.metadata["latitude_max"], 52.0)
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
            _make_nc(path, lon=[200.0, 2.5, 3.0])
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_LONGITUDE_OUT_OF_RANGE", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_ascending_longitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[2.0, 2.5, 3.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("ERA5_LONGITUDE_NON_MONOTONIC", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_descending_longitude_accepted(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[3.0, 2.5, 2.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("ERA5_LONGITUDE_NON_MONOTONIC", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_monotonic_longitude_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[2.0, 3.0, 2.5, 4.0])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_LONGITUDE_NON_MONOTONIC", _codes(result))
            w = [i for i in result.issues if i.code == "ERA5_LONGITUDE_NON_MONOTONIC"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_longitude_extent_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, lon=[2.0, 2.5, 3.0])
            result = _validator().validate(_artifact(path))
            self.assertAlmostEqual(result.metadata["longitude_min"], 2.0)
            self.assertAlmostEqual(result.metadata["longitude_max"], 3.0)
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
            self.assertNotIn("ERA5_TIME_NON_MONOTONIC", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_non_monotonic_time_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, times=[_T0, _T0 + 2, _T0 + 1])
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_TIME_NON_MONOTONIC", _codes(result))
            w = [i for i in result.issues if i.code == "ERA5_TIME_NON_MONOTONIC"][0]
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
            # start should differ from end
            self.assertNotEqual(result.metadata["time_start"], result.metadata["time_end"])
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Units validation
# ---------------------------------------------------------------------------

class UnitsTests(unittest.TestCase):
    def test_canonical_units_pass(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units="m s**-1", v_units="m s**-1")
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("ERA5_U10_UNITS_UNEXPECTED", _codes(result))
            self.assertNotIn("ERA5_V10_UNITS_UNEXPECTED", _codes(result))
            self.assertEqual(result.metadata.get("u_units"), "m s**-1")
            self.assertEqual(result.metadata.get("v_units"), "m s**-1")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_ms_slash_units_pass(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units="m/s", v_units="m/s")
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertNotIn("ERA5_U10_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_units_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path, u_units=None, v_units=None)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_U10_UNITS_MISSING", _codes(result))
            self.assertIn("ERA5_V10_UNITS_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "ERA5_U10_UNITS_MISSING"][0]
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
            self.assertIn("ERA5_U10_UNITS_UNEXPECTED", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Missing / non-finite wind data
# ---------------------------------------------------------------------------

class WindDataTests(unittest.TestCase):
    def test_all_missing_u10_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (6, 5, 5)
            all_fill = np.full(shape, float(_FILL), dtype=np.float32)
            _make_nc(path, u_data=all_fill)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_U10_ALL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_all_missing_v10_fails(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (6, 5, 5)
            all_fill = np.full(shape, float(_FILL), dtype=np.float32)
            _make_nc(path, v_data=all_fill)
            result = _validator().validate(_artifact(path))
            self.assertFalse(result.passed)
            self.assertIn("ERA5_V10_ALL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_partial_missing_u10_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (6, 5, 5)
            arr = np.full(shape, 2.5, dtype=np.float32)
            arr[0, 0, 0] = float(_FILL)  # one fill value
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_U10_PARTIAL_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "ERA5_U10_PARTIAL_MISSING"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_partial_missing_v10_warns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (6, 5, 5)
            arr = np.full(shape, 1.5, dtype=np.float32)
            arr[0, 0, 0] = float(_FILL)
            _make_nc(path, v_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_V10_PARTIAL_MISSING", _codes(result))
        finally:
            Path(path).unlink(missing_ok=True)

    def test_inf_values_warn(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (6, 5, 5)
            arr = np.full(shape, 2.5, dtype=np.float32)
            arr[0, 0, 0] = np.inf
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertTrue(result.passed)
            self.assertIn("ERA5_U10_NON_FINITE", _codes(result))
            w = [i for i in result.issues if i.code == "ERA5_U10_NON_FINITE"][0]
            self.assertEqual(w.severity, _WARN)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_counts_in_metadata(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            shape = (6, 5, 5)
            arr = np.full(shape, 2.5, dtype=np.float32)
            arr[0, 0, 0] = float(_FILL)
            arr[0, 0, 1] = float(_FILL)
            _make_nc(path, u_data=arr)
            result = _validator().validate(_artifact(path))
            self.assertEqual(result.metadata.get("u_missing_count"), 2)
            self.assertEqual(result.metadata.get("u_total_count"), 150)
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
            self.assertNotIn("ERA5_U10_PARTIAL_MISSING", _codes(result))
            self.assertNotIn("ERA5_V10_PARTIAL_MISSING", _codes(result))
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
        result = _validator().validate(_artifact("/nonexistent/era5.nc"))
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
            _make_nc(path, lat=[52.0, 51.0, 51.5, 50.0])  # non-monotonic → WARNING
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

    def test_full_metadata_populated(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as f:
            path = f.name
        try:
            _make_nc(path)
            result = _validator().validate(_artifact(path))
            m = result.metadata
            for key in (
                "identified_u_component", "identified_v_component",
                "latitude_min", "latitude_max",
                "longitude_min", "longitude_max",
                "time_start", "time_end",
                "u_units", "v_units",
                "u_missing_count", "v_missing_count",
                "validation_classification",
            ):
                self.assertIn(key, m, f"missing metadata key: {key}")
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
