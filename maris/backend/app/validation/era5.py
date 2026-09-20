"""ERA5 10 m wind NetCDF validation for Stage A4.3.

Inspects the NetCDF artifact produced by the A3.3 ERA5 acquisition provider
using xarray (netcdf4 engine).  Read-only: the source file is never modified.

Confirmed ERA5 structure (from empirical probe):
  wind variables : u10 (eastward), v10 (northward)
  dimensions     : (time, latitude, longitude)
  coordinate names: time, latitude, longitude
  wind dtype     : float32
  units          : 'm s**-1'  (also accept 'm/s')
  time dtype     : datetime64[ns] after xarray decoding
  fill values    : exposed as NaN by xarray masking
  latitude order : normally descending (north→south); ascending also valid
  longitude order: normally ascending (west→east); descending also valid

Validation classification stored in ValidationResult.metadata:
  PREFERRED            — no ERRORs, no WARNINGs
  USABLE_WITH_WARNINGS — no ERRORs, at least one WARNING
  INVALID              — at least one ERROR  (passed=False)
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

VALIDATOR_NAME = "era5_validator"
VALIDATOR_VERSION = "1.0.0"

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING
_INFO = ValidationSeverity.INFO

# Required wind variable names as produced by CDS reanalysis-era5-single-levels
_U_VAR = "u10"
_V_VAR = "v10"

# Required coordinate names
_COORD_TIME = "time"
_COORD_LAT = "latitude"
_COORD_LON = "longitude"

# Accepted units strings (case-insensitive comparison after normalisation)
_ACCEPTED_UNITS = {"m s**-1", "m/s", "m s-1", "ms-1"}

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


# ---------------------------------------------------------------------------
# Individual checks — each returns a list of issues and optional metadata
# ---------------------------------------------------------------------------

def _check_wind_variables(
    xds: Any,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Verify u10 and v10 are present; record identity metadata."""
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    for var_name, direction in ((_U_VAR, "eastward"), (_V_VAR, "northward")):
        if var_name not in xds.data_vars:
            issues.append(_issue(
                _ERROR, f"ERA5_{var_name.upper()}_MISSING",
                f"required wind variable '{var_name}' ({direction} component) is absent",
            ))
            meta[f"identified_{'u' if var_name == _U_VAR else 'v'}_component"] = None
        else:
            meta[f"identified_{'u' if var_name == _U_VAR else 'v'}_component"] = var_name

    return issues, meta


