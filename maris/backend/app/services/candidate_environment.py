"""Stage C2 — Candidate-Level Environmental Context & SAR Lookalike Gating.

Connects maritime dark anomalies detected in Stage B3 to authoritative metocean
forcing datasets (ERA5 10 m wind and CMEMS surface currents) acquired in Stage C1.

CRITICAL SCIENTIFIC PRINCIPLES:
1. Environmental information is corroborating evidence, NOT ground truth.
2. Favorable wind speed indicates that the ocean surface supports active Bragg scattering
   where capillary wave damping produces observable radar backscatter contrast.
   It does NOT prove that a dark feature is petroleum hydrocarbon.
3. Dark features under low wind (< 2.5 m/s) suffer from calm-sea look-alike ambiguity.
4. Dark features under high wind (> 12 m/s) suffer from slick dispersion and contrast loss.
5. This module does NOT modify upstream B3 detector confidence scores or thresholds.
6. This module does NOT drop or reclassify candidates as confirmed spills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy.interpolate import RegularGridInterpolator
import xarray as xr

from app.models.asset import Asset
from app.services.spill_detection.base import SpillDetectionResult, SpillRegionStats


# ---------------------------------------------------------------------------
# Enums and Scientific Thresholds
# ---------------------------------------------------------------------------

class WindRegime(str, Enum):
    """Deterministic physical SAR wind regime classification."""

    CALM_WATER_LOOKALIKE = "CALM_WATER_LOOKALIKE"
    FAVORABLE_DETECTION_WINDOW = "FAVORABLE_DETECTION_WINDOW"
    HIGH_WIND_DISPERSION = "HIGH_WIND_DISPERSION"
    UNKNOWN = "UNKNOWN"


# Peer-reviewed physical thresholds for C-band SAR oil-slick contrast
CALM_WATER_THRESHOLD_MS: float = 2.5
HIGH_WIND_THRESHOLD_MS: float = 12.0

WIND_REGIME_DESCRIPTIONS: dict[WindRegime, str] = {
    WindRegime.CALM_WATER_LOOKALIKE: (
        "Wind speed is below the nominal active Bragg-scattering regime; "
        "calm-sea dark anomalies are a plausible SAR look-alike."
    ),
    WindRegime.FAVORABLE_DETECTION_WINDOW: (
        "Wind speed is within the nominal range where oil-film damping "
        "can produce observable SAR contrast."
    ),
    WindRegime.HIGH_WIND_DISPERSION: (
        "Wind speed exceeds the nominal detection window; "
        "strong mixing may reduce or disperse surface slick signatures."
    ),
    WindRegime.UNKNOWN: (
        "Wind speed is unavailable; environmental SAR look-alike consistency cannot be assessed."
    ),
}


class CandidateEnvironmentError(Exception):
    """Raised when candidate environmental sampling encounters fatal corruption."""


# ---------------------------------------------------------------------------
# Data Contracts
# ---------------------------------------------------------------------------

class WindEvidence(BaseModel):
    """Atmospheric wind evidence sampled at candidate location and sensing time."""

    u10_ms: float | None = Field(default=None, description="Eastward 10 m wind component (m/s)")
    v10_ms: float | None = Field(default=None, description="Northward 10 m wind component (m/s)")
    speed_ms: float | None = Field(default=None, description="10 m scalar wind speed (m/s)")
    direction_from_deg: float | None = Field(
        default=None, description="Meteorological wind direction: where wind blows FROM (0-360 deg)"
    )
    source: str | None = Field(default=None, description="Data provider / dataset name")
    source_timestamp: str | None = Field(default=None, description="Timestamp(s) used for extraction")
    temporal_interpolation: str | None = Field(default=None, description="Temporal interpolation method")
    spatial_interpolation: str | None = Field(default=None, description="Spatial interpolation method")


class CurrentEvidence(BaseModel):
    """Near-surface ocean current evidence sampled at candidate location."""

    uo_ms: float | None = Field(default=None, description="Eastward ocean surface current (m/s)")
    vo_ms: float | None = Field(default=None, description="Northward ocean surface current (m/s)")
    speed_ms: float | None = Field(default=None, description="Ocean current speed (m/s)")
    direction_to_deg: float | None = Field(
        default=None, description="Oceanographic current direction: where current flows TOWARDS (0-360 deg)"
    )
    source: str | None = Field(default=None, description="Data provider / dataset name")
    source_timestamp: str | None = Field(default=None, description="Timestamp used for extraction")
    temporal_interpolation: str | None = Field(default=None, description="Temporal extraction method")
    spatial_interpolation: str | None = Field(default=None, description="Spatial interpolation method")


class WindRegimeAssessment(BaseModel):
    """Physical assessment of SAR imaging conditions at candidate location."""

    classification: WindRegime = Field(..., description="Categorical wind regime")
    evidence_text: str = Field(..., description="Scientific explanatory context")
    lookalike_risk: str = Field(
        ..., description="Risk of environmental look-alikes (HIGH_LOOKALIKE_PROBABILITY / LOW_LOOKALIKE_PROBABILITY / HIGH_DISPERSION_RISK / UNKNOWN)"
    )
    damping_consistent: bool | None = Field(
        default=None, description="True if wind conditions physically permit oil-film Bragg damping contrast"
    )


class EnvironmentalDataQuality(BaseModel):
    """Integrity and provenance diagnostics for candidate sampling."""

    wind_available: bool = Field(default=False, description="True if valid wind vector was sampled")
    current_available: bool = Field(default=False, description="True if valid ocean current was sampled")
    interpolation_valid: bool = Field(default=False, description="True if spatio-temporal interpolation succeeded")
    outside_coverage: bool = Field(default=False, description="True if candidate centroid fell outside grid bounds")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal data issues or caveats")


class CandidateEnvironment(BaseModel):
    """Complete candidate-level environmental context."""

    wind: WindEvidence = Field(default_factory=WindEvidence)
    current: CurrentEvidence = Field(default_factory=CurrentEvidence)
    regime: WindRegimeAssessment
    data_quality: EnvironmentalDataQuality


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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


def _open_dataset(source: str | Path | Asset | xr.Dataset | None) -> xr.Dataset | None:
    """Safely open an Asset, file path, directory, or return existing xr.Dataset."""
    if source is None:
        return None
    if isinstance(source, xr.Dataset):
        return _normalize_netcdf_dataset(source)
    if isinstance(source, Asset):
        loc = source.location
    else:
        loc = str(source)

    path = Path(loc)
    if path.is_file():
        ds = xr.open_dataset(str(path), engine="netcdf4")
        return _normalize_netcdf_dataset(ds)
    if path.is_dir():
        nc_files = sorted(path.glob("*.nc"))
        if not nc_files:
            return None
        if len(nc_files) == 1:
            ds = xr.open_dataset(str(nc_files[0]), engine="netcdf4")
            return _normalize_netcdf_dataset(ds)
        ds = xr.open_mfdataset([str(f) for f in nc_files], engine="netcdf4", combine="by_coords")
        return _normalize_netcdf_dataset(ds)
    return None


def classify_wind_regime(wind_speed_ms: float | None) -> WindRegimeAssessment:
    """Determine physical SAR wind regime and scientific evidence text."""
    if wind_speed_ms is None or math.isnan(wind_speed_ms):
        return WindRegimeAssessment(
            classification=WindRegime.UNKNOWN,
            evidence_text=WIND_REGIME_DESCRIPTIONS[WindRegime.UNKNOWN],
            lookalike_risk="UNKNOWN",
            damping_consistent=None,
        )
    if wind_speed_ms < CALM_WATER_THRESHOLD_MS:
        return WindRegimeAssessment(
            classification=WindRegime.CALM_WATER_LOOKALIKE,
            evidence_text=WIND_REGIME_DESCRIPTIONS[WindRegime.CALM_WATER_LOOKALIKE],
            lookalike_risk="HIGH_LOOKALIKE_PROBABILITY",
            damping_consistent=False,
        )
    elif wind_speed_ms <= HIGH_WIND_THRESHOLD_MS:
        return WindRegimeAssessment(
            classification=WindRegime.FAVORABLE_DETECTION_WINDOW,
            evidence_text=WIND_REGIME_DESCRIPTIONS[WindRegime.FAVORABLE_DETECTION_WINDOW],
            lookalike_risk="LOW_LOOKALIKE_PROBABILITY",
            damping_consistent=True,
        )
    else:
        return WindRegimeAssessment(
            classification=WindRegime.HIGH_WIND_DISPERSION,
            evidence_text=WIND_REGIME_DESCRIPTIONS[WindRegime.HIGH_WIND_DISPERSION],
            lookalike_risk="HIGH_DISPERSION_RISK",
            damping_consistent=False,
        )


def _compute_wind_direction_from_deg(u: float, v: float) -> float:
    """Compute meteorological wind direction (direction wind blows FROM, 0-360 deg)."""
    # u is eastward (+x), v is northward (+y)
    # Direction wind blows towards: atan2(u, v)
    # Direction wind blows from: atan2(-u, -v)
    deg = math.degrees(math.atan2(-u, -v))
    return float((deg + 360.0) % 360.0)


def _compute_current_direction_to_deg(uo: float, vo: float) -> float:
    """Compute oceanographic current direction (direction current flows TOWARDS, 0-360 deg)."""
    deg = math.degrees(math.atan2(uo, vo))
    return float((deg + 360.0) % 360.0)


# ---------------------------------------------------------------------------
# Candidate Environmental Sampler
# ---------------------------------------------------------------------------

class CandidateEnvironmentSampler:
    """Efficient batch sampler for candidate-level environmental intelligence.

    Pre-computes 2D regular grid spatial interpolators at the exact Sentinel-1
    sensing time (performing linear temporal interpolation of wind vector grids
    once), enabling O(1) sampling across arbitrary numbers of candidate geometries.
    """

    def __init__(
        self,
        sensing_time: datetime,
        era5_source: str | Path | Asset | xr.Dataset | None = None,
        cmems_source: str | Path | Asset | xr.Dataset | None = None,
        era5_provider_name: str = "Copernicus Climate Data Store / ERA5",
        cmems_provider_name: str = "Copernicus Marine Service / GLORYS12V1",
    ) -> None:
        self.sensing_time = _utc(sensing_time)
        self.era5_provider_name = era5_provider_name
        self.cmems_provider_name = cmems_provider_name

        self._ds_era5 = _open_dataset(era5_source)
        self._ds_cmems = _open_dataset(cmems_source)

        self._era5_warnings: list[str] = []
        self._cmems_warnings: list[str] = []

        self._wind_u_interp: RegularGridInterpolator | None = None
        self._wind_v_interp: RegularGridInterpolator | None = None
        self._era5_source_timestamp: str | None = None
        self._era5_temporal_method: str | None = None
        self._era5_lat_bounds: tuple[float, float] | None = None
        self._era5_lon_bounds: tuple[float, float] | None = None

        self._curr_u_interp: RegularGridInterpolator | None = None
        self._curr_v_interp: RegularGridInterpolator | None = None
        self._cmems_source_timestamp: str | None = None
        self._cmems_temporal_method: str | None = None
        self._cmems_lat_bounds: tuple[float, float] | None = None
        self._cmems_lon_bounds: tuple[float, float] | None = None

        self._build_wind_interpolator()
        self._build_current_interpolator()

    def _build_wind_interpolator(self) -> None:
        if self._ds_era5 is None:
            self._era5_warnings.append("ERA5 wind dataset is unavailable")
            return

        ds = self._ds_era5
        if "u10" not in ds or "v10" not in ds:
            self._era5_warnings.append("ERA5 dataset missing u10 or v10 variables")
            return

        if "latitude" not in ds.coords or "longitude" not in ds.coords:
            self._era5_warnings.append("ERA5 dataset missing latitude or longitude coordinates")
            return

        lats = np.asarray(ds["latitude"].values, dtype=np.float64)
        lons = np.asarray(ds["longitude"].values, dtype=np.float64)

        self._era5_lat_bounds = (float(np.min(lats)), float(np.max(lats)))
        self._era5_lon_bounds = (float(np.min(lons)), float(np.max(lons)))

        # Time handling & temporal interpolation
        if "time" in ds.coords and ds["time"].size > 1:
            times = pd.to_datetime(ds["time"].values)
            # Normalize times to tz-aware UTC
            if times.tz is None:
                times = times.tz_localize("UTC")
            else:
                times = times.tz_convert("UTC")

            target_ts = pd.Timestamp(self.sensing_time)
            if target_ts.tz is None:
                target_ts = target_ts.tz_localize("UTC")
            else:
                target_ts = target_ts.tz_convert("UTC")

            if target_ts <= times[0]:
                u_slice = ds["u10"].isel(time=0).values
                v_slice = ds["v10"].isel(time=0).values
                self._era5_source_timestamp = str(times[0])
                self._era5_temporal_method = "nearest_boundary_step"
                if target_ts < times[0] - pd.Timedelta(hours=2):
                    self._era5_warnings.append(
                        f"Sensing time {self.sensing_time.isoformat()} precedes ERA5 start {times[0].isoformat()}"
                    )
            elif target_ts >= times[-1]:
                u_slice = ds["u10"].isel(time=-1).values
                v_slice = ds["v10"].isel(time=-1).values
                self._era5_source_timestamp = str(times[-1])
                self._era5_temporal_method = "nearest_boundary_step"
                if target_ts > times[-1] + pd.Timedelta(hours=2):
                    self._era5_warnings.append(
                        f"Sensing time {self.sensing_time.isoformat()} succeeds ERA5 end {times[-1].isoformat()}"
                    )
            else:
                idx = int(times.searchsorted(target_ts))
                t1, t2 = times[idx - 1], times[idx]
                if t1 == t2:
                    u_slice = ds["u10"].isel(time=idx - 1).values
                    v_slice = ds["v10"].isel(time=idx - 1).values
                    self._era5_source_timestamp = str(t1)
                    self._era5_temporal_method = "exact_step"
                else:
                    weight = float((target_ts - t1) / (t2 - t1))
                    u1 = ds["u10"].isel(time=idx - 1).values
                    u2 = ds["u10"].isel(time=idx).values
                    v1 = ds["v10"].isel(time=idx - 1).values
                    v2 = ds["v10"].isel(time=idx).values
                    # Vector interpolation prior to magnitude/direction calculation
                    u_slice = (1.0 - weight) * u1 + weight * u2
                    v_slice = (1.0 - weight) * v1 + weight * v2
                    self._era5_source_timestamp = f"{t1.isoformat()} / {t2.isoformat()}"
                    self._era5_temporal_method = f"linear_vector_interpolation (weight={weight:.3f})"
        elif "time" in ds.coords and ds["time"].size == 1:
            u_slice = ds["u10"].isel(time=0).values
            v_slice = ds["v10"].isel(time=0).values
            self._era5_source_timestamp = str(pd.to_datetime(ds["time"].values[0]))
            self._era5_temporal_method = "single_time_step"
        else:
            u_slice = ds["u10"].values
            v_slice = ds["v10"].values
            self._era5_source_timestamp = "undated"
            self._era5_temporal_method = "static_slice"

        u_2d = np.asarray(u_slice, dtype=np.float64)
        v_2d = np.asarray(v_slice, dtype=np.float64)

        # Coordinate monotonicity: RegularGridInterpolator requires strictly ascending axes
        lat_arr = lats.copy()
        lon_arr = lons.copy()

        if lat_arr.size > 1 and lat_arr[0] > lat_arr[-1]:
            lat_arr = lat_arr[::-1]
            u_2d = u_2d[::-1, :]
            v_2d = v_2d[::-1, :]

        if lon_arr.size > 1 and lon_arr[0] > lon_arr[-1]:
            lon_arr = lon_arr[::-1]
            u_2d = u_2d[:, ::-1]
            v_2d = v_2d[:, ::-1]

        self._wind_u_interp = RegularGridInterpolator(
            (lat_arr, lon_arr),
            u_2d,
            method="linear",
            bounds_error=False,
            fill_value=np.nan,
        )
        self._wind_v_interp = RegularGridInterpolator(
            (lat_arr, lon_arr),
            v_2d,
            method="linear",
            bounds_error=False,
            fill_value=np.nan,
        )

    def _build_current_interpolator(self) -> None:
        if self._ds_cmems is None:
            self._cmems_warnings.append("CMEMS ocean current dataset is unavailable")
            return

        ds = self._ds_cmems
        if "uo" not in ds or "vo" not in ds:
            self._cmems_warnings.append("CMEMS dataset missing uo or vo variables")
            return

        if "latitude" not in ds.coords or "longitude" not in ds.coords:
            self._cmems_warnings.append("CMEMS dataset missing latitude or longitude coordinates")
            return

        lats = np.asarray(ds["latitude"].values, dtype=np.float64)
        lons = np.asarray(ds["longitude"].values, dtype=np.float64)

        self._cmems_lat_bounds = (float(np.min(lats)), float(np.max(lats)))
        self._cmems_lon_bounds = (float(np.min(lons)), float(np.max(lons)))

        if "time" in ds.coords:
            cmems_times = pd.to_datetime(ds["time"].values)
            target_ts = pd.Timestamp(self.sensing_time)
            if cmems_times.tz is None and target_ts.tz is not None:
                target_sel_time = target_ts.tz_localize(None)
            elif cmems_times.tz is not None and target_ts.tz is None:
                target_sel_time = target_ts.tz_localize("UTC")
            else:
                target_sel_time = target_ts
            # CMEMS is daily mean: nearest day extraction
            cmems_slice = ds.sel(time=target_sel_time, method="nearest")
            uo_slice = cmems_slice["uo"].values
            vo_slice = cmems_slice["vo"].values
            self._cmems_source_timestamp = str(pd.to_datetime(cmems_slice["time"].values))
            self._cmems_temporal_method = "nearest_daily_mean"
        else:
            uo_slice = ds["uo"].values
            vo_slice = ds["vo"].values
            self._cmems_source_timestamp = "undated"
            self._cmems_temporal_method = "static_slice"

        uo_2d = np.asarray(uo_slice, dtype=np.float64)
        vo_2d = np.asarray(vo_slice, dtype=np.float64)

        lat_arr = lats.copy()
        lon_arr = lons.copy()

        if lat_arr.size > 1 and lat_arr[0] > lat_arr[-1]:
            lat_arr = lat_arr[::-1]
            uo_2d = uo_2d[::-1, :]
            vo_2d = vo_2d[::-1, :]

        if lon_arr.size > 1 and lon_arr[0] > lon_arr[-1]:
            lon_arr = lon_arr[::-1]
            uo_2d = uo_2d[:, ::-1]
            vo_2d = vo_2d[:, ::-1]

        self._curr_u_interp = RegularGridInterpolator(
            (lat_arr, lon_arr),
            uo_2d,
            method="linear",
            bounds_error=False,
            fill_value=np.nan,
        )
        self._curr_v_interp = RegularGridInterpolator(
            (lat_arr, lon_arr),
            vo_2d,
            method="linear",
            bounds_error=False,
            fill_value=np.nan,
        )

    def sample_point(self, lat: float, lon: float) -> CandidateEnvironment:
        """Sample candidate-level environmental context at (lat, lon)."""
        warnings: list[str] = list(self._era5_warnings) + list(self._cmems_warnings)
        outside_coverage = False

        # --- 1. Wind Sampling ---
        wind_available = False
        u10: float | None = None
        v10: float | None = None
        wind_speed: float | None = None
        wind_dir: float | None = None
        spatial_interp_wind: str | None = None

        if self._wind_u_interp is not None and self._wind_v_interp is not None:
            # Check domain boundaries
            lat_min, lat_max = self._era5_lat_bounds  # type: ignore
            lon_min, lon_max = self._era5_lon_bounds  # type: ignore

            # Longitude normalization if grid is [0, 360) and point is negative
            q_lon = lon
            if lon_min >= 0.0 and q_lon < 0.0:
                q_lon += 360.0
            elif lon_max <= 180.0 and q_lon > 180.0:
                q_lon -= 360.0

            if not (lat_min <= lat <= lat_max and lon_min <= q_lon <= lon_max):
                outside_coverage = True
                warnings.append(
                    f"Candidate coordinate ({lat:.4f} N, {lon:.4f} E) is outside ERA5 domain "
                    f"[{lat_min:.2f}..{lat_max:.2f} N, {lon_min:.2f}..{lon_max:.2f} E]"
                )
            else:
                interp_pt = np.array([[lat, q_lon]], dtype=np.float64)
                u_val = float(self._wind_u_interp(interp_pt)[0])
                v_val = float(self._wind_v_interp(interp_pt)[0])

                if np.isfinite(u_val) and np.isfinite(v_val):
                    u10 = round(u_val, 4)
                    v10 = round(v_val, 4)
                    wind_speed = round(float(math.hypot(u_val, v_val)), 2)
                    wind_dir = round(_compute_wind_direction_from_deg(u_val, v_val), 1)
                    wind_available = True
                    spatial_interp_wind = "bilinear_regular_grid"
                else:
                    warnings.append("ERA5 interpolated wind returned non-finite value")

        # --- 2. Ocean Current Sampling ---
        current_available = False
        uo: float | None = None
        vo: float | None = None
        curr_speed: float | None = None
        curr_dir: float | None = None
        spatial_interp_curr: str | None = None

        if self._curr_u_interp is not None and self._curr_v_interp is not None:
            lat_min, lat_max = self._cmems_lat_bounds  # type: ignore
            lon_min, lon_max = self._cmems_lon_bounds  # type: ignore

            q_lon = lon
            if lon_min >= 0.0 and q_lon < 0.0:
                q_lon += 360.0
            elif lon_max <= 180.0 and q_lon > 180.0:
                q_lon -= 360.0

            if not (lat_min <= lat <= lat_max and lon_min <= q_lon <= lon_max):
                warnings.append(
                    f"Candidate coordinate ({lat:.4f} N, {lon:.4f} E) is outside CMEMS domain "
                    f"[{lat_min:.2f}..{lat_max:.2f} N, {lon_min:.2f}..{lon_max:.2f} E]"
                )
            else:
                interp_pt = np.array([[lat, q_lon]], dtype=np.float64)
                uo_val = float(self._curr_u_interp(interp_pt)[0])
                vo_val = float(self._curr_v_interp(interp_pt)[0])

                if np.isfinite(uo_val) and np.isfinite(vo_val):
                    uo = round(uo_val, 4)
                    vo = round(vo_val, 4)
                    curr_speed = round(float(math.hypot(uo_val, vo_val)), 4)
                    curr_dir = round(_compute_current_direction_to_deg(uo_val, vo_val), 1)
                    current_available = True
                    spatial_interp_curr = "bilinear_regular_grid"
                else:
                    # In CMEMS, nearshore/land pixels are NaNs
                    warnings.append(
                        "Ocean current unavailable at candidate location (nearshore/land-masked cell in CMEMS grid)"
                    )

        # --- 3. Regime Classification ---
        regime_assessment = classify_wind_regime(wind_speed)

        # Assemble Evidence Objects
        wind_ev = WindEvidence(
            u10_ms=u10,
            v10_ms=v10,
            speed_ms=wind_speed,
            direction_from_deg=wind_dir,
            source=self.era5_provider_name if wind_available else None,
            source_timestamp=self._era5_source_timestamp if wind_available else None,
            temporal_interpolation=self._era5_temporal_method if wind_available else None,
            spatial_interpolation=spatial_interp_wind,
        )

        curr_ev = CurrentEvidence(
            uo_ms=uo,
            vo_ms=vo,
            speed_ms=curr_speed,
            direction_to_deg=curr_dir,
            source=self.cmems_provider_name if current_available else None,
            source_timestamp=self._cmems_source_timestamp if current_available else None,
            temporal_interpolation=self._cmems_temporal_method if current_available else None,
            spatial_interpolation=spatial_interp_curr,
        )

        dq = EnvironmentalDataQuality(
            wind_available=wind_available,
            current_available=current_available,
            interpolation_valid=wind_available,
            outside_coverage=outside_coverage,
            warnings=warnings,
        )

        return CandidateEnvironment(
            wind=wind_ev,
            current=curr_ev,
            regime=regime_assessment,
            data_quality=dq,
        )


# ---------------------------------------------------------------------------
# High-Level Integration Utilities
# ---------------------------------------------------------------------------

def sample_candidate_environment(
    candidates: Sequence[SpillRegionStats] | list[dict[str, Any]] | list[tuple[float, float]],
    sensing_time: datetime,
    era5_source: str | Path | Asset | xr.Dataset | None = None,
    cmems_source: str | Path | Asset | xr.Dataset | None = None,
    era5_provider_name: str = "Copernicus Climate Data Store / ERA5",
    cmems_provider_name: str = "Copernicus Marine Service / GLORYS12V1",
) -> list[CandidateEnvironment]:
    """Sample environmental context for a list of candidates or centroids.

    Args:
        candidates: Sequence of SpillRegionStats objects, GeoJSON feature dicts,
                    or (lon, lat) / (lat, lon) centroid tuples.
        sensing_time: Sentinel-1 scene acquisition timestamp.
        era5_source: ERA5 NetCDF asset, path, or Dataset.
        cmems_source: CMEMS NetCDF asset, path, or Dataset.
        era5_provider_name: Provenance identifier for atmospheric dataset.
        cmems_provider_name: Provenance identifier for marine dataset.

    Returns:
        List of CandidateEnvironment instances, 1-to-1 with input candidates.
    """
    sampler = CandidateEnvironmentSampler(
        sensing_time=sensing_time,
        era5_source=era5_source,
        cmems_source=cmems_source,
        era5_provider_name=era5_provider_name,
        cmems_provider_name=cmems_provider_name,
    )

    results: list[CandidateEnvironment] = []

    for cand in candidates:
        lat, lon = _extract_centroid(cand)
        env = sampler.sample_point(lat=lat, lon=lon)
        results.append(env)

    return results


def _extract_centroid(cand: Any) -> tuple[float, float]:
    """Extract (lat, lon) from various candidate representations."""
    # SpillRegionStats dataclass: centroid is (lon, lat)
    if isinstance(cand, SpillRegionStats):
        return float(cand.centroid[1]), float(cand.centroid[0])

    if hasattr(cand, "centroid") and isinstance(cand.centroid, (tuple, list)):
        return float(cand.centroid[1]), float(cand.centroid[0])

    # GeoJSON Feature dict
    if isinstance(cand, dict):
        props = cand.get("properties", {})
        if "centroid" in props and isinstance(props["centroid"], (tuple, list)):
            # GeoJSON convention: centroid is [lon, lat]
            return float(props["centroid"][1]), float(props["centroid"][0])
        # GeoJSON geometry centroid fallback
        geom = cand.get("geometry", {})
        if geom.get("type") == "Point" and "coordinates" in geom:
            return float(geom["coordinates"][1]), float(geom["coordinates"][0])
        if "lat" in cand and "lon" in cand:
            return float(cand["lat"]), float(cand["lon"])
        if "latitude" in cand and "longitude" in cand:
            return float(cand["latitude"]), float(cand["longitude"])

    # Tuple / list of coordinates: treat as (lat, lon) if first > second and in [-90, 90], or check bounds
    if isinstance(cand, (tuple, list)) and len(cand) == 2:
        val0, val1 = float(cand[0]), float(cand[1])
        # If val0 is clearly lat [-90, 90] and val1 is lon [-180, 180]
        # Standard in MARIS SpillRegionStats is (lon, lat)
        # But if passed as (lat, lon), handle disambiguation
        if -90 <= val1 <= 90 and -180 <= val0 <= 180 and not (-90 <= val0 <= 90 and 8 <= val1 <= 15):
            # Probably (lon, lat)
            return val1, val0
        return val0, val1

    raise CandidateEnvironmentError(f"Could not extract (lat, lon) centroid from candidate: {type(cand)}")


def enrich_geojson_feature_collection(
    feature_collection: dict[str, Any],
    sensing_time: datetime,
    era5_source: str | Path | Asset | xr.Dataset | None = None,
    cmems_source: str | Path | Asset | xr.Dataset | None = None,
) -> dict[str, Any]:
    """Enrich a B3 GeoJSON FeatureCollection in-place with Stage C2 candidate intelligence."""
    sampler = CandidateEnvironmentSampler(
        sensing_time=sensing_time,
        era5_source=era5_source,
        cmems_source=cmems_source,
    )

    features = feature_collection.get("features", [])
    regime_counts: dict[str, int] = {
        WindRegime.CALM_WATER_LOOKALIKE.value: 0,
        WindRegime.FAVORABLE_DETECTION_WINDOW.value: 0,
        WindRegime.HIGH_WIND_DISPERSION.value: 0,
        WindRegime.UNKNOWN.value: 0,
    }

    for feat in features:
        props = feat.setdefault("properties", {})
        centroid = props.get("centroid")
        if centroid and len(centroid) == 2:
            # centroid is [lon, lat]
            lat, lon = float(centroid[1]), float(centroid[0])
            env = sampler.sample_point(lat=lat, lon=lon)
            props["environment"] = env.model_dump()
            props["wind_regime"] = env.regime.classification.value
            props["wind_speed_ms"] = env.wind.speed_ms
            props["wind_direction_from_deg"] = env.wind.direction_from_deg
            props["current_speed_ms"] = env.current.speed_ms
            props["current_direction_to_deg"] = env.current.direction_to_deg
            regime_counts[env.regime.classification.value] += 1

    props_fc = feature_collection.setdefault("properties", {})
    props_fc["candidate_environment_summary"] = {
        "total_candidates": len(features),
        "regime_counts": regime_counts,
        "sensing_time": _utc(sensing_time).isoformat(),
        "calm_water_threshold_ms": CALM_WATER_THRESHOLD_MS,
        "high_wind_threshold_ms": HIGH_WIND_THRESHOLD_MS,
    }

    return feature_collection
