"""Domain models for MARIS Stage D3 — Backward Drift & Source Candidate Zone Estimation.

BackwardDriftStep   : one backward Euler time-step (historical position, metocean forcing, dispersion radius).
SourceEstimateResult: complete backward trajectory and analytical source candidate zone from a B3 spill.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BackwardDriftStep(BaseModel):
    """A single backward-integration time step tracing an oil slick's historical trajectory.

    Records the trajectory position stepping backwards in time (t - dt), the metocean
    forcing interpolated at that location and timestamp, the derived drift velocity,
    the cumulative backward distance traversed from the observed spill centroid,
    and the expanding analytical uncertainty envelope radius at this lookback horizon.
    """

    timestamp: datetime = Field(
        description="UTC timestamp at this historical integration step (precedes observation_time)"
    )
    lon: float = Field(
        ge=-180.0, le=180.0,
        description="WGS84 longitude at this historical backward step",
    )
    lat: float = Field(
        ge=-90.0, le=90.0,
        description="WGS84 latitude at this historical backward step",
    )
    u_wind_ms: float = Field(
        description="ERA5 u10 (eastward 10 m wind) at (lon, lat, t) — m/s"
    )
    v_wind_ms: float = Field(
        description="ERA5 v10 (northward 10 m wind) at (lon, lat, t) — m/s"
    )
    u_current_ms: float = Field(
        description="CMEMS uo (eastward near-surface current) at (lon, lat, t) — m/s"
    )
    v_current_ms: float = Field(
        description="CMEMS vo (northward near-surface current) at (lon, lat, t) — m/s"
    )
    drift_u_ms: float = Field(
        description="Net drift U = u_current + α·u_wind (m/s) at this step"
    )
    drift_v_ms: float = Field(
        description="Net drift V = v_current + α·v_wind (m/s) at this step"
    )
    cumulative_backward_distance_m: float = Field(
        ge=0.0,
        description="Cumulative great-circle arc distance traced backward from origin (m)",
    )
    uncertainty_radius_m: float = Field(
        ge=0.0,
        description=(
            "Heuristic analytical uncertainty radius (m) at this lookback time. "
            "NOTE: This is an analytical search envelope assumption, NOT a statistically "
            "calibrated probability or confidence level."
        ),
    )


class SourceEstimateResult(BaseModel):
    """Estimated source candidate zone and backward drift trajectory for an observed spill.

    This model is the primary output of Stage D3 (time-reversed Leeway-Euler integration
    coupled with an analytical source candidate zone expansion). It links back to the
    originating SpillDetection, ERA5 wind asset, CMEMS current asset, and the registered
    DRIFT_PRODUCT artifact in the AssetRegistry.

    Scientific limitations & operational framing:
    - This model estimates a physically plausible historical source candidate zone
      based on oceanographic and meteorological forcing. It does NOT identify a vessel,
      and does NOT establish culpability or legal responsibility.
    - Reverse integration: backward advection reverses the deterministic velocity vector
      (-v_drift) from the observed spill centroid back in time.
    - Analytical uncertainty envelope: R(τ) = R0 + c·τ is a heuristic operational
      search boundary reflecting unmodelled turbulent dispersion, sub-grid eddy mixing,
      and metocean forcing uncertainty. It MUST NOT be interpreted as a calibrated
      probability distribution, 95% confidence region, or attribution score.
    - Constant leeway fraction α = 0.035 is an operational baseline (ITOPF / NOAA GNOME).
    - ERA5 10 m wind and CMEMS near-surface current (depth ≈ 0.5 m) are used as forcing proxies.
    - CMEMS daily temporal resolution leaves sub-daily current variability unresolved.
    - No weathering, emulsification, or evaporation model is applied. Slicks older than
      12–24 hours may have experienced substantial physical weathering.
    - AIS vessel correlation and attribution belong strictly to Stages E and F.
    """

    id: str
    investigation_id: str
    spill_detection_id: str = Field(
        description="B3 SpillDetection ID this source estimate is derived from"
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
        default="leeway_euler_backward_v1",
        description="Backward drift model version identifier for provenance and reproducibility",
    )
    observation_time: datetime = Field(
        description="SAR scene acquisition timestamp — backward trajectory anchor (t=0)"
    )
    origin_lon: float = Field(
        ge=-180.0, le=180.0,
        description="Spill centroid longitude at observation_time (WGS84)",
    )
    origin_lat: float = Field(
        ge=-90.0, le=90.0,
        description="Spill centroid latitude at observation_time (WGS84)",
    )
    source_time: datetime = Field(
        description="Estimated historical release timestamp = observation_time - lookback_hours"
    )
    source_point_lon: float = Field(
        ge=-180.0, le=180.0,
        description="Estimated release point longitude at source_time (WGS84)",
    )
    source_point_lat: float = Field(
        ge=-90.0, le=90.0,
        description="Estimated release point latitude at source_time (WGS84)",
    )
    lookback_hours: float = Field(
        gt=0.0,
        description="Requested backward integration duration (hours)",
    )
    step_hours: float = Field(
        gt=0.0,
        description="Backward integration time step size (hours). Final step may be shorter.",
    )
    leeway_fraction: float = Field(
        ge=0.0,
        description="Wind leeway coefficient α (default 0.035 = 3.5%).",
    )
    source_uncertainty_radius_km: float = Field(
        ge=0.0,
        description=(
            "Heuristic analytical uncertainty envelope radius (km) at source_time. "
            "Defines the spatial extent of the source candidate zone."
        ),
    )
    steps: list[BackwardDriftStep] = Field(
        description="Ordered backward trajectory steps from observation_time back to source_time"
    )
    source_zone_geometry: dict[str, Any] = Field(
        description="GeoJSON Polygon geometry representing the 32-vertex source candidate zone"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
