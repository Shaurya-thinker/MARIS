"""MARIS Real-Experiment — Environment Selector Service.

Acquires ERA5 wind and CMEMS ocean-current data for a Sentinel-1 observation
time and bounding box.  Wraps the existing acquisition providers directly
(no G1 workflow dependency).

If credentials are absent for either provider the service raises
ConfigurationUnavailable with a clear message.  The API layer translates this
into a 503 with the provider name so the wizard UI can display exactly which
credential is missing.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.acquisition.base import AcquisitionConfigurationError, AcquisitionError
from app.acquisition.providers.cmems import CmemsAcquisitionProvider
from app.acquisition.providers.era5 import Era5AcquisitionProvider
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings, settings as default_settings
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigurationUnavailable(Exception):
    """Raised when required credentials are not present."""


class EnvironmentAcquisitionError(Exception):
    """Raised when a provider call fails."""


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentSample:
    """Sample scalar values extracted from the acquired field for display."""
    u: float | None = None   # U-component (m/s)
    v: float | None = None   # V-component (m/s)


@dataclass
class SelectedEnvironment:
    """Result of a successful environment selection."""
    era5_netcdf_path: str
    era5_timestamp: datetime
    era5_sample: EnvironmentSample
    cmems_netcdf_path: str
    cmems_timestamp: datetime
    cmems_sample: EnvironmentSample
    auto_selected: bool = True   # False if user provided manual override paths

    def as_dict(self) -> dict[str, Any]:
        return {
            "era5_netcdf_path": self.era5_netcdf_path,
            "era5_timestamp": self.era5_timestamp.isoformat(),
            "era5_u_sample": self.era5_sample.u,
            "era5_v_sample": self.era5_sample.v,
            "cmems_netcdf_path": self.cmems_netcdf_path,
            "cmems_timestamp": self.cmems_timestamp.isoformat(),
            "cmems_u_sample": self.cmems_sample.u,
            "cmems_v_sample": self.cmems_sample.v,
            "auto_selected": self.auto_selected,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class EnvironmentSelectorService:
    """Acquire ERA5 + CMEMS data for a real satellite observation.

    Usage::

        svc = EnvironmentSelectorService()
        env = svc.select_for_scene(
            observation_time=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
            west=-10.0, south=35.0, east=5.0, north=45.0,
            backtrack_hours=12.0,
        )
    """

    MARGIN_DEG = 1.0   # Extra spatial margin added around the bbox when querying

    def __init__(
        self,
        cfg: Settings | None = None,
        era5_provider: Era5AcquisitionProvider | None = None,
        cmems_provider: CmemsAcquisitionProvider | None = None,
    ) -> None:
        self._cfg = cfg or default_settings
        self._era5 = era5_provider or Era5AcquisitionProvider(settings=self._cfg)
        self._cmems = cmems_provider or CmemsAcquisitionProvider(settings=self._cfg)

    def era5_configured(self) -> bool:
        return bool(self._cfg.cds_api_key)

    def cmems_configured(self) -> bool:
        return bool(self._cfg.cmems_username and self._cfg.cmems_password)

    def select_for_scene(
        self,
        *,
        observation_time: datetime,
        west: float,
        south: float,
        east: float,
        north: float,
        backtrack_hours: float = 12.0,
        investigation_id: str = "real-experiment",
        era5_override_path: str | None = None,
        cmems_override_path: str | None = None,
    ) -> SelectedEnvironment:
        """Acquire ERA5 and CMEMS data for the observation window + spatial extent.

        The temporal window extends from (observation_time - backtrack_hours) to
        observation_time so the backward drift engine has full coverage.

        Args:
            observation_time: Sentinel-1 acquisition time (UTC).
            west/south/east/north: scene bounding box (WGS84).
            backtrack_hours: backward drift duration; determines ERA5 time span.
            investigation_id: logical grouping ID used for file naming.
            era5_override_path: skip ERA5 acquisition and use this existing .nc file.
            cmems_override_path: skip CMEMS acquisition and use this existing .nc file.

        Returns:
            SelectedEnvironment with paths, timestamps, and sample values.

        Raises:
            ConfigurationUnavailable: if required credentials are absent.
            EnvironmentAcquisitionError: if a provider call fails.
        """
        obs_utc = self._utc(observation_time)
        start_utc = obs_utc - timedelta(hours=max(backtrack_hours, 1.0))

        # ---- ERA5 ---------------------------------------------------------
        if era5_override_path:
            era5_path = era5_override_path
            era5_ts = obs_utc
            era5_sample = self._sample_netcdf(era5_path, "u10", "v10")
            auto_selected = False
        else:
            if not self.era5_configured():
                raise ConfigurationUnavailable(
                    "ERA5 credentials unavailable. "
                    "Set CDSAPI_KEY (and optionally CDSAPI_URL) in the backend environment."
                )
            era5_path, era5_ts = self._acquire_era5(
                investigation_id, west, south, east, north, start_utc, obs_utc
            )
            era5_sample = self._sample_netcdf(era5_path, "u10", "v10")
            auto_selected = True

        # ---- CMEMS --------------------------------------------------------
        if cmems_override_path:
            cmems_path = cmems_override_path
            cmems_ts = obs_utc
            cmems_sample = self._sample_netcdf(cmems_path, "uo", "vo")
            auto_selected = False
        else:
            if not self.cmems_configured():
                raise ConfigurationUnavailable(
                    "Copernicus Marine credentials unavailable. "
                    "Set COPERNICUSMARINE_SERVICE_USERNAME + COPERNICUSMARINE_SERVICE_PASSWORD "
                    "(or CMEMS_USERNAME + CMEMS_PASSWORD) in the backend environment."
                )
            cmems_path, cmems_ts = self._acquire_cmems(
                investigation_id, west, south, east, north, start_utc, obs_utc
            )
            cmems_sample = self._sample_netcdf(cmems_path, "uo", "vo")

        return SelectedEnvironment(
            era5_netcdf_path=era5_path,
            era5_timestamp=era5_ts,
            era5_sample=era5_sample,
            cmems_netcdf_path=cmems_path,
            cmems_timestamp=cmems_ts,
            cmems_sample=cmems_sample,
            auto_selected=auto_selected,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _acquire_era5(
        self,
        inv_id: str,
        west: float, south: float, east: float, north: float,
        start: datetime, end: datetime,
    ) -> tuple[str, datetime]:
        margin = self.MARGIN_DEG
        bbox = BBoxAreaOfInterest(
            bbox=BoundingBox(
                west=max(-180.0, west - margin),
                south=max(-90.0, south - margin),
                east=min(180.0, east + margin),
                north=min(90.0, north + margin),
            )
        )
        req = AcquisitionRequest(
            investigation_id=inv_id,
            provider_id="era5",
            asset_type=AssetType.ENVIRONMENT_WIND,
            area_of_interest=bbox,
            time_window=TimeWindow(start=start, end=end),
        )
        try:
            result = self._era5.acquire(req)
        except AcquisitionConfigurationError as exc:
            raise ConfigurationUnavailable(str(exc)) from exc
        except AcquisitionError as exc:
            raise EnvironmentAcquisitionError(f"ERA5 acquisition failed: {exc}") from exc

        if not result.artifacts:
            raise EnvironmentAcquisitionError("ERA5 provider returned no artifacts")
        artifact = result.artifacts[0]
        return artifact.location, artifact.acquisition_time or end

    def _acquire_cmems(
        self,
        inv_id: str,
        west: float, south: float, east: float, north: float,
        start: datetime, end: datetime,
    ) -> tuple[str, datetime]:
        margin = self.MARGIN_DEG
        bbox = BBoxAreaOfInterest(
            bbox=BoundingBox(
                west=max(-180.0, west - margin),
                south=max(-90.0, south - margin),
                east=min(180.0, east + margin),
                north=min(90.0, north + margin),
            )
        )
        req = AcquisitionRequest(
            investigation_id=inv_id,
            provider_id="cmems",
            asset_type=AssetType.ENVIRONMENT_CURRENT,
            area_of_interest=bbox,
            time_window=TimeWindow(start=start, end=end),
        )
        try:
            result = self._cmems.acquire(req)
        except AcquisitionConfigurationError as exc:
            raise ConfigurationUnavailable(str(exc)) from exc
        except AcquisitionError as exc:
            raise EnvironmentAcquisitionError(f"CMEMS acquisition failed: {exc}") from exc

        if not result.artifacts:
            raise EnvironmentAcquisitionError("CMEMS provider returned no artifacts")
        artifact = result.artifacts[0]
        return artifact.location, artifact.acquisition_time or end

    @staticmethod
    def _sample_netcdf(path: str, u_var: str, v_var: str) -> EnvironmentSample:
        """Extract a representative scalar sample from a NetCDF file for display."""
        try:
            import netCDF4 as nc
            import numpy as np

            ds = nc.Dataset(path, "r")

            def _extract_finite(var_name: str) -> np.ndarray:
                var_obj = ds.variables.get(var_name)
                if var_obj is None:
                    return np.array([])
                data = var_obj[:]
                if np.ma.is_masked(data):
                    data = data.compressed()
                else:
                    data = np.asarray(data).flatten()
                return data[np.isfinite(data)]

            u_finite = _extract_finite(u_var)
            v_finite = _extract_finite(v_var)
            ds.close()

            return EnvironmentSample(
                u=float(np.nanmean(u_finite)) if len(u_finite) else None,
                v=float(np.nanmean(v_finite)) if len(v_finite) else None,
            )
        except Exception:
            return EnvironmentSample()

    @staticmethod
    def _utc(dt: datetime | str) -> datetime:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
