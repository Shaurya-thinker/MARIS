"""MARIS Stage D1 — Deterministic Leeway-Euler Forward Drift Modelling Service.

Consumes:
- ERA5 10 m wind (u10, v10) validated and registered by Stage C1
- CMEMS near-surface ocean current (uo, vo, depth≈0.5m) validated and registered by Stage C1
- B3 SpillDetection centroid as trajectory origin
- B3 SatelliteScene acquisition_time as trajectory anchor (t=0)

Model: Leeway-Euler forward integration
    v_drift(t) = v_current(lon, lat, t)  +  α × v_wind(lon, lat, t)

    v_current  = (uo, vo) from CMEMS GLORYS12V1 near-surface layer
    v_wind     = (u10, v10) from ERA5 10 m reanalysis
    α          = leeway fraction (default 0.035 = 3.5% of 10 m wind speed)
    dt         = step_hours (default 1 h)

Position update (flat-Earth, valid for short-range drift < ~500 km):
    lon_new = lon + (drift_u × dt_s) / (cos(lat_rad) × 111320)
    lat_new = lat + (drift_v × dt_s) / 111320

Spatial interpolation: bilinear via scipy.interpolate.RegularGridInterpolator
Temporal selection:    nearest available grid time step (ERA5: hourly; CMEMS: daily P1D)

Scientific limitations — D1 is a deterministic baseline, NOT an operational forecast:
- α = 0.035 is documented (ITOPF / NOAA GNOME / Breivik et al. 2011), not calibrated.
- ERA5 10 m wind ≠ immediate sea-surface forcing; sea-state adjustment excluded.
- CMEMS ~0.5 m depth excludes Stokes drift, Langmuir circulation, wave mixing.
- CMEMS P1D: sub-daily current variability is unresolved.
- Euler 1st-order integration; flat-Earth position update.
- No stochastic ensemble spread; no backward drift; no source attribution.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr
from scipy.interpolate import RegularGridInterpolator

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.drift import DriftResult, DriftStep
from app.models.satellite import SpillDetection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION = "leeway_euler_v1"

DEFAULT_LEEWAY_FRACTION = 0.035   # 3.5% of 10 m wind speed — ITOPF/NOAA GNOME baseline
DEFAULT_DRIFT_HOURS = 24.0        # default forward integration horizon
DEFAULT_STEP_HOURS = 1.0          # default Euler time step

# Flat-Earth: meters per degree latitude (equatorial approximation)
_M_PER_DEG_LAT = 111_320.0

# Tiny epsilon to guard against floating-point remainder in the `while remaining > 0` loop
_STEP_EPSILON = 1e-9

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class DriftModellingError(Exception):
    """Raised when drift modelling fails due to invalid inputs or insufficient data."""


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sanitize(val: str, fallback: str) -> str:
    """Replace non-filesystem-safe characters for path segment construction."""
    cleaned = _UNSAFE_FILENAME.sub("_", val).strip("._")
    return cleaned or fallback


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two WGS84 points.

    Uses the haversine formula. Numerically stable for distances in the range
    relevant to 24-hour oil-spill drift (a few kilometres to ~100 km).
    """
    R = 6_371_000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * R * math.asin(math.sqrt(min(a, 1.0)))


def _normalize_netcdf_dataset(ds: xr.Dataset) -> xr.Dataset:
    """Normalize NetCDF coordinate names and dimensions for ECMWF/CMEMS compatibility."""
    if "valid_time" in ds and "time" not in ds:
        ds = ds.rename({"valid_time": "time"})
    elif "valid_time" in ds.coords and "time" not in ds.coords:
        ds = ds.rename_vars({"valid_time": "time"})

    if "expver" in ds.dims:
        ds = ds.isel(expver=0, drop=True)
    if "number" in ds.dims:
        ds = ds.isel(number=0, drop=True)
    if "depth" in ds.dims:
        ds = ds.isel(depth=0, drop=True)

    if "lat" in ds.dims and "latitude" not in ds.dims:
        ds = ds.rename({"lat": "latitude"})
    if "lon" in ds.dims and "longitude" not in ds.dims:
        ds = ds.rename({"lon": "longitude"})

    return ds


