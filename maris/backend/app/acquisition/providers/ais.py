"""Historical AIS acquisition adapter for MARIS.

Stage A3.5 defines the provider boundary and artifact contract. It does not call a
live AIS vendor. A real historical adapter can be injected later without changing
AcquisitionProvider or A2 domain models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
import json
import re

from pydantic import BaseModel, Field, ValidationError

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

PROVIDER_ID = "ais"
ARTIFACT_NAME = "ais_positions.json"
ARTIFACT_SCHEMA = "maris.ais.positions.v1"
OUTPUT_FORMAT = "json"

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class AisPositionRecord(BaseModel):
    """Provider-neutral historical AIS position. Missing source fields stay null."""

    timestamp: datetime
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None
    speed_over_ground: float | None = None
    course_over_ground: float | None = None
    heading: float | None = None
    navigation_status: str | None = None
    source_attributes: dict[str, Any] | None = None


class AisHistoricalQuery(BaseModel):
    investigation_id: str
    area_of_interest: AreaOfInterest
    time_window: TimeWindow


class AisSourceAdapter(Protocol):
    """Vendor-specific historical AIS fetch. Implementations must not live in base.py."""

    adapter_id: str

    def fetch_positions(self, query: AisHistoricalQuery) -> list[dict[str, Any]]:
        """Return raw position dicts for the requested AOI and time window. No interpolation."""


class UnconfiguredAisAdapter:
    """Default adapter until a historical AIS source is selected."""

    adapter_id = "unconfigured"

    def fetch_positions(self, query: AisHistoricalQuery) -> list[dict[str, Any]]:
        raise AcquisitionConfigurationError(
            "No historical AIS adapter is configured. Stage A3.5 is an acquisition "
            "boundary only; set MARIS_AIS_ADAPTER and inject a real adapter after "
            "provider selection. Live AIS network access is not implemented."
        )


class StaticAisAdapter:
    """Injectable adapter for tests and local fixtures. Does not call a network."""

    def __init__(self, records: list[dict[str, Any]], adapter_id: str = "static") -> None:
        self.adapter_id = adapter_id
        self._records = records

    def fetch_positions(self, query: AisHistoricalQuery) -> list[dict[str, Any]]:
        copied: list[dict[str, Any]] = []
        for record in self._records:
            if isinstance(record, dict):
                copied.append(dict(record))
            else:
                copied.append(record)  # type: ignore[arg-type]
        return copied


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
        raise AcquisitionValidationError("AIS requests require west < east (dateline-crossing boxes are not supported)")
    if south >= north:
        raise AcquisitionValidationError("AIS requests require south < north")
    return (west, south, east, north)


def request_folder_name(area: AreaOfInterest, window: TimeWindow) -> str:
    west, south, east, north = area_to_lonlat_bbox(area)
    start = utc(window.start).strftime("%Y%m%dT%H%M%SZ")
    end = utc(window.end).strftime("%Y%m%dT%H%M%SZ")
    raw = f"{start}_{end}_{north:.4f}_{west:.4f}_{south:.4f}_{east:.4f}"
    return _UNSAFE_FILENAME.sub("_", raw).strip("._") or "ais-request"


def _in_time_window(timestamp: datetime, window: TimeWindow) -> bool:
    instant = utc(timestamp)
    start = utc(window.start)
    end = utc(window.end)
    return start <= instant <= end


def _in_bbox(lat: float, lon: float, area: AreaOfInterest) -> bool:
    west, south, east, north = area_to_lonlat_bbox(area)
    return south <= lat <= north and west <= lon <= east


def normalize_ais_records(
    raw_records: list[dict[str, Any]],
    query: AisHistoricalQuery,
) -> list[AisPositionRecord]:
    if not isinstance(raw_records, list):
        raise AcquisitionError("AIS adapter did not return a list of position records")
    normalized: list[AisPositionRecord] = []
    for index, item in enumerate(raw_records):
        if not isinstance(item, dict):
            raise AcquisitionError(f"AIS record {index} is malformed: expected an object")
        try:
            record = AisPositionRecord.model_validate(item)
        except ValidationError as error:
            raise AcquisitionError(f"AIS record {index} is invalid: {error}") from error
        if not _in_time_window(record.timestamp, query.time_window):
            raise AcquisitionError(f"AIS record {index} timestamp is outside the requested time window")
        if not _in_bbox(record.lat, record.lon, query.area_of_interest):
            raise AcquisitionError(f"AIS record {index} coordinates are outside the requested AOI")
        normalized.append(record)
    return normalized


def records_to_jsonable(records: list[AisPositionRecord]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for record in records:
        dumped = record.model_dump(mode="json")
        payload.append({key: value for key, value in dumped.items() if value is not None})
    return payload


class AisAcquisitionProvider(AcquisitionProvider):
    def __init__(
        self,
        settings: Settings | None = None,
        adapter: AisSourceAdapter | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._adapter = adapter

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            id=PROVIDER_ID,
            name="Historical AIS (adapter boundary)",
            supported_asset_types=[AssetType.VESSEL_TRACK],
        )

    def validate_request(self, request: AcquisitionRequest) -> None:
        require_scope(request)
        if request.asset_type != AssetType.VESSEL_TRACK:
            raise AcquisitionValidationError("ais only supports vessel_track assets")
        assert request.area_of_interest is not None
        area_to_lonlat_bbox(request.area_of_interest)

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.validate(request)
        assert request.area_of_interest is not None
        assert request.time_window is not None
        adapter = self._resolve_adapter()
        query = AisHistoricalQuery(
            investigation_id=request.investigation_id,
            area_of_interest=request.area_of_interest,
            time_window=request.time_window,
        )
        try:
            raw_records = adapter.fetch_positions(query)
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError(f"AIS adapter fetch failed: {error}") from error
        records = normalize_ais_records(raw_records, query)
        destination_dir = self._artifact_dir(request.investigation_id, request.area_of_interest, request.time_window)
        destination_dir.mkdir(parents=True, exist_ok=True)
        target = destination_dir / ARTIFACT_NAME
        west, south, east, north = area_to_lonlat_bbox(request.area_of_interest)
        retrieved_at = datetime.now(timezone.utc)
        artifact_body = {
            "schema": ARTIFACT_SCHEMA,
            "query": {
                "investigation_id": request.investigation_id,
                "time_window": {
                    "start": utc(request.time_window.start).isoformat(),
                    "end": utc(request.time_window.end).isoformat(),
                },
                "bounding_box": {"west": west, "south": south, "east": east, "north": north},
            },
            "records": records_to_jsonable(records),
        }
        target.write_text(json.dumps(artifact_body, indent=2) + "\n", encoding="utf-8")
        if not target.exists() or target.stat().st_size <= 0:
            raise AcquisitionError("AIS acquisition produced an empty artifact")
        artifact = AcquiredArtifact(
            asset_type=AssetType.VESSEL_TRACK,
            location=str(target),
            source=f"ais-adapter:{adapter.adapter_id}",
            acquisition_time=retrieved_at,
            provenance=Provenance(
                product_id=ARTIFACT_SCHEMA,
                retrieved_at=retrieved_at,
                notes=(
                    "Historical AIS position snapshot. Records are adapter-supplied observations "
                    "only; MARIS does not interpolate or reconstruct trajectories in A3.5."
                ),
                extra={
                    "provider": PROVIDER_ID,
                    "adapter_id": adapter.adapter_id,
                    "configured_adapter": self._settings.ais_adapter_id,
                    "dataset": ARTIFACT_SCHEMA,
                    "source_format": OUTPUT_FORMAT,
                    "record_count": len(records),
                    "time_range": {
                        "start": utc(request.time_window.start).isoformat(),
                        "end": utc(request.time_window.end).isoformat(),
                    },
                    "bounding_box": {"west": west, "south": south, "east": east, "north": north},
                },
            ),
            metadata={
                "provider": PROVIDER_ID,
                "adapter_id": adapter.adapter_id,
                "product_name": ARTIFACT_NAME,
                "record_count": len(records),
            },
        )
        return AcquisitionResult(
            artifacts=[artifact],
            metadata={
                "provider": PROVIDER_ID,
                "adapter_id": adapter.adapter_id,
                "record_count": len(records),
            },
        )

    def _resolve_adapter(self) -> AisSourceAdapter:
        if self._adapter is not None:
            return self._adapter
        if self._settings.ais_adapter_id and self._settings.ais_adapter_id != "unconfigured":
            raise AcquisitionConfigurationError(
                f"AIS adapter '{self._settings.ais_adapter_id}' is named in configuration but "
                "no live historical AIS adapter is implemented. Inject an AisSourceAdapter."
            )
        return UnconfiguredAisAdapter()

    def _artifact_dir(self, investigation_id: str, area: AreaOfInterest, window: TimeWindow) -> Path:
        safe_investigation = _UNSAFE_FILENAME.sub("_", investigation_id).strip("._") or "investigation"
        return (
            Path(self._settings.data_dir)
            / "acquisitions"
            / safe_investigation
            / PROVIDER_ID
            / request_folder_name(area, window)
        )
