"""MARIS Stage D3 — Backward Drift & Source Candidate Zone Estimation Service.

Estimates a physically plausible historical source candidate zone from an observed
SAR spill detection and validated C1 metocean forcing (ERA5 10 m wind and CMEMS
near-surface current).

Integration:
    Time-reversed Leeway-Euler stepping:
        v_drift(t) = v_current(lon, lat, t) + α · v_wind(lon, lat, t)
        lon_prev   = lon - (drift_u · dt_s) / (cos(lat_rad) · 111320)
        lat_prev   = lat - (drift_v · dt_s) / 111320
        t_prev     = t - dt

Analytical Search Envelope / Source Candidate Zone:
    R0 = max(sqrt(spill_area / pi), 500 m)
    R(τ) = R0 + c_growth · τ (default growth rate c_growth = 500 m/h)
    Geometry: 32-vertex regular polygon centered at estimated source point.

Scientific framing & operational limitations:
- This service estimates a physically plausible historical source candidate zone
  based on oceanographic and meteorological forcing. It does NOT identify a vessel
  and does NOT establish culpability or legal responsibility.
- The expansion rate is a heuristic analytical search envelope assumption.
  It MUST NOT be described as a 95% confidence region, probability distribution,
  or statistically calibrated uncertainty.
- Reverse advection assumes smooth Eulerian flow fields; turbulent mixing and
  diffusion are accounted for by the analytical expansion radius, not reverse diffusion.
- Weathering, emulsification, and evaporation are not modelled; slicks older than
  12–24 hours may be unconstrained.
- AIS vessel correlation and attribution belong strictly to Stages E and F.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.satellite import SpillDetection
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.services.drift_modelling import (
    _M_PER_DEG_LAT,
    _STEP_EPSILON,
    _build_interpolator,
    _haversine_m,
    _nearest_time_index,
    _open_netcdf,
    _sanitize,
    _utc,
    _validate_required_dims,
    _validate_required_vars,
    extract_centroid,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_VERSION = "leeway_euler_backward_v1"

DEFAULT_LOOKBACK_HOURS = 12.0               # Default historical backtracking duration
DEFAULT_STEP_HOURS = 1.0                    # Default Euler time step size
DEFAULT_LEEWAY_FRACTION = 0.035             # 3.5% of 10 m wind speed (ITOPF / NOAA GNOME baseline)
DEFAULT_UNCERTAINTY_GROWTH_M_PER_H = 500.0  # Heuristic 500 m/hour search envelope expansion
MIN_INITIAL_RADIUS_M = 500.0                # Minimum initial spill radius fallback
POLYGON_VERTICES = 32                       # Number of vertices for source candidate zone polygon


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SourceEstimationError(Exception):
    """Raised when backward drift or source candidate zone estimation fails."""


# ---------------------------------------------------------------------------
# Geometry Utilities
# ---------------------------------------------------------------------------

def generate_source_candidate_polygon(
    center_lon: float,
    center_lat: float,
    radius_m: float,
    num_vertices: int = POLYGON_VERTICES,
) -> dict[str, Any]:
    """Generate a closed GeoJSON Polygon approximating a circular uncertainty envelope.

    Constructs a regular polygon of num_vertices in WGS84 coordinates centered at
    (center_lon, center_lat) with geographic radius radius_m.

    The first and last coordinate are identical to form a closed linear ring according
    to the GeoJSON specification (RFC 7946).
    """
    if radius_m <= 0.0:
        raise SourceEstimationError(f"radius_m must be > 0, got {radius_m}")
    if not math.isfinite(center_lon) or not math.isfinite(center_lat):
        raise SourceEstimationError(
            f"center_lon and center_lat must be finite; got ({center_lon}, {center_lat})"
        )

    cos_lat = math.cos(math.radians(center_lat))
    if abs(cos_lat) < 1e-6:
        cos_lat = 1e-6

    coords: list[list[float]] = []
    for i in range(num_vertices + 1):
        angle = 2.0 * math.pi * (i % num_vertices) / num_vertices
        dx = radius_m * math.cos(angle)
        dy = radius_m * math.sin(angle)

        v_lon = center_lon + dx / (cos_lat * _M_PER_DEG_LAT)
        v_lat = center_lat + dy / _M_PER_DEG_LAT

        # Clamp to valid WGS84 range
        v_lon = max(-180.0, min(180.0, v_lon))
        v_lat = max(-90.0, min(90.0, v_lat))

        coords.append([round(v_lon, 7), round(v_lat, 7)])

    return {
        "type": "Polygon",
        "coordinates": [coords],
    }


# ---------------------------------------------------------------------------
# Core Backward Integration (Pure, testable)
# ---------------------------------------------------------------------------

def run_backward_drift(
    origin_lon: float,
    origin_lat: float,
    observation_time: datetime,
    wind_ds: xr.Dataset,
    curr_ds: xr.Dataset,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    step_hours: float = DEFAULT_STEP_HOURS,
    leeway_fraction: float = DEFAULT_LEEWAY_FRACTION,
    spill_area_m2: float | None = None,
    uncertainty_growth_m_per_h: float = DEFAULT_UNCERTAINTY_GROWTH_M_PER_H,
) -> list[BackwardDriftStep]:
    """Execute time-reversed Leeway-Euler backward integration.

    Steps backward in time:
        v_drift = v_current(lon, lat, t) + α · v_wind(lon, lat, t)
        lon_prev = lon - (drift_u · dt_s) / (cos(lat_rad) · 111320)
        lat_prev = lat - (drift_v · dt_s) / 111320
        t_prev   = t - dt

    The integration uses the exact requested duration:
        remaining = lookback_hours
        while remaining > _STEP_EPSILON:
            dt = min(step_hours, remaining)
            integrate dt backward
            remaining -= dt

    Fails closed if the historical time window [observation_time - lookback_hours, observation_time]
    falls outside the available environmental datasets.

    Raises:
        SourceEstimationError: on validation failure, out-of-bounds coordinate, or data gap.
    """
    if lookback_hours <= 0.0:
        raise SourceEstimationError(f"lookback_hours must be > 0, got {lookback_hours}")
    if step_hours <= 0.0:
        raise SourceEstimationError(f"step_hours must be > 0, got {step_hours}")
    if leeway_fraction < 0.0:
        raise SourceEstimationError(f"leeway_fraction must be >= 0, got {leeway_fraction}")
    if uncertainty_growth_m_per_h < 0.0:
        raise SourceEstimationError(
            f"uncertainty_growth_m_per_h must be >= 0, got {uncertainty_growth_m_per_h}"
        )

    # Initial slick radius R0: max(sqrt(area / pi), 500 m)
    if spill_area_m2 is not None and spill_area_m2 > 0.0:
        r0 = max(math.sqrt(spill_area_m2 / math.pi), MIN_INITIAL_RADIUS_M)
    else:
        r0 = MIN_INITIAL_RADIUS_M

    # Coordinate arrays
    wind_lats = wind_ds["latitude"].values.astype(np.float64)
    wind_lons = wind_ds["longitude"].values.astype(np.float64)
    wind_times = wind_ds["time"].values

    curr_lats = curr_ds["latitude"].values.astype(np.float64)
    curr_lons = curr_ds["longitude"].values.astype(np.float64)
    curr_times = curr_ds["time"].values

    # Check origin inside spatial grids
    w_lat_min, w_lat_max = float(wind_lats.min()), float(wind_lats.max())
    w_lon_min, w_lon_max = float(wind_lons.min()), float(wind_lons.max())
    if not (w_lat_min <= origin_lat <= w_lat_max):
        raise SourceEstimationError(
            f"Origin latitude {origin_lat:.4f}° is outside ERA5 grid [{w_lat_min:.4f}, {w_lat_max:.4f}]"
        )
    if not (w_lon_min <= origin_lon <= w_lon_max):
        raise SourceEstimationError(
            f"Origin longitude {origin_lon:.4f}° is outside ERA5 grid [{w_lon_min:.4f}, {w_lon_max:.4f}]"
        )

    c_lat_min, c_lat_max = float(curr_lats.min()), float(curr_lats.max())
    c_lon_min, c_lon_max = float(curr_lons.min()), float(curr_lons.max())
    if not (c_lat_min <= origin_lat <= c_lat_max):
        raise SourceEstimationError(
            f"Origin latitude {origin_lat:.4f}° is outside CMEMS grid [{c_lat_min:.4f}, {c_lat_max:.4f}]"
        )
    if not (c_lon_min <= origin_lon <= c_lon_max):
        raise SourceEstimationError(
            f"Origin longitude {origin_lon:.4f}° is outside CMEMS grid [{c_lon_min:.4f}, {c_lon_max:.4f}]"
        )

    # Check temporal coverage of entire requested historical window
    obs_utc = _utc(observation_time)
    start_historical_time = obs_utc - timedelta(hours=lookback_hours)

    w_t_min = wind_times.min().astype("datetime64[ns]")
    w_t_max = wind_times.max().astype("datetime64[ns]")
    c_t_min = curr_times.min().astype("datetime64[ns]")
    c_t_max = curr_times.max().astype("datetime64[ns]")

    obs_np = np.datetime64(obs_utc.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), "s").astype("datetime64[ns]")
    start_np = np.datetime64(start_historical_time.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"), "s").astype("datetime64[ns]")

    time_tol_ns = np.timedelta64(3600, "s").astype("timedelta64[ns]")
    c_time_tol_ns = np.timedelta64(86400, "s").astype("timedelta64[ns]")

    if obs_np < (w_t_min - time_tol_ns) or obs_np > (w_t_max + time_tol_ns):
        raise SourceEstimationError(
            f"Observation time {obs_utc.isoformat()} is outside ERA5 data temporal range [{w_t_min}, {w_t_max}]."
        )
    if obs_np < (c_t_min - c_time_tol_ns) or obs_np > (c_t_max + c_time_tol_ns):
        raise SourceEstimationError(
            f"Observation time {obs_utc.isoformat()} is outside CMEMS data temporal range [{c_t_min}, {c_t_max}]."
        )

    # Fail closed if the historical lookback extends prior to available environmental data
    if start_np < (w_t_min - time_tol_ns):
        raise SourceEstimationError(
            f"Requested backward lookback ({lookback_hours} h) reaches {start_historical_time.isoformat()}, "
            f"which precedes the start of available ERA5 wind data ({w_t_min}). "
            "Cannot estimate historical source zone without complete environmental coverage."
        )
    if start_np < (c_t_min - c_time_tol_ns):
        raise SourceEstimationError(
            f"Requested backward lookback ({lookback_hours} h) reaches {start_historical_time.isoformat()}, "
            f"which precedes the start of available CMEMS current data ({c_t_min}). "
            "Cannot estimate historical source zone without complete environmental coverage."
        )

    # Interpolator caches: (var_name, time_index) -> RegularGridInterpolator
    wind_cache: dict[tuple[str, int], Any] = {}
    curr_cache: dict[tuple[str, int], Any] = {}

    def get_wind_interp(var: str, t_idx: int):
        key = (var, t_idx)
        if key not in wind_cache:
            w_data = wind_ds[var].values[t_idx]
            if w_data.ndim == 3:
                w_data = w_data[0]
            wind_cache[key] = _build_interpolator(
                wind_lats, wind_lons, w_data
            )
        return wind_cache[key]

    def get_curr_interp(var: str, t_idx: int):
        key = (var, t_idx)
        if key not in curr_cache:
            c_data = curr_ds[var].values[t_idx]
            if c_data.ndim == 3:
                c_data = c_data[0]
            curr_cache[key] = _build_interpolator(
                curr_lats, curr_lons, c_data
            )
        return curr_cache[key]

    steps: list[BackwardDriftStep] = []
    remaining = lookback_hours
    current_lon = origin_lon
    current_lat = origin_lat
    current_time = obs_utc
    cumulative_dist = 0.0
    elapsed_hours = 0.0

    while remaining > _STEP_EPSILON:
        dt_h = min(step_hours, remaining)
        dt_s = dt_h * 3600.0

        # Find nearest time index in each dataset for current_time
        wind_t_idx = _nearest_time_index(wind_times, current_time)
        curr_t_idx = _nearest_time_index(curr_times, current_time)

        query_pt = np.array([[current_lat, current_lon]], dtype=np.float64)

        try:
            u_wind = float(get_wind_interp("u10", wind_t_idx)(query_pt)[0])
            v_wind = float(get_wind_interp("v10", wind_t_idx)(query_pt)[0])
        except Exception as exc:
            raise SourceEstimationError(
                f"Spatial interpolation failed for ERA5 wind at t={current_time.isoformat()}, "
                f"position (lon={current_lon:.4f}, lat={current_lat:.4f}): {exc}"
            ) from exc

        try:
            u_curr = float(get_curr_interp("uo", curr_t_idx)(query_pt)[0])
            v_curr = float(get_curr_interp("vo", curr_t_idx)(query_pt)[0])
        except Exception as exc:
            raise SourceEstimationError(
                f"Spatial interpolation failed for CMEMS current at t={current_time.isoformat()}, "
                f"position (lon={current_lon:.4f}, lat={current_lat:.4f}): {exc}"
            ) from exc

        if not all(math.isfinite(v) for v in (u_wind, v_wind, u_curr, v_curr)):
            raise SourceEstimationError(
                f"Interpolated non-finite forcing at t={current_time.isoformat()}, "
                f"position (lon={current_lon:.4f}, lat={current_lat:.4f}). "
                "The backward trajectory may have hit land or nodata."
            )

        # Net drift velocity vector
        drift_u = u_curr + leeway_fraction * u_wind
        drift_v = v_curr + leeway_fraction * v_wind

        # Step BACKWARD: subtract displacement
        cos_lat = math.cos(math.radians(current_lat))
        if abs(cos_lat) < 1e-6:
            cos_lat = 1e-6

        dlon = -(drift_u * dt_s) / (cos_lat * _M_PER_DEG_LAT)
        dlat = -(drift_v * dt_s) / _M_PER_DEG_LAT

        new_lon = current_lon + dlon
        new_lat = current_lat + dlat
        step_time = current_time - timedelta(seconds=dt_s)
        elapsed_hours += dt_h

        step_dist = _haversine_m(current_lon, current_lat, new_lon, new_lat)
        cumulative_dist += step_dist

        # Expanding analytical uncertainty radius: R0 + c * elapsed_hours
        uncertainty_r = r0 + uncertainty_growth_m_per_h * elapsed_hours

        steps.append(
            BackwardDriftStep(
                timestamp=step_time,
                lon=new_lon,
                lat=new_lat,
                u_wind_ms=u_wind,
                v_wind_ms=v_wind,
                u_current_ms=u_curr,
                v_current_ms=v_curr,
                drift_u_ms=drift_u,
                drift_v_ms=drift_v,
                cumulative_backward_distance_m=cumulative_dist,
                uncertainty_radius_m=uncertainty_r,
            )
        )

        current_lon = new_lon
        current_lat = new_lat
        current_time = step_time
        remaining -= dt_h

    return steps


# ---------------------------------------------------------------------------
# Main Service Entrypoint
# ---------------------------------------------------------------------------

def compute_source_estimate_for_spill(
    investigation_id: str,
    spill_detection_id: str,
    origin_lon: float,
    origin_lat: float,
    observation_time: datetime,
    wind_asset: Asset,
    current_asset: Asset,
    lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
    step_hours: float = DEFAULT_STEP_HOURS,
    leeway_fraction: float = DEFAULT_LEEWAY_FRACTION,
    spill_area_m2: float | None = None,
    uncertainty_growth_m_per_h: float = DEFAULT_UNCERTAINTY_GROWTH_M_PER_H,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[SourceEstimateResult, Asset]:
    """Calculate the estimated source candidate zone and register the DRIFT_PRODUCT asset.

    Performs reverse Leeway-Euler integration from the spill centroid back in time,
    constructs the 32-vertex source candidate zone Polygon and centerline LineString,
    writes a GeoJSON FeatureCollection artifact, and registers the output.

    Args:
        investigation_id:            MARIS investigation identifier.
        spill_detection_id:          B3 SpillDetection.id (provenance link).
        origin_lon:                  Spill centroid longitude at observation (WGS84).
        origin_lat:                  Spill centroid latitude at observation (WGS84).
        observation_time:            SAR scene acquisition timestamp (anchor t=0).
        wind_asset:                  Registered C1 ENVIRONMENT_WIND Asset (ERA5 NetCDF).
        current_asset:               Registered C1 ENVIRONMENT_CURRENT Asset (CMEMS NetCDF).
        lookback_hours:              Hours to integrate backward (default 12.0).
        step_hours:                  Euler integration step in hours (default 1.0).
        leeway_fraction:             Wind leeway coefficient α (default 0.035).
        spill_area_m2:               Observed slick area in m2 (used for R0).
        uncertainty_growth_m_per_h:  Heuristic envelope expansion rate in m/hour (default 500).
        output_dir:                  Directory to save the GeoJSON artifact.
        registry:                    AssetRegistry instance (defaults to default_asset_registry).

    Returns:
        (SourceEstimateResult, Asset): Domain result and registered DRIFT_PRODUCT asset.

    Raises:
        SourceEstimationError: on validation failure or integration error.
    """
    if lookback_hours <= 0.0:
        raise SourceEstimationError(f"lookback_hours must be > 0, got {lookback_hours}")
    if step_hours <= 0.0:
        raise SourceEstimationError(f"step_hours must be > 0, got {step_hours}")
    if not math.isfinite(origin_lon) or not math.isfinite(origin_lat):
        raise SourceEstimationError(
            f"origin coordinates must be finite; got lon={origin_lon}, lat={origin_lat}"
        )
    if not (-180.0 <= origin_lon <= 180.0):
        raise SourceEstimationError(f"origin_lon {origin_lon} is outside WGS84 [-180, 180]")
    if not (-90.0 <= origin_lat <= 90.0):
        raise SourceEstimationError(f"origin_lat {origin_lat} is outside WGS84 [-90, 90]")

    wind_path = Path(wind_asset.location)
    if not wind_path.exists():
        raise SourceEstimationError(f"ERA5 wind asset does not exist at '{wind_asset.location}'")

    curr_path = Path(current_asset.location)
    if not curr_path.exists():
        raise SourceEstimationError(f"CMEMS current asset does not exist at '{current_asset.location}'")

    # --- Open datasets with guaranteed handle release ---
    wind_ds = None
    curr_raw = None
    try:
        try:
            wind_ds = _open_netcdf(wind_asset.location)
        except Exception as exc:
            raise SourceEstimationError(
                f"Failed to open ERA5 wind NetCDF '{wind_asset.location}': {exc}"
            ) from exc

        try:
            curr_raw = _open_netcdf(current_asset.location)
            if "depth" in curr_raw.dims or "depth" in curr_raw.coords:
                curr_ds = curr_raw.isel(depth=0)
            else:
                curr_ds = curr_raw
        except Exception as exc:
            raise SourceEstimationError(
                f"Failed to open CMEMS current NetCDF '{current_asset.location}': {exc}"
            ) from exc

        _validate_required_vars(wind_ds, ["u10", "v10"], "ERA5")
        _validate_required_vars(curr_ds, ["uo", "vo"], "CMEMS")
        _validate_required_dims(wind_ds, ["time", "latitude", "longitude"], "ERA5")
        _validate_required_dims(curr_ds, ["time", "latitude", "longitude"], "CMEMS")

        # Run backward integration
        steps = run_backward_drift(
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=observation_time,
            wind_ds=wind_ds,
            curr_ds=curr_ds,
            lookback_hours=lookback_hours,
            step_hours=step_hours,
            leeway_fraction=leeway_fraction,
            spill_area_m2=spill_area_m2,
            uncertainty_growth_m_per_h=uncertainty_growth_m_per_h,
        )
    finally:
        if wind_ds is not None:
            wind_ds.close()
        if curr_raw is not None:
            curr_raw.close()

    if not steps:
        raise SourceEstimationError("Backward integration produced no steps.")

    source_step = steps[-1]
    source_point_lon = source_step.lon
    source_point_lat = source_step.lat
    source_time = source_step.timestamp
    source_uncertainty_km = source_step.uncertainty_radius_m / 1000.0

    # Construct 32-vertex analytical candidate zone Polygon
    source_polygon = generate_source_candidate_polygon(
        center_lon=source_point_lon,
        center_lat=source_point_lat,
        radius_m=source_step.uncertainty_radius_m,
        num_vertices=POLYGON_VERTICES,
    )

    # Determine artifact directory
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize(investigation_id, "investigation")
        safe_sd = _sanitize(spill_detection_id, "spill")
        target_dir = (
            Path(settings.data_dir)
            / "derived"
            / safe_inv
            / "source_estimate"
            / safe_sd
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    # Build GeoJSON FeatureCollection with:
    # 1. Backward trajectory LineString (from origin back to source)
    # 2. Source candidate zone Polygon
    obs_utc = _utc(observation_time)
    trajectory_coords = [[origin_lon, origin_lat]] + [[s.lon, s.lat] for s in steps]

    feature_collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": trajectory_coords,
                },
                "properties": {
                    "feature_kind": "backward_drift_centerline",
                    "investigation_id": investigation_id,
                    "spill_detection_id": spill_detection_id,
                    "observation_time": obs_utc.isoformat(),
                    "source_time": source_time.isoformat(),
                    "lookback_hours": lookback_hours,
                    "step_count": len(steps),
                    "total_backward_distance_m": source_step.cumulative_backward_distance_m,
                },
            },
            {
                "type": "Feature",
                "geometry": source_polygon,
                "properties": {
                    "feature_kind": "source_candidate_zone",
                    "investigation_id": investigation_id,
                    "spill_detection_id": spill_detection_id,
                    "source_time": source_time.isoformat(),
                    "center_lon": source_point_lon,
                    "center_lat": source_point_lat,
                    "uncertainty_radius_km": source_uncertainty_km,
                    "scientific_framing": (
                        "Heuristic analytical uncertainty envelope. "
                        "NOT a 95% confidence region, probability distribution, "
                        "or statistically calibrated uncertainty."
                    ),
                },
            },
        ],
    }

    geojson_path = target_dir / "source_candidate_zone.geojson"
    with open(geojson_path, "w", encoding="utf-8") as fp:
        json.dump(feature_collection, fp, indent=2)

    # Assemble metadata
    metadata: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "integration_scheme": "euler_backward_time_reversal",
        "leeway_fraction": leeway_fraction,
        "lookback_hours": lookback_hours,
        "step_hours": step_hours,
        "step_count": len(steps),
        "uncertainty_growth_rate_m_per_h": uncertainty_growth_m_per_h,
        "source_uncertainty_radius_km": source_uncertainty_km,
        "wind_asset_id": wind_asset.id,
        "current_asset_id": current_asset.id,
        "spill_detection_id": spill_detection_id,
        "artifact_path": str(geojson_path.resolve()),
        "scientific_limitations": [
            (
                "Estimates a physically plausible historical source candidate zone from oceanographic/meteorological "
                "forcing. It does NOT identify a vessel and does NOT establish legal culpability or responsibility."
            ),
            (
                "Source-zone radius is a heuristic analytical search envelope assumption. "
                "It is NOT a 95% confidence region, probability distribution, or calibrated uncertainty."
            ),
            (
                "Constant leeway fraction α = 0.035 is an operational baseline, not calibrated for specific oil types."
            ),
            (
                "ERA5 10 m wind and CMEMS near-surface current proxy (≈ 0.5 m depth). Daily CMEMS leaves sub-daily "
                "variability unresolved."
            ),
            (
                "No oil weathering, emulsification, or evaporation modelled. Backtracking beyond 12–24 hours "
                "is increasingly unconstrained."
            ),
        ],
    }

    # Register DRIFT_PRODUCT asset
    retrieved_at = datetime.now(timezone.utc)
    artifact = AcquiredArtifact(
        asset_type=AssetType.DRIFT_PRODUCT,
        location=str(geojson_path.resolve()),
        source="source_estimation_service",
        acquisition_time=obs_utc,
        provenance=Provenance(
            product_id=spill_detection_id,
            retrieved_at=retrieved_at,
            processing_level="derived_source_candidate_zone",
            notes=(
                f"Historical source candidate zone derived via time-reversed Leeway-Euler backtracking. "
                f"Lookback={lookback_hours}h, dt={step_hours}h, α={leeway_fraction}. "
                f"Source radius={source_uncertainty_km:.2f}km. Heuristic analytical search envelope."
            ),
            extra={
                "model_version": MODEL_VERSION,
                "spill_detection_id": spill_detection_id,
                "wind_asset_id": wind_asset.id,
                "current_asset_id": current_asset.id,
                "lookback_hours": lookback_hours,
                "step_hours": step_hours,
                "leeway_fraction": leeway_fraction,
                "source_uncertainty_radius_km": source_uncertainty_km,
            },
        ),
        metadata={"investigation_id": investigation_id, "model_version": MODEL_VERSION},
    )

    target_registry = registry or default_asset_registry
    derived_asset = target_registry.register(
        investigation_id, "source_estimation_service", artifact
    )

    source_id = f"source-{_sanitize(spill_detection_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"

    result = SourceEstimateResult(
        id=source_id,
        investigation_id=investigation_id,
        spill_detection_id=spill_detection_id,
        wind_asset_id=wind_asset.id,
        current_asset_id=current_asset.id,
        asset_id=derived_asset.id,
        model_version=MODEL_VERSION,
        observation_time=obs_utc,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        source_time=source_time,
        source_point_lon=source_point_lon,
        source_point_lat=source_point_lat,
        lookback_hours=lookback_hours,
        step_hours=step_hours,
        leeway_fraction=leeway_fraction,
        source_uncertainty_radius_km=source_uncertainty_km,
        steps=steps,
        source_zone_geometry=source_polygon,
        metadata=metadata,
    )

    return result, derived_asset
