from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.acquisition.base import (
    AcquisitionConfigurationError,
    AcquisitionError,
    AcquisitionValidationError,
)
from app.acquisition.providers.cmems import (
    ARTIFACT_NAME,
    DATASET_ID,
    DOCUMENTED_FIRST_LEVEL_M,
    NEAR_SURFACE_DEPTH_MAX_M,
    NEAR_SURFACE_DEPTH_MIN_M,
    PRODUCT_ID,
    VARIABLES,
    CmemsAcquisitionProvider,
    build_cmems_subset_request,
    request_folder_name,
)
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


def _window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2024, 6, 3, 0, 0, tzinfo=timezone.utc),
    )


def _aoi() -> BBoxAreaOfInterest:
    return BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))


def _request(**overrides: object) -> AcquisitionRequest:
    payload: dict[str, object] = {
        "investigation_id": "inv-cmems",
        "provider_id": "cmems",
        "asset_type": AssetType.ENVIRONMENT_CURRENT,
        "area_of_interest": _aoi(),
        "time_window": _window(),
    }
    payload.update(overrides)
    return AcquisitionRequest.model_validate(payload)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "cmems_username": "mock-cmems-user",
        "cmems_password": "mock-cmems-pass",
    }
    values.update(overrides)
    return Settings(**values)


class FakeCmemsClient:
    def __init__(self, *, write_bytes: bytes = b"CDF\x01mock", error: Exception | None = None) -> None:
        self.write_bytes = write_bytes
        self.error = error
        self.calls: list[dict[str, object]] = []

    def subset(self, **kwargs: object) -> Path:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        directory = Path(str(kwargs["output_directory"]))
        filename = str(kwargs["output_filename"])
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.write_bytes)
        return path


class CmemsValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = CmemsAcquisitionProvider(settings=_settings(Path(".")), client=FakeCmemsClient())

    def test_valid_current_request(self) -> None:
        self.provider.validate(_request())

    def test_missing_aoi(self) -> None:
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(_request(area_of_interest=None))

    def test_missing_time_window(self) -> None:
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(_request(time_window=None))

    def test_unsupported_asset_type(self) -> None:
        with self.assertRaises(AcquisitionValidationError):
            self.provider.validate(_request(asset_type=AssetType.ENVIRONMENT_WIND))

    def test_invalid_time_window_rejected_by_schema(self) -> None:
        with self.assertRaises(Exception):
            TimeWindow(
                start=datetime(2024, 6, 3, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, tzinfo=timezone.utc),
            )

    def test_invalid_bbox_rejected_by_schema(self) -> None:
        with self.assertRaises(Exception):
            BoundingBox(west=0, south=10, east=1, north=10)


class CmemsRequestMappingTests(unittest.TestCase):
    def test_dataset_and_variables(self) -> None:
        payload = build_cmems_subset_request(_aoi(), _window(), Path("out"))
        self.assertEqual(payload["dataset_id"], DATASET_ID)
        self.assertEqual(DATASET_ID.startswith("cmems_mod_glo_phy_my"), True)
        self.assertEqual(PRODUCT_ID, "GLOBAL_MULTIYEAR_PHY_001_030")
        self.assertEqual(payload["variables"], ["uo", "vo"])
        self.assertEqual(list(VARIABLES), ["uo", "vo"])
        self.assertEqual(payload["file_format"], "netcdf")

    def test_aoi_time_depth_translation(self) -> None:
        payload = build_cmems_subset_request(_aoi(), _window(), Path("out"))
        self.assertEqual(payload["minimum_longitude"], 2.0)
        self.assertEqual(payload["maximum_longitude"], 3.0)
        self.assertEqual(payload["minimum_latitude"], 51.0)
        self.assertEqual(payload["maximum_latitude"], 52.0)
        self.assertIn("2024-06-01", payload["start_datetime"])
        self.assertIn("2024-06-03", payload["end_datetime"])
        self.assertEqual(payload["minimum_depth"], NEAR_SURFACE_DEPTH_MIN_M)
        self.assertEqual(payload["maximum_depth"], NEAR_SURFACE_DEPTH_MAX_M)
        self.assertEqual(payload["coordinates_selection_method"], "nearest")
        self.assertLess(payload["maximum_depth"], 2.0)
        self.assertEqual(DOCUMENTED_FIRST_LEVEL_M, 0.494025)


