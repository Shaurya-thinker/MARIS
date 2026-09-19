"""MARIS Real-Experiment — AIS Search Service.

Discovers AIS vessel tracks near a reconstructed source zone using the
configured AIS acquisition provider.

If the AIS adapter is unconfigured (MARIS_AIS_ADAPTER=unconfigured or unset)
the service returns an explicit ConfigurationUnavailable error so the wizard
UI can display:
    "AIS data source not configured. Set MARIS_AIS_ADAPTER and the required
    credentials to search for vessel tracks."

ZERO fabrication policy:
    This service NEVER generates synthetic vessel positions.
    If no positions are returned from the adapter, the result is an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.acquisition.base import AcquisitionConfigurationError, AcquisitionError
from app.acquisition.providers.ais import (
    AisAcquisitionProvider,
    AisHistoricalQuery,
    AisPositionRecord,
    UnconfiguredAisAdapter,
    normalize_ais_records,
)
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings, settings as default_settings
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigurationUnavailable(Exception):
    """Raised when no AIS adapter is configured."""


class AisSearchError(Exception):
    """Raised when the AIS query fails."""


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class VesselTrackSummary:
    """AIS track summary for one vessel, returned to the wizard UI."""

    mmsi: str | None
    vessel_name: str | None
    imo: str | None
    position_count: int
    first_timestamp: datetime
    last_timestamp: datetime
    source_adapter: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mmsi": self.mmsi,
            "vessel_name": self.vessel_name,
            "imo": self.imo,
            "position_count": self.position_count,
            "first_timestamp": self.first_timestamp.isoformat(),
            "last_timestamp": self.last_timestamp.isoformat(),
            "source_adapter": self.source_adapter,
        }


@dataclass
class AisSearchResult:
    """Aggregated AIS search result."""

    vessels: list[VesselTrackSummary] = field(default_factory=list)
    total_positions: int = 0
    search_bbox: dict[str, float] = field(default_factory=dict)
    search_window_start: str = ""
    search_window_end: str = ""
    adapter_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "vessels": [v.as_dict() for v in self.vessels],
            "total_positions": self.total_positions,
            "search_bbox": self.search_bbox,
            "search_window_start": self.search_window_start,
            "search_window_end": self.search_window_end,
            "adapter_id": self.adapter_id,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AisSearchService:
    """Search for AIS vessel tracks using the configured adapter.

    Usage::

        svc = AisSearchService()
        result = svc.search_near_source_zone(
            west=55.0, south=20.0, east=60.0, north=25.0,
            start=datetime(2024, 6, 1, 2, 0, tzinfo=timezone.utc),
            end=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
        )
    """

    def __init__(
        self,
        cfg: Settings | None = None,
        ais_provider: AisAcquisitionProvider | None = None,
    ) -> None:
        self._cfg = cfg or default_settings
        self._ais = ais_provider or AisAcquisitionProvider(settings=self._cfg)

    def is_configured(self) -> bool:
        """Return True if a real AIS adapter is configured."""
        adapter_id = getattr(self._cfg, "ais_adapter_id", "unconfigured")
        return adapter_id not in ("unconfigured", "", None)

    def search_near_source_zone(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        start: datetime,
        end: datetime,
        investigation_id: str = "real-experiment",
    ) -> AisSearchResult:
        """Find vessels whose AIS positions fall within the given bbox and time window.

        Args:
            west/south/east/north: spatial search box (WGS84).
            start/end: temporal search window (UTC).
            investigation_id: logical grouping for asset naming.

        Returns:
            AisSearchResult with per-vessel summaries.

        Raises:
            ConfigurationUnavailable: AIS adapter not configured.
            AisSearchError: adapter call failed.
        """
        if not self.is_configured():
            raise ConfigurationUnavailable(
                "AIS data source not configured. "
                "Set MARIS_AIS_ADAPTER and the required credentials to search for vessel tracks. "
                "Contact your MARIS administrator to configure an AIS provider."
            )

        start_utc = self._utc(start)
        end_utc = self._utc(end)

        bbox = BBoxAreaOfInterest(
            bbox=BoundingBox(west=west, south=south, east=east, north=north)
        )
        req = AcquisitionRequest(
            investigation_id=investigation_id,
            provider_id="ais",
            asset_type=AssetType.AIS_TRACK,
            area_of_interest=bbox,
            time_window=TimeWindow(start=start_utc, end=end_utc),
        )

        try:
            result = self._ais.acquire(req)
        except AcquisitionConfigurationError as exc:
            raise ConfigurationUnavailable(str(exc)) from exc
        except AcquisitionError as exc:
            raise AisSearchError(f"AIS search failed: {exc}") from exc

        # Parse the written artifact JSON back into AisPositionRecord objects
        import json
        all_records: list[AisPositionRecord] = []
        for artifact in result.artifacts:
            try:
                raw = json.loads(Path_read(artifact.location))
                if isinstance(raw, list):
                    query = AisHistoricalQuery(
                        investigation_id=investigation_id,
                        area_of_interest=bbox,
                        time_window=TimeWindow(start=start_utc, end=end_utc),
                    )
                    all_records.extend(normalize_ais_records(raw, query))
            except Exception:
                pass  # Skip artifacts that cannot be parsed; do not fabricate data

        return self._group_by_vessel(
            all_records,
            west=west, south=south, east=east, north=north,
            start=start_utc, end=end_utc,
            adapter_id=getattr(self._cfg, "ais_adapter_id", "unknown"),
        )

    @staticmethod
    def _group_by_vessel(
        records: list[AisPositionRecord],
        *,
        west: float, south: float, east: float, north: float,
        start: datetime, end: datetime,
        adapter_id: str,
    ) -> AisSearchResult:
        """Group position records by vessel identifier and build track summaries."""
        from collections import defaultdict

        groups: dict[str, list[AisPositionRecord]] = defaultdict(list)
        for rec in records:
            key = rec.mmsi or rec.vessel_name or "unknown"
            groups[key].append(rec)

        summaries: list[VesselTrackSummary] = []
        for key, positions in groups.items():
            sorted_pos = sorted(positions, key=lambda p: p.timestamp)
            sample = sorted_pos[0]
            summaries.append(
                VesselTrackSummary(
                    mmsi=sample.mmsi,
                    vessel_name=sample.vessel_name,
                    imo=sample.imo,
                    position_count=len(sorted_pos),
                    first_timestamp=sorted_pos[0].timestamp,
                    last_timestamp=sorted_pos[-1].timestamp,
                    source_adapter=adapter_id,
                )
            )

        # Sort: most positions first (more data = more informative)
        summaries.sort(key=lambda s: s.position_count, reverse=True)

        return AisSearchResult(
            vessels=summaries,
            total_positions=len(records),
            search_bbox={"west": west, "south": south, "east": east, "north": north},
            search_window_start=start.isoformat(),
            search_window_end=end.isoformat(),
            adapter_id=adapter_id,
        )

    @staticmethod
    def _utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


def Path_read(path: str) -> str:
    """Read text content from a file path (helper to avoid importing Path in a loop)."""
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")