def _open_netcdf(location: str) -> xr.Dataset:
    """Open a validated NetCDF artifact.

    Handles:
    - Single file: directly opened with xarray/netcdf4
    - Directory: ERA5 multi-month output — all .nc files combined by_coords
    """
    path = Path(location)
    if path.is_file():
        ds = xr.open_dataset(str(path), engine="netcdf4")
        return _normalize_netcdf_dataset(ds)
    if path.is_dir():
        nc_files = sorted(path.glob("*.nc"))
        if not nc_files:
            raise DriftModellingError(
                f"NetCDF directory '{path}' contains no .nc files. "
                "Expected ERA5 monthly output from Stage C1."
            )
        ds = xr.open_mfdataset(
            [str(f) for f in nc_files],
            engine="netcdf4",
            combine="by_coords",
        )
        return _normalize_netcdf_dataset(ds)
    raise DriftModellingError(
        f"NetCDF artifact path does not exist: '{location}'"
    )


def _build_interpolator(
    lat_vals: np.ndarray,
    lon_vals: np.ndarray,
    data_2d: np.ndarray,
) -> RegularGridInterpolator:
    """Build a bilinear RegularGridInterpolator for a 2D (lat, lon) field slice.

    Sorts latitude and longitude to strictly ascending order, as required by
    RegularGridInterpolator. ERA5 latitudes are typically descending (90→−90).

    bounds_error=True: the interpolator raises ValueError if a query point falls
    outside the grid domain. The calling code converts this to DriftModellingError.
    """
    lat_arr = np.asarray(lat_vals, dtype=np.float64)
    lon_arr = np.asarray(lon_vals, dtype=np.float64)
    data = np.asarray(data_2d, dtype=np.float64)

    # Ensure latitude is ascending
    if lat_arr.size > 1 and lat_arr[0] > lat_arr[-1]:
        lat_arr = lat_arr[::-1]
        data = data[::-1, :]

    # Ensure longitude is ascending (unusual but handled defensively)
    if lon_arr.size > 1 and lon_arr[0] > lon_arr[-1]:
        lon_arr = lon_arr[::-1]
        data = data[:, ::-1]

    return RegularGridInterpolator(
        (lat_arr, lon_arr),
        data,
        method="linear",
        bounds_error=True,
    )


def _nearest_time_index(time_values: np.ndarray, target: datetime) -> int:
    """Return the index of the time value nearest to target.

    Converts time_values to datetime64[ns] int64 for stable comparison across
    different xarray time encodings.
    """
    target_utc = _utc(target)
    # Build a numpy datetime64[ns] scalar from the UTC datetime (strip tzinfo)
    target_np = np.datetime64(
        target_utc.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), "s"
    ).astype("datetime64[ns]")
    times_ns = time_values.astype("datetime64[ns]")
    diffs = np.abs((times_ns - target_np).astype(np.int64))
    return int(np.argmin(diffs))


def _validate_required_vars(ds: xr.Dataset, required_vars: list[str], label: str) -> None:
    """Raise DriftModellingError if any required variable is missing."""
    missing = [v for v in required_vars if v not in ds.data_vars]
    if missing:
        raise DriftModellingError(
            f"{label} dataset is missing required variable(s): {missing}. "
            f"Available data variables: {list(ds.data_vars)}"
        )


def _validate_required_dims(ds: xr.Dataset, required_dims: list[str], label: str) -> None:
    """Raise DriftModellingError if any required dimension or coordinate is missing."""
    available = set(ds.dims) | set(ds.coords)
    missing = [d for d in required_dims if d not in available]
    if missing:
        raise DriftModellingError(
            f"{label} dataset is missing required dimension(s)/coordinate(s): {missing}. "
            f"Available: {sorted(available)}"
        )


# ---------------------------------------------------------------------------
# Public utilities
# ---------------------------------------------------------------------------

