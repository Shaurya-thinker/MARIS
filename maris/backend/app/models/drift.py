"""Domain models for MARIS Stage D1 — Forward Drift Modelling.

DriftStep  : one Euler integration time-step (position, forcing, displacement).
DriftResult: full forward trajectory from a B3 spill observation and C1 metocean fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DriftStep(BaseModel):
    """A single forward-integration time step in a Leeway-Euler drift trajectory.

    Records the trajectory position AFTER integrating one dt, the interpolated ERA5
    and CMEMS forcing at that position, the derived net drift velocity, and the
    cumulative arc distance from the origin.
    """

    timestamp: datetime = Field(description="UTC timestamp at the end of this integration step")
    lon: float = Field(ge=-180.0, le=180.0, description="WGS84 longitude after this step")
    lat: float = Field(ge=-90.0, le=90.0, description="WGS84 latitude after this step")
    u_wind_ms: float = Field(description="ERA5 u10 (eastward 10 m wind) at (lon, lat, t) — m/s")
    v_wind_ms: float = Field(description="ERA5 v10 (northward 10 m wind) at (lon, lat, t) — m/s")
    u_current_ms: float = Field(description="CMEMS uo (eastward near-surface current) at (lon, lat, t) — m/s")
    v_current_ms: float = Field(description="CMEMS vo (northward near-surface current) at (lon, lat, t) — m/s")
    drift_u_ms: float = Field(description="Net drift U = u_current + α·u_wind (m/s)")
    drift_v_ms: float = Field(description="Net drift V = v_current + α·v_wind (m/s)")
    cumulative_distance_m: float = Field(ge=0.0, description="Great-circle arc distance from origin (m)")


class DriftResult(BaseModel):
    """Forward drift trajectory derived from a B3 spill observation and C1 metocean fields.

    This model is the primary output of Stage D1 (Leeway-Euler forward integration).
    It links back to the originating SpillDetection, ERA5 wind asset, CMEMS current asset,
    and the registered DRIFT_PRODUCT artifact in the AssetRegistry.

    Scientific limitations — D1 deterministic baseline, NOT operational forecast:
    - Leeway fraction α = 0.035 is an operational constant (ITOPF / NOAA GNOME /
      Breivik et al. 2011). It is NOT calibrated or tuned for this investigation,
      oil type, slick thickness, or emulsification state.
    - ERA5 10 m wind is used as a surface forcing proxy. Sea-state-dependent
      adjustment (e.g. Stokes drift correction) is not applied.
    - CMEMS GLORYS12V1 shallowest level (≈ 0.5 m depth) is used as a near-surface
      current proxy. Langmuir circulation, wind-wave mixing, and Stokes drift are
      excluded.
    - CMEMS temporal resolution is daily (P1D); sub-daily current variability is
      unresolved and not interpolated.
    - Euler 1st-order forward integration with flat-Earth position update. Appropriate
      for short-range drift (< ~500 km); geodesic stepping reserved for D2+.
    - No stochastic ensemble spread; endpoint_uncertainty_km is None in D1.
    - No backward drift, no source/origin estimation, no AIS correlation,
      no evidence fusion, no attribution.
    """

    id: str
    investigation_id: str
    spill_detection_id: str = Field(
        description="B3 SpillDetection ID this trajectory is derived from"
    )
    wind_asset_id: str = Field(
        description="C1 ENVIRONMENT_WIND Asset ID (ERA5 NetCDF registered in AssetRegistry)"
    )
    current_asset_id: str = Field(
        description="C1 ENVIRONMENT_CURRENT Asset ID (CMEMS NetCDF registered in AssetRegistry)"
    )
    asset_id: str = Field(
        description="Registered DRIFT_PRODUCT Asset ID in AssetRegistry"
    )
    model_version: str = Field(
        default="leeway_euler_v1",
        description="Drift model identifier; used for provenance and reproducibility",
    )
    observation_time: datetime = Field(
        description="SAR scene acquisition time — trajectory integration anchor (t=0)"
    )
    origin_lon: float = Field(
        ge=-180.0, le=180.0,
        description="Spill centroid longitude at observation_time (WGS84)",
    )
    origin_lat: float = Field(
        ge=-90.0, le=90.0,
        description="Spill centroid latitude at observation_time (WGS84)",
    )
    steps: list[DriftStep] = Field(
        description="Ordered forward trajectory steps (one per integration dt)"
    )
    total_duration_hours: float = Field(
        gt=0.0,
        description="Requested total drift duration (hours)",
    )
    step_hours: float = Field(
        gt=0.0,
        description="Euler integration time step size (hours). Final step may be shorter.",
    )
    leeway_fraction: float = Field(
        ge=0.0,
        description="Wind leeway coefficient α. D1 default: 0.035 (3.5% of 10 m wind speed).",
    )
    endpoint_lon: float = Field(
        ge=-180.0, le=180.0,
        description="Final trajectory longitude = steps[-1].lon",
    )
    endpoint_lat: float = Field(
        ge=-90.0, le=90.0,
        description="Final trajectory latitude = steps[-1].lat",
    )
    endpoint_uncertainty_km: float | None = Field(
        default=None,
        description=(
            "Spatial uncertainty at the endpoint (km). "
            "Not computed in D1 (no stochastic ensemble). Reserved for D2+."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
