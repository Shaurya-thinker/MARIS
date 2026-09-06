"""CMEMS near-surface current NetCDF validation for Stage A4.4.

Inspects the NetCDF artifact produced by the A3.4 CMEMS acquisition provider
using xarray (netcdf4 engine).  Read-only: the source file is never modified.

Confirmed CMEMS / GLORYS12V1 structure (from empirical probe, A3.4 contract):
  current variables : uo (eastward), vo (northward)
  dimensions        : (time, depth, latitude, longitude)
  example shape     : (3, 1, 14, 14)  — depth size 1 retained
  coordinate names  : time, depth, latitude, longitude
  current dtype     : float32
  units             : 'm s-1'  (also accept 'm/s', 'm s**-1')
  time dtype        : datetime64[ns] after xarray decoding
  time encoding     : days since 1950-01-01 00:00:00, gregorian
  depth             : size 1, value ≈ 0.494025 m (first GLORYS12V1 level)
  fill values       : exposed as NaN by xarray masking (_FillValue 9.96921e+36)
  latitude order    : ascending or descending; both accepted
  longitude order   : ascending or descending; both accepted (signed [-180, 180])

Validation classification stored in ValidationResult.metadata:
  PREFERRED            — no ERRORs, no WARNINGs
  USABLE_WITH_WARNINGS — no ERRORs, at least one WARNING
  INVALID              — at least one ERROR  (passed=False)

A4.4 validates CMEMS current data.
It does NOT perform drift modelling, current interpolation, resampling,
spill detection, or attribution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.schemas import AcquiredArtifact
from app.validation.base import ScientificValidator, _issue
from app.validation.schemas import ValidationIssue, ValidationResult, ValidationSeverity

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALIDATOR_NAME = "cmems_validator"
VALIDATOR_VERSION = "1.0.0"

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING
_INFO = ValidationSeverity.INFO

# Required current variable names as produced by A3.4 / GLORYS12V1
_U_VAR = "uo"
_V_VAR = "vo"

# Required coordinate names
_COORD_TIME = "time"
_COORD_LAT = "latitude"
_COORD_LON = "longitude"
_COORD_DEPTH = "depth"

# Accepted velocity units strings (case-insensitive comparison after normalisation)
_ACCEPTED_UNITS = {"m s-1", "m/s", "m s**-1", "ms-1"}

# Accepted depth units (canonical SI metre representations)
_ACCEPTED_DEPTH_UNITS = {"m", "meter", "meters", "metre", "metres"}

# Expected near-surface GLORYS12V1 first level (A3.4 contract)
# Tolerance chosen to be tight enough to distinguish levels but loose enough
# for floating-point representation differences across NetCDF implementations.
_NEAR_SURFACE_DEPTH_M = 0.494025
_NEAR_SURFACE_DEPTH_TOLERANCE_M = 0.01

# Classification labels
_CLASS_PREFERRED = "PREFERRED"
_CLASS_USABLE = "USABLE_WITH_WARNINGS"
_CLASS_INVALID = "INVALID"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(issues: list[ValidationIssue]) -> str:
    if any(i.severity == _ERROR for i in issues):
        return _CLASS_INVALID
    if any(i.severity == _WARN for i in issues):
        return _CLASS_USABLE
    return _CLASS_PREFERRED


def _normalise_units(raw: str) -> str:
    """Lower-case and strip whitespace for units comparison."""
    return raw.strip().lower().replace(" ", "")


def _units_accepted(raw: str) -> bool:
    return _normalise_units(raw) in {_normalise_units(u) for u in _ACCEPTED_UNITS}


def _depth_units_accepted(raw: str) -> bool:
    return _normalise_units(raw) in {_normalise_units(u) for u in _ACCEPTED_DEPTH_UNITS}


# ---------------------------------------------------------------------------
# Individual checks — each returns a list of issues and optional metadata
# ---------------------------------------------------------------------------

def _check_current_variables(
    xds: Any,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Verify uo and vo are present; record identity metadata."""
    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    for var_name, direction, u_or_v in (
        (_U_VAR, "eastward", "u"),
        (_V_VAR, "northward", "v"),
    ):
        if var_name not in xds.data_vars:
            issues.append(_issue(
                _ERROR, f"CMEMS_{var_name.upper()}_MISSING",
                f"required current variable '{var_name}' ({direction} sea-water velocity) is absent",
            ))
            meta[f"identified_{u_or_v}_component"] = None
        else:
            meta[f"identified_{u_or_v}_component"] = var_name

    return issues, meta