class CmemsAcquisitionTests(unittest.TestCase):
    def test_missing_credentials(self) -> None:
        provider = CmemsAcquisitionProvider(settings=_settings(Path("."), cmems_username="", cmems_password=""))
        with self.assertRaises(AcquisitionConfigurationError):
            provider.acquire(_request())

    def test_acquisition_failure(self) -> None:
        client = FakeCmemsClient(error=AcquisitionError("CMEMS subset failed"))
        provider = CmemsAcquisitionProvider(settings=_settings(Path(".")), client=client)
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("CMEMS", str(context.exception))

    def test_empty_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeCmemsClient(write_bytes=b"")
            provider = CmemsAcquisitionProvider(settings=_settings(Path(tmp)), client=client)
            with self.assertRaises(AcquisitionError) as context:
                provider.acquire(_request())
            self.assertIn("empty artifact", str(context.exception))

    def test_non_netcdf_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeCmemsClient(write_bytes=b"not-netcdf")
            provider = CmemsAcquisitionProvider(settings=_settings(Path(tmp)), client=client)
            with self.assertRaises(AcquisitionError) as context:
                provider.acquire(_request())
            self.assertIn("NetCDF", str(context.exception))

    def test_successful_mocked_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeCmemsClient()
            provider = CmemsAcquisitionProvider(settings=_settings(Path(tmp)), client=client)
            result = provider.acquire(_request())
            self.assertEqual(len(result.artifacts), 1)
            artifact = result.artifacts[0]
            self.assertEqual(artifact.asset_type, AssetType.ENVIRONMENT_CURRENT)
            self.assertEqual(artifact.source, "copernicus-marine-service")
            self.assertEqual(artifact.metadata["provider"], "cmems")
            self.assertEqual(artifact.metadata["dataset"], DATASET_ID)
            self.assertEqual(artifact.provenance.product_id, DATASET_ID)
            self.assertEqual(artifact.provenance.extra["product_id"], PRODUCT_ID)
            self.assertEqual(artifact.provenance.extra["variables"], ["uo", "vo"])
            self.assertEqual(artifact.provenance.extra["output_format"], "netcdf")
            self.assertEqual(artifact.provenance.extra["depth"]["minimum_m"], 0.0)
            self.assertEqual(artifact.provenance.extra["depth"]["maximum_m"], 1.0)
            self.assertEqual(artifact.provenance.extra["depth"]["documented_first_level_m"], DOCUMENTED_FIRST_LEVEL_M)
            self.assertEqual(
                artifact.provenance.extra["bounding_box"],
                {"west": 2.0, "south": 51.0, "east": 3.0, "north": 52.0},
            )
            path = Path(artifact.location)
            self.assertTrue(path.exists())
            self.assertEqual(path.name, ARTIFACT_NAME)
            expected = (
                Path(tmp)
                / "acquisitions"
                / "inv-cmems"
                / "cmems"
                / request_folder_name(_aoi(), _window())
                / ARTIFACT_NAME
            )
            self.assertEqual(path, expected)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0]["dataset_id"], DATASET_ID)
            self.assertNotIn("username", client.calls[0])
            self.assertNotIn("password", client.calls[0])
            dumped = str(artifact.provenance.model_dump())
            self.assertNotIn("mock-cmems-pass", dumped)
            self.assertNotIn("mock-cmems-user", dumped)


if __name__ == "__main__":
    unittest.main()
