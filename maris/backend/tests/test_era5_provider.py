from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.acquisition.base import (
    AcquisitionConfigurationError,
    AcquisitionError,
    AcquisitionValidationError,
)
from app.acquisition.providers.era5 import (
    ARTIFACT_NAME,
    DATASET_ID,
    VARIABLES,
    Era5AcquisitionProvider,
    build_era5_month_requests,
    request_folder_name,
)
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


def _window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
    )


def _aoi() -> BBoxAreaOfInterest:
    return BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))


def _request(**overrides: object) -> AcquisitionRequest:
    payload: dict[str, object] = {
        "investigation_id": "inv-era5",
        "provider_id": "era5",
        "asset_type": AssetType.ENVIRONMENT_WIND,
        "area_of_interest": _aoi(),
        "time_window": _window(),
    }
    payload.update(overrides)
    return AcquisitionRequest.model_validate(payload)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "cds_url": "https://cds.climate.copernicus.eu/api",
        "cds_api_key": "mock-cds-key",
    }
    values.update(overrides)
    return Settings(**values)


class FakeCdsClient:
    def __init__(self, *, write_bytes: bytes = b"CDF\x01mock", error: Exception | None = None) -> None:
        self.write_bytes = write_bytes
        self.error = error
        self.calls: list[tuple[str, dict[str, object], str]] = []

    def retrieve(self, dataset: str, request: dict[str, object], target: str) -> None:
        self.calls.append((dataset, request, target))
        if self.error is not None:
            raise self.error
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.write_bytes)


class Era5ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = Era5AcquisitionProvider(settings=_settings(Path(".")), client=FakeCdsClient())

    def test_valid_wind_request(self) -> None:
        self.provider.validate(_request())

    def test_missing_aoi(self) -> None:
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(_request(area_of_interest=None))

    def test_missing_time_window(self) -> None:
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(_request(time_window=None))

    def test_unsupported_asset_type(self) -> None:
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(_request(asset_type=AssetType.SATELLITE_SCENE))

    def test_invalid_time_window_rejected_by_schema(self) -> None:
        with self.assertRaises(Exception):
            TimeWindow(
                start=datetime(2024, 6, 2, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, tzinfo=timezone.utc),
            )

    def test_invalid_bbox_rejected_by_schema(self) -> None:
        with self.assertRaises(Exception):
            BoundingBox(west=0, south=10, east=1, north=10)


class Era5RequestMappingTests(unittest.TestCase):
    def test_cds_request_is_bbox_limited_hourly_wind(self) -> None:
        payload = build_era5_month_requests(_aoi(), _window())[0]
        self.assertEqual(payload["variable"], list(VARIABLES))
        self.assertEqual(payload["product_type"], ["reanalysis"])
        self.assertEqual(payload["data_format"], "netcdf")
        self.assertEqual(payload["year"], ["2024"])
        self.assertEqual(payload["month"], ["06"])
        self.assertEqual(payload["day"], ["01"])
        self.assertEqual(payload["time"], ["00:00", "01:00", "02:00", "03:00", "04:00", "05:00"])
        self.assertEqual(payload["area"], [52.0, 2.0, 51.0, 3.0])
        self.assertNotIn("global", str(payload).lower())

    def test_multi_month_window_splits_requests(self) -> None:
        window = TimeWindow(
            start=datetime(2024, 5, 31, 22, 0, tzinfo=timezone.utc),
            end=datetime(2024, 6, 1, 2, 0, tzinfo=timezone.utc),
        )
        payloads = build_era5_month_requests(_aoi(), window)
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["month"], ["05"])
        self.assertEqual(payloads[0]["day"], ["31"])
        self.assertEqual(payloads[1]["month"], ["06"])
        self.assertEqual(payloads[1]["day"], ["01"])


class Era5AcquisitionTests(unittest.TestCase):
    def test_missing_credentials(self) -> None:
        provider = Era5AcquisitionProvider(settings=_settings(Path("."), cds_api_key=""))
        with self.assertRaises(AcquisitionConfigurationError):
            provider.acquire(_request())

    def test_download_failure(self) -> None:
        client = FakeCdsClient(error=AcquisitionError("CDS retrieve failed"))
        provider = Era5AcquisitionProvider(settings=_settings(Path(".")), client=client)
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("CDS", str(context.exception))

    def test_empty_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeCdsClient(write_bytes=b"")
            provider = Era5AcquisitionProvider(settings=_settings(Path(tmp)), client=client)
            with self.assertRaises(AcquisitionError) as context:
                provider.acquire(_request())
            self.assertIn("empty artifact", str(context.exception))

    def test_non_netcdf_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeCdsClient(write_bytes=b"not-netcdf")
            provider = Era5AcquisitionProvider(settings=_settings(Path(tmp)), client=client)
            with self.assertRaises(AcquisitionError) as context:
                provider.acquire(_request())
            self.assertIn("NetCDF", str(context.exception))

    def test_successful_mocked_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeCdsClient()
            settings = _settings(Path(tmp))
            provider = Era5AcquisitionProvider(settings=settings, client=client)
            result = provider.acquire(_request())
            self.assertEqual(len(result.artifacts), 1)
            artifact = result.artifacts[0]
            self.assertEqual(artifact.asset_type, AssetType.ENVIRONMENT_WIND)
            self.assertEqual(artifact.source, "copernicus-climate-data-store")
            self.assertEqual(artifact.metadata["provider"], "era5")
            self.assertEqual(artifact.metadata["dataset"], DATASET_ID)
            self.assertEqual(artifact.provenance.product_id, DATASET_ID)
            self.assertEqual(artifact.provenance.extra["variables"], list(VARIABLES))
            self.assertEqual(artifact.provenance.extra["output_format"], "netcdf")
            self.assertEqual(artifact.provenance.extra["dataset"], DATASET_ID)
            self.assertEqual(
                artifact.provenance.extra["bounding_box"],
                {"north": 52.0, "west": 2.0, "south": 51.0, "east": 3.0},
            )
            self.assertIn("2024-06-01", artifact.provenance.extra["time_range"]["start"])
            self.assertIsNotNone(artifact.provenance.retrieved_at)
            path = Path(artifact.location)
            self.assertTrue(path.exists())
            self.assertEqual(path.name, ARTIFACT_NAME)
            self.assertGreater(path.stat().st_size, 0)
            expected_dir = (
                Path(tmp)
                / "acquisitions"
                / "inv-era5"
                / "era5"
                / request_folder_name(_aoi(), _window())
                / ARTIFACT_NAME
            )
            self.assertEqual(path, expected_dir)
            self.assertEqual(len(client.calls), 1)
            dataset, payload, target = client.calls[0]
            self.assertEqual(dataset, DATASET_ID)
            self.assertEqual(payload["area"], [52.0, 2.0, 51.0, 3.0])
            self.assertEqual(target, str(path))
            dumped = str(artifact.provenance.model_dump())
            self.assertNotIn("mock-cds-key", dumped)
            self.assertNotIn("password", dumped.lower())


if __name__ == "__main__":
    unittest.main()