def extract_centroid(spill_detection: SpillDetection) -> tuple[float, float]:
    """Extract the (lon, lat) drift origin from a SpillDetection's metadata centroid.

    Raises:
        DriftModellingError: if detected=False, centroid is absent, or non-finite.
    """
    if not spill_detection.detected:
        raise DriftModellingError(
            f"SpillDetection '{spill_detection.id}' has detected=False. "
            "Forward drift modelling requires a positive spill detection."
        )
    centroid = spill_detection.metadata.get("centroid")
    if not centroid:
        raise DriftModellingError(
            f"SpillDetection '{spill_detection.id}' has no centroid in metadata. "
            "Cannot derive drift origin location."
        )
    try:
        lon = float(centroid["longitude"])
        lat = float(centroid["latitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DriftModellingError(
            f"SpillDetection '{spill_detection.id}' centroid is malformed: {centroid}"
        ) from exc
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise DriftModellingError(
            f"SpillDetection '{spill_detection.id}' centroid contains non-finite "
            f"coordinates: lon={lon}, lat={lat}"
        )
    return lon, lat


# ---------------------------------------------------------------------------
# Core integration (pure, no I/O — directly testable)
# ---------------------------------------------------------------------------

def run_forward_drift(
    origin_lon: float,
    origin_lat: float,
    observation_time: datetime,
    wind_ds: xr.Dataset,
    curr_ds: xr.Dataset,
    drift_hours: float = DEFAULT_DRIFT_HOURS,
    step_hours: float = DEFAULT_STEP_HOURS,
    leeway_fraction: float = DEFAULT_LEEWAY_FRACTION,
) -> list[DriftStep]:
    """Execute the Leeway-Euler forward integration.

    Builds RegularGridInterpolators once per (variable, time-index) pair, caching
    them across steps to avoid unnecessary reconstruction when the nearest time index
    does not change.

    The integration uses the exact requested duration:
        remaining = drift_hours
        while remaining > epsilon:
            dt = min(step_hours, remaining)
            integrate dt
            remaining -= dt

    This ensures the final step is shortened rather than discarded or over-extended
    when drift_hours is not an exact multiple of step_hours.

    Raises:
        DriftModellingError: if origin is outside grid bounds, observation_time is
            outside data range, or an interpolated value is NaN (e.g., land fill).
    """
    # --- Extract coordinate arrays ---
    wind_lats = wind_ds["latitude"].values.astype(np.float64)
    wind_lons = wind_ds["longitude"].values.astype(np.float64)
    wind_times = wind_ds["time"].values

    curr_lats = curr_ds["latitude"].values.astype(np.float64)
    curr_lons = curr_ds["longitude"].values.astype(np.float64)
    curr_times = curr_ds["time"].values

    # --- Validate origin within ERA5 grid ---
    w_lat_min, w_lat_max = float(wind_lats.min()), float(wind_lats.max())
    w_lon_min, w_lon_max = float(wind_lons.min()), float(wind_lons.max())

    if not (w_lat_min <= origin_lat <= w_lat_max):
        raise DriftModellingError(
            f"Origin latitude {origin_lat:.4f}° is outside the ERA5 grid "
            f"latitude range [{w_lat_min:.4f}, {w_lat_max:.4f}]."
        )
    if not (w_lon_min <= origin_lon <= w_lon_max):
        raise DriftModellingError(
            f"Origin longitude {origin_lon:.4f}° is outside the ERA5 grid "
            f"longitude range [{w_lon_min:.4f}, {w_lon_max:.4f}]."
        )

    # --- Validate origin within CMEMS grid ---
    c_lat_min, c_lat_max = float(curr_lats.min()), float(curr_lats.max())
    c_lon_min, c_lon_max = float(curr_lons.min()), float(curr_lons.max())

    if not (c_lat_min <= origin_lat <= c_lat_max):
        raise DriftModellingError(
            f"Origin latitude {origin_lat:.4f}° is outside the CMEMS grid "
            f"latitude range [{c_lat_min:.4f}, {c_lat_max:.4f}]."
        )
    if not (c_lon_min <= origin_lon <= c_lon_max):
        raise DriftModellingError(
            f"Origin longitude {origin_lon:.4f}° is outside the CMEMS grid "
            f"longitude range [{c_lon_min:.4f}, {c_lon_max:.4f}]."
        )

    # --- Validate observation_time within both data ranges ---
    obs_utc = _utc(observation_time)
    obs_np = np.datetime64(
        obs_utc.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), "s"
    ).astype("datetime64[ns]")

    wind_times_ns = wind_times.astype("datetime64[ns]")
    curr_times_ns = curr_times.astype("datetime64[ns]")

    time_tol_ns = np.timedelta64(3600, "s").astype("timedelta64[ns]")
    c_time_tol_ns = np.timedelta64(86400, "s").astype("timedelta64[ns]")

    if obs_np < (wind_times_ns.min() - time_tol_ns) or obs_np > (wind_times_ns.max() + time_tol_ns):
        raise DriftModellingError(
            f"Observation time {obs_utc.isoformat()} is outside the ERA5 data "
            f"temporal range [{wind_times_ns.min()}, {wind_times_ns.max()}]."
        )
    if obs_np < (curr_times_ns.min() - c_time_tol_ns) or obs_np > (curr_times_ns.max() + c_time_tol_ns):
        raise DriftModellingError(
            f"Observation time {obs_utc.isoformat()} is outside the CMEMS data "
            f"temporal range [{curr_times_ns.min()}, {curr_times_ns.max()}]."
        )

    # --- Euler integration loop ---
    steps: list[DriftStep] = []
    lon = float(origin_lon)
    lat = float(origin_lat)
    current_time = obs_utc
    cumulative_dist = 0.0

    # Per-step interpolator cache keyed by (variable_name, time_index)
    _wind_cache: dict[tuple[str, int], RegularGridInterpolator] = {}
    _curr_cache: dict[tuple[str, int], RegularGridInterpolator] = {}

    remaining = float(drift_hours)

    while remaining > _STEP_EPSILON:
        dt_h = min(step_hours, remaining)
        dt_s = dt_h * 3600.0

        # Use start-of-step time for nearest-time selection (forward Euler convention)
        query_time = current_time

        # --- Find nearest time indices ---
        wind_t_idx = _nearest_time_index(wind_times, query_time)
        curr_t_idx = _nearest_time_index(curr_times, query_time)

        # --- Build ERA5 interpolators if not cached for this time index ---
        if ("u10", wind_t_idx) not in _wind_cache:
            u10_slice = wind_ds["u10"].isel(time=wind_t_idx).values.astype(np.float64)
            v10_slice = wind_ds["v10"].isel(time=wind_t_idx).values.astype(np.float64)
            _wind_cache[("u10", wind_t_idx)] = _build_interpolator(wind_lats, wind_lons, u10_slice)
            _wind_cache[("v10", wind_t_idx)] = _build_interpolator(wind_lats, wind_lons, v10_slice)

        # --- Build CMEMS interpolators if not cached for this time index ---
        if ("uo", curr_t_idx) not in _curr_cache:
            uo_slice = curr_ds["uo"].isel(time=curr_t_idx).values.astype(np.float64)
            vo_slice = curr_ds["vo"].isel(time=curr_t_idx).values.astype(np.float64)
            _curr_cache[("uo", curr_t_idx)] = _build_interpolator(curr_lats, curr_lons, uo_slice)
            _curr_cache[("vo", curr_t_idx)] = _build_interpolator(curr_lats, curr_lons, vo_slice)

        # --- Spatial bilinear interpolation at current position ---
        query_point = np.array([[lat, lon]])
        try:
            u_wind = float(_wind_cache[("u10", wind_t_idx)](query_point)[0])
            v_wind = float(_wind_cache[("v10", wind_t_idx)](query_point)[0])
            u_curr = float(_curr_cache[("uo", curr_t_idx)](query_point)[0])
            v_curr = float(_curr_cache[("vo", curr_t_idx)](query_point)[0])
        except ValueError as exc:
            raise DriftModellingError(
                f"Spatial interpolation failed at step t={current_time.isoformat()}, "
                f"position (lon={lon:.4f}, lat={lat:.4f}). "
                f"The trajectory may have drifted outside the data coverage area. "
                f"Details: {exc}"
            ) from exc

        # Fail-closed on NaN (e.g. land fill-value region)
        if not all(math.isfinite(v) for v in (u_wind, v_wind, u_curr, v_curr)):
            raise DriftModellingError(
                f"Non-finite interpolated forcing at step t={current_time.isoformat()}, "
                f"(lon={lon:.4f}, lat={lat:.4f}). "
                "The origin or a later trajectory position may be in a land fill-value region."
            )

        # --- Net drift velocity ---
        drift_u = u_curr + leeway_fraction * u_wind
        drift_v = v_curr + leeway_fraction * v_wind

        # --- Flat-Earth position update ---
        lat_rad = math.radians(lat)
        cos_lat = math.cos(lat_rad)
        if abs(cos_lat) < 1e-10:
            # Exactly at pole — longitude undefined; hold fixed
            new_lon = lon
        else:
            new_lon = lon + (drift_u * dt_s) / (cos_lat * _M_PER_DEG_LAT)
        new_lat = lat + (drift_v * dt_s) / _M_PER_DEG_LAT

        # Clamp to WGS84 range (no wrapping: flag if truly out of range)
        new_lon = max(-180.0, min(180.0, new_lon))
        new_lat = max(-90.0, min(90.0, new_lat))

        # --- Cumulative arc distance ---
        step_dist = _haversine_m(lon, lat, new_lon, new_lat)
        cumulative_dist += step_dist

        step_time = current_time + timedelta(hours=dt_h)

        steps.append(DriftStep(
            timestamp=step_time,
            lon=round(new_lon, 6),
            lat=round(new_lat, 6),
            u_wind_ms=round(u_wind, 6),
            v_wind_ms=round(v_wind, 6),
            u_current_ms=round(u_curr, 6),
            v_current_ms=round(v_curr, 6),
            drift_u_ms=round(drift_u, 6),
            drift_v_ms=round(v_curr + leeway_fraction * v_wind, 6),
            cumulative_distance_m=round(cumulative_dist, 2),
        ))

        lon = new_lon
        lat = new_lat
        current_time = step_time
        remaining -= dt_h

    return steps


