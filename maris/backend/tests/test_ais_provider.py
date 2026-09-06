from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

from app.acquisition.base import (
    AcquisitionConfigurationError,
    AcquisitionError,
    AcquisitionValidationError,
)
from app.acquisition.providers.ais import (
    ARTIFACT_NAME,
    ARTIFACT_SCHEMA,
    AisAcquisitionProvider,
    StaticAisAdapter,
    request_folder_name,
)
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


def _window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
        end=datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc),
    )


def _aoi() -> BBoxAreaOfInterest:
    return BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))


def _request(**overrides: object) -> AcquisitionRequest:
    payload: dict[str, object] = {
        "investigation_id": "inv-ais",
        "provider_id": "ais",
        "asset_type": AssetType.VESSEL_TRACK,
        "area_of_interest": _aoi(),
        "time_window": _window(),
    }
    payload.update(overrides)
    return AcquisitionRequest.model_validate(payload)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {"data_dir": tmp_path, "ais_adapter_id": "unconfigured"}
    values.update(overrides)
    return Settings(**values)


def _record(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "timestamp": "2024-06-01T12:00:00+00:00",
        "lat": 51.4,
        "lon": 2.4,
        "mmsi": "244123456",
        "imo": "9123456",
        "vessel_name": "EXAMPLE SHIP",
        "speed_over_ground": 12.3,
        "course_over_ground": 80.0,
        "heading": 82.0,
        "navigation_status": "under way using engine",
    }
    payload.update(overrides)
    return payload


class AisValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = AisAcquisitionProvider(
            settings=_settings(Path(".")),
            adapter=StaticAisAdapter([]),
        )

    def test_valid_historical_request(self) -> None:
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


class AisAcquisitionTests(unittest.TestCase):
    def test_unconfigured_adapter(self) -> None:
        provider = AisAcquisitionProvider(settings=_settings(Path(".")))
        with self.assertRaises(AcquisitionConfigurationError):
            provider.acquire(_request())

    def test_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = AisAcquisitionProvider(
                settings=_settings(Path(tmp)),
                adapter=StaticAisAdapter([]),
            )
            result = provider.acquire(_request())
            artifact = result.artifacts[0]
            path = Path(artifact.location)
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["records"], [])
            self.assertEqual(artifact.provenance.extra["record_count"], 0)
            self.assertGreater(path.stat().st_size, 0)

    def test_malformed_record(self) -> None:
        provider = AisAcquisitionProvider(
            settings=_settings(Path(".")),
            adapter=StaticAisAdapter(["not-an-object"]),  # type: ignore[list-item]
        )
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("malformed", str(context.exception))

    def test_invalid_coordinates(self) -> None:
        provider = AisAcquisitionProvider(
            settings=_settings(Path(".")),
            adapter=StaticAisAdapter([_record(lat=95.0)]),
        )
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("invalid", str(context.exception).lower())

    def test_coordinates_outside_aoi(self) -> None:
        provider = AisAcquisitionProvider(
            settings=_settings(Path(".")),
            adapter=StaticAisAdapter([_record(lat=10.0, lon=10.0)]),
        )
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("AOI", str(context.exception))

    def test_invalid_timestamp(self) -> None:
        provider = AisAcquisitionProvider(
            settings=_settings(Path(".")),
            adapter=StaticAisAdapter([_record(timestamp="not-a-timestamp")]),
        )
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("invalid", str(context.exception).lower())

    def test_timestamp_outside_window(self) -> None:
        provider = AisAcquisitionProvider(
            settings=_settings(Path(".")),
            adapter=StaticAisAdapter([_record(timestamp="2020-01-01T00:00:00Z")]),
        )
        with self.assertRaises(AcquisitionError) as context:
            provider.acquire(_request())
        self.assertIn("time window", str(context.exception))

    def test_successful_mocked_acquisition_does_not_fabricate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            supplied = [
                _record(mmsi="111", vessel_name="ALPHA"),
                {
                    "timestamp": "2024-06-01T18:30:00Z",
                    "lat": 51.5,
                    "lon": 2.5,
                    "mmsi": "222",
                },
            ]
            provider = AisAcquisitionProvider(
                settings=_settings(Path(tmp)),
                adapter=StaticAisAdapter(supplied, adapter_id="static-test"),
            )
            result = provider.acquire(_request())
            artifact = result.artifacts[0]
            path = Path(artifact.location)
            expected = (
                Path(tmp)
                / "acquisitions"
                / "inv-ais"
                / "ais"
                / request_folder_name(_aoi(), _window())
                / ARTIFACT_NAME
            )
            self.assertEqual(path, expected)
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["schema"], ARTIFACT_SCHEMA)
            self.assertEqual(len(body["records"]), 2)
            self.assertEqual(body["records"][0]["mmsi"], "111")
            self.assertEqual(body["records"][1]["mmsi"], "222")
            self.assertNotIn("imo", body["records"][1])
            self.assertNotIn("speed_over_ground", body["records"][1])
            self.assertEqual(artifact.asset_type, AssetType.VESSEL_TRACK)
            self.assertEqual(artifact.source, "ais-adapter:static-test")
            self.assertEqual(artifact.metadata["provider"], "ais")
            self.assertEqual(artifact.provenance.extra["record_count"], 2)
            self.assertEqual(artifact.provenance.extra["adapter_id"], "static-test")
            self.assertEqual(artifact.provenance.extra["source_format"], "json")
            self.assertEqual(
                artifact.provenance.extra["bounding_box"],
                {"west": 2.0, "south": 51.0, "east": 3.0, "north": 52.0},
            )
            dumped = str(artifact.provenance.model_dump())
            self.assertNotIn("password", dumped.lower())
            self.assertNotIn("api_key", dumped.lower())


if __name__ == "__main__":
    unittest.main()
