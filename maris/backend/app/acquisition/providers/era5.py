"""ERA5 10 m wind acquisition via the Copernicus Climate Data Store.

Uses dataset reanalysis-era5-single-levels (hourly single-level reanalysis).
This stage only acquires a NetCDF wind field; it does not run drift modelling.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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

PROVIDER_ID = "era5"
DATASET_ID = "reanalysis-era5-single-levels"
DATASET_TITLE = "ERA5 hourly data on single levels"
DATASET_DOI = "10.24381/cds.adbb2d47"
PRODUCT_TYPE = "reanalysis"
OUTPUT_FORMAT = "netcdf"
VARIABLES = ("10m_u_component_of_wind", "10m_v_component_of_wind")
ARTIFACT_NAME = "era5_10m_wind.nc"

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
_NETCDF_CLASSIC = b"CDF"
_NETCDF_HDF5 = b"\x89HDF"


class Era5CdsClient(Protocol):
    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> Any: ...


CdsClientFactory = Callable[[Settings], Era5CdsClient]


class CdsapiEra5Client:
    """Thin wrapper around the official cdsapi client. Created only when credentials exist."""

    def __init__(self, url: str, key: str) -> None:
        import cdsapi

        self._client = cdsapi.Client(url=url, key=key, quiet=True, progress=False)

    def retrieve(self, dataset: str, request: dict[str, Any], target: str) -> Any:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        return self._client.retrieve(dataset, request, target)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def floor_hour(value: datetime) -> datetime:
    instant = utc(value)
    return instant.replace(minute=0, second=0, microsecond=0)


def hourly_slots(window: TimeWindow) -> list[datetime]:
    start = floor_hour(window.start)
    end = utc(window.end)
    slots: list[datetime] = []
    current = start
    while current < end:
        slots.append(current)
        current += timedelta(hours=1)
    if not slots:
        slots.append(start)
    return slots


def area_to_cds_bbox(area: AreaOfInterest) -> tuple[float, float, float, float]:
    """Return CDS area order: north, west, south, east."""
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
        raise AcquisitionValidationError("ERA5 requests require west < east (dateline-crossing boxes are not supported)")
    if south >= north:
        raise AcquisitionValidationError("ERA5 requests require south < north")
    return (north, west, south, east)


def bbox_from_cds_area(area: tuple[float, float, float, float]) -> dict[str, float]:
    north, west, south, east = area
    return {"north": north, "west": west, "south": south, "east": east}


def build_era5_month_requests(area: AreaOfInterest, window: TimeWindow) -> list[dict[str, Any]]:
    """One CDS request per calendar month to avoid extra days from cartesian products."""
    grouped: dict[tuple[int, int], list[datetime]] = {}
    for slot in hourly_slots(window):
        grouped.setdefault((slot.year, slot.month), []).append(slot)
    north, west, south, east = area_to_cds_bbox(area)
    requests: list[dict[str, Any]] = []
    for year, month in sorted(grouped):
        slots = grouped[(year, month)]
        requests.append(
            {
                "product_type": [PRODUCT_TYPE],
                "variable": list(VARIABLES),
                "year": [f"{year:04d}"],
                "month": [f"{month:02d}"],
                "day": sorted({slot.strftime("%d") for slot in slots}),
                "time": sorted({slot.strftime("%H:00") for slot in slots}),
                "area": [north, west, south, east],
                "data_format": OUTPUT_FORMAT,
                "download_format": "unarchived",
            }
        )
    return requests


def looks_like_netcdf(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(4)
    return header.startswith(_NETCDF_CLASSIC) or header.startswith(_NETCDF_HDF5[:4])


def request_folder_name(area: AreaOfInterest, window: TimeWindow) -> str:
    north, west, south, east = area_to_cds_bbox(area)
    start = utc(window.start).strftime("%Y%m%dT%H%M%SZ")
    end = utc(window.end).strftime("%Y%m%dT%H%M%SZ")
    raw = f"{start}_{end}_{north:.4f}_{west:.4f}_{south:.4f}_{east:.4f}"
    return _UNSAFE_FILENAME.sub("_", raw).strip("._") or "era5-request"


def default_cds_client_factory(settings: Settings) -> Era5CdsClient:
    return CdsapiEra5Client(url=settings.cds_url, key=settings.cds_api_key)


class Era5AcquisitionProvider(AcquisitionProvider):
    def __init__(
        self,
        settings: Settings | None = None,
        client: Era5CdsClient | None = None,
        client_factory: CdsClientFactory | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._client = client
        self._client_factory = client_factory or default_cds_client_factory

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            id=PROVIDER_ID,
            name="Copernicus ERA5 10 m wind (CDS)",
            supported_asset_types=[AssetType.ENVIRONMENT_WIND],
        )

    def validate_request(self, request: AcquisitionRequest) -> None:
        require_scope(request)
        if request.asset_type != AssetType.ENVIRONMENT_WIND:
            raise AcquisitionValidationError("era5 only supports environment_wind assets")
        assert request.area_of_interest is not None
        area_to_cds_bbox(request.area_of_interest)

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.validate(request)
        client = self._client or self._live_client()
        assert request.area_of_interest is not None
        assert request.time_window is not None
        cds_requests = build_era5_month_requests(request.area_of_interest, request.time_window)
        destination_dir = self._artifact_dir(request.investigation_id, request.area_of_interest, request.time_window)
        destination_dir.mkdir(parents=True, exist_ok=True)
        retrieved_at = datetime.now(timezone.utc)
        written: list[Path] = []
        try:
            for payload in cds_requests:
                target = destination_dir / (
                    ARTIFACT_NAME
                    if len(cds_requests) == 1
                    else f"era5_10m_wind_{payload['year'][0]}{payload['month'][0]}.nc"
                )
                self._retrieve(client, payload, target)
                self._validate_artifact(target)
                written.append(target)
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError(f"CDS ERA5 retrieve failed: {error}") from error
        location = written[0] if len(written) == 1 else destination_dir
        north, west, south, east = area_to_cds_bbox(request.area_of_interest)
        artifact = AcquiredArtifact(
            asset_type=AssetType.ENVIRONMENT_WIND,
            location=str(location),
            source="copernicus-climate-data-store",
            acquisition_time=retrieved_at,
            provenance=Provenance(
                product_id=DATASET_ID,
                license="Copernicus Climate Change Service (C3S) ERA5",
                retrieved_at=retrieved_at,
                processing_level=PRODUCT_TYPE,
                notes="Raw ERA5 10 m wind NetCDF from CDS. Not a drift product.",
                extra={
                    "dataset": DATASET_ID,
                    "dataset_title": DATASET_TITLE,
                    "doi": DATASET_DOI,
                    "product_type": PRODUCT_TYPE,
                    "variables": list(VARIABLES),
                    "output_format": OUTPUT_FORMAT,
                    "time_range": {
                        "start": utc(request.time_window.start).isoformat(),
                        "end": utc(request.time_window.end).isoformat(),
                    },
                    "bounding_box": bbox_from_cds_area((north, west, south, east)),
                    "cds_url": self._settings.cds_url,
                    "files": [path.name for path in written],
                },
            ),
            metadata={
                "provider": PROVIDER_ID,
                "dataset": DATASET_ID,
                "product_name": ARTIFACT_NAME if len(written) == 1 else destination_dir.name,
            },
        )
        return AcquisitionResult(
            artifacts=[artifact],
            metadata={
                "provider": PROVIDER_ID,
                "dataset": DATASET_ID,
                "file_count": len(written),
            },
        )

    def _live_client(self) -> Era5CdsClient:
        self._ensure_credentials()
        return self._client_factory(self._settings)

    def _ensure_credentials(self) -> None:
        if not self._settings.cds_api_key:
            raise AcquisitionConfigurationError(
                "CDS credentials are missing. Set CDSAPI_KEY "
                "(and optionally CDSAPI_URL) for the Climate Data Store."
            )

    def _retrieve(self, client: Era5CdsClient, payload: dict[str, Any], target: Path) -> None:
        try:
            client.retrieve(DATASET_ID, payload, str(target))
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError(f"CDS ERA5 retrieve failed: {error}") from error

    def _validate_artifact(self, path: Path) -> None:
        if not path.exists() or path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            raise AcquisitionError("CDS ERA5 download produced an empty artifact")
        if not looks_like_netcdf(path):
            raise AcquisitionError("CDS ERA5 download did not produce a NetCDF artifact")

    def _artifact_dir(self, investigation_id: str, area: AreaOfInterest, window: TimeWindow) -> Path:
        safe_investigation = _UNSAFE_FILENAME.sub("_", investigation_id).strip("._") or "investigation"
        return (
            Path(self._settings.data_dir)
            / "acquisitions"
            / safe_investigation
            / PROVIDER_ID
            / request_folder_name(area, window)
        )
