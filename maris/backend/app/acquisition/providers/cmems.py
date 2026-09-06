"""CMEMS near-surface current acquisition via the Copernicus Marine Toolbox.

Product GLOBAL_MULTIYEAR_PHY_001_030 (GLORYS12V1). This stage only acquires a
NetCDF current field; it does not run drift modelling.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import re

from app.acquisition.base import (
    AcquisitionConfigurationError,
    AcquisitionError,
    AcquisitionProvider,
    AcquisitionValidationError,
    require_scope,
)
from app.acquisition.schemas import (
    AcquiredArtifact,
    AcquisitionRequest,
    AcquisitionResult,
    ProviderIdentity,
)
from app.core.config import Settings, settings as default_settings
from app.models.common import (
    AreaOfInterest,
    AssetType,
    BBoxAreaOfInterest,
    PolygonAreaOfInterest,
    Provenance,
    TimeWindow,
)

PROVIDER_ID = "cmems"
PRODUCT_ID = "GLOBAL_MULTIYEAR_PHY_001_030"
DATASET_ID = "cmems_mod_glo_phy_my_0.083deg_P1D-m"
DATASET_TITLE = "GLORYS12V1 global ocean physics reanalysis, daily means"
VARIABLES = ("uo", "vo")
OUTPUT_FORMAT = "netcdf"
ARTIFACT_NAME = "cmems_surface_currents.nc"

# GLORYS12V1 is published on 50 standard depth levels. The shallowest standard
# level in product GLOBAL_MULTIYEAR_PHY_001_030 is approximately 0.49 m
# (NetCDF depth coordinate 0.494025 m). We request a 0–1 m window with nearest
# selection so the toolbox returns that first level without a full profile.
NEAR_SURFACE_DEPTH_MIN_M = 0.0
NEAR_SURFACE_DEPTH_MAX_M = 1.0
DOCUMENTED_FIRST_LEVEL_M = 0.494025
DOCUMENTED_FIRST_LEVEL_SOURCE = (
    "GLORYS12V1 standard vertical levels for GLOBAL_MULTIYEAR_PHY_001_030 "
    "(shallowest published z-level in the product NetCDF)"
)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_NETCDF_CLASSIC = b"CDF"
_NETCDF_HDF5 = b"\x89HDF"


class CmemsSubsetClient(Protocol):
    def subset(self, **kwargs: Any) -> Any: ...


CmemsClientFactory = Callable[[Settings], CmemsSubsetClient]


class CopernicusMarineSubsetClient:
    """Thin wrapper around copernicusmarine.subset. Created only when credentials exist."""

    def __init__(self, username: str, password: str) -> None:
        import copernicusmarine

        self._subset = copernicusmarine.subset
        self._username = username
        self._password = password

    def subset(self, **kwargs: Any) -> Any:
        return self._subset(
            username=self._username,
            password=self._password,
            disable_progress_bar=True,
            **kwargs,
        )


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def area_to_lonlat_bbox(area: AreaOfInterest) -> tuple[float, float, float, float]:
    """Return west, south, east, north."""
    if isinstance(area, BBoxAreaOfInterest):
        west, south, east, north = area.bbox.west, area.bbox.south, area.bbox.east, area.bbox.north
    elif isinstance(area, PolygonAreaOfInterest):
        lons = [position[0] for position in area.coordinates[0]]
        lats = [position[1] for position in area.coordinates[0]]
        west, east = min(lons), max(lons)
        south, north = min(lats), max(lats)
    else:
        raise AcquisitionValidationError("unsupported area of interest")
    if west >= east:
        raise AcquisitionValidationError("CMEMS requests require west < east (dateline-crossing boxes are not supported)")
    if south >= north:
        raise AcquisitionValidationError("CMEMS requests require south < north")
    return (west, south, east, north)


def looks_like_netcdf(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(4)
    return header.startswith(_NETCDF_CLASSIC) or header.startswith(_NETCDF_HDF5[:4])


def request_folder_name(area: AreaOfInterest, window: TimeWindow) -> str:
    west, south, east, north = area_to_lonlat_bbox(area)
    start = utc(window.start).strftime("%Y%m%dT%H%M%SZ")
    end = utc(window.end).strftime("%Y%m%dT%H%M%SZ")
    raw = f"{start}_{end}_{north:.4f}_{west:.4f}_{south:.4f}_{east:.4f}"
    return _UNSAFE_FILENAME.sub("_", raw).strip("._") or "cmems-request"


def build_cmems_subset_request(
    area: AreaOfInterest,
    window: TimeWindow,
    output_directory: Path,
    output_filename: str = ARTIFACT_NAME,
) -> dict[str, Any]:
    west, south, east, north = area_to_lonlat_bbox(area)
    return {
        "dataset_id": DATASET_ID,
        "variables": list(VARIABLES),
        "minimum_longitude": west,
        "maximum_longitude": east,
        "minimum_latitude": south,
        "maximum_latitude": north,
        "start_datetime": utc(window.start).isoformat(),
        "end_datetime": utc(window.end).isoformat(),
        "minimum_depth": NEAR_SURFACE_DEPTH_MIN_M,
        "maximum_depth": NEAR_SURFACE_DEPTH_MAX_M,
        "coordinates_selection_method": "nearest",
        "file_format": OUTPUT_FORMAT,
        "output_directory": str(output_directory),
        "output_filename": output_filename,
        "overwrite": True,
    }


def default_cmems_client_factory(settings: Settings) -> CmemsSubsetClient:
    return CopernicusMarineSubsetClient(username=settings.cmems_username, password=settings.cmems_password)


class CmemsAcquisitionProvider(AcquisitionProvider):
    def __init__(
        self,
        settings: Settings | None = None,
        client: CmemsSubsetClient | None = None,
        client_factory: CmemsClientFactory | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._client = client
        self._client_factory = client_factory or default_cmems_client_factory

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            id=PROVIDER_ID,
            name="Copernicus Marine GLORYS12V1 currents (CMEMS)",
            supported_asset_types=[AssetType.ENVIRONMENT_CURRENT],
        )

    def validate_request(self, request: AcquisitionRequest) -> None:
        require_scope(request)
        if request.asset_type != AssetType.ENVIRONMENT_CURRENT:
            raise AcquisitionValidationError("cmems only supports environment_current assets")
        assert request.area_of_interest is not None
        area_to_lonlat_bbox(request.area_of_interest)

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.validate(request)
        client = self._client or self._live_client()
        assert request.area_of_interest is not None
        assert request.time_window is not None
        destination_dir = self._artifact_dir(request.investigation_id, request.area_of_interest, request.time_window)
        destination_dir.mkdir(parents=True, exist_ok=True)
        payload = build_cmems_subset_request(request.area_of_interest, request.time_window, destination_dir)
        retrieved_at = datetime.now(timezone.utc)
        try:
            client.subset(**payload)
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError(f"CMEMS subset failed: {error}") from error
        target = destination_dir / ARTIFACT_NAME
        self._validate_artifact(target)
        west, south, east, north = area_to_lonlat_bbox(request.area_of_interest)
        artifact = AcquiredArtifact(
            asset_type=AssetType.ENVIRONMENT_CURRENT,
            location=str(target),
            source="copernicus-marine-service",
            acquisition_time=retrieved_at,
            provenance=Provenance(
                product_id=DATASET_ID,
                license="Copernicus Marine Service",
                retrieved_at=retrieved_at,
                processing_level="reanalysis",
                notes="Raw near-surface GLORYS12V1 uo/vo NetCDF from CMEMS. Not a drift product.",
                extra={
                    "product_id": PRODUCT_ID,
                    "dataset": DATASET_ID,
                    "dataset_title": DATASET_TITLE,
                    "variables": list(VARIABLES),
                    "output_format": OUTPUT_FORMAT,
                    "time_range": {
                        "start": utc(request.time_window.start).isoformat(),
                        "end": utc(request.time_window.end).isoformat(),
                    },
                    "bounding_box": {
                        "west": west,
                        "south": south,
                        "east": east,
                        "north": north,
                    },
                    "depth": {
                        "minimum_m": NEAR_SURFACE_DEPTH_MIN_M,
                        "maximum_m": NEAR_SURFACE_DEPTH_MAX_M,
                        "selection": "nearest",
                        "documented_first_level_m": DOCUMENTED_FIRST_LEVEL_M,
                        "documented_first_level_source": DOCUMENTED_FIRST_LEVEL_SOURCE,
                    },
                },
            ),
            metadata={
                "provider": PROVIDER_ID,
                "dataset": DATASET_ID,
                "product_name": ARTIFACT_NAME,
            },
        )
        return AcquisitionResult(
            artifacts=[artifact],
            metadata={
                "provider": PROVIDER_ID,
                "dataset": DATASET_ID,
                "product_id": PRODUCT_ID,
            },
        )

    def _live_client(self) -> CmemsSubsetClient:
        self._ensure_credentials()
        return self._client_factory(self._settings)

    def _ensure_credentials(self) -> None:
        if not self._settings.cmems_username or not self._settings.cmems_password:
            raise AcquisitionConfigurationError(
                "CMEMS credentials are missing. Set COPERNICUSMARINE_SERVICE_USERNAME "
                "and COPERNICUSMARINE_SERVICE_PASSWORD (or CMEMS_USERNAME and CMEMS_PASSWORD)."
            )

    def _validate_artifact(self, path: Path) -> None:
        if not path.exists() or path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            raise AcquisitionError("CMEMS download produced an empty artifact")
        if not looks_like_netcdf(path):
            raise AcquisitionError("CMEMS download did not produce a NetCDF artifact")

    def _artifact_dir(self, investigation_id: str, area: AreaOfInterest, window: TimeWindow) -> Path:
        safe_investigation = _UNSAFE_FILENAME.sub("_", investigation_id).strip("._") or "investigation"
        return (
            Path(self._settings.data_dir)
            / "acquisitions"
            / safe_investigation
            / PROVIDER_ID
            / request_folder_name(area, window)
        )