def _check_coordinates(xds: Any) -> list[ValidationIssue]:
    """Verify time, latitude, longitude are present and non-empty."""
    issues: list[ValidationIssue] = []
    for coord in (_COORD_TIME, _COORD_LAT, _COORD_LON):
        if coord not in xds.coords:
            issues.append(_issue(
                _ERROR, f"ERA5_{coord.upper()}_MISSING",
                f"required coordinate '{coord}' is absent from the dataset",
            ))
        elif xds.coords[coord].size == 0:
            issues.append(_issue(
                _ERROR, f"ERA5_{coord.upper()}_EMPTY",
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
        issues.append(_issue(_ERROR, "ERA5_LATITUDE_NON_FINITE",
                             "latitude coordinate contains non-finite values"))
        return issues, meta

    if np.any(lat > 90.0) or np.any(lat < -90.0):
        bad = lat[(lat > 90.0) | (lat < -90.0)]
        issues.append(_issue(_ERROR, "ERA5_LATITUDE_OUT_OF_RANGE",
                             f"latitude values outside [-90, 90]: {bad[:3].tolist()}"))
        return issues, meta

    meta["latitude_min"] = float(lat.min())
    meta["latitude_max"] = float(lat.max())

    if len(lat) > 1:
        diffs = np.diff(lat)
        ascending = bool(np.all(diffs > 0))
        descending = bool(np.all(diffs < 0))
        if not ascending and not descending:
            issues.append(_issue(_WARN, "ERA5_LATITUDE_NON_MONOTONIC",
                                 "latitude coordinate is not monotonically ordered"))

    return issues, meta


def _check_longitude(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    if _COORD_LON not in xds.coords or xds.coords[_COORD_LON].size == 0:
        return issues, meta

    lon = xds.coords[_COORD_LON].values.astype(float)

    if not np.all(np.isfinite(lon)):
        issues.append(_issue(_ERROR, "ERA5_LONGITUDE_NON_FINITE",
                             "longitude coordinate contains non-finite values"))
        return issues, meta

    if np.any(lon > 180.0) or np.any(lon < -180.0):
        bad = lon[(lon > 180.0) | (lon < -180.0)]
        issues.append(_issue(_ERROR, "ERA5_LONGITUDE_OUT_OF_RANGE",
                             f"longitude values outside [-180, 180]: {bad[:3].tolist()}"))
        return issues, meta

    meta["longitude_min"] = float(lon.min())
    meta["longitude_max"] = float(lon.max())

    if len(lon) > 1:
        diffs = np.diff(lon)
        ascending = bool(np.all(diffs > 0))
        descending = bool(np.all(diffs < 0))
        if not ascending and not descending:
            issues.append(_issue(_WARN, "ERA5_LONGITUDE_NON_MONOTONIC",
                                 "longitude coordinate is not monotonically ordered"))

    return issues, meta


def _check_time(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    import numpy as np

    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    if _COORD_TIME not in xds.coords or xds.coords[_COORD_TIME].size == 0:
        return issues, meta

    try:
        tvals = xds.coords[_COORD_TIME].values
        # xarray decodes ERA5 time to datetime64[ns]; convert to int64 for diff
        tint = tvals.astype("datetime64[ns]").astype("int64")
    except Exception as exc:
        issues.append(_issue(_ERROR, "ERA5_TIME_UNREADABLE",
                             f"time coordinate cannot be read: {exc}"))
        return issues, meta

    if not np.all(np.isfinite(tint)):
        issues.append(_issue(_ERROR, "ERA5_TIME_NON_FINITE",
                             "time coordinate contains non-finite values"))
        return issues, meta

    # Record temporal extent as ISO strings
    try:
        meta["time_start"] = str(tvals[0])
        meta["time_end"] = str(tvals[-1])
    except Exception:
        pass

    if len(tvals) > 1:
        diffs = np.diff(tint)
        if not (np.all(diffs > 0) or np.all(diffs < 0)):
            issues.append(_issue(_WARN, "ERA5_TIME_NON_MONOTONIC",
                                 "time coordinate is not monotonically ordered"))

    return issues, meta


def _check_units(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    issues: list[ValidationIssue] = []
    meta: dict[str, Any] = {}

    for var_name, key in ((_U_VAR, "u_units"), (_V_VAR, "v_units")):
        if var_name not in xds.data_vars:
            continue  # missing variable already reported
        attrs = xds[var_name].attrs
        if "units" not in attrs:
            meta[key] = None
            issues.append(_issue(_WARN, f"ERA5_{var_name.upper()}_UNITS_MISSING",
                                 f"'{var_name}' has no units attribute"))
        else:
            raw = attrs["units"]
            meta[key] = raw
            if not _units_accepted(raw):
                issues.append(_issue(_ERROR, f"ERA5_{var_name.upper()}_UNITS_UNEXPECTED",
                                     f"'{var_name}' units '{raw}' are not an accepted "
                                     f"metres-per-second representation"))

    return issues, meta


def _check_wind_data(xds: Any) -> tuple[list[ValidationIssue], dict[str, Any]]:
    """Check for all-missing and partial-missing/non-finite wind values."""
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
        nonfinite_count = nan_count + inf_count

        meta[f"{u_or_v}_missing_count"] = nan_count
        meta[f"{u_or_v}_nonfinite_count"] = nonfinite_count
        meta[f"{u_or_v}_total_count"] = total

        if nan_count == total:
            issues.append(_issue(
                _ERROR, f"ERA5_{var_name.upper()}_ALL_MISSING",
                f"all {total} values in '{var_name}' are missing/masked",
                variable=var_name, total=total,
            ))
        elif nan_count > 0:
            issues.append(_issue(
                _WARN, f"ERA5_{var_name.upper()}_PARTIAL_MISSING",
                f"{nan_count} of {total} values in '{var_name}' are missing/masked",
                variable=var_name, missing=nan_count, total=total,
            ))

        if inf_count > 0:
            issues.append(_issue(
                _WARN, f"ERA5_{var_name.upper()}_NON_FINITE",
                f"{inf_count} non-finite (±inf) values in '{var_name}'",
                variable=var_name, inf_count=inf_count,
            ))

    return issues, meta


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

class Era5Validator(ScientificValidator):
    """Validates an ERA5 10 m wind NetCDF artifact.

    Checks performed:
    - NetCDF can be opened (not corrupt/unreadable)
    - u10 and v10 wind variables are present
    - time, latitude, longitude coordinates are present and non-empty
    - latitude values are finite and within [-90, 90]
    - longitude values are finite and within [-180, 180]
    - latitude and longitude are monotonically ordered (WARNING if not)
    - time is monotonically ordered (WARNING if not)
    - wind variable units are an accepted m/s representation
    - wind arrays are not entirely missing/NaN
    - partial missing values are flagged as WARNING

    Does not perform drift modelling, interpolation, resampling, or
    AOI intersection.  Read-only: source artifact is never modified.
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
            issues.append(_issue(_ERROR, "ERA5_ARTIFACT_NOT_FOUND",
                                 "artifact file does not exist", path=str(path)))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        if path.stat().st_size == 0:
            issues.append(_issue(_ERROR, "ERA5_ARTIFACT_EMPTY",
                                 "artifact file is empty", path=str(path)))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        try:
            import xarray as xr
            xds = xr.open_dataset(str(path), engine="netcdf4")
            if "valid_time" in xds and "time" not in xds:
                xds = xds.rename({"valid_time": "time"})
            elif "valid_time" in xds.coords and "time" not in xds.coords:
                xds = xds.rename_vars({"valid_time": "time"})
        except Exception as exc:
            issues.append(_issue(_ERROR, "ERA5_NETCDF_UNREADABLE",
                                 f"NetCDF cannot be opened: {exc}"))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        try:
            # --- all checks inside the open-dataset context ---
            var_issues, var_meta = _check_wind_variables(xds)
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

            time_issues, time_meta = _check_time(xds)
            issues.extend(time_issues)
            meta.update(time_meta)

            units_issues, units_meta = _check_units(xds)
            issues.extend(units_issues)
            meta.update(units_meta)

            data_issues, data_meta = _check_wind_data(xds)
            issues.extend(data_issues)
            meta.update(data_meta)

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
