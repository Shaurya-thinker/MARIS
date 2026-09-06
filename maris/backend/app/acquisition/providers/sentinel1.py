"""Sentinel-1 acquisition via Copernicus Data Space Ecosystem OData.

Ranking is deterministic acquisition selection, not oil-spill optimality.
Order: IW mode, then GRDH/GRDM/SLC/other, then online availability,
then proximity of sensing start to the request time-window midpoint,
then product id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import re

from pydantic import BaseModel

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

PROVIDER_ID = "sentinel1"
COLLECTION_NAME = "SENTINEL-1"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
CATALOGUE_PAGE_SIZE = 50

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

_CLASS_RANK = {
    "GRDH": 0,
    "GRDM": 1,
    "SLC": 2,
}


class Sentinel1Product(BaseModel):
    """Parsed CDSE catalogue record used for selection. Not a domain Asset."""

    id: str
    name: str
    sensing_start: datetime
    sensing_end: datetime | None = None
    online: bool = True
    content_length: int | None = None
    footprint: dict[str, Any] | None = None
    origin: str | None = None
    mode: str | None = None
    product_class: str | None = None
    polarisation: str | None = None


class CdseTransport(Protocol):
    def get_json(self, url: str, timeout: float | None = 30) -> dict[str, Any]: ...

    def post_form(self, url: str, data: dict[str, str], timeout: float | None = 30) -> dict[str, Any]: ...

    def stream_download(
        self,
        url: str,
        destination: Path,
        headers: dict[str, str],
        timeout: float | None = None,
    ) -> int: ...


class _KeepAuthorizationRedirectHandler(HTTPRedirectHandler):
    """CDSE download hops from zipper to object storage; keep the bearer token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        authorization = req.get_header("Authorization")
        if authorization:
            redirected.add_header("Authorization", authorization)
        return redirected


class UrllibCdseTransport:
    """stdlib HTTP client. No extra HTTP dependency."""

    def __init__(self) -> None:
        self._opener = build_opener(_KeepAuthorizationRedirectHandler)

    def get_json(self, url: str, timeout: float | None = 30) -> dict[str, Any]:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        return _read_json(self._opener, request, timeout)

    def post_form(self, url: str, data: dict[str, str], timeout: float | None = 30) -> dict[str, Any]:
        body = urlencode(data).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        return _read_json(self._opener, request, timeout)

    def stream_download(
        self,
        url: str,
        destination: Path,
        headers: dict[str, str],
        timeout: float | None = None,
    ) -> int:
        request = Request(url, method="GET", headers=headers)
        destination.parent.mkdir(parents=True, exist_ok=True)
        part_path = destination.with_name(destination.name + ".part")
        bytes_written = 0
        try:
            with self._opener.open(request, timeout=timeout) as response, part_path.open("wb") as handle:
                status = getattr(response, "status", None) or response.getcode()
                if status != 200:
                    raise AcquisitionError(f"CDSE download failed with HTTP {status}")
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    bytes_written += len(chunk)
            if bytes_written <= 0:
                part_path.unlink(missing_ok=True)
                raise AcquisitionError("CDSE download produced an empty artifact")
            part_path.replace(destination)
        except AcquisitionError:
            part_path.unlink(missing_ok=True)
            raise
        except HTTPError as error:
            part_path.unlink(missing_ok=True)
            raise AcquisitionError(f"CDSE download failed with HTTP {error.code}") from error
        except URLError as error:
            part_path.unlink(missing_ok=True)
            raise AcquisitionError(f"CDSE download failed: {error.reason}") from error
        except Exception:
            part_path.unlink(missing_ok=True)
            raise
        return bytes_written


def _read_json(opener: Any, request: Request, timeout: float | None) -> dict[str, Any]:
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            payload = response.read()
    except HTTPError as error:
        raise AcquisitionError(f"CDSE HTTP {error.code} for {request.full_url}") from error
    except URLError as error:
        raise AcquisitionError(f"CDSE request failed for {request.full_url}: {error.reason}") from error
    if status != 200:
        raise AcquisitionError(f"CDSE HTTP {status} for {request.full_url}")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise AcquisitionError("CDSE returned a non-JSON response") from error
    if not isinstance(parsed, dict):
        raise AcquisitionError("CDSE returned an unexpected JSON payload")
    return parsed


def parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def area_to_wkt(area: AreaOfInterest) -> str:
    if isinstance(area, BBoxAreaOfInterest):
        west, south, east, north = area.bbox.west, area.bbox.south, area.bbox.east, area.bbox.north
        ring = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    elif isinstance(area, PolygonAreaOfInterest):
        ring = [(position[0], position[1]) for position in area.coordinates[0]]
    else:
        raise AcquisitionValidationError("unsupported area of interest")
    path = ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in ring)
    return f"POLYGON(({path}))"