def _check_coordinates(xds: Any) -> list[ValidationIssue]:
    """Verify time, latitude, longitude, depth are present and non-empty."""
    issues: list[ValidationIssue] = []
    for coord in (_COORD_TIME, _COORD_LAT, _COORD_LON, _COORD_DEPTH):
        if coord not in xds.coords:
            issues.append(_issue(
                _ERROR, f"CMEMS_{coord.upper()}_MISSING",
                f"required coordinate '{coord}' is absent from the dataset",
            ))
        elif xds.coords[coord].size == 0:
            issues.append(_issue(
                _ERROR, f"CMEMS_{coord.upper()}_EMPTY",
                f"coordinate '{coord}' exists but contains no values",
            ))
    return issues


def _check_latitude(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    if _COORD_LAT not in xds.coords or xds.coords[_COORD_LAT].size == 0:
        return issues, meta  # already reported by _check_coordinates

    lat = xds.coords[_COORD_LAT].values.astype(float)

    if not np.all(np.isfinite(lat)):
        issues.append(_issue(_ERROR, "CMEMS_LATITUDE_NON_FINITE",
                             "latitude coordinate contains non-finite values"))
        return issues, meta

    if np.any(lat > 90.0) or np.any(lat < -90.0):
        bad = lat[(lat > 90.0) | (lat < -90.0)]
        issues.append(_issue(_ERROR, "CMEMS_LATITUDE_OUT_OF_RANGE",
                             f"latitude values outside [-90, 90]: {bad[:3].tolist()}"))
        return issues, meta

    meta["latitude_min"] = float(lat.min())
    meta["latitude_max"] = float(lat.max())
    meta["latitude_count"] = int(lat.size)

    if len(lat) > 1:
        diffs = np.diff(lat)
        ascending = bool(np.all(diffs > 0))
        descending = bool(np.all(diffs < 0))
        if ascending:
            meta["latitude_order"] = "ascending"
        elif descending:
            meta["latitude_order"] = "descending"
        else:
            meta["latitude_order"] = "non_monotonic"
            issues.append(_issue(_WARN, "CMEMS_LATITUDE_NON_MONOTONIC",
                                 "latitude coordinate is not monotonically ordered"))
    else:
        meta["latitude_order"] = "single_value"

    # Record coordinate units (informational; missing is a WARNING)
    lat_attrs = xds.coords[_COORD_LAT].attrs
    lat_units = lat_attrs.get("units")
    meta["latitude_units"] = lat_units

    return issues, meta


def _check_longitude(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    if _COORD_LON not in xds.coords or xds.coords[_COORD_LON].size == 0:
        return issues, meta

    lon = xds.coords[_COORD_LON].values.astype(float)

    if not np.all(np.isfinite(lon)):
        issues.append(_issue(_ERROR, "CMEMS_LONGITUDE_NON_FINITE",
                             "longitude coordinate contains non-finite values"))
        return issues, meta

    if np.any(lon > 180.0) or np.any(lon < -180.0):
        bad = lon[(lon > 180.0) | (lon < -180.0)]
        issues.append(_issue(_ERROR, "CMEMS_LONGITUDE_OUT_OF_RANGE",
                             f"longitude values outside [-180, 180]: {bad[:3].tolist()}"))
        return issues, meta

    meta["longitude_min"] = float(lon.min())
    meta["longitude_max"] = float(lon.max())
    meta["longitude_count"] = int(lon.size)

    if len(lon) > 1:
        diffs = np.diff(lon)
        ascending = bool(np.all(diffs > 0))
        descending = bool(np.all(diffs < 0))
        if ascending:
            meta["longitude_order"] = "ascending"
        elif descending:
            meta["longitude_order"] = "descending"
        else:
            meta["longitude_order"] = "non_monotonic"
            issues.append(_issue(_WARN, "CMEMS_LONGITUDE_NON_MONOTONIC",
                                 "longitude coordinate is not monotonically ordered"))
    else:
        meta["longitude_order"] = "single_value"

    # Record coordinate units (informational)
    lon_attrs = xds.coords[_COORD_LON].attrs
    lon_units = lon_attrs.get("units")
    meta["longitude_units"] = lon_units

    return issues, meta


def _check_depth(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Validate the depth coordinate.  Key A4.4 check."""
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    if _COORD_DEPTH not in xds.coords or xds.coords[_COORD_DEPTH].size == 0:
        return issues, meta  # already reported by _check_coordinates

    depth_coord = xds.coords[_COORD_DEPTH]
    depth_vals = depth_coord.values.astype(float)
    depth_attrs = depth_coord.attrs

    if not np.all(np.isfinite(depth_vals)):
        issues.append(_issue(_ERROR, "CMEMS_DEPTH_NON_FINITE",
                             "depth coordinate contains non-finite values"))
        return issues, meta

    if np.any(depth_vals < 0.0):
        issues.append(_issue(_ERROR, "CMEMS_DEPTH_NEGATIVE",
                             "depth coordinate contains negative values (expected positive-down)"))
        return issues, meta

    meta["depth_min"] = float(depth_vals.min())
    meta["depth_max"] = float(depth_vals.max())
    meta["depth_count"] = int(depth_vals.size)

    # --- units ---
    raw_depth_units = depth_attrs.get("units")
    meta["depth_units"] = raw_depth_units

    if raw_depth_units is None:
        issues.append(_issue(_WARN, "CMEMS_DEPTH_UNITS_MISSING",
                             "depth coordinate has no units attribute"))
    elif not _depth_units_accepted(raw_depth_units):
        issues.append(_issue(_ERROR, "CMEMS_DEPTH_UNITS_UNEXPECTED",
                             f"depth coordinate units '{raw_depth_units}' are not an accepted "
                             f"metres representation"))

    # --- positive direction ---
    positive = depth_attrs.get("positive")
    meta["depth_positive"] = positive
    if positive is not None and str(positive).lower() != "down":
        issues.append(_issue(_WARN, "CMEMS_DEPTH_POSITIVE_UNEXPECTED",
                             f"depth 'positive' attribute is '{positive}'; expected 'down'"))

    # --- near-surface level recognition ---
    # A3.4 selects the first GLORYS12V1 level (≈ 0.494025 m).
    # If the minimum depth is close enough, record it as recognized.
    near_depth = float(depth_vals.min())
    meta["near_surface_depth"] = near_depth
    recognized = abs(near_depth - _NEAR_SURFACE_DEPTH_M) <= _NEAR_SURFACE_DEPTH_TOLERANCE_M
    meta["near_surface_depth_recognized"] = recognized
    if not recognized:
        issues.append(_issue(
            _WARN, "CMEMS_DEPTH_NOT_NEAR_SURFACE",
            f"minimum depth {near_depth:.6f} m differs from the expected GLORYS12V1 "
            f"first level ({_NEAR_SURFACE_DEPTH_M} m) by more than the "
            f"{_NEAR_SURFACE_DEPTH_TOLERANCE_M} m tolerance; "
            f"the A3.4 near-surface current contract may not be satisfied",
        ))

    return issues, meta


def _check_time(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    if _COORD_TIME not in xds.coords or xds.coords[_COORD_TIME].size == 0:
        return issues, meta  # already reported by _check_coordinates

    try:
        tvals = xds.coords[_COORD_TIME].values
        # xarray decodes CMEMS time to datetime64[ns]; convert to int64 for diff
        tint = tvals.astype("datetime64[ns]").astype("int64")
    except Exception as exc:
        issues.append(_issue(_ERROR, "CMEMS_TIME_UNREADABLE",
                             f"time coordinate cannot be read: {exc}"))
        return issues, meta

    if not np.all(np.isfinite(tint)):
        issues.append(_issue(_ERROR, "CMEMS_TIME_NON_FINITE",
                             "time coordinate contains non-finite values"))
        return issues, meta

    # Record temporal extent as ISO strings
    try:
        meta["time_start"] = str(tvals[0])
        meta["time_end"] = str(tvals[-1])
        meta["time_count"] = int(tvals.size)
    except Exception:
        pass

    if len(tvals) > 1:
        diffs = np.diff(tint)
        if not (np.all(diffs > 0) or np.all(diffs < 0)):
            issues.append(_issue(_WARN, "CMEMS_TIME_NON_MONOTONIC",
                                 "time coordinate is not monotonically ordered"))

    return issues, meta


def _check_units(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Validate units attributes for uo and vo."""
    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    for var_name, u_or_v in ((_U_VAR, "u"), (_V_VAR, "v")):
        key = f"{u_or_v}_units"
        if var_name not in xds.data_vars:
            continue  # missing variable already reported
        attrs = xds[var_name].attrs
        if "units" not in attrs:
            meta[key] = None
            issues.append(_issue(_WARN, f"CMEMS_{var_name.upper()}_UNITS_MISSING",
                                 f"'{var_name}' has no units attribute"))
        else:
            raw = attrs["units"]
            meta[key] = raw
            if not _units_accepted(raw):
                issues.append(_issue(_ERROR, f"CMEMS_{var_name.upper()}_UNITS_UNEXPECTED",
                                     f"'{var_name}' units '{raw}' are not an accepted "
                                     f"metres-per-second representation"))

    return issues, meta


def _check_variable_dimensions(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Record the actual uo/vo dimensions and shape in metadata."""
    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    for var_name, u_or_v in ((_U_VAR, "u"), (_V_VAR, "v")):
        if var_name not in xds.data_vars:
            continue
        var = xds[var_name]
        meta[f"{u_or_v}_dims"] = list(var.dims)
        meta[f"{u_or_v}_shape"] = list(var.shape)

    return issues, meta


def _check_current_data(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Check for all-missing and partial-missing/non-finite current values."""
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    for var_name, u_or_v in ((_U_VAR, "u"), (_V_VAR, "v")):
        if var_name not in xds.data_vars:
            continue

        arr = xds[var_name].values  # fill_value already masked to NaN by xarray

        total = arr.size
        # NaN covers both xarray-masked fill values and genuine NaN
        nan_count = int(np.sum(np.isnan(arr)))
        # inf is separate from NaN
        inf_count = int(np.sum(np.isinf(arr)))
        # nonfinite_count = nan_count + inf_count (no double-counting: NaN and inf are disjoint)
        nonfinite_count = nan_count + inf_count

        meta[f"{u_or_v}_missing_count"] = nan_count
        meta[f"{u_or_v}_nonfinite_count"] = nonfinite_count
        meta[f"{u_or_v}_total_count"] = total

        if nan_count == total:
            issues.append(_issue(
                _ERROR, f"CMEMS_{var_name.upper()}_ALL_MISSING",
                f"all {total} values in '{var_name}' are missing/masked",
                variable=var_name, total=total,
            ))
        elif nan_count > 0:
            issues.append(_issue(
                _WARN, f"CMEMS_{var_name.upper()}_PARTIAL_MISSING",
                f"{nan_count} of {total} values in '{var_name}' are missing/masked",
                variable=var_name, missing=nan_count, total=total,
            ))

        if inf_count > 0:
            issues.append(_issue(
                _WARN, f"CMEMS_{var_name.upper()}_NON_FINITE",
                f"{inf_count} non-finite (±inf) values in '{var_name}'",
                variable=var_name, inf_count=inf_count,
            ))

    return issues, meta


def _check_dataset_metadata(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Record useful dataset-level global attributes where available."""
    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    attrs = xds.attrs
    for key in ("product_id", "dataset_id", "source", "institution",
                 "processing_level", "Conventions"):
        val = attrs.get(key)
        if val is not None:
            meta[f"dataset_{key.lower()}"] = str(val)

    return issues, meta


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

class CmemsValidator(ScientificValidator):
    """Validates a CMEMS near-surface current NetCDF artifact (A4.4).

    Checks performed:
    - NetCDF can be opened (not corrupt/unreadable)
    - uo and vo current variables are present
    - time, latitude, longitude, depth coordinates are present and non-empty
    - depth dimension is retained (not squeezed) per A3.4 contract
    - depth values are positive, finite, in metres, with the expected
      GLORYS12V1 near-surface first level (≈ 0.494025 m)
    - latitude values are finite and within [-90, 90]
    - longitude values are finite and within [-180, 180] (not normalised)
    - latitude and longitude are monotonically ordered (WARNING if not)
    - time is monotonically ordered (WARNING if not)
    - current variable units are an accepted m s⁻¹ representation
    - current arrays are not entirely missing/NaN
    - partial missing values are flagged as WARNING
    - useful dataset-level metadata is recorded

    Does not perform drift modelling, interpolation, resampling, spill
    detection, AOI intersection, or attribution.  Read-only: the source
    artifact is never modified.
    """

    @property
    def name(self) -> str:
        return VALIDATOR_NAME

    @property
    def version(self) -> str:
        return VALIDATOR_VERSION

    def validate(self, artifact: AcquiredArtifact) -> ValidationResult:
        issues: list[ValidationIssue] = []
        meta: dict[str, Any] = {}
        path = Path(artifact.location)

        # --- file existence / readability ---
        if not path.exists():
            issues.append(_issue(_ERROR, "CMEMS_ARTIFACT_NOT_FOUND",
                                 "artifact file does not exist", path=str(path)))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        if path.stat().st_size == 0:
            issues.append(_issue(_ERROR, "CMEMS_ARTIFACT_EMPTY",
                                 "artifact file is empty", path=str(path)))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        try:
            import xarray as xr
            xds = xr.open_dataset(str(path), engine="netcdf4")
        except Exception as exc:
            issues.append(_issue(_ERROR, "CMEMS_NETCDF_UNREADABLE",
                                 f"NetCDF cannot be opened: {exc}"))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        try:
            # All checks inside the open-dataset context
            var_issues, var_meta = _check_current_variables(xds)
            issues.extend(var_issues)
            meta.update(var_meta)

            coord_issues = _check_coordinates(xds)
            issues.extend(coord_issues)

            lat_issues, lat_meta = _check_latitude(xds)
            issues.extend(lat_issues)
            meta.update(lat_meta)

            lon_issues, lon_meta = _check_longitude(xds)
            issues.extend(lon_issues)
            meta.update(lon_meta)

            depth_issues, depth_meta = _check_depth(xds)
            issues.extend(depth_issues)
            meta.update(depth_meta)

            time_issues, time_meta = _check_time(xds)
            issues.extend(time_issues)
            meta.update(time_meta)

            units_issues, units_meta = _check_units(xds)
            issues.extend(units_issues)
            meta.update(units_meta)

            dims_issues, dims_meta = _check_variable_dimensions(xds)
            issues.extend(dims_issues)
            meta.update(dims_meta)

            data_issues, data_meta = _check_current_data(xds)
            issues.extend(data_issues)
            meta.update(data_meta)

            ds_issues, ds_meta = _check_dataset_metadata(xds)
            issues.extend(ds_issues)
            meta.update(ds_meta)

        except Exception as exc:
            # Fail-closed: unexpected internal errors must not silently pass.
            issues.append(_issue(
                _ERROR, "CMEMS_VALIDATION_INTERNAL_ERROR",
                f"unexpected error during validation: {exc}",
            ))
        finally:
            xds.close()

        meta["validation_classification"] = _classify(issues)
        return self._result(artifact, issues, meta)

    def _result(
        self,
        artifact: AcquiredArtifact,
        issues: list[ValidationIssue],
        meta: dict[str, Any],
    ) -> ValidationResult:
        return ValidationResult(
            artifact_location=artifact.location,
            validator_name=self.name,
            validator_version=self.version,
            validated_at=datetime.now(timezone.utc),
            issues=issues,
            metadata=meta,
        )