# ---------------------------------------------------------------------------
# Main entry point (handles I/O, registration)
# ---------------------------------------------------------------------------

def compute_drift_for_spill(
    investigation_id: str,
    spill_detection_id: str,
    origin_lon: float,
    origin_lat: float,
    observation_time: datetime,
    wind_asset: Asset,
    current_asset: Asset,
    drift_hours: float = DEFAULT_DRIFT_HOURS,
    step_hours: float = DEFAULT_STEP_HOURS,
    leeway_fraction: float = DEFAULT_LEEWAY_FRACTION,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[DriftResult, Asset]:
    """Run Stage D1 forward drift and register the output as a DRIFT_PRODUCT asset.

    Validates all inputs fail-closed, opens validated ERA5 and CMEMS NetCDF files
    using xarray, executes the Leeway-Euler forward integration, writes a GeoJSON
    LineString artifact, and registers the result in AssetRegistry.

    Args:
        investigation_id:    MARIS investigation identifier.
        spill_detection_id:  B3 SpillDetection.id (provenance link).
        origin_lon:          Drift origin longitude (WGS84 decimal degrees).
        origin_lat:          Drift origin latitude (WGS84 decimal degrees).
        observation_time:    SAR scene acquisition time — trajectory anchor (t=0).
        wind_asset:          Registered C1 ENVIRONMENT_WIND Asset (ERA5 NetCDF).
        current_asset:       Registered C1 ENVIRONMENT_CURRENT Asset (CMEMS NetCDF).
        drift_hours:         Total forward integration horizon in hours (default 24).
        step_hours:          Euler time step in hours (default 1). Final step may be
                             shorter when drift_hours is not an exact multiple.
        leeway_fraction:     Wind leeway coefficient α (default 0.035 = 3.5%).
        output_dir:          Output directory for the GeoJSON artifact. If None, defaults
                             to {MARIS_DATA_DIR}/derived/{investigation_id}/drift/{spill_id}/.
        registry:            AssetRegistry instance. Defaults to default_asset_registry.

    Returns:
        (DriftResult, Asset): typed domain result and the registered DRIFT_PRODUCT asset.

    Raises:
        DriftModellingError: on any validation failure or integration error.
    """
    # --- Input validation (fail-closed) ---
    if drift_hours <= 0.0:
        raise DriftModellingError(
            f"drift_hours must be > 0, got {drift_hours}"
        )
    if step_hours <= 0.0:
        raise DriftModellingError(
            f"step_hours must be > 0, got {step_hours}"
        )
    if not math.isfinite(origin_lon) or not math.isfinite(origin_lat):
        raise DriftModellingError(
            f"origin_lon and origin_lat must be finite; got lon={origin_lon}, lat={origin_lat}"
        )
    if not (-180.0 <= origin_lon <= 180.0):
        raise DriftModellingError(
            f"origin_lon {origin_lon} is outside WGS84 range [-180, 180]"
        )
    if not (-90.0 <= origin_lat <= 90.0):
        raise DriftModellingError(
            f"origin_lat {origin_lat} is outside WGS84 range [-90, 90]"
        )

    wind_path = Path(wind_asset.location)
    if not wind_path.exists():
        raise DriftModellingError(
            f"ERA5 wind asset does not exist at '{wind_asset.location}'. "
            "Ensure Stage C1 completed successfully."
        )

    curr_path = Path(current_asset.location)
    if not curr_path.exists():
        raise DriftModellingError(
            f"CMEMS current asset does not exist at '{current_asset.location}'. "
            "Ensure Stage C1 completed successfully."
        )

    # --- Open datasets ---
    wind_ds = None
    curr_raw = None
    try:
        try:
            wind_ds = _open_netcdf(wind_asset.location)
        except DriftModellingError:
            raise
        except Exception as exc:
            raise DriftModellingError(
                f"Failed to open ERA5 wind NetCDF '{wind_asset.location}': {exc}"
            ) from exc

        try:
            curr_raw = _open_netcdf(current_asset.location)
            # Select near-surface layer (CMEMS GLORYS12V1 depth ≈ 0.494 m)
            if "depth" in curr_raw.dims or "depth" in curr_raw.coords:
                curr_ds = curr_raw.isel(depth=0)
            else:
                curr_ds = curr_raw
        except DriftModellingError:
            raise
        except Exception as exc:
            raise DriftModellingError(
                f"Failed to open CMEMS current NetCDF '{current_asset.location}': {exc}"
            ) from exc

        # --- Validate required variables and dimensions ---
        _validate_required_vars(wind_ds, ["u10", "v10"], "ERA5")
        _validate_required_vars(curr_ds, ["uo", "vo"], "CMEMS")
        _validate_required_dims(wind_ds, ["time", "latitude", "longitude"], "ERA5")
        _validate_required_dims(curr_ds, ["time", "latitude", "longitude"], "CMEMS")

        # --- Run forward integration ---
        steps = run_forward_drift(
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=observation_time,
            wind_ds=wind_ds,
            curr_ds=curr_ds,
            drift_hours=drift_hours,
            step_hours=step_hours,
            leeway_fraction=leeway_fraction,
        )
    finally:
        if wind_ds is not None:
            wind_ds.close()
        if curr_raw is not None:
            curr_raw.close()

    if not steps:
        raise DriftModellingError(
            "Forward integration produced no steps. "
            "Check that drift_hours and step_hours are both positive."
        )

    endpoint_lon = steps[-1].lon
    endpoint_lat = steps[-1].lat

    # --- Determine output directory ---
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize(investigation_id, "investigation")
        safe_sd = _sanitize(spill_detection_id, "spill")
        target_dir = (
            Path(settings.data_dir)
            / "derived"
            / safe_inv
            / "drift"
            / safe_sd
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    # --- Build GeoJSON LineString: origin + all step positions ---
    obs_utc = _utc(observation_time)
    coordinates = [[origin_lon, origin_lat]] + [[s.lon, s.lat] for s in steps]

    geojson_feature: dict[str, Any] = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
        "properties": {
            "investigation_id": investigation_id,
            "spill_detection_id": spill_detection_id,
            "wind_asset_id": wind_asset.id,
            "current_asset_id": current_asset.id,
            "model_version": MODEL_VERSION,
            "observation_time": obs_utc.isoformat(),
            "origin_lon": origin_lon,
            "origin_lat": origin_lat,
            "endpoint_lon": endpoint_lon,
            "endpoint_lat": endpoint_lat,
            "total_duration_hours": drift_hours,
            "step_hours": step_hours,
            "leeway_fraction": leeway_fraction,
            "step_count": len(steps),
            "total_distance_m": steps[-1].cumulative_distance_m,
            "scientific_note": (
                "Deterministic Leeway-Euler baseline model. "
                "α=0.035 is not calibrated. Not an operational forecast."
            ),
        },
    }

    geojson_path = target_dir / "drift_trajectory.geojson"
    with open(geojson_path, "w", encoding="utf-8") as fp:
        json.dump(geojson_feature, fp, indent=2)

    # --- Assemble metadata ---
    drift_metadata: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "integration_scheme": "euler_forward",
        "leeway_fraction": leeway_fraction,
        "drift_hours": drift_hours,
        "step_hours": step_hours,
        "step_count": len(steps),
        "wind_asset_id": wind_asset.id,
        "current_asset_id": current_asset.id,
        "spill_detection_id": spill_detection_id,
        "trajectory_path": str(geojson_path.resolve()),
        "scientific_limitations": [
            (
                "α = 0.035 is a documented operational baseline (ITOPF / NOAA GNOME / "
                "Breivik et al. 2011), NOT calibrated for this investigation, oil type, "
                "slick thickness, or emulsification state."
            ),
            (
                "ERA5 10 m wind is used as a surface forcing proxy. "
                "Sea-state-dependent adjustment (Stokes contribution) is not applied."
            ),
            (
                "CMEMS GLORYS12V1 depth ≈ 0.5 m is used as a near-surface current proxy. "
                "Langmuir circulation, wave mixing, and Stokes drift are excluded."
            ),
            (
                "CMEMS temporal resolution is daily (P1D). "
                "Sub-daily current variability is unresolved."
            ),
            (
                "Euler 1st-order forward integration with flat-Earth position update. "
                "Appropriate for short-range drift (< ~500 km). "
                "Runge-Kutta 4th-order and geodesic stepping are reserved for D2+."
            ),
            (
                "No stochastic ensemble spread. endpoint_uncertainty_km is None. "
                "No backward drift. No source/origin estimation. No AIS correlation. "
                "No evidence fusion or attribution."
            ),
        ],
    }

    # --- Register DRIFT_PRODUCT asset in AssetRegistry ---
    retrieved_at = datetime.now(timezone.utc)
    artifact = AcquiredArtifact(
        asset_type=AssetType.DRIFT_PRODUCT,
        location=str(geojson_path.resolve()),
        source="drift_modelling_service",
        acquisition_time=obs_utc,
        provenance=Provenance(
            product_id=spill_detection_id,
            retrieved_at=retrieved_at,
            processing_level="derived_drift_trajectory",
            notes=(
                f"Leeway-Euler forward drift trajectory. "
                f"α={leeway_fraction}, dt={step_hours}h, T={drift_hours}h. "
                "Deterministic D1 baseline; not an operational forecast."
            ),
            extra={
                "model_version": MODEL_VERSION,
                "spill_detection_id": spill_detection_id,
                "wind_asset_id": wind_asset.id,
                "current_asset_id": current_asset.id,
                "leeway_fraction": leeway_fraction,
                "drift_hours": drift_hours,
                "step_hours": step_hours,
            },
        ),
        metadata={"investigation_id": investigation_id, "model_version": MODEL_VERSION},
    )

    target_registry = registry or default_asset_registry
    derived_asset = target_registry.register(
        investigation_id, "drift_modelling_service", artifact
    )

    # --- Assemble DriftResult (asset_id known after registration) ---
    drift_id = (
        f"drift-{_sanitize(spill_detection_id, 'spill')}"
        f"-{_sanitize(investigation_id, 'inv')}"
    )

    result = DriftResult(
        id=drift_id,
        investigation_id=investigation_id,
        spill_detection_id=spill_detection_id,
        wind_asset_id=wind_asset.id,
        current_asset_id=current_asset.id,
        asset_id=derived_asset.id,
        model_version=MODEL_VERSION,
        observation_time=obs_utc,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        steps=steps,
        total_duration_hours=drift_hours,
        step_hours=step_hours,
        leeway_fraction=leeway_fraction,
        endpoint_lon=endpoint_lon,
        endpoint_lat=endpoint_lat,
        endpoint_uncertainty_km=None,
        metadata=drift_metadata,
    )

    return result, derived_asset