def window_midpoint(window: TimeWindow) -> datetime:
    start = window.start if window.start.tzinfo else window.start.replace(tzinfo=timezone.utc)
    end = window.end if window.end.tzinfo else window.end.replace(tzinfo=timezone.utc)
    return start + (end - start) / 2


def parse_product_name(name: str) -> tuple[str | None, str | None, str | None]:
    parts = name.replace(".SAFE", "").split("_")
    mode = parts[1] if len(parts) > 1 else None
    product_class = parts[2] if len(parts) > 2 else None
    polarisation = None
    if len(parts) > 3 and parts[3].startswith("1S") and len(parts[3]) >= 4:
        polarisation = parts[3][2:]
    return mode, product_class, polarisation


def parse_catalogue_products(payload: dict[str, Any]) -> list[Sentinel1Product]:
    records = payload.get("value")
    if not isinstance(records, list):
        raise AcquisitionError("CDSE catalogue response is missing a value list")
    products: list[Sentinel1Product] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        product_id = item.get("Id")
        name = item.get("Name")
        content_date = item.get("ContentDate") or {}
        start_raw = content_date.get("Start") if isinstance(content_date, dict) else None
        if not isinstance(product_id, str) or not isinstance(name, str) or not isinstance(start_raw, str):
            continue
        end_raw = content_date.get("End") if isinstance(content_date, dict) else None
        mode, product_class, polarisation = parse_product_name(name)
        footprint = item.get("GeoFootprint") or item.get("Footprint")
        products.append(
            Sentinel1Product(
                id=product_id,
                name=name,
                sensing_start=parse_datetime(start_raw),
                sensing_end=parse_datetime(end_raw) if isinstance(end_raw, str) else None,
                online=bool(item.get("Online", True)),
                content_length=item.get("ContentLength") if isinstance(item.get("ContentLength"), int) else None,
                footprint=footprint if isinstance(footprint, dict) else None,
                origin=item.get("Origin") if isinstance(item.get("Origin"), str) else None,
                mode=mode,
                product_class=product_class,
                polarisation=polarisation,
            )
        )
    return products


def rank_key(product: Sentinel1Product, target: datetime) -> tuple[int, int, int, float, str]:
    mode_rank = 0 if product.mode == "IW" else 1
    class_name = (product.product_class or "").rstrip("_")
    class_rank = _CLASS_RANK.get(class_name, 9)
    if class_name.startswith("RAW"):
        class_rank = 8
    online_rank = 0 if product.online else 1
    delta = abs((product.sensing_start - target).total_seconds())
    return (mode_rank, class_rank, online_rank, delta, product.id)


def select_sentinel1_product(products: list[Sentinel1Product], window: TimeWindow) -> Sentinel1Product:
    if not products:
        raise AcquisitionError("No Sentinel-1 products matched the requested AOI and time window")
    target = window_midpoint(window)
    return min(products, key=lambda product: rank_key(product, target))


def build_catalogue_url(catalogue_base: str, area: AreaOfInterest, window: TimeWindow) -> str:
    start = window.start if window.start.tzinfo else window.start.replace(tzinfo=timezone.utc)
    end = window.end if window.end.tzinfo else window.end.replace(tzinfo=timezone.utc)
    wkt = area_to_wkt(area)
    start_text = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_text = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    filter_expr = (
        f"Collection/Name eq '{COLLECTION_NAME}' and "
        f"OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') and "
        f"ContentDate/Start ge {start_text} and ContentDate/Start le {end_text}"
    )
    query = urlencode(
        {
            "$filter": filter_expr,
            "$top": str(CATALOGUE_PAGE_SIZE),
            "$orderby": "ContentDate/Start asc",
        },
        quote_via=quote,
    )
    return f"{catalogue_base.rstrip('/')}/Products?{query}"


def download_url(download_base: str, product_id: str) -> str:
    return f"{download_base.rstrip('/')}/Products({product_id})/$value"


