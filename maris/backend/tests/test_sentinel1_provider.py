from datetime import datetime, timezone
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from app.acquisition.base import (
    AcquisitionConfigurationError,
    AcquisitionError,
    AcquisitionValidationError,
)
from app.acquisition.providers.sentinel1 import (
    Sentinel1AcquisitionProvider,
    Sentinel1Product,
    parse_catalogue_products,
    select_sentinel1_product,
)
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings
from app.main import app
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


def _window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2024, 3, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2024, 3, 2, 0, 0, tzinfo=timezone.utc),
    )


def _aoi() -> BBoxAreaOfInterest:
    return BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))


def _request(**overrides: object) -> AcquisitionRequest:
    payload: dict[str, object] = {
        "investigation_id": "inv-test",
        "provider_id": "sentinel1",
        "asset_type": AssetType.SATELLITE_SCENE,
        "area_of_interest": _aoi(),
        "time_window": _window(),
    }
    payload.update(overrides)
    return AcquisitionRequest.model_validate(payload)


def _product(
    product_id: str,
    name: str,
    start: datetime,
    *,
    online: bool = True,
) -> Sentinel1Product:
    parts = name.replace(".SAFE", "").split("_")
    mode = parts[1] if len(parts) > 1 else None
    product_class = parts[2] if len(parts) > 2 else None
    polarisation = parts[3] if len(parts) > 3 else ""
    polarisation_code = polarisation[2:] if polarisation.startswith("1S") else None
    return Sentinel1Product(
        id=product_id,
        name=name,
        sensing_start=start,
        online=online,
        mode=mode,
        product_class=product_class,
        polarisation=polarisation_code,
    )


class FakeTransport:
    def __init__(
        self,
        *,
        catalogue: dict[str, object] | Exception,
        token: dict[str, object] | Exception | None = None,
        download_bytes: bytes = b"SAFE-ZIP",
        download_error: Exception | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.token = token if token is not None else {"access_token": "unused"}
        self.download_bytes = download_bytes
        self.download_error = download_error
        self.get_urls: list[str] = []
        self.post_urls: list[str] = []
        self.download_urls: list[str] = []

    def get_json(self, url: str, timeout: float | None = 30) -> dict[str, object]:
        self.get_urls.append(url)
        if isinstance(self.catalogue, Exception):
            raise self.catalogue
        return self.catalogue

    def post_form(self, url: str, data: dict[str, str], timeout: float | None = 30) -> dict[str, object]:
        self.post_urls.append(url)
        if "password" in data:
            raise AssertionError("tests must not send live CDSE passwords")
        if isinstance(self.token, Exception):
            raise self.token
        return self.token

    def stream_download(
        self,
        url: str,
        destination: Path,
        headers: dict[str, str],
        timeout: float | None = None,
    ) -> int:
        self.download_urls.append(url)
        if self.download_error is not None:
            raise self.download_error
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.download_bytes)
        return len(self.download_bytes)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "cdse_username": "",
        "cdse_password": "",
        "cdse_access_token": "mock-access-token",
        "cdse_totp": "",
    }
    values.update(overrides)
    return Settings(**values)


def _catalogue(*names_and_ids: tuple[str, str, str]) -> dict[str, object]:
    records = []
    for product_id, name, start in names_and_ids:
        records.append(
            {
                "Id": product_id,
                "Name": name,
                "ContentDate": {"Start": start, "End": start},
                "Online": True,
                "ContentLength": 128,
            }
        )
    return {"value": records}


class HealthTests(unittest.TestCase):
    def test_health(self) -> None:
        client = TestClient(app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "MARIS backend")


class Sentinel1ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = Sentinel1AcquisitionProvider(settings=_settings(Path(".")))

    def test_valid_request(self) -> None:
        self.provider.validate(_request())

    def test_missing_aoi(self) -> None:
        request = _request(area_of_interest=None)
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(request)

    def test_missing_time_window(self) -> None:
        request = _request(time_window=None)
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(request)

    def test_unsupported_asset_type(self) -> None:
        request = _request(asset_type=AssetType.VESSEL_TRACK)
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(request)

    def test_invalid_time_window_rejected_by_schema(self) -> None:
        with self.assertRaises(Exception):
            TimeWindow(
                start=datetime(2024, 3, 2, tzinfo=timezone.utc),
                end=datetime(2024, 3, 1, tzinfo=timezone.utc),
            )

    def test_invalid_bbox_rejected_by_schema(self) -> None:
        with self.assertRaises(Exception):
            BoundingBox(west=0, south=10, east=1, north=10)


