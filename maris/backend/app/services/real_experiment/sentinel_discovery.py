"""MARIS Real-Experiment — Sentinel-1 Discovery Service.

Queries the Copernicus Data Space Ecosystem (CDSE) OData catalogue for
Sentinel-1 products that intersect a given bounding box and time window.
Does NOT download any scene; returns only catalogue metadata so the user
can select which observation to analyse.

If CDSE credentials are absent the service raises ConfigurationUnavailable,
which the API layer translates into a structured 503 response with the message
"Sentinel-1 credentials/configuration unavailable".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.acquisition.base import AcquisitionConfigurationError, AcquisitionError
from app.acquisition.providers.sentinel1 import (
    Sentinel1Product,
    UrllibCdseTransport,
    area_to_wkt,
    parse_catalogue_products,
    parse_product_name,
)
from app.core.config import Settings, settings as default_settings
from app.models.common import BBoxAreaOfInterest, BoundingBox, TimeWindow


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigurationUnavailable(Exception):
    """Raised when required credentials are not present in the environment."""


class DiscoveryError(Exception):
    """Raised when the CDSE catalogue request fails."""


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class SentinelProductSummary:
    """Lightweight catalogue record returned to the API layer and UI wizard."""

    product_id: str
    title: str
    sensing_start: datetime
    sensing_end: datetime | None
    online: bool
    platform: str                    # e.g. "S1A"
    mode: str | None                 # e.g. "IW"
    product_class: str | None        # e.g. "GRDH"
    polarisation: str | None         # e.g. "DV"
    centroid_lon: float | None
    centroid_lat: float | None
    footprint: dict[str, Any] | None
    content_length_bytes: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "sensing_start": self.sensing_start.isoformat(),
            "sensing_end": self.sensing_end.isoformat() if self.sensing_end else None,
            "online": self.online,
            "platform": self.platform,
            "mode": self.mode,
            "product_class": self.product_class,
            "polarisation": self.polarisation,
            "centroid_lon": self.centroid_lon,
            "centroid_lat": self.centroid_lat,
            "footprint": self.footprint,
            "content_length_bytes": self.content_length_bytes,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SentinelDiscoveryService:
    """Discover Sentinel-1 observations via CDSE OData catalogue.

    Usage::

        svc = SentinelDiscoveryService()
        products = svc.discover(
            west=-10.0, south=35.0, east=5.0, north=45.0,
            start=datetime(2024, 6, 1, tzinfo=timezone.utc),
            end=datetime(2024, 6, 3, tzinfo=timezone.utc),
            limit=20,
        )

    Raises ConfigurationUnavailable if CDSE credentials are absent.
    Raises DiscoveryError if the catalogue query fails.
    """

    def __init__(self, cfg: Settings | None = None) -> None:
        self._cfg = cfg or default_settings
        self._transport = UrllibCdseTransport()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_configured(self) -> bool:
        """Return True if CDSE credentials are present in the environment."""
        cfg = self._cfg
        return bool(
            (cfg.cdse_username and cfg.cdse_password)
            or cfg.cdse_access_token
        )

    def discover(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        start: datetime,
        end: datetime,
        limit: int = 20,
    ) -> list[SentinelProductSummary]:
        """Return Sentinel-1 products whose footprint intersects the bbox and time window.

        Args:
            west/south/east/north: bounding box in WGS84 decimal degrees.
            start/end: UTC datetime search window (inclusive).
            limit: maximum number of results to return (1–100).

        Returns:
            List of SentinelProductSummary, sorted by sensing_start descending.

        Raises:
            ConfigurationUnavailable: no CDSE credentials configured.
            DiscoveryError: catalogue request failed or returned unexpected data.
        """
        if not self.check_configured():
            raise ConfigurationUnavailable(
                "Sentinel-1 credentials/configuration unavailable. "
                "Set CDSE_USERNAME + CDSE_PASSWORD (or CDSE_ACCESS_TOKEN) "
                "in the backend environment."
            )

        limit = max(1, min(limit, 100))
        bbox_area = BBoxAreaOfInterest(bbox=BoundingBox(west=west, south=south, east=east, north=north))
        wkt = area_to_wkt(bbox_area)

        start_utc = self._to_utc(start)
        end_utc = self._to_utc(end)

        url = self._build_catalogue_url(wkt, start_utc, end_utc, top=limit)

        try:
            payload = self._transport.get_json(url, timeout=30)
        except AcquisitionError as exc:
            raise DiscoveryError(f"CDSE catalogue query failed: {exc}") from exc

        try:
            products = parse_catalogue_products(payload)
        except AcquisitionError as exc:
            raise DiscoveryError(f"Failed to parse CDSE catalogue response: {exc}") from exc

        return [self._to_summary(p) for p in products]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_catalogue_url(
        self,
        wkt: str,
        start: datetime,
        end: datetime,
        top: int,
    ) -> str:
        catalogue_base = self._cfg.cdse_catalogue_url.rstrip("/")
        start_s = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_s = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        filter_clause = (
            f"Collection/Name eq 'SENTINEL-1' "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') "
            f"and ContentDate/Start ge {start_s} "
            f"and ContentDate/Start le {end_s}"
        )

        from urllib.parse import urlencode, quote
        params = urlencode({
            "$filter": filter_clause,
            "$orderby": "ContentDate/Start desc",
            "$top": str(top),
            "$expand": "Attributes",
        })
        return f"{catalogue_base}/Products?{params}"

    @staticmethod
    def _to_utc(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _centroid(footprint: dict[str, Any] | None) -> tuple[float | None, float | None]:
        """Extract a rough centroid from a GeoJSON footprint dict."""
        if not footprint or not isinstance(footprint, dict):
            return None, None
        coords_raw = footprint.get("coordinates")
        if not coords_raw or not isinstance(coords_raw, list):
            return None, None
        try:
            ring = coords_raw[0]
            lons = [c[0] for c in ring if isinstance(c, list) and len(c) >= 2]
            lats = [c[1] for c in ring if isinstance(c, list) and len(c) >= 2]
            if not lons:
                return None, None
            return round(sum(lons) / len(lons), 6), round(sum(lats) / len(lats), 6)
        except (IndexError, TypeError):
            return None, None

    def _to_summary(self, product: Sentinel1Product) -> SentinelProductSummary:
        platform = "S1A"
        if product.name.startswith("S1B"):
            platform = "S1B"
        elif product.name.startswith("S1C"):
            platform = "S1C"

        centroid_lon, centroid_lat = self._centroid(product.footprint)

        return SentinelProductSummary(
            product_id=product.id,
            title=product.name,
            sensing_start=product.sensing_start,
            sensing_end=product.sensing_end,
            online=product.online,
            platform=platform,
            mode=product.mode,
            product_class=product.product_class,
            polarisation=product.polarisation,
            centroid_lon=centroid_lon,
            centroid_lat=centroid_lat,
            footprint=product.footprint,
            content_length_bytes=product.content_length,
        )