def safe_filename(product_name: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", product_name).strip("._") or "sentinel1-product"
    if not cleaned.lower().endswith(".zip"):
        cleaned = f"{cleaned}.zip"
    return cleaned


class Sentinel1AcquisitionProvider(AcquisitionProvider):
    def __init__(
        self,
        settings: Settings | None = None,
        transport: CdseTransport | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._transport = transport or UrllibCdseTransport()

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            id=PROVIDER_ID,
            name="Copernicus Sentinel-1 (CDSE OData)",
            supported_asset_types=[AssetType.SATELLITE_SCENE],
        )

    def validate_request(self, request: AcquisitionRequest) -> None:
        require_scope(request)
        if request.asset_type != AssetType.SATELLITE_SCENE:
            raise AcquisitionValidationError("sentinel1 only supports satellite_scene assets")

    def acquire(self, request: AcquisitionRequest) -> AcquisitionResult:
        self.validate(request)
        self._ensure_credentials()
        assert request.area_of_interest is not None
        assert request.time_window is not None
        products = self._discover(request.area_of_interest, request.time_window)
        selected = select_sentinel1_product(products, request.time_window)
        destination = self._artifact_path(request.investigation_id, selected)
        token = self._access_token()
        retrieved_at = datetime.now(timezone.utc)
        bytes_written = self._download(selected.id, destination, token)
        if bytes_written <= 0 or not destination.exists() or destination.stat().st_size <= 0:
            destination.unlink(missing_ok=True)
            raise AcquisitionError("CDSE download produced an empty artifact")
        artifact = AcquiredArtifact(
            asset_type=AssetType.SATELLITE_SCENE,
            location=str(destination),
            source="copernicus-dataspace-odata",
            acquisition_time=selected.sensing_start,
            provenance=Provenance(
                product_id=selected.id,
                license="Copernicus Sentinel data",
                retrieved_at=retrieved_at,
                processing_level=selected.product_class,
                notes=(
                    "Raw Sentinel-1 product downloaded from CDSE. "
                    "Selection is deterministic acquisition ranking, not oil-spill fitness."
                ),
                extra={
                    "product_name": selected.name,
                    "collection": COLLECTION_NAME,
                    "mode": selected.mode,
                    "polarisation": selected.polarisation,
                    "online": selected.online,
                    "bytes_written": bytes_written,
                    "candidate_count": len(products),
                },
            ),
            metadata={
                "provider": PROVIDER_ID,
                "product_name": selected.name,
                "product_id": selected.id,
            },
        )
        return AcquisitionResult(
            artifacts=[artifact],
            metadata={
                "provider": PROVIDER_ID,
                "selected_product_id": selected.id,
                "selected_product_name": selected.name,
                "candidate_count": len(products),
            },
        )

    def _discover(self, area: AreaOfInterest, window: TimeWindow) -> list[Sentinel1Product]:
        url = build_catalogue_url(self._settings.cdse_catalogue_url, area, window)
        try:
            payload = self._transport.get_json(url)
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError(f"CDSE catalogue query failed: {error}") from error
        return parse_catalogue_products(payload)

    def _ensure_credentials(self) -> None:
        if self._settings.cdse_access_token:
            return
        if self._settings.cdse_username and self._settings.cdse_password:
            return
        raise AcquisitionConfigurationError(
            "CDSE credentials are missing. Set CDSE_USERNAME and CDSE_PASSWORD, "
            "or set CDSE_ACCESS_TOKEN."
        )

    def _access_token(self) -> str:
        existing = self._settings.cdse_access_token
        if existing:
            return existing
        self._ensure_credentials()
        data = {
            "client_id": self._settings.cdse_client_id,
            "grant_type": "password",
            "username": self._settings.cdse_username,
            "password": self._settings.cdse_password,
        }
        if self._settings.cdse_totp:
            data["totp"] = self._settings.cdse_totp
        try:
            payload = self._transport.post_form(self._settings.cdse_token_url, data)
        except AcquisitionError:
            raise
        except Exception as error:
            raise AcquisitionError(f"CDSE token request failed: {error}") from error
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise AcquisitionError("CDSE token response did not include access_token")
        return token

    def _download(self, product_id: str, destination: Path, token: str) -> int:
        url = download_url(self._settings.cdse_download_url, product_id)
        headers = {"Authorization": f"Bearer {token}", "Accept": "*/*"}
        try:
            return self._transport.stream_download(url, destination, headers)
        except AcquisitionError:
            raise
        except HTTPError as error:
            raise AcquisitionError(f"CDSE download failed with HTTP {error.code}") from error
        except URLError as error:
            raise AcquisitionError(f"CDSE download failed: {error.reason}") from error
        except Exception as error:
            raise AcquisitionError(f"CDSE download failed: {error}") from error

    def _artifact_path(self, investigation_id: str, product: Sentinel1Product) -> Path:
        safe_investigation = _UNSAFE_FILENAME.sub("_", investigation_id).strip("._") or "investigation"
        return (
            Path(self._settings.data_dir)
            / "acquisitions"
            / safe_investigation
            / PROVIDER_ID
            / product.id
            / safe_filename(product.name)
        )