class Sentinel1SelectionTests(unittest.TestCase):
    def test_prefers_iw_grdh_closest_to_window_midpoint(self) -> None:
        window = _window()
        products = [
            _product(
                "ew-close",
                "S1A_EW_GRDM_1SDH_20240301T120000_20240301T120100_000001_000000_000000",
                datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc),
            ),
            _product(
                "iw-slc",
                "S1A_IW_SLC__1SDV_20240301T120000_20240301T120100_000001_000000_000000",
                datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc),
            ),
            _product(
                "iw-grdh-far",
                "S1A_IW_GRDH_1SDV_20240301T010000_20240301T010100_000001_000000_000000",
                datetime(2024, 3, 1, 1, 0, tzinfo=timezone.utc),
            ),
            _product(
                "iw-grdh-near",
                "S1A_IW_GRDH_1SDV_20240301T110000_20240301T110100_000001_000000_000000",
                datetime(2024, 3, 1, 11, 0, tzinfo=timezone.utc),
            ),
        ]
        selected = select_sentinel1_product(products, window)
        self.assertEqual(selected.id, "iw-grdh-near")

    def test_tie_breaks_on_product_id(self) -> None:
        start = datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
        products = [
            _product("b-id", "S1A_IW_GRDH_1SDV_20240301T120000_20240301T120100_000001_000000_000000", start),
            _product("a-id", "S1A_IW_GRDH_1SDV_20240301T120000_20240301T120100_000001_000000_000000", start),
        ]
        selected = select_sentinel1_product(products, _window())
        self.assertEqual(selected.id, "a-id")

    def test_no_matching_products(self) -> None:
        with self.assertRaises(AcquisitionError) as context:
            select_sentinel1_product([], _window())
        self.assertIn("No Sentinel-1 products matched", str(context.exception))

    def test_skips_invalid_catalogue_rows(self) -> None:
        products = parse_catalogue_products(
            {
                "value": [
                    {"Id": "ok", "Name": "S1A_IW_GRDH_1SDV_x", "ContentDate": {"Start": "2024-03-01T11:00:00Z"}},
                    {"Id": 1, "Name": "bad"},
                    "ignore",
                ]
            }
        )
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].id, "ok")


class Sentinel1AcquisitionTests(unittest.TestCase):
    def test_missing_credentials(self) -> None:
        settings = _settings(Path("."), cdse_access_token="", cdse_username="", cdse_password="")
        transport = FakeTransport(catalogue=_catalogue())
        provider = Sentinel1AcquisitionProvider(settings=settings, transport=transport)
        with self.assertRaises(AcquisitionConfigurationError):
            provider.acquire(_request())

    def test_catalogue_http_failure(self) -> None:
        transport = FakeTransport(catalogue=AcquisitionError("CDSE HTTP 503 for catalogue"))
        provider = Sentinel1AcquisitionProvider(settings=_settings(Path(".")), transport=transport)
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("503", str(context.exception))

    def test_empty_catalogue(self) -> None:
        transport = FakeTransport(catalogue={"value": []})
        provider = Sentinel1AcquisitionProvider(settings=_settings(Path(".")), transport=transport)
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("No Sentinel-1 products matched", str(context.exception))

    def test_download_failure(self) -> None:
        transport = FakeTransport(
            catalogue=_catalogue(
                ("prod-1", "S1A_IW_GRDH_1SDV_20240301T110000_20240301T110100_000001_000000_000000", "2024-03-01T11:00:00Z")
            ),
            download_error=AcquisitionError("CDSE download failed with HTTP 401"),
        )
        provider = Sentinel1AcquisitionProvider(settings=_settings(Path(".")), transport=transport)
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("401", str(context.exception))

    def test_empty_download_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeTransport(
                catalogue=_catalogue(
                    ("prod-1", "S1A_IW_GRDH_1SDV_20240301T110000_20240301T110100_000001_000000_000000", "2024-03-01T11:00:00Z")
                ),
                download_bytes=b"",
            )
            provider = Sentinel1AcquisitionProvider(settings=_settings(Path(tmp)), transport=transport)
            with self.assertRaises(AcquisitionError) as context:
                provider.acquire(_request())
            self.assertIn("empty artifact", str(context.exception))

    def test_successful_mocked_acquisition(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeTransport(
                catalogue=_catalogue(
                    (
                        "ew-1",
                        "S1A_EW_GRDM_1SDH_20240301T120000_20240301T120100_000001_000000_000000",
                        "2024-03-01T12:00:00Z",
                    ),
                    (
                        "iw-1",
                        "S1A_IW_GRDH_1SDV_20240301T110000_20240301T110100_000001_000000_000000",
                        "2024-03-01T11:00:00Z",
                    ),
                )
            )
            provider = Sentinel1AcquisitionProvider(settings=_settings(Path(tmp)), transport=transport)
            result = provider.acquire(_request())
            self.assertEqual(len(result.artifacts), 1)
            artifact = result.artifacts[0]
            self.assertEqual(artifact.asset_type, AssetType.SATELLITE_SCENE)
            self.assertEqual(artifact.source, "copernicus-dataspace-odata")
            self.assertEqual(artifact.metadata["provider"], "sentinel1")
            self.assertEqual(artifact.metadata["product_id"], "iw-1")
            self.assertEqual(
                artifact.metadata["product_name"],
                "S1A_IW_GRDH_1SDV_20240301T110000_20240301T110100_000001_000000_000000",
            )
            self.assertEqual(artifact.provenance.product_id, "iw-1")
            self.assertEqual(
                artifact.provenance.extra["product_name"],
                "S1A_IW_GRDH_1SDV_20240301T110000_20240301T110100_000001_000000_000000",
            )
            self.assertEqual(artifact.provenance.extra["collection"], "SENTINEL-1")
            self.assertEqual(artifact.provenance.processing_level, "GRDH")
            self.assertIsNotNone(artifact.provenance.retrieved_at)
            self.assertTrue(Path(artifact.location).exists())
            self.assertGreater(Path(artifact.location).stat().st_size, 0)
            self.assertIn("acquisitions", artifact.location)
            self.assertIn("sentinel1", artifact.location)
            self.assertTrue(transport.get_urls)
            self.assertIn("/Products", transport.get_urls[0])
            self.assertTrue(transport.download_urls)
            self.assertIn("iw-1", transport.download_urls[0])
            self.assertFalse(transport.post_urls)
            self.assertNotIn("password", str(artifact.provenance.model_dump()))
            self.assertNotIn("mock-access-token", str(artifact.provenance.model_dump()))


if __name__ == "__main__":
    unittest.main()
